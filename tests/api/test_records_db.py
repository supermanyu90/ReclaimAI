"""Prompt 2 acceptance: repeat seeding is safe; counts are consistent; balances never
include insurance cover or SIP installments; cross-tenant reads are denied."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reclaim_api import auth
from reclaim_api import models as m
from reclaim_api.config import Settings
from reclaim_api.db import get_engine
from reclaim_api.main import app
from reclaim_api.seed import SeedStateError, fixture_id, seed

pytestmark = pytest.mark.db

client = TestClient(app)
RAMESH, PRIYA, KAVITA = fixture_id("ramesh"), fixture_id("priya"), fixture_id("kavita")
REVIEWER = fixture_id("reviewer_anil")


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{user_id}"}


def load(state: str, reset: bool = True) -> dict[str, int]:
    with Session(get_engine()) as session, session.begin():
        return seed(session, state, do_reset=reset)


@pytest.fixture(autouse=True)
def before_state():
    load("before")
    yield
    load("before")


def patel_assets() -> list[dict]:
    res = client.get("/v1/assets", headers=as_user(RAMESH))
    assert res.status_code == 200
    return res.json()["assets"]


# --- repeat seeding ----------------------------------------------------------


def test_repeat_seeding_is_safe_in_both_states():
    assert load("before", reset=False) == load("before", reset=False)
    first_after = load("after", reset=False)
    assert load("after", reset=False) == first_after
    assert first_after["assets"] == 8 and first_after["confirmations"] == 1
    with Session(get_engine()) as s:
        confirmed_events = s.scalar(select(func.count()).select_from(m.OutboxEvent).where(m.OutboxEvent.event_type == "asset.version_confirmed"))
    assert confirmed_events == 1  # the FD's confirmation event; the rest are minimized analytics copies


def test_returning_to_before_requires_explicit_reset():
    load("after", reset=False)
    with Session(get_engine()) as session, session.begin(), pytest.raises(SeedStateError):
        seed(session, "before")
    assert len(patel_assets()) == 7  # nothing was deleted


# --- counts and fixture shape -------------------------------------------------


def test_six_assets_before_and_seven_after_fd_confirmation():
    before = patel_assets()
    assert len(before) == 6
    assert "fixed_deposit" not in {a["asset_type"] for a in before}

    load("after", reset=False)
    after = patel_assets()
    assert len(after) == 7
    assert sorted(a["asset_type"] for a in after) == sorted(
        ["savings", "savings", "fixed_deposit", "mutual_fund", "life_insurance", "property", "pension"]
    )
    assert len({a["institution_name"] for a in after}) == 5
    assert {a["jurisdiction"] for a in after} == {"IN", "SG"}


def test_fixture_contains_required_edge_cases():
    assets = {a["label"]: a for a in patel_assets()}
    assert [a["label"] for a in assets.values() if a["nomination_status"] == "unknown"] == ["Term life policy"]
    # Two years before the fixture's "today" (2026-10-15): only the 2023 statement qualifies.
    old = [a for a in assets.values() if a["source_date"] and date.fromisoformat(a["source_date"]) < date(2024, 10, 15)]
    assert [a["label"] for a in old] == ["Savings account"]
    assert assets["Equity mutual fund folio"]["open_conflicts"] == 1

    with Session(get_engine()) as session:
        dup = session.get(m.Document, fixture_id("doc_mf_stmt_2026_09_reupload"))
        original = session.get(m.Document, fixture_id("doc_mf_stmt_2026_09"))
        assert dup.duplicate_of_document_id == original.id and dup.sha256 == original.sha256


def test_corrections_create_new_versions():
    pension = next(a for a in patel_assets() if a["asset_type"] == "pension")
    assert pension["version"] == 2 and pension["amounts"]["balance_minor"] == 146000000
    with Session(get_engine()) as session:
        versions = session.scalars(select(m.AssetVersion).where(m.AssetVersion.asset_id == fixture_id("pension")).order_by(m.AssetVersion.version)).all()
        assert [(v.change_kind, v.balance_minor) for v in versions] == [("create", 140000000), ("correct", 146000000)]


def test_family_relationship_implies_no_nomination_or_grant():
    with Session(get_engine()) as session:
        rels = session.scalars(select(m.Relationship).where(m.Relationship.tenant_id == fixture_id("patel"))).all()
        priya_roles = {(r.kind, r.to_kind) for r in rels if PRIYA in (r.from_id, r.to_id)}
        assert priya_roles == {("family_relation", "user"), ("trusted_contact", "user"), ("registered_nominee", "asset")}
        nominee = next(r for r in rels if r.kind == "registered_nominee")
        assert nominee.to_id == fixture_id("savings") and nominee.evidence_document_id is not None
        assert not any(r.kind in ("executor", "legal_heir") for r in rels)

        post = session.get(m.AccessGrant, fixture_id("grant_priya_post"))
        granted = set(post.scope["asset_ids"])
        assert str(fixture_id("sgd_savings")) not in granted and str(fixture_id("fd")) not in granted

    assert client.get("/v1/assets", headers=as_user(PRIYA)).json()["assets"] == []
    assert client.get("/v1/assets", headers=as_user(REVIEWER)).json()["assets"] == []


# --- amount kinds -------------------------------------------------------------


def test_balances_never_include_cover_or_sip():
    totals = client.get("/v1/inventory/totals", headers=as_user(RAMESH)).json()["by_currency"]
    inr = totals["INR"]
    assert inr["balance_minor"] == 34215000 + 87540000 + 146000000
    assert inr["insurance_cover_minor"] == 500000000
    assert inr["sip_installment_minor"] == 1000000
    assert inr["estimated_value_minor"] == 850000000 and inr["liability_minor"] == 124000000
    assert totals["SGD"]["balance_minor"] == 1825000  # never converted or merged into INR

    load("after", reset=False)
    inr_after = client.get("/v1/inventory/totals", headers=as_user(RAMESH)).json()["by_currency"]["INR"]
    assert inr_after["balance_minor"] == inr["balance_minor"] + 50000000
    assert inr_after["insurance_cover_minor"] == inr["insurance_cover_minor"]


@pytest.mark.parametrize(
    ("asset_key", "asset_type", "field"),
    [("savings", "savings", "insurance_cover_minor"), ("savings", "savings", "sip_installment_minor"), ("life_policy", "life_insurance", "balance_minor")],
)
def test_database_rejects_amount_kind_on_wrong_asset_type(asset_key, asset_type, field):
    with Session(get_engine()) as session, pytest.raises(IntegrityError):
        session.add(
            m.AssetVersion(
                tenant_id=fixture_id("patel"), asset_id=fixture_id(asset_key), asset_type=asset_type, version=99,
                change_kind="update", jurisdiction="IN", currency="INR", nomination_status="unknown",
                verification_state="user_reported", created_by_user_id=RAMESH, **{field: 100},
            )
        )
        session.flush()


# --- tenant isolation -----------------------------------------------------------


def test_cross_tenant_reads_are_denied_and_indistinguishable_from_missing():
    ramesh_savings = fixture_id("savings")
    assert client.get(f"/v1/assets/{ramesh_savings}", headers=as_user(RAMESH)).status_code == 200

    cross = client.get(f"/v1/assets/{ramesh_savings}", headers=as_user(KAVITA))
    missing = client.get(f"/v1/assets/{uuid.uuid4()}", headers=as_user(KAVITA))
    assert cross.status_code == missing.status_code == 404
    assert cross.json() == missing.json()

    assert client.get(f"/v1/assets/{fixture_id('mehta_savings')}", headers=as_user(RAMESH)).status_code == 404
    kavita_assets = client.get("/v1/assets", headers=as_user(KAVITA)).json()["assets"]
    assert [a["id"] for a in kavita_assets] == [str(fixture_id("mehta_savings"))]
    assert set(client.get("/v1/inventory/totals", headers=as_user(KAVITA)).json()["by_currency"]) == {"INR"}


def test_database_refuses_cross_tenant_references():
    with Session(get_engine()) as session, pytest.raises(IntegrityError):
        session.add(
            m.AssetVersion(
                tenant_id=fixture_id("mehta"), asset_id=fixture_id("savings"), asset_type="savings", version=99,
                change_kind="update", jurisdiction="IN", nomination_status="unknown",
                verification_state="user_reported", created_by_user_id=KAVITA,
            )
        )
        session.flush()


def test_tenant_counts_are_isolated():
    with Session(get_engine()) as session:
        per_tenant = dict(session.execute(select(m.Asset.tenant_id, func.count()).group_by(m.Asset.tenant_id)).all())
    assert per_tenant[fixture_id("patel")] == 6 and per_tenant[fixture_id("mehta")] == 1


# --- authentication -------------------------------------------------------------


@pytest.mark.parametrize("header", [None, "Bearer nope", "Bearer dev:not-a-uuid", f"Bearer dev:{uuid.uuid4()}", f"Basic dev:{RAMESH}"])
def test_requests_without_a_valid_actor_are_rejected(header):
    headers = {"Authorization": header} if header else {}
    assert client.get("/v1/assets", headers=headers).status_code == 401


def test_dev_identities_are_refused_outside_local_mode(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(app_mode="cloud", _env_file=None))
    assert client.get("/v1/assets", headers=as_user(RAMESH)).status_code == 401

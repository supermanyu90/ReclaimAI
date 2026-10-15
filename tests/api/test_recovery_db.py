"""Prompt 13 acceptance: opt-in search over synthetic records; possible matches stay out of the
inventory; same-name hard negatives and conflicting DOB lower or block; Priya searches only her
authorized case; no match triggers access or money release; the core flow works without it."""

import importlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.db import get_engine
from reclaim_api.recovery import service
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)
H = {u: {"Authorization": f"Bearer dev:{fixture_id(u)}"} for u in ("ramesh", "priya", "kavita", "reviewer_anil")}


@pytest.fixture(autouse=True)
def fresh():
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    yield


def opt_in_and_search() -> dict:
    assert api.post("/v1/recovery/opt-in", headers=H["ramesh"]).status_code == 200
    res = api.post("/v1/recovery/search", json={}, headers=H["ramesh"])
    assert res.status_code == 200, res.text
    return res.json()


def release(state: str = "RELEASED") -> str:
    hid = uuid.uuid4()
    with Session(get_engine()) as s, s.begin():
        s.add(m.HandoverRequest(id=hid, tenant_id=fixture_id("patel"), owner_user_id=fixture_id("ramesh"), requester_user_id=fixture_id("priya"),
                                grant_id=fixture_id("grant_priya_post"), grant_version=1, state=state))  # fmt: skip
    return str(hid)


def by_record(view: dict) -> dict[str, dict]:
    return {mt["record"]["id"]: mt for mt in view["matches"]}


def state_snapshot() -> dict:
    assets = api.get("/v1/assets", headers=H["ramesh"]).json()["assets"]
    res = api.get("/v1/inventory/totals", headers=H["ramesh"])
    assert res.status_code == 200
    totals = res.json()
    with Session(get_engine()) as s:
        grants = sorted((str(g.id), g.status, g.version) for g in s.scalars(select(m.AccessGrant)))
        handovers = sorted((str(h.id), h.state) for h in s.scalars(select(m.HandoverRequest)))
        asset_events = s.scalar(select(func.count()).select_from(m.OutboxEvent).where(m.OutboxEvent.aggregate_type == "asset"))
    return {"assets": sorted(a["id"] for a in assets), "totals": totals, "grants": grants, "handovers": handovers, "asset_events": asset_events}


# --- consent and results ------------------------------------------------------------------------------


def test_search_needs_opt_in_and_withdrawal_deletes_results():
    assert api.post("/v1/recovery/search", json={}, headers=H["ramesh"]).status_code == 403
    v = api.get("/v1/recovery", headers=H["ramesh"]).json()
    assert v["profile"]["opted_in"] is False and v["matches"] == [] and v["optional"] is True
    opt_in_and_search()
    out = api.post("/v1/recovery/opt-out", headers=H["ramesh"]).json()
    assert out["profile"]["opted_in"] is False and out["matches"] == [] and out["ruled_out"] == []
    with Session(get_engine()) as s:
        assert s.scalar(select(func.count()).select_from(m.RecoveryMatch)) == 0


def test_ramesh_sees_the_642_lakh_deposit_with_its_evidence_path():
    v = opt_in_and_search()
    dr1 = by_record(v)["DR-0001"]
    assert dr1["record"]["amount_minor"] == 64_200_000 and dr1["record"]["currency"] == "INR" and dr1["record"]["account_ref"] == "****7710"
    assert dr1["band"] == "strong evidence" and dr1["status"] == "candidate"
    assert {f["key"] for f in dr1["features"]} == {"name", "date_of_birth", "address"}
    assert all({"registry_value", "profile_value", "points", "max_points", "detail"} <= f.keys() for f in dr1["features"])
    assert "not a probability" in v["score_label"] and v["registry"]["synthetic"] is True
    e = dr1["explanation"]
    assert e and "decides nothing" in e["label"] and {s["feature"] for s in e["statements"]} <= {"name", "date_of_birth", "address"}


def test_hard_negatives_are_ruled_out_or_weak():
    v = opt_in_and_search()
    ruled = {r["record_id"]: r["blocked_by"] for r in v["ruled_out"]}
    assert ruled == {"DR-0002": ["date_of_birth_conflict"], "DR-0004": ["first_name_conflict"], "DR-0005": ["date_of_birth_conflict"]}
    dr3 = by_record(v)["DR-0003"]
    assert dr3["band"] == "weak evidence" and dr3["explanation"] is None  # a name alone gets no AI story
    assert set(by_record(v)) == {"DR-0001", "DR-0003"}  # other surnames are never compared or shown


def test_search_cannot_take_a_name_or_other_terms():
    api.post("/v1/recovery/opt-in", headers=H["ramesh"])
    for body in ({"name": "Kavita Mehta"}, {"holder_name": "Ramesh Patel"}, {"date_of_birth": "1975-02-09"}):
        assert api.post("/v1/recovery/search", json=body, headers=H["ramesh"]).status_code == 422


# --- possible matches are not assets; nothing is released ------------------------------------------------------


def test_matches_and_claims_never_touch_inventory_access_or_money():
    before = state_snapshot()
    v = opt_in_and_search()
    mid = by_record(v)["DR-0001"]["id"]
    res = api.post(f"/v1/recovery/matches/{mid}/decision", json={"decision": "open_claim"}, headers=H["ramesh"])
    assert res.status_code == 200
    claim = by_record(res.json())["DR-0001"]["claims"][0]
    assert claim["status"] == "preparing" and any("does not submit claims or move money" in s["step"] for s in claim["checklist"])
    assert state_snapshot() == before
    workspace = api.get("/v1/workspace", headers=H["ramesh"]).text
    assert "7710" not in workspace and "DR-0001" not in workspace and "642000" not in workspace


def test_decisions_are_idempotent_and_blocked_records_cannot_be_claimed():
    v = opt_in_and_search()
    ids = {k: x["id"] for k, x in by_record(v).items()}
    blocked = api.get("/v1/recovery", headers=H["ramesh"]).json()
    with Session(get_engine()) as s:
        dr2 = s.scalar(select(m.RecoveryMatch.id).where(m.RecoveryMatch.registry_record_id == "DR-0002"))
    assert api.post(f"/v1/recovery/matches/{dr2}/decision", json={"decision": "open_claim"}, headers=H["ramesh"]).status_code == 409
    assert blocked["ruled_out"]
    for _ in range(2):
        api.post(f"/v1/recovery/matches/{ids['DR-0001']}/decision", json={"decision": "open_claim"}, headers=H["ramesh"])
    with Session(get_engine()) as s:
        assert s.scalar(select(func.count()).select_from(m.RecoveryClaim)) == 1
    api.post(f"/v1/recovery/matches/{ids['DR-0003']}/decision", json={"decision": "not_mine"}, headers=H["ramesh"])
    assert api.post(f"/v1/recovery/matches/{ids['DR-0003']}/decision", json={"decision": "open_claim"}, headers=H["ramesh"]).status_code == 409
    again = api.post("/v1/recovery/search", json={}, headers=H["ramesh"]).json()
    assert by_record(again)["DR-0003"]["status"] == "not_mine" and by_record(again)["DR-0001"]["status"] == "claim_opened"  # decisions survive


# --- Priya: only her authorized case -----------------------------------------------------------------------------


def test_priya_searches_only_inside_her_released_case():
    api.post("/v1/recovery/opt-in", headers=H["ramesh"])
    assert api.post("/v1/recovery/search", json={}, headers=H["priya"]).status_code == 404  # no case: no subject
    assert api.post("/v1/recovery/search", json={"case_id": str(uuid.uuid4())}, headers=H["priya"]).status_code == 404
    case = release()
    pending = release("VERIFICATION_PENDING")  # a later case that is not released gives nothing, even with an earlier release
    assert api.post("/v1/recovery/search", json={"case_id": pending}, headers=H["priya"]).status_code == 404
    res = api.post("/v1/recovery/search", json={"case_id": case}, headers=H["priya"])
    assert res.status_code == 200 and "DR-0001" in by_record(res.json()) and res.json()["profile"]["editable_by_viewer"] is False
    for who in ("kavita", "reviewer_anil", "ramesh"):
        assert api.post("/v1/recovery/search", json={"case_id": case}, headers=H[who]).status_code == 404
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.AccessGrant).where(m.AccessGrant.id == fixture_id("grant_priya_post")).values(status="revoked"))
    assert api.post("/v1/recovery/search", json={"case_id": case}, headers=H["priya"]).status_code == 404


def test_priya_needs_the_owners_opt_in_and_cannot_change_consent():
    case = release()
    assert api.post("/v1/recovery/search", json={"case_id": case}, headers=H["priya"]).status_code == 403
    assert api.post("/v1/recovery/opt-in", headers=H["priya"]).status_code == 404


def test_priya_claim_is_a_separate_case_and_releases_nothing():
    api.post("/v1/recovery/opt-in", headers=H["ramesh"])
    case = release()
    before = state_snapshot()
    v = api.post("/v1/recovery/search", json={"case_id": case}, headers=H["priya"]).json()
    mid = by_record(v)["DR-0001"]["id"]
    assert api.post(f"/v1/recovery/matches/{mid}/decision", json={"decision": "open_claim"}, headers=H["priya"]).status_code == 404  # needs the case
    res = api.post(f"/v1/recovery/matches/{mid}/decision", json={"decision": "open_claim", "case_id": case}, headers=H["priya"])
    claim = by_record(res.json())["DR-0001"]["claims"][0]
    assert claim["mine"] and any("decided by the institution and the law" in s["step"] for s in claim["checklist"])
    with Session(get_engine()) as s:
        row = s.scalar(select(m.RecoveryClaim))
        assert row.handover_request_id == uuid.UUID(case) and row.claimant_user_id == fixture_id("priya")
        assert s.scalar(select(func.count()).select_from(m.ClaimCase)) == 0  # not an asset claim case
    assert state_snapshot() == before


# --- explanation guard and feature switch ------------------------------------------------------------------------


class ScriptedLLM:
    model_version = "scripted"

    def __init__(self, out):
        self.out = out

    def generate_structured(self, **_):
        return self.out


def test_explanations_that_claim_ownership_relationship_or_invent_numbers_are_dropped():
    reg = service.registry()
    record = reg.records["DR-0001"]
    from reclaim_api.recovery import matching

    profile = {"names": ["Ramesh Kumar Patel"], "date_of_birth": None, "addresses": []}
    result = matching.evaluate(record, profile)
    bad = [
        {"feature": "name", "text": "This deposit belongs to Ramesh."},
        {"feature": "name", "text": "The holder is likely his brother."},
        {"feature": "address", "text": "There is a 90% match."},
        {"feature": "name", "text": "The account was opened in 1987."},  # number not in the evidence
        {"feature": "account_number", "text": "Unknown feature."},
    ]
    assert service.explain(record, result, ScriptedLLM({"statements": bad})) is None
    good = {"feature": "name", "text": "The surname matches and only the first initial is given."}
    out = service.explain(record, result, ScriptedLLM({"statements": [*bad, good]}))
    assert out["statements"] == [good] and out["dropped"] == 5
    assert out["missing"] == ["Date of birth: nothing to compare."]  # from the features, not from the model
    assert service.explain(record, result, ScriptedLLM({"statements": [good], "extra": "x"})) is None  # schema is strict


def test_explanations_that_contradict_the_evidence_are_dropped():
    from reclaim_api.recovery import matching

    record = service.registry().records["DR-0001"]
    profile = {"names": ["Ramesh Patel"], "date_of_birth": None, "addresses": [{"line": "Baner Road, Pune", "pincode": "411045"}]}
    result = matching.evaluate(record, profile)  # name: same surname + matching initial; address: no match
    said = [
        {"feature": "name", "text": "The names share the surname but differ in first initial."},  # seen from Gemma 3 4B
        {"feature": "address", "text": "The address matches the profile."},
        {"feature": "address", "text": "The registry address does not match any profile address."},
        {"feature": "name", "text": "Same surname, and the first initial R matches Ramesh."},
    ]
    out = service.explain(record, result, ScriptedLLM({"statements": said}))
    assert [s["text"] for s in out["statements"]] == [said[2]["text"], said[3]["text"]] and out["dropped"] == 2


def test_recovery_can_be_switched_off_without_affecting_the_core_flow(monkeypatch):
    monkeypatch.setattr(main.settings, "recovery_enabled", False)
    assert api.get("/v1/recovery", headers=H["ramesh"]).status_code == 404
    assert api.post("/v1/recovery/opt-in", headers=H["ramesh"]).status_code == 404
    assert api.get("/v1/system/capabilities").json()["optional_features"] == {"recovery": False}
    assert len(api.get("/v1/assets", headers=H["ramesh"]).json()["assets"]) == 6

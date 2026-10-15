"""Deterministic synthetic fixture loader.

    reclaim-seed                  # "before" state: six Patel assets (demo start)
    reclaim-seed --state after    # adds the confirmed FD: seven assets
    reclaim-seed --reset ...      # wipe the synthetic demo tenants first

IDs are uuid5 values derived from fixture keys and every insert is ON CONFLICT DO
NOTHING, so seeding the same state twice changes nothing. Going from "after" back
to "before" needs --reset; the seeder never silently deletes a confirmed record.
Only tenants listed in fixtures/demo.json and flagged is_synthetic are ever reset.
"""

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.db import get_engine

NAMESPACE = uuid.UUID("3f6c1a52-8a6e-4c1e-9a1f-5d0c2b7e9a10")
STATES = ("before", "after")

# Children first, so a reset never trips a foreign key.
RESET_ORDER = (
    m.RecoveryClaim,
    m.RecoveryMatch,
    m.RecoveryProfile,
    m.CategoryConfirmation,
    m.HandoverEvidence,
    m.FreshnessFinding,
    m.AssetProjection,
    m.ReportJob,
    m.ChatTurn,
    m.LinkCode,
    m.ReviewDecision,
    m.ClaimCase,
    m.HandoverRequest,
    m.PassportVersion,
    m.FieldEvidence,
    m.Confirmation,
    m.Proposal,
    m.AssetVersion,
    m.Relationship,
    m.AccessGrant,
    m.Asset,
    m.Document,
    m.ChannelLink,
    m.OutboxEvent,
    m.AuditEvent,
    m.User,
)


class SeedStateError(RuntimeError):
    pass


def fixture_id(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"reclaim-demo:{key}")


def default_fixture_path() -> Path:
    override = os.environ.get("FIXTURES_DIR")
    root = Path(override) if override else Path(__file__).resolve().parents[4] / "fixtures"
    return root / "demo.json"


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or default_fixture_path()).read_text(encoding="utf-8"))


def _in_state(item: dict[str, Any], state: str) -> bool:
    return item.get("state", "before") == "before" or state == "after"


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _put(session: Session, model: type[m.Base], rows: list[dict[str, Any]], *, conflict: list[str] | None = None) -> None:
    if rows:
        stmt = insert(model).values(rows).on_conflict_do_nothing(index_elements=conflict or ["id"])
        session.execute(stmt)


def reset(session: Session, fixture: dict[str, Any]) -> None:
    tenant_ids = [fixture_id(t["key"]) for t in fixture["tenants"]]
    synthetic = list(session.scalars(select(m.Tenant.id).where(m.Tenant.id.in_(tenant_ids), m.Tenant.is_synthetic.is_(True))))
    if not synthetic:
        _reset_local_warehouse(tenant_ids)  # first run: creates the warehouse schema, clears nothing
        return
    # asset_versions <-> confirmations reference each other; break the cycle first.
    session.execute(update(m.AssetVersion).where(m.AssetVersion.tenant_id.in_(synthetic)).values(confirmation_id=None))
    for model in RESET_ORDER:
        session.execute(delete(model).where(model.tenant_id.in_(synthetic)))
    session.execute(delete(m.Tenant).where(m.Tenant.id.in_(synthetic)))
    _reset_local_warehouse(synthetic)


def _reset_local_warehouse(tenant_ids: list[uuid.UUID]) -> None:
    """The local warehouse is an analytical copy; a demo reset clears it for synthetic tenants."""
    from reclaim_api.analytics.warehouse import LocalWarehouse
    from reclaim_api.config import get_settings, provider
    from reclaim_api.events.envelope import pseudonym
    from reclaim_api.telegram.linking import hash_key

    settings = get_settings()
    if provider(settings, "warehouse") != "local":
        return
    key = hash_key(settings)
    LocalWarehouse(get_engine()).reset_tenants([pseudonym(t, key) for t in tenant_ids])


def seed(session: Session, state: str = "before", *, do_reset: bool = False, fixture: dict[str, Any] | None = None) -> dict[str, int]:
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    fx = fixture or load_fixture()
    if do_reset:
        reset(session, fx)

    after_only = [a for a in fx["assets"] if a.get("state") == "after"]
    if state == "before" and any(session.get(m.Asset, fixture_id(a["key"])) for a in after_only):
        raise SeedStateError("database holds the 'after' fixture state; rerun with --reset to return to 'before'")

    tid = {t["key"]: fixture_id(t["key"]) for t in fx["tenants"]}
    uid = {u["key"]: fixture_id(u["key"]) for u in fx["users"]}

    _put(session, m.Tenant, [{"id": tid[t["key"]], "name": t["name"], "is_synthetic": True} for t in fx["tenants"]])
    _put(
        session,
        m.User,
        [
            {"id": uid[u["key"]], "tenant_id": tid[u["tenant"]], "display_name": u["display_name"], "email": u["email"], "role": u["role"]}
            for u in fx["users"]
        ],
    )

    docs = [d for d in fx["documents"] if _in_state(d, state)]
    # Originals before re-uploads, so duplicate_of references resolve.
    for d in sorted(docs, key=lambda d: "duplicate_of" in d):
        content = d["content"].encode("utf-8")
        _put(
            session,
            m.Document,
            [
                {
                    "id": fixture_id(d["key"]),
                    "tenant_id": tid[d["tenant"]],
                    "owner_user_id": uid[d["owner"]],
                    "kind": d["kind"],
                    "title": d["title"],
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mime_type": "text/plain",
                    "size_bytes": len(content),
                    "status": "fixture",
                    "duplicate_of_document_id": fixture_id(d["duplicate_of"]) if "duplicate_of" in d else None,
                    "source_date": _date(d.get("source_date")),
                }
            ],
        )

    for a in fx["assets"]:
        if _in_state(a, state):
            _seed_asset(session, a, tid, uid)

    for r in fx["relationships"]:
        _put(
            session,
            m.Relationship,
            [
                {
                    "id": fixture_id(r["key"]),
                    "tenant_id": tid[r["tenant"]],
                    "kind": r["kind"],
                    "from_kind": r["from"][0],
                    "from_id": fixture_id(r["from"][1]),
                    "to_kind": r["to"][0],
                    "to_id": fixture_id(r["to"][1]),
                    "detail": r.get("detail"),
                    "verification_state": r["verification_state"],
                    "evidence_document_id": fixture_id(r["evidence_document"]) if "evidence_document" in r else None,
                    "created_by_user_id": uid[r["created_by"]],
                }
            ],
        )

    _put(
        session,
        m.AccessGrant,
        [
            {
                "id": fixture_id(g["key"]),
                "tenant_id": tid[g["tenant"]],
                "owner_user_id": uid[g["owner"]],
                "grantee_user_id": uid[g["grantee"]],
                "stage": g["stage"],
                "scope": {"asset_ids": [str(fixture_id(k)) for k in g["asset_keys"]], "sections": g["sections"]},
            }
            for g in fx["grants"]
        ],
    )

    # Recovery profiles exist but are not opted in: searching needs the owner's explicit consent.
    _put(
        session,
        m.RecoveryProfile,
        [
            {"id": fixture_id(f"recovery_profile:{r['owner']}"), "tenant_id": tid[r["tenant"]], "owner_user_id": uid[r["owner"]],
             "legal_name": r["legal_name"], "name_variants": r["name_variants"], "date_of_birth": _date(r.get("date_of_birth")),
             "addresses": r["addresses"]}
            for r in fx.get("recovery_profiles", [])
        ],
    )  # fmt: skip

    for t in fx["tenants"]:
        _put(
            session,
            m.AuditEvent,
            [
                {
                    "id": fixture_id(f"audit:seed:{t['key']}:{state}"),
                    "tenant_id": tid[t["key"]],
                    "action": "fixture.seeded",
                    "object_type": "tenant",
                    "object_id": tid[t["key"]],
                    "details": {"state": state, "fixture_version": fx["fixture_version"]},
                }
            ],
        )

    session.flush()
    # Read models for the seeded assets, as the worker would have produced them.
    from reclaim_api.events.rebuild import rebuild

    rebuild(session, today=date.today(), tenant_ids=list(tid.values()))
    return counts(session, list(tid.values()))


def _seed_asset(session: Session, a: dict[str, Any], tid: dict[str, uuid.UUID], uid: dict[str, uuid.UUID]) -> None:
    asset_id = fixture_id(a["key"])
    tenant_id = tid[a["tenant"]]
    owner_id = uid[a["owner"]]
    versions = a["versions"]
    _put(
        session,
        m.Asset,
        [{"id": asset_id, "tenant_id": tenant_id, "owner_user_id": owner_id, "asset_type": a["type"], "current_version": len(versions)}],
    )

    proposal_id = confirmation_id = None
    if "proposal" in a:
        p = a["proposal"]
        proposal_id = fixture_id(f"{a['key']}:proposal")
        confirmation_id = fixture_id(f"{a['key']}:confirmation")
        first = {k: v for k, v in versions[0].items() if k not in ("evidence", "change_kind")}
        _put(
            session,
            m.Proposal,
            [
                {
                    "id": proposal_id,
                    "tenant_id": tenant_id,
                    "owner_user_id": owner_id,
                    "change_kind": "create",
                    "proposed_fields": {"asset_type": a["type"], **first},
                    "evidence_refs": [str(fixture_id(e["document"])) for e in versions[0]["evidence"] if "document" in e],
                    "status": "confirmed",
                    "channel": p["channel"],
                    "model_version": p["model_version"],
                }
            ],
        )

    for n, v in enumerate(versions, start=1):
        version_id = fixture_id(f"{a['key']}:v{n}")
        _put(
            session,
            m.AssetVersion,
            [
                {
                    "id": version_id,
                    "tenant_id": tenant_id,
                    "asset_id": asset_id,
                    "asset_type": a["type"],
                    "version": n,
                    "change_kind": v["change_kind"],
                    "label": v.get("label"),
                    "institution_name": v.get("institution_name"),
                    "jurisdiction": v["jurisdiction"],
                    "masked_reference": v.get("masked_reference"),
                    "currency": v.get("currency"),
                    "balance_minor": v.get("balance_minor"),
                    "estimated_value_minor": v.get("estimated_value_minor"),
                    "insurance_cover_minor": v.get("insurance_cover_minor"),
                    "sip_installment_minor": v.get("sip_installment_minor"),
                    "liability_minor": v.get("liability_minor"),
                    "opened_on": _date(v.get("opened_on")),
                    "maturity_on": _date(v.get("maturity_on")),
                    "term_months": v.get("term_months"),
                    "nomination_status": v["nomination_status"],
                    "document_location": v.get("document_location"),
                    "source_date": _date(v.get("source_date")),
                    "verification_state": v["verification_state"],
                    "created_by_user_id": owner_id,
                    "confirmation_id": None,
                }
            ],
        )
        _put(
            session,
            m.FieldEvidence,
            [
                {
                    "id": fixture_id(f"{a['key']}:v{n}:ev{i}"),
                    "tenant_id": tenant_id,
                    "asset_version_id": version_id,
                    "field_name": e["field"],
                    "observed_value": e.get("observed"),
                    "source_kind": e["source"],
                    "document_id": fixture_id(e["document"]) if "document" in e else None,
                    "page": e.get("page"),
                    "confidence": e.get("confidence"),
                    "extractor_version": "fixture-v1" if e["source"] == "document" else None,
                    "source_date": _date(v.get("source_date")),
                    "conflicts_with_current": e.get("conflicts_with_current", False),
                }
                for i, e in enumerate(v["evidence"])
            ],
        )

    if proposal_id and confirmation_id:
        version_id = fixture_id(f"{a['key']}:v1")
        _put(
            session,
            m.Confirmation,
            [
                {
                    "id": confirmation_id,
                    "tenant_id": tenant_id,
                    "proposal_id": proposal_id,
                    "confirmed_by_user_id": owner_id,
                    "idempotency_key": f"fixture:{a['key']}",
                    "asset_version_id": version_id,
                }
            ],
        )
        session.execute(update(m.AssetVersion).where(m.AssetVersion.id == version_id).values(confirmation_id=confirmation_id))
        _put(
            session,
            m.OutboxEvent,
            [
                {
                    "id": fixture_id(f"{a['key']}:event:v1"),
                    "tenant_id": tenant_id,
                    "aggregate_type": "asset",
                    "aggregate_id": asset_id,
                    "aggregate_version": 1,
                    "event_type": "asset.version_confirmed",
                    "payload": {"asset_version_id": str(version_id), "change_kind": "create"},
                }
            ],
        )


def counts(session: Session, tenant_ids: list[uuid.UUID]) -> dict[str, int]:
    from sqlalchemy import func

    tables = (m.User, m.Asset, m.AssetVersion, m.FieldEvidence, m.Document, m.Relationship, m.AccessGrant, m.Proposal, m.Confirmation, m.OutboxEvent)
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(tenant_ids))) or 0
        for model in tables
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load deterministic synthetic demo fixtures.")
    parser.add_argument("--state", choices=STATES, default="before")
    parser.add_argument("--reset", action="store_true", help="delete the synthetic demo tenants first")
    args = parser.parse_args()
    try:
        with Session(get_engine()) as session, session.begin():
            result = seed(session, args.state, do_reset=args.reset)
    except SeedStateError as err:
        print(f"seed refused: {err.args[0]}", file=sys.stderr)  # our own message, no user data
        raise SystemExit(2) from None
    summary = " ".join(f"{table}={n}" for table, n in result.items())
    print(f"seeded synthetic state={args.state} {summary}")

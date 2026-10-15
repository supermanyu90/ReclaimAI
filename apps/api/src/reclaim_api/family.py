"""What a designated family member sees.

Before release: only the sections the owner allowed in advance (contacts, where papers are
kept); no asset details. After a reviewed release, while the grant stays active: a read-only
inventory of the granted assets only, the current passport export, an evidence index and
claim checklists. Every view is audited. Access to information is not ownership.
"""

import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from reclaim_api import handover, records
from reclaim_api import models as m
from reclaim_api.auth import Actor

NOTICE = "Seeing these records does not make anyone the owner or entitle anyone to them. Institutions decide claims and payments."


class Forbidden(PermissionError):
    pass


def _audit(session: Session, actor: Actor, action: str, owner_id: uuid.UUID, **details: Any) -> None:
    session.add(m.AuditEvent(tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action=action, object_type="owner", object_id=owner_id, details=details))


def _grants(session: Session, actor: Actor) -> dict[uuid.UUID, dict[str, m.AccessGrant]]:
    out: dict[uuid.UUID, dict[str, m.AccessGrant]] = {}
    for g in session.scalars(select(m.AccessGrant).where(m.AccessGrant.tenant_id == actor.tenant_id, m.AccessGrant.grantee_user_id == actor.user_id,
                                                         m.AccessGrant.status == "active")):  # fmt: skip
        out.setdefault(g.owner_user_id, {})[g.stage] = g
    return out


def released_asset(session: Session, actor: Actor, asset_id: uuid.UUID) -> dict[str, Any]:
    """Resolver for granted assets after release; NotFound for anything else (hidden assets
    look exactly like missing ones)."""
    asset = session.scalar(select(m.Asset).where(m.Asset.id == asset_id, m.Asset.tenant_id == actor.tenant_id))
    if asset is None or handover.released_for(session, actor, asset.owner_user_id) is None:
        raise records.NotFound
    grant = handover._grant(session, asset.owner_user_id, actor.user_id)
    if str(asset_id) not in grant.scope.get("asset_ids", []):
        raise records.NotFound
    version = session.scalar(select(m.AssetVersion).where(m.AssetVersion.asset_id == asset.id, m.AssetVersion.version == asset.current_version))
    view = records._view(asset, version, conflicts=0)
    if "document_locations" not in grant.scope.get("sections", []):
        view["document_location"] = None
    return view


def _pre_release(session: Session, owner_id: uuid.UUID, grant: m.AccessGrant | None) -> dict[str, Any]:
    sections = grant.scope.get("sections", []) if grant else []
    out: dict[str, Any] = {"sections": sections}
    if "contacts" in sections:
        users = {u.id: u.display_name for u in session.scalars(select(m.User).where(m.User.tenant_id == grant.tenant_id))}
        out["contacts"] = [{"name": users.get(r.to_id), "role": r.kind.replace("_", " ")} for r in session.scalars(
            select(m.Relationship).where(m.Relationship.tenant_id == grant.tenant_id, m.Relationship.from_id == owner_id,
                                         m.Relationship.kind.in_(("trusted_contact", "executor"))))]  # fmt: skip
    if "document_locations" in sections:
        locations = session.scalars(
            select(m.AssetVersion.document_location)
            .join(m.Asset, and_(m.Asset.id == m.AssetVersion.asset_id, m.Asset.current_version == m.AssetVersion.version))
            .where(m.Asset.owner_user_id == owner_id, m.Asset.status == "active", m.AssetVersion.document_location.is_not(None))
            .order_by(m.AssetVersion.document_location)
        ).all()
        out["document_locations"] = sorted(set(locations))  # where papers are kept; no asset details
    return out


def overview(session: Session, actor: Actor) -> dict[str, Any]:
    if actor.role != "family":
        raise Forbidden
    people = []
    for owner_id, grants in _grants(session, actor).items():
        owner = session.get(m.User, owner_id)
        case = session.scalar(select(m.HandoverRequest).where(m.HandoverRequest.owner_user_id == owner_id, m.HandoverRequest.requester_user_id == actor.user_id)
                              .order_by(m.HandoverRequest.created_at.desc()).limit(1))  # fmt: skip
        entry: dict[str, Any] = {
            "owner_user_id": str(owner_id), "owner": owner.display_name, "designated": "post_release" in grants,
            "handover": {"id": str(case.id), "state": case.state} if case else None,
            "before_release": _pre_release(session, owner_id, grants.get("pre_handover")),
            "simulated_verification": handover.SIMULATED,
        }  # fmt: skip
        if handover.released_for(session, actor, owner_id):
            entry["status_text"] = handover.RELEASED_STATUS
            entry["after_release"] = released_view(session, actor, owner_id)
        people.append(entry)
        _audit(session, actor, "family.overview_viewed", owner_id, released=bool(entry.get("after_release")))
    session.flush()
    return {"people": people, "notice": NOTICE}


def released_view(session: Session, actor: Actor, owner_id: uuid.UUID) -> dict[str, Any]:
    grant = handover._grant(session, owner_id, actor.user_id)
    assets = []
    for a in grant.scope.get("asset_ids", []):
        try:
            assets.append(released_asset(session, actor, uuid.UUID(a)))
        except records.NotFound:
            continue  # closed or removed since: not shown
    ids = [uuid.UUID(a["id"]) for a in assets]
    docs = session.execute(
        select(m.Document.title, m.Document.kind, m.Document.source_date, m.AssetVersion.label)
        .join(m.FieldEvidence, m.FieldEvidence.document_id == m.Document.id)
        .join(m.AssetVersion, m.AssetVersion.id == m.FieldEvidence.asset_version_id)
        .join(m.Asset, and_(m.Asset.id == m.AssetVersion.asset_id, m.Asset.current_version == m.AssetVersion.version))
        .where(m.Asset.id.in_(ids)).distinct()
    ).all() if ids and "evidence_index" in grant.scope.get("sections", []) else []  # fmt: skip
    passport = session.scalar(select(m.PassportVersion.id).where(m.PassportVersion.owner_user_id == owner_id, m.PassportVersion.recipient_user_id == actor.user_id,
                                                                 m.PassportVersion.status == "current"))  # fmt: skip
    _audit(session, actor, "family.inventory_viewed", owner_id, assets=len(assets))
    return {
        "read_only": True,
        "assets": assets,
        "evidence_index": [{"title": t, "kind": k, "source_date": d.isoformat() if d else None, "supports": label} for t, k, d, label in docs],
        "passport_id": str(passport) if passport else None,
        "claim_checklists": "claim_checklist" in grant.scope.get("sections", []),
        "totals": _totals(assets),
    }


def _totals(assets: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for a in assets:
        if a["currency"] is None:
            continue
        for k, v in a["amounts"].items():
            out.setdefault(a["currency"], {}).setdefault(k, 0)
            out[a["currency"]][k] += v
    return out

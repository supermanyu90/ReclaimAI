"""Owner workspace: assets, a graph of recorded relationships, timeline, review queue,
freshness findings and a preview of what designated family can see.

The graph contains only what is recorded: owner → asset → institution, owner → person
(as recorded, e.g. "daughter"), person → asset for registered nominees, and document →
asset/relationship for supporting evidence. Assets are never nested under other assets,
and no score summarises the estate.
"""

import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals, records
from reclaim_api.auth import Actor

FINDING_LABELS = {
    "stale_source": "Old information",
    "no_source_date": "No date on the information",
    "unknown_nomination": "Nomination unknown",
    "missing_document_location": "Paper location not recorded",
}


class Forbidden(PermissionError):
    pass


def _owner(actor: Actor) -> None:
    if actor.role != "owner":
        raise Forbidden


def graph(session: Session, actor: Actor, assets: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {f"user:{actor.user_id}": {"id": f"user:{actor.user_id}", "kind": "owner", "label": "You"}}
    edges: list[dict[str, Any]] = []
    asset_ids = {uuid.UUID(a["id"]) for a in assets}
    for a in assets:
        aid = f"asset:{a['id']}"
        nodes[aid] = {"id": aid, "kind": "asset", "label": a["label"], "asset_type": a["asset_type"], "jurisdiction": a["jurisdiction"], "status": a["status"]}
        edges.append({"from": f"user:{actor.user_id}", "to": aid, "kind": "owns", "basis": a["verification_state"]})
        if a["institution_name"]:
            iid = f"institution:{a['institution_name']}|{a['jurisdiction']}"
            nodes.setdefault(iid, {"id": iid, "kind": "institution", "label": a["institution_name"], "jurisdiction": a["jurisdiction"]})
            edges.append({"from": aid, "to": iid, "kind": "held_at", "basis": a["verification_state"]})

    users = {u.id: u for u in session.scalars(select(m.User).where(m.User.tenant_id == actor.tenant_id))}
    rels = session.scalars(select(m.Relationship).where(m.Relationship.tenant_id == actor.tenant_id)).all()
    for r in rels:
        ends = []
        for kind, ident in ((r.from_kind, r.from_id), (r.to_kind, r.to_id)):
            if kind == "user" and ident in users:
                key = f"user:{ident}"
                if key not in nodes:
                    nodes[key] = {"id": key, "kind": "person", "label": users[ident].display_name}
                ends.append(key)
            elif kind == "asset" and ident in asset_ids:
                ends.append(f"asset:{ident}")
        if len(ends) == 2:
            edges.append({"from": ends[0], "to": ends[1], "kind": r.kind, "detail": r.detail, "basis": r.verification_state,
                          "relationship_id": str(r.id)})  # fmt: skip
        if r.evidence_document_id and len(ends) == 2:
            did = f"document:{r.evidence_document_id}"
            nodes.setdefault(did, {"id": did, "kind": "document", "label": None})
            edges.append({"from": did, "to": ends[1], "kind": "supports", "field": r.kind})

    current = (
        select(m.FieldEvidence.document_id, m.FieldEvidence.field_name, m.AssetVersion.asset_id)
        .join(m.AssetVersion, m.AssetVersion.id == m.FieldEvidence.asset_version_id)
        .join(m.Asset, and_(m.Asset.id == m.AssetVersion.asset_id, m.Asset.current_version == m.AssetVersion.version))
        .where(m.Asset.tenant_id == actor.tenant_id, m.Asset.owner_user_id == actor.user_id, m.FieldEvidence.document_id.is_not(None))
    )
    supports: dict[tuple[str, str], set[str]] = {}
    for doc_id, field, asset_id in session.execute(current):
        supports.setdefault((str(doc_id), str(asset_id)), set()).add(field)
    docs = {d.id: d for d in session.scalars(select(m.Document).where(m.Document.tenant_id == actor.tenant_id))}
    for (doc_id, asset_id), fields in sorted(supports.items()):
        did = f"document:{doc_id}"
        nodes.setdefault(did, {"id": did, "kind": "document", "label": None})
        edges.append({"from": did, "to": f"asset:{asset_id}", "kind": "supports", "field": ", ".join(sorted(fields))})
    for key, node in nodes.items():
        if node["kind"] == "document":
            d = docs.get(uuid.UUID(key.split(":", 1)[1]))
            node.update(label=d.title if d else "Document", status=d.status if d else None, source_date=d.source_date.isoformat() if d and d.source_date else None)
    return {"nodes": sorted(nodes.values(), key=lambda n: (n["kind"], n["label"] or "")), "edges": edges}


def timeline(session: Session, actor: Actor, limit: int = 60) -> list[dict[str, Any]]:
    labels = {uuid.UUID(a["id"]): a["label"] for a in records.list_assets(session, actor)}
    out = []
    for v in session.scalars(select(m.AssetVersion).where(m.AssetVersion.tenant_id == actor.tenant_id, m.AssetVersion.asset_id.in_(labels))):
        what = {"create": "Recorded", "update": "Updated", "correct": "Corrected", "close": "Marked closed"}[v.change_kind]
        out.append({"at": v.created_at.isoformat(), "kind": f"asset.{v.change_kind}", "text": f"{what}: {labels[v.asset_id]} (version {v.version}, {v.verification_state.replace('_', ' ')})"})
    for d in session.scalars(select(m.Document).where(m.Document.tenant_id == actor.tenant_id, m.Document.owner_user_id == actor.user_id)):
        out.append({"at": d.ingested_at.isoformat(), "kind": "document.uploaded", "text": f"Document added: {d.title} ({d.status.replace('_', ' ')})"})
    for p in session.scalars(select(m.PassportVersion).where(m.PassportVersion.tenant_id == actor.tenant_id, m.PassportVersion.owner_user_id == actor.user_id)):
        who = "your passport" if p.recipient_user_id is None else "a family export"
        out.append({"at": p.generated_at.isoformat(), "kind": "passport.generated", "text": f"Generated {who}, version {p.version}"})
    return sorted(out, key=lambda e: e["at"], reverse=True)[:limit]


def review_queue(session: Session, actor: Actor) -> list[dict[str, Any]]:
    items = []
    for p in session.scalars(select(m.Proposal).where(m.Proposal.tenant_id == actor.tenant_id, m.Proposal.owner_user_id == actor.user_id,
                                                      m.Proposal.status.in_(("draft", "awaiting_confirmation")))):  # fmt: skip
        open_conflicts = len(proposals.unresolved(p.conflicts))
        items.append({"kind": "proposal", "id": str(p.id), "text": f"Draft ({p.change_kind}) awaiting you" + (f"; {open_conflicts} conflict(s) to resolve" if open_conflicts else "")})
    for d in session.scalars(select(m.Document).where(m.Document.tenant_id == actor.tenant_id, m.Document.owner_user_id == actor.user_id, m.Document.status == "needs_review")):
        items.append({"kind": "document", "id": str(d.id), "text": f"{d.title}: {'; '.join(d.review_reasons) or 'needs review'}"})
    queued = session.scalar(select(m.ReportJob.id).where(m.ReportJob.tenant_id == actor.tenant_id, m.ReportJob.owner_user_id == actor.user_id, m.ReportJob.status == "queued").limit(1))
    if queued:
        items.append({"kind": "passport", "id": None, "text": "Records changed since your last passport. Generate a new version when ready."})
    return items


def findings(session: Session, actor: Actor) -> list[dict[str, Any]]:
    rows = session.execute(
        select(m.FreshnessFinding, m.AssetVersion.label)
        .join(m.Asset, m.Asset.id == m.FreshnessFinding.asset_id)
        .join(m.AssetVersion, and_(m.AssetVersion.asset_id == m.Asset.id, m.AssetVersion.version == m.Asset.current_version))
        .where(m.Asset.tenant_id == actor.tenant_id, m.Asset.owner_user_id == actor.user_id)
        .order_by(m.FreshnessFinding.kind, m.AssetVersion.label)
    ).all()
    return [{"asset_id": str(f.asset_id), "asset": label, "kind": f.kind, "label": FINDING_LABELS[f.kind], "detail": f.detail} for f, label in rows]


def access_preview(session: Session, actor: Actor, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What each designated person can see before handover and after a reviewed release."""
    by_id = {a["id"]: a for a in assets}
    grants = session.scalars(select(m.AccessGrant).where(m.AccessGrant.tenant_id == actor.tenant_id, m.AccessGrant.owner_user_id == actor.user_id,
                                                         m.AccessGrant.status == "active")).all()  # fmt: skip
    people: dict[uuid.UUID, dict[str, Any]] = {}
    for g in grants:
        person = people.setdefault(g.grantee_user_id, {"grantee_user_id": str(g.grantee_user_id), "name": session.get(m.User, g.grantee_user_id).display_name})
        visible = [by_id[a] for a in g.scope.get("asset_ids", []) if a in by_id]
        person[g.stage] = {
            "sections": g.scope.get("sections", []), "grant_version": g.version,
            "assets": [{"label": a["label"], "institution": a["institution_name"]} for a in visible],
            "hidden_assets": [a["label"] for a in assets if a["id"] not in {v["id"] for v in visible}],
        }  # fmt: skip
    for p in people.values():
        p["note"] = "Before handover only the listed sections are visible. After a reviewed release, only the listed assets. Access never means ownership."
    return list(people.values())


def overview(session: Session, actor: Actor) -> dict[str, Any]:
    _owner(actor)
    assets = records.list_assets(session, actor)
    return {
        "assets": assets,
        "totals": records.inventory_totals(session, actor),
        "graph": graph(session, actor, [a for a in assets if a["status"] == "active"]),
        "timeline": timeline(session, actor),
        "review_queue": review_queue(session, actor),
        "findings": findings(session, actor),
        "access_preview": access_preview(session, actor, [a for a in assets if a["status"] == "active"]),
        "notice": "Recorded information only. ReclaimAI does not establish completeness, ownership or entitlement.",
    }

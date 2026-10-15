"""Family Asset Passport: a deterministic, versioned PDF from an approved SQL snapshot.

Generation is an explicit owner action. The snapshot is read in one REPEATABLE READ
transaction from confirmed (current) asset versions only; it is canonical JSON with a
SHA-256 hash, and the same snapshot always renders to the same PDF bytes. Recipient exports
contain only the assets and sections in that person's post-release grant. Every earlier
current version for the same audience becomes stale. Downloads check permission at the time
of the download, but a copy already downloaded cannot be revoked; the PDF says so.
"""

import hashlib
import io
import json
import uuid
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals
from reclaim_api.auth import Actor
from reclaim_api.db import get_engine

NOTICES = [
    ("This passport lists what the owner recorded. It does not establish that the list is complete, "
    "who owns any asset, or who is entitled to it. Institutions decide claims and make payments."),
    ("Verification states: user reported = stated by the owner; document extracted = read from a document "
    "(not proof that the document is genuine). Nothing here is verified by an institution."),
    "References are masked. Never share passwords, PINs or OTPs with anyone.",
    "A downloaded copy of this file cannot be revoked. Keep it private.",
]
KIND_LABELS = {"balance_minor": "Balance / principal", "estimated_value_minor": "Estimated value", "insurance_cover_minor": "Insurance cover",
               "sip_installment_minor": "SIP installment (monthly)", "liability_minor": "Loan outstanding"}  # fmt: skip


class Forbidden(PermissionError):
    pass


class NotFound(LookupError):
    pass


def ascii_money(minor: int | None, currency: str | None) -> str:
    """'INR 5,00,000' / 'SGD 18,250.00': portable in any PDF font."""
    text_ = proposals.format_money(minor, currency) or ""
    return text_.replace("₹", "INR ").replace("S$", "SGD ")


def _mask(ref: str | None) -> str | None:
    return ref.replace("•", "*") if ref else None


# --- snapshot -----------------------------------------------------------------------------


def build_snapshot(session: Session, owner: m.User, *, recipient: m.User | None, as_of: date) -> dict[str, Any]:
    rows = session.execute(
        select(m.Asset, m.AssetVersion)
        .join(m.AssetVersion, and_(m.AssetVersion.asset_id == m.Asset.id, m.AssetVersion.version == m.Asset.current_version))
        .where(m.Asset.tenant_id == owner.tenant_id, m.Asset.owner_user_id == owner.id, m.Asset.status == "active")
        .order_by(m.AssetVersion.label, m.Asset.id)
    ).all()
    sections = {"inventory", "contacts", "document_locations", "evidence_index", "next_steps"}
    scope_ids: set[str] | None = None
    grant_version = None
    if recipient is not None:
        grant = session.scalar(
            select(m.AccessGrant).where(m.AccessGrant.owner_user_id == owner.id, m.AccessGrant.grantee_user_id == recipient.id,
                                        m.AccessGrant.stage == "post_release", m.AccessGrant.status == "active")
        )
        if grant is None:
            raise Forbidden("no active post-release grant for this recipient")
        scope_ids = set(grant.scope.get("asset_ids", []))
        allowed = set(grant.scope.get("sections", []))
        sections = {"inventory"} | ({"evidence_index"} & allowed) | ({"next_steps"} if "claim_checklist" in allowed else set())
        grant_version = grant.version
    rows = [(a, v) for a, v in rows if scope_ids is None or str(a.id) in scope_ids]

    version_ids = [v.id for _, v in rows]
    evidence = session.execute(
        select(m.FieldEvidence.asset_version_id, m.FieldEvidence.field_name, m.FieldEvidence.page, m.Document.id, m.Document.title, m.Document.kind, m.Document.source_date)
        .join(m.Document, m.Document.id == m.FieldEvidence.document_id)
        .where(m.FieldEvidence.asset_version_id.in_(version_ids))
        .order_by(m.Document.title, m.FieldEvidence.field_name)
    ).all() if version_ids else []  # fmt: skip
    doc_index: dict[str, dict[str, Any]] = {}
    for _, _, _, doc_id, title, kind, source_date in evidence:
        doc_index.setdefault(str(doc_id), {"title": title, "kind": kind, "source_date": source_date.isoformat() if source_date else None})
    refs = {doc_id: f"D{i}" for i, doc_id in enumerate(sorted(doc_index, key=lambda d: (doc_index[d]["title"], d)), start=1)}

    findings = {
        (f.asset_id, f.kind): f.detail
        for f in session.scalars(select(m.FreshnessFinding).where(m.FreshnessFinding.asset_id.in_([a.id for a, _ in rows])))
    } if rows else {}  # fmt: skip

    assets, totals = [], {}
    for a, v in rows:
        amounts = {k: getattr(v, k) for k in KIND_LABELS if getattr(v, k) is not None}
        for k, minor in amounts.items():
            bucket = totals.setdefault(v.currency, {})
            bucket[k] = bucket.get(k, 0) + minor
        sources = sorted({(refs[str(doc_id)], field, page) for vid, field, page, doc_id, *_ in evidence if vid == v.id})
        assets.append({
            "id": str(a.id), "version": v.version, "label": v.label, "asset_type": a.asset_type,
            "institution": v.institution_name, "jurisdiction": v.jurisdiction, "reference": _mask(v.masked_reference),
            "currency": v.currency, "amounts": amounts, "source_date": v.source_date.isoformat() if v.source_date else None,
            "verification_state": v.verification_state, "nomination_status": v.nomination_status,
            "document_location": v.document_location if "document_locations" in sections else None,
            "sources": [{"document": d, "field": f, "page": p} for d, f, p in sources] if "evidence_index" in sections else [],
            "findings": sorted(detail for (aid, _), detail in findings.items() if aid == a.id) if "next_steps" in sections else [],
        })  # fmt: skip

    contacts = []
    if "contacts" in sections:
        users = {u.id: u.display_name for u in session.scalars(select(m.User).where(m.User.tenant_id == owner.tenant_id))}
        labels = {a["id"]: a["label"] for a in assets}
        for r in session.scalars(select(m.Relationship).where(m.Relationship.tenant_id == owner.tenant_id).order_by(m.Relationship.kind, m.Relationship.id)):
            if r.kind in ("trusted_contact", "registered_nominee", "executor", "legal_heir"):
                person = users.get(r.to_id if r.kind == "trusted_contact" else r.from_id)
                about = labels.get(str(r.to_id)) if r.to_kind == "asset" else None
                if person:
                    contacts.append({"name": person, "role": r.kind.replace("_", " "), "for": about, "basis": r.verification_state.replace("_", " ")})

    return {
        "schema": 1,
        "synthetic": True,
        "as_of": as_of.isoformat(),
        "owner": owner.display_name,
        "audience": "owner" if recipient is None else f"export for {recipient.display_name}",
        "grant_version": grant_version,
        "sections": sorted(sections),
        "assets": assets,
        "totals": {cur: {k: totals[cur][k] for k in sorted(totals[cur])} for cur in sorted(totals)},
        "documents": [{"ref": refs[d], **doc_index[d]} for d in sorted(doc_index, key=lambda d: refs[d])] if "evidence_index" in sections else [],
        "contacts": contacts,
        "notices": NOTICES,
    }


def canonical(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# --- rendering ---------------------------------------------------------------------------------


def render(snapshot: dict[str, Any], *, version: int, snapshot_sha: str) -> bytes:
    """Deterministic: same snapshot → same bytes (ReportLab invariant mode, no timestamps)."""
    styles = getSampleStyleSheet()
    body, small = styles["BodyText"], ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    h1, h2 = styles["Title"], styles["Heading2"]

    def p(t: str, style=body) -> Paragraph:
        return Paragraph(escape(t), style)

    def table(data: list[list[Any]], widths: list[float]) -> Table:
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6d3d1")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))  # fmt: skip
        return t

    s = snapshot
    story: list[Any] = [
        p("Family Asset Passport", h1),
        p(f"SYNTHETIC DEMO. Owner: {s['owner']}. For: {'the owner' if s['audience'] == 'owner' else s['audience'].removeprefix('export for ')}. Version {version}. Records as of {s['as_of']}."),
        Spacer(1, 4 * mm),
    ]
    story += [p(n, small) for n in s["notices"]] + [Spacer(1, 4 * mm)]

    story.append(p("Totals by currency (recorded assets only; kinds are never added together)", h2))
    rows = [["Currency", "Kind", "Total"]] + [[cur, KIND_LABELS[k], ascii_money(v, cur)] for cur, kinds in s["totals"].items() for k, v in kinds.items()]
    story += [table(rows, [25 * mm, 70 * mm, 50 * mm]), Spacer(1, 4 * mm)]

    story.append(p("Recorded assets", h2))
    for a in s["assets"]:
        lines = [f"{a['label']} - {a['asset_type'].replace('_', ' ')}, {a['institution'] or 'no institution recorded'} ({a['jurisdiction']})"]
        if a["reference"]:
            lines.append(f"Reference: {a['reference']}")
        for k, v in a["amounts"].items():
            lines.append(f"{KIND_LABELS[k]}: {ascii_money(v, a['currency'])}")
        lines.append(f"As of: {a['source_date'] or 'unknown'}. Verification: {a['verification_state'].replace('_', ' ')}. "
                     f"Nomination: {a['nomination_status'].replace('_', ' ')}.")  # fmt: skip
        if a["document_location"]:
            lines.append(f"Papers kept at: {a['document_location']}")
        if a["sources"]:
            lines.append("Sources: " + "; ".join(f"{x['document']} p.{x['page'] or '?'} ({x['field'].replace('_minor', '').replace('_', ' ')})" for x in a["sources"]))
        for f in a["findings"]:
            lines.append(f"To check: {f}")
        story += [p(lines[0], styles["Heading4"])] + [p(line, small) for line in lines[1:]] + [Spacer(1, 2 * mm)]

    if s["documents"]:
        story.append(p("Document index", h2))
        story.append(table([["Ref", "Document", "Kind", "Date"]] + [[d["ref"], d["title"], d["kind"].replace("_", " "), d["source_date"] or "-"] for d in s["documents"]],
                           [12 * mm, 90 * mm, 35 * mm, 25 * mm]))  # fmt: skip
    if s["contacts"]:
        story.append(p("Authorised contacts (as recorded by the owner)", h2))
        story.append(table([["Name", "Role", "For", "Basis"]] + [[c["name"], c["role"], c["for"] or "-", c["basis"]] for c in s["contacts"]],
                           [45 * mm, 40 * mm, 55 * mm, 30 * mm]))  # fmt: skip
    if "next_steps" in s["sections"]:
        story.append(p("Next steps", h2))
        for step in ("Contact each institution to confirm the account exists and what it needs; institutions decide claims.",
                     "Use the claim preparation checklist in ReclaimAI for cited, policy-specific document lists.",
                     "Resolve any item marked 'To check' above with the institution."):  # fmt: skip
            story.append(p(f"- {step}", small))

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, f"ReclaimAI passport v{version} | snapshot {snapshot_sha[:16]} | page {doc.page} | synthetic demo")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, invariant=1, title="Family Asset Passport", author="ReclaimAI",
                            leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm)  # fmt: skip
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# --- generation, listing, download ----------------------------------------------------------------------


def _view(pv: m.PassportVersion, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {"id": str(pv.id), "version": pv.version, "status": pv.status, "audience": "owner" if pv.recipient_user_id is None else names.get(pv.recipient_user_id),
            "recipient_user_id": str(pv.recipient_user_id) if pv.recipient_user_id else None, "generated_at": pv.generated_at.isoformat(),
            "stale_at": pv.stale_at.isoformat() if pv.stale_at else None, "snapshot_sha256": pv.snapshot_sha256, "pdf_sha256": pv.pdf_sha256,
            "assets": len(pv.snapshot.get("assets", []))}  # fmt: skip


def generate(session: Session, actor: Actor, *, recipient_id: uuid.UUID | None, store: Any, today: date) -> dict[str, Any]:
    if actor.role != "owner":
        raise Forbidden("only the owner generates a passport")
    owner = session.get(m.User, actor.user_id)
    recipient = None
    if recipient_id is not None:
        recipient = session.get(m.User, recipient_id)
        if recipient is None or recipient.tenant_id != actor.tenant_id:
            raise NotFound
    # One consistent read of everything that goes into the snapshot.
    with Session(get_engine().execution_options(isolation_level="REPEATABLE READ")) as snap:
        snapshot = build_snapshot(snap, snap.get(m.User, owner.id), recipient=snap.get(m.User, recipient.id) if recipient else None, as_of=today)
    snapshot_sha = hashlib.sha256(canonical(snapshot)).hexdigest()

    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"passport:{owner.id}"})
    audience = m.PassportVersion.recipient_user_id.is_(None) if recipient is None else m.PassportVersion.recipient_user_id == recipient.id
    version = (session.scalar(select(func.max(m.PassportVersion.version)).where(m.PassportVersion.owner_user_id == owner.id, audience)) or 0) + 1
    pdf = render(snapshot, version=version, snapshot_sha=snapshot_sha)
    pdf_sha = hashlib.sha256(pdf).hexdigest()
    key = f"passports/{owner.tenant_id}/{owner.id}/{recipient.id if recipient else 'owner'}/v{version}-{pdf_sha[:12]}.pdf"
    store.put(key=key, data=pdf, content_type="application/pdf")

    now = session.scalar(select(func.now()))
    session.execute(update(m.PassportVersion).where(m.PassportVersion.owner_user_id == owner.id, audience, m.PassportVersion.status == "current").values(status="stale", stale_at=now))
    pv = m.PassportVersion(tenant_id=owner.tenant_id, owner_user_id=owner.id, recipient_user_id=recipient.id if recipient else None, version=version,
                           snapshot=snapshot, snapshot_sha256=snapshot_sha, pdf_storage_key=key, pdf_sha256=pdf_sha, status="current")  # fmt: skip
    session.add(pv)
    if recipient is None:
        session.execute(update(m.ReportJob).where(m.ReportJob.owner_user_id == owner.id, m.ReportJob.status == "queued").values(status="done"))
    session.add(m.AuditEvent(tenant_id=owner.tenant_id, actor_user_id=owner.id, action="passport.generated", object_type="passport_version",
                             details={"version": version, "audience": snapshot["audience"], "snapshot_sha256": snapshot_sha}))  # fmt: skip
    session.flush()
    return _view(pv, {recipient.id: recipient.display_name} if recipient else {})


def recipient_released(session: Session, actor: Actor, owner_id: uuid.UUID) -> bool:
    """A recipient may download only while their post-release grant is active AND a handover
    they requested has been released (Prompt 11)."""
    grant = session.scalar(select(m.AccessGrant.id).where(m.AccessGrant.owner_user_id == owner_id, m.AccessGrant.grantee_user_id == actor.user_id,
                                                          m.AccessGrant.stage == "post_release", m.AccessGrant.status == "active"))  # fmt: skip
    released = session.scalar(select(m.HandoverRequest.id).where(m.HandoverRequest.owner_user_id == owner_id, m.HandoverRequest.requester_user_id == actor.user_id,
                                                                 m.HandoverRequest.state == "RELEASED"))  # fmt: skip
    return grant is not None and released is not None


def list_versions(session: Session, actor: Actor) -> list[dict[str, Any]]:
    stmt = select(m.PassportVersion).where(m.PassportVersion.tenant_id == actor.tenant_id)
    stmt = stmt.where(m.PassportVersion.owner_user_id == actor.user_id) if actor.role == "owner" else stmt.where(m.PassportVersion.recipient_user_id == actor.user_id)
    names = {u.id: u.display_name for u in session.scalars(select(m.User).where(m.User.tenant_id == actor.tenant_id))}
    return [_view(pv, names) for pv in session.scalars(stmt.order_by(m.PassportVersion.recipient_user_id.nulls_first(), m.PassportVersion.version.desc()))]


def download(session: Session, actor: Actor, passport_id: uuid.UUID, *, store: Any) -> tuple[bytes, m.PassportVersion]:
    pv = session.scalar(select(m.PassportVersion).where(m.PassportVersion.id == passport_id, m.PassportVersion.tenant_id == actor.tenant_id))
    if pv is None:
        raise NotFound
    if pv.owner_user_id == actor.user_id:
        allowed = True
    elif pv.recipient_user_id == actor.user_id:
        # Checked now, not when the file was made: only the current export, only after release.
        allowed = pv.status == "current" and recipient_released(session, actor, pv.owner_user_id)
    else:
        allowed = False
    if not allowed:
        raise NotFound
    data = store.get(key=pv.pdf_storage_key)
    if hashlib.sha256(data).hexdigest() != pv.pdf_sha256:
        raise NotFound  # stored file does not match its recorded hash
    session.add(m.AuditEvent(tenant_id=pv.tenant_id, actor_user_id=actor.user_id, action="passport.downloaded", object_type="passport_version",
                             object_id=pv.id, details={"version": pv.version}))  # fmt: skip
    session.flush()
    return data, pv

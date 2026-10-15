"""Proposals and owner confirmation.

A proposal is a draft. Only `confirm_proposal`, called from an authenticated owner API
request, turns it into an asset version, and it is deliberately absent from the chat
tool registry. Confirmation is idempotent per proposal and per Idempotency-Key, checks
the asset version the draft was based on, and writes the asset version, evidence,
confirmation, outbox event and audit event in one transaction.
"""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.auth import Actor
from reclaim_api.lib.log import correlation_id
from reclaim_api.records import NotFound  # one "not found" for out-of-scope and missing alike

__all__ = ["Conflict", "NotFound"]


class Conflict(RuntimeError):
    """409: state does not allow this action. `code` is stable for the UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


AMOUNT_FIELDS = ("balance_minor", "estimated_value_minor", "insurance_cover_minor", "sip_installment_minor", "liability_minor")
AMOUNT_RULES = {
    "insurance_cover_minor": {"life_insurance"},
    "sip_installment_minor": {"mutual_fund"},
    "balance_minor": {"savings", "fixed_deposit", "mutual_fund", "pension"},
    "estimated_value_minor": {"property"},
    "liability_minor": {"savings", "fixed_deposit", "mutual_fund", "life_insurance", "property", "pension"},
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "fixed_deposit": ("institution_name", "balance_minor", "term_months", "opened_on"),
    "savings": ("institution_name",),
    "mutual_fund": ("institution_name",),
    "life_insurance": ("institution_name",),
    "pension": ("institution_name",),
    "property": (),
}
# Asked one at a time, in this order.
QUESTION_ORDER = ("institution_name", "balance_minor", "currency", "term_months", "opened_on")

TYPE_LABELS = {
    "savings": "Savings account",
    "fixed_deposit": "Fixed deposit",
    "mutual_fund": "Mutual fund",
    "life_insurance": "Life insurance policy",
    "property": "Property",
    "pension": "Pension account",
}

EDITABLE = {
    "label", "institution_name", "jurisdiction", "currency", *AMOUNT_FIELDS, "opened_on", "maturity_on",
    "term_months", "nomination_status", "document_location", "source_date",
}  # fmt: skip


class ProposedAsset(BaseModel):
    """Backend validation of a complete asset record, independent of how it was drafted."""

    model_config = ConfigDict(extra="forbid")

    asset_type: str
    label: str | None = Field(default=None, max_length=120)
    institution_name: str | None = Field(default=None, max_length=120)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    masked_reference: str | None = Field(default=None, pattern=r"^••••[A-Za-z0-9/]{0,4}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    balance_minor: int | None = Field(default=None, ge=0)
    estimated_value_minor: int | None = Field(default=None, ge=0)
    insurance_cover_minor: int | None = Field(default=None, ge=0)
    sip_installment_minor: int | None = Field(default=None, ge=0)
    liability_minor: int | None = Field(default=None, ge=0)
    opened_on: date | None = None
    maturity_on: date | None = None
    term_months: int | None = Field(default=None, gt=0, le=1200)
    nomination_status: str = Field(default="unknown", pattern=r"^(unknown|recorded|not_recorded|not_applicable)$")
    document_location: str | None = Field(default=None, max_length=200)
    source_date: date | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ProposedAsset":
        if self.asset_type not in REQUIRED:
            raise ValueError("unknown asset type")
        for field in AMOUNT_FIELDS:
            if getattr(self, field) is not None:
                if self.asset_type not in AMOUNT_RULES[field]:
                    raise ValueError(f"{field} does not apply to {self.asset_type}")
                if self.currency is None:
                    raise ValueError("amounts need a currency")
        if self.opened_on and self.maturity_on and self.maturity_on <= self.opened_on:
            raise ValueError("maturity must be after opening")
        return self


def missing_fields(change_kind: str, fields: dict[str, Any]) -> list[str]:
    if change_kind != "create":
        return []
    asset_type = fields.get("asset_type")
    if asset_type not in REQUIRED:
        return ["asset_type"]
    missing = [f for f in REQUIRED[asset_type] if fields.get(f) is None]
    if "term_months" in missing and fields.get("maturity_on"):
        missing.remove("term_months")
    if any(fields.get(f) is not None for f in AMOUNT_FIELDS) and not fields.get("currency"):
        missing.append("currency")
    return sorted(missing, key=lambda f: QUESTION_ORDER.index(f) if f in QUESTION_ORDER else 99)


def unresolved(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document-vs-draft disagreements the owner has not settled. They block confirmation."""
    return [c for c in conflicts if c.get("kind") == "document_conflict" and not c.get("resolved")]


def status_for(change_kind: str, fields: dict[str, Any], conflicts: list[dict[str, Any]]) -> tuple[str, list[str]]:
    missing = missing_fields(change_kind, fields)
    return ("draft" if missing or unresolved(conflicts) else "awaiting_confirmation"), missing


def term_text(months: int) -> str:
    if months % 12 == 0:
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "")
    return f"{months} month" + ("s" if months != 1 else "")


def format_money(minor: int | None, currency: str | None) -> str | None:
    if minor is None:
        return None
    if currency == "INR":
        whole, paise = divmod(minor, 100)
        s = str(whole)
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        grouped = ",".join(([head] if head else []) + groups + [tail]) if len(s) > 3 else s
        return f"₹{grouped}" + (f".{paise:02d}" if paise else "")
    symbol = {"SGD": "S$"}.get(currency or "", f"{currency} " if currency else "")
    return f"{symbol}{minor / 100:,.2f}"


FIELD_LABELS = {
    "asset_type": "Type", "label": "Name", "institution_name": "Institution", "jurisdiction": "Country",
    "currency": "Currency", "balance_minor": "Balance / principal", "estimated_value_minor": "Estimated value",
    "insurance_cover_minor": "Insurance cover", "sip_installment_minor": "SIP installment (monthly)",
    "liability_minor": "Loan outstanding", "opened_on": "Opened on", "maturity_on": "Matures on",
    "term_months": "Term", "nomination_status": "Nomination", "document_location": "Documents kept at",
    "source_date": "As of",
}  # fmt: skip


def _display(field: str, fields: dict[str, Any]) -> str | None:
    value = fields.get(field)
    if value is None:
        return None
    if field in AMOUNT_FIELDS:
        return format_money(value, fields.get("currency"))
    if field == "term_months":
        return term_text(value)
    if field == "asset_type":
        return TYPE_LABELS.get(value, value)
    if field == "nomination_status":
        return {"unknown": "Unknown (not stated)", "recorded": "Recorded", "not_recorded": "Not recorded", "not_applicable": "Not applicable"}[value]
    return str(value)


def _display_conflict(c: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    if c.get("kind") != "document_conflict":
        return c
    label = FIELD_LABELS.get(c["field"], c["field"])
    return {**c, "label": label, "draft_display": _display(c["field"], {**fields, c["field"]: c["draft"]}),
            "document_display": _display(c["field"], {**fields, c["field"]: c["document"]})}  # fmt: skip


def proposal_view(p: m.Proposal, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """`target` describes the existing asset an update/close applies to (label, institution)."""
    fields = p.proposed_fields
    sources: dict[str, list[dict[str, Any]]] = {}
    for ref in p.evidence_refs:
        sources.setdefault(ref.get("field", ""), []).append(ref)
    rows = [
        {"field": f, "label": FIELD_LABELS[f], "value": _display(f, fields), "raw": fields.get(f), "sources": sources.get(f, [])}
        for f in FIELD_LABELS
        if fields.get(f) is not None or f in p.missing_fields
    ]
    return {
        "id": str(p.id),
        "status": p.status,
        "change_kind": p.change_kind,
        "asset_id": str(p.asset_id) if p.asset_id else None,
        "base_version": p.base_version,
        "target": target,
        "fields": rows,
        "missing_fields": p.missing_fields,
        "conflicts": [_display_conflict(c, fields) for c in p.conflicts],
        "model_version": p.model_version,
        "channel": p.channel,
        "unresolved_conflicts": [_display_conflict(c, fields) for c in unresolved(p.conflicts)],
        "documents": sorted({r["document_id"] for r in p.evidence_refs if r.get("document_id")}),
        "can_confirm": p.status == "awaiting_confirmation" and not unresolved(p.conflicts),
    }


def _load_owned(session: Session, actor: Actor, proposal_id: uuid.UUID, *, lock: bool = False) -> m.Proposal:
    if actor.role != "owner":
        raise NotFound
    stmt = select(m.Proposal).where(m.Proposal.id == proposal_id, m.Proposal.tenant_id == actor.tenant_id, m.Proposal.owner_user_id == actor.user_id)
    if lock:
        stmt = stmt.with_for_update()
    p = session.scalar(stmt)
    if p is None:
        raise NotFound
    return p


def target_of(session: Session, actor: Actor, p: m.Proposal) -> dict[str, Any] | None:
    if p.asset_id is None:
        return None
    from reclaim_api import records

    try:
        a = records.get_asset(session, actor, p.asset_id)
    except NotFound:
        return None
    return {"label": a["label"], "institution_name": a["institution_name"], "masked_reference": a["masked_reference"], "version": a["version"]}


def get_proposal(session: Session, actor: Actor, proposal_id: uuid.UUID) -> dict[str, Any]:
    p = _load_owned(session, actor, proposal_id)
    return proposal_view(p, target_of(session, actor, p))


def edit_proposal(session: Session, actor: Actor, proposal_id: uuid.UUID, changes: dict[str, Any]) -> dict[str, Any]:
    p = _load_owned(session, actor, proposal_id, lock=True)
    if p.status not in ("draft", "awaiting_confirmation"):
        raise Conflict("not_editable", f"proposal is {p.status}")
    unknown = set(changes) - EDITABLE
    if unknown:
        raise Conflict("invalid_fields", "fields not editable: " + ", ".join(sorted(unknown)))
    fields = {**p.proposed_fields, **changes}
    fields = {k: v for k, v in fields.items() if v is not None}
    if p.change_kind == "create":
        try:
            ProposedAsset.model_validate(fields)
        except ValidationError as err:
            raise Conflict("invalid_fields", f"{err.error_count()} invalid field(s)") from None
    p.proposed_fields = fields
    p.evidence_refs = [r for r in p.evidence_refs if r.get("field") not in changes or r.get("source") == "document"] + [
        {"field": f, "source": "owner_edit"} for f in changes if changes[f] is not None
    ]
    p.conflicts = [
        {**c, "resolved": "owner_edit"} if c.get("kind") == "document_conflict" and c.get("field") in changes and not c.get("resolved") else c
        for c in p.conflicts
    ]
    p.status, p.missing_fields = status_for(p.change_kind, fields, p.conflicts)
    session.flush()
    return proposal_view(p, target_of(session, actor, p))


def cancel_proposal(session: Session, actor: Actor, proposal_id: uuid.UUID) -> dict[str, Any]:
    p = _load_owned(session, actor, proposal_id, lock=True)
    if p.status == "cancelled":
        return proposal_view(p)
    if p.status not in ("draft", "awaiting_confirmation"):
        raise Conflict("not_cancellable", f"proposal is {p.status}")
    p.status = "cancelled"
    session.flush()
    return proposal_view(p, target_of(session, actor, p))


def _result(c: m.Confirmation, version: m.AssetVersion, replayed: bool) -> dict[str, Any]:
    return {
        "confirmation_id": str(c.id),
        "proposal_id": str(c.proposal_id),
        "asset_id": str(version.asset_id),
        "asset_version": version.version,
        "verification_state": version.verification_state,
        "replayed": replayed,
    }


def confirm_proposal(session: Session, actor: Actor, proposal_id: uuid.UUID, idempotency_key: str) -> dict[str, Any]:
    if not 8 <= len(idempotency_key) <= 128:
        raise Conflict("bad_idempotency_key", "Idempotency-Key must be 8–128 characters")
    p = _load_owned(session, actor, proposal_id, lock=True)

    existing = session.scalar(select(m.Confirmation).where(m.Confirmation.proposal_id == p.id))
    if existing is not None:
        return _result(existing, session.get(m.AssetVersion, existing.asset_version_id), replayed=True)
    reused = session.scalar(select(m.Confirmation).where(m.Confirmation.tenant_id == actor.tenant_id, m.Confirmation.idempotency_key == idempotency_key))
    if reused is not None:
        raise Conflict("idempotency_key_reused", "this Idempotency-Key was used for a different proposal")
    if unresolved(p.conflicts):
        raise Conflict("unresolved_conflicts", "a document disagrees with this draft: " + ", ".join(c["field"] for c in unresolved(p.conflicts)))
    if p.status != "awaiting_confirmation":
        raise Conflict("not_confirmable", f"proposal is {p.status}" + (": " + ", ".join(p.missing_fields) + " missing" if p.missing_fields else ""))

    if p.change_kind == "create":
        asset = m.Asset(
            id=uuid.uuid4(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
            asset_type=p.proposed_fields["asset_type"], current_version=1,
        )  # fmt: skip
        session.add(asset)
        base: dict[str, Any] = {}
        new_version = 1
    else:
        asset = session.scalar(
            select(m.Asset).where(m.Asset.id == p.asset_id, m.Asset.tenant_id == actor.tenant_id, m.Asset.owner_user_id == actor.user_id).with_for_update()
        )
        if asset is None:
            raise NotFound
        if asset.current_version != p.base_version or asset.status != "active":
            p.status = "superseded"
            session.flush()
            raise Conflict("stale_proposal", "the record changed after this draft was made; review it again")
        current = session.scalar(select(m.AssetVersion).where(m.AssetVersion.asset_id == asset.id, m.AssetVersion.version == asset.current_version))
        base = {f: getattr(current, f) for f in ProposedAsset.model_fields if f != "asset_type"}
        new_version = asset.current_version + 1

    record = {k: v for k, v in {**base, **p.proposed_fields, "asset_type": asset.asset_type}.items() if v is not None}
    record.setdefault("label", TYPE_LABELS[asset.asset_type])
    try:
        valid = ProposedAsset.model_validate(record)
    except ValidationError as err:
        raise Conflict("invalid_fields", f"{err.error_count()} invalid field(s)") from None

    if p.change_kind == "close":
        asset.status = "closed"
    asset.current_version = new_version
    asset.updated_at = _now(session)

    version = m.AssetVersion(
        id=uuid.uuid4(), tenant_id=actor.tenant_id, asset_id=asset.id, version=new_version, change_kind=p.change_kind,
        created_by_user_id=actor.user_id, verification_state=_verification_state(valid, p.evidence_refs), **valid.model_dump(),
    )  # fmt: skip
    session.add(version)
    session.flush()

    for ref in p.evidence_refs:
        name = ref.get("field")
        if name not in ProposedAsset.model_fields:
            continue
        if ref.get("source") in ("user_message", "owner_edit"):
            session.add(
                m.FieldEvidence(
                    tenant_id=actor.tenant_id, asset_version_id=version.id, field_name=name,
                    observed_value=ref.get("quote"), source_kind="user_message", source_date=version.source_date,
                    extractor_version=p.model_version if ref["source"] == "user_message" else None,
                )
            )  # fmt: skip
        elif ref.get("source") == "document" and ref.get("document_id"):
            final = getattr(valid, name)
            session.add(
                m.FieldEvidence(
                    tenant_id=actor.tenant_id, asset_version_id=version.id, field_name=name,
                    observed_value=str(ref.get("value")), source_kind="document", document_id=uuid.UUID(ref["document_id"]),
                    page=ref.get("page"), span_start=(ref.get("span") or [None])[0], span_end=(ref.get("span") or [None, None])[1],
                    bounding_box={"vertices": ref["bounding_box"]} if ref.get("bounding_box") else None,
                    confidence=ref.get("confidence"), extractor_version=ref.get("processor_version"),
                    source_date=date.fromisoformat(ref["source_date"]) if ref.get("source_date") else None,
                    conflicts_with_current=str(ref.get("value")) != str(final.isoformat() if isinstance(final, date) else final),
                )
            )  # fmt: skip

    confirmation = m.Confirmation(
        id=uuid.uuid4(), tenant_id=actor.tenant_id, proposal_id=p.id, confirmed_by_user_id=actor.user_id,
        idempotency_key=idempotency_key, asset_version_id=version.id,
    )  # fmt: skip
    session.add(confirmation)
    session.flush()
    version.confirmation_id = confirmation.id
    p.status = "confirmed"

    cid = correlation_id.get()
    session.add(
        m.OutboxEvent(
            tenant_id=actor.tenant_id, aggregate_type="asset", aggregate_id=asset.id, aggregate_version=new_version,
            event_type="asset.version_confirmed", correlation_id=cid,
            payload={"asset_version_id": str(version.id), "change_kind": p.change_kind},
        )
    )  # fmt: skip
    session.add(
        m.AuditEvent(
            tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="proposal.confirmed", object_type="asset",
            object_id=asset.id, correlation_id=cid,
            details={"proposal_id": str(p.id), "version": new_version, "change_kind": p.change_kind},
        )
    )  # fmt: skip
    session.flush()
    return _result(confirmation, version, replayed=False)


_PRIMARY_AMOUNT = {
    "savings": "balance_minor", "fixed_deposit": "balance_minor", "mutual_fund": "balance_minor", "pension": "balance_minor",
    "life_insurance": "insurance_cover_minor", "property": "estimated_value_minor",
}  # fmt: skip


def _verification_state(valid: ProposedAsset, refs: list[dict[str, Any]]) -> str:
    """document_extracted only when a document states the same primary amount that is being
    recorded. Owner confirmation never makes anything institution_verified."""
    column = _PRIMARY_AMOUNT[valid.asset_type]
    final = getattr(valid, column)
    backed = any(r.get("source") == "document" and r.get("field") == column and r.get("value") == final for r in refs)
    return "document_extracted" if final is not None and backed else "user_reported"


ATTACHABLE = {"processed", "needs_review"}
_DOC_FIELDS = (
    "institution_name", "jurisdiction", "currency", *AMOUNT_FIELDS, "opened_on", "maturity_on", "term_months",
    "nomination_status", "masked_reference", "source_date",
)  # fmt: skip


def attach_document(session: Session, actor: Actor, proposal_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    """Enrich a draft with a processed document. Missing fields are filled; disagreements
    become conflicts the owner must resolve; nothing is overwritten silently."""
    p = _load_owned(session, actor, proposal_id, lock=True)
    if p.status not in ("draft", "awaiting_confirmation"):
        raise Conflict("not_editable", f"proposal is {p.status}")
    doc = session.scalar(
        select(m.Document).where(m.Document.id == document_id, m.Document.tenant_id == actor.tenant_id, m.Document.owner_user_id == actor.user_id)
    )
    if doc is None:
        raise NotFound
    if doc.status not in ATTACHABLE:
        raise Conflict("document_not_ready", f"document is {doc.status}")
    if doc.kind in ("birth_certificate", "death_certificate", "other"):
        raise Conflict("not_asset_document", "this document is for human reviewers, not asset records")
    ext = doc.extraction or {}
    draft_type = session.get(m.Asset, p.asset_id).asset_type if p.asset_id else p.proposed_fields.get("asset_type")
    if ext.get("asset_type") and draft_type and ext["asset_type"] != draft_type:
        raise Conflict("document_type_mismatch", f"document describes a {ext['asset_type']}, the draft is a {draft_type}")
    if str(doc.id) in {r.get("document_id") for r in p.evidence_refs}:
        return proposal_view(p, target_of(session, actor, p))

    fields = dict(p.proposed_fields)
    conflicts = list(p.conflicts)
    refs = list(p.evidence_refs)
    source_date = doc.source_date.isoformat() if doc.source_date else None
    by_field = {e["field"]: e for e in ext.get("evidence", [])}
    # Handwritten or low-quality transcriptions support the record but never fill fields.
    applied = {} if doc.kind == "handwritten_inventory" else ext.get("fields", {})
    for name in _DOC_FIELDS:
        value = applied.get(name)
        if value is None:
            continue
        if name == "nomination_status" and value == "unknown":
            continue  # a document silent on nomination adds nothing
        ref = {**by_field.get(name, {}), "field": name, "source": "document", "document_id": str(doc.id), "value": value, "source_date": source_date}
        if name not in by_field:
            ref["note"] = "derived from another field in this document"
        current = fields.get(name)
        if current is None:
            fields[name] = value
            refs.append(ref)
        elif current == value:
            refs.append({**ref, "note": "document agrees with the draft"})
        else:
            refs.append({**ref, "note": "document disagrees with the draft"})
            conflicts.append({"kind": "document_conflict", "field": name, "draft": current, "document": value, "document_id": str(doc.id)})
    for dup in ext.get("possible_duplicates", []):
        conflicts.append({"kind": "possible_duplicate", "asset_id": dup["asset_id"], "label": dup["label"],
                          "note": "Same institution and reference ending. Not merged; check before confirming."})  # fmt: skip
    for reason in doc.review_reasons:
        conflicts.append({"kind": "document_needs_review", "document_id": str(doc.id), "note": reason})

    p.proposed_fields, p.conflicts, p.evidence_refs = fields, conflicts, refs
    p.status, p.missing_fields = status_for(p.change_kind, fields, conflicts)
    session.add(m.AuditEvent(tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="proposal.document_attached", object_type="proposal",
                             object_id=p.id, details={"document_id": str(doc.id), "conflicts": len(unresolved(conflicts))}))  # fmt: skip
    session.flush()
    return proposal_view(p, target_of(session, actor, p))


def resolve_conflict(session: Session, actor: Actor, proposal_id: uuid.UUID, field: str, use: str) -> dict[str, Any]:
    """The owner chooses the draft's value or the document's value for one field."""
    if use not in ("draft", "document"):
        raise Conflict("invalid_choice", "use must be 'draft' or 'document'")
    p = _load_owned(session, actor, proposal_id, lock=True)
    open_ = [c for c in unresolved(p.conflicts) if c["field"] == field]
    if not open_:
        raise Conflict("no_conflict", f"no unresolved conflict on {field}")
    fields = dict(p.proposed_fields)
    if use == "document":
        fields[field] = open_[-1]["document"]
    p.conflicts = [{**c, "resolved": use} if c in open_ else c for c in p.conflicts]
    p.proposed_fields = fields
    p.status, p.missing_fields = status_for(p.change_kind, fields, p.conflicts)
    session.flush()
    return proposal_view(p, target_of(session, actor, p))


def _now(session: Session):
    from sqlalchemy import func

    return session.scalar(select(func.now()))

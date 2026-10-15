"""Document intake and asynchronous processing.

Intake (request path): owner only; type sniffed from bytes (the declared type is not
trusted); size-limited; stored under a quarantine prefix as `pending`. Re-uploads are
detected by hash within the tenant and point at the first copy.

Processing (background): extract text → normalize with the model → provenance and
review reasons → `processed` or `needs_review`. Processing never touches proposals,
assets, grants or handover state; attaching evidence is a separate owner action.
"""

import hashlib
import logging
import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import records
from reclaim_api.auth import Actor
from reclaim_api.documents import extract
from reclaim_api.lib.log import error_tag
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.documents import UnsupportedDocument

log = logging.getLogger("reclaim_api.documents")

MAX_BYTES = 10 * 1024 * 1024
_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


class Rejected(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def sniff(content: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if content.startswith(magic):
            return mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    return "text/plain" if "\x00" not in text and printable / max(len(text), 1) > 0.95 else None


def _title(filename: str | None) -> str:
    name = re.split(r"[\\/]", filename or "")[-1]
    name = re.sub(r"[^\w .()-]", "", name).strip()[:120]
    return name or "Uploaded document"


def view(doc: m.Document) -> dict[str, Any]:
    ext = doc.extraction or {}
    return {
        "id": str(doc.id),
        "title": doc.title,
        "kind": doc.kind,
        "status": doc.status,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "sha256": doc.sha256,
        "duplicate_of": str(doc.duplicate_of_document_id) if doc.duplicate_of_document_id else None,
        "source_date": doc.source_date.isoformat() if doc.source_date else None,
        "ingested_at": doc.ingested_at.isoformat() if doc.ingested_at else None,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
        "processor_version": doc.processor_version,
        "review_reasons": doc.review_reasons,
        "asset_type": ext.get("asset_type"),
        "fields": ext.get("fields", {}),
        "evidence": ext.get("evidence", []),
        "possible_duplicates": ext.get("possible_duplicates", []),
        "notice": "Extraction confidence describes reading the document, not its authenticity or ownership.",
    }


def _owned(session: Session, actor: Actor, document_id: uuid.UUID, *, lock: bool = False) -> m.Document:
    if actor.role != "owner":
        raise records.NotFound
    stmt = select(m.Document).where(m.Document.id == document_id, m.Document.tenant_id == actor.tenant_id, m.Document.owner_user_id == actor.user_id)
    doc = session.scalar(stmt.with_for_update() if lock else stmt)
    if doc is None:
        raise records.NotFound
    return doc


def get_document(session: Session, actor: Actor, document_id: uuid.UUID) -> dict[str, Any]:
    return view(_owned(session, actor, document_id))


def list_documents(session: Session, actor: Actor) -> list[dict[str, Any]]:
    if actor.role != "owner":
        return []
    docs = session.scalars(
        select(m.Document).where(m.Document.tenant_id == actor.tenant_id, m.Document.owner_user_id == actor.user_id).order_by(m.Document.ingested_at.desc())
    )
    return [view(d) for d in docs]


def intake(session: Session, actor: Actor, content: bytes, filename: str | None, *, store: Any, roles: tuple[str, ...] = ("owner",)) -> m.Document:
    """`roles`: owners upload their own records; family members upload handover evidence only."""
    if actor.role not in roles:
        raise Rejected(403, "uploads are available to asset owners")
    if not content:
        raise Rejected(400, "empty file")
    if len(content) > MAX_BYTES:
        raise Rejected(413, f"file larger than {MAX_BYTES // (1024 * 1024)} MB")
    mime = sniff(content)
    if mime is None:
        raise Rejected(415, "unsupported file type (PDF, PNG, JPEG, TIFF, WEBP or plain text)")

    sha = hashlib.sha256(content).hexdigest()
    original = session.scalar(
        select(m.Document)
        .where(m.Document.tenant_id == actor.tenant_id, m.Document.sha256 == sha, m.Document.duplicate_of_document_id.is_(None))
        .order_by(m.Document.ingested_at)
        .limit(1)
    )
    doc_id = uuid.uuid4()
    if original is not None:
        doc = m.Document(
            id=doc_id, tenant_id=actor.tenant_id, owner_user_id=actor.user_id, kind=original.kind, title=_title(filename),
            sha256=sha, mime_type=mime, size_bytes=len(content), storage_key=original.storage_key,
            status="pending", duplicate_of_document_id=original.id,
        )  # fmt: skip
    else:
        key = f"quarantine/{actor.tenant_id}/{doc_id}"
        store.put(key=key, data=content, content_type=mime)
        doc = m.Document(
            id=doc_id, tenant_id=actor.tenant_id, owner_user_id=actor.user_id, kind="other", title=_title(filename),
            sha256=sha, mime_type=mime, size_bytes=len(content), storage_key=key, status="pending",
        )  # fmt: skip
    session.add(doc)
    session.add(
        m.OutboxEvent(
            tenant_id=actor.tenant_id, aggregate_type="document", aggregate_id=doc_id, aggregate_version=1,
            event_type="document.uploaded", payload={"duplicate_of": str(original.id) if original else None},
        )
    )  # fmt: skip
    session.add(m.AuditEvent(tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="document.uploaded", object_type="document", object_id=doc_id,
                             details={"mime_type": mime, "size_bytes": len(content), "duplicate": original is not None}))  # fmt: skip
    session.flush()
    return doc


def _possible_duplicates(session: Session, doc: m.Document, fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Recorded assets matching institution + masked reference. Suggestions only; never merged."""
    ref, inst = fields.get("masked_reference"), fields.get("institution_name")
    if not ref or not inst:
        return []
    rows = session.execute(
        select(m.Asset.id, m.AssetVersion.label, m.Asset.asset_type)
        .join(m.AssetVersion, and_(m.AssetVersion.asset_id == m.Asset.id, m.AssetVersion.version == m.Asset.current_version))
        .where(m.Asset.tenant_id == doc.tenant_id, m.Asset.owner_user_id == doc.owner_user_id, m.AssetVersion.masked_reference == ref,
               m.AssetVersion.institution_name == inst)
    ).all()  # fmt: skip
    return [{"asset_id": str(r.id), "label": r.label, "asset_type": r.asset_type, "matched_on": ["institution_name", "masked_reference"]} for r in rows]


def process_document(session: Session, document_id: uuid.UUID, *, providers: Any, today: date) -> str:
    """Idempotent: a document that is no longer pending is left alone."""
    doc = session.scalar(select(m.Document).where(m.Document.id == document_id).with_for_update())
    if doc is None or doc.status != "pending":
        return doc.status if doc else "missing"

    if doc.duplicate_of_document_id:
        original = session.get(m.Document, doc.duplicate_of_document_id)
        if original.status == "pending":
            process_document(session, original.id, providers=providers, today=today)
        doc.kind, doc.extraction, doc.review_reasons = original.kind, original.extraction, original.review_reasons
        doc.processor_version, doc.source_date, doc.text_storage_key = original.processor_version, original.source_date, original.text_storage_key
        doc.status, doc.processed_at = original.status, func.now()
        session.flush()
        return doc.status

    store = providers.object_storage
    try:
        content = store.get(key=doc.storage_key)
        ext = providers.documents.extract(content=content, mime_type=doc.mime_type)
    except UnsupportedDocument as err:
        doc.status, doc.review_reasons, doc.processed_at = "needs_review", [f"unsupported: {err.args[0]}"], func.now()
        session.flush()
        return doc.status
    except ProviderUnavailable as err:
        # Transient: leave it pending and let the event be redelivered (bounded, then dead-lettered).
        log.warning("document processing unavailable %s", error_tag(err))
        raise

    text_key = f"extracted/{doc.tenant_id}/{doc.id}.txt"
    store.put(key=text_key, data=ext.text.encode("utf-8"), content_type="text/plain")
    result = extract.run(providers.llm, ext, document_id=str(doc.id), today=today)

    doc.kind = result.kind
    doc.source_date = result.source_date
    doc.processor_version = ext.processor_version
    doc.text_storage_key = text_key
    doc.extraction = {
        "asset_type": result.asset_type,
        "fields": result.fields,
        "evidence": result.evidence,
        "possible_duplicates": _possible_duplicates(session, doc, result.fields),
        "normalizer": getattr(providers.llm, "model_version", None),
    }
    doc.review_reasons = result.review
    doc.status = "needs_review" if result.review else "processed"
    doc.processed_at = func.now()
    session.add(m.AuditEvent(tenant_id=doc.tenant_id, action="document.processed", object_type="document", object_id=doc.id,
                             details={"status": doc.status, "kind": doc.kind, "fields": sorted(result.fields)}))  # fmt: skip
    session.flush()
    return doc.status


def content_for_owner(session: Session, actor: Actor, document_id: uuid.UUID, store: Any) -> tuple[bytes, str]:
    """Original bytes, only once processing has run (unprocessed uploads stay isolated)."""
    doc = _owned(session, actor, document_id)
    if doc.status not in ("processed", "needs_review"):
        raise records.NotFound
    return store.get(key=doc.storage_key), doc.mime_type

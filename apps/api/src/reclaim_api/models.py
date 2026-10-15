"""Operational data model. PostgreSQL is the source of truth.

Conventions (PROJECT_BRIEF.md "Data contract"):
- UUID primary keys; every row carries tenant_id. Child rows reference parents by
  (tenant_id, id), so the database itself refuses a cross-tenant link.
- UTC timestamptz everywhere. Source dates (what a document or person says) are
  stored separately from ingestion/creation time.
- Amounts are integer minor units with an ISO 4217 currency on the same row.
  Balance, estimated value, insurance cover, SIP installment and liability are
  separate columns and are never summed together.
- Corrections create a new asset_version; versions are never updated in place.
- Enumerations are text + CHECK constraints so migrations stay simple.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = metadata


# --- Vocabularies -----------------------------------------------------------

USER_ROLES = ("owner", "family", "reviewer")
ASSET_TYPES = ("savings", "fixed_deposit", "mutual_fund", "life_insurance", "property", "pension")
ASSET_STATUSES = ("active", "closed")
CHANGE_KINDS = ("create", "update", "correct", "close")
VERIFICATION_STATES = ("user_reported", "document_extracted", "institution_verified")
# Unknown is the default: absent nomination data never means "no nominee".
NOMINATION_STATUSES = ("unknown", "recorded", "not_recorded", "not_applicable")
EVIDENCE_SOURCES = ("user_message", "document", "institution")
DOCUMENT_KINDS = ("statement", "fd_receipt", "policy_schedule", "property_deed", "handwritten_inventory", "birth_certificate", "death_certificate", "other")
# pending: stored in quarantine, not yet processed. needs_review: extracted, but a human must
# check unsupported/unclear fields before they are relied on.
DOCUMENT_STATUSES = ("pending", "processed", "needs_review", "failed", "quarantined", "fixture")
# Roles are distinct; a family relation never implies any of the others.
RELATIONSHIP_KINDS = ("family_relation", "trusted_contact", "registered_nominee", "executor", "legal_heir")
NODE_KINDS = ("user", "person", "asset")
# draft: essential fields still missing; awaiting_confirmation: complete, owner has not confirmed.
PROPOSAL_STATUSES = ("draft", "awaiting_confirmation", "confirmed", "cancelled", "superseded")
CHANNELS = ("web", "telegram", "fixture")
GRANT_STAGES = ("pre_handover", "post_release")
HANDOVER_STATES = ("PRIVATE", "REQUESTED", "VERIFICATION_PENDING", "APPROVED", "RELEASED", "NEEDS_INFORMATION", "HOLD", "REJECTED")
REVIEW_DECISIONS = ("approve", "reject", "hold", "needs_information", "resume")
CLAIM_STATUSES = ("preparing", "documents_pending", "submitted_by_family", "closed")
# Optional recovery (Prompt 13). blocked: a conflict rules the record out; not_mine: the owner or
# family said so; claim_opened: a separate recovery claim case exists. None of these is ownership.
RECOVERY_MATCH_STATUSES = ("candidate", "blocked", "not_mine", "claim_opened")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), nullable=False, index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _tenant_fk(cols: list[str], table: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    """Composite FK (tenant_id, <col>) -> <table>(tenant_id, id): same-tenant references only."""
    return ForeignKeyConstraint(["tenant_id", *cols], [f"{table}.tenant_id", f"{table}.id"], ondelete=ondelete)


# --- Identity ---------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint(_in("role", USER_ROLES), name="role"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # Identity Platform subject ("sub"), linked on first sign-in with a verified email.
    identity_subject: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created()


class ChannelLink(Base):
    """A private chat linked to an authenticated user. The chat ID is stored hashed only."""

    __tablename__ = "channel_links"
    __table_args__ = (
        _tenant_fk(["user_id"], "users", ondelete="CASCADE"),
        CheckConstraint("channel = 'telegram'", name="channel"),
        CheckConstraint("status IN ('active', 'unlinked')", name="status"),
        Index("uq_channel_links_active_chat", "channel", "chat_id_hash", unique=True, postgresql_where=text("status = 'active'")),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    chat_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    linked_at: Mapped[datetime] = _created()
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LinkCode(Base):
    """Single-use code shown once in the authenticated web app; only its hash is stored."""

    __tablename__ = "link_codes"
    __table_args__ = (
        _tenant_fk(["user_id"], "users", ondelete="CASCADE"),
        UniqueConstraint("code_hash"),
        CheckConstraint("channel = 'telegram'", name="channel"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class TelegramUpdate(Base):
    """Seen update IDs (dedup) and per-chat arrival times (rate limit). No message content."""

    __tablename__ = "telegram_updates"
    __table_args__ = (Index("ix_telegram_updates_chat_time", "chat_id_hash", "received_at"),)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    chat_id_hash: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(24))
    received_at: Mapped[datetime] = _created()


# --- Records ----------------------------------------------------------------


class Asset(Base):
    """Stable identity of an asset. Its facts live in asset_versions."""

    __tablename__ = "assets"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("id", "asset_type"),  # target for asset_versions' type-consistency FK
        CheckConstraint(_in("asset_type", ASSET_TYPES), name="asset_type"),
        CheckConstraint(_in("status", ASSET_STATUSES), name="status"),
        CheckConstraint("current_version >= 1", name="current_version"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    # Optimistic concurrency: writers must name the version they read.
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class AssetVersion(Base):
    """Immutable snapshot of an asset's recorded facts."""

    __tablename__ = "asset_versions"
    __table_args__ = (
        _tenant_fk(["asset_id"], "assets", ondelete="CASCADE"),
        ForeignKeyConstraint(["asset_id", "asset_type"], ["assets.id", "assets.asset_type"]),
        _tenant_fk(["created_by_user_id"], "users"),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("asset_id", "version"),
        CheckConstraint(_in("change_kind", CHANGE_KINDS), name="change_kind"),
        CheckConstraint(_in("verification_state", VERIFICATION_STATES), name="verification_state"),
        CheckConstraint(_in("nomination_status", NOMINATION_STATUSES), name="nomination_status"),
        CheckConstraint("jurisdiction ~ '^[A-Z]{2}$'", name="jurisdiction"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint(
            "currency IS NOT NULL OR (balance_minor IS NULL AND estimated_value_minor IS NULL AND insurance_cover_minor IS NULL"
            " AND sip_installment_minor IS NULL AND liability_minor IS NULL)",
            name="amount_needs_currency",
        ),
        CheckConstraint(
            "coalesce(balance_minor, 0) >= 0 AND coalesce(estimated_value_minor, 0) >= 0 AND coalesce(insurance_cover_minor, 0) >= 0"
            " AND coalesce(sip_installment_minor, 0) >= 0 AND coalesce(liability_minor, 0) >= 0",
            name="amounts_non_negative",
        ),
        # Amount kinds are tied to asset types so they cannot be mixed into a balance.
        CheckConstraint("insurance_cover_minor IS NULL OR asset_type = 'life_insurance'", name="cover_only_on_insurance"),
        CheckConstraint("sip_installment_minor IS NULL OR asset_type = 'mutual_fund'", name="sip_only_on_mutual_fund"),
        CheckConstraint("balance_minor IS NULL OR asset_type IN ('savings', 'fixed_deposit', 'mutual_fund', 'pension')", name="balance_kind"),
        CheckConstraint("estimated_value_minor IS NULL OR asset_type = 'property'", name="estimate_only_on_property"),
        CheckConstraint("masked_reference IS NULL OR masked_reference ~ '^••••[A-Za-z0-9/]{0,4}$'", name="reference_masked"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    institution_name: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str] = mapped_column(String(2), nullable=False)
    # Only ever the masked form; raw account/policy numbers are never stored.
    masked_reference: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(3))
    balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    estimated_value_minor: Mapped[int | None] = mapped_column(BigInteger)
    insurance_cover_minor: Mapped[int | None] = mapped_column(BigInteger)
    sip_installment_minor: Mapped[int | None] = mapped_column(BigInteger)
    liability_minor: Mapped[int | None] = mapped_column(BigInteger)
    opened_on: Mapped[date | None] = mapped_column(Date)
    maturity_on: Mapped[date | None] = mapped_column(Date)
    term_months: Mapped[int | None] = mapped_column(Integer)
    nomination_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    document_location: Mapped[str | None] = mapped_column(Text)
    # Date the underlying source describes (statement date), distinct from created_at.
    source_date: Mapped[date | None] = mapped_column(Date)
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confirmation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        _tenant_fk(["duplicate_of_document_id"], "documents"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(_in("kind", DOCUMENT_KINDS), name="kind"),
        CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        Index("ix_documents_tenant_sha256", "tenant_id", "sha256"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # A re-upload keeps its own row (who/when) but points at the first copy.
    duplicate_of_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_date: Mapped[date | None] = mapped_column(Date)
    ingested_at: Mapped[datetime] = _created()
    # Normalized fields with page/span/confidence per field. Never includes full identifiers.
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    review_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    processor_version: Mapped[str | None] = mapped_column(Text)
    text_storage_key: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FieldEvidence(Base):
    """Where a field value came from. Several sources may disagree; conflicts stay visible."""

    __tablename__ = "field_evidence"
    __table_args__ = (
        _tenant_fk(["asset_version_id"], "asset_versions", ondelete="CASCADE"),
        _tenant_fk(["document_id"], "documents"),
        CheckConstraint(_in("source_kind", EVIDENCE_SOURCES), name="source_kind"),
        CheckConstraint("source_kind <> 'document' OR document_id IS NOT NULL", name="document_source_has_document"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    asset_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Display-safe observed value (amounts as minor-unit strings, references masked).
    observed_value: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    page: Mapped[int | None] = mapped_column(Integer)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    bounding_box: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Extraction confidence only: says nothing about authenticity or ownership.
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    extractor_version: Mapped[str | None] = mapped_column(Text)
    source_date: Mapped[date | None] = mapped_column(Date)
    conflicts_with_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _created()


class Relationship(Base):
    """Edges for the evidence/people graph. Each role kind is recorded explicitly;
    none is derived from another (a daughter is not thereby a nominee or a grantee)."""

    __tablename__ = "relationships"
    __table_args__ = (
        _tenant_fk(["created_by_user_id"], "users"),
        CheckConstraint(_in("kind", RELATIONSHIP_KINDS), name="kind"),
        CheckConstraint(_in("from_kind", NODE_KINDS), name="from_kind"),
        CheckConstraint(_in("to_kind", NODE_KINDS), name="to_kind"),
        CheckConstraint("kind <> 'family_relation' OR detail IS NOT NULL", name="family_relation_detail"),
        CheckConstraint("kind <> 'registered_nominee' OR to_kind = 'asset'", name="nominee_is_per_asset"),
        UniqueConstraint("tenant_id", "kind", "from_kind", "from_id", "to_kind", "to_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    from_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, server_default="user_reported")
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _created()


# --- Proposals and confirmations ------------------------------------------------


class Proposal(Base):
    """An AI- or user-drafted change. Nothing here is a committed fact."""

    __tablename__ = "proposals"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        _tenant_fk(["asset_id"], "assets"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="status"),
        CheckConstraint(_in("change_kind", CHANGE_KINDS), name="change_kind"),
        CheckConstraint(_in("channel", CHANNELS), name="channel"),
        CheckConstraint("(asset_id IS NULL) = (base_version IS NULL)", name="base_version_with_asset"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The asset version the draft was based on; confirming against a newer one fails.
    base_version: Mapped[int | None] = mapped_column(Integer)
    change_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    conflicts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    missing_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ChatTurn(Base):
    """Conversation history for bounded model context. Long digit runs are redacted before
    storage; this table is never logged or exported to analytics."""

    __tablename__ = "chat_turns"
    __table_args__ = (
        _tenant_fk(["user_id"], "users", ondelete="CASCADE"),
        _tenant_fk(["proposal_id"], "proposals"),
        CheckConstraint("speaker IN ('owner', 'assistant')", name="speaker"),
        CheckConstraint(_in("channel", CHANNELS), name="channel"),
        Index("ix_chat_turns_user_time", "tenant_id", "user_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(String(24))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class Confirmation(Base):
    """The owner's explicit confirmation. One per proposal; a retry finds the existing row."""

    __tablename__ = "confirmations"
    __table_args__ = (
        _tenant_fk(["proposal_id"], "proposals"),
        _tenant_fk(["confirmed_by_user_id"], "users"),
        _tenant_fk(["asset_version_id"], "asset_versions"),
        UniqueConstraint("proposal_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    asset_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confirmed_at: Mapped[datetime] = _created()


# --- Access, passport, handover -------------------------------------------------


class AccessGrant(Base):
    """What a designated recipient may see, and when. Only the owner creates grants."""

    __tablename__ = "access_grants"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        _tenant_fk(["grantee_user_id"], "users"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(_in("stage", GRANT_STAGES), name="stage"),
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
        CheckConstraint("owner_user_id <> grantee_user_id", name="not_self"),
        CheckConstraint("version >= 1", name="version"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grantee_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    # {"asset_ids": [...], "sections": [...]} — explicit allow-list; absent means hidden.
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PassportVersion(Base):
    __tablename__ = "passport_versions"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        _tenant_fk(["recipient_user_id"], "users"),
        UniqueConstraint("owner_user_id", "recipient_user_id", "version"),
        CheckConstraint("status IN ('current', 'stale')", name="status"),
        CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name="snapshot_sha256"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Null for the owner's full passport; set for a recipient-filtered export.
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pdf_storage_key: Mapped[str | None] = mapped_column(Text)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="current")
    generated_at: Mapped[datetime] = _created()
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HandoverRequest(Base):
    __tablename__ = "handover_requests"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users"),
        _tenant_fk(["requester_user_id"], "users"),
        _tenant_fk(["grant_id"], "access_grants"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(_in("state", HANDOVER_STATES), name="state"),
        CheckConstraint("requester_user_id <> owner_user_id", name="requester_not_owner"),
        # One open request per owner and requester; finished ones stay as history.
        Index("uq_handover_open", "owner_user_id", "requester_user_id", unique=True,
              postgresql_where=text("state NOT IN ('RELEASED', 'REJECTED')")),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grant_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    # Bumped on any material evidence change; approvals on an older value no longer count.
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Demo only: identity and death verification are simulated and labelled as such.
    identity_check: Mapped[str] = mapped_column(String(32), nullable=False, server_default="simulated")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HandoverEvidence(Base):
    """A document the requester supplied for review. Adding or removing one is a material change."""

    __tablename__ = "handover_evidence"
    __table_args__ = (
        _tenant_fk(["handover_request_id"], "handover_requests", ondelete="CASCADE"),
        _tenant_fk(["document_id"], "documents"),
        _tenant_fk(["added_by_user_id"], "users"),
        UniqueConstraint("handover_request_id", "document_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    handover_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = _created()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        _tenant_fk(["handover_request_id"], "handover_requests"),
        _tenant_fk(["reviewer_user_id"], "users"),
        CheckConstraint(_in("decision", REVIEW_DECISIONS), name="decision"),
        # One reviewer approves a given evidence/grant version at most once.
        Index(
            "uq_review_decisions_one_approval",
            "handover_request_id",
            "reviewer_user_id",
            "evidence_version",
            "grant_version",
            unique=True,
            postgresql_where=text("decision = 'approve'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    handover_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    grant_version: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = _created()


class ClaimCase(Base):
    """Claim preparation guidance. Institutions decide claims; this tracks evidence only."""

    __tablename__ = "claim_cases"
    __table_args__ = (
        _tenant_fk(["handover_request_id"], "handover_requests"),
        _tenant_fk(["asset_id"], "assets"),
        _tenant_fk(["claimant_user_id"], "users"),
        CheckConstraint(_in("status", CLAIM_STATUSES), name="status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    handover_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claimant_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    # Pinned checklist: paths, cited sections, evidence status, explanation (see claims/copilot.py).
    checklist: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


# --- Optional dormant-asset recovery (Prompt 13) -----------------------------------------------------


class RecoveryProfile(Base):
    """What the owner allowed a recovery search to compare: names, date of birth, past addresses.
    Searching needs an explicit opt-in (opted_in_at); withdrawing deletes the results."""

    __tablename__ = "recovery_profiles"
    __table_args__ = (_tenant_fk(["owner_user_id"], "users", ondelete="CASCADE"), UniqueConstraint("owner_user_id"))
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_variants: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    addresses: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    opted_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class RecoveryMatch(Base):
    """A registry record compared with an owner's profile. Kept outside the inventory: it is a
    possibility to check, never an asset, and creates no access, claim or payment by itself."""

    __tablename__ = "recovery_matches"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("owner_user_id", "registry_record_id"),
        CheckConstraint(_in("status", RECOVERY_MATCH_STATUSES), name="status"),
        CheckConstraint("(status = 'blocked') = (score IS NULL)", name="blocked_has_no_score"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    registry_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    registry_version: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)  # heuristic 0-100, not a probability
    features: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    blocked_by: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    explanation: Mapped[Any | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class RecoveryClaim(Base):
    """A separate claim-preparation case for a possible match. The institution decides."""

    __tablename__ = "recovery_claims"
    __table_args__ = (
        _tenant_fk(["match_id"], "recovery_matches", ondelete="CASCADE"),
        _tenant_fk(["claimant_user_id"], "users"),
        _tenant_fk(["handover_request_id"], "handover_requests"),
        UniqueConstraint("match_id", "claimant_user_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claimant_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    handover_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="preparing")
    checklist: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = _created()


# --- Audit and events ------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index("ix_audit_events_tenant_time", "tenant_id", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    correlation_id: Mapped[str | None] = mapped_column(Text)
    # IDs and shapes only; never message text, document text or identifiers.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurred_at: Mapped[datetime] = _created()


class OutboxEvent(Base):
    """Written in the same transaction as the change it describes (Prompt 6 publishes)."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_version", "event_type"),
        Index("ix_outbox_events_unpublished", "occurred_at", postgresql_where=text("published_at IS NULL")),
    )
    id: Mapped[uuid.UUID] = _pk()  # the event_id
    tenant_id: Mapped[uuid.UUID] = _tenant()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    correlation_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = _created()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    # "app-events" for workers; "analytics" for minimized analytics copies (Prompt 8 sink).
    topic: Mapped[str] = mapped_column(String(32), nullable=False, server_default="app-events")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set after the bounded publish retries are exhausted; replay clears it.
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeadLetter(Base):
    """A message a consumer could not process and retrying cannot fix (malformed envelope,
    unknown event type), or one that exhausted its retries. Reasons are log-safe."""

    __tablename__ = "dead_letters"
    id: Mapped[uuid.UUID] = _pk()
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class AssetProjection(Base):
    """Read model rebuilt from SQL on each confirmed change. Never written by the API."""

    __tablename__ = "asset_projections"
    __table_args__ = (_tenant_fk(["asset_id"], "assets", ondelete="CASCADE"),)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    view: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    projected_at: Mapped[datetime] = _created()


class FreshnessFinding(Base):
    __tablename__ = "freshness_findings"
    __table_args__ = (
        _tenant_fk(["asset_id"], "assets", ondelete="CASCADE"),
        CheckConstraint("kind IN ('stale_source', 'unknown_nomination', 'missing_document_location', 'no_source_date')", name="kind"),
        UniqueConstraint("asset_id", "kind"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = _created()


class ReportJob(Base):
    """Queued passport regeneration (Prompt 10 runs these). One per triggering event."""

    __tablename__ = "report_jobs"
    __table_args__ = (
        _tenant_fk(["owner_user_id"], "users", ondelete="CASCADE"),
        UniqueConstraint("reason_event_id"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="passport")
    reason_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    created_at: Mapped[datetime] = _created()


class CategoryConfirmation(Base):
    """An owner's confirmed category for a (synthetic) transaction. Separate from predictions,
    which live in the warehouse with their model version and are never treated as confirmed."""

    __tablename__ = "category_confirmations"
    __table_args__ = (
        _tenant_fk(["confirmed_by_user_id"], "users", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "transaction_id"),
        CheckConstraint("category IN ('income', 'investment_contribution', 'insurance_premium', 'loan_payment', 'transfer', 'other')", name="category"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_category: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str | None] = mapped_column(Text)
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confirmed_at: Mapped[datetime] = _created()


class ProcessedEvent(Base):
    """At-least-once delivery guard: a consumer handles each event_id once."""

    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = _created()

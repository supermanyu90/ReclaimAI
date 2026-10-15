"""Server tools the chat may invoke. Each takes the session's Actor; none accepts a tenant,
owner or user ID from the model. Confirmation is intentionally not a tool.
"""

import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals, records
from reclaim_api.auth import Actor
from reclaim_api.providers.registry import get_providers


def list_authorized_assets(session: Session, actor: Actor) -> list[dict[str, Any]]:
    return records.list_assets(session, actor)


class ProposeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_kind: Literal["create", "update", "close"]
    asset_id: uuid.UUID | None = None
    fields: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    channel: Literal["web", "telegram"] = "web"
    model_version: str | None = None
    proposal_id: uuid.UUID | None = None  # an open draft of this owner to merge into


def propose_asset_change(session: Session, actor: Actor, args: ProposeArgs) -> m.Proposal:
    """Create or update a draft. Never writes an asset or asset version."""
    if actor.role != "owner":
        raise proposals.NotFound
    base_version = None
    if args.change_kind != "create":
        if args.asset_id is None:
            raise proposals.NotFound
        base_version = records.get_asset(session, actor, args.asset_id)["version"]  # NotFound outside scope

    if args.proposal_id is not None:
        p = session.scalar(
            select(m.Proposal)
            .where(m.Proposal.id == args.proposal_id, m.Proposal.tenant_id == actor.tenant_id, m.Proposal.owner_user_id == actor.user_id)
            .with_for_update()
        )
        if p is None or p.status not in ("draft", "awaiting_confirmation"):
            raise proposals.NotFound
        # Document conflicts and review notes survive chat follow-ups until the owner resolves them.
        kept = [c for c in p.conflicts if c.get("kind", "").startswith("document_")]
        doc_refs = [r for r in p.evidence_refs if r.get("source") == "document" and r not in args.evidence_refs]
        p.proposed_fields, p.conflicts, p.model_version = args.fields, kept + args.conflicts, args.model_version
        p.evidence_refs = args.evidence_refs + doc_refs
        p.status, p.missing_fields = proposals.status_for(args.change_kind, p.proposed_fields, p.conflicts)
    else:
        # One open draft per owner: a new topic supersedes the previous draft.
        for old in session.scalars(
            select(m.Proposal).where(
                m.Proposal.tenant_id == actor.tenant_id, m.Proposal.owner_user_id == actor.user_id,
                m.Proposal.status.in_(("draft", "awaiting_confirmation")),
            )
        ):  # fmt: skip
            old.status = "superseded"
        p = m.Proposal(
            id=uuid.uuid4(), tenant_id=actor.tenant_id, owner_user_id=actor.user_id, asset_id=args.asset_id,
            base_version=base_version, change_kind=args.change_kind, proposed_fields=args.fields,
            evidence_refs=args.evidence_refs, conflicts=args.conflicts, channel=args.channel,
            model_version=args.model_version,
        )  # fmt: skip
        p.status, p.missing_fields = proposals.status_for(args.change_kind, args.fields, args.conflicts)
        session.add(p)
    session.flush()
    return p


def get_proposal(session: Session, actor: Actor, proposal_id: uuid.UUID) -> dict[str, Any]:
    return proposals.get_proposal(session, actor, proposal_id)


def retrieve_policy(session: Session, actor: Actor, query: str, *, llm: Any, today: Any) -> dict[str, Any]:
    """A grounded answer from approved, current policy for the actor's institutions."""
    from reclaim_api.claims import copilot

    return copilot.answer_question(session, actor, query, retriever=get_providers().retrieval, llm=llm, today=today)


TOOLS: dict[str, Callable[..., Any]] = {
    "list_authorized_assets": list_authorized_assets,
    "propose_asset_change": propose_asset_change,
    "get_proposal": get_proposal,
    "retrieve_policy": retrieve_policy,
}

"""Read access to recorded assets, scoped by the authenticated actor.

Scope is always (actor.tenant_id, actor's role). A record outside scope is reported
as not found, so callers cannot probe which IDs exist in other tenants.

Visibility today: an owner sees their own assets. Family and reviewers see none here;
grant-scoped views arrive with the family-access and handover work (Prompts 10–11).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.auth import Actor


class NotFound(LookupError):
    pass


AMOUNT_KINDS = ("balance_minor", "estimated_value_minor", "insurance_cover_minor", "sip_installment_minor", "liability_minor")


def _owned_current(actor: Actor) -> Select:
    return (
        select(m.Asset, m.AssetVersion)
        .join(
            m.AssetVersion,
            and_(m.AssetVersion.asset_id == m.Asset.id, m.AssetVersion.version == m.Asset.current_version),
        )
        .where(m.Asset.tenant_id == actor.tenant_id, m.Asset.owner_user_id == actor.user_id)
    )


def _view(asset: m.Asset, v: m.AssetVersion, conflicts: int) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "asset_type": asset.asset_type,
        "status": asset.status,
        "version": asset.current_version,
        "label": v.label,
        "institution_name": v.institution_name,
        "jurisdiction": v.jurisdiction,
        "masked_reference": v.masked_reference,
        "currency": v.currency,
        "amounts": {k: getattr(v, k) for k in AMOUNT_KINDS if getattr(v, k) is not None},
        "opened_on": v.opened_on.isoformat() if v.opened_on else None,
        "maturity_on": v.maturity_on.isoformat() if v.maturity_on else None,
        "term_months": v.term_months,
        "nomination_status": v.nomination_status,
        "document_location": v.document_location,
        "source_date": v.source_date.isoformat() if v.source_date else None,
        "verification_state": v.verification_state,
        "open_conflicts": conflicts,
    }


def _conflict_counts(session: Session, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not version_ids:
        return {}
    rows = session.execute(
        select(m.FieldEvidence.asset_version_id, func.count())
        .where(m.FieldEvidence.asset_version_id.in_(version_ids), m.FieldEvidence.conflicts_with_current.is_(True))
        .group_by(m.FieldEvidence.asset_version_id)
    )
    return dict(rows.all())


def list_assets(session: Session, actor: Actor) -> list[dict[str, Any]]:
    if actor.role != "owner":
        return []
    rows = session.execute(_owned_current(actor).order_by(m.Asset.created_at, m.Asset.id)).all()
    conflicts = _conflict_counts(session, [v.id for _, v in rows])
    return [_view(a, v, conflicts.get(v.id, 0)) for a, v in rows]


def get_asset(session: Session, actor: Actor, asset_id: uuid.UUID) -> dict[str, Any]:
    if actor.role != "owner":
        raise NotFound
    row = session.execute(_owned_current(actor).where(m.Asset.id == asset_id)).one_or_none()
    if row is None:
        raise NotFound
    asset, version = row
    return _view(asset, version, _conflict_counts(session, [version.id]).get(version.id, 0))


@dataclass
class _Bucket:
    asset_count: int = 0
    oldest_source_date: date | None = None


def inventory_totals(session: Session, actor: Actor) -> dict[str, Any]:
    """Per-currency totals, one figure per amount kind. Kinds and currencies are never
    combined: a balance total excludes insurance cover, SIP installments, estimated
    property values and liabilities by construction."""
    sums: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(AMOUNT_KINDS, 0))
    meta: dict[str, _Bucket] = defaultdict(_Bucket)
    for asset in list_assets(session, actor):
        if asset["status"] != "active" or asset["currency"] is None:
            continue
        cur = asset["currency"]
        for kind, value in asset["amounts"].items():
            sums[cur][kind] += value
        b = meta[cur]
        b.asset_count += 1
        if asset["source_date"]:
            d = date.fromisoformat(asset["source_date"])
            b.oldest_source_date = d if b.oldest_source_date is None else min(b.oldest_source_date, d)
    return {
        "by_currency": {
            cur: {
                "asset_count": meta[cur].asset_count,
                "oldest_source_date": meta[cur].oldest_source_date.isoformat() if meta[cur].oldest_source_date else None,
                **sums[cur],
            }
            for cur in sorted(sums)
        },
        "notes": [
            "Totals cover recorded assets only; completeness is not established.",
            "Figures are as of each record's source date, not live balances.",
            "Insurance cover, SIP installments, property estimates and liabilities are reported separately and never added to balances.",
        ],
    }

"""Rebuild read models from SQL: the recovery path when projections are missing or suspect.

Uses the same functions as the event consumers, so a rebuilt projection is identical to
one produced by events.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.events import consumers
from reclaim_api.events.envelope import Envelope


def rebuild(session: Session, *, today: date, tenant_ids: list[uuid.UUID] | None = None) -> int:
    stmt = select(m.Asset)
    if tenant_ids:
        stmt = stmt.where(m.Asset.tenant_id.in_(tenant_ids))
    count = 0
    ctx = consumers.Context(providers=None, today=today)
    for asset in session.scalars(stmt):
        ev = Envelope(
            event_id=uuid.uuid4(), schema_version=1, tenant_id=str(asset.tenant_id), aggregate_type="asset",
            aggregate_id=str(asset.id), aggregate_version=asset.current_version, event_type="rebuild",
            occurred_at=asset.updated_at, payload={"change_kind": "close" if asset.status == "closed" else "rebuild"},
        )  # fmt: skip
        consumers.project_asset(session, ev, ctx)
        consumers.refresh_freshness(session, ev, ctx)
        consumers.emit_analytics(session, ev, ctx)  # minimized copy for the warehouse (deduplicated by key)
        count += 1
    session.flush()
    return count

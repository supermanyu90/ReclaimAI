"""Outbox publishing: at-least-once delivery from SQL to the event bus.

Rows are written in the same transaction as the change they describe. The publisher
claims a batch with SKIP LOCKED, publishes, and marks rows published. A failure
increments attempts and schedules a retry with exponential backoff; after
OUTBOX_MAX_ATTEMPTS the row is dead-lettered and shows up in stats until replayed.
A crash between publishing and committing republishes later; consumers deduplicate.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.events import envelope as env
from reclaim_api.events.publishers import PublishError

log = logging.getLogger("reclaim_api.events")


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 300))


def publish_pending(session: Session, publisher: Any, *, analytics_key: bytes, batch: int = 50, max_attempts: int = 8, topics: list[str] | None = None) -> dict[str, int]:
    """One pass. Call inside a transaction; returns counts for observability."""
    now = session.scalar(select(func.now()))
    rows = session.scalars(
        select(m.OutboxEvent)
        .where(
            m.OutboxEvent.published_at.is_(None), m.OutboxEvent.dead_lettered_at.is_(None),
            (m.OutboxEvent.next_attempt_at.is_(None)) | (m.OutboxEvent.next_attempt_at <= now),
        )
        .where(m.OutboxEvent.topic.in_(topics) if topics else True)
        .order_by(m.OutboxEvent.occurred_at, m.OutboxEvent.aggregate_version)
        .limit(batch)
        .with_for_update(skip_locked=True)
    ).all()
    counts = {"published": 0, "failed": 0, "dead_lettered": 0}
    for row in rows:
        try:
            publisher.publish(topic=row.topic, envelope=env.build(row, analytics_key=analytics_key))
        except PublishError as err:
            row.attempts += 1
            row.last_error = err.args[0]
            if row.attempts >= max_attempts:
                row.dead_lettered_at = now
                counts["dead_lettered"] += 1
                log.error("outbox event dead-lettered event_type=%s attempts=%d", row.event_type, row.attempts)
            else:
                row.next_attempt_at = now + backoff(row.attempts)
                counts["failed"] += 1
                log.warning("outbox publish failed event_type=%s attempts=%d", row.event_type, row.attempts)
            continue
        row.published_at = now
        row.last_error = None
        counts["published"] += 1
    session.flush()
    return counts


def replay(session: Session, event_ids: list[Any] | None = None) -> int:
    """Re-queue dead-lettered rows. Safe: consumers skip events they already processed."""
    stmt = update(m.OutboxEvent).where(m.OutboxEvent.dead_lettered_at.is_not(None))
    if event_ids:
        stmt = stmt.where(m.OutboxEvent.id.in_(event_ids))
    result = session.execute(stmt.values(dead_lettered_at=None, attempts=0, next_attempt_at=None))
    return result.rowcount


def stats(session: Session) -> dict[str, Any]:
    """Counts only; no tenant or payload data."""

    def n(*where) -> int:
        return session.scalar(select(func.count()).select_from(m.OutboxEvent).where(*where)) or 0

    unpublished = (m.OutboxEvent.published_at.is_(None), m.OutboxEvent.dead_lettered_at.is_(None))
    oldest = session.scalar(select(func.extract("epoch", func.now() - func.min(m.OutboxEvent.occurred_at))).where(*unpublished))
    processed = session.execute(select(m.ProcessedEvent.consumer, func.count()).group_by(m.ProcessedEvent.consumer)).all()
    return {
        "outbox": {
            "pending": n(*unpublished, m.OutboxEvent.attempts == 0),
            "retrying": n(*unpublished, m.OutboxEvent.attempts > 0),
            "dead_lettered": n(m.OutboxEvent.dead_lettered_at.is_not(None)),
            "published": n(m.OutboxEvent.published_at.is_not(None)),
            "oldest_unpublished_age_s": round(float(oldest), 1) if oldest is not None else None,
        },
        "consumer_dead_letters": session.scalar(select(func.count()).select_from(m.DeadLetter)) or 0,
        "processed_by_consumer": {consumer: count for consumer, count in processed},
        "note": "Delivery and processing of application events. Nothing here moves or settles money.",
    }

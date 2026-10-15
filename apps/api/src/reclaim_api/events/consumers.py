"""Event consumers run by the worker.

Each consumer handles an event in its own transaction, together with an insert into
processed_events(consumer, event_id): a redelivered event is skipped, and a failure
rolls back that consumer's work without affecting the others. Projections and findings
are rebuilt from SQL rather than from event payloads, so out-of-order or stale events
can never regress them.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import records
from reclaim_api.events.envelope import ANALYTICS_TOPIC, Envelope
from reclaim_api.lib.log import error_tag

log = logging.getLogger("reclaim_api.consumers")

STALE_AFTER = timedelta(days=365)


class Retryable(RuntimeError):
    """A transient failure: the message should be redelivered."""


@dataclass
class Context:
    providers: Any
    today: date


def _current(session: Session, asset_id: uuid.UUID) -> tuple[m.Asset, m.AssetVersion] | None:
    row = session.execute(
        select(m.Asset, m.AssetVersion)
        .join(m.AssetVersion, and_(m.AssetVersion.asset_id == m.Asset.id, m.AssetVersion.version == m.Asset.current_version))
        .where(m.Asset.id == asset_id)
    ).one_or_none()
    return (row[0], row[1]) if row else None


# --- consumers -------------------------------------------------------------------------


def project_asset(session: Session, ev: Envelope, ctx: Context) -> str:
    asset_id = uuid.UUID(ev.aggregate_id)
    existing = session.get(m.AssetProjection, asset_id)
    if existing and ev.aggregate_version < existing.version:
        return "stale"  # a newer version is already projected
    current = _current(session, asset_id)
    if current is None:
        return "missing"
    asset, version = current
    view = records._view(asset, version, conflicts=0)
    stmt = insert(m.AssetProjection).values(
        asset_id=asset.id, tenant_id=asset.tenant_id, owner_user_id=asset.owner_user_id, version=asset.current_version, view=view
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["asset_id"],
            set_={"version": stmt.excluded.version, "view": stmt.excluded.view, "projected_at": stmt.excluded.projected_at},
            where=m.AssetProjection.version <= stmt.excluded.version,
        )
    )
    return "projected"


def freshness_findings(asset: m.Asset, v: m.AssetVersion, today: date) -> list[tuple[str, str]]:
    findings = []
    if v.source_date is None:
        findings.append(("no_source_date", "No date recorded for the information"))
    elif today - v.source_date > STALE_AFTER:
        findings.append(("stale_source", f"Information dates from {v.source_date.isoformat()}; over a year old"))
    if v.nomination_status == "unknown":
        findings.append(("unknown_nomination", "Nomination status not known; check with the institution"))
    if v.document_location is None:
        findings.append(("missing_document_location", "Where the papers are kept is not recorded"))
    return findings


def refresh_freshness(session: Session, ev: Envelope, ctx: Context) -> str:
    current = _current(session, uuid.UUID(ev.aggregate_id))
    if current is None:
        return "missing"
    asset, version = current
    session.execute(delete(m.FreshnessFinding).where(m.FreshnessFinding.asset_id == asset.id))
    if asset.status == "active":
        for kind, detail in freshness_findings(asset, version, ctx.today):
            session.add(m.FreshnessFinding(tenant_id=asset.tenant_id, asset_id=asset.id, asset_version=asset.current_version, kind=kind, detail=detail))
    return "refreshed"


def mark_passports_stale(session: Session, ev: Envelope, ctx: Context) -> str:
    asset = session.get(m.Asset, uuid.UUID(ev.aggregate_id))
    if asset is None:
        return "missing"
    # Only passports generated before the change became stale.
    result = session.execute(
        update(m.PassportVersion)
        .where(m.PassportVersion.owner_user_id == asset.owner_user_id, m.PassportVersion.status == "current",
               m.PassportVersion.generated_at < ev.occurred_at)
        .values(status="stale", stale_at=ev.occurred_at)
    )  # fmt: skip
    return f"staled {result.rowcount}"


def queue_report(session: Session, ev: Envelope, ctx: Context) -> str:
    asset = session.get(m.Asset, uuid.UUID(ev.aggregate_id))
    if asset is None:
        return "missing"
    session.execute(
        insert(m.ReportJob)
        .values(tenant_id=asset.tenant_id, owner_user_id=asset.owner_user_id, reason_event_id=ev.event_id)
        .on_conflict_do_nothing(index_elements=["reason_event_id"])
    )
    return "queued"


def emit_analytics(session: Session, ev: Envelope, ctx: Context) -> str:
    """A separate, minimized analytics event via the outbox (topic "analytics")."""
    current = _current(session, uuid.UUID(ev.aggregate_id))
    if current is None:
        return "missing"
    asset, version = current
    session.execute(
        insert(m.OutboxEvent)
        .values(
            tenant_id=asset.tenant_id, topic=ANALYTICS_TOPIC, aggregate_type="asset", aggregate_id=asset.id,
            aggregate_version=ev.aggregate_version, event_type="analytics.asset_changed",
            payload={"asset_type": asset.asset_type, "change_kind": ev.payload.get("change_kind"),
                     "verification_state": version.verification_state, "jurisdiction": version.jurisdiction,
                     "occurred_on": ev.occurred_at.date().isoformat(),
                     "source_date": version.source_date.isoformat() if version.source_date else None},
        )
        .on_conflict_do_nothing()
    )  # fmt: skip
    return "emitted"


def process_document(session: Session, ev: Envelope, ctx: Context) -> str:
    from reclaim_api.documents import service as documents
    from reclaim_api.providers.base import ProviderUnavailable

    try:
        return documents.process_document(session, uuid.UUID(ev.aggregate_id), providers=ctx.providers, today=ctx.today)
    except ProviderUnavailable as err:
        raise Retryable(err.args[0]) from None


Consumer = Callable[[Session, Envelope, Context], str]
SUBSCRIPTIONS: dict[str, list[tuple[str, Consumer]]] = {
    "asset.version_confirmed": [
        ("asset-projection", project_asset),
        ("freshness", refresh_freshness),
        ("passport-stale", mark_passports_stale),
        ("report-queue", queue_report),
        ("analytics-export", emit_analytics),
    ],
    "document.uploaded": [("document-processor", process_document)],
}


@dataclass
class Result:
    status: int  # 204 ack, 500 retry
    outcomes: dict[str, str]


def dispatch(session_factory: Callable[[], Session], ev: Envelope, ctx: Context) -> Result:
    subs = SUBSCRIPTIONS.get(ev.event_type)
    if subs is None:
        with session_factory() as s, s.begin():
            s.add(m.DeadLetter(consumer="dispatcher", event_id=ev.event_id, event_type=ev.event_type, reason="unknown event type"))
        return Result(204, {"dispatcher": "dead-lettered"})

    outcomes: dict[str, str] = {}
    failed = False
    for name, consumer in subs:
        try:
            with session_factory() as s, s.begin():
                claimed = s.scalar(
                    insert(m.ProcessedEvent).values(consumer=name, event_id=ev.event_id).on_conflict_do_nothing().returning(m.ProcessedEvent.event_id)
                )
                if claimed is None:
                    outcomes[name] = "duplicate"
                    continue
                outcomes[name] = consumer(s, ev, ctx)
        except Exception as err:  # noqa: BLE001 — any consumer failure means: roll back, redeliver
            failed = True
            outcomes[name] = "retry"
            log.warning("consumer failed consumer=%s event_type=%s %s", name, ev.event_type, error_tag(err))
    return Result(500 if failed else 204, outcomes)

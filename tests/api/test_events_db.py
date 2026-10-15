"""Prompt 6 acceptance: duplicate delivery, crash/retry and out-of-order events preserve one
logical update; failed publishing is retried from the outbox; failures are observable."""

import importlib
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.db import get_engine
from reclaim_api.events import consumers, envelope, outbox
from reclaim_api.events.publishers import PublishError
from reclaim_api.providers.registry import LOCAL_PUSH_TOKEN
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
worker = importlib.import_module("reclaim_worker.app")
api = TestClient(main.app)
wk = TestClient(worker.app)
RAMESH = {"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}
PUSH = {"Authorization": f"Bearer local:{LOCAL_PUSH_TOKEN}"}
KEY = b"test-analytics-key"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    monkeypatch.setattr(worker, "_today", lambda: date(2026, 10, 15))
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
        s.execute(text("DELETE FROM dead_letters"))
        s.execute(text("DELETE FROM processed_events"))
    yield


def correct_pension(amount: str = "₹15,20,000") -> dict:
    pid = api.post("/v1/chat/messages", json={"text": f"Actually my pension balance is now {amount}"}, headers=RAMESH).json()["proposal"]["id"]
    res = api.post(f"/v1/proposals/{pid}/confirm", headers={**RAMESH, "Idempotency-Key": str(uuid.uuid4())})
    assert res.status_code == 200
    return res.json()


def envelopes(event_type: str = "asset.version_confirmed") -> list[envelope.Envelope]:
    with Session(get_engine()) as s:
        rows = s.scalars(select(m.OutboxEvent).where(m.OutboxEvent.event_type == event_type).order_by(m.OutboxEvent.aggregate_version)).all()
        return [envelope.build(r, analytics_key=KEY) for r in rows]


def push(ev: envelope.Envelope, headers=PUSH):
    return wk.post("/pubsub/push", json=envelope.to_push_body(ev, "test"), headers=headers)


def projection(asset_key: str) -> m.AssetProjection:
    with Session(get_engine()) as s:
        return s.get(m.AssetProjection, fixture_id(asset_key))


def count(model, *where) -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


# --- the happy path ------------------------------------------------------------------------


def test_confirmed_change_flows_through_outbox_to_all_consumers(drain):
    correct_pension()
    assert count(m.OutboxEvent, m.OutboxEvent.published_at.is_(None), m.OutboxEvent.topic == "app-events") == 1  # written with the change
    drain()
    p = projection("pension")
    assert p.version == 3 and p.view["amounts"]["balance_minor"] == 152000000
    assert count(m.ReportJob) == 1
    assert {r.consumer for r in _processed()} == {"asset-projection", "freshness", "passport-stale", "report-queue", "analytics-export"}
    assert count(m.OutboxEvent, m.OutboxEvent.published_at.is_(None)) == 0  # app and analytics events all delivered
    from sqlalchemy import text as sql_text

    with get_engine().connect() as conn:
        rows = conn.execute(sql_text("SELECT event_type, tenant_id, payload::text FROM warehouse.analytics_raw WHERE aggregate_version = 3")).all()
    assert len(rows) == 1 and rows[0][0] == "analytics.asset_changed"
    assert str(fixture_id("patel")) not in rows[0][1] and "152000000" not in rows[0][2]  # minimized


def _processed() -> list[m.ProcessedEvent]:
    with Session(get_engine()) as s:
        return s.scalars(select(m.ProcessedEvent)).all()


# --- duplicates, order, crashes ------------------------------------------------------------


def test_duplicate_delivery_is_processed_once():
    correct_pension()
    ev = envelopes()[0]
    assert push(ev).status_code == 204
    assert push(ev).status_code == 204
    assert count(m.ReportJob) == 1
    assert count(m.ProcessedEvent, m.ProcessedEvent.event_id == ev.event_id) == 5
    assert count(m.OutboxEvent, m.OutboxEvent.event_type == "analytics.asset_changed", m.OutboxEvent.aggregate_version == 3) == 1


def test_out_of_order_events_never_regress_the_projection():
    correct_pension("₹15,20,000")  # v3
    correct_pension("₹16,00,000")  # v4
    v3, v4 = envelopes()
    assert (v3.aggregate_version, v4.aggregate_version) == (3, 4)
    push(v4)
    push(v3)  # arrives late
    p = projection("pension")
    assert p.version == 4 and p.view["amounts"]["balance_minor"] == 160000000


def test_crash_mid_delivery_retries_without_double_applying(drain, monkeypatch):
    correct_pension()
    calls = {"n": 0}
    real = consumers.refresh_freshness

    def flaky(session, ev, ctx):
        calls["n"] += 1
        session.add(m.FreshnessFinding(tenant_id=fixture_id("patel"), asset_id=fixture_id("pension"), asset_version=99, kind="no_source_date", detail="partial"))
        session.flush()
        raise RuntimeError("worker crashed after a partial write")

    monkeypatch.setitem(consumers.SUBSCRIPTIONS, "asset.version_confirmed",
                        [(n, flaky if n == "freshness" else f) for n, f in consumers.SUBSCRIPTIONS["asset.version_confirmed"]])  # fmt: skip
    counts = drain(max_passes=1)
    assert counts["failed"] == 1  # worker answered 500 → outbox keeps the event
    with Session(get_engine()) as s:
        assert s.scalar(select(func.count()).select_from(m.FreshnessFinding).where(m.FreshnessFinding.asset_version == 99)) == 0  # rolled back
    monkeypatch.setitem(consumers.SUBSCRIPTIONS, "asset.version_confirmed",
                        [(n, real if n == "freshness" else f) for n, f in consumers.SUBSCRIPTIONS["asset.version_confirmed"]])  # fmt: skip
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.OutboxEvent).values(next_attempt_at=None))
    drain()
    assert calls["n"] == 1
    assert count(m.ReportJob) == 1  # the consumers that succeeded the first time did not run again
    assert count(m.ProcessedEvent, m.ProcessedEvent.consumer == "freshness") == 1


# --- publishing failures ----------------------------------------------------------------------


class Down:
    def publish(self, **_):
        raise PublishError("pubsub publish failed (ServiceUnavailable)")


def test_failed_publishing_is_retried_then_dead_lettered_and_replayable(drain):
    correct_pension()
    with Session(get_engine()) as s, s.begin():
        outbox.publish_pending(s, Down(), analytics_key=KEY, max_attempts=3, topics=["app-events"])
    stats = api.get("/v1/system/events").json()
    assert stats["outbox"]["retrying"] == 1 and stats["outbox"]["published"] == 0
    for _ in range(2):
        with Session(get_engine()) as s, s.begin():
            s.execute(update(m.OutboxEvent).values(next_attempt_at=None))
            outbox.publish_pending(s, Down(), analytics_key=KEY, max_attempts=3, topics=["app-events"])
    stats = api.get("/v1/system/events").json()["outbox"]
    assert stats["dead_lettered"] == 1 and stats["retrying"] == 0
    assert projection("pension").version == 2  # nothing delivered, nothing projected

    with Session(get_engine()) as s, s.begin():
        assert outbox.replay(s) == 1
    drain()
    assert projection("pension").version == 3
    assert api.get("/v1/system/events").json()["outbox"]["dead_lettered"] == 0


def test_backoff_is_bounded():
    assert [outbox.backoff(n).total_seconds() for n in (1, 2, 5, 20)] == [2, 4, 32, 300]


# --- worker boundary ----------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer local:wrong"}, {"Authorization": "Basic x"}])
def test_push_requires_authentication(headers):
    correct_pension()
    assert push(envelopes()[0], headers=headers).status_code == 401
    assert count(m.ProcessedEvent) == 0


def test_local_token_is_refused_in_cloud_mode(monkeypatch):
    correct_pension()
    monkeypatch.setattr(worker.settings, "app_mode", "cloud")  # shared settings object: switch after the API call
    assert push(envelopes()[0]).status_code == 401


def test_oidc_requires_the_push_service_account(monkeypatch):
    from google.oauth2 import id_token

    monkeypatch.setattr(worker.settings, "worker_audience", "https://worker.example")
    monkeypatch.setattr(worker.settings, "push_service_account", "pubsub-push@demo.iam.gserviceaccount.com")
    claims = {"email": "someone-else@demo.iam.gserviceaccount.com", "email_verified": True}
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, request, audience: claims)
    assert worker.verify_oidc("token") is False
    claims["email"] = "pubsub-push@demo.iam.gserviceaccount.com"
    assert worker.verify_oidc("token") is True


def test_cloud_outbox_task_accepts_only_the_scheduler_identity(monkeypatch):
    from google.oauth2 import id_token

    monkeypatch.setattr(worker.settings, "app_mode", "cloud")
    monkeypatch.setattr(worker.settings, "worker_audience", "https://worker.example")
    monkeypatch.setattr(worker.settings, "push_service_account", "pubsub-push@demo.iam.gserviceaccount.com")
    monkeypatch.setattr(worker.settings, "scheduler_service_account", "scheduler@demo.iam.gserviceaccount.com")
    monkeypatch.setattr(worker.settings, "channel_hash_key", "test-only-key")
    claims = {"email": "pubsub-push@demo.iam.gserviceaccount.com", "email_verified": True}
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, request, audience: claims)
    bearer = {"Authorization": "Bearer t"}
    assert wk.post("/tasks/publish-outbox", headers=bearer).status_code == 401  # push identity cannot run tasks
    assert wk.post("/tasks/publish-outbox", headers={"Authorization": "Bearer local:x"}).status_code == 401
    claims["email"] = "scheduler@demo.iam.gserviceaccount.com"
    assert wk.post("/tasks/publish-outbox", headers=bearer).status_code == 200
    assert wk.post("/pubsub/push", json={}, headers=bearer).status_code == 401  # and the scheduler cannot push


def test_malformed_and_unknown_messages_are_dead_lettered_not_retried():
    res = wk.post("/pubsub/push", json={"message": {"data": "not-base64!!"}}, headers=PUSH)
    assert res.status_code == 204
    correct_pension()
    ev = envelopes()[0].model_copy(update={"event_type": "asset.teleported", "event_id": uuid.uuid4()})
    assert push(ev).status_code == 204
    reasons = api.get("/v1/system/events").json()["consumer_dead_letters"]
    assert reasons == 2


# --- what leaves in an event ------------------------------------------------------------------------


def test_envelopes_carry_only_whitelisted_minimal_data():
    with Session(get_engine()) as s, s.begin():
        row = m.OutboxEvent(
            tenant_id=fixture_id("patel"), aggregate_type="asset", aggregate_id=fixture_id("savings"), aggregate_version=9,
            event_type="asset.version_confirmed", correlation_id="cid-1",
            payload={"change_kind": "update", "document_text": "Principal Rs. 5,00,000", "account_number": "000011112222", "label": "Savings"},
        )  # fmt: skip
        s.add(row)
        s.flush()
        app_env = envelope.build(row, analytics_key=KEY)
        row.topic = "analytics"
        row.event_type = "analytics.asset_changed"
        an_env = envelope.build(row, analytics_key=KEY)
    assert app_env.payload == {"change_kind": "update"}
    dumped = an_env.model_dump_json()
    assert str(fixture_id("patel")) not in dumped and str(fixture_id("savings")) not in dumped and an_env.correlation_id is None


def test_passport_marked_stale_only_if_generated_before_the_change(drain):
    with Session(get_engine()) as s, s.begin():
        for version, when in ((1, "2026-01-01"), (2, "2999-01-01")):
            s.add(m.PassportVersion(tenant_id=fixture_id("patel"), owner_user_id=fixture_id("ramesh"), version=version, snapshot={},
                                    snapshot_sha256="0" * 64, generated_at=text(f"'{when}'::timestamptz")))  # fmt: skip
    correct_pension()
    drain()
    with Session(get_engine()) as s:
        statuses = dict(s.execute(select(m.PassportVersion.version, m.PassportVersion.status)).all())
    assert statuses == {1: "stale", 2: "current"}


def test_document_upload_is_processed_by_the_worker(drain):
    from pathlib import Path

    receipt = (Path(__file__).resolve().parents[2] / "fixtures" / "documents" / "fd_receipt.txt").read_bytes()
    doc = api.post("/v1/documents", files={"file": ("r.txt", receipt, "text/plain")}, headers=RAMESH).json()
    assert doc["status"] == "pending"
    drain()
    assert api.get(f"/v1/documents/{doc['id']}", headers=RAMESH).json()["status"] == "processed"


def test_findings_are_served_from_the_read_model():
    kinds = {(f["label"], f["kind"]) for f in api.get("/v1/findings", headers=RAMESH).json()["findings"]}
    assert ("Savings account", "stale_source") in kinds and ("Term life policy", "unknown_nomination") in kinds
    assert not any(label == "Flat, Kothrud, Pune (home loan with Sagar Bharat Bank)" for label, _ in kinds)

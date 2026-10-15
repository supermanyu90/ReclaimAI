"""Prompt 8 acceptance: duplicate events do not inflate totals; INR and SGD stay separate;
late events land correctly; empty histories produce insufficient-data status. Every flag
carries window, sample size, rule/threshold and explanation; scope comes from the actor."""

import importlib
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from reclaim_api.analytics import synthetic
from reclaim_api.analytics.warehouse import LocalWarehouse
from reclaim_api.config import get_settings
from reclaim_api.db import get_engine
from reclaim_api.events import outbox
from reclaim_api.events.envelope import ANALYTICS_TOPIC, TRANSACTIONS_TOPIC, Envelope
from reclaim_api.seed import fixture_id, seed
from reclaim_api.telegram.linking import hash_key

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)
RAMESH = {"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}
KEY = hash_key(get_settings())
WAREHOUSE = LocalWarehouse(get_engine())


class Sink:
    """Publishes minimized topics straight into the local warehouse (as the subscription would)."""

    def publish(self, *, topic, envelope):
        WAREHOUSE.ingest(topic, envelope)
        return str(envelope.event_id)


def publish() -> None:
    while True:
        with Session(get_engine()) as s, s.begin():
            if outbox.publish_pending(s, Sink(), analytics_key=KEY, topics=[TRANSACTIONS_TOPIC, ANALYTICS_TOPIC], batch=500)["published"] == 0:
                return


@pytest.fixture(autouse=True)
def loaded(monkeypatch):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    WAREHOUSE.ensure()  # a fresh database (CI) has no warehouse schema yet
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
        s.execute(text("DELETE FROM warehouse.ingest_errors"))
    with Session(get_engine()) as s, s.begin():
        synthetic.write_outbox(s, synthetic.generate(), analytics_key=KEY)
    publish()
    WAREHOUSE.redeliver(7)
    yield


def summary(headers=RAMESH, **params) -> dict:
    res = api.get("/v1/analytics", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def expected_totals() -> dict[tuple[str, str], dict[str, int]]:
    out: dict = defaultdict(lambda: {"inflow_minor": 0, "outflow_minor": 0})
    for t in synthetic.generate():
        out[(t.currency, t.booked.strftime("%Y-%m"))]["inflow_minor" if t.direction == "credit" else "outflow_minor"] += t.amount_minor
    return out


def test_duplicate_deliveries_do_not_inflate_totals():
    d = summary()
    assert d["duplicates"]["deliveries"] == d["duplicates"]["distinct_events"] + 7
    expected = expected_totals()
    for cur, rows in d["flows_by_currency"].items():
        for r in rows:
            assert {k: r[k] for k in ("inflow_minor", "outflow_minor")} == expected[(cur, r["month"])]
    assert d["sample_size"] == len(synthetic.generate())


def test_inr_and_sgd_stay_separate():
    d = summary()
    assert set(d["flows_by_currency"]) == {"INR", "SGD"}
    sgd_in = sum(r["inflow_minor"] for r in d["flows_by_currency"]["SGD"])
    assert sgd_in == sum(t.amount_minor for t in synthetic.generate() if t.currency == "SGD" and t.direction == "credit")
    assert all(r["inflow"].startswith("S$") for r in d["flows_by_currency"]["SGD"])


def test_late_event_lands_in_its_own_month():
    march_before = next(r for r in summary()["flows_by_currency"]["INR"] if r["month"] == "2026-03")["outflow_minor"]
    late = synthetic.Txn("late:pharmacy", "savings", datetime(2026, 3, 14, 18, tzinfo=UTC), 125_000, "debit", "INR", "other", "UPI DR PHARMACY")
    with Session(get_engine()) as s, s.begin():
        synthetic.write_outbox(s, [late], analytics_key=KEY)
    publish()  # ingested now, in October
    d = summary()
    march = next(r for r in d["flows_by_currency"]["INR"] if r["month"] == "2026-03")
    assert march["outflow_minor"] == march_before + 125_000
    assert not any(r["month"] == "2026-10" for r in d["flows_by_currency"]["INR"])
    with get_engine().connect() as conn:
        booked, published = conn.execute(text("SELECT occurred_at, publish_time FROM warehouse.transactions_raw WHERE payload->>'narrative' = 'UPI DR PHARMACY' ORDER BY publish_time DESC LIMIT 1")).one()
    assert booked.date() == date(2026, 3, 14) and published.date() > booked.date()


def test_empty_history_is_insufficient_data():
    d = summary(headers={"Authorization": f"Bearer dev:{fixture_id('kavita')}"})
    assert d["status"] == "insufficient_data" and d["sample_size"] == 0 and d["flags"] == [] and d["flows_by_currency"] == {}
    assert d["handover_turnaround"]["status"] == "insufficient_data"


def test_flags_are_explainable_review_requests():
    d = summary()
    kinds = sorted((f["kind"], f.get("month") or f.get("date")) for f in d["flags"])
    assert kinds == [("recurring_gap", "2026-07"), ("unusual_amount", "2026-08-22")]
    for f in d["flags"]:
        assert f["rule"] and f["threshold"] and f["data_window"] and f["sample_size"] > 0 and f["explanation"]
    assert "not evidence of fraud" in d["notice"]


def test_short_histories_are_not_flagged():
    # A window of two months cannot establish a recurring series.
    d = summary(start="2026-08-01", end="2026-09-30")
    assert not any(f["kind"] == "recurring_gap" for f in d["flags"])


def test_stale_record_counts_use_latest_source_dates():
    s = summary()["stale_records"]
    assert (s["assets"], s["stale"]) == (6, 2)  # 2023 savings statement and 2025 policy schedule


def test_scope_comes_from_the_actor_only():
    assert api.get("/v1/analytics", headers={"Authorization": f"Bearer dev:{fixture_id('priya')}"}).status_code == 403
    mine = summary(tenant=str(fixture_id("mehta")))  # an extra parameter is ignored
    assert mine["sample_size"] == len(synthetic.generate())
    assert api.get("/v1/analytics", headers=RAMESH, params={"start": "2020-01-01", "end": "2026-10-15"}).status_code == 422


def test_schema_errors_are_recorded_as_pipeline_errors():
    bad = Envelope(event_id=uuid.uuid4(), schema_version=1, tenant_id="x", aggregate_type="transaction", aggregate_id="y",
                   aggregate_version=1, event_type="transaction.recorded", occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                   payload={"account_token": "a", "transaction_id": "t", "amount_minor": -5, "direction": "debit", "currency": "INR", "feed_category": "other"})  # fmt: skip
    WAREHOUSE.ingest(TRANSACTIONS_TOPIC, bad)
    errors = summary(start="2026-10-01", end="2026-10-15")["pipeline_errors"]
    assert errors == [{"topic": "transactions", "reason": "payload.amount_minor must be positive", "errors": 1}]


def test_warehouse_rows_are_minimized():
    with get_engine().connect() as conn:
        tenants = {r[0] for r in conn.execute(text("SELECT DISTINCT tenant_id FROM warehouse.transactions_raw"))}
        payloads = " ".join(r[0] for r in conn.execute(text("SELECT payload::text FROM warehouse.transactions_raw")))
    assert str(fixture_id("patel")) not in tenants and all(len(t) == 32 for t in tenants)
    for forbidden in ("Ramesh", "Priya", "4821", str(fixture_id("savings"))):
        assert forbidden not in payloads

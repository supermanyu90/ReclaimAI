"""Deterministic synthetic transaction feed (no bank is connected).

Twelve months of activity on Ramesh Patel's savings (INR) and SGD savings accounts, with
deliberate review cases: the SIP missing in July 2026, an out-of-pattern ₹2,50,000 transfer
in August 2026, and simulated Pub/Sub redeliveries. Transactions are written to the outbox on
the "transactions" topic, separate from asset events. Accounts are identified only by a
keyed-hash token; narratives contain no names or account numbers.

    reclaim-transactions seed [--redeliver 5]   # also publishes pending minimized analytics events
"""

import argparse
import calendar
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.events.envelope import ANALYTICS_TOPIC, TRANSACTIONS_TOPIC, pseudonym
from reclaim_api.seed import fixture_id

NS = uuid.UUID("9b1f2c44-6a7e-4f0b-8d3a-0c5e1f2a3b4c")
MONTHS = [(2025, 10), (2025, 11), (2025, 12)] + [(2026, n) for n in range(1, 10)]


@dataclass(frozen=True)
class Txn:
    key: str
    asset_key: str
    booked: datetime
    amount_minor: int
    direction: str
    currency: str
    category: str
    narrative: str


def _at(y: int, mth: int, day: int, hour: int = 10) -> datetime:
    return datetime.combine(date(y, mth, min(day, calendar.monthrange(y, mth)[1])), time(hour), tzinfo=UTC)


def generate() -> list[Txn]:
    rng = random.Random(20261015)
    out: list[Txn] = []
    for y, mth in MONTHS:
        tag = f"{y}-{mth:02d}"

        def add(key: str, *fields: object, _tag: str = tag) -> None:
            out.append(Txn(f"{_tag}:{key}", *fields))

        add("pension", "savings", _at(y, mth, 1), 3_800_000, "credit", "INR", "income", "NEFT CR RASHTRIYA PENSION TRUST")
        if (y, mth) != (2026, 7):  # the missing SIP
            add("sip", "savings", _at(y, mth, 5), 1_000_000, "debit", "INR", "investment_contribution", "ACH DR KAVERI MF SIP")
        add("emi", "savings", _at(y, mth, 7), 2_150_000, "debit", "INR", "loan_payment", "EMI DR SAGAR BHARAT HOME LOAN")
        add("premium", "savings", _at(y, mth, 10), 420_000, "debit", "INR", "insurance_premium", "ECS DR NAVJEEVAN LIFE PREMIUM")
        add("family", "savings", _at(y, mth, 20), 800_000, "debit", "INR", "transfer", "IMPS DR FAMILY TRANSFER")
        for i in range(rng.randint(3, 5)):
            merchant = rng.choice(["UPI DR GROCERY", "UPI DR PHARMACY", "BBPS DR ELECTRICITY", "UPI DR FUEL"])
            add(f"spend{i}", "savings", _at(y, mth, rng.randint(2, 27), 18), rng.randint(60_000, 600_000), "debit", "INR", "other", merchant)
        add("rent", "sgd_savings", _at(y, mth, 3), 120_000, "credit", "SGD", "income", "GIRO CR RENTAL INCOME")
        for i in range(rng.randint(1, 2)):
            add(f"sgspend{i}", "sgd_savings", _at(y, mth, rng.randint(4, 26), 12), rng.randint(4_000, 18_000), "debit", "SGD", "other", "NETS DR RETAIL")
    out.append(Txn("2026-08:large-transfer", "savings", _at(2026, 8, 22, 11), 25_000_000, "debit", "INR", "transfer", "RTGS DR TRANSFER"))
    return out


def txn_id(t: Txn) -> uuid.UUID:
    return uuid.uuid5(NS, t.key)


def write_outbox(session: Session, txns: list[Txn], *, analytics_key: bytes, tenant_key: str = "patel") -> int:
    """Idempotent: deterministic event IDs, ON CONFLICT DO NOTHING."""
    tenant = fixture_id(tenant_key)
    rows = [
        {
            "id": uuid.uuid5(NS, f"event:{t.key}"), "tenant_id": tenant, "topic": TRANSACTIONS_TOPIC,
            "aggregate_type": "transaction", "aggregate_id": txn_id(t), "aggregate_version": 1,
            "event_type": "transaction.recorded", "occurred_at": t.booked,  # event time = booking time
            "payload": {"account_token": pseudonym(fixture_id(t.asset_key), analytics_key), "transaction_id": str(txn_id(t)),
                        "amount_minor": t.amount_minor, "direction": t.direction, "currency": t.currency,
                        "feed_category": t.category, "narrative": t.narrative},
        }
        for t in txns
    ]  # fmt: skip
    if not rows:
        return 0
    inserted = session.scalars(insert(m.OutboxEvent).values(rows).on_conflict_do_nothing().returning(m.OutboxEvent.id)).all()
    return len(inserted)


def load_feed(redeliver: int = 5) -> tuple[int, int, int]:
    """Write the synthetic feed to the outbox and publish it (idempotent). Returns
    (outbox rows written, events published, simulated duplicate deliveries)."""
    from reclaim_api.config import get_settings
    from reclaim_api.db import get_engine
    from reclaim_api.events import outbox
    from reclaim_api.providers.registry import get_providers
    from reclaim_api.telegram.linking import hash_key

    key = hash_key(get_settings())
    providers = get_providers()
    with Session(get_engine()) as session, session.begin():
        written = write_outbox(session, generate(), analytics_key=key)
    published = 0
    while True:
        with Session(get_engine()) as session, session.begin():
            counts = outbox.publish_pending(session, providers.events, analytics_key=key, topics=[TRANSACTIONS_TOPIC, ANALYTICS_TOPIC], batch=200)
        published += counts["published"]
        if counts["published"] == 0:
            break
    # Simulate redelivery once, when the feed is first loaded, so repeated runs stay idempotent.
    duplicated = providers.warehouse.redeliver(redeliver) if redeliver and written else 0
    return written, published, duplicated


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic transaction feed")
    parser.add_argument("command", choices=["seed"])
    parser.add_argument("--redeliver", type=int, default=5, help="simulate N duplicate Pub/Sub deliveries")
    args = parser.parse_args()
    written, published, duplicated = load_feed(args.redeliver)
    print(f"synthetic transactions: outbox_written={written} published={published} simulated_redeliveries={duplicated}")

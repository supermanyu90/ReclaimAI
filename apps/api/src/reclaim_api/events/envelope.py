"""Event envelopes. Built only from outbox rows; payloads are whitelisted per event type,
so no document text, message text, labels, amounts or identifiers can leave in an event.

Analytics and transaction envelopes are further minimized: tenant and aggregate IDs are replaced by keyed
hashes and the correlation ID is dropped.
"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from reclaim_api import models as m

SCHEMA_VERSION = 1
APP_TOPIC = "app-events"
ANALYTICS_TOPIC = "analytics"
TRANSACTIONS_TOPIC = "transactions"
MINIMIZED_TOPICS = {ANALYTICS_TOPIC, TRANSACTIONS_TOPIC}

# Allowed payload keys per event type. Anything else is dropped before publishing.
PAYLOAD_KEYS: dict[str, set[str]] = {
    "asset.version_confirmed": {"asset_version_id", "change_kind"},
    "document.uploaded": {"duplicate_of"},
    "analytics.asset_changed": {"asset_type", "change_kind", "verification_state", "jurisdiction", "occurred_on", "source_date"},
    # Synthetic feed transactions: pseudonymous account token, never an account number or a name.
    "transaction.recorded": {"account_token", "transaction_id", "amount_minor", "direction", "currency", "feed_category", "narrative"},
}


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    schema_version: int
    tenant_id: str  # a UUID on app-events; a keyed hash on analytics
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    occurred_at: datetime
    correlation_id: str | None = None
    payload: dict[str, Any]


def pseudonym(value: uuid.UUID | str, key: bytes) -> str:
    return hmac.new(key, f"analytics:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def build(row: m.OutboxEvent, *, analytics_key: bytes) -> Envelope:
    allowed = PAYLOAD_KEYS.get(row.event_type, set())
    payload = {k: v for k, v in (row.payload or {}).items() if k in allowed}
    if row.topic in MINIMIZED_TOPICS:
        return Envelope(
            event_id=row.id, schema_version=row.schema_version, tenant_id=pseudonym(row.tenant_id, analytics_key),
            aggregate_type=row.aggregate_type, aggregate_id=pseudonym(row.aggregate_id, analytics_key),
            aggregate_version=row.aggregate_version, event_type=row.event_type, occurred_at=row.occurred_at, payload=payload,
        )  # fmt: skip
    return Envelope(
        event_id=row.id, schema_version=row.schema_version, tenant_id=str(row.tenant_id), aggregate_type=row.aggregate_type,
        aggregate_id=str(row.aggregate_id), aggregate_version=row.aggregate_version, event_type=row.event_type,
        occurred_at=row.occurred_at, correlation_id=row.correlation_id, payload=payload,
    )  # fmt: skip


def to_push_body(env: Envelope, subscription: str) -> dict[str, Any]:
    """The JSON shape Pub/Sub push delivers, so local and cloud share one consumer path."""
    data = base64.b64encode(env.model_dump_json().encode()).decode()
    return {
        "message": {"data": data, "messageId": str(env.event_id), "attributes": {"event_type": env.event_type}},
        "subscription": subscription,
    }


def from_push_body(body: Any) -> Envelope:
    """Raises ValueError for anything that is not a well-formed envelope."""
    try:
        data = base64.b64decode(body["message"]["data"], validate=True)
        return Envelope.model_validate(json.loads(data))
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError("malformed push message") from err

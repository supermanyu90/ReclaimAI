"""Analytical warehouse: BigQuery in cloud, a separate Postgres schema locally.

The analytical SQL lives in analytics/sql/*.sql in BigQuery dialect. BigQueryWarehouse runs
it natively; LocalWarehouse transpiles it to Postgres with sqlglot, so the same queries are
exercised by the local tests. Raw tables mirror what a Pub/Sub BigQuery subscription writes
(envelope columns, JSON payload, publish_time); views deduplicate by event_id.

Ingestion: in cloud, Pub/Sub BigQuery subscriptions write raw rows (no code here). Locally,
the event publisher hands analytics/transaction envelopes to LocalWarehouse.ingest, which
validates them against the table schema and records rejects in ingest_errors.
"""

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from reclaim_api.events.envelope import ANALYTICS_TOPIC, TRANSACTIONS_TOPIC, Envelope
from reclaim_api.lib.log import error_tag
from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable

SQL_DIR = Path(__file__).with_name("sql")
SCHEMA = "warehouse"
RAW_TABLES = {ANALYTICS_TOPIC: "analytics_raw", TRANSACTIONS_TOPIC: "transactions_raw"}
REQUIRED_PAYLOAD = {
    TRANSACTIONS_TOPIC: {"account_token": str, "transaction_id": str, "amount_minor": int, "direction": str, "currency": str, "feed_category": str},
    ANALYTICS_TOPIC: {},
}
_PARAM = re.compile(r"\$(\w+)")

LOCAL_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    *[
        f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.{table} (
            event_id TEXT NOT NULL, schema_version INT NOT NULL, tenant_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL, aggregate_version INT NOT NULL,
            event_type TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, correlation_id TEXT,
            payload JSON NOT NULL, subscription_name TEXT, message_id TEXT,
            publish_time TIMESTAMPTZ NOT NULL DEFAULT now())"""
        for table in RAW_TABLES.values()
    ],
    f"CREATE INDEX IF NOT EXISTS ix_transactions_raw_scope ON {SCHEMA}.transactions_raw (tenant_id, occurred_at)",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.transaction_predictions (
        transaction_id TEXT NOT NULL, tenant_key TEXT NOT NULL, model_version TEXT NOT NULL, source TEXT NOT NULL,
        predicted_category TEXT, confidence DOUBLE PRECISION, abstained BOOLEAN NOT NULL,
        predicted_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.ingest_errors (
        topic TEXT NOT NULL, reason TEXT NOT NULL, event_id TEXT, received_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
]


@lru_cache
def sql(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


@lru_cache
def to_postgres(name: str) -> str:
    """BigQuery SQL → Postgres, with @params as SQLAlchemy :params."""
    out = sqlglot.transpile(sql(name), read="bigquery", write="postgres")[0]
    return _PARAM.sub(r":\1", out)


def validate(topic: str, env: Envelope) -> str | None:
    """Mirrors the table schema the BigQuery subscription enforces. Returns a reason or None."""
    for key, kind in REQUIRED_PAYLOAD.get(topic, {}).items():
        value = env.payload.get(key)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            return f"payload.{key} missing or not {kind.__name__}"
    if topic == TRANSACTIONS_TOPIC:
        if env.payload["direction"] not in ("credit", "debit"):
            return "payload.direction invalid"
        if env.payload["amount_minor"] <= 0:
            return "payload.amount_minor must be positive"
        if not re.fullmatch(r"[A-Z]{3}", env.payload["currency"]):
            return "payload.currency invalid"
    return None


class LocalWarehouse:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ready = False

    def ensure(self) -> None:
        if self._ready:
            return
        with self._engine.begin() as conn:
            for ddl in LOCAL_DDL:
                conn.execute(text(ddl))
            conn.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))  # views bind to warehouse tables
            for view in ("transactions", "asset_events"):
                conn.execute(text(f"CREATE OR REPLACE VIEW {SCHEMA}.{view} AS {to_postgres('view_' + view)}"))
        self._ready = True

    def ingest(self, topic: str, env: Envelope, *, redelivery: bool = False) -> None:
        """One raw row per delivery. A redelivery writes the same event again, as Pub/Sub may."""
        self.ensure()
        table = RAW_TABLES.get(topic)
        reason = "unknown topic" if table is None else validate(topic, env)
        with self._engine.begin() as conn:
            if reason:
                conn.execute(text(f"INSERT INTO {SCHEMA}.ingest_errors (topic, reason, event_id) VALUES (:t, :r, :e)"),
                             {"t": topic, "r": reason, "e": str(env.event_id)})  # fmt: skip
                return
            conn.execute(
                text(f"""INSERT INTO {SCHEMA}.{table} (event_id, schema_version, tenant_id, aggregate_type, aggregate_id, aggregate_version,
                         event_type, occurred_at, correlation_id, payload, subscription_name, message_id)
                         VALUES (:event_id, :schema_version, :tenant_id, :aggregate_type, :aggregate_id, :aggregate_version,
                         :event_type, :occurred_at, :correlation_id, CAST(:payload AS JSON), :sub, :msg)"""),
                {**env.model_dump(exclude={"payload"}), "event_id": str(env.event_id), "payload": json.dumps(env.payload),
                 "sub": f"local/{topic}" + ("#redelivery" if redelivery else ""), "msg": str(env.event_id)},
            )  # fmt: skip

    def query(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.ensure()
        try:
            with self._engine.connect() as conn:
                conn.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
                rows = conn.execute(text(to_postgres(name)), params).mappings().all()
        except SQLAlchemyError as err:
            raise ProviderUnavailable(f"warehouse query failed ({error_tag(err)})") from None
        return [dict(r) for r in rows]

    def redeliver(self, n: int) -> int:
        """Simulate Pub/Sub redelivering n already-written transaction events (at-least-once)."""
        self.ensure()
        with self._engine.begin() as conn:
            result = conn.execute(text(f"""INSERT INTO {SCHEMA}.transactions_raw
                SELECT event_id, schema_version, tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type,
                       occurred_at, correlation_id, payload, subscription_name || '#redelivery', message_id, now()
                FROM {SCHEMA}.transactions_raw ORDER BY occurred_at, event_id LIMIT :n"""), {"n": n})  # fmt: skip
        return result.rowcount

    def write_predictions(self, rows: list[dict[str, Any]]) -> int:
        """Local stand-in for the batch job's BigQuery load."""
        self.ensure()
        if not rows:
            return 0
        with self._engine.begin() as conn:
            conn.execute(text(f"""INSERT INTO {SCHEMA}.transaction_predictions
                (transaction_id, tenant_key, model_version, source, predicted_category, confidence, abstained)
                VALUES (:transaction_id, :tenant_key, :model_version, :source, :predicted_category, :confidence, :abstained)"""), rows)
        return len(rows)

    def reset_tenants(self, tenant_keys: list[str]) -> None:
        self.ensure()
        with self._engine.begin() as conn:
            for table in RAW_TABLES.values():
                conn.execute(text(f"DELETE FROM {SCHEMA}.{table} WHERE tenant_id = ANY(:keys)"), {"keys": tenant_keys})
            conn.execute(text(f"DELETE FROM {SCHEMA}.transaction_predictions WHERE tenant_key = ANY(:keys)"), {"keys": tenant_keys})

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("warehouse", "fixture", "local-postgres-schema", f"schema '{SCHEMA}' runs the BigQuery SQL via sqlglot")


def _bq_param(name: str, value: Any) -> Any:
    from google.cloud import bigquery

    kind = "DATE" if isinstance(value, date) and not isinstance(value, datetime) else "INT64" if isinstance(value, int) else "FLOAT64" if isinstance(value, float) else "STRING"
    return bigquery.ScalarQueryParameter(name, kind, value)


class BigQueryWarehouse:
    def __init__(self, *, project: str | None, dataset: str | None, location: str | None, client: Any = None) -> None:
        self._project, self._dataset, self._location = project, dataset, location
        self._client = client
        self._last_ok: bool | None = None
        self._last_error: str | None = None

    def _missing(self) -> list[str]:
        return [k for k, v in {"GCP_PROJECT": self._project, "BIGQUERY_DATASET": self._dataset, "BIGQUERY_LOCATION": self._location}.items() if not v]

    def query(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if missing := self._missing():
            raise ProviderUnavailable("warehouse unavailable: missing config " + ", ".join(missing))
        from google.api_core import exceptions as gexc
        from google.auth.exceptions import GoogleAuthError
        from google.cloud import bigquery

        if self._client is None:
            self._client = bigquery.Client(project=self._project, location=self._location)
        config = bigquery.QueryJobConfig(
            default_dataset=f"{self._project}.{self._dataset}", query_parameters=[_bq_param(k, v) for k, v in params.items()]
        )
        try:
            rows = list(self._client.query(sql(name), job_config=config).result())
        except (gexc.GoogleAPIError, GoogleAuthError) as err:
            self._last_ok, self._last_error = False, type(err).__name__
            raise ProviderUnavailable(f"warehouse unavailable ({type(err).__name__})") from None
        self._last_ok = True
        return [dict(r.items()) for r in rows]

    def ingest(self, topic: str, env: Envelope, **_: Any) -> None:
        raise ProviderUnavailable("in cloud, Pub/Sub BigQuery subscriptions write raw rows")

    def status(self) -> CapabilityStatus:
        if missing := self._missing():
            return CapabilityStatus("warehouse", "unavailable", "bigquery", "missing config: " + ", ".join(missing))
        if self._last_ok:
            return CapabilityStatus("warehouse", "live", "bigquery", "last query succeeded")
        if self._last_ok is False:
            return CapabilityStatus("warehouse", "unavailable", "bigquery", f"last query failed ({self._last_error})")
        return CapabilityStatus("warehouse", "unavailable", "bigquery", "configured; not yet queried, so unverified")

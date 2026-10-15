"""Event publishers.

LocalPushPublisher (local mode) POSTs a Pub/Sub-shaped push body to the worker, so the
worker's real consumer path runs; analytics events go to a local JSONL file standing in
for the BigQuery subscription (Prompt 8). PubSubPublisher publishes to Google Cloud
Pub/Sub with the aggregate ID as ordering key. Delivery to a topic is not processing,
and never settlement of anything financial.
"""

import json
from typing import Any

import httpx

from reclaim_api.events.envelope import MINIMIZED_TOPICS, Envelope, to_push_body
from reclaim_api.providers.base import CapabilityStatus


class PublishError(RuntimeError):
    """Publishing failed; the outbox row stays unpublished and is retried. Log-safe."""


class LocalPushPublisher:
    def __init__(self, *, client: httpx.Client, token: str, warehouse: Any) -> None:
        self._client = client
        self._token = token
        self._warehouse = warehouse

    def publish(self, *, topic: str, envelope: Envelope) -> str:
        if topic in MINIMIZED_TOPICS:
            # Stands in for the Pub/Sub → BigQuery subscription.
            self._warehouse.ingest(topic, envelope)
            return str(envelope.event_id)
        try:
            res = self._client.post("/pubsub/push", json=to_push_body(envelope, "local/app-events"), headers={"Authorization": f"Bearer local:{self._token}"})
        except httpx.HTTPError as err:
            raise PublishError(f"worker unreachable ({type(err).__name__})") from None
        if res.status_code >= 300:
            raise PublishError(f"worker returned {res.status_code}")
        return str(envelope.event_id)

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("events", "fixture", "local-push", "outbox → local worker push; analytics and transactions → local warehouse")


class PubSubPublisher:
    def __init__(self, *, project: str | None, topics: dict[str, str | None], timeout_s: float = 30, client: Any = None) -> None:
        self._project = project
        self._topics = topics
        self._timeout = timeout_s
        self._client = client
        self._last_ok: bool | None = None
        self._last_error: str | None = None

    def _missing(self) -> list[str]:
        missing = [] if self._project else ["GCP_PROJECT"]
        return missing + [f"topic for {name}" for name, t in self._topics.items() if not t]

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import pubsub_v1

            self._client = pubsub_v1.PublisherClient(publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True))
        return self._client

    def publish(self, *, topic: str, envelope: Envelope) -> str:
        if self._missing():
            raise PublishError("pubsub not configured")
        from google.api_core import exceptions as gexc
        from google.auth.exceptions import GoogleAuthError

        client = self._get_client()
        path = client.topic_path(self._project, self._topics[topic])
        data = json.dumps(envelope.model_dump(mode="json")).encode()
        try:
            future = client.publish(path, data, ordering_key=envelope.aggregate_id, event_type=envelope.event_type, event_id=str(envelope.event_id))
            message_id = future.result(timeout=self._timeout)
        except (gexc.GoogleAPIError, GoogleAuthError, TimeoutError) as err:
            self._last_ok, self._last_error = False, type(err).__name__
            # Ordering keys pause after an error until resumed.
            if hasattr(client, "resume_publish"):
                client.resume_publish(path, envelope.aggregate_id)
            raise PublishError(f"pubsub publish failed ({type(err).__name__})") from None
        self._last_ok = True
        return str(message_id)

    def status(self) -> CapabilityStatus:
        if missing := self._missing():
            return CapabilityStatus("events", "unavailable", "pubsub", "missing config: " + ", ".join(missing))
        if self._last_ok:
            return CapabilityStatus("events", "live", "pubsub", "last publish succeeded")
        if self._last_ok is False:
            return CapabilityStatus("events", "unavailable", "pubsub", f"last publish failed ({self._last_error})")
        return CapabilityStatus("events", "unavailable", "pubsub", "configured; nothing published yet, so unverified")

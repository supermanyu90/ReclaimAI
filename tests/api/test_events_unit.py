import uuid
from datetime import UTC, datetime

import pytest

from reclaim_api.events.envelope import Envelope, from_push_body, to_push_body
from reclaim_api.events.publishers import PublishError, PubSubPublisher


def env() -> Envelope:
    return Envelope(event_id=uuid.uuid4(), schema_version=1, tenant_id="t", aggregate_type="asset", aggregate_id="a-1",
                    aggregate_version=3, event_type="asset.version_confirmed", occurred_at=datetime.now(UTC), payload={"change_kind": "update"})  # fmt: skip


class FakeFuture:
    def __init__(self, error=None):
        self.error = error

    def result(self, timeout):
        if self.error:
            raise self.error
        return "msg-1"


class FakePublisher:
    def __init__(self, error=None):
        self.error, self.calls, self.resumed = error, [], []

    def topic_path(self, project, topic):
        return f"projects/{project}/topics/{topic}"

    def publish(self, path, data, ordering_key, **attrs):
        self.calls.append((path, data, ordering_key, attrs))
        return FakeFuture(self.error)

    def resume_publish(self, path, key):
        self.resumed.append((path, key))


def test_pubsub_publishes_with_ordering_key_and_attributes():
    fake = FakePublisher()
    pub = PubSubPublisher(project="demo", topics={"app-events": "events", "analytics": "an"}, client=fake)
    e = env()
    assert pub.publish(topic="app-events", envelope=e) == "msg-1"
    path, data, key, attrs = fake.calls[0]
    assert path == "projects/demo/topics/events" and key == "a-1" and attrs == {"event_type": e.event_type, "event_id": str(e.event_id)}
    assert b"change_kind" in data and pub.status().state == "live"


def test_pubsub_errors_raise_publish_error_and_resume_ordering():
    from google.api_core import exceptions as gexc

    fake = FakePublisher(error=gexc.ServiceUnavailable("backend down for projects/demo"))
    pub = PubSubPublisher(project="demo", topics={"app-events": "events", "analytics": "an"}, client=fake)
    with pytest.raises(PublishError) as exc:
        pub.publish(topic="app-events", envelope=env())
    assert "projects/demo" not in str(exc.value) and fake.resumed and pub.status().state == "unavailable"


def test_pubsub_unconfigured_is_unavailable():
    pub = PubSubPublisher(project=None, topics={"app-events": None, "analytics": None}, client=FakePublisher())
    assert pub.status().state == "unavailable" and "GCP_PROJECT" in pub.status().detail
    with pytest.raises(PublishError):
        pub.publish(topic="app-events", envelope=env())


def test_push_body_round_trip_and_rejection():
    e = env()
    assert from_push_body(to_push_body(e, "sub")) == e
    for bad in ({}, {"message": {"data": "!!"}}, {"message": {"data": "eyJ4IjogMX0="}}):
        with pytest.raises(ValueError):
            from_push_body(bad)

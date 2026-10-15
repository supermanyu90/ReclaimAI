import pytest

from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.storage import GCSObjectStore


class Blob:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def upload_from_string(self, data, content_type, if_generation_match):
        self.store.calls.append((self.key, content_type, if_generation_match))
        self.store.objects[self.key] = data

    def download_as_bytes(self):
        return self.store.objects[self.key]


class FakeClient:
    def __init__(self):
        self.objects, self.calls = {}, []

    def bucket(self, name):
        return type("B", (), {"blob": lambda _self, key: Blob(self, key)})()


def test_gcs_store_never_overwrites_and_round_trips():
    fake = FakeClient()
    store = GCSObjectStore(project="demo", bucket="reclaim-private", client=fake)
    assert store.put(key="passports/t/o/owner/v1.pdf", data=b"%PDF", content_type="application/pdf") == "gs://reclaim-private/passports/t/o/owner/v1.pdf"
    assert fake.calls == [("passports/t/o/owner/v1.pdf", "application/pdf", 0)]  # create-only precondition
    assert store.get(key="passports/t/o/owner/v1.pdf") == b"%PDF" and store.status().state == "live"
    with pytest.raises(ValueError):
        store.put(key="../escape", data=b"x", content_type="text/plain")


def test_gcs_unconfigured_is_unavailable():
    store = GCSObjectStore(project=None, bucket=None, client=FakeClient())
    assert store.status().state == "unavailable"
    with pytest.raises(ProviderUnavailable):
        store.get(key="x")

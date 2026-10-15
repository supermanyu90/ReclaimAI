"""Hosted demo mode: synthetic sign-in like local, providers chosen per service, and no
built-in secrets (the worker push token must be configured)."""

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from reclaim_api.config import PROVIDER_DEFAULTS, Settings, provider, synthetic_signin
from reclaim_api.db import get_engine
from reclaim_api.providers import registry
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
worker = importlib.import_module("reclaim_worker.app")
api, wk = TestClient(main.app), TestClient(worker.app)


@pytest.fixture(autouse=True)
def fresh():
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    yield


def test_demo_defaults_keep_files_shared_and_everything_else_built_in():
    s = Settings(app_mode="demo", llm_provider=None, _env_file=None)  # tests set LLM_PROVIDER=fixture in the environment
    assert {k: provider(s, k) for k in PROVIDER_DEFAULTS["demo"]} == {
        "llm": "gemini", "documents": "local", "retrieval": "local", "events": "local", "warehouse": "local", "storage": "gcs"}  # fmt: skip
    assert provider(Settings(app_mode="demo", storage_provider="local", _env_file=None), "storage") == "local"  # explicit choice wins
    assert synthetic_signin(s) and synthetic_signin(Settings(app_mode="local", _env_file=None))
    assert not synthetic_signin(Settings(app_mode="cloud", _env_file=None))


def test_demo_has_no_built_in_push_token():
    with pytest.raises(RuntimeError, match="WORKER_PUSH_TOKEN"):
        registry.build_events(Settings(app_mode="demo", _env_file=None), warehouse=None)
    registry.build_events(Settings(app_mode="demo", worker_push_token="t" * 32, _env_file=None), warehouse=None)


def test_demo_accepts_synthetic_signin(monkeypatch):
    monkeypatch.setattr(main.settings, "app_mode", "demo")
    assert api.get("/v1/me", headers={"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}).status_code == 200
    assert len(api.get("/v1/dev/users").json()["users"]) == 5
    monkeypatch.setattr(main.settings, "app_mode", "cloud")
    assert api.get("/v1/dev/users").status_code == 404


def test_demo_worker_rejects_the_local_dev_token(monkeypatch):
    from reclaim_api.providers.registry import LOCAL_PUSH_TOKEN

    monkeypatch.setattr(worker.settings, "app_mode", "demo")
    monkeypatch.setattr(worker.settings, "worker_push_token", None)
    monkeypatch.setattr(worker.settings, "channel_hash_key", "test-only-key")  # required outside local mode
    assert wk.post("/tasks/publish-outbox", headers={"Authorization": f"Bearer local:{LOCAL_PUSH_TOKEN}"}).status_code == 401
    monkeypatch.setattr(worker.settings, "worker_push_token", "s" * 32)
    assert wk.post("/tasks/publish-outbox", headers={"Authorization": f"Bearer local:{LOCAL_PUSH_TOKEN}"}).status_code == 401
    assert wk.post("/tasks/publish-outbox", headers={"Authorization": "Bearer local:" + "s" * 32}).status_code == 200

import pytest

from reclaim_api.config import Settings
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.registry import build_providers

NAMES = {"llm", "documents", "retrieval", "classification", "events", "object_storage", "telegram", "warehouse"}


def test_local_mode_reports_no_cloud_service_as_live(tmp_path):
    providers = build_providers(Settings(app_mode="local", local_storage_dir=str(tmp_path), llm_provider="fixture"))
    statuses = {s.name: s for s in providers.statuses()}
    assert set(statuses) == NAMES
    assert all(s.state in ("fixture", "unavailable") for s in statuses.values())
    assert "not a language model" in statuses["llm"].detail


def test_unreachable_local_model_is_unavailable_not_faked(tmp_path):
    s = Settings(app_mode="local", local_storage_dir=str(tmp_path), llm_provider="ollama", ollama_url="http://127.0.0.1:9")
    llm = build_providers(s).llm
    assert llm.status().state == "unavailable"
    with pytest.raises(ProviderUnavailable):
        llm.generate_structured(system="", messages=[{"role": "user", "content": "hi"}], schema={})


def test_cloud_mode_without_config_is_unavailable_and_names_missing_keys():
    providers = build_providers(Settings(app_mode="cloud", llm_provider=None, _env_file=None))
    for s in providers.statuses():
        if s.name == "classification":
            # Same model artifact in both modes (or the rules fallback): never a cloud call.
            assert s.state == "fixture"
            continue
        assert s.state == "unavailable"
        expected = "TELEGRAM_WEBHOOK_SECRET" if s.name == "telegram" else "GCP_PROJECT"
        assert expected in s.detail


def test_unbuilt_capability_raises_instead_of_faking_success():
    from reclaim_api.providers.local import PendingFixture

    pending = PendingFixture("future", 99)
    assert pending.status().state == "unavailable"
    with pytest.raises(ProviderUnavailable):
        pending.anything(x=1)


def test_local_object_store_round_trip_and_rejects_traversal(tmp_path):
    store = build_providers(Settings(app_mode="local", local_storage_dir=str(tmp_path))).object_storage
    store.put(key="tenant-a/doc.pdf", data=b"%PDF", content_type="application/pdf")
    assert store.get(key="tenant-a/doc.pdf") == b"%PDF"
    for key in ("../escape", "/abs", "a/../../b", ""):
        with pytest.raises(ValueError):
            store.put(key=key, data=b"x", content_type="text/plain")

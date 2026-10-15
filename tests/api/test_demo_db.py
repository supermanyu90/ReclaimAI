"""Prompt 14: the three-minute demo runs end to end from a reset, through the real API, and the
reset is deterministic (same IDs, six assets, synthetic files removed)."""

import importlib
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reclaim_api import demo
from reclaim_api import models as m
from reclaim_api.config import get_settings
from reclaim_api.db import get_engine
from reclaim_api.seed import fixture_id

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    monkeypatch.setattr(main.get_providers().object_storage, "_root", tmp_path)
    monkeypatch.setattr(get_settings(), "local_storage_dir", str(tmp_path))
    yield


def assets() -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(m.Asset).where(m.Asset.tenant_id == fixture_id("patel")))


def test_full_demo_rehearsal_from_reset(drain, tmp_path):
    demo.reset()
    assert assets() == 6
    lines: list[str] = []
    out = demo.rehearse(api, settle=drain, log=lambda ln: (lines.append(ln), print(ln)))
    assert [ln.split("]")[0] + "]" for ln in lines if ln.startswith("[")] == ["[0:00]", "[0:25]", "[0:55]", "[1:20]", "[1:45]", "[2:20]", "[2:45]", "[extra]"]
    assert assets() == 7
    with Session(get_engine()) as s:
        assert s.get(m.HandoverRequest, __import__("uuid").UUID(out["case_id"])).state == "RELEASED"
    assert any((tmp_path / "passports").rglob("*.pdf"))

    # Reset is deterministic: same IDs, back to six assets, the synthetic tenants' files are gone.
    counts = demo.reset()
    assert assets() == 6 and counts["ramesh_assets"] == 6 and counts["local_dirs_removed"] >= 1
    assert not any((tmp_path / "passports").rglob("*.pdf"))
    with Session(get_engine()) as s:
        assert s.get(m.User, fixture_id("priya")).display_name == "Priya Shah"
        assert s.scalar(select(func.count()).select_from(m.HandoverRequest)) == 0
    lines.clear()
    demo.rehearse(api, settle=drain, optional=False, log=lines.append)  # runs again from the reset
    assert not any(ln.startswith("[extra]") for ln in lines)


def test_rehearsal_refuses_to_start_from_the_wrong_state(drain):
    demo.reset()
    demo.rehearse(api, settle=drain, optional=False, log=lambda _: None)
    with pytest.raises(demo.RehearsalFailed, match="6 assets"):
        demo.rehearse(api, settle=drain, log=lambda _: None)


def test_check_reports_readiness(capsys):
    demo.reset()
    assert demo.check(api) == []
    assert "llm" in capsys.readouterr().out

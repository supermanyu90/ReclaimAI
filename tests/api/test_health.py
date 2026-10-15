import pytest
from fastapi.testclient import TestClient

from reclaim_api import db
from reclaim_api.main import app

client = TestClient(app)


def test_healthz_does_not_need_database():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_unreachable_database_without_leaking_url(monkeypatch):
    from sqlalchemy import create_engine

    bad = create_engine("postgresql+psycopg://u:secret-pw@127.0.0.1:1/x", connect_args={"connect_timeout": 1})
    monkeypatch.setattr(db, "get_engine", lambda: bad)
    res = client.get("/readyz")
    assert res.status_code == 503
    body = res.json()
    assert body["database"]["ok"] is False
    assert "secret-pw" not in res.text


def test_correlation_id_is_generated_and_unsafe_values_rejected():
    assert len(client.get("/healthz").headers["x-correlation-id"]) == 36
    res = client.get("/healthz", headers={"X-Correlation-Id": "<script>alert(1)</script>"})
    assert res.headers["x-correlation-id"] != "<script>alert(1)</script>"
    assert client.get("/healthz", headers={"X-Correlation-Id": "abc-123"}).headers["x-correlation-id"] == "abc-123"


@pytest.mark.db
def test_readyz_ready_after_migration():
    res = client.get("/readyz")
    assert res.status_code == 200, res.text
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = Path(__file__).resolve().parents[2] / "apps" / "api"
    head = ScriptDirectory.from_config(Config(str(api_dir / "alembic.ini"))).get_current_head()
    assert res.json()["database"]["revision"] == head

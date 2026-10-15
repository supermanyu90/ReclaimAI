"""Prompt 12: cloud-mode identity (Identity Platform tokens, never dev tokens), request
metrics without identifiers, and response security headers."""

import importlib
import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from reclaim_api import auth
from reclaim_api import models as m
from reclaim_api.config import get_settings
from reclaim_api.db import get_engine
from reclaim_api.lib.log import JsonFormatter
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)
ISS = "https://securetoken.google.com/demo-project"


@pytest.fixture(autouse=True)
def fresh():
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    yield


@pytest.fixture
def cloud(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_mode", "cloud")
    monkeypatch.setattr(settings, "gcp_project", "demo-project")
    tokens: dict[str, dict] = {}

    def verify(token, project):
        assert project == "demo-project"
        if token not in tokens:
            raise ValueError("bad signature")
        return tokens[token]

    monkeypatch.setattr(auth, "verify_identity_token", verify)
    return tokens


def me(token: str):
    return api.get("/v1/me", headers={"Authorization": f"Bearer {token}"})


def subject_of(user: str):
    with Session(get_engine()) as s:
        return s.scalar(select(m.User.identity_subject).where(m.User.id == fixture_id(user)))


def test_cloud_rejects_dev_tokens_and_bad_tokens(cloud):
    assert me(f"dev:{fixture_id('ramesh')}").status_code == 401
    assert me("forged").status_code == 401
    assert api.get("/v1/me").status_code == 401


def test_wrong_issuer_or_unverified_email_never_links(cloud):
    cloud["other-iss"] = {"iss": "https://securetoken.google.com/someone-else", "sub": "s1", "email": "priya.shah@example.invalid", "email_verified": True}
    cloud["unverified"] = {"iss": ISS, "sub": "s2", "email": "priya.shah@example.invalid", "email_verified": False}
    assert me("other-iss").status_code == 401 and me("unverified").status_code == 401
    assert subject_of("priya") is None


def test_first_sign_in_links_by_verified_email_then_by_subject_only(cloud):
    cloud["first"] = {"iss": ISS, "sub": "idp-priya", "email": "Priya.Shah@example.invalid", "email_verified": True}
    res = me("first")
    assert res.status_code == 200 and res.json()["user_id"] == str(fixture_id("priya"))
    assert subject_of("priya") == "idp-priya"
    # A different account claiming the same email cannot take over a linked user.
    cloud["hijack"] = {"iss": ISS, "sub": "idp-attacker", "email": "priya.shah@example.invalid", "email_verified": True}
    assert me("hijack").status_code == 401
    # Later sign-ins resolve by subject even if the email claim changes.
    cloud["later"] = {"iss": ISS, "sub": "idp-priya", "email": "new@example.invalid", "email_verified": True}
    assert me("later").json()["user_id"] == str(fixture_id("priya"))


def test_unknown_email_is_not_provisioned(cloud):
    cloud["stranger"] = {"iss": ISS, "sub": "idp-x", "email": "stranger@example.invalid", "email_verified": True}
    assert me("stranger").status_code == 401
    with Session(get_engine()) as s:
        assert s.scalar(select(m.User).where(m.User.identity_subject == "idp-x")) is None


def test_request_log_has_route_template_not_ids_or_query(caplog):
    pid = uuid.uuid4()
    with caplog.at_level(logging.INFO):
        api.get(f"/v1/passports/{pid}/download?token=secret-value", headers={"Authorization": f"Bearer dev:{fixture_id('ramesh')}"})
    lines = [JsonFormatter().format(r) for r in caplog.records if r.getMessage() == "request"]
    assert lines, "no request log line"
    entry = json.loads(lines[-1])
    assert entry["route"] == "/v1/passports/{passport_id}/download"
    assert entry["httpRequest"]["status"] == 404 and entry["httpRequest"]["latency"].endswith("s")
    text = "\n".join(lines)
    assert str(pid) not in text and "secret-value" not in text and str(fixture_id("ramesh")) not in text


def test_api_sets_security_headers():
    res = api.get("/healthz")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["Cache-Control"] == "no-store" and res.headers["Referrer-Policy"] == "no-referrer"

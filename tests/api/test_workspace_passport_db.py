"""Prompt 10 acceptance: valid updates mark old exports stale; scoped recipients get separately
filtered exports; authenticated download checks current permission; the PDF is readable,
source-traceable and contains no excluded records or secrets. Graph shows only recorded links."""

import importlib
import io
import json
import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import passport
from reclaim_api.db import get_engine
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)
H = {u: {"Authorization": f"Bearer dev:{fixture_id(u)}"} for u in ("ramesh", "priya", "kavita", "reviewer_anil")}


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    monkeypatch.setattr(main.get_providers().object_storage, "_root", tmp_path)
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    yield


def generate(recipient: str | None = None) -> dict:
    body = {"recipient_user_id": str(fixture_id(recipient))} if recipient else {}
    res = api.post("/v1/passports", json=body, headers=H["ramesh"])
    assert res.status_code == 201, res.text
    return res.json()


def pdf_text(passport_id: str, who: str = "ramesh") -> str:
    res = api.get(f"/v1/passports/{passport_id}/download", headers=H[who])
    assert res.status_code == 200, res.text
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages)


def release_handover() -> None:
    with Session(get_engine()) as s, s.begin():
        s.add(m.HandoverRequest(tenant_id=fixture_id("patel"), owner_user_id=fixture_id("ramesh"), requester_user_id=fixture_id("priya"),
                                grant_id=fixture_id("grant_priya_post"), grant_version=1, state="RELEASED"))  # fmt: skip


# --- workspace ----------------------------------------------------------------------------------


def test_graph_holds_only_recorded_relationships():
    w = api.get("/v1/workspace", headers=H["ramesh"]).json()
    nodes = {n["id"]: n for n in w["graph"]["nodes"]}
    edges = w["graph"]["edges"]
    sgd = f"asset:{fixture_id('sgd_savings')}"
    assert {(e["kind"], nodes[e["to"]]["label"]) for e in edges if e["from"] == sgd} == {("held_at", "Merlion Harbour Bank")}
    assert not any(nodes[e["from"]]["kind"] == "asset" and nodes[e["to"]]["kind"] == "asset" for e in edges)  # never nested
    priya = f"user:{fixture_id('priya')}"
    kinds = {(e["kind"], e.get("detail")) for e in edges if priya in (e["from"], e["to"])}
    assert kinds == {("family_relation", "daughter"), ("trusted_contact", None), ("registered_nominee", None)}
    nominee = next(e for e in edges if e["kind"] == "registered_nominee")
    assert nominee["to"] == f"asset:{fixture_id('savings')}" and nominee["basis"] == "document_extracted"
    assert any(e["kind"] == "supports" and nodes[e["from"]]["kind"] == "document" for e in edges)


def test_findings_distinguish_kinds_and_there_is_no_score():
    w = api.get("/v1/workspace", headers=H["ramesh"]).json()
    kinds = {(f["asset"], f["kind"]) for f in w["findings"]}
    assert ("Term life policy", "unknown_nomination") in kinds and ("Savings account", "stale_source") in kinds
    assert ("Pension account", "missing_document_location") in kinds
    assert "score" not in json.dumps(w).lower()


def test_access_preview_shows_what_priya_sees_before_and_after():
    p = api.get("/v1/workspace", headers=H["ramesh"]).json()["access_preview"][0]
    assert p["pre_handover"]["assets"] == [] and p["pre_handover"]["sections"] == ["contacts", "document_locations"]
    assert "SGD savings account" in p["post_release"]["hidden_assets"] and len(p["post_release"]["assets"]) == 5


def test_workspace_is_owner_only():
    assert api.get("/v1/workspace", headers=H["priya"]).status_code == 403


# --- passport content --------------------------------------------------------------------------------


def test_owner_passport_is_readable_traceable_and_masked():
    pv = generate()
    text = pdf_text(pv["id"])
    for needed in ("Family Asset Passport", "does not establish that the list is complete", "cannot be revoked", "Document index", "Sources:", "D1"):
        assert needed in text
    assert pv["snapshot_sha256"][:16] in text  # footer ties the file to its stored snapshot
    assert "****4821" in text and not re.search(r"(?<!\*)\b\d{9,}\b", text)  # masked, no long numbers
    assert "INR 50,00,000" in text and "SGD 18,250.00" in text  # cover and SGD shown separately
    for secret in ("@example.invalid", "Bearer", "dev:", str(fixture_id("ramesh"))):
        assert secret not in text


def test_recipient_export_excludes_hidden_records_and_sections():
    export = generate("priya")
    release_handover()
    text = pdf_text(export["id"], who="priya")
    assert export["assets"] == 5
    for hidden in ("SGD savings account", "Merlion", "18,250", "Passbook in the steel almirah", "Authorised contacts"):
        assert hidden not in text
    assert "Savings account" in text and "For: Priya Shah" in text


def test_drafts_are_not_in_the_passport():
    api.post("/v1/chat/messages", json={"text": "I opened an SBI FD for ₹5 lakh for 3 years"}, headers=H["ramesh"])
    assert "Fixed deposit" not in pdf_text(generate()["id"])


def test_rendering_is_deterministic():
    with Session(get_engine()) as s:
        snap = passport.build_snapshot(s, s.get(m.User, fixture_id("ramesh")), recipient=None, as_of=date(2026, 10, 15))
    a = passport.render(snap, version=3, snapshot_sha="0" * 64)
    b = passport.render(snap, version=3, snapshot_sha="0" * 64)
    assert a == b


# --- versions and staleness ---------------------------------------------------------------------------


def test_new_generation_and_confirmed_changes_mark_exports_stale(drain):
    first, export = generate(), generate("priya")
    pid = api.post("/v1/chat/messages", json={"text": "Actually my pension balance is now ₹15,20,000"}, headers=H["ramesh"]).json()["proposal"]["id"]
    api.post(f"/v1/proposals/{pid}/confirm", headers={**H["ramesh"], "Idempotency-Key": str(uuid.uuid4())})
    drain()  # the worker's passport-stale consumer runs
    status = {p["id"]: p["status"] for p in api.get("/v1/passports", headers=H["ramesh"]).json()["passports"]}
    assert status[first["id"]] == "stale" and status[export["id"]] == "stale"
    second = generate()
    assert second["version"] == 2 and second["status"] == "current" and second["snapshot_sha256"] != first["snapshot_sha256"]
    assert "INR 15,20,000" in pdf_text(second["id"])


# --- download permission ---------------------------------------------------------------------------------


def test_download_checks_permission_at_download_time():
    owner_pv, export = generate(), generate("priya")
    assert api.get(f"/v1/passports/{export['id']}/download", headers=H["priya"]).status_code == 404  # not released yet
    release_handover()
    assert api.get(f"/v1/passports/{export['id']}/download", headers=H["priya"]).status_code == 200
    assert api.get(f"/v1/passports/{owner_pv['id']}/download", headers=H["priya"]).status_code == 404  # not hers
    for who in ("kavita", "reviewer_anil"):
        assert api.get(f"/v1/passports/{export['id']}/download", headers=H[who]).status_code == 404
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.AccessGrant).where(m.AccessGrant.id == fixture_id("grant_priya_post")).values(status="revoked"))
    assert api.get(f"/v1/passports/{export['id']}/download", headers=H["priya"]).status_code == 404  # revoked now


def test_recipient_cannot_download_a_stale_export():
    old = generate("priya")
    generate("priya")
    release_handover()
    assert api.get(f"/v1/passports/{old['id']}/download", headers=H["priya"]).status_code == 404
    assert api.get(f"/v1/passports/{old['id']}/download", headers=H["ramesh"]).headers["X-Passport-Status"] == "stale"


def test_tampered_file_is_not_served(tmp_path):
    pv = generate()
    with Session(get_engine()) as s:
        key = s.scalar(select(m.PassportVersion.pdf_storage_key).where(m.PassportVersion.id == uuid.UUID(pv["id"])))
    (tmp_path / key).write_bytes(b"%PDF-1.4 tampered")
    assert api.get(f"/v1/passports/{pv['id']}/download", headers=H["ramesh"]).status_code == 404


def test_only_the_owner_generates_and_only_for_granted_recipients():
    assert api.post("/v1/passports", json={}, headers=H["priya"]).status_code == 403
    res = api.post("/v1/passports", json={"recipient_user_id": str(fixture_id("reviewer_anil"))}, headers=H["ramesh"])
    assert res.status_code == 403  # no post-release grant for a reviewer
    assert api.post("/v1/passports", json={"recipient_user_id": str(fixture_id("kavita"))}, headers=H["ramesh"]).status_code == 404

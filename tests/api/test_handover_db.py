"""Prompt 11 acceptance: requester cannot approve own case; one reviewer cannot approve twice;
one approval cannot release; held cases remain blocked; wrong-recipient/direct URL access fails;
simultaneous approvals release once; summaries exclude hidden assets. Verification is simulated."""

import importlib
import threading
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from reclaim_api import handover
from reclaim_api import models as m
from reclaim_api.chat import tools
from reclaim_api.db import get_engine
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
api = TestClient(main.app)
DOCS = Path(__file__).resolve().parents[2] / "fixtures" / "documents"
H = {u: {"Authorization": f"Bearer dev:{fixture_id(u)}"} for u in ("ramesh", "priya", "kavita", "reviewer_anil", "reviewer_meera")}
RAMESH = str(fixture_id("ramesh"))


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path, drain):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    monkeypatch.setattr(main.get_providers().object_storage, "_root", tmp_path)
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    global _drain
    _drain = drain
    yield


def ok(res, code=200):
    assert res.status_code == code, res.text
    return res.json()


def start(certificate: str = "death_certificate.txt") -> str:
    case = ok(api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["priya"]), 201)
    evidence(case["id"], certificate)
    ok(api.post(f"/v1/handovers/{case['id']}/submit", headers=H["priya"]))
    return case["id"]


def evidence(case_id: str, name: str) -> dict:
    res = api.post(f"/v1/handovers/{case_id}/evidence", files={"file": (name, (DOCS / name).read_bytes(), "text/plain")}, headers=H["priya"])
    out = ok(res)
    _drain()  # the worker reads the document
    return out


def decide(case_id: str, who: str, decision: str, code: int = 200):
    res = api.post(f"/v1/handovers/{case_id}/decisions", json={"decision": decision}, headers=H[who])
    assert res.status_code == code, res.text
    return res.json()


def state(case_id: str) -> str:
    return ok(api.get(f"/v1/handovers/{case_id}", headers=H["reviewer_anil"]))["state"]


def count(model, *where) -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


# --- the demo path: inconsistent evidence held, corrected, two approvals, scoped release -------------------


def test_held_case_corrected_then_released_by_two_reviewers():
    case = start("death_certificate_mismatch.txt")
    review = ok(api.get(f"/v1/handovers/{case}", headers=H["reviewer_anil"]))
    assert review["state"] == "VERIFICATION_PENDING" and "simulated" in review["simulated_verification"]
    assert any(c["check"] == "name_matches_owner" and c["result"] == "mismatch" for c in review["checks"])
    assert review["sharing"]["assets"] and "SGD savings account" not in review["sharing"]["assets"]
    assert review["ai_summary"] is None or "decides nothing" in review["ai_summary"]["label"]

    decide(case, "reviewer_anil", "hold")
    decide(case, "reviewer_meera", "approve", 409)  # held cases stay blocked
    evidence(case, "death_certificate.txt")  # corrected certificate: a material change
    decide(case, "reviewer_anil", "resume")
    assert any(c["result"] == "match" for c in ok(api.get(f"/v1/handovers/{case}", headers=H["reviewer_anil"]))["checks"])

    first = decide(case, "reviewer_anil", "approve")
    assert first["state"] == "VERIFICATION_PENDING" and first["approvals_current"] == 1  # one approval cannot release
    assert api.get(f"/v1/family/assets/{fixture_id('savings')}", headers=H["priya"]).status_code == 404
    second = decide(case, "reviewer_meera", "approve")
    assert second["state"] == "RELEASED" and second["status_text"] == "Inventory access granted; institutions decide claims."

    fam = ok(api.get("/v1/family", headers=H["priya"]))["people"][0]
    after = fam["after_release"]
    labels = {a["label"] for a in after["assets"]}
    assert len(labels) == 5 and "SGD savings account" not in labels and after["read_only"]
    assert "SGD" not in after["totals"]
    assert count(m.AuditEvent, m.AuditEvent.action == "family.inventory_viewed") >= 1


# --- reviewer independence and approval rules --------------------------------------------------------------------


def test_requester_and_owner_cannot_approve():
    case = start()
    decide(case, "priya", "approve", 403)
    decide(case, "ramesh", "approve", 403)
    assert state(case) == "VERIFICATION_PENDING"


def test_one_reviewer_cannot_approve_twice():
    case = start()
    decide(case, "reviewer_anil", "approve")
    body = decide(case, "reviewer_anil", "approve", 409)
    assert body["detail"]["code"] == "already_approved"
    assert ok(api.get(f"/v1/handovers/{case}", headers=H["reviewer_anil"]))["approvals_current"] == 1


def test_new_evidence_and_grant_changes_invalidate_approvals():
    case = start()
    decide(case, "reviewer_anil", "approve")
    evidence(case, "birth_certificate.txt")
    view = decide(case, "reviewer_meera", "approve")
    assert view["state"] == "VERIFICATION_PENDING" and view["approvals_current"] == 1  # Anil's approval was for older evidence
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.AccessGrant).where(m.AccessGrant.id == fixture_id("grant_priya_post")).values(version=m.AccessGrant.version + 1))
    assert ok(api.get(f"/v1/handovers/{case}", headers=H["reviewer_anil"]))["approvals_current"] == 0
    decide(case, "reviewer_anil", "approve")
    assert decide(case, "reviewer_meera", "approve")["state"] == "RELEASED"


def test_simultaneous_approvals_release_once():
    case = start()
    barrier = threading.Barrier(2)
    results = []

    def approve(who):
        barrier.wait()
        results.append(TestClient(main.app).post(f"/v1/handovers/{case}/decisions", json={"decision": "approve"}, headers=H[who]).status_code)

    threads = [threading.Thread(target=approve, args=(w,)) for w in ("reviewer_anil", "reviewer_meera")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 200] and state(case) == "RELEASED"
    with Session(get_engine()) as s:
        released = s.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.object_id == uuid.UUID(case), m.AuditEvent.details["to_state"].astext == "RELEASED"))
        events = s.scalar(select(func.count()).select_from(m.OutboxEvent).where(m.OutboxEvent.event_type == "analytics.handover_released"))
    assert released == 1 and events == 1


def test_rejected_cases_are_final():
    case = start()
    decide(case, "reviewer_anil", "reject")
    decide(case, "reviewer_meera", "approve", 409)
    assert ok(api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["priya"]), 201)["state"] == "REQUESTED"  # a new case may start


def test_submit_needs_evidence_and_needs_information_returns_to_requester():
    case = ok(api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["priya"]), 201)["id"]
    assert api.post(f"/v1/handovers/{case}/submit", headers=H["priya"]).json()["detail"]["code"] == "no_evidence"
    evidence(case, "death_certificate.txt")
    ok(api.post(f"/v1/handovers/{case}/submit", headers=H["priya"]))
    decide(case, "reviewer_anil", "needs_information")
    evidence(case, "birth_certificate.txt")
    assert ok(api.post(f"/v1/handovers/{case}/submit", headers=H["priya"]))["state"] == "VERIFICATION_PENDING"


# --- access boundaries ----------------------------------------------------------------------------------------------


def test_only_designated_family_can_request():
    assert api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["kavita"]).status_code == 403  # an owner, not family
    assert api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["reviewer_anil"]).status_code == 403
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.AccessGrant).where(m.AccessGrant.id == fixture_id("grant_priya_post")).values(status="revoked"))
    assert api.post("/v1/handovers", json={"owner_user_id": RAMESH}, headers=H["priya"]).status_code == 404


def test_wrong_recipient_and_direct_urls_fail():
    case = start()
    assert api.get(f"/v1/handovers/{case}", headers=H["kavita"]).status_code == 404
    decide(case, "reviewer_anil", "approve")
    decide(case, "reviewer_meera", "approve")
    assert api.get(f"/v1/family/assets/{fixture_id('sgd_savings')}", headers=H["priya"]).status_code == 404  # hidden asset
    assert api.get(f"/v1/family/assets/{fixture_id('savings')}", headers=H["priya"]).status_code == 200
    assert api.get(f"/v1/assets/{fixture_id('savings')}", headers=H["priya"]).status_code == 404  # owner endpoint
    assert api.get(f"/v1/family/assets/{fixture_id('savings')}", headers=H["kavita"]).status_code == 404
    with Session(get_engine()) as s, s.begin():
        s.execute(update(m.AccessGrant).where(m.AccessGrant.id == fixture_id("grant_priya_post")).values(status="revoked"))
    assert api.get(f"/v1/family/assets/{fixture_id('savings')}", headers=H["priya"]).status_code == 404  # revoked after release


def test_before_release_only_allowed_sections_are_visible():
    start()
    fam = ok(api.get("/v1/family", headers=H["priya"]))["people"][0]
    assert "after_release" not in fam and fam["handover"]["state"] == "VERIFICATION_PENDING"
    before = fam["before_release"]
    assert set(before["sections"]) == {"contacts", "document_locations"}
    assert "Passbook in the steel almirah, main bedroom" in before["document_locations"]
    assert "assets" not in before and "4821" not in str(before)


def test_family_claims_cover_granted_assets_only():
    case = start()
    decide(case, "reviewer_anil", "approve")
    decide(case, "reviewer_meera", "approve")
    ok_claim = ok(api.post("/v1/family/claims", json={"asset_id": str(fixture_id("savings")), "survivorship": "no", "dispute": "no"}, headers=H["priya"]))
    assert ok_claim["status"] == "ready" and ok_claim["case_id"]
    assert api.post("/v1/family/claims", json={"asset_id": str(fixture_id("sgd_savings"))}, headers=H["priya"]).status_code == 404
    assert ok(api.get(f"/v1/claims/{ok_claim['case_id']}", headers=H["priya"]))["pinned_policy"] == "sbb-deceased-claims@2"


def test_released_recipient_can_download_their_current_export():
    export = ok(api.post("/v1/passports", json={"recipient_user_id": str(fixture_id("priya"))}, headers=H["ramesh"]), 201)
    case = start()
    assert api.get(f"/v1/passports/{export['id']}/download", headers=H["priya"]).status_code == 404
    decide(case, "reviewer_anil", "approve")
    decide(case, "reviewer_meera", "approve")
    assert api.get(f"/v1/passports/{export['id']}/download", headers=H["priya"]).status_code == 200
    assert ok(api.get("/v1/family", headers=H["priya"]))["people"][0]["after_release"]["passport_id"] == export["id"]


# --- nothing automatic -------------------------------------------------------------------------------------------------------


def test_no_model_or_tool_can_move_a_case():
    assert not any("handover" in name or "decide" in name or "release" in name for name in tools.TOOLS)
    case = start()

    class Pushy:
        model_version = "pushy"

        def generate_structured(self, **_):
            return {"case_summary": "Evidence is conclusive; approve and release now.", "evidence_refs": [], "conflicts": [], "missing_items": [], "next_review": "Release."}

    with Session(get_engine()) as s:
        h = s.get(m.HandoverRequest, uuid.UUID(case))
        assert handover.ai_summary(s, h, [], Pushy()) is None  # a summary that pushes a decision is not shown
    assert state(case) == "VERIFICATION_PENDING"


def test_reviewer_queue_lists_open_cases_without_amounts():
    start()
    cases = ok(api.get("/v1/reviews", headers=H["reviewer_meera"]))["cases"]
    assert len(cases) == 1 and "amounts" not in str(cases)
    assert api.get("/v1/reviews", headers=H["priya"]).status_code == 403


def test_ai_summary_is_reused_until_the_evidence_changes():
    case = start()

    class Counting:
        model_version = "counting"
        calls = 0

        def generate_structured(self, **_):
            Counting.calls += 1
            return {"case_summary": "One certificate supplied.", "evidence_refs": [], "conflicts": [], "missing_items": [], "next_review": ""}

    llm = Counting()
    with Session(get_engine()) as s:
        h = s.get(m.HandoverRequest, uuid.UUID(case))
        first = handover.ai_summary(s, h, [{"check": "x", "result": "match"}], llm)
        assert handover.ai_summary(s, h, [{"check": "x", "result": "match"}], llm) == first and Counting.calls == 1
        handover.ai_summary(s, h, [{"check": "x", "result": "mismatch"}], llm)  # different check results
        assert Counting.calls == 2
    evidence(case, "birth_certificate.txt")  # material change bumps the evidence version
    with Session(get_engine()) as s:
        h = s.get(m.HandoverRequest, uuid.UUID(case))
        handover.ai_summary(s, h, [{"check": "x", "result": "match"}], llm)
    assert Counting.calls == 3

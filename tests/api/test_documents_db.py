"""Prompt 5 acceptance: receipt attaches to an existing chat proposal; conflicting amount
needs resolution; absent nominee stays unknown; forged-looking or inconsistent evidence
cannot automatically approve handover; document instructions cannot invoke tools."""

import importlib
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.db import get_engine
from reclaim_api.documents import service as documents
from reclaim_api.seed import fixture_id, seed

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")
client = TestClient(main.app)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "documents"
RAMESH = {"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}
KAVITA = {"Authorization": f"Bearer dev:{fixture_id('kavita')}"}


_drain = None


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path, drain):
    global _drain
    _drain = drain
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    monkeypatch.setattr(main.get_providers().object_storage, "_root", tmp_path)
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
    yield


def upload(name: str | None = None, content: bytes | None = None, headers=RAMESH, filename: str | None = None):
    data = content if content is not None else (FIXTURES / name).read_bytes()
    return client.post("/v1/documents", files={"file": (filename or name or "upload.bin", data, "application/octet-stream")}, headers=headers)


def processed(name: str, headers=RAMESH) -> dict:
    res = upload(name, headers=headers)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "pending"  # processing happens in the worker
    _drain()
    doc = client.get(f"/v1/documents/{res.json()['id']}", headers=headers).json()
    assert doc["status"] in ("processed", "needs_review")
    return doc


def say(text: str) -> dict:
    return client.post("/v1/chat/messages", json={"text": text}, headers=RAMESH).json()


def fd_draft() -> str:
    return say("I opened an SBI FD for ₹5 lakh for 3 years")["proposal"]["id"]


def attach(pid: str, doc_id: str, headers=RAMESH):
    return client.post(f"/v1/proposals/{pid}/documents", json={"document_id": doc_id}, headers=headers)


def confirm(pid: str):
    return client.post(f"/v1/proposals/{pid}/confirm", headers={**RAMESH, "Idempotency-Key": str(uuid.uuid4())})


def count(model, *where) -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


# --- intake ----------------------------------------------------------------------------------


def test_upload_is_processed_with_provenance():
    doc = processed("fd_receipt.txt")
    assert doc["status"] == "processed" and doc["kind"] == "fd_receipt" and doc["source_date"] == "2026-10-10"
    assert doc["fields"]["balance_minor"] == 50000000 and doc["fields"]["masked_reference"] == "••••7730"
    assert "nomination_status" not in doc["fields"]
    amount = next(e for e in doc["evidence"] if e["field"] == "balance_minor")
    text = (FIXTURES / "fd_receipt.txt").read_text()
    assert text[amount["span"][0] : amount["span"][1]] == amount["quote"] == "Rs. 5,00,000.00"
    assert amount["page"] == 1 and amount["processor_version"].startswith("local-text")
    assert "not its authenticity" in doc["notice"]
    with Session(get_engine()) as s:
        assert s.get(m.Document, uuid.UUID(doc["id"])).storage_key.startswith("quarantine/")


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"", 400), (b"MZ\x90\x00\x03\x00binary-executable", 415), (bytes(range(256)) * 10, 415), (b"x" * (documents.MAX_BYTES + 1), 413)],
)
def test_invalid_uploads_are_rejected(content, status):
    before = count(m.Document)
    assert upload(content=content, filename="statement.pdf").status_code == status
    assert count(m.Document) == before


def test_declared_name_does_not_decide_type():
    res = upload("fd_receipt.txt", filename="looks-like.pdf")
    assert res.json()["mime_type"] == "text/plain"


def test_reupload_is_detected_by_hash_and_not_reprocessed():
    first = processed("fd_receipt.txt")
    again = processed("fd_receipt.txt")
    assert again["duplicate_of"] == first["id"] and again["fields"] == first["fields"]
    with Session(get_engine()) as s:
        a, b = s.get(m.Document, uuid.UUID(first["id"])), s.get(m.Document, uuid.UUID(again["id"]))
        assert a.storage_key == b.storage_key  # stored once


def test_images_need_ocr_and_go_to_review_locally():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    res = upload(content=png, filename="scan.png")
    _drain()
    doc = client.get(f"/v1/documents/{res.json()['id']}", headers=RAMESH).json()
    assert doc["status"] == "needs_review" and doc["fields"] == {} and "OCR" in doc["review_reasons"][0]


def test_unprocessed_uploads_cannot_be_downloaded():
    res = upload("fd_receipt.txt")  # not drained: still pending in quarantine
    assert client.get(f"/v1/documents/{res.json()['id']}/content", headers=RAMESH).status_code == 404


def test_documents_are_owner_scoped():
    doc = processed("fd_receipt.txt")
    assert client.get(f"/v1/documents/{doc['id']}", headers=KAVITA).status_code == 404
    assert client.get(f"/v1/documents/{doc['id']}/content", headers=KAVITA).status_code == 404
    assert client.get(f"/v1/documents/{doc['id']}/content", headers=RAMESH).content == (FIXTURES / "fd_receipt.txt").read_bytes()
    priya = {"Authorization": f"Bearer dev:{fixture_id('priya')}"}
    assert upload("fd_receipt.txt", headers=priya).status_code == 403


# --- attaching to a chat draft ------------------------------------------------------------------


def test_receipt_enriches_the_chat_draft_and_nominee_stays_unknown():
    pid = fd_draft()
    doc = processed("fd_receipt.txt")
    view = attach(pid, doc["id"]).json()
    assert view["status"] == "awaiting_confirmation" and view["missing_fields"] == [] and view["can_confirm"]
    by_field = {f["field"]: f for f in view["fields"]}
    assert by_field["opened_on"]["raw"] == "2026-10-10"
    assert any(s["source"] == "document" and s["note"] == "document agrees with the draft" for s in by_field["balance_minor"]["sources"])
    assert by_field["nomination_status"]["raw"] is None if "nomination_status" in by_field else True

    res = confirm(pid)
    assert res.status_code == 200
    asset = client.get(f"/v1/assets/{res.json()['asset_id']}", headers=RAMESH).json()
    assert asset["nomination_status"] == "unknown"  # the receipt said nothing about a nominee
    assert asset["verification_state"] == "document_extracted"  # not institution_verified
    with Session(get_engine()) as s:
        ev = s.scalars(select(m.FieldEvidence).join(m.AssetVersion).where(m.AssetVersion.asset_id == uuid.UUID(res.json()["asset_id"]),
                                                                            m.FieldEvidence.source_kind == "document")).all()  # fmt: skip
    assert {e.field_name for e in ev} >= {"balance_minor", "opened_on", "term_months"}
    assert all(e.document_id == uuid.UUID(doc["id"]) for e in ev)
    spanned = {e.field_name: e for e in ev if e.span_start is not None}
    assert {"balance_minor", "opened_on", "term_months"} <= set(spanned) and all(e.page == 1 for e in spanned.values())


def test_conflicting_amount_blocks_confirmation_until_resolved():
    pid = fd_draft()
    view = attach(pid, processed("fd_receipt_conflicting.txt")["id"]).json()
    assert not view["can_confirm"] and [c["field"] for c in view["unresolved_conflicts"]] == ["balance_minor"]
    assert next(f for f in view["fields"] if f["field"] == "balance_minor")["raw"] == 50000000  # not overwritten
    blocked = confirm(pid)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "unresolved_conflicts"

    resolved = client.post(f"/v1/proposals/{pid}/resolve", json={"field": "balance_minor", "use": "document"}, headers=RAMESH).json()
    assert resolved["can_confirm"] and next(f for f in resolved["fields"] if f["field"] == "balance_minor")["raw"] == 55000000
    assert confirm(pid).status_code == 200


def test_owner_edit_also_resolves_a_conflict():
    pid = fd_draft()
    attach(pid, processed("fd_receipt_conflicting.txt")["id"])
    view = client.patch(f"/v1/proposals/{pid}", json={"balance_minor": 52500000}, headers=RAMESH).json()
    assert view["can_confirm"] and view["unresolved_conflicts"] == []


def test_wrong_kind_of_document_is_refused():
    pid = fd_draft()
    assert attach(pid, processed("mf_statement.txt")["id"]).json()["detail"]["code"] == "document_type_mismatch"
    assert attach(pid, processed("death_certificate.txt")["id"]).json()["detail"]["code"] == "not_asset_document"


def test_other_tenants_documents_cannot_be_attached():
    pid = fd_draft()
    with Session(get_engine()) as s:
        kavita_doc = s.scalar(select(m.Document.id).where(m.Document.tenant_id == fixture_id("mehta")))
    assert attach(pid, str(kavita_doc)).status_code == 404


def test_possible_duplicate_is_flagged_but_never_merged():
    with Session(get_engine()) as s, s.begin():
        seed(s, "after")  # the FD ending 7730 already exists
    doc = processed("fd_receipt.txt")
    assert [d["asset_id"] for d in doc["possible_duplicates"]] == [str(fixture_id("fd"))]
    pid = fd_draft()
    view = attach(pid, doc["id"]).json()
    assert any(c["kind"] == "possible_duplicate" for c in view["conflicts"])
    assert count(m.Asset, m.Asset.tenant_id == fixture_id("patel")) == 7  # nothing merged or created yet


# --- review paths and safety ----------------------------------------------------------------------


def test_unclear_nominee_and_handwriting_go_to_review():
    policy = processed("policy_schedule.txt")
    assert policy["status"] == "needs_review" and "nominee section unclear" in policy["review_reasons"]
    assert policy["fields"].get("nomination_status") == "unknown"
    assert policy["fields"]["insurance_cover_minor"] == 500000000 and "balance_minor" not in policy["fields"]
    note = processed("handwritten_inventory.txt")
    assert note["status"] == "needs_review" and note["kind"] == "handwritten_inventory"


def test_mf_statement_keeps_sip_separate_from_value():
    doc = processed("mf_statement.txt")
    assert doc["fields"]["balance_minor"] == 87540000 and doc["fields"]["sip_installment_minor"] == 1000000
    assert doc["fields"]["nomination_status"] == "not_recorded"


def _handover() -> uuid.UUID:
    with Session(get_engine()) as s, s.begin():
        h = m.HandoverRequest(tenant_id=fixture_id("patel"), owner_user_id=fixture_id("ramesh"), requester_user_id=fixture_id("priya"),
                              grant_id=fixture_id("grant_priya_post"), grant_version=1, state="REQUESTED")  # fmt: skip
        s.add(h)
        s.flush()
        return h.id


def test_death_certificate_never_moves_a_handover():
    hid = _handover()
    doc = processed("death_certificate.txt")
    assert doc["status"] == "needs_review" and doc["kind"] == "death_certificate" and doc["fields"] == {}
    with Session(get_engine()) as s:
        assert s.get(m.HandoverRequest, hid).state == "REQUESTED"
        assert s.scalar(select(func.count()).select_from(m.ReviewDecision)) == 0


def test_document_instructions_cannot_invoke_tools():
    hid = _handover()
    pid = fd_draft()
    before = (count(m.Confirmation), count(m.AccessGrant), count(m.Proposal))
    doc = processed("injection_receipt.txt")
    assert set(doc["fields"]) <= {"institution_name", "jurisdiction", "balance_minor", "currency", "term_months", "opened_on", "source_date"}
    assert (count(m.Confirmation), count(m.AccessGrant), count(m.Proposal)) == before
    assert client.get(f"/v1/proposals/{pid}", headers=RAMESH).json()["status"] == "draft"  # untouched until the owner attaches
    with Session(get_engine()) as s:
        assert s.get(m.HandoverRequest, hid).state == "REQUESTED"


def test_model_output_outside_schema_sends_document_to_review(monkeypatch):
    class Hostile:
        model_version = "hostile"

        def generate_structured(self, **_):
            return {"document_kind": "fd_receipt", "confirm_proposal": True, "grant_access": "priya"}

    import dataclasses

    worker = importlib.import_module("reclaim_worker.app")
    hostile = dataclasses.replace(worker.get_providers(), llm=Hostile())
    monkeypatch.setattr(worker, "get_providers", lambda: hostile)
    doc = processed("fd_receipt.txt")
    assert doc["status"] == "needs_review" and "model output invalid" in doc["review_reasons"] and doc["fields"] == {}

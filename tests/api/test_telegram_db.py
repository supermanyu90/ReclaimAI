"""Prompt 4 acceptance: expired/replayed linking codes fail; duplicate updates do not
duplicate drafts; an unlinked chat cannot query assets. Plus: webhook secret, group
rejection, rate limit, masked replies, no confirmation over Telegram, no real sends."""

import importlib
import itertools
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.db import get_engine
from reclaim_api.seed import fixture_id, seed
from reclaim_api.telegram.linking import chat_hash
from reclaim_api.telegram.sender import DryRunSender
from reclaim_api.telegram.simulate import build_update

pytestmark = pytest.mark.db
main = importlib.import_module("reclaim_api.main")

client = TestClient(main.app)
SECRET = "test-webhook-secret"
RAMESH_H = {"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}
ids = itertools.count(int(uuid.uuid4().int % 10**12))
CHAT = 5550001


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", SECRET)
    monkeypatch.setattr(main.settings, "telegram_rate_limit_per_min", 20)
    monkeypatch.setattr(main, "_today", lambda: date(2026, 10, 15))
    assert isinstance(main.get_providers().telegram, DryRunSender)  # tests never send for real
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
        s.execute(text("DELETE FROM telegram_updates"))
    yield


def tg(message: str, chat_id: int = CHAT, *, group: bool = False, update_id: int | None = None, secret: str = SECRET) -> dict:
    upd = build_update(message, chat_id, group=group, update_id=update_id if update_id is not None else next(ids))
    res = client.post("/v1/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    assert res.status_code == 200, res.text
    return res.json()["dry_run"]


def issue_code() -> str:
    res = client.post("/v1/channels/telegram/link-code", headers=RAMESH_H)
    assert res.status_code == 200
    return res.json()["code"]


def link(chat_id: int = CHAT) -> None:
    assert tg(f"/link {issue_code()}", chat_id)["outcome"] == "linked"


def count(model, *where) -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


# --- webhook authentication ---------------------------------------------------------------


@pytest.mark.parametrize("secret", ["", "wrong-secret", SECRET + "x"])
def test_bad_secret_is_rejected_and_nothing_recorded(secret):
    upd = build_update("/start", CHAT, update_id=next(ids))
    res = client.post("/v1/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    assert res.status_code == 401
    assert count(m.TelegramUpdate) == 0


def test_unconfigured_secret_refuses_everything(monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", None)
    res = client.post("/v1/telegram/webhook", json=build_update("/start", CHAT), headers={"X-Telegram-Bot-Api-Secret-Token": ""})
    assert res.status_code == 503


# --- linking ---------------------------------------------------------------------------------


def test_onboarding_explains_privacy_boundary():
    reply = tg("/start")["replies"][0]
    assert "masked" in reply and "web app" in reply and "OTP" in reply and "/link" in reply


def test_unlinked_chat_cannot_query_assets():
    for msg in ("show what I have recorded", "I opened an SBI FD for ₹5 lakh for 3 years"):
        out = tg(msg)
        assert out["outcome"] == "not_linked"
        assert "Sagar" not in out["replies"][0] and "asset" not in out["replies"][0].lower().replace("assets", "")
    assert count(m.ChatTurn) == 0 and count(m.Proposal) == 0


def test_username_or_phone_never_links_a_chat():
    upd = build_update("show what I have recorded", CHAT, update_id=next(ids))
    upd["message"]["from"] |= {"username": "ramesh_patel", "first_name": "Ramesh", "last_name": "Patel"}
    upd["message"]["contact"] = {"phone_number": "+919876543210", "first_name": "Ramesh"}
    res = client.post("/v1/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert res.json()["dry_run"]["outcome"] == "not_linked"


def test_code_is_single_use_and_stored_hashed():
    code = issue_code()
    assert tg(f"/link {code}")["outcome"] == "linked"
    assert tg(f"/link {code}", chat_id=5550002)["outcome"] == "link_failed"  # replay from another chat
    assert tg(f"/link {code}")["outcome"] == "link_failed"  # replay from the same chat
    with Session(get_engine()) as s:
        stored = s.scalars(select(m.LinkCode.code_hash)).all()
    assert code not in stored and all(len(h) == 64 for h in stored)


def test_expired_code_fails():
    code = issue_code()
    with Session(get_engine()) as s, s.begin():
        s.execute(text("UPDATE link_codes SET expires_at = now() - interval '1 second'"))
    assert tg(f"/link {code}")["outcome"] == "link_failed"
    assert count(m.ChannelLink, m.ChannelLink.status == "active") == 0


def test_issuing_a_new_code_retires_the_previous_one():
    old, new = issue_code(), issue_code()
    assert tg(f"/link {old}")["outcome"] == "link_failed"
    assert tg(f"/link {new}")["outcome"] == "linked"


def test_only_owners_can_issue_codes():
    for user in ("priya", "reviewer_meera"):
        res = client.post("/v1/channels/telegram/link-code", headers={"Authorization": f"Bearer dev:{fixture_id(user)}"})
        assert res.status_code == 403


def test_chat_id_is_stored_as_keyed_hash_only():
    link()
    with Session(get_engine()) as s:
        stored = s.scalars(select(m.ChannelLink.chat_id_hash)).all() + s.scalars(select(m.TelegramUpdate.chat_id_hash)).all()
    assert all(str(CHAT) not in h for h in stored)
    assert chat_hash(CHAT, main.settings) in stored
    import hashlib

    assert hashlib.sha256(f"telegram:{CHAT}".encode()).hexdigest() not in stored  # keyed, not plain


def test_unlink_from_chat_and_from_web():
    link()
    assert tg("/unlink")["outcome"] == "unlinked"
    assert tg("show what I have recorded")["outcome"] == "not_linked"
    link()
    assert client.post("/v1/channels/telegram/unlink", headers=RAMESH_H).json() == {"unlinked": True}
    assert tg("show what I have recorded")["outcome"] == "not_linked"
    assert client.get("/v1/channels/telegram", headers=RAMESH_H).json()["linked"] is False


# --- chat over Telegram ----------------------------------------------------------------------


def test_duplicate_update_does_not_duplicate_draft():
    link()
    uid = next(ids)
    first = tg("I opened an SBI FD for ₹5 lakh for 3 years", update_id=uid)
    again = tg("I opened an SBI FD for ₹5 lakh for 3 years", update_id=uid)
    assert first["outcome"] == "chat_clarification" and again == {"outcome": "duplicate", "replies": []}
    assert count(m.Proposal) == 1
    assert count(m.ChatTurn, m.ChatTurn.channel == "telegram") == 2  # one owner turn + one reply


def test_replies_are_masked_and_point_to_the_web_app():
    link()
    tg("I opened an SBI FD for ₹5 lakh for 3 years")
    out = tg("I opened it on 10 October 2026")
    reply = out["replies"][0]
    assert out["outcome"] == "chat_review"
    for secret_value in ("5,00,000", "₹5", "Sagar Bharat", "2026-10-10", "October"):
        assert secret_value not in reply
    assert "/chat" in reply and "sign-in required" in reply

    listing = tg("show what I have recorded")["replies"][0]
    assert "6 active recorded assets" in listing and "Kothrud" not in listing and "Merlion" not in listing


def test_confirmation_cannot_happen_over_telegram():
    link()
    tg("I opened an SBI FD for ₹5 lakh for 3 years")
    tg("I opened it on 10 October 2026")
    for msg in ("confirm", "yes, confirm it", "/confirm"):
        tg(msg)
    assert count(m.Confirmation) == 0
    assert count(m.Asset, m.Asset.tenant_id == fixture_id("patel")) == 6


def test_group_chats_are_ignored_even_when_text_looks_valid():
    link()
    out = tg("show what I have recorded", chat_id=CHAT, group=True)
    assert out == {"outcome": "rejected_group", "replies": []}


def test_documents_go_to_the_web_app():
    link()
    upd = build_update("", CHAT, update_id=next(ids))
    upd["message"].pop("text")
    upd["message"]["document"] = {"file_id": "x", "file_name": "statement.pdf"}
    res = client.post("/v1/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert res.json()["dry_run"]["outcome"] == "non_text"
    assert "web app" in res.json()["dry_run"]["replies"][0]


def test_rate_limit_answers_once_then_stays_quiet(monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_rate_limit_per_min", 3)
    outcomes = [tg("/help")["outcome"] for _ in range(3)]
    limited = [tg("/help") for _ in range(3)]
    assert outcomes == ["onboarding"] * 3
    assert [x["outcome"] for x in limited] == ["rate_limited"] * 3
    assert [len(x["replies"]) for x in limited] == [1, 0, 0]


def test_malformed_updates_are_dropped_without_error():
    res = client.post("/v1/telegram/webhook", content=b"not json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET, "Content-Type": "application/json"})
    assert res.status_code == 200
    res = client.post("/v1/telegram/webhook", json={"update_id": "x"}, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert res.status_code == 200 and res.json()["dry_run"]["outcome"] == "malformed"

"""Telegram webhook: the same conversation gateway as web chat, with a stricter output.

Order of checks: secret header (in the route) → dedup by update_id → private chats only
→ per-chat rate limit → commands (/start, /link, /unlink) → linked-owner chat.
Replies are masked: no amounts, dates or references leave through Telegram; review and
confirmation happen in the authenticated web app.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals
from reclaim_api.chat import service as chat
from reclaim_api.config import Settings
from reclaim_api.telegram import linking

log = logging.getLogger("reclaim_api.telegram")


class TgChat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    type: str


class TgMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message_id: int
    chat: TgChat
    text: str | None = None


class TgUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    update_id: int
    message: TgMessage | None = None


@dataclass
class Outcome:
    status: str
    replies: list[tuple[int, str]] = field(default_factory=list)


def onboarding(s: Settings) -> str:
    return (
        "ReclaimAI (synthetic demo).\n\n"
        "Privacy: this chat is only for describing assets. You will see short, masked summaries here; "
        "amounts, account details, documents and the Family Asset Passport stay in the web app, which "
        "requires sign-in. Nothing is recorded until you confirm on the web.\n\n"
        "Never send OTPs, PINs, passwords or full account numbers.\n\n"
        f"To link this chat: sign in at {s.web_origin}, choose \"Link Telegram\", then send /link CODE here. "
        "/unlink disconnects this chat."
    )


NOT_LINKED = "This chat isn't linked to a ReclaimAI account, so I can't look anything up. Send /start for how to link it."
UPLOAD_ON_WEB = "Please upload documents in the web app, not in Telegram. Telegram is not used for sensitive files."


def _summary(p: dict[str, Any], target: dict | None) -> str:
    """Masked: type and status only. Values stay in the web app."""
    fields = {f["field"]: f for f in p["fields"]}
    kind = (fields.get("asset_type") or {}).get("value") or (target or {}).get("label") or "asset"
    if p["change_kind"] == "close":
        return f"close {kind} ({(target or {}).get('masked_reference') or 'reference hidden'})"
    if p["change_kind"] == "update":
        return f"update to {kind}"
    missing = len(p["missing_fields"])
    return f"new {kind.lower()}" + (f", {missing} detail{'s' if missing != 1 else ''} still needed" if missing else "")


def render(result: dict[str, Any], s: Settings) -> str:
    p = result.get("proposal")
    if result["state"] == "review" and p:
        return (
            f"Draft ready: {_summary(p, p.get('target'))}. Details are hidden here.\n"
            f"Review and confirm in the web app (sign-in required): {s.web_origin}/chat"
        )
    text = result["reply"]
    if p and p["status"] in ("draft", "awaiting_confirmation"):
        text += f"\n\n(Draft: {_summary(p, p.get('target'))}. Full details: {s.web_origin}/chat)"
    return text


def _record(session: Session, update_id: int, chash: str | None) -> bool:
    """Insert the update ID; False if Telegram already delivered it."""
    inserted = session.scalar(
        insert(m.TelegramUpdate).values(update_id=update_id, chat_id_hash=chash).on_conflict_do_nothing().returning(m.TelegramUpdate.update_id)
    )
    return inserted is not None


def _set_outcome(session: Session, update_id: int, outcome: str) -> None:
    session.get(m.TelegramUpdate, update_id).outcome = outcome


def handle_update(session: Session, raw: dict[str, Any], *, s: Settings, llm: Any, today: date) -> Outcome:
    try:
        upd = TgUpdate.model_validate(raw)
    except ValidationError:
        return Outcome("malformed")

    msg = upd.message
    chash = linking.chat_hash(msg.chat.id, s) if msg else None
    if not _record(session, upd.update_id, chash):
        return Outcome("duplicate")
    if msg is None:
        _set_outcome(session, upd.update_id, "ignored")
        return Outcome("ignored")
    if msg.chat.type != "private":
        _set_outcome(session, upd.update_id, "rejected_group")
        return Outcome("rejected_group")  # never answer in groups or channels

    window_start = session.scalar(select(func.now())) - timedelta(minutes=1)
    recent = session.scalar(
        select(func.count()).select_from(m.TelegramUpdate).where(m.TelegramUpdate.chat_id_hash == chash, m.TelegramUpdate.received_at > window_start)
    )
    if recent > s.telegram_rate_limit_per_min:
        _set_outcome(session, upd.update_id, "rate_limited")
        # Say so once, then stay quiet for the rest of the window.
        notice = [(msg.chat.id, "Too many messages. Please wait a minute.")] if recent == s.telegram_rate_limit_per_min + 1 else []
        return Outcome("rate_limited", notice)

    def reply(status: str, text: str) -> Outcome:
        _set_outcome(session, upd.update_id, status)
        return Outcome(status, [(msg.chat.id, text)])

    text = (msg.text or "").strip()
    if not text:
        return reply("non_text", UPLOAD_ON_WEB)

    command, _, arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()  # "/link@ReclaimBot"
    if command in ("/start", "/help"):
        return reply("onboarding", onboarding(s))
    if command == "/link":
        try:
            user = linking.redeem_code(session, arg, chash)
        except linking.LinkError:
            return reply("link_failed", "That code is invalid or expired. Create a new one in the web app.")
        return reply("linked", f"Linked to {user.display_name}'s ReclaimAI account. Describe an asset to start; you'll confirm on the web.")
    if command == "/unlink":
        done = linking.unlink_chat(session, chash)
        return reply("unlinked" if done else "not_linked", "This chat is now unlinked." if done else NOT_LINKED)

    actor = linking.linked_actor(session, chash)
    if actor is None or actor.role != "owner":
        return reply("not_linked", NOT_LINKED)

    result = chat.handle_message(session, actor, text, llm=llm, today=today, history_turns=s.chat_history_turns, channel="telegram")
    if result.get("proposal") and result["proposal"].get("target") is None and result["proposal"]["asset_id"]:
        p = session.get(m.Proposal, uuid.UUID(result["proposal"]["id"]))
        result["proposal"]["target"] = proposals.target_of(session, actor, p)
    return reply(f"chat_{result['state']}", render(result, s))

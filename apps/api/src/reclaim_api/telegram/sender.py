"""Outbound Telegram messages.

DryRunSender is the default and is used by every test: it records replies in memory
and never touches the network. BotApiSender sends for real and is only built when
TELEGRAM_SEND_MODE=live and a bot token is configured.
"""

import threading
from dataclasses import dataclass
from typing import Protocol

import httpx

from reclaim_api.config import Settings
from reclaim_api.providers.base import CapabilityStatus


@dataclass(frozen=True)
class Outgoing:
    chat_id: int
    text: str


class TelegramSender(Protocol):
    def send(self, chat_id: int, text: str) -> None: ...

    def status(self) -> CapabilityStatus: ...


class DryRunSender:
    def __init__(self, webhook_ready: bool) -> None:
        self._lock = threading.Lock()
        self._webhook_ready = webhook_ready
        self.sent: list[Outgoing] = []

    def send(self, chat_id: int, text: str) -> None:
        with self._lock:
            self.sent.append(Outgoing(chat_id, text))
            del self.sent[:-200]  # bounded

    def status(self) -> CapabilityStatus:
        if not self._webhook_ready:
            return CapabilityStatus("telegram", "unavailable", "dry-run", "TELEGRAM_WEBHOOK_SECRET not set; webhook refuses requests")
        return CapabilityStatus("telegram", "fixture", "dry-run", "webhook verified locally; replies recorded, never sent")


class BotApiSender:
    def __init__(self, token: str, timeout_s: float = 10.0) -> None:
        self._client = httpx.Client(base_url=f"https://api.telegram.org/bot{token}", timeout=timeout_s)
        self._last_ok: bool | None = None

    def send(self, chat_id: int, text: str) -> None:
        try:
            res = self._client.post("/sendMessage", json={"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}})
            res.raise_for_status()
            self._last_ok = True
        except httpx.HTTPError:
            # The URL embeds the token: never log the exception text.
            self._last_ok = False

    def status(self) -> CapabilityStatus:
        if self._last_ok is False:
            return CapabilityStatus("telegram", "unavailable", "bot-api", "last send failed")
        detail = "sending enabled; last send succeeded" if self._last_ok else "sending enabled; nothing sent yet, so unverified"
        return CapabilityStatus("telegram", "live" if self._last_ok else "unavailable", "bot-api", detail)


def build_sender(s: Settings) -> TelegramSender:
    if s.telegram_send_mode == "live" and s.telegram_bot_token and s.telegram_webhook_secret:
        return BotApiSender(s.telegram_bot_token)
    return DryRunSender(webhook_ready=bool(s.telegram_webhook_secret))

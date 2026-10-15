"""Local Telegram simulator: posts Telegram-shaped updates to the local webhook.

    reclaim-telegram-sim "/start"
    reclaim-telegram-sim --chat-id 5550001 "/link ABCD2345"
    reclaim-telegram-sim --group "show my assets"     # should be ignored
    reclaim-telegram-sim                              # interactive

Uses the webhook secret from settings and prints the dry-run replies the API recorded.
Nothing reaches Telegram: the API only sends for real with TELEGRAM_SEND_MODE=live.
"""

import argparse
import itertools
import os
import sys
import time

import httpx

from reclaim_api.config import get_settings

_ids = itertools.count(int(time.time() * 1000))


def build_update(text: str, chat_id: int, *, group: bool = False, update_id: int | None = None) -> dict:
    chat = {"id": -chat_id if group else chat_id, "type": "group" if group else "private"}
    return {
        "update_id": update_id if update_id is not None else next(_ids),
        "message": {"message_id": next(_ids) % 1_000_000, "date": int(time.time()), "chat": chat, "text": text,
                    "from": {"id": chat_id, "is_bot": False, "first_name": "Simulated"}},
    }  # fmt: skip


def post(client: httpx.Client, update: dict, secret: str) -> dict:
    res = client.post("/v1/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    res.raise_for_status()
    return res.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send simulated Telegram updates to the local webhook.")
    parser.add_argument("text", nargs="*", help="message text; omit for interactive mode")
    parser.add_argument("--chat-id", type=int, default=5550001)
    parser.add_argument("--group", action="store_true", help="send from a group chat")
    parser.add_argument("--update-id", type=int, help="reuse an update_id to test deduplication")
    args = parser.parse_args()

    s = get_settings()
    if not s.telegram_webhook_secret:
        sys.exit("TELEGRAM_WEBHOOK_SECRET is not set in .env; the webhook refuses all requests without it.")
    api = os.environ.get("API_URL", f"http://localhost:{s.api_port}")
    with httpx.Client(base_url=api, timeout=60) as client:
        messages = [" ".join(args.text)] if args.text else iter(lambda: input("you> "), "")
        for text in messages:
            body = post(client, build_update(text, args.chat_id, group=args.group, update_id=args.update_id), s.telegram_webhook_secret)
            dry = body.get("dry_run")
            if dry is None:
                print("(sent via Bot API; no dry-run transcript)")
                continue
            print(f"[{dry['outcome']}]")
            for reply in dry["replies"] or ["(no reply)"]:
                print("bot> " + reply.replace("\n", "\nbot> "))

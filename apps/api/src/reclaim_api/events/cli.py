"""Outbox operations: `reclaim-events drain | stats | replay [EVENT_ID ...]`."""

import argparse
import time

from sqlalchemy.orm import Session

from reclaim_api.config import get_settings
from reclaim_api.db import get_engine
from reclaim_api.events import outbox
from reclaim_api.providers.registry import get_providers
from reclaim_api.telegram.linking import hash_key


def drain_once() -> dict[str, int]:
    s = get_settings()
    with Session(get_engine()) as session, session.begin():
        return outbox.publish_pending(session, get_providers().events, analytics_key=hash_key(s), batch=s.outbox_batch, max_attempts=s.outbox_max_attempts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Outbox operations")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("drain", help="publish until nothing is due")
    sub.add_parser("stats", help="counts only")
    sub.add_parser("rebuild", help="recompute projections and findings from SQL")
    rp = sub.add_parser("replay", help="re-queue dead-lettered events (all, or the given IDs)")
    rp.add_argument("event_ids", nargs="*")
    args = parser.parse_args()

    if args.cmd == "drain":
        total = {"published": 0, "failed": 0, "dead_lettered": 0}
        for _ in range(100):
            counts = drain_once()
            total = {k: total[k] + counts[k] for k in total}
            if counts["published"] == 0:
                break
            time.sleep(0.1)
        print(" ".join(f"{k}={v}" for k, v in total.items()))
    elif args.cmd == "stats":
        with Session(get_engine()) as session:
            st = outbox.stats(session)
        for key, value in st["outbox"].items():
            print(f"outbox.{key}={value}")
        print(f"consumer_dead_letters={st['consumer_dead_letters']}")
        for consumer, n in st["processed_by_consumer"].items():
            print(f"processed.{consumer}={n}")
    elif args.cmd == "rebuild":
        from datetime import date

        from reclaim_api.events.rebuild import rebuild

        with Session(get_engine()) as session, session.begin():
            print(f"rebuilt {rebuild(session, today=date.today())} assets")
    else:
        with Session(get_engine()) as session, session.begin():
            print(f"re-queued {outbox.replay(session, args.event_ids or None)}")

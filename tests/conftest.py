import os

# Tests never read a developer's .env; they set what they need explicitly.
os.environ.setdefault("APP_MODE", "local")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://reclaim:reclaim@localhost:55432/reclaim")
os.environ.setdefault("LLM_PROVIDER", "fixture")  # CI has no Ollama; live-model tests opt in explicitly
os.environ["TELEGRAM_SEND_MODE"] = "dry_run"  # tests must never send real Telegram messages
os.environ["OUTBOX_LOOP"] = "false"  # tests drive the outbox explicitly


import importlib

import pytest


@pytest.fixture
def drain(tmp_path):
    """Publish the outbox through the real worker push endpoint until nothing is due.
    Returns a function; each call runs passes until no event is published."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from reclaim_api.config import get_settings
    from reclaim_api.db import get_engine
    from reclaim_api.events import outbox
    from reclaim_api.events.publishers import LocalPushPublisher
    from reclaim_api.providers.registry import LOCAL_PUSH_TOKEN
    from reclaim_api.telegram.linking import hash_key

    worker = importlib.import_module("reclaim_worker.app")
    from reclaim_api.analytics.warehouse import LocalWarehouse

    warehouse = LocalWarehouse(get_engine())
    publisher = LocalPushPublisher(client=TestClient(worker.app), token=LOCAL_PUSH_TOKEN, warehouse=warehouse)

    def run(max_passes: int = 10, publisher_override=None) -> dict[str, int]:
        total = {"published": 0, "failed": 0, "dead_lettered": 0}
        for _ in range(max_passes):
            with Session(get_engine()) as s, s.begin():
                counts = outbox.publish_pending(s, publisher_override or publisher, analytics_key=hash_key(get_settings()), max_attempts=3)
            total = {k: total[k] + counts[k] for k in total}
            if counts["published"] == 0:
                break
        return total

    run.warehouse = warehouse
    return run

"""Worker HTTP surface.

POST /pubsub/push            Pub/Sub push (cloud: OIDC token from PUSH_SERVICE_ACCOUNT;
                             local: shared token, local mode only)
POST /tasks/publish-outbox   one outbox pass (Cloud Scheduler in cloud; same auth)
In local mode a background loop publishes the outbox every OUTBOX_POLL_S seconds.

Responses: 204 ack (processed, duplicate, or unfixable and dead-lettered), 500 retry.
"""

import hmac
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.config import get_settings
from reclaim_api.db import check_database, get_engine
from reclaim_api.events import consumers, outbox
from reclaim_api.events.envelope import from_push_body
from reclaim_api.lib.log import configure_logging, error_tag
from reclaim_api.providers.registry import LOCAL_PUSH_TOKEN, get_providers
from reclaim_api.telegram.linking import hash_key

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("reclaim_worker")


def _today() -> date:
    return date.today()


def verify_oidc(token: str, identity: str | None = None) -> bool:
    """Cloud: a Google-signed ID token for our audience, from the one identity this endpoint allows."""
    identity = identity or settings.push_service_account
    if not (settings.worker_audience and identity):
        return False
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience=settings.worker_audience)
    except (GoogleAuthError, ValueError):
        return False
    return claims.get("email") == identity and claims.get("email_verified") is True


def authorized(request: Request, identity: str | None = None) -> bool:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    if settings.app_mode in ("local", "demo") and token.startswith("local:"):
        # The hosted demo has no built-in token: WORKER_PUSH_TOKEN (Secret Manager) must be set.
        expected = settings.worker_push_token or (LOCAL_PUSH_TOKEN if settings.app_mode == "local" else None)
        return expected is not None and hmac.compare_digest(token.removeprefix("local:").encode(), expected.encode())
    if settings.app_mode == "cloud":
        return verify_oidc(token, identity)
    return False


def drain_once() -> dict[str, int]:
    with Session(get_engine()) as session, session.begin():
        return outbox.publish_pending(
            session, get_providers().events, analytics_key=hash_key(settings), batch=settings.outbox_batch, max_attempts=settings.outbox_max_attempts
        )


def _loop(stop: threading.Event) -> None:
    while not stop.wait(settings.outbox_poll_s):
        try:
            drain_once()
        except Exception as err:  # noqa: BLE001 — keep the loop alive; the next pass retries
            log.warning("outbox pass failed %s", error_tag(err))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    if settings.app_mode in ("local", "demo") and settings.outbox_loop:
        threading.Thread(target=_loop, args=(stop,), daemon=True, name="outbox").start()
    yield
    stop.set()


app = FastAPI(title="ReclaimAI worker", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict:
    db = check_database()
    return {"status": "ready" if db.ok else "not_ready", "database_ok": db.ok}


@app.post("/pubsub/push")
async def pubsub_push(request: Request) -> Response:
    if not authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        body = await request.json()
        ev = from_push_body(body)
    except ValueError:
        # Retrying cannot fix a malformed message: record it and acknowledge.
        with Session(get_engine()) as s, s.begin():
            s.add(m.DeadLetter(consumer="push-endpoint", reason="malformed push message"))
        return Response(status_code=204)
    ctx = consumers.Context(providers=get_providers(), today=_today())
    result = await run_in_threadpool(consumers.dispatch, lambda: Session(get_engine()), ev, ctx)
    log.info("event handled event_type=%s status=%d outcomes=%s", ev.event_type, result.status, ",".join(f"{k}:{v}" for k, v in result.outcomes.items()))
    return Response(status_code=result.status)


@app.post("/tasks/publish-outbox")
async def publish_outbox(request: Request) -> dict:
    if not authorized(request, settings.scheduler_service_account if settings.app_mode == "cloud" else None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return await run_in_threadpool(drain_once)

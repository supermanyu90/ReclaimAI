"""FastAPI application: health, capabilities and actor-scoped record reads."""

import hmac
import logging
import time
import uuid
from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals, records
from reclaim_api.auth import CurrentActor, DbSession
from reclaim_api.chat import service as chat
from reclaim_api.claims import copilot
from reclaim_api.config import get_settings, synthetic_signin
from reclaim_api.db import check_database, get_engine
from reclaim_api.documents import service as documents
from reclaim_api.lib.log import configure_logging, correlation_id
from reclaim_api.providers.registry import get_providers
from reclaim_api.telegram import linking as tg_linking
from reclaim_api.telegram import webhook as tg_webhook
from reclaim_api.telegram.sender import DryRunSender

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("reclaim_api")

app = FastAPI(title="ReclaimAI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-Id", "Idempotency-Key"],
)


@app.middleware("http")
async def with_correlation_id(request: Request, call_next):
    # Accept a caller's ID only if it looks like one; never echo arbitrary header text.
    supplied = request.headers.get("x-correlation-id", "")
    cid = supplied if len(supplied) <= 64 and supplied.replace("-", "").isalnum() else str(uuid.uuid4())
    token = correlation_id.set(cid)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        # One structured line per request: route *template* (no IDs or query strings), status,
        # latency. Log-based metrics in Cloud Logging turn these into latency and error rates.
        route = request.scope.get("route")
        log.info("request", extra={"http": {"method": request.method, "route": getattr(route, "path", "unmatched"), "status": status,
                                            "latency_ms": round((time.perf_counter() - started) * 1000, 1)}})  # fmt: skip
        correlation_id.reset(token)
    response.headers["X-Correlation-Id"] = cid
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: the database is reachable and migrated."""
    db = check_database()
    body = {"status": "ready" if db.ok else "not_ready", "app_mode": settings.app_mode, "database": asdict(db)}
    return JSONResponse(body, status_code=200 if db.ok else 503)


@app.get("/v1/system/events")
def event_stats(session: DbSession) -> dict:
    """Outbox and consumer health: counts only, no tenant or payload data."""
    from reclaim_api.events import outbox

    return outbox.stats(session)


@app.get("/v1/findings")
def findings(actor: CurrentActor, session: DbSession) -> dict:
    """Freshness findings computed by the worker for the owner's assets."""
    if actor.role != "owner":
        return {"findings": []}
    rows = session.execute(
        select(m.FreshnessFinding, m.AssetProjection.view)
        .join(m.Asset, m.Asset.id == m.FreshnessFinding.asset_id)
        .join(m.AssetProjection, m.AssetProjection.asset_id == m.Asset.id, isouter=True)
        .where(m.Asset.tenant_id == actor.tenant_id, m.Asset.owner_user_id == actor.user_id)
        .order_by(m.FreshnessFinding.kind)
    ).all()
    return {"findings": [{"asset_id": str(f.asset_id), "label": (view or {}).get("label"), "kind": f.kind, "detail": f.detail,
                          "asset_version": f.asset_version} for f, view in rows]}  # fmt: skip


@app.get("/v1/system/capabilities")
def capabilities() -> dict:
    return {"app_mode": settings.app_mode, "capabilities": [asdict(s) for s in get_providers().statuses()],
            "optional_features": {"recovery": settings.recovery_enabled}}  # fmt: skip


@app.get("/v1/me")
def me(actor: CurrentActor) -> dict:
    return {"user_id": str(actor.user_id), "role": actor.role}


@app.get("/v1/assets")
def list_assets(actor: CurrentActor, session: DbSession) -> dict:
    return {"assets": records.list_assets(session, actor), "synthetic": synthetic_signin(settings)}


@app.get("/v1/assets/{asset_id}")
def get_asset(asset_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    try:
        return records.get_asset(session, actor, asset_id)
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None


@app.get("/v1/inventory/totals")
def inventory_totals(actor: CurrentActor, session: DbSession) -> dict:
    return records.inventory_totals(session, actor)


# --- chat and proposals ------------------------------------------------------------


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=chat.MAX_MESSAGE_CHARS)


def _today() -> date:
    return date.today()


@app.post("/v1/chat/messages")
def post_chat_message(body: ChatMessage, actor: CurrentActor, session: DbSession) -> dict:
    try:
        result = chat.handle_message(
            session, actor, body.text, llm=get_providers().llm, today=_today(), history_turns=settings.chat_history_turns
        )
    except chat.Forbidden:
        raise HTTPException(status_code=403, detail="chat is available to asset owners only") from None
    session.commit()
    return result


@app.get("/v1/chat/history")
def chat_history(actor: CurrentActor, session: DbSession) -> dict:
    turns = session.scalars(
        select(m.ChatTurn)
        .where(m.ChatTurn.tenant_id == actor.tenant_id, m.ChatTurn.user_id == actor.user_id)
        .order_by(m.ChatTurn.created_at.desc())
        .limit(50)
    ).all()
    return {
        "turns": [
            {"id": str(t.id), "speaker": t.speaker, "text": t.text, "state": t.state, "proposal_id": str(t.proposal_id) if t.proposal_id else None}
            for t in reversed(turns)
        ]
    }


def _proposal_errors(fn):
    try:
        return fn()
    except proposals.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except proposals.Conflict as err:
        raise HTTPException(status_code=409, detail={"code": err.code, "message": err.args[0]}) from None


@app.get("/v1/proposals/{proposal_id}")
def get_proposal(proposal_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    return _proposal_errors(lambda: proposals.get_proposal(session, actor, proposal_id))


@app.patch("/v1/proposals/{proposal_id}")
def edit_proposal(proposal_id: uuid.UUID, changes: dict, actor: CurrentActor, session: DbSession) -> dict:
    result = _proposal_errors(lambda: proposals.edit_proposal(session, actor, proposal_id, changes))
    session.commit()
    return result


@app.post("/v1/proposals/{proposal_id}/cancel")
def cancel_proposal(proposal_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    result = _proposal_errors(lambda: proposals.cancel_proposal(session, actor, proposal_id))
    session.commit()
    return result


@app.post("/v1/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: uuid.UUID, actor: CurrentActor, session: DbSession, idempotency_key: str = Header()) -> dict:
    """The owner's explicit confirmation. Not reachable from the model."""
    try:
        result = _proposal_errors(lambda: proposals.confirm_proposal(session, actor, proposal_id, idempotency_key))
        session.commit()
    except HTTPException as err:
        if err.status_code == 409 and isinstance(err.detail, dict) and err.detail.get("code") == "stale_proposal":
            session.commit()  # keep the proposal marked superseded
        else:
            session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        # A concurrent request won the race; report what it recorded.
        result = _proposal_errors(lambda: proposals.confirm_proposal(session, actor, proposal_id, idempotency_key))
    return result


@app.get("/v1/dev/users")
def dev_users(session: DbSession) -> dict:
    """Synthetic-person sign-in picker (local and hosted demo). Absent in cloud mode."""
    if not synthetic_signin(settings):
        raise HTTPException(status_code=404, detail="not found")
    rows = session.execute(
        select(m.User.id, m.User.display_name, m.User.role, m.Tenant.name)
        .join(m.Tenant, m.Tenant.id == m.User.tenant_id)
        .where(m.Tenant.is_synthetic.is_(True))
        .order_by(m.Tenant.name, m.User.role, m.User.display_name)
    ).all()
    return {"synthetic": True, "users": [{"id": str(r.id), "display_name": r.display_name, "role": r.role, "tenant": r.name} for r in rows]}


# --- Telegram ----------------------------------------------------------------------


@app.post("/v1/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    """Telegram calls this. Authenticated by the webhook secret header, never by user identity."""
    expected = settings.telegram_webhook_secret
    if not expected:
        raise HTTPException(status_code=503, detail="telegram webhook not configured")
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        raw = await request.json()
    except ValueError:
        return {"ok": True}  # Telegram retries non-2xx; malformed input is dropped, not retried
    providers = get_providers()

    def process() -> tg_webhook.Outcome:
        with Session(get_engine()) as session, session.begin():
            result = tg_webhook.handle_update(session, raw if isinstance(raw, dict) else {}, s=settings, llm=providers.llm, today=_today())
        # Send only after commit, so a failed send never re-runs the update's effects.
        for chat_id, text_ in result.replies:
            providers.telegram.send(chat_id, text_)
        return result

    # Database and model calls are blocking; keep them off the event loop.
    outcome = await run_in_threadpool(process)
    body: dict = {"ok": True}
    if settings.app_mode == "local" and isinstance(providers.telegram, DryRunSender):
        body["dry_run"] = {"outcome": outcome.status, "replies": [t for _, t in outcome.replies]}
    return body


@app.post("/v1/channels/telegram/link-code")
def telegram_link_code(actor: CurrentActor, session: DbSession) -> dict:
    try:
        code, expires = tg_linking.issue_code(session, actor, settings)
    except PermissionError:
        raise HTTPException(status_code=403, detail="linking is available to asset owners") from None
    session.commit()
    return {
        "code": code,
        "expires_at": expires.isoformat(),
        "bot_username": settings.telegram_bot_username,
        "instructions": "In a private Telegram chat with the ReclaimAI bot, send: /link " + code,
    }


@app.get("/v1/channels/telegram")
def telegram_link_status(actor: CurrentActor, session: DbSession) -> dict:
    return tg_linking.link_status(session, actor)


@app.post("/v1/channels/telegram/unlink")
def telegram_unlink(actor: CurrentActor, session: DbSession) -> dict:
    done = tg_linking.unlink_user(session, actor)
    session.commit()
    return {"unlinked": done}


# --- documents ---------------------------------------------------------------------


@app.post("/v1/documents", status_code=202)
async def upload_document(actor: CurrentActor, session: DbSession, file: Annotated[UploadFile, File()]) -> dict:
    """Stores the upload and its document.uploaded outbox event in one transaction; the
    worker processes it when the event is delivered."""
    content = await file.read(documents.MAX_BYTES + 1)
    try:
        doc = await run_in_threadpool(documents.intake, session, actor, content, file.filename, store=get_providers().object_storage)
    except documents.Rejected as err:
        raise HTTPException(status_code=err.status, detail=err.args[0]) from None
    session.commit()
    return documents.view(doc)


@app.get("/v1/documents")
def list_documents(actor: CurrentActor, session: DbSession) -> dict:
    return {"documents": documents.list_documents(session, actor)}


@app.get("/v1/documents/{document_id}")
def get_document(document_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    try:
        return documents.get_document(session, actor, document_id)
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None


@app.get("/v1/documents/{document_id}/content")
def document_content(document_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> Response:
    try:
        data, mime = documents.content_for_owner(session, actor, document_id, get_providers().object_storage)
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store", "Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"})


class AttachBody(BaseModel):
    document_id: uuid.UUID


class ResolveBody(BaseModel):
    field: str = Field(max_length=40)
    use: str = Field(pattern="^(draft|document)$")


@app.post("/v1/proposals/{proposal_id}/documents")
def attach_document(proposal_id: uuid.UUID, body: AttachBody, actor: CurrentActor, session: DbSession) -> dict:
    result = _proposal_errors(lambda: proposals.attach_document(session, actor, proposal_id, body.document_id))
    session.commit()
    return result


@app.post("/v1/proposals/{proposal_id}/resolve")
def resolve_conflict(proposal_id: uuid.UUID, body: ResolveBody, actor: CurrentActor, session: DbSession) -> dict:
    result = _proposal_errors(lambda: proposals.resolve_conflict(session, actor, proposal_id, body.field, body.use))
    session.commit()
    return result


# --- claims ---------------------------------------------------------------------------


class ClaimRequest(BaseModel):
    asset_id: uuid.UUID
    survivorship: str = Field(default="unknown", pattern="^(yes|no|unknown)$")
    dispute: str = Field(default="unknown", pattern="^(yes|no|unknown)$")


@app.post("/v1/claims")
def create_claim(body: ClaimRequest, actor: CurrentActor, session: DbSession) -> dict:
    """Owner preview of the checklist (designated family after release: Prompt 11)."""
    try:
        result = copilot.create_case(
            session, actor, body.asset_id, survivorship=body.survivorship, dispute=body.dispute,
            retriever=get_providers().retrieval, llm=get_providers().llm, today=_today(),
        )  # fmt: skip
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    session.commit()
    return result


@app.get("/v1/claims/{case_id}")
def get_claim(case_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    try:
        return copilot.get_case(session, actor, case_id, retriever=get_providers().retrieval, today=_today())
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None


# --- analytics -------------------------------------------------------------------------


@app.get("/v1/analytics")
def analytics_summary(actor: CurrentActor, session: DbSession, start: date | None = None, end: date | None = None) -> dict:
    """Owner-scoped trends and review flags. Scope is derived from the actor, never from the request."""
    from datetime import timedelta

    from reclaim_api.analytics import service as analytics
    from reclaim_api.telegram.linking import hash_key

    as_of = _today()
    end = end or as_of
    start = start or (end.replace(day=1) - timedelta(days=335)).replace(day=1)
    if start > end or (end - start).days > 731:
        raise HTTPException(status_code=422, detail="window must be at most two years, start before end")
    try:
        return analytics.summary(session, actor, warehouse=get_providers().warehouse, analytics_key=hash_key(settings), start=start, end=end, as_of=as_of)
    except analytics.Forbidden:
        raise HTTPException(status_code=403, detail="analytics are available to asset owners") from None


# --- transaction categories (Prompt 9) ------------------------------------------------------


class CategoryBody(BaseModel):
    category: str = Field(max_length=32)


@app.get("/v1/analytics/review")
def category_review(actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api.classifier import review
    from reclaim_api.telegram.linking import hash_key

    try:
        result = review.queue(session, actor, warehouse=get_providers().warehouse, analytics_key=hash_key(settings))
    except review.Forbidden:
        raise HTTPException(status_code=403, detail="available to asset owners") from None
    result["classifier"] = asdict(get_providers().classification.status())
    return result


@app.post("/v1/analytics/transactions/{transaction_id}/category")
def confirm_category(transaction_id: str, body: CategoryBody, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api.classifier import review
    from reclaim_api.telegram.linking import hash_key

    try:
        result = review.confirm(session, actor, transaction_id, body.category, warehouse=get_providers().warehouse, analytics_key=hash_key(settings))
    except review.Forbidden:
        raise HTTPException(status_code=403, detail="available to asset owners") from None
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown category") from None
    session.commit()
    return result


# --- workspace and passport (Prompt 10) ---------------------------------------------------------


class PassportRequest(BaseModel):
    recipient_user_id: uuid.UUID | None = None


@app.get("/v1/workspace")
def owner_workspace(actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import workspace

    try:
        return workspace.overview(session, actor)
    except workspace.Forbidden:
        raise HTTPException(status_code=403, detail="the workspace is for asset owners") from None


@app.post("/v1/passports", status_code=201)
def generate_passport(body: PassportRequest, actor: CurrentActor, session: DbSession) -> dict:
    """Explicit owner action: snapshot the confirmed records and render a new version."""
    from reclaim_api import passport

    try:
        result = passport.generate(session, actor, recipient_id=body.recipient_user_id, store=get_providers().object_storage, today=_today())
    except passport.Forbidden as err:
        raise HTTPException(status_code=403, detail=str(err)) from None
    except passport.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    session.commit()
    return result


@app.get("/v1/passports")
def list_passports(actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import passport

    return {"passports": passport.list_versions(session, actor), "notice": passport.NOTICES[-1]}


@app.get("/v1/passports/{passport_id}/download")
def download_passport(passport_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> Response:
    from reclaim_api import passport

    try:
        data, pv = passport.download(session, actor, passport_id, store=get_providers().object_storage)
    except passport.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    session.commit()
    name = f"family-asset-passport-v{pv.version}{'' if pv.status == 'current' else '-stale'}.pdf"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Passport-Status": pv.status,
    })  # fmt: skip


# --- handover, reviewers and family (Prompt 11) ---------------------------------------------------


class HandoverCreate(BaseModel):
    owner_user_id: uuid.UUID


class DecisionBody(BaseModel):
    decision: str = Field(pattern="^(approve|reject|hold|needs_information|resume)$")
    note: str | None = Field(default=None, max_length=500)


class FamilyClaim(BaseModel):
    asset_id: uuid.UUID
    survivorship: str = Field(default="unknown", pattern="^(yes|no|unknown)$")
    dispute: str = Field(default="unknown", pattern="^(yes|no|unknown)$")


def _handover_errors(fn):
    from reclaim_api import handover

    try:
        return fn()
    except handover.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except handover.Forbidden as err:
        raise HTTPException(status_code=403, detail=str(err)) from None
    except handover.Conflict as err:
        raise HTTPException(status_code=409, detail={"code": err.code, "message": err.args[0]}) from None


@app.post("/v1/handovers", status_code=201)
def create_handover(body: HandoverCreate, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import handover

    result = _handover_errors(lambda: handover.request(session, actor, body.owner_user_id))
    session.commit()
    return result


@app.get("/v1/handovers/{handover_id}")
def get_handover(handover_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import handover

    p = get_providers()
    result = _handover_errors(lambda: handover.view(session, actor, handover_id, store=p.object_storage, llm=p.llm))
    session.commit()
    return result


@app.post("/v1/handovers/{handover_id}/evidence")
async def add_handover_evidence(handover_id: uuid.UUID, actor: CurrentActor, session: DbSession, file: Annotated[UploadFile, File()]) -> dict:
    from reclaim_api import handover

    content = await file.read(documents.MAX_BYTES + 1)

    def work() -> dict:
        h = session.get(m.HandoverRequest, handover_id)
        if h is None or h.requester_user_id != actor.user_id or h.tenant_id != actor.tenant_id:
            raise handover.NotFound
        try:
            doc = documents.intake(session, actor, content, file.filename, store=get_providers().object_storage, roles=("family",))
        except documents.Rejected as err:
            raise HTTPException(status_code=err.status, detail=err.args[0]) from None
        return handover.add_evidence(session, actor, handover_id, doc.id)

    result = await run_in_threadpool(lambda: _handover_errors(work))
    session.commit()
    return result


@app.post("/v1/handovers/{handover_id}/submit")
def submit_handover(handover_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import handover

    result = _handover_errors(lambda: handover.submit(session, actor, handover_id))
    session.commit()
    return result


@app.post("/v1/handovers/{handover_id}/decisions")
def decide_handover(handover_id: uuid.UUID, body: DecisionBody, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import handover

    try:
        result = _handover_errors(lambda: handover.decide(session, actor, handover_id, body.decision, body.note))
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "already_approved", "message": "this approval was already recorded"}) from None
    return result


@app.get("/v1/reviews")
def reviewer_queue(actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import handover

    return {"cases": _handover_errors(lambda: handover.reviewer_queue(session, actor)), "simulated_verification": handover.SIMULATED}


@app.get("/v1/family")
def family_overview(actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import family

    try:
        result = family.overview(session, actor)
    except family.Forbidden:
        raise HTTPException(status_code=403, detail="for designated family members") from None
    session.commit()
    return result


@app.get("/v1/family/assets/{asset_id}")
def family_asset(asset_id: uuid.UUID, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import family

    try:
        return family.released_asset(session, actor, asset_id)
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None


@app.post("/v1/family/claims")
def family_claim(body: FamilyClaim, actor: CurrentActor, session: DbSession) -> dict:
    from reclaim_api import family, handover

    try:
        asset = family.released_asset(session, actor, body.asset_id)
    except records.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    case = handover.released_for(session, actor, uuid.UUID(str(session.get(m.Asset, uuid.UUID(asset["id"])).owner_user_id)))
    result = copilot.create_case(session, actor, body.asset_id, survivorship=body.survivorship, dispute=body.dispute,
                                 retriever=get_providers().retrieval, llm=get_providers().llm, today=_today(),
                                 resolve=family.released_asset, handover_id=case.id if case else None)  # fmt: skip
    session.commit()
    return result


# --- optional dormant-asset recovery (Prompt 13) ---------------------------------------------------------


class RecoverySearch(BaseModel):
    # No name or other search terms: the subject always comes from the actor or the case.
    model_config = ConfigDict(extra="forbid")
    case_id: uuid.UUID | None = None


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(not_mine|open_claim)$")
    case_id: uuid.UUID | None = None


def _recovery(fn, session: Session) -> dict:
    from reclaim_api.recovery import service

    if not settings.recovery_enabled:
        raise HTTPException(status_code=404, detail="not found")
    try:
        result = fn(service)
    except service.NotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except service.Forbidden as err:
        raise HTTPException(status_code=403, detail=str(err)) from None
    except service.Conflict as err:
        raise HTTPException(status_code=409, detail={"code": err.code, "message": err.args[0]}) from None
    session.commit()
    return result


@app.get("/v1/recovery")
def recovery_view(actor: CurrentActor, session: DbSession, case: uuid.UUID | None = None) -> dict:
    return _recovery(lambda svc: svc.view(session, actor, case), session)


@app.post("/v1/recovery/opt-in")
def recovery_opt_in(actor: CurrentActor, session: DbSession) -> dict:
    return _recovery(lambda svc: svc.opt_in(session, actor), session)


@app.post("/v1/recovery/opt-out")
def recovery_opt_out(actor: CurrentActor, session: DbSession) -> dict:
    return _recovery(lambda svc: svc.opt_out(session, actor), session)


@app.post("/v1/recovery/search")
def recovery_search(body: RecoverySearch, actor: CurrentActor, session: DbSession) -> dict:
    return _recovery(lambda svc: svc.search(session, actor, body.case_id, llm=get_providers().llm), session)


@app.post("/v1/recovery/matches/{match_id}/decision")
def recovery_decision(match_id: uuid.UUID, body: RecoveryDecision, actor: CurrentActor, session: DbSession) -> dict:
    return _recovery(lambda svc: svc.decide(session, actor, match_id, body.decision, body.case_id), session)

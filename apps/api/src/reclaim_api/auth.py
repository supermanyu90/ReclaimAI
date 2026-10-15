"""Actor resolution. Every request handler gets an Actor from here, never from the body.

Local mode (and the gated hosted demo) accepts `Authorization: Bearer dev:<user-uuid>` for
users of synthetic tenants only, standing in for the identity provider. Cloud mode accepts only Identity
Platform ID tokens (verified signature, audience and issuer) and never falls back to dev.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.config import get_settings, synthetic_signin
from reclaim_api.db import get_engine


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


DbSession = Annotated[Session, Depends(get_session)]


def verify_identity_token(token: str, project: str) -> dict:
    """Identity Platform (Firebase Auth) ID token: signature, expiry, audience and issuer."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_firebase_token(token, google_requests.Request(), audience=project)


def _cloud_actor(session: Session, token: str) -> Actor:
    from google.auth.exceptions import GoogleAuthError

    project = get_settings().gcp_project
    if not project:
        raise _unauthorized("identity provider not configured")
    try:
        claims = verify_identity_token(token, project)
    except (GoogleAuthError, ValueError):
        raise _unauthorized("missing or invalid credentials") from None
    if not claims or claims.get("iss") != f"https://securetoken.google.com/{project}":
        raise _unauthorized("missing or invalid credentials")
    sub = claims.get("sub")
    user = session.scalar(select(m.User).where(m.User.identity_subject == sub)) if sub else None
    if user is None and sub and claims.get("email_verified") is True and claims.get("email"):
        # First sign-in: link an invited user by verified email, exactly once.
        candidates = session.scalars(select(m.User).where(func.lower(m.User.email) == claims["email"].lower(), m.User.identity_subject.is_(None))).all()
        if len(candidates) == 1:
            user = candidates[0]
            user.identity_subject = sub
            session.commit()
    if user is None:
        raise _unauthorized("missing or invalid credentials")
    return Actor(user_id=user.id, tenant_id=user.tenant_id, role=user.role)


def get_actor(session: DbSession, authorization: Annotated[str | None, Header()] = None) -> Actor:
    scheme, _, token = (authorization or "").partition(" ")
    if not synthetic_signin(get_settings()):
        if scheme.lower() != "bearer" or not token or token.startswith("dev:"):
            raise _unauthorized("missing or invalid credentials")
        return _cloud_actor(session, token)
    if scheme.lower() != "bearer" or not token.startswith("dev:"):
        raise _unauthorized("missing or invalid credentials")
    try:
        user_id = uuid.UUID(token.removeprefix("dev:"))
    except ValueError:
        raise _unauthorized("missing or invalid credentials") from None
    row = session.execute(
        select(m.User.id, m.User.tenant_id, m.User.role)
        .join(m.Tenant, m.Tenant.id == m.User.tenant_id)
        .where(m.User.id == user_id, m.Tenant.is_synthetic.is_(True))
    ).one_or_none()
    if row is None:
        raise _unauthorized("missing or invalid credentials")
    return Actor(user_id=row.id, tenant_id=row.tenant_id, role=row.role)


CurrentActor = Annotated[Actor, Depends(get_actor)]

"""Linking a private Telegram chat to an already-authenticated web user.

The web app issues a short single-use code to a signed-in owner; the owner sends
`/link CODE` from their private chat. Ownership is never inferred from a Telegram
username, display name or phone number. Codes are stored hashed, expire, and are
consumed atomically so a replayed code fails.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.auth import Actor
from reclaim_api.config import Settings

# No 0/O/1/I/L: codes are read off a screen and typed on a phone.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8
_LOCAL_DEV_KEY = "reclaim-local-dev-only-channel-hash-key"


class LinkError(RuntimeError):
    pass


def hash_key(s: Settings) -> bytes:
    if s.channel_hash_key:
        return s.channel_hash_key.encode()
    if s.app_mode == "local":
        return _LOCAL_DEV_KEY.encode()
    raise LinkError("CHANNEL_HASH_KEY is required outside local mode")


def chat_hash(chat_id: int, s: Settings) -> str:
    """Keyed hash of a chat ID. Plain SHA-256 of a ~10-digit number is brute-forceable."""
    return hmac.new(hash_key(s), f"telegram:{chat_id}".encode(), hashlib.sha256).hexdigest()


def _code_hash(code: str) -> str:
    return hashlib.sha256(f"link:{code.strip().upper()}".encode()).hexdigest()


def issue_code(session: Session, actor: Actor, s: Settings) -> tuple[str, datetime]:
    if actor.role != "owner":
        raise PermissionError("linking is available to asset owners")
    now = session.scalar(select(func.now()))
    # Outstanding codes for this user are retired: only the newest one works.
    session.execute(
        update(m.LinkCode).where(m.LinkCode.user_id == actor.user_id, m.LinkCode.used_at.is_(None)).values(expires_at=now)
    )
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    expires = now + timedelta(minutes=s.link_code_ttl_min)
    session.add(m.LinkCode(tenant_id=actor.tenant_id, user_id=actor.user_id, channel="telegram", code_hash=_code_hash(code), expires_at=expires))
    session.flush()
    return code, expires


def redeem_code(session: Session, code: str, chat_id_hash: str) -> m.User:
    """Consume a code and link the chat. Raises LinkError for unknown, used or expired codes
    without saying which, so a guesser learns nothing."""
    if len(code.strip()) != CODE_LENGTH:
        raise LinkError("invalid")
    row = session.scalar(
        update(m.LinkCode)
        .where(m.LinkCode.code_hash == _code_hash(code), m.LinkCode.used_at.is_(None), m.LinkCode.expires_at > func.now())
        .values(used_at=func.now())
        .returning(m.LinkCode)
    )
    if row is None:
        raise LinkError("invalid")
    user = session.get(m.User, row.user_id)
    if user is None or user.role != "owner":
        raise LinkError("invalid")
    # One active link per chat and per user.
    session.execute(
        update(m.ChannelLink)
        .where(
            m.ChannelLink.channel == "telegram",
            m.ChannelLink.status == "active",
            (m.ChannelLink.chat_id_hash == chat_id_hash) | (m.ChannelLink.user_id == user.id),
        )
        .values(status="unlinked", unlinked_at=func.now())
    )
    session.add(m.ChannelLink(id=uuid.uuid4(), tenant_id=user.tenant_id, user_id=user.id, channel="telegram", chat_id_hash=chat_id_hash))
    session.add(m.AuditEvent(tenant_id=user.tenant_id, actor_user_id=user.id, action="channel.linked", object_type="channel_link", details={"channel": "telegram"}))
    session.flush()
    return user


def linked_actor(session: Session, chat_id_hash: str) -> Actor | None:
    row = session.execute(
        select(m.User.id, m.User.tenant_id, m.User.role)
        .join(m.ChannelLink, (m.ChannelLink.user_id == m.User.id) & (m.ChannelLink.tenant_id == m.User.tenant_id))
        .where(m.ChannelLink.channel == "telegram", m.ChannelLink.status == "active", m.ChannelLink.chat_id_hash == chat_id_hash)
    ).one_or_none()
    return Actor(user_id=row.id, tenant_id=row.tenant_id, role=row.role) if row else None


def unlink_chat(session: Session, chat_id_hash: str) -> bool:
    actor = linked_actor(session, chat_id_hash)
    if actor is None:
        return False
    _unlink(session, actor, m.ChannelLink.chat_id_hash == chat_id_hash)
    return True


def unlink_user(session: Session, actor: Actor) -> bool:
    return _unlink(session, actor, m.ChannelLink.user_id == actor.user_id) > 0


def _unlink(session: Session, actor: Actor, condition) -> int:
    result = session.execute(
        update(m.ChannelLink)
        .where(m.ChannelLink.channel == "telegram", m.ChannelLink.status == "active", m.ChannelLink.tenant_id == actor.tenant_id, condition)
        .values(status="unlinked", unlinked_at=func.now())
    )
    if result.rowcount:
        session.add(m.AuditEvent(tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="channel.unlinked", object_type="channel_link", details={"channel": "telegram"}))
    session.flush()
    return result.rowcount


def link_status(session: Session, actor: Actor) -> dict:
    link = session.scalar(
        select(m.ChannelLink).where(
            m.ChannelLink.tenant_id == actor.tenant_id, m.ChannelLink.user_id == actor.user_id,
            m.ChannelLink.channel == "telegram", m.ChannelLink.status == "active",
        )
    )  # fmt: skip
    return {"linked": link is not None, "linked_at": link.linked_at.isoformat() if link else None}

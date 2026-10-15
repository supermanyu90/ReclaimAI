"""Database engine and readiness probe. PostgreSQL is the operational source of truth."""

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from reclaim_api.config import get_settings
from reclaim_api.lib.log import error_tag


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


@dataclass(frozen=True)
class DatabaseStatus:
    ok: bool
    revision: str | None
    detail: str


def check_database() -> DatabaseStatus:
    try:
        with get_engine().connect() as conn:
            has_table = conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar()
            if not has_table:
                return DatabaseStatus(False, None, "connected; migrations not applied")
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            return DatabaseStatus(True, revision, "connected")
    except SQLAlchemyError as err:
        # error_tag only: driver messages can echo the connection string.
        return DatabaseStatus(False, None, f"unreachable ({error_tag(err)})")

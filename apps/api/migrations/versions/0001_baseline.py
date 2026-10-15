"""Baseline: extensions the operational schema depends on.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() for UUID primary keys (Prompt 2). Available on Cloud SQL.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")

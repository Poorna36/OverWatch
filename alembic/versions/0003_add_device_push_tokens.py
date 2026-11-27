"""Add device_push_tokens table for encrypted F-06 push notifications.

Revision ID: 0003_add_device_push_tokens
Revises: 0002_add_asked_audit_decision
Create Date: 2026-11-27 19:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_device_push_tokens"
down_revision: str | None = "0002_add_asked_audit_decision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_push_tokens",
        sa.Column("token_id", sa.String(), nullable=False, primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=True),
    )
    op.create_index(
        "idx_device_push_tokens_owner",
        "device_push_tokens",
        ["owner_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_device_push_tokens_owner", table_name="device_push_tokens")
    op.drop_table("device_push_tokens")

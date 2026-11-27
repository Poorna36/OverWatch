"""Add connections table for F-18 encrypted connector vault storage.

Revision ID: 0006_add_connections_table
Revises: 0005_add_undo_records_table
Create Date: 2026-11-27 21:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_add_connections_table"
down_revision: str | None = "0005_add_undo_records_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("connection_id", sa.String(), nullable=False, primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("token_mode", sa.String(), nullable=False, server_default="linked"),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "token_mode IN ('session', 'linked')",
            name="ck_connections_token_mode",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_connections_status",
        ),
    )
    op.create_index(
        "idx_connections_owner_provider",
        "connections",
        ["owner_id", "provider", "status"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn_count = conn.execute(sa.text("SELECT COUNT(*) FROM connections")).scalar()
    if conn_count and conn_count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0006: populated data exists "
            f"({conn_count} connections). Active connections must be revoked first."
        )
    op.drop_index("idx_connections_owner_provider", table_name="connections")
    op.drop_table("connections")

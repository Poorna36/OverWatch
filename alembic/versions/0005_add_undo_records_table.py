"""Add undo_records table for F-13 reversible operations.

Revision ID: 0005_add_undo_records_table
Revises: 0004_add_plans_table
Create Date: 2026-11-27 21:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_add_undo_records_table"
down_revision: str | None = "0004_add_plans_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "undo_records",
        sa.Column("undo_id", sa.String(), nullable=False, primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
        ),
        sa.Column("audit_seq", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("inverse_tool", sa.String(), nullable=False),
        sa.Column("target_path", sa.String(), nullable=False),
        sa.Column("prior_exists", sa.Integer(), nullable=False),
        sa.Column("written_sha256", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="available"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "state IN ('available', 'used', 'expired')",
            name="ck_undo_records_state",
        ),
    )
    op.create_index(
        "idx_undo_records_session",
        "undo_records",
        ["session_id", "audit_seq"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    undo_count = conn.execute(sa.text("SELECT COUNT(*) FROM undo_records")).scalar()
    if undo_count and undo_count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0005: populated data exists "
            f"({undo_count} undo records). Undo history is retained."
        )
    op.drop_index("idx_undo_records_session", table_name="undo_records")
    op.drop_table("undo_records")

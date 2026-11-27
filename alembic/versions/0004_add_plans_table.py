"""Add plans table for F-11 plan preview and approval.

Revision ID: 0004_add_plans_table
Revises: 0003_add_device_push_tokens
Create Date: 2026-11-27 20:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_add_plans_table"
down_revision: str | None = "0003_add_device_push_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.String(), nullable=False, primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("est_budget_usd", sa.Float(), nullable=True),
        sa.Column("plan_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved')", name="ck_plans_status"),
    )
    op.create_index("idx_plans_session", "plans", ["session_id"])


def downgrade() -> None:
    conn = op.get_bind()
    plan_count = conn.execute(sa.text("SELECT COUNT(*) FROM plans")).scalar()
    if plan_count and plan_count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0004: populated data exists "
            f"({plan_count} plans). Plan records are permanently retained."
        )
    op.drop_index("idx_plans_session", table_name="plans")
    op.drop_table("plans")

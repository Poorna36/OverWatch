"""Initial core tables for sessions, audit, events, and approvals.

Revision ID: 0001_initial_core_tables
Revises:
Create Date: 2026-11-26 15:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_core_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. sessions table
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("dry_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("container_id", sa.String(), nullable=True),
        sa.Column("container_ip", sa.String(), nullable=True),
        sa.Column("receipt_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "state IN ('CREATED', 'PLANNING', 'AWAITING_PLAN_APPROVAL', "
            "'STARTING', 'ACTIVE', 'PAUSED', 'ENDING', 'ENDED', 'FAILED')",
            name="ck_sessions_state",
        ),
    )

    # 2. audit_entries table
    op.create_table(
        "audit_entries",
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("ts", sa.String(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("args_redacted", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("undo_id", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("prev_hash", sa.String(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('allowed', 'asked_approved', 'asked_denied', "
            "'blocked', 'dry_run')",
            name="ck_audit_entries_decision",
        ),
    )
    op.create_index(
        "idx_audit_lookup",
        "audit_entries",
        ["session_id", "decision", "seq"],
    )

    # 3. events table
    op.create_table(
        "events",
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("ts", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_events_replay",
        "events",
        ["session_id", "seq"],
    )

    # 4. approvals table
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("args_redacted", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("irreversible", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'timed_out')",
            name="ck_approvals_status",
        ),
    )

    # 5. Immutability triggers on audit_entries and sessions
    op.execute("""
        CREATE TRIGGER sessions_prevent_delete
        BEFORE DELETE ON sessions
        BEGIN
            SELECT RAISE(ABORT, 'Session records are permanent: DELETE not allowed');
        END;
        """)
    op.execute("""
        CREATE TRIGGER audit_entries_prevent_update
        BEFORE UPDATE ON audit_entries
        BEGIN
            SELECT RAISE(ABORT, 'Audit log entries are immutable: UPDATE not allowed');
        END;
        """)
    op.execute("""
        CREATE TRIGGER audit_entries_prevent_delete
        BEFORE DELETE ON audit_entries
        BEGIN
            SELECT RAISE(ABORT, 'Audit log entries are immutable: DELETE not allowed');
        END;
        """)


def downgrade() -> None:
    conn = op.get_bind()

    # Guard against dropping populated tables to enforce retention guarantees
    session_count = conn.execute(sa.text("SELECT COUNT(*) FROM sessions")).scalar()
    audit_count = conn.execute(sa.text("SELECT COUNT(*) FROM audit_entries")).scalar()

    if (session_count and session_count > 0) or (audit_count and audit_count > 0):
        raise RuntimeError(
            f"Cannot downgrade migration 0001: populated data exists "
            f"({session_count} sessions, {audit_count} audit entries). "
            f"Audit and session records are permanently retained."
        )

    op.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_delete;")
    op.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_update;")
    op.execute("DROP TRIGGER IF EXISTS sessions_prevent_delete;")
    op.drop_table("approvals")
    op.drop_index("idx_events_replay", table_name="events")
    op.drop_table("events")
    op.drop_index("idx_audit_lookup", table_name="audit_entries")
    op.drop_table("audit_entries")
    op.drop_table("sessions")

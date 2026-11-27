"""Add 'asked' to AuditDecision CHECK constraint on audit_entries.

Revision ID: 0002_add_asked_audit_decision
Revises: 0001_initial_core_tables
Create Date: 2026-11-26 22:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_asked_audit_decision"
down_revision: str | None = "0001_initial_core_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_DECISIONS = (
    "allowed",
    "asked",
    "asked_approved",
    "asked_denied",
    "blocked",
    "dry_run",
)
_DOWNGRADE_DECISIONS = (
    "allowed",
    "asked_approved",
    "asked_denied",
    "blocked",
    "dry_run",
)


def _rebuild_audit_entries(allowed_decisions: tuple[str, ...]) -> None:
    """Rebuild audit_entries with updated decision CHECK constraint cleanly."""
    decision_list = ", ".join(f"'{d}'" for d in allowed_decisions)

    op.create_table(
        "audit_entries_new",
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
            f"decision IN ({decision_list})",
            name="ck_audit_entries_decision",
        ),
    )

    op.execute("""
        INSERT INTO audit_entries_new (
            session_id, seq, ts, tool, args_redacted, decision,
            reason, tier, undo_id, duration_ms, prev_hash, hash
        )
        SELECT
            session_id, seq, ts, tool, args_redacted, decision,
            reason, tier, undo_id, duration_ms, prev_hash, hash
        FROM audit_entries
        ORDER BY session_id, seq;
        """)

    op.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_delete;")
    op.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_update;")
    op.drop_index("idx_audit_lookup", table_name="audit_entries")
    op.drop_table("audit_entries")
    op.rename_table("audit_entries_new", "audit_entries")

    op.create_index(
        "idx_audit_lookup",
        "audit_entries",
        ["session_id", "decision", "seq"],
    )
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


def upgrade() -> None:
    _rebuild_audit_entries(_UPGRADE_DECISIONS)


def downgrade() -> None:
    conn = op.get_bind()
    asked_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM audit_entries WHERE decision = 'asked'")
    ).scalar()
    if asked_count and asked_count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0002: {asked_count} audit_entries row(s) "
            f"have decision='asked'. Audit history cannot be altered or discarded."
        )
    _rebuild_audit_entries(_DOWNGRADE_DECISIONS)

"""SQLAlchemy declarative models for OverWatch core database tables (Phase 0).

Tables implemented:
- sessions: session lifecycle state and caller ownership
- audit_entries: immutable, hash-chained audit decisions
- events: sequenced WebSocket event stream for replay
- approvals: pending and resolved human-in-the-loop approval cards
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative class for all OverWatch models."""


class SessionModel(Base):
    """Tracks session lifecycle state, environment profile binding, and ownership.

    Session records are permanent and protected from DELETE by database trigger.
    """

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(
        String,
        CheckConstraint(
            "state IN ('CREATED', 'PLANNING', 'AWAITING_PLAN_APPROVAL', "
            "'STARTING', 'ACTIVE', 'PAUSED', 'ENDING', 'ENDED', 'FAILED')"
        ),
        nullable=False,
    )
    profile: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    dry_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    container_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    # Relationships
    audit_entries: Mapped[list[AuditEntryModel]] = relationship(
        "AuditEntryModel", back_populates="session"
    )
    events: Mapped[list[EventModel]] = relationship(
        "EventModel", back_populates="session"
    )
    approvals: Mapped[list[ApprovalModel]] = relationship(
        "ApprovalModel", back_populates="session"
    )


class AuditEntryModel(Base):
    """Stores the append-only, hash-chained audit log for all gateway decisions."""

    __tablename__ = "audit_entries"

    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.session_id"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    args_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(
        String,
        CheckConstraint(
            "decision IN ('allowed', 'asked', 'asked_approved', 'asked_denied', "
            "'blocked', 'dry_run')"
        ),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str | None] = mapped_column(String, nullable=True)
    undo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)

    session: Mapped[SessionModel] = relationship(
        "SessionModel", back_populates="audit_entries"
    )

    __table_args__ = (Index("idx_audit_lookup", "session_id", "decision", "seq"),)


class EventModel(Base):
    """Stores sequenced WebSocket event frames for reconnect replay."""

    __tablename__ = "events"

    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.session_id"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped[SessionModel] = relationship(
        "SessionModel", back_populates="events"
    )

    __table_args__ = (Index("idx_events_replay", "session_id", "seq"),)


class ApprovalModel(Base):
    """Tracks pending and historical human-in-the-loop approval cards."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.session_id"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String, nullable=False)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    args_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    irreversible: Mapped[int] = mapped_column(Integer, nullable=False)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        CheckConstraint("status IN ('pending', 'approved', 'denied', 'timed_out')"),
        nullable=False,
        default="pending",
    )
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(String, nullable=True)

    session: Mapped[SessionModel] = relationship(
        "SessionModel", back_populates="approvals"
    )


class DevicePushTokenModel(Base):
    """Encrypted device push tokens associated with an authenticated owner."""

    __tablename__ = "device_push_tokens"

    token_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (Index("idx_device_push_tokens_owner", "owner_id", "revoked_at"),)


class PlanModel(Base):
    """Persisted F-11 plan preview and approval status."""

    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.session_id"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[str] = mapped_column(String, nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    est_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        CheckConstraint("status IN ('pending', 'approved')"),
        nullable=False,
        default="pending",
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_plans_session", "session_id"),)


class UndoRecordModel(Base):
    """Persisted F-13 undo metadata (never stores raw file bodies)."""

    __tablename__ = "undo_records"

    undo_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.session_id"), nullable=False
    )
    audit_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    inverse_tool: Mapped[str] = mapped_column(String, nullable=False)
    target_path: Mapped[str] = mapped_column(String, nullable=False)
    prior_exists: Mapped[int] = mapped_column(Integer, nullable=False)
    written_sha256: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(
        String,
        CheckConstraint("state IN ('available', 'used', 'expired')"),
        nullable=False,
        default="available",
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_undo_records_session", "session_id", "audit_seq"),)


class ConnectionModel(Base):
    """Encrypted OAuth connector credentials stored via VAULT_KEY (F-18)."""

    __tablename__ = "connections"

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    token_mode: Mapped[str] = mapped_column(
        String,
        CheckConstraint("token_mode IN ('session', 'linked')"),
        nullable=False,
        default="linked",
    )
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        CheckConstraint("status IN ('active', 'revoked')"),
        nullable=False,
        default="active",
    )
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_connections_owner_provider", "owner_id", "provider", "status"),
    )

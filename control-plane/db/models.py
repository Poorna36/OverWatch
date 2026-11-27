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

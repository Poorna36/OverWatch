"""Persistent sequenced event bus and replay (Phase 4, F-10).

Publishes validated ``EventEnvelope`` frames into the SQLite ``events`` table
with monotonically increasing per-session sequence numbers and supports
ordered replay from ``last_seq``.

Payload sanitization reuses ``control_plane.audit.audit_writer`` helpers so
persisted event payloads never retain secret keys, full file/email bodies,
non-finite floats, or strings exceeding 200 characters.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import Any, get_args

from control_plane.audit.audit_writer import (
    AuditWriterError,
    _begin_immediate_if_sqlite,
    canonical_json,
    format_utc_iso,
    sanitize_audit_args,
)
from control_plane.contracts import EventEnvelope, EventType
from control_plane.db.models import EventModel, SessionModel
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

VALID_EVENT_TYPES: frozenset[str] = frozenset(get_args(EventType))


class EventBusError(Exception):
    """Raised when an event cannot be validated, persisted, or replayed safely."""


def _sanitize_event_data(data: Mapping[str, Any] | BaseModel | None) -> dict[str, Any]:
    """Validate and sanitize event payload data for persistent storage."""
    if isinstance(data, BaseModel):
        raw_mapping: Mapping[str, Any] = data.model_dump()
    elif data is None:
        raw_mapping = {}
    elif isinstance(data, Mapping):
        raw_mapping = data
    else:
        raise EventBusError(
            f"Event data must be a mapping, BaseModel, or None, "
            f"got {type(data).__name__}."
        )

    try:
        return sanitize_audit_args(raw_mapping)
    except AuditWriterError as exc:
        raise EventBusError(f"Invalid event payload: {exc}") from exc


def publish_event(
    db: Session,
    *,
    session_id: str,
    event_type: EventType,
    data: Mapping[str, Any] | BaseModel | None = None,
    ts: str | None = None,
    commit: bool = True,
) -> EventEnvelope[dict[str, Any]]:
    """Persist a sanitized event for ``session_id`` and return its ``EventEnvelope``.

    Assigns ``seq = last_seq + 1`` (starting at ``1``) independently per session.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise EventBusError("session_id must be a non-empty string.")
    if not isinstance(event_type, str) or event_type not in VALID_EVENT_TYPES:
        raise EventBusError(f"Invalid event_type: {event_type!r}.")

    event_ts = format_utc_iso() if ts is None else ts
    if not isinstance(event_ts, str) or not event_ts.strip():
        raise EventBusError("ts must be a non-empty UTC ISO 8601 string.")

    sanitized_data = _sanitize_event_data(data)
    try:
        data_json = canonical_json(sanitized_data)
    except (TypeError, ValueError) as exc:
        raise EventBusError(f"Event payload is not valid JSON: {exc}") from exc

    try:
        if commit:
            _begin_immediate_if_sqlite(db)

        last_seq = db.scalar(
            select(EventModel.seq)
            .where(EventModel.session_id == session_id)
            .order_by(EventModel.seq.desc())
            .limit(1)
        )

        if last_seq is None:
            session_exists = db.scalar(
                select(SessionModel.session_id).where(
                    SessionModel.session_id == session_id
                )
            )
            if session_exists is None:
                raise EventBusError(
                    f"Cannot publish event: session '{session_id}' does not exist."
                )
            next_seq = 1
        else:
            next_seq = last_seq + 1

        envelope = EventEnvelope[dict[str, Any]].model_validate(
            {
                "seq": next_seq,
                "session_id": session_id,
                "ts": event_ts,
                "type": event_type,
                "data": sanitized_data,
            }
        )

        db_row = EventModel(
            session_id=envelope.session_id,
            seq=envelope.seq,
            ts=envelope.ts,
            type=envelope.type,
            data=data_json,
        )
        db.add(db_row)
        db.flush()
        if commit:
            db.commit()
        return envelope

    except EventBusError:
        if commit:
            db.rollback()
        raise
    except (SQLAlchemyError, ValidationError, ValueError, TypeError) as exc:
        if commit:
            db.rollback()
        raise EventBusError(
            f"Failed to publish event for session '{session_id}': {exc}"
        ) from exc


def replay_events(
    db: Session,
    *,
    session_id: str,
    last_seq: int = 0,
) -> list[EventEnvelope[dict[str, Any]]]:
    """Return persisted events for ``session_id`` with ``seq > last_seq`` ascending."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise EventBusError("session_id must be a non-empty string.")
    if isinstance(last_seq, bool) or not isinstance(last_seq, int) or last_seq < 0:
        raise EventBusError(
            f"last_seq must be a non-negative integer, got {last_seq!r}."
        )

    try:
        session_exists = db.scalar(
            select(SessionModel.session_id).where(SessionModel.session_id == session_id)
        )
        if session_exists is None:
            raise EventBusError(
                f"Cannot replay events: session '{session_id}' does not exist."
            )

        rows = db.scalars(
            select(EventModel)
            .where(EventModel.session_id == session_id, EventModel.seq > last_seq)
            .order_by(EventModel.seq.asc())
        ).all()
    except EventBusError:
        raise
    except SQLAlchemyError as exc:
        raise EventBusError(
            f"Failed to replay events for session '{session_id}': {exc}"
        ) from exc

    envelopes: list[EventEnvelope[dict[str, Any]]] = []
    for row in rows:
        try:
            parsed_data = json.loads(row.data)
        except (json.JSONDecodeError, TypeError) as exc:
            raise EventBusError(
                f"Malformed JSON in persisted event ({session_id}, seq={row.seq}): "
                f"{exc}"
            ) from exc

        if not isinstance(parsed_data, dict):
            raise EventBusError(
                f"Persisted event data must be a JSON object "
                f"({session_id}, seq={row.seq})."
            )

        try:
            envelope = EventEnvelope[dict[str, Any]].model_validate(
                {
                    "seq": row.seq,
                    "session_id": row.session_id,
                    "ts": row.ts,
                    "type": row.type,
                    "data": parsed_data,
                }
            )
        except ValidationError as exc:
            raise EventBusError(
                f"Malformed persisted event ({session_id}, seq={row.seq}): {exc}"
            ) from exc

        envelopes.append(envelope)

    return envelopes


class EventBus:
    """Persistent, thread-safe event bus bound to a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._write_lock = threading.Lock()

    def publish(
        self,
        *,
        session_id: str,
        event_type: EventType,
        data: Mapping[str, Any] | BaseModel | None = None,
        ts: str | None = None,
    ) -> EventEnvelope[dict[str, Any]]:
        """Publish and persist an event in its own transaction."""
        with self._write_lock:
            with self._session_factory() as db:
                return publish_event(
                    db,
                    session_id=session_id,
                    event_type=event_type,
                    data=data,
                    ts=ts,
                    commit=True,
                )

    def replay(
        self,
        *,
        session_id: str,
        last_seq: int = 0,
    ) -> list[EventEnvelope[dict[str, Any]]]:
        """Replay persisted events for ``session_id`` with ``seq > last_seq``."""
        with self._session_factory() as db:
            return replay_events(db, session_id=session_id, last_seq=last_seq)


PersistentEventBus = EventBus

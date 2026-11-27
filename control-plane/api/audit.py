"""Phase 4 audit REST routes (F-07).

Implements:
- ``GET /api/v1/sessions/{session_id}/audit?decision=&after_seq=``
- ``GET /api/v1/sessions/{session_id}/receipt``
- ``GET /api/v1/audit/verify/{session_id}``
"""

from __future__ import annotations

import json
from typing import Any, get_args

from control_plane.api.deps import ApiError, get_session_factory, require_owner_id
from control_plane.audit.audit_writer import GENESIS_PREV_HASH
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import AuditDecision, AuditEntry, ErrorCode
from control_plane.db.models import AuditEntryModel, SessionModel
from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(prefix="/api/v1", tags=["audit"])

VALID_AUDIT_DECISIONS: frozenset[str] = frozenset(get_args(AuditDecision))


def _require_owned_session(
    session_factory: sessionmaker[Session],
    session_id: str,
    owner_id: str,
) -> None:
    """Verify ``session_id`` exists and belongs to ``owner_id`` without leaking."""
    try:
        with session_factory() as db:
            found = db.scalar(
                select(SessionModel.session_id).where(
                    SessionModel.session_id == session_id,
                    SessionModel.owner_id == owner_id,
                )
            )
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error looking up session: {exc}",
        ) from exc

    if found is None:
        raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")


def _row_to_audit_entry(row: AuditEntryModel) -> AuditEntry:
    """Deserialize and validate a stored ``AuditEntryModel`` row as ``AuditEntry``."""
    try:
        args_redacted = json.loads(row.args_redacted)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Malformed stored audit entry args_redacted at seq {row.seq}: {exc}",
        ) from exc

    if not isinstance(args_redacted, dict):
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Stored audit entry args_redacted at seq {row.seq} must be a JSON object.",
        )

    try:
        return AuditEntry.model_validate(
            {
                "seq": row.seq,
                "session_id": row.session_id,
                "ts": row.ts,
                "tool": row.tool,
                "args_redacted": args_redacted,
                "decision": row.decision,
                "reason": row.reason,
                "tier": row.tier,
                "undo_id": row.undo_id,
                "duration_ms": row.duration_ms,
                "prev_hash": row.prev_hash,
                "hash": row.hash,
            }
        )
    except ValidationError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Malformed stored audit entry at seq {row.seq}: {exc}",
        ) from exc


def _parse_decision_filter(decision: str | None) -> str | None:
    """Validate optional ``decision`` query parameter against ``AuditDecision``."""
    if decision is None or decision == "":
        return None
    if decision not in VALID_AUDIT_DECISIONS:
        raise ApiError(
            400,
            ErrorCode.ARG_INVALID,
            f"Invalid decision filter {decision!r}.",
        )
    return decision


def _parse_after_seq_cursor(after_seq: str | None) -> int | None:
    """Validate optional ``after_seq`` cursor (non-negative integer)."""
    if after_seq is None or after_seq == "":
        return None
    try:
        parsed = int(after_seq)
    except ValueError as exc:
        raise ApiError(
            400,
            ErrorCode.ARG_INVALID,
            f"after_seq must be a non-negative integer, got {after_seq!r}.",
        ) from exc
    if parsed < 0:
        raise ApiError(
            400,
            ErrorCode.ARG_INVALID,
            f"after_seq must be non-negative, got {parsed}.",
        )
    return parsed


@router.get("/sessions/{session_id}/audit")
def list_session_audit(
    session_id: str,
    decision: str | None = Query(default=None),
    after_seq: str | None = Query(default=None),
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, list[dict[str, Any]]]:
    """Return validated ``AuditEntry`` records for an owned session."""
    _require_owned_session(session_factory, session_id, owner_id)
    valid_decision = _parse_decision_filter(decision)
    cursor = _parse_after_seq_cursor(after_seq)

    try:
        with session_factory() as db:
            stmt = (
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == session_id)
                .order_by(AuditEntryModel.seq.asc())
            )
            if valid_decision is not None:
                stmt = stmt.where(AuditEntryModel.decision == valid_decision)
            if cursor is not None:
                stmt = stmt.where(AuditEntryModel.seq > cursor)
            rows = db.scalars(stmt).all()
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error reading audit entries: {exc}",
        ) from exc

    entries = [_row_to_audit_entry(row).model_dump() for row in rows]
    return {"entries": entries}


@router.get("/sessions/{session_id}/receipt")
def get_session_receipt(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    """Return all audit entries and ``chain_head_hash`` for an owned session."""
    _require_owned_session(session_factory, session_id, owner_id)

    try:
        with session_factory() as db:
            rows = db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == session_id)
                .order_by(AuditEntryModel.seq.asc())
            ).all()
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error building audit receipt: {exc}",
        ) from exc

    validated_entries = [_row_to_audit_entry(row) for row in rows]
    chain_head_hash = (
        validated_entries[-1].hash if validated_entries else GENESIS_PREV_HASH
    )
    return {
        "entries": [entry.model_dump() for entry in validated_entries],
        "chain_head_hash": chain_head_hash,
    }


@router.get("/audit/verify/{session_id}")
def verify_session_audit_route(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    """Run read-only hash-chain verification for an owned session."""
    _require_owned_session(session_factory, session_id, owner_id)

    try:
        with session_factory() as db:
            result = verify_session_audit(db, session_id, require_session_exists=True)
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error verifying audit chain: {exc}",
        ) from exc

    response: dict[str, Any] = {"valid": result.valid}
    if not result.valid and result.first_tampered_seq is not None:
        response["broken_at"] = result.first_tampered_seq
    return response

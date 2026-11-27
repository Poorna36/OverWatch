"""Phase 4 approval REST routes (F-06).

Implements:
- ``GET /api/v1/sessions/{session_id}/approvals?pending=true``
- ``POST /api/v1/sessions/{session_id}/approvals/{approval_id}``
"""

from __future__ import annotations

import json
from typing import Any

from control_plane.api.deps import (
    ApiError,
    get_approval_manager,
    get_session_factory,
    get_session_runner,
    require_owner_id,
)
from control_plane.contracts import ApprovalRequest, ApprovalResponse, ErrorCode
from control_plane.db.models import ApprovalModel, SessionModel
from control_plane.gateway.approval_manager import (
    ApprovalAlreadyResolvedError,
    ApprovalEventPublishError,
    ApprovalManager,
    ApprovalManagerError,
    ApprovalNotFoundError,
    InvalidApprovalIdError,
    validate_approval_id,
)
from control_plane.sessions.session_runner import SessionRunner, SessionRunnerError
from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(prefix="/api/v1", tags=["approvals"])


def _require_owned_session(
    session_factory: sessionmaker[Session],
    session_id: str,
    owner_id: str,
) -> str:
    """Verify ``session_id`` exists and belongs to ``owner_id`` without leaking."""
    with session_factory() as db:
        state = db.scalar(
            select(SessionModel.state).where(
                SessionModel.session_id == session_id,
                SessionModel.owner_id == owner_id,
            )
        )
    if state is None:
        raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")
    return state


def _row_to_approval_request(row: ApprovalModel) -> ApprovalRequest:
    """Convert an ``ApprovalModel`` row into a validated ``ApprovalRequest``."""
    try:
        args_redacted = json.loads(row.args_redacted)
        warnings = None if row.warnings is None else json.loads(row.warnings)
        return ApprovalRequest.model_validate(
            {
                "approval_id": row.approval_id,
                "session_id": row.session_id,
                "call_id": row.call_id,
                "tool": row.tool,
                "tier": row.tier,
                "args_redacted": args_redacted,
                "reason": row.reason,
                "irreversible": bool(row.irreversible),
                "warnings": warnings,
                "expires_at": row.expires_at,
            }
        )
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Malformed stored approval record: {exc}",
        ) from exc


@router.get("/sessions/{session_id}/approvals")
def list_session_approvals(
    session_id: str,
    pending: bool = Query(default=True),
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    approval_manager: ApprovalManager = Depends(get_approval_manager),
    session_runner: SessionRunner = Depends(get_session_runner),
) -> dict[str, list[dict[str, Any]]]:
    """List approvals for an owned session, expiring any due approvals first."""
    _require_owned_session(session_factory, session_id, owner_id)

    try:
        expired = approval_manager.expire_due(session_id=session_id)
    except ApprovalEventPublishError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to publish timeout event: {exc}",
        ) from exc
    except ApprovalManagerError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    for resolved in expired:
        try:
            session_runner.on_approval_resolved(resolved)
        except SessionRunnerError:
            pass

    with session_factory() as db:
        stmt = (
            select(ApprovalModel)
            .where(ApprovalModel.session_id == session_id)
            .order_by(ApprovalModel.expires_at.asc(), ApprovalModel.approval_id.asc())
        )
        if pending:
            stmt = stmt.where(ApprovalModel.status == "pending")
        rows = db.scalars(stmt).all()

    approvals = [_row_to_approval_request(row).model_dump() for row in rows]
    return {"approvals": approvals}


@router.post("/sessions/{session_id}/approvals/{approval_id}")
def resolve_session_approval(
    session_id: str,
    approval_id: str,
    body: ApprovalResponse,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    approval_manager: ApprovalManager = Depends(get_approval_manager),
    session_runner: SessionRunner = Depends(get_session_runner),
) -> dict[str, bool]:
    """Approve or deny a pending approval belonging to an owned session."""
    session_state = _require_owned_session(session_factory, session_id, owner_id)

    try:
        canonical_path_id = validate_approval_id(approval_id)
    except InvalidApprovalIdError as exc:
        raise ApiError(400, ErrorCode.ARG_INVALID, str(exc)) from exc

    if body.approval_id != approval_id and body.approval_id != canonical_path_id:
        raise ApiError(
            400,
            ErrorCode.ARG_INVALID,
            "Body approval_id must match path approval_id.",
        )

    try:
        existing = approval_manager.get_approval(canonical_path_id)
    except ApprovalNotFoundError as exc:
        raise ApiError(404, ErrorCode.POLICY_BLOCK, "Approval not found.") from exc
    except ApprovalManagerError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    if existing.session_id != session_id:
        raise ApiError(404, ErrorCode.POLICY_BLOCK, "Approval not found.")

    if session_state in ("ENDING", "ENDED", "FAILED"):
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Session is not active (state: {session_state}).",
        )

    try:
        result = approval_manager.resolve_approval(
            ApprovalResponse(approval_id=canonical_path_id, approve=body.approve)
        )
    except ApprovalAlreadyResolvedError as exc:
        with session_factory() as db:
            row = db.get(ApprovalModel, canonical_path_id)
            current_status = row.status if row is not None else None
        if current_status == "timed_out":
            raise ApiError(
                409,
                ErrorCode.APPROVAL_TIMEOUT,
                "Approval has already timed out.",
            ) from exc
        raise ApiError(
            409,
            ErrorCode.POLICY_BLOCK,
            "Approval has already been resolved.",
        ) from exc
    except ApprovalNotFoundError as exc:
        raise ApiError(404, ErrorCode.POLICY_BLOCK, "Approval not found.") from exc
    except ApprovalEventPublishError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Approval resolved, but event publishing failed: {exc}",
        ) from exc
    except ApprovalManagerError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    try:
        session_runner.on_approval_resolved(result)
    except SessionRunnerError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc

    if result.timed_out:
        raise ApiError(
            409,
            ErrorCode.APPROVAL_TIMEOUT,
            "Approval timed out before user decision.",
        )

    return {"ok": True}

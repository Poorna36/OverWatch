"""Phase 4 read-only session list and session detail REST routes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import anyio
from control_plane.api.deps import (
    ApiError,
    get_app_token,
    get_container_manager,
    get_event_bus,
    get_public_base_url,
    get_session_factory,
    get_session_runner,
    require_owner_id,
)
from control_plane.api.stream import ACTIVE_STREAM_STATES, get_stream_clock
from control_plane.api.stream_tokens import (
    StreamTokenError,
    issue_stream_token,
    issue_view_token,
    verify_view_token,
)
from control_plane.audit.audit_writer import format_utc_iso
from control_plane.contracts import ErrorCode, SessionSummary
from control_plane.db.models import SessionModel
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.mcp_runtime import McpRuntimeError
from control_plane.sessions.container_manager import (
    ContainerManagerError,
    SessionContainerManager,
)
from control_plane.sessions.session_runner import (
    SessionRunner,
    SessionRunnerError,
    SessionStartupError,
    SessionValidationError,
)
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(prefix="/api/v1", tags=["sessions"])

_WS_SCHEME_MAP: dict[str, str] = {
    "http": "ws",
    "https": "wss",
}
_HTTP_SCHEME_MAP: dict[str, str] = {
    "http": "http",
    "https": "https",
}


def _build_public_session_url(
    public_base_url: str | None,
    *,
    session_id: str,
    suffix: str,
    token: str,
    scheme_map: dict[str, str],
) -> str:
    """Validate ``public_base_url`` and build a tokenized session URL."""
    if not isinstance(public_base_url, str) or not public_base_url.strip():
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "PUBLIC_BASE_URL is not configured.",
        )

    if public_base_url != public_base_url.strip() or any(
        ord(ch) < 0x20 or ch.isspace() for ch in public_base_url
    ):
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "PUBLIC_BASE_URL contains invalid whitespace or control characters.",
        )

    parsed = urlsplit(public_base_url)
    target_scheme = scheme_map.get(parsed.scheme.lower())
    if target_scheme is None or not parsed.netloc or not parsed.hostname:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "PUBLIC_BASE_URL must be an http or https URL with a valid host.",
        )

    if parsed.username is not None or parsed.password is not None:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "PUBLIC_BASE_URL must not contain embedded credentials.",
        )

    if parsed.query or parsed.fragment:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "PUBLIC_BASE_URL must not contain query parameters or fragments.",
        )

    base_path = parsed.path.rstrip("/")
    return (
        f"{target_scheme}://{parsed.netloc}{base_path}"
        f"/api/v1/sessions/{session_id}/{suffix}?token={token}"
    )


def build_stream_ws_url(
    public_base_url: str | None,
    *,
    session_id: str,
    token: str,
) -> str:
    """Validate ``public_base_url`` and build the authenticated WebSocket stream URL."""
    return _build_public_session_url(
        public_base_url,
        session_id=session_id,
        suffix="stream",
        token=token,
        scheme_map=_WS_SCHEME_MAP,
    )


def build_view_url(
    public_base_url: str | None,
    *,
    session_id: str,
    token: str,
) -> str:
    """Validate ``public_base_url`` and build the authenticated live-view URL."""
    return _build_public_session_url(
        public_base_url,
        session_id=session_id,
        suffix="view",
        token=token,
        scheme_map=_HTTP_SCHEME_MAP,
    )


def _parse_created_at_utc(raw: str) -> datetime:
    """Parse stored UTC ISO 8601 ``created_at`` for accurate newest-first sorting."""
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class _CreateSessionRequest(BaseModel):
    """Strict validated request body for ``POST /api/v1/sessions``."""

    model_config = ConfigDict(extra="forbid")

    request_text: str
    profile: str | None = None
    dry_run: StrictBool = False


@router.post("/sessions")
async def create_session_route(
    payload: _CreateSessionRequest,
    owner_id: str = Depends(require_owner_id),
    session_runner: SessionRunner = Depends(get_session_runner),
) -> dict[str, Any]:
    """Create and start a sandboxed session, then launch its AgentLoop off-loop."""
    try:
        started = await anyio.to_thread.run_sync(
            partial(
                session_runner.start_session,
                owner_id=owner_id,
                request_text=payload.request_text,
                profile_name=payload.profile,
                dry_run=payload.dry_run,
            )
        )
    except SessionValidationError as exc:
        status_code = 401 if exc.code == ErrorCode.AUTH_ERROR else 400
        raise ApiError(status_code, exc.code, str(exc)) from exc
    except SessionStartupError as exc:
        raise ApiError(500, exc.code, str(exc)) from exc

    try:
        session_runner.dispatch_agent_run(
            started=started,
            request_text=payload.request_text,
        )
    except SessionRunnerError as exc:
        session_runner.close_runtime(started.session_id)
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    return {
        "session_id": started.session_id,
        "state": started.state,
    }


@router.get("/sessions")
def list_sessions(
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, list[dict[str, Any]]]:
    """List caller-owned sessions as ``SessionSummary`` items, newest first."""
    try:
        with session_factory() as db:
            rows = db.execute(
                select(
                    SessionModel.session_id,
                    SessionModel.state,
                    SessionModel.profile,
                    SessionModel.created_at,
                )
                .where(SessionModel.owner_id == owner_id)
                .order_by(
                    SessionModel.created_at.desc(),
                    SessionModel.session_id.desc(),
                )
            ).all()
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error listing sessions: {exc}",
        ) from exc

    summaries: list[SessionSummary] = []
    for row_session_id, row_state, row_profile, row_created_at in rows:
        try:
            summaries.append(
                SessionSummary(
                    session_id=row_session_id,
                    state=row_state,
                    profile=row_profile,
                    created_at=row_created_at,
                )
            )
        except ValidationError as exc:
            raise ApiError(
                500,
                ErrorCode.UPSTREAM_ERROR,
                f"Malformed stored session record: {exc}",
            ) from exc

    try:
        summaries.sort(
            key=lambda s: (_parse_created_at_utc(s.created_at), s.session_id),
            reverse=True,
        )
    except ValueError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Malformed stored session timestamp: {exc}",
        ) from exc

    return {"sessions": [item.model_dump() for item in summaries]}


@router.get("/sessions/{session_id}")
def get_session_detail(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    configured_token: str | None = Depends(get_app_token),
    public_base_url: str | None = Depends(get_public_base_url),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> dict[str, Any]:
    """Return session metadata and an active-only authenticated ``stream_url``."""
    try:
        with session_factory() as db:
            row = db.scalar(
                select(SessionModel).where(
                    SessionModel.session_id == session_id,
                    SessionModel.owner_id == owner_id,
                )
            )
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error reading session: {exc}",
        ) from exc

    if row is None:
        raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")

    payload: dict[str, Any] = {
        "session_id": row.session_id,
        "state": row.state,
        "profile": row.profile,
        "created_at": row.created_at,
    }

    if row.state in ACTIVE_STREAM_STATES:
        if not configured_token:
            raise ApiError(
                401,
                ErrorCode.AUTH_ERROR,
                "Authentication is not configured.",
            )
        now = clock()
        try:
            stream_token = issue_stream_token(
                session_id=row.session_id,
                owner_id=owner_id,
                signing_key=configured_token,
                now=now,
            )
            view_token = issue_view_token(
                session_id=row.session_id,
                owner_id=owner_id,
                signing_key=configured_token,
                now=now,
            )
        except StreamTokenError as exc:
            raise ApiError(
                500,
                ErrorCode.UPSTREAM_ERROR,
                f"Failed to issue session access token: {exc}",
            ) from exc

        payload["stream_url"] = build_stream_ws_url(
            public_base_url,
            session_id=row.session_id,
            token=stream_token,
        )
        payload["view_url"] = build_view_url(
            public_base_url,
            session_id=row.session_id,
            token=view_token,
        )

    return payload


class _TakeoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


def _load_owned_session_row(
    session_factory: sessionmaker[Session],
    session_id: str,
    owner_id: str,
) -> SessionModel:
    """Fetch a caller-owned ``SessionModel`` row or raise identical 404."""
    try:
        with session_factory() as db:
            row = db.scalar(
                select(SessionModel).where(
                    SessionModel.session_id == session_id,
                    SessionModel.owner_id == owner_id,
                )
            )
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error reading session: {exc}",
        ) from exc

    if row is None:
        raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")
    return row


def _persist_session_state(
    session_factory: sessionmaker[Session],
    *,
    session_id: str,
    owner_id: str,
    new_state: str,
    now_iso: str,
    receipt_url: str | None = None,
) -> None:
    """Persist updated session state (and optional receipt_url)."""
    try:
        with session_factory() as db:
            row = db.scalar(
                select(SessionModel).where(
                    SessionModel.session_id == session_id,
                    SessionModel.owner_id == owner_id,
                )
            )
            if row is None:
                raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")
            row.state = new_state
            row.updated_at = now_iso
            if receipt_url is not None:
                row.receipt_url = receipt_url
            db.commit()
    except ApiError:
        raise
    except SQLAlchemyError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Database error updating session state: {exc}",
        ) from exc


def _publish_session_state(
    event_bus: EventBus,
    *,
    session_id: str,
    state: str,
    ts: str,
    reason: str | None = None,
) -> None:
    """Emit documented ``session_state`` event."""
    data: dict[str, Any] = {"state": state}
    if reason is not None:
        data["reason"] = reason
    try:
        event_bus.publish(
            session_id=session_id,
            event_type="session_state",
            data=data,
            ts=ts,
        )
    except EventBusError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to publish session_state event: {exc}",
        ) from exc


def _pause_owned_session(
    *,
    session_id: str,
    owner_id: str,
    session_factory: sessionmaker[Session],
    container_manager: SessionContainerManager,
    event_bus: EventBus,
    now_iso: str,
    reason: str | None = None,
) -> dict[str, str]:
    """Pause an ACTIVE session (idempotent when already PAUSED)."""
    row = _load_owned_session_row(session_factory, session_id, owner_id)
    if row.state == "PAUSED":
        return {"state": "PAUSED"}
    if row.state != "ACTIVE":
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Cannot pause session in state {row.state}.",
        )

    try:
        container_manager.pause_session(session_id)
    except ContainerManagerError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    _persist_session_state(
        session_factory,
        session_id=session_id,
        owner_id=owner_id,
        new_state="PAUSED",
        now_iso=now_iso,
    )
    _publish_session_state(
        event_bus,
        session_id=session_id,
        state="PAUSED",
        ts=now_iso,
        reason=reason,
    )
    return {"state": "PAUSED"}


def _resume_owned_session(
    *,
    session_id: str,
    owner_id: str,
    session_factory: sessionmaker[Session],
    container_manager: SessionContainerManager,
    event_bus: EventBus,
    now_iso: str,
    reason: str | None = None,
) -> dict[str, str]:
    """Resume a PAUSED session (idempotent when already ACTIVE)."""
    row = _load_owned_session_row(session_factory, session_id, owner_id)
    if row.state == "ACTIVE":
        return {"state": "ACTIVE"}
    if row.state != "PAUSED":
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Cannot resume session in state {row.state}.",
        )

    try:
        container_manager.resume_session(session_id)
    except ContainerManagerError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    _persist_session_state(
        session_factory,
        session_id=session_id,
        owner_id=owner_id,
        new_state="ACTIVE",
        now_iso=now_iso,
    )
    _publish_session_state(
        event_bus,
        session_id=session_id,
        state="ACTIVE",
        ts=now_iso,
        reason=reason,
    )
    return {"state": "ACTIVE"}


@router.post("/sessions/{session_id}/pause")
def pause_session_route(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    container_manager: SessionContainerManager = Depends(get_container_manager),
    event_bus: EventBus = Depends(get_event_bus),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> dict[str, str]:
    """Pause an ACTIVE session and its container."""
    return _pause_owned_session(
        session_id=session_id,
        owner_id=owner_id,
        session_factory=session_factory,
        container_manager=container_manager,
        event_bus=event_bus,
        now_iso=format_utc_iso(clock()),
    )


@router.post("/sessions/{session_id}/resume")
def resume_session_route(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    container_manager: SessionContainerManager = Depends(get_container_manager),
    event_bus: EventBus = Depends(get_event_bus),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> dict[str, str]:
    """Resume a PAUSED session and its container."""
    return _resume_owned_session(
        session_id=session_id,
        owner_id=owner_id,
        session_factory=session_factory,
        container_manager=container_manager,
        event_bus=event_bus,
        now_iso=format_utc_iso(clock()),
    )


@router.post("/sessions/{session_id}/takeover")
def takeover_session_route(
    session_id: str,
    body: _TakeoverRequest,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    container_manager: SessionContainerManager = Depends(get_container_manager),
    event_bus: EventBus = Depends(get_event_bus),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> dict[str, str]:
    """Toggle manual takeover (enabled=True pauses, enabled=False resumes)."""
    now_iso = format_utc_iso(clock())
    if body.enabled:
        return _pause_owned_session(
            session_id=session_id,
            owner_id=owner_id,
            session_factory=session_factory,
            container_manager=container_manager,
            event_bus=event_bus,
            now_iso=now_iso,
            reason="takeover enabled",
        )
    return _resume_owned_session(
        session_id=session_id,
        owner_id=owner_id,
        session_factory=session_factory,
        container_manager=container_manager,
        event_bus=event_bus,
        now_iso=now_iso,
        reason="takeover disabled",
    )


class _SessionMessageRequest(BaseModel):
    """Strict validated request body for ``POST /api/v1/sessions/{id}/message``."""

    model_config = ConfigDict(extra="forbid")

    text: str


@router.post("/sessions/{session_id}/message")
def post_session_message_route(
    session_id: str,
    body: _SessionMessageRequest,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    session_runner: SessionRunner = Depends(get_session_runner),
) -> dict[str, bool]:
    """Deliver an in-memory user message to an ACTIVE session's agent loop."""
    row = _load_owned_session_row(session_factory, session_id, owner_id)
    if not isinstance(body.text, str) or not body.text.strip():
        raise ApiError(
            400,
            ErrorCode.ARG_INVALID,
            "Message text must be a non-empty string.",
        )
    if row.state != "ACTIVE":
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Cannot send message to session in state {row.state}.",
        )

    try:
        session_runner.send_session_message(
            session_id=session_id,
            text=body.text,
        )
    except SessionValidationError as exc:
        raise ApiError(400, exc.code, str(exc)) from exc
    except SessionRunnerError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc

    return {"ok": True}


@router.post("/sessions/{session_id}/end")
def end_session_route(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    container_manager: SessionContainerManager = Depends(get_container_manager),
    event_bus: EventBus = Depends(get_event_bus),
    session_runner: SessionRunner = Depends(get_session_runner),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> dict[str, str]:
    """Move session to ENDING, close MCP runtimes, tear down Docker, mark ENDED."""
    row = _load_owned_session_row(session_factory, session_id, owner_id)
    if row.state == "ENDED":
        session_runner.close_runtime(session_id)
        return {"state": "ENDED"}

    ending_iso = format_utc_iso(clock())
    if row.state != "ENDING":
        _persist_session_state(
            session_factory,
            session_id=session_id,
            owner_id=owner_id,
            new_state="ENDING",
            now_iso=ending_iso,
        )
        _publish_session_state(
            event_bus,
            session_id=session_id,
            state="ENDING",
            ts=ending_iso,
        )

    session_runner.close_runtime(session_id)
    try:
        container_manager.teardown_session(session_id)
    except ContainerManagerError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to tear down session '{session_id}': {exc}",
        ) from exc

    ended_iso = format_utc_iso(clock())
    receipt_url = f"/api/v1/sessions/{session_id}/receipt"
    _persist_session_state(
        session_factory,
        session_id=session_id,
        owner_id=owner_id,
        new_state="ENDED",
        now_iso=ended_iso,
        receipt_url=receipt_url,
    )
    _publish_session_state(
        event_bus,
        session_id=session_id,
        state="ENDED",
        ts=ended_iso,
    )
    try:
        event_bus.publish(
            session_id=session_id,
            event_type="session_ended",
            data={"receipt_url": receipt_url},
            ts=ended_iso,
        )
    except EventBusError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to publish session_ended event: {exc}",
        ) from exc

    return {"state": "ENDED"}


@router.get("/sessions/{session_id}/view")
def get_session_live_view(
    session_id: str,
    token: str = Query(default=""),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    configured_token: str | None = Depends(get_app_token),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> Response:
    """Validate a scoped view token; live KasmVNC proxy fails closed on isolation."""
    if not configured_token:
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            "Authentication is not configured.",
        )

    try:
        claims = verify_view_token(
            token,
            expected_session_id=session_id,
            signing_key=configured_token,
            now=clock(),
        )
    except StreamTokenError as exc:
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            f"Invalid view token: {exc}",
        ) from exc

    row = _load_owned_session_row(session_factory, session_id, claims.owner_id)
    if row.state not in ACTIVE_STREAM_STATES:
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Session '{session_id}' is not active (state={row.state}).",
        )

    raise ApiError(
        501,
        ErrorCode.UPSTREAM_ERROR,
        "Live KasmVNC view proxy is unavailable under isolated session "
        "networking; use the screenshot fallback.",
    )


@router.get("/sessions/{session_id}/screenshot")
async def get_session_screenshot(
    session_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    session_runner: SessionRunner = Depends(get_session_runner),
) -> Response:
    """Capture and return a live JPEG screenshot for an owned active session."""
    row = _load_owned_session_row(session_factory, session_id, owner_id)
    if row.state not in ACTIVE_STREAM_STATES:
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Session '{session_id}' is not active (state={row.state}).",
        )

    runtime = session_runner.get_runtime(session_id)
    if runtime is None:
        raise ApiError(
            409,
            ErrorCode.SESSION_INACTIVE,
            f"Session '{session_id}' runtime is not active.",
        )

    try:
        jpeg_bytes = await anyio.to_thread.run_sync(runtime.capture_screenshot_jpeg)
    except McpRuntimeError as exc:
        raise ApiError(
            502,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to capture session screenshot: {exc}",
        ) from exc

    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )

"""Phase 4 WebSocket event stream route (F-10).

Implements:
- ``GET /api/v1/sessions/{session_id}/stream?token=...``

Enforces short-lived, session-scoped, owner-bound token verification and
active-session ownership checks before upgrading, requires ``hello`` as the
first client message, replays persisted events with ``seq > last_seq`` via
``EventBus``, streams newly persisted ``EventEnvelope`` frames without blocking
the async event loop, and accepts ``ping`` / ``throughput`` frames silently.
"""

from __future__ import annotations

import asyncio
import json
import math
import secrets
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Literal

import anyio
from control_plane.api.deps import (
    get_app_token,
    get_event_bus,
    get_session_factory,
    get_stream_clock,
)
from control_plane.api.stream_tokens import StreamTokenError, verify_stream_token
from control_plane.db.models import SessionModel
from control_plane.events.event_bus import EventBus, EventBusError
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(prefix="/api/v1", tags=["stream"])

DEFAULT_STREAM_POLL_INTERVAL_S = 0.05

# Session states permitted to connect to the live event stream.
# Terminal and shutting-down states fail closed as inactive.
ACTIVE_STREAM_STATES: frozenset[str] = frozenset(
    {
        "CREATED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "STARTING",
        "ACTIVE",
        "PAUSED",
    }
)


class _HelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"]
    last_seq: int = 0

    @field_validator("last_seq", mode="before")
    @classmethod
    def _validate_last_seq(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("last_seq must be a non-negative integer.")
        return value


class _PingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ping"]


class _ThroughputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["throughput"]
    kbps: float

    @field_validator("kbps", mode="before")
    @classmethod
    def _validate_kbps(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("kbps must be a finite non-negative number.")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("kbps must be a finite non-negative number.")
        return numeric


def get_stream_poll_interval() -> float:
    """Return event poll cadence in seconds while a WebSocket stream is open."""
    return DEFAULT_STREAM_POLL_INTERVAL_S


def _authorize_session_for_stream(
    session_factory: sessionmaker[Session],
    *,
    session_id: str,
    owner_id: str,
) -> bool:
    """Verify session exists, belongs to ``owner_id``, and is still active."""
    with session_factory() as db:
        row = db.execute(
            select(SessionModel.owner_id, SessionModel.state).where(
                SessionModel.session_id == session_id
            )
        ).one_or_none()
    if row is None:
        return False
    db_owner_id, db_state = row
    if not isinstance(db_owner_id, str) or not secrets.compare_digest(
        db_owner_id, owner_id
    ):
        return False
    return db_state in ACTIVE_STREAM_STATES


def _parse_hello_message(raw_text: str) -> _HelloMessage | None:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return _HelloMessage.model_validate(payload)
    except ValidationError:
        return None


def _is_valid_runtime_client_message(raw_text: str) -> bool:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    msg_type = payload.get("type")
    try:
        if msg_type == "ping":
            _PingMessage.model_validate(payload)
            return True
        if msg_type == "throughput":
            _ThroughputMessage.model_validate(payload)
            return True
    except ValidationError:
        return False
    return False


async def _receive_client_text(websocket: WebSocket) -> tuple[bool, str | None]:
    """Receive one WebSocket frame cleanly.

    Returns ``(disconnected, text)``. Binary/non-text frames yield
    ``(False, None)``.
    """
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        return True, None
    text = message.get("text")
    if not isinstance(text, str):
        return False, None
    return False, text


@router.websocket("/sessions/{session_id}/stream")
async def stream_session_events(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
    configured_token: str | None = Depends(get_app_token),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    event_bus: EventBus = Depends(get_event_bus),
    poll_interval_s: float = Depends(get_stream_poll_interval),
    clock: Callable[[], datetime] = Depends(get_stream_clock),
) -> None:
    """Authenticate, replay missed events, and stream live ``EventEnvelope`` frames."""
    if not configured_token or not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        claims = verify_stream_token(
            token,
            expected_session_id=session_id,
            signing_key=configured_token,
            now=clock(),
        )
    except StreamTokenError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    authorized = await anyio.to_thread.run_sync(
        partial(
            _authorize_session_for_stream,
            session_factory,
            session_id=session_id,
            owner_id=claims.owner_id,
        )
    )
    if not authorized:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    try:
        disconnected, first_text = await _receive_client_text(websocket)
    except WebSocketDisconnect:
        return

    if disconnected:
        return
    if first_text is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    hello = _parse_hello_message(first_text)
    if hello is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    cursor_seq = hello.last_seq
    try:
        replayed = await anyio.to_thread.run_sync(
            partial(event_bus.replay, session_id=session_id, last_seq=cursor_seq)
        )
    except EventBusError:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    for envelope in replayed:
        await websocket.send_json(envelope.model_dump())
        cursor_seq = envelope.seq

    stop_event = asyncio.Event()

    async def _pump_live_events() -> None:
        nonlocal cursor_seq
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
                break
            except TimeoutError:
                pass

            try:
                new_events = await anyio.to_thread.run_sync(
                    partial(
                        event_bus.replay,
                        session_id=session_id,
                        last_seq=cursor_seq,
                    )
                )
            except EventBusError:
                stop_event.set()
                try:
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                except RuntimeError:
                    pass
                return

            for envelope in new_events:
                if stop_event.is_set():
                    return
                try:
                    await websocket.send_json(envelope.model_dump())
                except (WebSocketDisconnect, RuntimeError):
                    stop_event.set()
                    return
                cursor_seq = envelope.seq

    async def _consume_client_frames() -> None:
        while not stop_event.is_set():
            try:
                frame_disconnected, frame_text = await _receive_client_text(websocket)
            except WebSocketDisconnect:
                stop_event.set()
                return

            if frame_disconnected:
                stop_event.set()
                return

            if frame_text is None or not _is_valid_runtime_client_message(frame_text):
                stop_event.set()
                try:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                except RuntimeError:
                    pass
                return

    sender_task = asyncio.create_task(_pump_live_events())
    reader_task = asyncio.create_task(_consume_client_frames())
    try:
        _, pending = await asyncio.wait(
            {sender_task, reader_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_event.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(sender_task, reader_task, return_exceptions=True)
    finally:
        stop_event.set()
        for task in (sender_task, reader_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sender_task, reader_task, return_exceptions=True)

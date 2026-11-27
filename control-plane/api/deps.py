"""FastAPI dependencies for authentication, ownership, DB sessions, and loaders."""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from control_plane.agent.llm import LlmAdapter
from control_plane.agent.openai_adapter import OpenAIResponsesAdapter
from control_plane.contracts import ErrorBody, ErrorCode
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.policy_loader import PolicyLoader
from control_plane.sessions.container_manager import SessionContainerManager
from control_plane.sessions.profile_loader import ProfileLoader
from control_plane.sessions.session_runner import RuntimeRegistry, SessionRunner
from fastapi import Depends, Header
from sqlalchemy.orm import Session, sessionmaker


class ApiError(Exception):
    """Structured API error rendered as ``{"error": ErrorBody}``."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = ErrorBody(code=code, message=message, details=details)


def derive_owner_id(bearer_token: str) -> str:
    """Map an authenticated bearer token to a stable, non-reversible ``owner_id``.

    Never stores or exposes the raw bearer token.
    """
    digest = hashlib.sha256(
        b"overwatch:caller:" + bearer_token.encode("utf-8")
    ).hexdigest()[:24]
    return f"owner_{digest}"


def get_app_token() -> str | None:
    """Return the configured ``APP_TOKEN`` from the environment."""
    token = os.environ.get("APP_TOKEN")
    if token is None or not token.strip():
        return None
    return token


def get_public_base_url() -> str | None:
    """Return the configured ``PUBLIC_BASE_URL`` from the environment."""
    raw = os.environ.get("PUBLIC_BASE_URL")
    if raw is None or not raw.strip():
        return None
    return raw


def require_owner_id(
    authorization: str | None = Header(default=None),
    configured_token: str | None = Depends(get_app_token),
) -> str:
    """Validate ``Authorization: Bearer <APP_TOKEN>`` and return caller ``owner_id``."""
    if not configured_token:
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            "Authentication is not configured.",
        )

    if not authorization or not isinstance(authorization, str):
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            "Missing Authorization header.",
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            "Invalid Authorization header format; expected Bearer token.",
        )

    presented = parts[1]
    if not secrets.compare_digest(presented, configured_token):
        raise ApiError(
            401,
            ErrorCode.AUTH_ERROR,
            "Invalid bearer token.",
        )

    return derive_owner_id(configured_token)


@lru_cache(maxsize=1)
def _default_session_factory() -> sessionmaker[Session]:
    engine = create_db_engine()
    init_db(engine)
    return create_session_factory(engine)


def get_session_factory() -> sessionmaker[Session]:
    """Injectable SQLAlchemy sessionmaker dependency."""
    return _default_session_factory()


def get_event_bus(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> EventBus:
    """Injectable persistent EventBus dependency."""
    return EventBus(session_factory)


def get_approval_manager(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    event_bus: EventBus = Depends(get_event_bus),
) -> ApprovalManager:
    """Injectable ApprovalManager dependency."""
    return ApprovalManager(session_factory=session_factory, event_bus=event_bus)


def get_profile_loader() -> ProfileLoader:
    """Injectable ProfileLoader dependency."""
    return ProfileLoader()


def get_policy_loader() -> PolicyLoader:
    """Injectable PolicyLoader dependency."""
    return PolicyLoader()


@lru_cache(maxsize=1)
def _default_container_manager() -> SessionContainerManager:
    return SessionContainerManager()


def get_container_manager() -> SessionContainerManager:
    """Injectable SessionContainerManager dependency."""
    return _default_container_manager()


def get_stream_clock() -> Callable[[], datetime]:
    """Return current UTC clock callable for token and session timestamps."""
    return lambda: datetime.now(UTC)


def get_llm_factory() -> Callable[[], LlmAdapter]:
    """Injectable LLM adapter factory dependency."""
    return OpenAIResponsesAdapter.from_env


@lru_cache(maxsize=1)
def get_runtime_registry() -> RuntimeRegistry:
    """Shared in-process registry for active session MCP runtimes and workers."""
    return RuntimeRegistry()


def get_session_runner(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    container_manager: SessionContainerManager = Depends(get_container_manager),
    event_bus: EventBus = Depends(get_event_bus),
    approval_manager: ApprovalManager = Depends(get_approval_manager),
    profile_loader: ProfileLoader = Depends(get_profile_loader),
    policy_loader: PolicyLoader = Depends(get_policy_loader),
    llm_factory: Callable[[], LlmAdapter] = Depends(get_llm_factory),
    registry: RuntimeRegistry = Depends(get_runtime_registry),
    clock: Any = Depends(get_stream_clock),
) -> SessionRunner:
    """Injectable SessionRunner wired to request/override dependencies."""
    return SessionRunner(
        session_factory=session_factory,
        container_manager=container_manager,
        event_bus=event_bus,
        approval_manager=approval_manager,
        profile_loader=profile_loader,
        policy_loader=policy_loader,
        llm_factory=llm_factory,
        clock=clock,
        registry=registry,
    )

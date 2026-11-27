"""Session startup and agent execution coordinator (Phase 4, F-01/F-05).

Coordinates:
- Generating contract-valid ``s_`` ULIDs and persisting ``SessionModel`` rows.
- Loading trusted ``Profile`` and ``Policy`` configurations (defaulting to
  ``general`` when omitted and rejecting unimplemented ``dry_run=True``).
- Provisioning hardened session containers via ``SessionContainerManager`` and
  starting persistent ``SessionMcpRuntime`` connections for profile servers.
- Transitioning ``CREATED`` -> ``STARTING`` -> ``ACTIVE`` only once container
  and MCP servers are ready, or failing closed with resource cleanup +
  ``FAILED`` state/event on startup errors.
- Running ``AgentLoop`` with ``AuditedGatewayCoordinator``, container-backed
  ``PathJail``, and ``SessionMcpRuntime`` off the FastAPI event loop.

Note on approvals:
    When ``AgentLoop`` returns ``status == "awaiting_approval"``, the session
    and its ``SessionMcpRuntime`` stay alive without auto-approving or executing
    the pending call. Resuming the saved agent conversation after approval
    resolution remains a separate integration task.

Note on deployment:
    Using Docker-backed containers and exec stdio MCP servers requires supplying
    a usable injected Docker SDK client (remote Docker API auth/transport
    configuration remains outside this module; never mount raw
    ``/var/run/docker.sock``).
"""

from __future__ import annotations

import contextlib
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_plane.agent.llm import LlmAdapter, LlmAdapterError
from control_plane.agent.loop import AgentLoop, AgentLoopResult, LoopStatus
from control_plane.agent.openai_adapter import OpenAIResponsesAdapter
from control_plane.audit.audit_writer import AuditWriter, format_utc_iso
from control_plane.contracts import ErrorCode, Policy, Profile, SessionState
from control_plane.db.models import SessionModel
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.mcp_runtime import SessionMcpRuntime
from control_plane.gateway.path_jail import DEFAULT_WORKSPACE_ROOT
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import PolicyLoader, PolicyLoaderError
from control_plane.sessions.container_manager import (
    SessionContainerInfo,
    SessionContainerManager,
    validate_session_id,
)
from control_plane.sessions.profile_loader import ProfileLoader, ProfileLoaderError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

SESSION_ID_PREFIX = "s_"
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REPO_ROOT = Path(__file__).resolve().parents[2]


class SessionRunnerError(Exception):
    """Base error raised by ``SessionRunner``."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.UPSTREAM_ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SessionValidationError(SessionRunnerError):
    """Raised when session creation input/profile/dry_run fails validation."""


class SessionStartupError(SessionRunnerError):
    """Raised when container, MCP, or LLM startup fails (fail closed)."""


@dataclass(frozen=True)
class StartedSession:
    """Metadata returned once a session transitions to ``ACTIVE``."""

    session_id: str
    state: SessionState
    profile: Profile
    policy: Policy
    container_info: SessionContainerInfo
    created_at: str
    llm: LlmAdapter


@dataclass(frozen=True)
class SessionRunRecord:
    """In-memory snapshot of one session's agent execution outcome."""

    session_id: str
    status: LoopStatus
    error_code: str | None = None
    error_message: str | None = None
    pending_approval_id: str | None = None


def _encode_crockford_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_session_id(now: datetime | None = None) -> str:
    """Generate a contract-valid ``s_``-prefixed 26-character Crockford ULID."""
    dt = datetime.now(UTC) if now is None else now.astimezone(UTC)
    timestamp_ms = int(dt.timestamp() * 1000)
    if timestamp_ms < 0 or timestamp_ms >= (1 << 48):
        raise SessionRunnerError("Timestamp outside valid 48-bit ULID range.")
    ts_part = _encode_crockford_base32(timestamp_ms, 10)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_part = _encode_crockford_base32(rand_int, 16)
    return validate_session_id(f"{SESSION_ID_PREFIX}{ts_part}{rand_part}")


def _default_runtime_factory(
    *,
    docker_client: Any,
    session_id: str,
    profile: Profile,
    container_id: str,
) -> SessionMcpRuntime:
    return SessionMcpRuntime(
        docker_client=docker_client,
        session_id=session_id,
        profile=profile,
        container_id=container_id,
    )


def _default_llm_factory() -> LlmAdapter:
    return OpenAIResponsesAdapter.from_env()


class RuntimeRegistry:
    """Thread-safe in-memory store for session MCP runtimes and worker futures."""

    def __init__(self, *, max_workers: int = 8) -> None:
        self.lock = threading.RLock()
        self.runtimes: dict[str, SessionMcpRuntime] = {}
        self.futures: dict[str, Future[SessionRunRecord]] = {}
        self.run_records: dict[str, SessionRunRecord] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="overwatch-session-agent",
        )
        self.is_shutdown = False


class SessionRunner:
    """Owns session startup, container MCP runtime lifecycle, and agent turns."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        container_manager: SessionContainerManager,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        profile_loader: ProfileLoader,
        policy_loader: PolicyLoader,
        runtime_factory: Callable[..., SessionMcpRuntime] = _default_runtime_factory,
        llm_factory: Callable[[], LlmAdapter] = _default_llm_factory,
        clock: Callable[[], datetime] | None = None,
        prompts_base_dir: Path | str = REPO_ROOT,
        max_workers: int = 8,
        registry: RuntimeRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._container_manager = container_manager
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._profile_loader = profile_loader
        self._policy_loader = policy_loader
        self._runtime_factory = runtime_factory
        self._llm_factory = llm_factory
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._prompts_base_dir = prompts_base_dir

        self._registry = (
            registry
            if registry is not None
            else RuntimeRegistry(max_workers=max_workers)
        )
        self._lock = self._registry.lock
        self._runtimes = self._registry.runtimes
        self._futures = self._registry.futures
        self._run_records = self._registry.run_records
        self._executor = self._registry.executor

    def _now_iso(self) -> str:
        return format_utc_iso(self._clock())

    def _insert_session_row(
        self,
        *,
        session_id: str,
        profile_name: str,
        owner_id: str,
        created_at: str,
    ) -> None:
        with self._session_factory() as db:
            db.add(
                SessionModel(
                    session_id=session_id,
                    state="CREATED",
                    profile=profile_name,
                    owner_id=owner_id,
                    dry_run=0,
                    container_id=None,
                    container_ip=None,
                    receipt_url=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            db.commit()

    def _update_session_row(
        self,
        session_id: str,
        *,
        state: SessionState | None = None,
        container_id: str | None = None,
        container_ip: str | None = None,
    ) -> None:
        now_iso = self._now_iso()
        with self._session_factory() as db:
            row = db.get(SessionModel, session_id)
            if row is None:
                raise SessionStartupError(
                    f"Session '{session_id}' missing during state update."
                )
            if state is not None:
                row.state = state
            if container_id is not None:
                row.container_id = container_id
            if container_ip is not None:
                row.container_ip = container_ip
            row.updated_at = now_iso
            db.commit()

    def _emit_state(
        self,
        session_id: str,
        state: SessionState,
        *,
        reason: str | None = None,
        ts: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"state": state}
        if reason is not None:
            data["reason"] = reason
        self._event_bus.publish(
            session_id=session_id,
            event_type="session_state",
            data=data,
            ts=ts or self._now_iso(),
        )

    def start_session(
        self,
        *,
        owner_id: str,
        request_text: str,
        profile_name: str | None = None,
        dry_run: bool = False,
    ) -> StartedSession:
        """Validate inputs, provision hardened container + MCP servers, mark ACTIVE."""
        if self._registry.is_shutdown:
            raise SessionStartupError("SessionRunner is shut down.")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise SessionValidationError(
                "Caller owner_id is required.",
                code=ErrorCode.AUTH_ERROR,
            )
        if not isinstance(request_text, str) or not request_text.strip():
            raise SessionValidationError(
                "request_text must be a non-empty string.",
                code=ErrorCode.ARG_INVALID,
            )
        if dry_run:
            raise SessionValidationError(
                "dry_run mode is not implemented.",
                code=ErrorCode.POLICY_BLOCK,
            )

        selected_profile_name = (
            "general" if profile_name is None or not profile_name else profile_name
        )
        try:
            profile = self._profile_loader.load(selected_profile_name)
        except ProfileLoaderError as exc:
            raise SessionValidationError(
                f"Invalid or unknown profile '{selected_profile_name}': {exc}",
                code=ErrorCode.ARG_INVALID,
            ) from exc

        try:
            policy = self._policy_loader.load()
        except PolicyLoaderError as exc:
            raise SessionStartupError(
                f"Failed to load gateway policy: {exc}",
                code=ErrorCode.UPSTREAM_ERROR,
            ) from exc

        try:
            llm = self._llm_factory()
        except LlmAdapterError as exc:
            raise SessionStartupError(
                f"LLM adapter configuration failed: {exc}",
                code=ErrorCode.UPSTREAM_ERROR,
            ) from exc

        now_dt = self._clock()
        created_at = format_utc_iso(now_dt)
        session_id = generate_session_id(now_dt)

        row_created = False
        try:
            self._insert_session_row(
                session_id=session_id,
                profile_name=profile.name,
                owner_id=owner_id,
                created_at=created_at,
            )
            row_created = True
            self._emit_state(session_id, "CREATED", ts=created_at)

            self._update_session_row(session_id, state="STARTING")
            self._emit_state(session_id, "STARTING")

            container_info = self._container_manager.create_session(
                session_id,
                profile,
                start=True,
            )
            self._update_session_row(
                session_id,
                container_id=container_info.container_id,
                container_ip=container_info.ip_address,
            )

            runtime = self._runtime_factory(
                docker_client=self._container_manager.client,
                session_id=session_id,
                profile=profile,
                container_id=container_info.container_id,
            )
            with self._lock:
                self._runtimes[session_id] = runtime

            runtime.verify_container()
            for server_name in profile.mcp_servers:
                runtime.start_server(server_name)

            self._update_session_row(session_id, state="ACTIVE")
            self._emit_state(session_id, "ACTIVE")
        except Exception as exc:
            self.close_runtime(session_id)
            with contextlib.suppress(Exception):
                self._container_manager.teardown_session(session_id)

            safe_reason = f"session startup failed ({type(exc).__name__})"
            if row_created:
                with contextlib.suppress(SQLAlchemyError, SessionStartupError):
                    self._update_session_row(session_id, state="FAILED")
                with contextlib.suppress(EventBusError):
                    self._emit_state(session_id, "FAILED", reason=safe_reason)

            raise SessionStartupError(
                f"Failed starting session '{session_id}': {exc}",
                code=ErrorCode.UPSTREAM_ERROR,
            ) from exc

        return StartedSession(
            session_id=session_id,
            state="ACTIVE",
            profile=profile,
            policy=policy,
            container_info=container_info,
            created_at=created_at,
            llm=llm,
        )

    def run_session_agent(
        self,
        *,
        started: StartedSession,
        request_text: str,
    ) -> SessionRunRecord:
        """Execute ``AgentLoop`` synchronously using the session's MCP runtime."""
        session_id = started.session_id
        runtime = self.get_runtime(session_id)
        if runtime is None:
            record = SessionRunRecord(
                session_id=session_id,
                status="error",
                error_code=ErrorCode.FATAL.value,
                error_message="Session MCP runtime is unavailable.",
            )
            with self._lock:
                self._run_records[session_id] = record
            return record

        try:
            workspace_jail = runtime.build_workspace_path_jail()
            pipeline = GatewayPreflightPipeline(
                started.policy,
                workspace_root=DEFAULT_WORKSPACE_ROOT,
                path_jail=workspace_jail,
            )
            coordinator = AuditedGatewayCoordinator(
                pipeline=pipeline,
                audit_writer=AuditWriter(self._session_factory),
            )
            loop = AgentLoop(
                llm=started.llm,
                coordinator=coordinator,
                approval_manager=self._approval_manager,
                event_bus=self._event_bus,
                policy=started.policy,
                profile=started.profile,
                prompts_base_dir=self._prompts_base_dir,
                workspace_root=DEFAULT_WORKSPACE_ROOT,
            )
            result: AgentLoopResult = loop.run(
                session_id=session_id,
                request_text=request_text,
                executor=runtime,
                session_state="ACTIVE",
            )
        except Exception as exc:
            with contextlib.suppress(SQLAlchemyError, SessionStartupError):
                self._update_session_row(session_id, state="FAILED")
            with contextlib.suppress(EventBusError):
                self._emit_state(
                    session_id,
                    "FAILED",
                    reason=f"agent execution failed ({type(exc).__name__})",
                )
            record = SessionRunRecord(
                session_id=session_id,
                status="error",
                error_code=ErrorCode.FATAL.value,
                error_message="Session agent execution failed.",
            )
            with self._lock:
                self._run_records[session_id] = record
            return record

        if result.status == "error":
            # Mark FAILED only on fatal/upstream loop failures while avoiding
            # leaking request_text in session_state reasons.
            with contextlib.suppress(SQLAlchemyError, SessionStartupError):
                self._update_session_row(session_id, state="FAILED")
            with contextlib.suppress(EventBusError):
                err_code = (
                    result.error.code if result.error is not None else ErrorCode.FATAL
                )
                self._emit_state(
                    session_id,
                    "FAILED",
                    reason=f"agent stopped with {err_code}",
                )

        # On 'awaiting_approval', keep session state ACTIVE and keep runtime open.
        # Resuming the saved agent conversation after approval resolution remains
        # a separate integration task.
        record = SessionRunRecord(
            session_id=session_id,
            status=result.status,
            error_code=result.error.code if result.error is not None else None,
            error_message=result.error.message if result.error is not None else None,
            pending_approval_id=(
                result.pending_approval.approval_id
                if result.pending_approval is not None
                else None
            ),
        )
        with self._lock:
            self._run_records[session_id] = record
        return record

    def dispatch_agent_run(
        self,
        *,
        started: StartedSession,
        request_text: str,
    ) -> Future[SessionRunRecord]:
        """Submit ``AgentLoop`` execution to the off-loop thread pool."""
        with self._lock:
            if self._registry.is_shutdown:
                raise SessionRunnerError("SessionRunner is shut down.")
            future = self._executor.submit(
                self.run_session_agent,
                started=started,
                request_text=request_text,
            )
            self._futures[started.session_id] = future
            return future

    def get_runtime(self, session_id: str) -> SessionMcpRuntime | None:
        with self._lock:
            return self._runtimes.get(session_id)

    def get_run_record(self, session_id: str) -> SessionRunRecord | None:
        with self._lock:
            return self._run_records.get(session_id)

    def wait_for_session(
        self, session_id: str, *, timeout: float | None = 5.0
    ) -> SessionRunRecord | None:
        with self._lock:
            future = self._futures.get(session_id)
        if future is not None:
            return future.result(timeout=timeout)
        return self.get_run_record(session_id)

    def close_runtime(self, session_id: str) -> None:
        """Close and unregister the MCP runtime for ``session_id`` if present."""
        with self._lock:
            runtime = self._runtimes.pop(session_id, None)
        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime.close()

    def shutdown(self, *, wait: bool = True) -> None:
        """Wait for active background runs and close all open MCP runtimes."""
        with self._lock:
            self._registry.is_shutdown = True
            session_ids = list(self._runtimes.keys())
            futures = list(self._futures.values())

        for sid in session_ids:
            self.close_runtime(sid)

        if wait:
            for fut in futures:
                with contextlib.suppress(Exception):
                    fut.result(timeout=5.0)
        self._executor.shutdown(wait=wait)

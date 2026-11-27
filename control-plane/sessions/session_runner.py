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
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from control_plane.agent.llm import LlmAdapter, LlmAdapterError
from control_plane.agent.loop import (
    AgentConversationSnapshot,
    AgentLoop,
    AgentLoopResult,
    LoopStatus,
    PendingApprovalContinuation,
)
from control_plane.agent.openai_adapter import OpenAIResponsesAdapter
from control_plane.audit.audit_writer import AuditWriter, format_utc_iso
from control_plane.contracts import (
    ApprovalRequest,
    ErrorCode,
    Plan,
    Policy,
    Profile,
    SessionState,
)
from control_plane.db.models import SessionModel
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.approval_manager import (
    ApprovalManager,
    ApprovalManagerError,
    ApprovalResolutionResult,
)
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
from control_plane.sessions.plan_service import (
    PlanNotFoundError,
    PlanService,
    PlanValidationError,
)
from control_plane.sessions.profile_loader import ProfileLoader, ProfileLoaderError
from control_plane.sessions.undo_manager import UndoError, UndoManager
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from control_plane.notifications.push import PushNotificationService

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
    plan: Plan | None = None


@dataclass(frozen=True)
class SessionRunRecord:
    """In-memory snapshot of one session's agent execution outcome."""

    session_id: str
    status: LoopStatus
    error_code: str | None = None
    error_message: str | None = None
    pending_approval_id: str | None = None


@dataclass
class PendingApprovalEntry:
    """In-memory-only pending approval state and timer for an active session."""

    session_id: str
    approval_id: str
    started: StartedSession
    continuation: PendingApprovalContinuation
    expires_at: str
    timer: threading.Timer | None = None

    def __repr__(self) -> str:
        return (
            f"PendingApprovalEntry(session_id={self.session_id!r}, "
            f"approval_id={self.approval_id!r}, expires_at={self.expires_at!r})"
        )


@dataclass
class SessionExecutionState:
    """In-memory-only conversation snapshot and message queue for one session."""

    session_id: str
    started: StartedSession
    is_running: bool = False
    snapshot: AgentConversationSnapshot | None = field(default=None, repr=False)
    pending_messages: deque[str] = field(default_factory=deque, repr=False)
    terminal_status: LoopStatus | None = None

    def __repr__(self) -> str:
        return (
            f"SessionExecutionState(session_id={self.session_id!r}, "
            f"is_running={self.is_running}, "
            f"queued={len(self.pending_messages)}, "
            f"terminal_status={self.terminal_status!r})"
        )


def _normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_utc_iso(raw: str) -> datetime:
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    return _normalize_utc(datetime.fromisoformat(normalized))


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
        self.pending_approvals: dict[str, PendingApprovalEntry] = {}
        self.pending_plans: dict[str, tuple[StartedSession, str]] = {}
        self.session_states: dict[str, SessionExecutionState] = {}
        self.session_locks: dict[str, threading.Lock] = {}
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
        push_service: PushNotificationService | None = None,
        undo_manager: UndoManager | None = None,
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
        self._push_service = push_service
        self._undo_manager = (
            UndoManager(session_factory, clock=self._clock)
            if undo_manager is None
            else undo_manager
        )

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
        plan_preview: bool = False,
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
        generated_plan: Plan | None = None
        try:
            self._insert_session_row(
                session_id=session_id,
                profile_name=profile.name,
                owner_id=owner_id,
                created_at=created_at,
            )
            row_created = True
            self._emit_state(session_id, "CREATED", ts=created_at)

            if plan_preview:
                self._update_session_row(session_id, state="PLANNING")
                self._emit_state(session_id, "PLANNING")
                generated_plan = PlanService(
                    self._session_factory, clock=self._clock
                ).create_plan(
                    session_id=session_id,
                    profile=profile,
                    policy=policy,
                )
                self._event_bus.publish(
                    session_id=session_id,
                    event_type="plan",
                    data=generated_plan,
                )

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

            final_state: SessionState = (
                "AWAITING_PLAN_APPROVAL" if plan_preview else "ACTIVE"
            )
            self._update_session_row(session_id, state=final_state)
            self._emit_state(session_id, final_state)
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

        started = StartedSession(
            session_id=session_id,
            state=final_state,
            profile=profile,
            policy=policy,
            container_info=container_info,
            created_at=created_at,
            llm=llm,
            plan=generated_plan,
        )
        if plan_preview:
            with self._lock:
                self._registry.pending_plans[session_id] = (started, request_text)
        return started

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        with self._lock:
            return self._registry.session_locks.setdefault(session_id, threading.Lock())

    def _get_session_state(self, session_id: str) -> SessionState | None:
        with self._session_factory() as db:
            row = db.get(SessionModel, session_id)
            if row is None:
                return None
            return row.state  # type: ignore[return-value]

    def _get_session_owner_id(self, session_id: str) -> str | None:
        with contextlib.suppress(SQLAlchemyError):
            with self._session_factory() as db:
                row = db.get(SessionModel, session_id)
                if row is not None:
                    return row.owner_id
        return None

    def _mark_failed_if_active(self, session_id: str, *, reason: str) -> bool:
        """Atomically transition ``ACTIVE -> FAILED`` and emit ``session_state``."""
        now_iso = self._now_iso()
        transitioned = False
        with contextlib.suppress(SQLAlchemyError):
            with self._session_factory() as db:
                res = db.execute(
                    update(SessionModel)
                    .where(
                        SessionModel.session_id == session_id,
                        SessionModel.state == "ACTIVE",
                    )
                    .values(state="FAILED", updated_at=now_iso)
                )
                db.commit()
                transitioned = res.rowcount == 1
        if transitioned:
            with contextlib.suppress(EventBusError):
                self._emit_state(session_id, "FAILED", reason=reason)
        return transitioned

    def _mark_paused_if_active(self, session_id: str, *, reason: str) -> bool:
        """Atomically transition ``ACTIVE -> PAUSED`` and emit ``session_state``."""
        now_iso = self._now_iso()
        transitioned = False
        with contextlib.suppress(SQLAlchemyError):
            with self._session_factory() as db:
                res = db.execute(
                    update(SessionModel)
                    .where(
                        SessionModel.session_id == session_id,
                        SessionModel.state == "ACTIVE",
                    )
                    .values(state="PAUSED", updated_at=now_iso)
                )
                db.commit()
                transitioned = res.rowcount == 1
        if transitioned:
            with contextlib.suppress(EventBusError):
                self._emit_state(session_id, "PAUSED", reason=reason)
        return transitioned

    def _build_agent_loop(
        self,
        *,
        started: StartedSession,
        runtime: SessionMcpRuntime,
    ) -> AgentLoop:
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
        return AgentLoop(
            llm=started.llm,
            coordinator=coordinator,
            approval_manager=self._approval_manager,
            event_bus=self._event_bus,
            policy=started.policy,
            profile=started.profile,
            prompts_base_dir=self._prompts_base_dir,
            workspace_root=DEFAULT_WORKSPACE_ROOT,
            approved_plan=started.plan,
            undo_manager=self._undo_manager,
            file_reader=lambda path: self._read_workspace_file(runtime, path),
        )

    @staticmethod
    def _read_workspace_file(
        runtime: SessionMcpRuntime, path: str
    ) -> tuple[bool, str | None]:
        from control_plane.gateway.argument_validation import ValidatedArguments

        try:
            res = runtime(ValidatedArguments(tool="files.read", args={"path": path}))
        except Exception:  # noqa: BLE001
            return False, None
        if isinstance(res, dict) and isinstance(res.get("content"), str):
            return True, res["content"]
        return False, None

    def undo_step(self, *, session_id: str, undo_id: str) -> bool:
        """Undo one reversible ``files.write`` for an active session."""
        with self._lock:
            runtime = self._runtimes.get(session_id)
            ctx = self._registry.session_states.get(session_id)
        if runtime is None or ctx is None:
            raise SessionRunnerError(
                "Session runtime is unavailable for undo.",
                code=ErrorCode.SESSION_INACTIVE,
            )
        started = ctx.started
        workspace_jail = runtime.build_workspace_path_jail()
        coordinator = AuditedGatewayCoordinator(
            pipeline=GatewayPreflightPipeline(
                started.policy,
                workspace_root=DEFAULT_WORKSPACE_ROOT,
                path_jail=workspace_jail,
            ),
            audit_writer=AuditWriter(self._session_factory),
        )
        try:
            return self._undo_manager.undo_step(
                session_id=session_id,
                undo_id=undo_id,
                coordinator=coordinator,
                profile=started.profile,
                executor=runtime,
                reader=lambda path: self._read_workspace_file(runtime, path),
                session_state=self._get_session_state(session_id) or "ENDED",
            )
        except UndoError as exc:
            raise SessionRunnerError(str(exc), code=ErrorCode.POLICY_BLOCK) from exc

    def undo_all(self, *, session_id: str) -> int:
        """Undo all available reversible writes for an active session."""
        with self._lock:
            runtime = self._runtimes.get(session_id)
            ctx = self._registry.session_states.get(session_id)
        if runtime is None or ctx is None:
            raise SessionRunnerError(
                "Session runtime is unavailable for undo.",
                code=ErrorCode.SESSION_INACTIVE,
            )
        started = ctx.started
        workspace_jail = runtime.build_workspace_path_jail()
        coordinator = AuditedGatewayCoordinator(
            pipeline=GatewayPreflightPipeline(
                started.policy,
                workspace_root=DEFAULT_WORKSPACE_ROOT,
                path_jail=workspace_jail,
            ),
            audit_writer=AuditWriter(self._session_factory),
        )
        try:
            return self._undo_manager.undo_all(
                session_id=session_id,
                coordinator=coordinator,
                profile=started.profile,
                executor=runtime,
                reader=lambda path: self._read_workspace_file(runtime, path),
                session_state=self._get_session_state(session_id) or "ENDED",
            )
        except UndoError as exc:
            raise SessionRunnerError(str(exc), code=ErrorCode.POLICY_BLOCK) from exc

    def edit_plan(
        self,
        *,
        session_id: str,
        plan_id: str,
        remove_steps: Sequence[int],
    ) -> Plan:
        """Remove steps from a pending plan, re-hash, and emit updated event."""
        try:
            updated = PlanService(self._session_factory, clock=self._clock).edit_plan(
                session_id=session_id,
                plan_id=plan_id,
                remove_steps=remove_steps,
            )
        except PlanNotFoundError as exc:
            raise SessionValidationError(str(exc), code=ErrorCode.ARG_INVALID) from exc
        except PlanValidationError as exc:
            raise SessionValidationError(str(exc), code=ErrorCode.ARG_INVALID) from exc

        with self._lock:
            pending = self._registry.pending_plans.get(session_id)
            if pending is not None:
                started, req_text = pending
                self._registry.pending_plans[session_id] = (
                    StartedSession(
                        session_id=started.session_id,
                        state=started.state,
                        profile=started.profile,
                        policy=started.policy,
                        container_info=started.container_info,
                        created_at=started.created_at,
                        llm=started.llm,
                        plan=updated,
                    ),
                    req_text,
                )

        with contextlib.suppress(EventBusError):
            self._event_bus.publish(
                session_id=session_id,
                event_type="plan",
                data=updated,
            )
        return updated

    def approve_plan(
        self,
        *,
        session_id: str,
        plan_id: str,
    ) -> StartedSession:
        """Approve a pending plan, transition to ACTIVE, and dispatch agent run."""
        current_state = self._get_session_state(session_id)
        if current_state != "AWAITING_PLAN_APPROVAL":
            raise SessionRunnerError(
                f"Session is not awaiting plan approval (state: {current_state}).",
                code=ErrorCode.SESSION_INACTIVE,
            )

        try:
            approved = PlanService(
                self._session_factory, clock=self._clock
            ).approve_plan(session_id=session_id, plan_id=plan_id)
        except PlanNotFoundError as exc:
            raise SessionValidationError(str(exc), code=ErrorCode.ARG_INVALID) from exc
        except PlanValidationError as exc:
            raise SessionValidationError(str(exc), code=ErrorCode.ARG_INVALID) from exc

        with self._lock:
            pending = self._registry.pending_plans.pop(session_id, None)
            if pending is None:
                raise SessionRunnerError(
                    "Missing in-memory request state for plan approval.",
                    code=ErrorCode.SESSION_INACTIVE,
                )
            old_started, request_text = pending
            active_started = StartedSession(
                session_id=old_started.session_id,
                state="ACTIVE",
                profile=old_started.profile,
                policy=old_started.policy,
                container_info=old_started.container_info,
                created_at=old_started.created_at,
                llm=old_started.llm,
                plan=approved,
            )

        self._update_session_row(session_id, state="ACTIVE")
        self._emit_state(session_id, "ACTIVE")
        self.dispatch_agent_run(started=active_started, request_text=request_text)
        return active_started

    def _register_pending_approval(
        self,
        *,
        started: StartedSession,
        approval: ApprovalRequest,
        continuation: PendingApprovalContinuation,
    ) -> None:
        """Retain in-memory continuation state and schedule expiration wake-up."""
        session_id = started.session_id
        approval_id = approval.approval_id
        with self._lock:
            if self._registry.is_shutdown or session_id not in self._runtimes:
                return
            expires_dt = _parse_utc_iso(approval.expires_at)
            now_dt = _normalize_utc(self._clock())
            delay_s = max(0.0, (expires_dt - now_dt).total_seconds())
            if delay_s > 0:
                delay_s += 0.01

            timer = threading.Timer(
                delay_s,
                self._on_approval_deadline,
                args=(session_id, approval_id),
            )
            timer.daemon = True
            entry = PendingApprovalEntry(
                session_id=session_id,
                approval_id=approval_id,
                started=started,
                continuation=continuation,
                expires_at=approval.expires_at,
                timer=timer,
            )
            self._registry.pending_approvals[approval_id] = entry
            self._run_records[session_id] = SessionRunRecord(
                session_id=session_id,
                status="awaiting_approval",
                pending_approval_id=approval_id,
            )
            timer.start()

    def _on_approval_deadline(self, session_id: str, approval_id: str) -> None:
        """Expire a due pending approval without polling and wake the waiting run."""
        with self._lock:
            if self._registry.is_shutdown or session_id not in self._runtimes:
                return
            entry = self._registry.pending_approvals.get(approval_id)
            if entry is None or entry.session_id != session_id:
                return
            expires_dt = _parse_utc_iso(entry.expires_at)

        expired: list[ApprovalResolutionResult] = []
        try:
            for attempt in range(3):
                if self._registry.is_shutdown:
                    return
                expired = self._approval_manager.expire_due(session_id=session_id)
                if any(r.approval_id == approval_id for r in expired):
                    break
                with self._lock:
                    still_pending = (
                        approval_id in self._registry.pending_approvals
                        and not self._registry.is_shutdown
                    )
                if not still_pending:
                    return
                remaining = (expires_dt - _normalize_utc(self._clock())).total_seconds()
                if remaining <= 0.25 and attempt < 2:
                    time.sleep(max(0.01, remaining + 0.02))
                else:
                    break
        except (ApprovalManagerError, Exception):
            with self._lock:
                self._registry.pending_approvals.pop(approval_id, None)
                self._run_records[session_id] = SessionRunRecord(
                    session_id=session_id,
                    status="error",
                    error_code=ErrorCode.UPSTREAM_ERROR.value,
                    error_message="Approval expiration failed.",
                )
            return

        for resolved in expired:
            if resolved.approval_id == approval_id:
                with contextlib.suppress(SessionRunnerError):
                    self.on_approval_resolved(resolved)
                return

    def _drain_queued_messages(self, session_id: str) -> tuple[str, ...]:
        with self._lock:
            ctx = self._registry.session_states.get(session_id)
            if ctx is None or not ctx.pending_messages:
                return ()
            drained = tuple(ctx.pending_messages)
            ctx.pending_messages.clear()
            return drained

    def _clear_session_execution_state(
        self,
        session_id: str,
        *,
        terminal_status: LoopStatus | None = None,
    ) -> None:
        with self._lock:
            ctx = self._registry.session_states.get(session_id)
            if ctx is not None:
                ctx.is_running = False
                ctx.snapshot = None
                ctx.pending_messages.clear()
                if terminal_status is not None:
                    ctx.terminal_status = terminal_status

    def _finalize_loop_result(
        self,
        *,
        session_id: str,
        started: StartedSession,
        runtime: SessionMcpRuntime,
        result: AgentLoopResult,
    ) -> SessionRunRecord:
        while result.status == "completed" and result.snapshot is not None:
            with self._lock:
                if self._registry.is_shutdown or session_id not in self._runtimes:
                    self._clear_session_execution_state(session_id)
                    break
                ctx = self._registry.session_states.get(session_id)
                if ctx is None or not ctx.pending_messages:
                    if ctx is not None:
                        ctx.is_running = False
                        ctx.snapshot = result.snapshot
                        ctx.terminal_status = None
                    break
                queued = tuple(ctx.pending_messages)
                ctx.pending_messages.clear()

            current_state = self._get_session_state(session_id)
            if current_state != "ACTIVE":
                self._clear_session_execution_state(
                    session_id, terminal_status="blocked"
                )
                result = AgentLoopResult(
                    session_id=session_id,
                    status="blocked",
                    error=None,
                    calls_used=result.calls_used,
                    calls_max=result.calls_max,
                    turns=result.turns,
                    audit_seqs=result.audit_seqs,
                )
                break

            loop = self._build_agent_loop(started=started, runtime=runtime)
            result = loop.continue_with_messages(
                snapshot=result.snapshot,
                user_messages=queued,
                executor=runtime,
                session_state=current_state,
                on_approval_created=lambda approval, cont: (
                    self._register_pending_approval(
                        started=started,
                        approval=approval,
                        continuation=cont,
                    )
                ),
                drain_messages=lambda: self._drain_queued_messages(session_id),
            )

        if result.status == "awaiting_approval":
            pending_id = (
                result.pending_approval.approval_id
                if result.pending_approval is not None
                else None
            )
            record = SessionRunRecord(
                session_id=session_id,
                status="awaiting_approval",
                pending_approval_id=pending_id,
            )
            with self._lock:
                # Only clear is_running / update run_records if the approval
                # has not already been resolved and scheduled for resumption.
                still_pending = (
                    pending_id is not None
                    and pending_id in self._registry.pending_approvals
                )
                if still_pending:
                    ctx = self._registry.session_states.get(session_id)
                    if ctx is not None:
                        ctx.is_running = False
                        ctx.snapshot = None
                    self._run_records[session_id] = record
            return record

        if result.status in ("blocked", "budget_exceeded", "error", "paused"):
            self._clear_session_execution_state(
                session_id, terminal_status=result.status
            )

        if result.status == "paused":
            self._mark_paused_if_active(
                session_id,
                reason=(
                    result.error.code
                    if result.error is not None
                    else ErrorCode.LOOP_DETECTED.value
                ),
            )

        if result.status == "error":
            # Mark FAILED only on fatal/upstream loop failures while avoiding
            # leaking request_text in session_state reasons.
            err_code = (
                result.error.code if result.error is not None else ErrorCode.FATAL
            )
            self._mark_failed_if_active(
                session_id, reason=f"agent stopped with {err_code}"
            )

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
        if result.status == "completed" and self._push_service is not None:
            owner_id = self._get_session_owner_id(session_id)
            if owner_id is not None:
                with contextlib.suppress(Exception):
                    self._push_service.notify_session_completed(
                        owner_id=owner_id,
                        session_id=session_id,
                    )
        return record

    def run_session_agent(
        self,
        *,
        started: StartedSession,
        request_text: str,
    ) -> SessionRunRecord:
        """Execute ``AgentLoop`` synchronously using the session's MCP runtime."""
        session_id = started.session_id
        with self._get_session_lock(session_id):
            with self._lock:
                ctx = self._registry.session_states.get(session_id)
                if ctx is None:
                    ctx = SessionExecutionState(
                        session_id=session_id,
                        started=started,
                        is_running=True,
                    )
                    self._registry.session_states[session_id] = ctx
                else:
                    ctx.is_running = True

            runtime = self.get_runtime(session_id)
            if runtime is None:
                self._clear_session_execution_state(session_id, terminal_status="error")
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
                loop = self._build_agent_loop(started=started, runtime=runtime)
                result: AgentLoopResult = loop.run(
                    session_id=session_id,
                    request_text=request_text,
                    executor=runtime,
                    session_state="ACTIVE",
                    on_approval_created=lambda approval, cont: (
                        self._register_pending_approval(
                            started=started,
                            approval=approval,
                            continuation=cont,
                        )
                    ),
                    drain_messages=lambda: self._drain_queued_messages(session_id),
                )
            except Exception as exc:
                self._clear_session_execution_state(session_id, terminal_status="error")
                self._mark_failed_if_active(
                    session_id,
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

            return self._finalize_loop_result(
                session_id=session_id,
                started=started,
                runtime=runtime,
                result=result,
            )

    def _resume_session_agent(
        self,
        *,
        entry: PendingApprovalEntry,
        resolution: ApprovalResolutionResult,
        edited_args: Mapping[str, Any] | None = None,
    ) -> SessionRunRecord:
        """Continue an awaiting ``AgentLoop`` after an approval resolution."""
        session_id = entry.session_id
        with self._get_session_lock(session_id):
            if self._registry.is_shutdown:
                self._clear_session_execution_state(session_id, terminal_status="error")
                record = SessionRunRecord(
                    session_id=session_id,
                    status="error",
                    error_code=ErrorCode.SESSION_INACTIVE.value,
                    error_message="SessionRunner is shut down.",
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            runtime = self.get_runtime(session_id)
            current_state = self._get_session_state(session_id)
            if (
                runtime is None
                or current_state is None
                or current_state in ("ENDING", "ENDED", "FAILED")
            ):
                is_terminal = current_state in ("ENDING", "ENDED", "FAILED")
                self._clear_session_execution_state(
                    session_id,
                    terminal_status="blocked" if is_terminal else "error",
                )
                record = SessionRunRecord(
                    session_id=session_id,
                    status="blocked" if is_terminal else "error",
                    error_code=(
                        ErrorCode.SESSION_INACTIVE.value
                        if current_state is not None
                        else ErrorCode.FATAL.value
                    ),
                    error_message=(
                        "Session is no longer active for approval continuation."
                    ),
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            with self._lock:
                ctx = self._registry.session_states.get(session_id)
                if ctx is not None:
                    ctx.is_running = True

            try:
                loop = self._build_agent_loop(started=entry.started, runtime=runtime)
                result = loop.resume_after_approval(
                    continuation=entry.continuation,
                    approved=resolution.approved,
                    timed_out=resolution.timed_out,
                    executor=runtime,
                    session_state=current_state,
                    edited_args=edited_args,
                    on_approval_created=lambda approval, cont: (
                        self._register_pending_approval(
                            started=entry.started,
                            approval=approval,
                            continuation=cont,
                        )
                    ),
                    drain_messages=lambda: self._drain_queued_messages(session_id),
                )
            except Exception as exc:
                self._clear_session_execution_state(session_id, terminal_status="error")
                self._mark_failed_if_active(
                    session_id,
                    reason=f"agent continuation failed ({type(exc).__name__})",
                )
                record = SessionRunRecord(
                    session_id=session_id,
                    status="error",
                    error_code=ErrorCode.FATAL.value,
                    error_message="Session agent continuation failed.",
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            return self._finalize_loop_result(
                session_id=session_id,
                started=entry.started,
                runtime=runtime,
                result=result,
            )

    def _continue_session_messages(
        self,
        *,
        ctx: SessionExecutionState,
        snapshot: AgentConversationSnapshot,
        initial_messages: tuple[str, ...],
    ) -> SessionRunRecord:
        """Continue a completed ``ACTIVE`` session's conversation with new messages."""
        session_id = ctx.session_id
        with self._get_session_lock(session_id):
            if self._registry.is_shutdown:
                self._clear_session_execution_state(session_id, terminal_status="error")
                record = SessionRunRecord(
                    session_id=session_id,
                    status="error",
                    error_code=ErrorCode.SESSION_INACTIVE.value,
                    error_message="SessionRunner is shut down.",
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            runtime = self.get_runtime(session_id)
            current_state = self._get_session_state(session_id)
            if runtime is None or current_state != "ACTIVE":
                self._clear_session_execution_state(
                    session_id, terminal_status="blocked"
                )
                record = SessionRunRecord(
                    session_id=session_id,
                    status="blocked",
                    error_code=ErrorCode.SESSION_INACTIVE.value,
                    error_message="Session is not ACTIVE.",
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            extra = self._drain_queued_messages(session_id)
            combined = initial_messages + extra
            try:
                loop = self._build_agent_loop(started=ctx.started, runtime=runtime)
                result = loop.continue_with_messages(
                    snapshot=snapshot,
                    user_messages=combined,
                    executor=runtime,
                    session_state=current_state,
                    on_approval_created=lambda approval, cont: (
                        self._register_pending_approval(
                            started=ctx.started,
                            approval=approval,
                            continuation=cont,
                        )
                    ),
                    drain_messages=lambda: self._drain_queued_messages(session_id),
                )
            except Exception as exc:
                self._clear_session_execution_state(session_id, terminal_status="error")
                self._mark_failed_if_active(
                    session_id,
                    reason=f"agent message failed ({type(exc).__name__})",
                )
                record = SessionRunRecord(
                    session_id=session_id,
                    status="error",
                    error_code=ErrorCode.FATAL.value,
                    error_message="Session message execution failed.",
                )
                with self._lock:
                    self._run_records[session_id] = record
                return record

            return self._finalize_loop_result(
                session_id=session_id,
                started=ctx.started,
                runtime=runtime,
                result=result,
            )

    def send_session_message(
        self,
        *,
        session_id: str,
        text: str,
    ) -> Future[SessionRunRecord] | None:
        """Deliver an in-memory user message to an active session's AgentLoop."""
        if not isinstance(text, str) or not text.strip():
            raise SessionValidationError(
                "Message text must be a non-empty string.",
                code=ErrorCode.ARG_INVALID,
            )

        current_state = self._get_session_state(session_id)
        if current_state != "ACTIVE":
            raise SessionRunnerError(
                f"Cannot send message to session in state {current_state}.",
                code=ErrorCode.SESSION_INACTIVE,
            )

        with self._lock:
            if self._registry.is_shutdown:
                raise SessionRunnerError(
                    "SessionRunner is shut down.",
                    code=ErrorCode.SESSION_INACTIVE,
                )
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                raise SessionRunnerError(
                    "Session MCP runtime is unavailable.",
                    code=ErrorCode.SESSION_INACTIVE,
                )
            if any(
                e.session_id == session_id
                for e in self._registry.pending_approvals.values()
            ):
                raise SessionRunnerError(
                    "Session is awaiting approval; resolve the pending approval first.",
                    code=ErrorCode.POLICY_BLOCK,
                )
            ctx = self._registry.session_states.get(session_id)
            if ctx is None:
                raise SessionRunnerError(
                    "Session execution context is unavailable.",
                    code=ErrorCode.SESSION_INACTIVE,
                )
            if ctx.terminal_status is not None:
                raise SessionRunnerError(
                    "Session agent run is no longer active "
                    f"(status: {ctx.terminal_status}).",
                    code=ErrorCode.SESSION_INACTIVE,
                )
            if ctx.is_running:
                ctx.pending_messages.append(text)
                return self._futures.get(session_id)
            if ctx.snapshot is not None:
                snapshot = ctx.snapshot
                ctx.snapshot = None
                ctx.is_running = True
                future = self._executor.submit(
                    self._continue_session_messages,
                    ctx=ctx,
                    snapshot=snapshot,
                    initial_messages=(text,),
                )
                self._futures[session_id] = future
                return future
            raise SessionRunnerError(
                "Session is not eligible to accept messages.",
                code=ErrorCode.SESSION_INACTIVE,
            )

    def on_approval_resolved(
        self,
        resolution: ApprovalResolutionResult,
        *,
        edited_args: Mapping[str, Any] | None = None,
    ) -> Future[SessionRunRecord] | None:
        """Schedule continuation for a resolved approval, failing closed if missing."""
        if not isinstance(resolution, ApprovalResolutionResult):
            raise TypeError(
                "Expected ApprovalResolutionResult, "
                f"got {type(resolution).__name__}."
            )

        timer_to_cancel: threading.Timer | None = None
        with self._lock:
            if self._registry.is_shutdown:
                return None

            existing = self._registry.pending_approvals.get(resolution.approval_id)
            if existing is not None and existing.session_id != resolution.session_id:
                raise SessionRunnerError(
                    "Approval does not belong to session.",
                    code=ErrorCode.POLICY_BLOCK,
                )

            entry = self._registry.pending_approvals.pop(resolution.approval_id, None)
            if entry is None:
                missing_entry = True
            else:
                missing_entry = False

        if missing_entry:
            current_state = self._get_session_state(resolution.session_id)
            if current_state not in ("ENDING", "ENDED", "FAILED"):
                self._clear_session_execution_state(
                    resolution.session_id, terminal_status="error"
                )
                with self._lock:
                    self._run_records[resolution.session_id] = SessionRunRecord(
                        session_id=resolution.session_id,
                        status="error",
                        error_code=ErrorCode.FATAL.value,
                        error_message=(
                            "Missing in-memory continuation state for "
                            "approval resumption."
                        ),
                    )
            return None

        with self._lock:
            if self._registry.is_shutdown:
                if entry.timer is not None:
                    entry.timer.cancel()
                return None

            timer_to_cancel = entry.timer
            runtime = self._runtimes.get(resolution.session_id)
            if runtime is None:
                self._clear_session_execution_state(
                    resolution.session_id, terminal_status="error"
                )
                self._run_records[resolution.session_id] = SessionRunRecord(
                    session_id=resolution.session_id,
                    status="error",
                    error_code=ErrorCode.SESSION_INACTIVE.value,
                    error_message="Session MCP runtime is unavailable.",
                )
                future: Future[SessionRunRecord] | None = None
            else:
                ctx = self._registry.session_states.get(resolution.session_id)
                if ctx is not None:
                    ctx.is_running = True
                future = self._executor.submit(
                    self._resume_session_agent,
                    entry=entry,
                    resolution=resolution,
                    edited_args=edited_args,
                )
                self._futures[resolution.session_id] = future

        if timer_to_cancel is not None:
            timer_to_cancel.cancel()
        return future

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
            self._registry.session_states[started.session_id] = SessionExecutionState(
                session_id=started.session_id,
                started=started,
                is_running=True,
            )
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
        """Close MCP runtime, cancel pending timers, and drop in-memory state."""
        timers: list[threading.Timer] = []
        with self._lock:
            runtime = self._runtimes.pop(session_id, None)
            self._registry.pending_plans.pop(session_id, None)
            ctx = self._registry.session_states.pop(session_id, None)
            if ctx is not None:
                ctx.is_running = False
                ctx.snapshot = None
                ctx.pending_messages.clear()
            matching_ids = [
                aid
                for aid, entry in self._registry.pending_approvals.items()
                if entry.session_id == session_id
            ]
            for aid in matching_ids:
                entry = self._registry.pending_approvals.pop(aid)
                if entry.timer is not None:
                    timers.append(entry.timer)

        for timer in timers:
            timer.cancel()

        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime.close()
        with contextlib.suppress(Exception):
            self._undo_manager.clear_session_snapshots(session_id)

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel pending timers, wait for active runs, and close open runtimes."""
        with self._lock:
            self._registry.is_shutdown = True
            session_ids = list(self._runtimes.keys())
            futures = list(self._futures.values())
            pending_entries = list(self._registry.pending_approvals.values())
            self._registry.pending_approvals.clear()
            for ctx in self._registry.session_states.values():
                ctx.is_running = False
                ctx.snapshot = None
                ctx.pending_messages.clear()
            self._registry.session_states.clear()

        for entry in pending_entries:
            if entry.timer is not None:
                entry.timer.cancel()

        for sid in session_ids:
            self.close_runtime(sid)

        if wait:
            for fut in futures:
                with contextlib.suppress(Exception):
                    fut.result(timeout=5.0)
        self._executor.shutdown(wait=wait)

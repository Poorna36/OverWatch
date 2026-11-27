"""Phase 4 single-session agent/tool-call loop core.

Coordinates one session's turn loop between an injected provider-neutral
``LlmAdapter`` and ``AuditedGatewayCoordinator`` without ever calling an MCP
server directly or persisting raw ``request_text``.

Integration boundaries left for later work:
- Live vendor LLM SDK implementation behind ``LlmAdapter``.
- Automatic resumption of a paused/waiting turn after an approval resolves.
- USD cost tracking (``ProfileBudget.max_usd`` cannot be enforced until model
  token usage and pricing metadata exist).
- Live MCP transport invocation wired behind the injected ``executor``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from control_plane.agent.llm import (
    LlmAdapter,
    LlmAdapterError,
    LlmMessage,
    LlmToolCall,
    ToolDefinition,
)
from control_plane.audit.audit_writer import AuditWriterError
from control_plane.contracts import (
    ApprovalRequest,
    ErrorBody,
    ErrorCode,
    Plan,
    Policy,
    Profile,
    SessionState,
    ToolCall,
)
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.approval_manager import (
    ApprovalManager,
    ApprovalManagerError,
)
from control_plane.gateway.argument_validation import (
    _TOOL_ARG_MODELS,
    ArgumentValidationError,
    ValidatedArguments,
    validate_tool_arguments,
)
from control_plane.gateway.coordinator import (
    AuditedCallResult,
    AuditedGatewayCoordinator,
    GatewayCoordinatorError,
)
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
)
from control_plane.gateway.pipeline import PreflightRejectedError, execute_if_allowed

REPO_ROOT = Path(__file__).resolve().parents[2]

LoopStatus = Literal[
    "completed",
    "blocked",
    "awaiting_approval",
    "budget_exceeded",
    "error",
]


class AgentLoopError(Exception):
    """Raised when agent loop configuration (such as role prompt loading) fails."""


@dataclass(frozen=True)
class PendingApprovalContinuation:
    """In-memory-only agent state needed to resume after approval resolution.

    Never persisted to SQLite, audit entries, events, or logs because
    ``messages`` retains raw ``request_text`` and ``pending_call`` retains
    unredacted tool arguments.
    """

    session_id: str
    approval_id: str
    pending_call: ToolCall
    system_prompt: str
    tools: tuple[ToolDefinition, ...]
    messages: tuple[LlmMessage, ...]
    remaining_tool_calls: tuple[LlmToolCall, ...]
    calls_used: int
    calls_max: int
    turns: int
    audit_seqs: tuple[int, ...] = field(default_factory=tuple)

    def __repr__(self) -> str:
        return (
            f"PendingApprovalContinuation(session_id={self.session_id!r}, "
            f"approval_id={self.approval_id!r}, tool={self.pending_call.tool!r}, "
            f"calls_used={self.calls_used}, turns={self.turns})"
        )


@dataclass(frozen=True)
class AgentConversationSnapshot:
    """In-memory-only conversation state retained between active turns."""

    session_id: str
    system_prompt: str
    tools: tuple[ToolDefinition, ...]
    messages: tuple[LlmMessage, ...]
    calls_used: int
    calls_max: int
    turns: int
    audit_seqs: tuple[int, ...] = field(default_factory=tuple)

    def __repr__(self) -> str:
        return (
            f"AgentConversationSnapshot(session_id={self.session_id!r}, "
            f"calls_used={self.calls_used}, turns={self.turns})"
        )


@dataclass(frozen=True)
class AgentLoopResult:
    """Structured outcome returned after one agent loop execution stops."""

    session_id: str
    status: LoopStatus
    assistant_message: str | None = None
    error: ErrorBody | None = None
    pending_approval: ApprovalRequest | None = None
    calls_used: int = 0
    calls_max: int = 0
    turns: int = 0
    audit_seqs: tuple[int, ...] = field(default_factory=tuple)
    continuation: PendingApprovalContinuation | None = field(
        default=None, repr=False, compare=False
    )
    snapshot: AgentConversationSnapshot | None = field(
        default=None, repr=False, compare=False
    )


def build_allowed_tools(profile: Profile, policy: Policy) -> list[ToolDefinition]:
    """Build the LLM tool list from ``profile.mcp_servers`` ∩ ``policy.tools``."""
    tools: list[ToolDefinition] = []
    for tool_name in sorted(policy.tools.keys()):
        if "." not in tool_name:
            continue
        server_name, action = tool_name.split(".", 1)
        if not server_name or not action or server_name not in profile.mcp_servers:
            continue
        tool_policy = policy.tools[tool_name]
        arg_model = _TOOL_ARG_MODELS.get(tool_name)
        parameters = arg_model.model_json_schema() if arg_model is not None else {}
        tools.append(
            ToolDefinition(
                name=tool_name,
                description=f"{tool_name} (tier={tool_policy.tier})",
                parameters=parameters,
            )
        )
    return tools


def load_role_system_prompt(
    profile: Profile,
    *,
    base_dir: Path | str = REPO_ROOT,
) -> str:
    """Load and validate the role system prompt from ``profile.system_prompt_file``."""
    if not profile.system_prompt_file:
        raise AgentLoopError(
            f"Profile '{profile.name}' does not define system_prompt_file."
        )

    raw_rel = profile.system_prompt_file.strip()
    if not raw_rel or "\x00" in raw_rel or Path(raw_rel).is_absolute():
        raise AgentLoopError(
            f"Invalid system_prompt_file path {profile.system_prompt_file!r}."
        )

    root = Path(base_dir).resolve()
    candidate = (root / raw_rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise AgentLoopError(
            f"Trusted role prompt file not found within {root}: {raw_rel!r}."
        )

    try:
        content = candidate.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentLoopError(f"Failed to read role prompt {raw_rel!r}: {exc}") from exc

    if not content:
        raise AgentLoopError(f"Role prompt file {raw_rel!r} is empty.")
    return content


def _bound_tool_result_text(result: Any, cap_bytes: int) -> str:
    """Serialize tool output for the LLM conversation within ``cap_bytes``."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = repr(result)

    if cap_bytes <= 0:
        return ""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= cap_bytes:
        return text
    suffix = "...[truncated]"
    suffix_bytes = len(suffix.encode("utf-8"))
    if cap_bytes <= suffix_bytes:
        return raw[:cap_bytes].decode("utf-8", errors="ignore")
    return raw[: cap_bytes - suffix_bytes].decode("utf-8", errors="ignore") + suffix


def _emit_event(
    event_bus: EventBus,
    *,
    session_id: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    event_bus.publish(
        session_id=session_id,
        event_type=event_type,  # type: ignore[arg-type]
        data=data,
    )


def _emit_budget_event(
    event_bus: EventBus,
    *,
    session_id: str,
    calls_used: int,
    calls_max: int,
    usd_max: float,
) -> None:
    _emit_event(
        event_bus,
        session_id=session_id,
        event_type="budget",
        data={
            "calls_used": calls_used,
            "calls_max": calls_max,
            "usd_used": 0.0,
            "usd_max": usd_max,
        },
    )


class AgentLoop:
    """Executes one session's request/tool-call loop through the audited gateway."""

    def __init__(
        self,
        *,
        llm: LlmAdapter,
        coordinator: AuditedGatewayCoordinator,
        approval_manager: ApprovalManager,
        event_bus: EventBus,
        policy: Policy,
        profile: Profile,
        prompts_base_dir: Path | str = REPO_ROOT,
        workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
        approved_plan: Plan | None = None,
    ) -> None:
        self._llm = llm
        self._coordinator = coordinator
        self._approval_manager = approval_manager
        self._event_bus = event_bus
        self._policy = policy
        self._profile = profile
        self._prompts_base_dir = prompts_base_dir
        self._workspace_root = workspace_root
        self._approved_plan = approved_plan

    def run(
        self,
        *,
        session_id: str,
        request_text: str,
        executor: Callable[[ValidatedArguments], Any],
        session_state: SessionState = "ACTIVE",
        initial_calls_used: int = 0,
        on_approval_created: (
            Callable[[ApprovalRequest, PendingApprovalContinuation], None] | None
        ) = None,
        drain_messages: Callable[[], Sequence[str]] | None = None,
    ) -> AgentLoopResult:
        """Run turns until completion, block, ask (awaiting approval), or budget."""
        if not isinstance(request_text, str) or not request_text.strip():
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.ARG_INVALID,
                    message="request_text must be a non-empty string.",
                ),
                calls_used=initial_calls_used,
                calls_max=self._profile.budget.max_tool_calls,
            )

        if session_state != "ACTIVE":
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.SESSION_INACTIVE,
                    message=f"Session is not ACTIVE (state: {session_state}).",
                ),
                calls_used=initial_calls_used,
                calls_max=self._profile.budget.max_tool_calls,
            )

        system_prompt = load_role_system_prompt(
            self._profile, base_dir=self._prompts_base_dir
        )
        tools: tuple[ToolDefinition, ...] = tuple(
            build_allowed_tools(self._profile, self._policy)
        )
        calls_max = self._profile.budget.max_tool_calls
        calls_used = initial_calls_used
        turns = 0
        audit_seqs: list[int] = []

        # request_text lives solely in this ephemeral in-memory message list.
        messages: list[LlmMessage] = [LlmMessage(role="user", content=request_text)]

        return self._run_from_state(
            session_id=session_id,
            system_prompt=system_prompt,
            tools=tools,
            messages=messages,
            executor=executor,
            session_state=session_state,
            calls_used=calls_used,
            calls_max=calls_max,
            turns=turns,
            audit_seqs=audit_seqs,
            on_approval_created=on_approval_created,
            drain_messages=drain_messages,
        )

    def continue_with_messages(
        self,
        *,
        snapshot: AgentConversationSnapshot,
        user_messages: Sequence[str],
        executor: Callable[[ValidatedArguments], Any],
        session_state: SessionState = "ACTIVE",
        on_approval_created: (
            Callable[[ApprovalRequest, PendingApprovalContinuation], None] | None
        ) = None,
        drain_messages: Callable[[], Sequence[str]] | None = None,
    ) -> AgentLoopResult:
        """Continue an in-memory conversation with new user message(s)."""
        cleaned = [m for m in user_messages if isinstance(m, str) and m.strip()]
        if not cleaned:
            return AgentLoopResult(
                session_id=snapshot.session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.ARG_INVALID,
                    message="Message text must be a non-empty string.",
                ),
                calls_used=snapshot.calls_used,
                calls_max=snapshot.calls_max,
                turns=snapshot.turns,
                audit_seqs=snapshot.audit_seqs,
            )

        if session_state != "ACTIVE":
            return AgentLoopResult(
                session_id=snapshot.session_id,
                status="blocked",
                error=ErrorBody(
                    code=ErrorCode.SESSION_INACTIVE,
                    message=f"Session is not ACTIVE (state: {session_state}).",
                ),
                calls_used=snapshot.calls_used,
                calls_max=snapshot.calls_max,
                turns=snapshot.turns,
                audit_seqs=snapshot.audit_seqs,
            )

        messages = list(snapshot.messages)
        for text in cleaned:
            messages.append(LlmMessage(role="user", content=text))

        return self._run_from_state(
            session_id=snapshot.session_id,
            system_prompt=snapshot.system_prompt,
            tools=snapshot.tools,
            messages=messages,
            executor=executor,
            session_state=session_state,
            calls_used=snapshot.calls_used,
            calls_max=snapshot.calls_max,
            turns=snapshot.turns,
            audit_seqs=list(snapshot.audit_seqs),
            on_approval_created=on_approval_created,
            drain_messages=drain_messages,
        )

    def resume_after_approval(
        self,
        *,
        continuation: PendingApprovalContinuation,
        approved: bool,
        executor: Callable[[ValidatedArguments], Any],
        timed_out: bool = False,
        session_state: SessionState = "ACTIVE",
        edited_args: Mapping[str, Any] | None = None,
        on_approval_created: (
            Callable[[ApprovalRequest, PendingApprovalContinuation], None] | None
        ) = None,
        drain_messages: Callable[[], Sequence[str]] | None = None,
    ) -> AgentLoopResult:
        """Resume a paused ``AgentLoop`` after an approval resolution."""
        session_id = continuation.session_id
        call = continuation.pending_call
        messages = list(continuation.messages)
        audit_seqs = list(continuation.audit_seqs)
        calls_used = continuation.calls_used
        calls_max = continuation.calls_max
        turns = continuation.turns

        if not approved or timed_out:
            code = ErrorCode.APPROVAL_TIMEOUT if timed_out else ErrorCode.USER_DENIED
            msg = (
                f"Approval timed out for tool '{call.tool}'."
                if timed_out
                else f"User denied approval for tool '{call.tool}'."
            )
            err = ErrorBody(
                code=code,
                message=msg,
                details={"tool": call.tool, "approval_id": continuation.approval_id},
            )
            try:
                _emit_event(
                    self._event_bus,
                    session_id=session_id,
                    event_type="tool_result",
                    data={
                        "call_id": call.call_id,
                        "ok": False,
                        "error": err.model_dump(),
                        "duration_ms": 0.0,
                    },
                )
            except EventBusError as exc:
                return AgentLoopResult(
                    session_id=session_id,
                    status="error",
                    error=ErrorBody(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"Failed to publish tool_result event: {exc}",
                    ),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )

            if session_state != "ACTIVE":
                return AgentLoopResult(
                    session_id=session_id,
                    status="blocked",
                    error=ErrorBody(
                        code=ErrorCode.SESSION_INACTIVE,
                        message=f"Session is not ACTIVE (state: {session_state}).",
                    ),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )

            messages.append(
                LlmMessage(
                    role="tool",
                    content=_bound_tool_result_text(
                        {
                            "ok": False,
                            "decision": "asked_denied",
                            "error": err.model_dump(),
                        },
                        self._policy.defaults.output_cap_bytes,
                    ),
                    tool_call_id=call.call_id,
                    tool_name=call.tool,
                )
            )
            return self._run_from_state(
                session_id=session_id,
                system_prompt=continuation.system_prompt,
                tools=continuation.tools,
                messages=messages,
                executor=executor,
                session_state=session_state,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=audit_seqs,
                pending_tool_calls=continuation.remaining_tool_calls,
                on_approval_created=on_approval_created,
                drain_messages=drain_messages,
            )

        if edited_args is not None:
            if not isinstance(edited_args, Mapping):
                err = ErrorBody(
                    code=ErrorCode.ARG_INVALID,
                    message="Edited tool arguments must be a mapping.",
                    details={"tool": call.tool},
                )
                return self._record_post_approval_block(
                    session_id=session_id,
                    call=call,
                    args_redacted={},
                    tier=None,
                    err=err,
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=audit_seqs,
                )
            candidate_call = ToolCall(
                session_id=call.session_id,
                call_id=call.call_id,
                tool=call.tool,
                args=dict(edited_args),
            )
        else:
            candidate_call = call

        try:
            preflight = self._coordinator.pipeline.evaluate(
                candidate_call,
                self._profile,
                session_state=session_state,
                workspace_root=self._workspace_root,
            )
        except Exception as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.FATAL,
                    message=f"Gateway preflight revalidation failed: {exc}",
                ),
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        if preflight.decision == "block":
            err = preflight.error or ErrorBody(
                code=ErrorCode.POLICY_BLOCK,
                message=preflight.reason or "Blocked by gateway policy.",
                details={"tool": candidate_call.tool},
            )
            return self._record_post_approval_block(
                session_id=session_id,
                call=candidate_call,
                args_redacted=preflight.args_redacted,
                tier=preflight.tier,
                err=err,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=audit_seqs,
            )

        if preflight.decision not in ("allow", "ask"):
            err = ErrorBody(
                code=ErrorCode.POLICY_BLOCK,
                message=f"Unexpected preflight decision {preflight.decision!r}.",
                details={"tool": candidate_call.tool},
            )
            return self._record_post_approval_block(
                session_id=session_id,
                call=candidate_call,
                args_redacted=preflight.args_redacted,
                tier=preflight.tier,
                err=err,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=audit_seqs,
            )

        validated_args: ValidatedArguments | None = None
        if preflight.decision == "ask":
            pipeline_jail = getattr(self._coordinator.pipeline, "path_jail", None)
            try:
                jail = (
                    pipeline_jail
                    if pipeline_jail is not None
                    else PathJail(self._workspace_root)
                )
                validated_args = validate_tool_arguments(
                    tool=candidate_call.tool,
                    args=candidate_call.args,
                    tool_policy=preflight.tool_policy,
                    path_jail=jail,
                )
            except (PathJailError, ArgumentValidationError) as exc:
                return self._record_post_approval_block(
                    session_id=session_id,
                    call=candidate_call,
                    args_redacted=preflight.args_redacted,
                    tier=preflight.tier,
                    err=exc.to_error_body(),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=audit_seqs,
                )

        if edited_args is not None:
            try:
                audit_entry = self._coordinator.audit_writer.append(
                    session_id=preflight.session_id,
                    tool=preflight.tool,
                    args=preflight.args_redacted,
                    decision="asked_approved",
                    reason="approved edited arguments passed preflight",
                    tier=preflight.tier,
                )
                audit_seqs.append(audit_entry.seq)
            except AuditWriterError as exc:
                return AgentLoopResult(
                    session_id=session_id,
                    status="error",
                    error=ErrorBody(
                        code=ErrorCode.FATAL,
                        message=f"Gateway audit persistence failed: {exc}",
                    ),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )

        start_ns = time.monotonic_ns()
        try:
            if preflight.decision == "allow":
                output = execute_if_allowed(preflight, executor)
            else:
                assert validated_args is not None
                output = executor(validated_args)
        except PreflightRejectedError as exc:
            return self._record_post_approval_block(
                session_id=session_id,
                call=candidate_call,
                args_redacted=preflight.args_redacted,
                tier=preflight.tier,
                err=exc.error,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=audit_seqs,
            )
        except Exception as exc:
            exec_error = ErrorBody(
                code=ErrorCode.UPSTREAM_ERROR,
                message=f"Tool execution failed: {exc}",
            )
            duration_ms = round((time.monotonic_ns() - start_ns) / 1_000_000, 3)
            try:
                _emit_event(
                    self._event_bus,
                    session_id=session_id,
                    event_type="tool_result",
                    data={
                        "call_id": candidate_call.call_id,
                        "ok": False,
                        "error": exec_error.model_dump(),
                        "duration_ms": duration_ms,
                    },
                )
            except EventBusError:
                pass
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=exec_error,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        duration_ms = round((time.monotonic_ns() - start_ns) / 1_000_000, 3)
        bounded_text = _bound_tool_result_text(
            output,
            self._policy.defaults.output_cap_bytes,
        )
        try:
            _emit_event(
                self._event_bus,
                session_id=session_id,
                event_type="tool_result",
                data={
                    "call_id": candidate_call.call_id,
                    "ok": True,
                    "duration_ms": duration_ms,
                },
            )
        except EventBusError as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.UPSTREAM_ERROR,
                    message=f"Failed to publish tool_result event: {exc}",
                ),
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        messages.append(
            LlmMessage(
                role="tool",
                content=bounded_text,
                tool_call_id=candidate_call.call_id,
                tool_name=candidate_call.tool,
            )
        )
        return self._run_from_state(
            session_id=session_id,
            system_prompt=continuation.system_prompt,
            tools=continuation.tools,
            messages=messages,
            executor=executor,
            session_state=session_state,
            calls_used=calls_used,
            calls_max=calls_max,
            turns=turns,
            audit_seqs=audit_seqs,
            pending_tool_calls=continuation.remaining_tool_calls,
            on_approval_created=on_approval_created,
            drain_messages=drain_messages,
        )

    def _record_post_approval_block(
        self,
        *,
        session_id: str,
        call: ToolCall,
        args_redacted: dict[str, Any],
        tier: Any,
        err: ErrorBody,
        calls_used: int,
        calls_max: int,
        turns: int,
        audit_seqs: list[int],
    ) -> AgentLoopResult:
        """Audit and emit events when an approved call fails preflight recheck."""
        try:
            audit_entry = self._coordinator.audit_writer.append(
                session_id=session_id,
                tool=call.tool,
                args=args_redacted,
                decision="blocked",
                reason=err.message or err.code,
                tier=tier,
            )
            audit_seqs.append(audit_entry.seq)
        except AuditWriterError as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.FATAL,
                    message=f"Gateway audit persistence failed: {exc}",
                ),
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        try:
            _emit_event(
                self._event_bus,
                session_id=session_id,
                event_type="tool_call",
                data={
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "decision": "block",
                    "args_redacted": args_redacted,
                },
            )
            _emit_event(
                self._event_bus,
                session_id=session_id,
                event_type="blocked",
                data={
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "code": err.code,
                    "reason": err.message,
                },
            )
        except EventBusError as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.UPSTREAM_ERROR,
                    message=f"Failed to publish blocked event: {exc}",
                ),
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        return AgentLoopResult(
            session_id=session_id,
            status="blocked",
            error=err,
            calls_used=calls_used,
            calls_max=calls_max,
            turns=turns,
            audit_seqs=tuple(audit_seqs),
        )

    def _run_from_state(
        self,
        *,
        session_id: str,
        system_prompt: str,
        tools: tuple[ToolDefinition, ...],
        messages: list[LlmMessage],
        executor: Callable[[ValidatedArguments], Any],
        session_state: SessionState,
        calls_used: int,
        calls_max: int,
        turns: int,
        audit_seqs: list[int],
        pending_tool_calls: tuple[LlmToolCall, ...] = (),
        on_approval_created: (
            Callable[[ApprovalRequest, PendingApprovalContinuation], None] | None
        ) = None,
        drain_messages: Callable[[], Sequence[str]] | None = None,
    ) -> AgentLoopResult:
        """Execute any remaining tool calls, then run subsequent LLM turns."""
        for idx, llm_call in enumerate(pending_tool_calls):
            outcome = self._process_tool_call(
                session_id=session_id,
                llm_call=llm_call,
                executor=executor,
                session_state=session_state,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=audit_seqs,
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                remaining_tool_calls=pending_tool_calls[idx + 1 :],
                on_approval_created=on_approval_created,
            )
            if outcome is not None:
                return outcome
            calls_used += 1

        while True:
            if drain_messages is not None:
                for queued_text in drain_messages():
                    if isinstance(queued_text, str) and queued_text.strip():
                        messages.append(LlmMessage(role="user", content=queued_text))

            turns += 1
            try:
                turn = self._llm.next_turn(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                )
            except LlmAdapterError as exc:
                return AgentLoopResult(
                    session_id=session_id,
                    status="error",
                    error=ErrorBody(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"LLM adapter failure: {exc}",
                    ),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )

            if not turn.tool_calls:
                final_text = turn.assistant_text or ""
                if final_text:
                    try:
                        _emit_event(
                            self._event_bus,
                            session_id=session_id,
                            event_type="agent_message",
                            data={"text": final_text},
                        )
                    except EventBusError as exc:
                        return AgentLoopResult(
                            session_id=session_id,
                            status="error",
                            error=ErrorBody(
                                code=ErrorCode.UPSTREAM_ERROR,
                                message=f"Event publication failed: {exc}",
                            ),
                            calls_used=calls_used,
                            calls_max=calls_max,
                            turns=turns,
                            audit_seqs=tuple(audit_seqs),
                        )

                messages.append(LlmMessage(role="assistant", content=final_text))
                if drain_messages is not None:
                    queued_after = [
                        q for q in drain_messages() if isinstance(q, str) and q.strip()
                    ]
                    if queued_after:
                        for queued_text in queued_after:
                            messages.append(
                                LlmMessage(role="user", content=queued_text)
                            )
                        continue

                snapshot = AgentConversationSnapshot(
                    session_id=session_id,
                    system_prompt=system_prompt,
                    tools=tools,
                    messages=tuple(messages),
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )
                return AgentLoopResult(
                    session_id=session_id,
                    status="completed",
                    assistant_message=final_text,
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                    snapshot=snapshot,
                )

            messages.append(
                LlmMessage(
                    role="assistant",
                    content=turn.assistant_text or "",
                    tool_calls=turn.tool_calls,
                )
            )

            for idx, llm_call in enumerate(turn.tool_calls):
                outcome = self._process_tool_call(
                    session_id=session_id,
                    llm_call=llm_call,
                    executor=executor,
                    session_state=session_state,
                    calls_used=calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=audit_seqs,
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    remaining_tool_calls=turn.tool_calls[idx + 1 :],
                    on_approval_created=on_approval_created,
                )
                if outcome is not None:
                    return outcome
                calls_used += 1

    def _process_tool_call(
        self,
        *,
        session_id: str,
        llm_call: LlmToolCall,
        executor: Callable[[ValidatedArguments], Any],
        session_state: SessionState,
        calls_used: int,
        calls_max: int,
        turns: int,
        audit_seqs: list[int],
        messages: list[LlmMessage],
        system_prompt: str,
        tools: tuple[ToolDefinition, ...],
        remaining_tool_calls: tuple[LlmToolCall, ...] = (),
        on_approval_created: (
            Callable[[ApprovalRequest, PendingApprovalContinuation], None] | None
        ) = None,
    ) -> AgentLoopResult | None:
        """Process one requested tool call, returning a result only on stop."""
        if calls_used >= calls_max:
            err = ErrorBody(
                code=ErrorCode.BUDGET_EXCEEDED,
                message=(
                    f"Tool call budget exhausted ({calls_used}/{calls_max} calls)."
                ),
            )
            try:
                _emit_event(
                    self._event_bus,
                    session_id=session_id,
                    event_type="blocked",
                    data={
                        "call_id": llm_call.call_id,
                        "tool": llm_call.tool,
                        "code": err.code,
                        "reason": err.message,
                    },
                )
                _emit_budget_event(
                    self._event_bus,
                    session_id=session_id,
                    calls_used=calls_used,
                    calls_max=calls_max,
                    usd_max=self._profile.budget.max_usd,
                )
            except EventBusError:
                pass
            return AgentLoopResult(
                session_id=session_id,
                status="budget_exceeded",
                error=err,
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        call = ToolCall(
            session_id=session_id,
            call_id=llm_call.call_id,
            tool=llm_call.tool,
            args=dict(llm_call.args),
        )

        exec_error: ErrorBody | None = None
        start_ns = time.monotonic_ns()

        def _guarded_executor(validated_args: ValidatedArguments) -> Any:
            return executor(validated_args)

        try:
            audited: AuditedCallResult = self._coordinator.handle_call(
                call,
                self._profile,
                executor=_guarded_executor,
                session_state=session_state,
                workspace_root=self._workspace_root,
                approved_plan=self._approved_plan,
            )
        except (GatewayCoordinatorError, AuditWriterError) as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.FATAL,
                    message=f"Gateway coordinator failure: {exc}",
                ),
                calls_used=calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )
        except Exception as exc:
            exec_error = ErrorBody(
                code=ErrorCode.UPSTREAM_ERROR,
                message=f"Tool execution failed: {exc}",
            )
            duration_ms = round((time.monotonic_ns() - start_ns) / 1_000_000, 3)
            new_calls_used = calls_used + 1
            try:
                _emit_event(
                    self._event_bus,
                    session_id=session_id,
                    event_type="tool_result",
                    data={
                        "call_id": call.call_id,
                        "ok": False,
                        "error": exec_error.model_dump(),
                        "duration_ms": duration_ms,
                    },
                )
                _emit_budget_event(
                    self._event_bus,
                    session_id=session_id,
                    calls_used=new_calls_used,
                    calls_max=calls_max,
                    usd_max=self._profile.budget.max_usd,
                )
            except EventBusError:
                pass
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=exec_error,
                calls_used=new_calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        duration_ms = round((time.monotonic_ns() - start_ns) / 1_000_000, 3)
        audit_seqs.append(audited.audit_entry.seq)
        new_calls_used = calls_used + 1
        decision = audited.tool_result.decision

        try:
            _emit_event(
                self._event_bus,
                session_id=session_id,
                event_type="tool_call",
                data={
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "decision": decision,
                    "args_redacted": audited.preflight.args_redacted,
                },
            )
            _emit_budget_event(
                self._event_bus,
                session_id=session_id,
                calls_used=new_calls_used,
                calls_max=calls_max,
                usd_max=self._profile.budget.max_usd,
            )

            if decision == "block":
                err = audited.tool_result.error or ErrorBody(
                    code=ErrorCode.POLICY_BLOCK,
                    message="Blocked by gateway policy.",
                    details={"tool": call.tool},
                )
                _emit_event(
                    self._event_bus,
                    session_id=session_id,
                    event_type="blocked",
                    data={
                        "call_id": call.call_id,
                        "tool": call.tool,
                        "code": err.code,
                        "reason": err.message,
                    },
                )
                return AgentLoopResult(
                    session_id=session_id,
                    status="blocked",
                    error=err,
                    calls_used=new_calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )

            if decision == "ask":
                pending = audited.preflight.pending_approval
                if pending is None:
                    return AgentLoopResult(
                        session_id=session_id,
                        status="error",
                        error=ErrorBody(
                            code=ErrorCode.FATAL,
                            message="Missing pending approval metadata for ask call.",
                        ),
                        calls_used=new_calls_used,
                        calls_max=calls_max,
                        turns=turns,
                        audit_seqs=tuple(audit_seqs),
                    )
                creation = self._approval_manager.create_approval(
                    pending, policy=self._policy
                )
                continuation = PendingApprovalContinuation(
                    session_id=session_id,
                    approval_id=creation.approval.approval_id,
                    pending_call=call,
                    system_prompt=system_prompt,
                    tools=tools,
                    messages=tuple(messages),
                    remaining_tool_calls=remaining_tool_calls,
                    calls_used=new_calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                )
                if on_approval_created is not None:
                    on_approval_created(creation.approval, continuation)
                return AgentLoopResult(
                    session_id=session_id,
                    status="awaiting_approval",
                    pending_approval=creation.approval,
                    calls_used=new_calls_used,
                    calls_max=calls_max,
                    turns=turns,
                    audit_seqs=tuple(audit_seqs),
                    continuation=continuation,
                )

            # Allowed call executed via injected gateway executor
            bounded_text = _bound_tool_result_text(
                audited.tool_result.result,
                self._policy.defaults.output_cap_bytes,
            )
            _emit_event(
                self._event_bus,
                session_id=session_id,
                event_type="tool_result",
                data={
                    "call_id": call.call_id,
                    "ok": True,
                    "duration_ms": duration_ms,
                },
            )
        except (EventBusError, ApprovalManagerError) as exc:
            return AgentLoopResult(
                session_id=session_id,
                status="error",
                error=ErrorBody(
                    code=ErrorCode.UPSTREAM_ERROR,
                    message=f"Failed to persist approval or event: {exc}",
                ),
                calls_used=new_calls_used,
                calls_max=calls_max,
                turns=turns,
                audit_seqs=tuple(audit_seqs),
            )

        messages.append(
            LlmMessage(
                role="tool",
                content=bounded_text,
                tool_call_id=call.call_id,
                tool_name=call.tool,
            )
        )
        return None


def run_session_loop(
    *,
    session_id: str,
    request_text: str,
    llm: LlmAdapter,
    coordinator: AuditedGatewayCoordinator,
    approval_manager: ApprovalManager,
    event_bus: EventBus,
    policy: Policy,
    profile: Profile,
    executor: Callable[[ValidatedArguments], Any],
    session_state: SessionState = "ACTIVE",
    initial_calls_used: int = 0,
    prompts_base_dir: Path | str = REPO_ROOT,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
) -> AgentLoopResult:
    """Convenience function executing one session's ``AgentLoop``."""
    return AgentLoop(
        llm=llm,
        coordinator=coordinator,
        approval_manager=approval_manager,
        event_bus=event_bus,
        policy=policy,
        profile=profile,
        prompts_base_dir=prompts_base_dir,
        workspace_root=workspace_root,
    ).run(
        session_id=session_id,
        request_text=request_text,
        executor=executor,
        session_state=session_state,
        initial_calls_used=initial_calls_used,
    )

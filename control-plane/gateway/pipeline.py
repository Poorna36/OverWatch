"""Phase 2 gateway preflight pipeline (F-02).

Combines the policy decision engine (``policy_engine.py``), strict tool
argument validation (``argument_validation.py``), workspace ``PathJail``
(``path_jail.py``), and audit redaction (``audit_writer.sanitize_audit_args``)
into a single fail-closed preflight gate before any later execution or
approval layer.

Preflight pipeline order (``docs/ARCHITECTURE.md`` §4, fail-closed):
1. Policy evaluation (``evaluate_tool_call``):
   - Session state must be ``ACTIVE`` (else ``SESSION_INACTIVE``).
   - Tool MCP server must be permitted by ``profile.mcp_servers``
     (else ``NOT_IN_PROFILE``).
   - Tool must exist in ``policy.tools`` and resolve a valid tier
     (else ``POLICY_BLOCK``).
   - Profile ``tool_overrides`` and pre-call ``escalations`` may only tighten.
2. Workspace jail & argument validation (``validate_tool_arguments``):
   - Runs for any non-blocked call (both ``allow`` and ``ask``) so invalid
     arguments, ``PathJail`` escapes, and ``deny_patterns`` never reach an
     approval card or executor.
3. Redaction & typed result emission:
   - Sanitizes arguments via ``sanitize_audit_args`` for safe logging and
     approval-card rendering (stripping secrets, redacting file/email bodies,
     truncating strings to 200 chars).
   - Returns ``GatewayPreflightResult`` marking:
     - ``allow``: ``executable=True``, ``audit_decision="allowed"``,
       ``validated_args`` populated for a later execution layer.
     - ``ask``: ``executable=False``, ``needs_approval=True``,
       ``validated_args=None``, ``pending_approval`` populated with redacted-safe
       fields only, and ``audit_decision="asked"``.
     - ``block``: ``executable=False``, ``needs_approval=False``,
       ``validated_args=None``, ``error`` populated with contract
       ``ErrorBody``, and ``audit_decision="blocked"``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from control_plane.audit.audit_writer import AuditWriterError, sanitize_audit_args
from control_plane.contracts import (
    ApprovalRequest,
    AuditDecision,
    Decision,
    ErrorBody,
    ErrorCode,
    Plan,
    Policy,
    Profile,
    SessionState,
    Tier,
    ToolCall,
    ToolPolicy,
)
from control_plane.gateway.argument_validation import (
    ArgumentValidationError,
    ValidatedArguments,
    validate_tool_arguments,
)
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
)
from control_plane.gateway.policy_engine import PolicyDecision, evaluate_tool_call

# Tools listed in docs/CONTRACTS.md §12 as having inverse undo capture.
_UNDOABLE_TOOLS: frozenset[str] = frozenset(
    {
        "files.write",
        "calendar.create_event",
        "calendar.update_event",
        "gmail.create_draft",
    }
)

_T = TypeVar("_T")


class PreflightRejectedError(Exception):
    """Raised when an execution guard receives a non-executable preflight result."""

    def __init__(self, decision: Decision, error: ErrorBody) -> None:
        super().__init__(error.message)
        self.decision = decision
        self.error = error


@dataclass(frozen=True)
class PendingApprovalData:
    """Validated, redacted-safe preflight payload for a later approval layer."""

    session_id: str
    call_id: str
    tool: str
    tier: Tier
    args_redacted: dict[str, Any]
    reason: str
    irreversible: bool

    def to_approval_request(
        self,
        *,
        approval_id: str,
        expires_at: str,
        warnings: list[str] | None = None,
    ) -> ApprovalRequest:
        """Build a contract ``ApprovalRequest`` once approval_id/expiry exist."""
        return ApprovalRequest(
            approval_id=approval_id,
            session_id=self.session_id,
            call_id=self.call_id,
            tool=self.tool,
            tier=self.tier,
            args_redacted=dict(self.args_redacted),
            reason=self.reason,
            irreversible=self.irreversible,
            warnings=warnings,
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class GatewayPreflightResult:
    """Typed outcome of the Phase 2 gateway preflight pipeline."""

    call_id: str
    session_id: str
    tool: str
    decision: Decision
    tier: Tier | None = None
    reason: str = ""
    executable: bool = False
    needs_approval: bool = False
    validated_args: ValidatedArguments | None = None
    args_redacted: dict[str, Any] = field(default_factory=dict)
    audit_decision: AuditDecision | None = None
    error: ErrorBody | None = None
    escalated: bool = False
    tool_policy: ToolPolicy | None = None
    pending_approval: PendingApprovalData | None = None

    @property
    def is_blocked(self) -> bool:
        """Return True when the tool call was blocked by preflight checks."""
        return self.decision == "block"


def _safe_redact_blocked_args(raw_args: Any) -> dict[str, Any]:
    """Best-effort fail-closed redaction for already-blocked calls."""
    if not isinstance(raw_args, dict):
        return {}
    try:
        return sanitize_audit_args(raw_args)
    except AuditWriterError:
        return {}


def _blocked_result(
    *,
    call: ToolCall,
    tier: Tier | None,
    error: ErrorBody,
    args_redacted: dict[str, Any],
    escalated: bool = False,
    tool_policy: ToolPolicy | None = None,
) -> GatewayPreflightResult:
    """Build a fail-closed blocked preflight result."""
    return GatewayPreflightResult(
        call_id=call.call_id,
        session_id=call.session_id,
        tool=call.tool,
        decision="block",
        tier=tier,
        reason=error.message,
        executable=False,
        needs_approval=False,
        validated_args=None,
        args_redacted=args_redacted,
        audit_decision="blocked",
        error=error,
        escalated=escalated,
        tool_policy=tool_policy,
        pending_approval=None,
    )


def run_preflight(
    call: ToolCall,
    *,
    policy: Policy,
    profile: Profile,
    session_state: SessionState = "ACTIVE",
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    path_jail: PathJail | None = None,
    approved_plan: Plan | None = None,
) -> GatewayPreflightResult:
    """Evaluate a contract ``ToolCall`` through policy, jail, and arg checks.

    Never executes MCP tools and never treats ``ask`` calls as approved.
    """
    if not isinstance(call, ToolCall):
        raise TypeError(f"Expected contract ToolCall, got {type(call).__name__}.")
    if not isinstance(policy, Policy):
        raise TypeError(f"Expected contract Policy, got {type(policy).__name__}.")
    if not isinstance(profile, Profile):
        raise TypeError(f"Expected contract Profile, got {type(profile).__name__}.")
    if path_jail is not None and not isinstance(path_jail, PathJail):
        raise TypeError(f"Expected PathJail or None, got {type(path_jail).__name__}.")

    # Step 1: Session state, profile membership, tool policy, overrides, escalations
    policy_decision: PolicyDecision = evaluate_tool_call(
        call=call,
        policy=policy,
        profile=profile,
        session_state=session_state,
    )

    if policy_decision.decision == "block":
        err_code = policy_decision.error_code or ErrorCode.POLICY_BLOCK
        return _blocked_result(
            call=call,
            tier=policy_decision.tier,
            error=ErrorBody(
                code=err_code,
                message=policy_decision.reason or "Blocked by gateway policy.",
                details={"tool": call.tool},
            ),
            args_redacted=_safe_redact_blocked_args(call.args),
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    # Step 2: Workspace PathJail initialization (fail closed if root is invalid)
    try:
        jail = path_jail if path_jail is not None else PathJail(workspace_root)
    except PathJailError as exc:
        return _blocked_result(
            call=call,
            tier=policy_decision.tier,
            error=exc.to_error_body(),
            args_redacted=_safe_redact_blocked_args(call.args),
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    # Step 3: Strict argument schema, size cap, PathJail, and deny_patterns
    try:
        validated_args = validate_tool_arguments(
            tool=call.tool,
            args=call.args,
            tool_policy=policy_decision.tool_policy,
            path_jail=jail,
        )
    except ArgumentValidationError as exc:
        return _blocked_result(
            call=call,
            tier=policy_decision.tier,
            error=exc.to_error_body(),
            args_redacted=_safe_redact_blocked_args(call.args),
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    # Step 4: Sanitize arguments for audit logging and approval rendering
    try:
        args_redacted = sanitize_audit_args(validated_args.args)
    except AuditWriterError as exc:
        return _blocked_result(
            call=call,
            tier=policy_decision.tier,
            error=ErrorBody(
                code=ErrorCode.ARG_INVALID,
                message=f"Argument sanitization failed: {exc}",
                details={"tool": call.tool},
            ),
            args_redacted=_safe_redact_blocked_args(call.args),
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    # Defensive check: a resolved allow/ask decision must carry a tier
    if policy_decision.tier is None:
        return _blocked_result(
            call=call,
            tier=None,
            error=ErrorBody(
                code=ErrorCode.POLICY_BLOCK,
                message=f"Policy decision for '{call.tool}' is missing a tier.",
                details={"tool": call.tool},
            ),
            args_redacted=args_redacted,
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    # Step 4.5: Escalate off-plan tool calls to 'ask' (F-11), never relaxing block
    resolved_decision = policy_decision.decision
    resolved_reason = policy_decision.reason
    resolved_escalated = policy_decision.escalated
    if approved_plan is not None and resolved_decision == "allow":
        planned_tools = {step.tool for step in approved_plan.steps}
        if call.tool not in planned_tools:
            resolved_decision = "ask"
            resolved_reason = "off-plan tool call requires approval"
            resolved_escalated = True

    # Step 5: Keep approval-required calls in unresolved 'ask' state
    if resolved_decision == "ask":
        irreversible = (
            policy_decision.tier == "sensitive" or call.tool not in _UNDOABLE_TOOLS
        )
        pending = PendingApprovalData(
            session_id=call.session_id,
            call_id=call.call_id,
            tool=call.tool,
            tier=policy_decision.tier,
            args_redacted=args_redacted,
            reason=resolved_reason,
            irreversible=irreversible,
        )
        return GatewayPreflightResult(
            call_id=call.call_id,
            session_id=call.session_id,
            tool=call.tool,
            decision="ask",
            tier=policy_decision.tier,
            reason=resolved_reason,
            executable=False,
            needs_approval=True,
            validated_args=None,
            args_redacted=args_redacted,
            audit_decision="asked",
            error=None,
            escalated=resolved_escalated,
            tool_policy=policy_decision.tool_policy,
            pending_approval=pending,
        )

    # Step 6: Only explicit 'allow' reaches executable=True
    if resolved_decision != "allow":
        return _blocked_result(
            call=call,
            tier=policy_decision.tier,
            error=ErrorBody(
                code=ErrorCode.POLICY_BLOCK,
                message=f"Unexpected policy decision {policy_decision.decision!r}.",
                details={"tool": call.tool},
            ),
            args_redacted=args_redacted,
            escalated=policy_decision.escalated,
            tool_policy=policy_decision.tool_policy,
        )

    return GatewayPreflightResult(
        call_id=call.call_id,
        session_id=call.session_id,
        tool=call.tool,
        decision="allow",
        tier=policy_decision.tier,
        reason=policy_decision.reason,
        executable=True,
        needs_approval=False,
        validated_args=validated_args,
        args_redacted=args_redacted,
        audit_decision="allowed",
        error=None,
        escalated=policy_decision.escalated,
        tool_policy=policy_decision.tool_policy,
        pending_approval=None,
    )


def execute_if_allowed(
    preflight: GatewayPreflightResult,
    executor: Callable[[ValidatedArguments], _T],
) -> _T:
    """Invoke ``executor`` if and only if ``preflight`` is explicitly executable.

    Fail-closed guard ensuring ``ask`` and ``block`` results never reach an
    execution callback.
    """
    if (
        not preflight.executable
        or preflight.decision != "allow"
        or preflight.error is not None
        or preflight.validated_args is None
    ):
        err = preflight.error or ErrorBody(
            code=ErrorCode.POLICY_BLOCK,
            message=(
                f"Call '{preflight.call_id}' ({preflight.tool}) is not executable "
                f"(decision={preflight.decision})."
            ),
            details={"tool": preflight.tool, "decision": preflight.decision},
        )
        raise PreflightRejectedError(preflight.decision, err)

    return executor(preflight.validated_args)


class GatewayPreflightPipeline:
    """Reusable Phase 2 gateway preflight pipeline bound to policy and root."""

    def __init__(
        self,
        policy: Policy,
        workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
        path_jail: PathJail | None = None,
    ) -> None:
        if not isinstance(policy, Policy):
            raise TypeError(f"Expected contract Policy, got {type(policy).__name__}.")
        if path_jail is not None and not isinstance(path_jail, PathJail):
            raise TypeError(
                f"Expected PathJail or None, got {type(path_jail).__name__}."
            )
        self.policy = policy
        self.workspace_root = workspace_root
        self.path_jail = path_jail

    def evaluate(
        self,
        call: ToolCall,
        profile: Profile,
        *,
        session_state: SessionState = "ACTIVE",
        workspace_root: Path | str | None = None,
        path_jail: PathJail | None = None,
        approved_plan: Plan | None = None,
    ) -> GatewayPreflightResult:
        """Run preflight checks for ``call`` without executing any tool."""
        root = self.workspace_root if workspace_root is None else workspace_root
        jail = self.path_jail if path_jail is None else path_jail
        return run_preflight(
            call,
            policy=self.policy,
            profile=profile,
            session_state=session_state,
            workspace_root=root,
            path_jail=jail,
            approved_plan=approved_plan,
        )

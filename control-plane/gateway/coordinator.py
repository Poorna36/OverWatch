"""Audited gateway coordinator composing preflight evaluation and audit persistence.

Executes preflight checks (``GatewayPreflightPipeline``), persists every
preflight decision (``"allowed"``, ``"asked"``, or ``"blocked"``) into the
append-only hash-chained audit log (``AuditWriter``) before any executor runs,
and invokes the injected executor callback if and only if the call is
explicitly ``allow`` / ``executable=True``.

Fail-closed guarantees:
- If preflight rejects a call (``ask`` or ``block``), its audit record is
  persisted and the executor callback is never called.
- If audit persistence fails, execution fails closed immediately and the
  executor callback is never called.
- Transport wiring and approval resolution remain out of scope here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import (
    AuditDecision,
    AuditEntry,
    Decision,
    ErrorBody,
    ErrorCode,
    Plan,
    Policy,
    Profile,
    SessionState,
    ToolCall,
    ToolResult,
)
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.path_jail import DEFAULT_WORKSPACE_ROOT
from control_plane.gateway.pipeline import (
    GatewayPreflightPipeline,
    GatewayPreflightResult,
    execute_if_allowed,
)

_EXPECTED_AUDIT_DECISION: dict[Decision, AuditDecision] = {
    "allow": "allowed",
    "ask": "asked",
    "block": "blocked",
}


class GatewayCoordinatorError(Exception):
    """Raised when coordinator preflight-to-audit invariants fail."""


@dataclass(frozen=True)
class AuditedCallResult:
    """Typed outcome of an audited gateway coordinator invocation."""

    preflight: GatewayPreflightResult
    audit_entry: AuditEntry
    tool_result: ToolResult
    executed: bool


def _resolve_audit_decision(preflight: GatewayPreflightResult) -> AuditDecision:
    """Validate that preflight decision maps consistently to an AuditDecision."""
    expected = _EXPECTED_AUDIT_DECISION.get(preflight.decision)
    if expected is None:
        raise GatewayCoordinatorError(
            f"Unsupported preflight decision {preflight.decision!r}."
        )
    if preflight.audit_decision != expected:
        raise GatewayCoordinatorError(
            f"Preflight audit_decision mismatch for decision={preflight.decision!r}: "
            f"expected {expected!r}, got {preflight.audit_decision!r}."
        )
    return expected


def _resolve_audit_reason(
    preflight: GatewayPreflightResult,
    audit_decision: AuditDecision,
) -> str:
    """Resolve a non-empty audit reason string aligned with contracts."""
    reason = preflight.reason.strip()
    if reason:
        return reason
    if preflight.error is not None and preflight.error.code.strip():
        return preflight.error.code.strip()
    return audit_decision


class AuditedGatewayCoordinator:
    """Coordinates preflight checks, fail-closed audit writes, and execution."""

    def __init__(
        self,
        pipeline: GatewayPreflightPipeline,
        audit_writer: AuditWriter,
    ) -> None:
        if not hasattr(pipeline, "evaluate") or not callable(pipeline.evaluate):
            raise TypeError("pipeline must provide a callable evaluate(...) method.")
        if not hasattr(audit_writer, "append") or not callable(audit_writer.append):
            raise TypeError("audit_writer must provide a callable append(...) method.")
        self.pipeline = pipeline
        self.audit_writer = audit_writer

    def handle_call(
        self,
        call: ToolCall,
        profile: Profile,
        *,
        executor: Callable[[ValidatedArguments], Any],
        session_state: SessionState = "ACTIVE",
        workspace_root: Path | str | None = None,
        approved_plan: Plan | None = None,
    ) -> AuditedCallResult:
        """Evaluate, audit-log, and conditionally execute a single ``ToolCall``."""
        if not callable(executor):
            raise TypeError("executor must be callable.")

        preflight = self.pipeline.evaluate(
            call,
            profile,
            session_state=session_state,
            workspace_root=workspace_root,
            approved_plan=approved_plan,
        )
        audit_decision = _resolve_audit_decision(preflight)
        audit_reason = _resolve_audit_reason(preflight, audit_decision)

        # Persist the gateway decision BEFORE any executor can run (fail-closed).
        audit_entry = self.audit_writer.append(
            session_id=preflight.session_id,
            tool=preflight.tool,
            args=preflight.args_redacted,
            decision=audit_decision,
            reason=audit_reason,
            tier=preflight.tier,
        )

        if preflight.decision == "block":
            error = preflight.error or ErrorBody(
                code=ErrorCode.POLICY_BLOCK,
                message=audit_reason,
                details={"tool": preflight.tool},
            )
            return AuditedCallResult(
                preflight=preflight,
                audit_entry=audit_entry,
                tool_result=ToolResult(
                    call_id=preflight.call_id,
                    ok=False,
                    decision="block",
                    result=None,
                    error=error,
                    audit_seq=audit_entry.seq,
                ),
                executed=False,
            )

        if preflight.decision == "ask":
            return AuditedCallResult(
                preflight=preflight,
                audit_entry=audit_entry,
                tool_result=ToolResult(
                    call_id=preflight.call_id,
                    ok=False,
                    decision="ask",
                    result=None,
                    error=None,
                    audit_seq=audit_entry.seq,
                ),
                executed=False,
            )

        output = execute_if_allowed(preflight, executor)
        return AuditedCallResult(
            preflight=preflight,
            audit_entry=audit_entry,
            tool_result=ToolResult(
                call_id=preflight.call_id,
                ok=True,
                decision="allow",
                result=output,
                error=None,
                audit_seq=audit_entry.seq,
            ),
            executed=True,
        )


def coordinate_tool_call(
    call: ToolCall,
    *,
    policy: Policy,
    profile: Profile,
    audit_writer: AuditWriter,
    executor: Callable[[ValidatedArguments], Any],
    session_state: SessionState = "ACTIVE",
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
) -> AuditedCallResult:
    """Convenience helper wrapping ``AuditedGatewayCoordinator`` for one call."""
    pipeline = GatewayPreflightPipeline(policy=policy, workspace_root=workspace_root)
    coordinator = AuditedGatewayCoordinator(
        pipeline=pipeline, audit_writer=audit_writer
    )
    return coordinator.handle_call(
        call,
        profile,
        executor=executor,
        session_state=session_state,
        workspace_root=workspace_root,
    )

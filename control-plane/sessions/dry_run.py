"""F-12 Dry-run preview recording and drift-checked apply."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from control_plane.audit.audit_writer import canonical_json, truncate_string
from control_plane.contracts import (
    ApprovalRequest,
    Plan,
    Policy,
    Profile,
    SessionState,
    ToolCall,
)
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.sessions.undo_manager import FileReader, sha256_text

_ABSENT_SENTINEL = "ABSENT"


def _summarize_dry_run_change(tool: str, args_redacted: dict[str, Any]) -> str:
    return truncate_string(f"{tool} {canonical_json(args_redacted)}", max_len=200)


@dataclass(frozen=True)
class DryRunStep:
    """In-memory recorded write/mutating call captured during dry-run."""

    call: ToolCall
    summary: str
    baseline_sha256: str | None = None


@dataclass(frozen=True)
class DryRunApplyResult:
    """Counts returned by ``POST /sessions/{id}/dry_run/apply``."""

    applied: int
    flagged: int
    pending_approvals: tuple[ApprovalRequest, ...] = field(default_factory=tuple)


class DryRunManager:
    """Tracks in-memory dry-run writes per session and applies them safely."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._steps: dict[str, list[DryRunStep]] = {}

    def record_step(
        self,
        *,
        session_id: str,
        call: ToolCall,
        args_redacted: dict[str, Any],
        reader: FileReader | None = None,
    ) -> list[dict[str, str]]:
        """Record a non-read call during dry-run and return redacted diff items."""
        baseline: str | None = None
        if reader is not None and call.tool in ("files.write", "files.delete"):
            rel_path = call.args.get("path")
            if isinstance(rel_path, str):
                exists, content = reader(rel_path)
                baseline = (
                    sha256_text(content)
                    if exists and content is not None
                    else _ABSENT_SENTINEL
                )

        step = DryRunStep(
            call=call,
            summary=_summarize_dry_run_change(call.tool, args_redacted),
            baseline_sha256=baseline,
        )
        with self._lock:
            items = self._steps.setdefault(session_id, [])
            items.append(step)
            return [{"tool": item.call.tool, "summary": item.summary} for item in items]

    def get_steps(self, session_id: str) -> list[DryRunStep]:
        with self._lock:
            return list(self._steps.get(session_id, ()))

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._steps.pop(session_id, None)

    def apply(
        self,
        *,
        session_id: str,
        coordinator: AuditedGatewayCoordinator,
        approval_manager: ApprovalManager,
        profile: Profile,
        policy: Policy,
        executor: Callable[[ValidatedArguments], Any],
        reader: FileReader | None,
        get_session_state: Callable[[], SessionState | None],
        approved_plan: Plan | None = None,
    ) -> DryRunApplyResult:
        """Replay recorded dry-run steps through the gateway with drift checks."""
        with self._lock:
            steps = list(self._steps.pop(session_id, ()))

        applied = 0
        flagged = 0
        approvals: list[ApprovalRequest] = []

        for idx, step in enumerate(steps):
            current_state = get_session_state()
            if current_state != "ACTIVE":
                flagged += len(steps) - idx
                break

            # Drift check for file mutations
            if (
                step.baseline_sha256 is not None
                and reader is not None
                and step.call.tool in ("files.write", "files.delete")
            ):
                rel_path = str(step.call.args.get("path", ""))
                exists, content = reader(rel_path)
                current_hash = (
                    sha256_text(content)
                    if exists and content is not None
                    else _ABSENT_SENTINEL
                )
                if current_hash != step.baseline_sha256:
                    flagged += 1
                    continue

            replay_call = ToolCall(
                session_id=session_id,
                call_id=f"apply_{step.call.call_id}",
                tool=step.call.tool,
                args=dict(step.call.args),
            )
            audited = coordinator.handle_call(
                replay_call,
                profile,
                executor=executor,
                session_state=current_state,
                approved_plan=approved_plan,
                dry_run=False,
            )
            if audited.executed:
                applied += 1
            elif (
                audited.preflight.decision == "ask"
                and audited.preflight.pending_approval is not None
            ):
                created = approval_manager.create_approval(
                    audited.preflight.pending_approval,
                    policy=policy,
                )
                approvals.append(created.approval)
                flagged += 1
            else:
                flagged += 1

        return DryRunApplyResult(
            applied=applied,
            flagged=flagged,
            pending_approvals=tuple(approvals),
        )

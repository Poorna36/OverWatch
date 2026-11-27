"""Gateway policy decision engine (Phase 2, F-02).

Evaluates a ``ToolCall`` against the loaded ``Policy``, ``Profile``, and
session state following ``docs/ARCHITECTURE.md`` §4, ``docs/CONTRACTS.md``
§§1, 4–6, and ``docs/SECURITY.md`` §4.

Scope:
- Enforces ``ACTIVE`` session state (``SESSION_INACTIVE``).
- Enforces profile MCP server membership (``NOT_IN_PROFILE``).
- Enforces policy tool allowlisting (unknown tools block with ``POLICY_BLOCK``).
- Resolves base tier decisions and profile ``tool_overrides`` (strict-only:
  overrides can tighten ``allow -> ask -> block``, never relax).
- Evaluates pre-call ``escalations`` rules (e.g., terminal network commands),
  which can also only tighten decisions and never turn ``ask``/``block`` into
  ``allow``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from control_plane.contracts import (
    Decision,
    ErrorCode,
    EscalationRule,
    Policy,
    Profile,
    SessionState,
    Tier,
    ToolCall,
    ToolPolicy,
)

# Decision strictness ordering: allow < ask < block.
_DECISION_RANK: dict[Decision, int] = {
    "allow": 0,
    "ask": 1,
    "block": 2,
}

_VALID_TIERS: frozenset[str] = frozenset(
    {"read", "reversible", "sensitive", "forbidden"}
)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating a ToolCall against session state, profile, and policy."""

    decision: Decision
    tier: Tier | None = None
    reason: str = ""
    error_code: ErrorCode | None = None
    escalated: bool = False
    tool_policy: ToolPolicy | None = None


def _tighten_decision(current: Decision, candidate: Decision) -> Decision:
    """Return the stricter of two decisions (never relax allow <- ask <- block)."""
    if _DECISION_RANK[candidate] > _DECISION_RANK[current]:
        return candidate
    return current


def _extract_server_name(tool_name: str) -> str | None:
    """Extract the MCP server prefix from '<server>.<action>'."""
    if not isinstance(tool_name, str) or "." not in tool_name:
        return None
    server, action = tool_name.split(".", 1)
    if not server or not action:
        return None
    return server


def _matches_escalation_rule(rule: EscalationRule, call: ToolCall) -> bool:
    """Return True if a pre-call escalation rule matches the ToolCall."""
    if rule.decision is None:
        # Post-call rules (e.g. output_bytes_gt -> action: truncate) have no decision.
        return False

    when = rule.when
    if not isinstance(when, dict) or not when:
        return False

    # Post-execution condition cannot be evaluated pre-call.
    if "output_bytes_gt" in when:
        return False

    rule_tool = when.get("tool")
    if rule_tool is not None and rule_tool != "*" and rule_tool != call.tool:
        return False

    pattern = when.get("matches")
    if pattern is not None:
        if not isinstance(pattern, str):
            return False
        command = call.args.get("command")
        if isinstance(command, str):
            haystacks = [command]
        else:
            haystacks = [v for v in call.args.values() if isinstance(v, str)]
        if not any(re.search(pattern, text) for text in haystacks):
            return False

    # Reject unknown condition keys so unimplemented conditions never silently pass.
    known_keys = {"tool", "matches"}
    if not set(when.keys()).issubset(known_keys):
        return False

    return True


def _apply_profile_override(
    decision: Decision,
    tier: Tier,
    override: dict[str, object] | None,
    policy: Policy,
) -> tuple[Decision, Tier, ErrorCode | None, str | None]:
    """Apply a profile tool_override if and only if it tightens the decision.

    Returns:
        (updated_decision, updated_tier, error_code_if_invalid, failure_reason)
    """
    if not override:
        return decision, tier, None, None

    updated_decision = decision
    updated_tier = tier

    if "tier" in override:
        raw_tier = override["tier"]
        if not isinstance(raw_tier, str) or raw_tier not in _VALID_TIERS:
            return (
                "block",
                tier,
                ErrorCode.POLICY_BLOCK,
                f"Invalid tier override {raw_tier!r}.",
            )
        override_tier: Tier = raw_tier  # type: ignore[assignment]
        tier_cfg = policy.tiers.get(override_tier)
        if tier_cfg is None:
            return (
                "block",
                tier,
                ErrorCode.POLICY_BLOCK,
                f"Override tier {override_tier!r} is not configured in policy.tiers.",
            )
        tightened = _tighten_decision(updated_decision, tier_cfg.decision)
        if tightened != updated_decision:
            updated_decision = tightened
            updated_tier = override_tier

    if "decision" in override:
        raw_decision = override["decision"]
        if raw_decision not in _DECISION_RANK:
            return (
                "block",
                updated_tier,
                ErrorCode.POLICY_BLOCK,
                f"Invalid decision override {raw_decision!r}.",
            )
        override_decision: Decision = raw_decision  # type: ignore[assignment]
        updated_decision = _tighten_decision(updated_decision, override_decision)

    return updated_decision, updated_tier, None, None


def evaluate_tool_call(
    call: ToolCall,
    policy: Policy,
    profile: Profile,
    session_state: SessionState = "ACTIVE",
) -> PolicyDecision:
    """Evaluate a ToolCall against session state, profile, and gateway policy."""
    # 1. Session must be ACTIVE
    if session_state != "ACTIVE":
        return PolicyDecision(
            decision="block",
            tier=None,
            reason=f"Session is not ACTIVE (state: {session_state}).",
            error_code=ErrorCode.SESSION_INACTIVE,
        )

    # 2. Tool's MCP server must be enabled in the session's profile
    server_name = _extract_server_name(call.tool)
    if server_name is None or server_name not in profile.mcp_servers:
        return PolicyDecision(
            decision="block",
            tier=None,
            reason=f"Tool '{call.tool}' is not permitted by profile '{profile.name}'.",
            error_code=ErrorCode.NOT_IN_PROFILE,
        )

    # 3. Tool must exist in policy.tools (unknown tools block)
    tool_policy = policy.tools.get(call.tool)
    if tool_policy is None:
        return PolicyDecision(
            decision="block",
            tier=None,
            reason=f"Tool '{call.tool}' is not in policy.tools (unknown_tool=block).",
            error_code=ErrorCode.POLICY_BLOCK,
        )

    # 4. Resolve base tier decision (fail closed if tier config is missing)
    effective_tier: Tier = tool_policy.tier
    tier_cfg = policy.tiers.get(effective_tier)
    if tier_cfg is None:
        return PolicyDecision(
            decision="block",
            tier=effective_tier,
            reason=(
                f"Tier '{effective_tier}' for tool '{call.tool}' "
                "is missing from policy.tiers."
            ),
            error_code=ErrorCode.POLICY_BLOCK,
            tool_policy=tool_policy,
        )

    decision: Decision = tier_cfg.decision
    reason = f"tier={effective_tier} -> {decision}"

    # 5. Apply profile tool_overrides (strict-only: may only tighten)
    override = profile.tool_overrides.get(call.tool)
    decision, effective_tier, err_code, err_reason = _apply_profile_override(
        decision, effective_tier, override, policy
    )
    if err_code is not None:
        return PolicyDecision(
            decision="block",
            tier=effective_tier,
            reason=err_reason or "Invalid profile tool override.",
            error_code=err_code,
            tool_policy=tool_policy,
        )
    if override and decision != tier_cfg.decision:
        reason = f"profile override -> {decision}"

    # 6. Evaluate escalations (strict-only: may only tighten)
    escalated = False
    for rule in policy.escalations:
        try:
            matched = _matches_escalation_rule(rule, call)
        except re.error as exc:
            return PolicyDecision(
                decision="block",
                tier=effective_tier,
                reason=f"Invalid escalation regex: {exc}",
                error_code=ErrorCode.POLICY_BLOCK,
                tool_policy=tool_policy,
            )

        if matched and rule.decision is not None:
            tightened = _tighten_decision(decision, rule.decision)
            if _DECISION_RANK[tightened] > _DECISION_RANK[decision]:
                decision = tightened
                escalated = True
                if rule.reason:
                    reason = rule.reason
                else:
                    reason = f"escalated -> {decision}"

    error_code = ErrorCode.POLICY_BLOCK if decision == "block" else None
    return PolicyDecision(
        decision=decision,
        tier=effective_tier,
        reason=reason,
        error_code=error_code,
        escalated=escalated,
        tool_policy=tool_policy,
    )


class PolicyEngine:
    """Stateless policy evaluator bound to a validated gateway Policy."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def evaluate(
        self,
        call: ToolCall,
        profile: Profile,
        session_state: SessionState = "ACTIVE",
    ) -> PolicyDecision:
        """Evaluate a ToolCall using the configured Policy."""
        return evaluate_tool_call(
            call=call,
            policy=self.policy,
            profile=profile,
            session_state=session_state,
        )

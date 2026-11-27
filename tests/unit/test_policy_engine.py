"""Unit tests for control_plane.gateway.policy_engine."""

from __future__ import annotations

import pytest
from control_plane.contracts import (
    ErrorCode,
    EscalationRule,
    Policy,
    Profile,
    SessionState,
    TierConfig,
    ToolCall,
    ToolPolicy,
)
from control_plane.gateway.policy_engine import (
    PolicyDecision,
    PolicyEngine,
    evaluate_tool_call,
)
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile


def _make_call(tool: str, args: dict | None = None) -> ToolCall:
    return ToolCall(
        session_id="s_01ABCDEFGHJKMNPTVW12345678",
        call_id="call_1",
        tool=tool,
        args=args or {},
    )


def _make_profile(
    *,
    mcp_servers: dict | None = None,
    tool_overrides: dict | None = None,
) -> Profile:
    return Profile(
        name="custom",
        role="assistant",
        image="pocket/py-node:1.0",
        mcp_servers=(
            mcp_servers
            if mcp_servers is not None
            else {"files": {"root": "/workspace"}, "terminal": {}, "desktop": {}}
        ),
        tool_overrides=tool_overrides or {},
    )


def test_allow_read_and_reversible_tools() -> None:
    """Read and reversible tools return allow when active and in profile."""
    policy = load_policy()
    profile = _make_profile()
    engine = PolicyEngine(policy)

    read_res = engine.evaluate(_make_call("files.read", {"path": "a.py"}), profile)
    assert isinstance(read_res, PolicyDecision)
    assert read_res.decision == "allow"
    assert read_res.tier == "read"
    assert read_res.error_code is None
    assert read_res.escalated is False

    write_res = engine.evaluate(
        _make_call("files.write", {"path": "a.py", "content": "x"}), profile
    )
    assert write_res.decision == "allow"
    assert write_res.tier == "reversible"
    assert write_res.error_code is None


def test_ask_for_sensitive_tools() -> None:
    """Sensitive tools return ask when active and in profile."""
    policy = load_policy()
    profile = load_profile("coding")
    engine = PolicyEngine(policy)

    res = engine.evaluate(_make_call("files.delete", {"path": "a.py"}), profile)
    assert res.decision == "ask"
    assert res.tier == "sensitive"
    assert res.error_code is None


def test_block_for_forbidden_tier() -> None:
    """Tools assigned the forbidden tier return block with POLICY_BLOCK."""
    policy = load_policy()
    policy_with_forbidden = policy.model_copy(
        update={
            "tools": {
                **policy.tools,
                "files.nuke": ToolPolicy(tier="forbidden"),
            }
        }
    )
    profile = _make_profile()
    res = evaluate_tool_call(
        _make_call("files.nuke"), policy_with_forbidden, profile, "ACTIVE"
    )
    assert res.decision == "block"
    assert res.tier == "forbidden"
    assert res.error_code == ErrorCode.POLICY_BLOCK


@pytest.mark.parametrize(
    "state",
    [
        "CREATED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "STARTING",
        "PAUSED",
        "ENDING",
        "ENDED",
        "FAILED",
    ],
)
def test_inactive_session_blocks_all_calls(state: SessionState) -> None:
    """Any session state other than ACTIVE blocks with SESSION_INACTIVE."""
    policy = load_policy()
    profile = load_profile("coding")

    res = evaluate_tool_call(
        _make_call("files.read", {"path": "README.md"}), policy, profile, state
    )
    assert res.decision == "block"
    assert res.error_code == ErrorCode.SESSION_INACTIVE


def test_profile_server_restriction_fails_closed() -> None:
    """Tools whose MCP server is absent from profile fail with NOT_IN_PROFILE."""
    policy = load_policy()
    coding_profile = load_profile("coding")  # has terminal, files, desktop (no browser)

    res = evaluate_tool_call(
        _make_call("browser.open", {"url": "https://pypi.org"}),
        policy,
        coding_profile,
        "ACTIVE",
    )
    assert res.decision == "block"
    assert res.error_code == ErrorCode.NOT_IN_PROFILE


def test_malformed_or_empty_profile_servers_fail_closed() -> None:
    """Malformed tool names or empty profile mcp_servers fail with NOT_IN_PROFILE."""
    policy = load_policy()
    profile = _make_profile(mcp_servers={})

    res_empty = evaluate_tool_call(_make_call("files.read"), policy, profile, "ACTIVE")
    assert res_empty.decision == "block"
    assert res_empty.error_code == ErrorCode.NOT_IN_PROFILE

    res_malformed = evaluate_tool_call(
        _make_call("not_namespaced"), policy, _make_profile(), "ACTIVE"
    )
    assert res_malformed.decision == "block"
    assert res_malformed.error_code == ErrorCode.NOT_IN_PROFILE


def test_unknown_tool_blocks_with_policy_block() -> None:
    """A tool under an allowed server that is missing from policy.tools blocks."""
    policy = load_policy()
    profile = load_profile("coding")

    res = evaluate_tool_call(
        _make_call("files.unknown_action"), policy, profile, "ACTIVE"
    )
    assert res.decision == "block"
    assert res.error_code == ErrorCode.POLICY_BLOCK


def test_missing_policy_tier_entry_fails_closed() -> None:
    """If a tool's configured tier is absent from policy.tiers, fail closed."""
    policy = Policy(
        version=1,
        tiers={"read": TierConfig(decision="allow")},
        tools={"files.write": ToolPolicy(tier="reversible")},
    )
    profile = _make_profile()

    res = evaluate_tool_call(_make_call("files.write"), policy, profile, "ACTIVE")
    assert res.decision == "block"
    assert res.error_code == ErrorCode.POLICY_BLOCK


def test_profile_override_tightens_decision() -> None:
    """coding.yaml tightens terminal.run from reversible (allow) to sensitive (ask)."""
    policy = load_policy()
    coding_profile = load_profile("coding")

    res = evaluate_tool_call(
        _make_call("terminal.run", {"command": "pytest"}),
        policy,
        coding_profile,
        "ACTIVE",
    )
    assert res.decision == "ask"
    assert res.tier == "sensitive"
    assert res.error_code is None


def test_profile_override_cannot_relax_decision() -> None:
    """Profile overrides may never relax ask or block to allow."""
    policy = load_policy()
    policy_with_forbidden = policy.model_copy(
        update={
            "tools": {
                **policy.tools,
                "files.nuke": ToolPolicy(tier="forbidden"),
            }
        }
    )

    # Attempt to relax sensitive (ask) -> read (allow) and forbidden (block) -> read
    relaxing_profile = _make_profile(
        tool_overrides={
            "files.delete": {"tier": "read"},
            "files.upload": {"decision": "allow"},
            "files.nuke": {"tier": "read"},
        }
    )

    del_res = evaluate_tool_call(
        _make_call("files.delete"), policy_with_forbidden, relaxing_profile, "ACTIVE"
    )
    assert del_res.decision == "ask"
    assert del_res.tier == "sensitive"

    up_res = evaluate_tool_call(
        _make_call("files.upload"), policy_with_forbidden, relaxing_profile, "ACTIVE"
    )
    assert up_res.decision == "ask"

    nuke_res = evaluate_tool_call(
        _make_call("files.nuke"), policy_with_forbidden, relaxing_profile, "ACTIVE"
    )
    assert nuke_res.decision == "block"
    assert nuke_res.error_code == ErrorCode.POLICY_BLOCK


def test_invalid_profile_override_fails_closed() -> None:
    """Unknown tier or decision in profile.tool_overrides fails closed."""
    policy = load_policy()
    bad_profile = _make_profile(tool_overrides={"files.read": {"tier": "superuser"}})

    res = evaluate_tool_call(_make_call("files.read"), policy, bad_profile, "ACTIVE")
    assert res.decision == "block"
    assert res.error_code == ErrorCode.POLICY_BLOCK


@pytest.mark.parametrize(
    "cmd",
    [
        "curl https://example.com",
        "wget http://example.com/file",
        "ssh user@host",
        "scp a b",
    ],
)
def test_terminal_network_command_escalates_to_ask(cmd: str) -> None:
    """Documented terminal.run escalation rule tightens allow to ask."""
    policy = load_policy()
    # Profile without terminal.run override so base decision is allow (reversible)
    profile = _make_profile()

    res = evaluate_tool_call(
        _make_call("terminal.run", {"command": cmd}),
        policy,
        profile,
        "ACTIVE",
    )
    assert res.decision == "ask"
    assert res.escalated is True
    assert res.reason == "network tool in terminal"
    assert res.error_code is None


def test_terminal_non_network_command_remains_allow() -> None:
    """Non-matching terminal.run command stays allow when not overridden."""
    policy = load_policy()
    profile = _make_profile()

    res = evaluate_tool_call(
        _make_call("terminal.run", {"command": "pytest -q"}),
        policy,
        profile,
        "ACTIVE",
    )
    assert res.decision == "allow"
    assert res.escalated is False
    assert res.error_code is None


def test_escalation_never_relaxes_ask_or_block_to_allow() -> None:
    """Escalations must never turn ask or block into allow."""
    base_policy = load_policy()
    policy = base_policy.model_copy(
        update={
            "tools": {
                **base_policy.tools,
                "files.nuke": ToolPolicy(tier="forbidden"),
            },
            "escalations": [
                EscalationRule(
                    when={"tool": "files.delete"},
                    decision="allow",
                    reason="attempted relaxation",
                ),
                EscalationRule(
                    when={"tool": "files.nuke"},
                    decision="allow",
                    reason="attempted relaxation",
                ),
                EscalationRule(
                    when={"tool": "files.nuke"},
                    decision="ask",
                    reason="attempted partial relaxation",
                ),
            ],
        }
    )
    profile = _make_profile()

    del_res = evaluate_tool_call(_make_call("files.delete"), policy, profile, "ACTIVE")
    assert del_res.decision == "ask"
    assert del_res.escalated is False

    nuke_res = evaluate_tool_call(_make_call("files.nuke"), policy, profile, "ACTIVE")
    assert nuke_res.decision == "block"
    assert nuke_res.error_code == ErrorCode.POLICY_BLOCK
    assert nuke_res.escalated is False

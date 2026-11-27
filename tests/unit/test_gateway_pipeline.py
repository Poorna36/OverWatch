"""Focused unit tests for the Phase 2 gateway preflight pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from control_plane.contracts import (
    ApprovalRequest,
    ErrorCode,
    Policy,
    Profile,
    SessionState,
    ToolCall,
    ToolPolicy,
)
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.pipeline import (
    GatewayPreflightPipeline,
    GatewayPreflightResult,
    PendingApprovalData,
    PreflightRejectedError,
    execute_if_allowed,
    run_preflight,
)
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile

SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Create an isolated temporary session workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    src = ws / "src"
    src.mkdir()
    (src / "app.py").write_text("print('ok')\n", encoding="utf-8")
    return ws


@pytest.fixture
def policy() -> Policy:
    """Load the shipped repository gateway policy."""
    return load_policy()


@pytest.fixture
def coding_profile() -> Profile:
    """Load the shipped coding profile."""
    return load_profile("coding")


@pytest.fixture
def general_profile() -> Profile:
    """Load the shipped general profile."""
    return load_profile("general")


def _call(tool: str, args: dict | None = None, call_id: str = "call_1") -> ToolCall:
    return ToolCall(
        session_id=SESSION_ID,
        call_id=call_id,
        tool=tool,
        args={} if args is None else args,
    )


# ---------------------------------------------------------------------------
# 1. Allow Decisions
# ---------------------------------------------------------------------------
def test_allow_valid_read_and_write_calls(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Valid read/reversible calls pass preflight with redacted audit args."""
    pipeline = GatewayPreflightPipeline(policy, workspace_root=workspace_dir)

    read_res = pipeline.evaluate(
        _call("files.read", {"path": "./src/app.py"}), coding_profile
    )
    assert isinstance(read_res, GatewayPreflightResult)
    assert read_res.decision == "allow"
    assert read_res.tier == "read"
    assert read_res.executable is True
    assert read_res.needs_approval is False
    assert read_res.is_blocked is False
    assert read_res.audit_decision == "allowed"
    assert read_res.error is None
    assert read_res.pending_approval is None
    assert read_res.validated_args == ValidatedArguments(
        tool="files.read",
        args={"path": "src/app.py"},
        resolved_paths={"path": "src/app.py"},
    )
    assert read_res.args_redacted == {"path": "src/app.py"}

    write_res = pipeline.evaluate(
        _call("files.write", {"path": "src/new.py", "content": "SECRET_FILE_BODY"}),
        coding_profile,
    )
    assert write_res.decision == "allow"
    assert write_res.executable is True
    assert write_res.audit_decision == "allowed"
    assert write_res.validated_args is not None
    assert write_res.validated_args.args["content"] == "SECRET_FILE_BODY"
    assert "SECRET_FILE_BODY" not in str(write_res.args_redacted)
    assert write_res.args_redacted["content"].startswith("[REDACTED ")


def test_executable_allow_reaches_execution_callback(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Execution callback runs only when preflight decision is allow."""
    res = run_preflight(
        _call("files.list", {"path": "."}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    callback = MagicMock(return_value={"entries": []})

    out = execute_if_allowed(res, callback)

    assert out == {"entries": []}
    callback.assert_called_once_with(res.validated_args)


# ---------------------------------------------------------------------------
# 2. Ask Decisions & Unresolved Approval Contract Gap Handling
# ---------------------------------------------------------------------------
def test_ask_sensitive_tool_stays_pending_and_redacted(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Sensitive tools return ask, stay non-executable, and carry redacted data."""
    long_dest = "https://upload.example.com/" + ("x" * 240)
    res = run_preflight(
        _call(
            "files.upload",
            {"path": "./src/app.py", "destination": long_dest},
            call_id="call_ask_1",
        ),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )

    assert res.decision == "ask"
    assert res.tier == "sensitive"
    assert res.executable is False
    assert res.needs_approval is True
    assert res.is_blocked is False
    assert res.error is None
    assert res.validated_args is None

    assert res.audit_decision == "asked"

    assert isinstance(res.pending_approval, PendingApprovalData)
    assert res.pending_approval.session_id == SESSION_ID
    assert res.pending_approval.call_id == "call_ask_1"
    assert res.pending_approval.tool == "files.upload"
    assert res.pending_approval.tier == "sensitive"
    assert res.pending_approval.irreversible is True
    assert res.pending_approval.args_redacted["path"] == "src/app.py"
    assert len(res.pending_approval.args_redacted["destination"]) == 200

    # Converts cleanly to contract ApprovalRequest when approval_id & expiry exist
    req = res.pending_approval.to_approval_request(
        approval_id="a_01ABCDEFGHJKMNPTVW12345678",
        expires_at="2026-11-26T22:00:00Z",
    )
    assert isinstance(req, ApprovalRequest)
    assert req.args_redacted == res.pending_approval.args_redacted


def test_escalated_terminal_call_returns_ask_without_executing(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Escalated network command under terminal.run enters ask state safely."""
    permissive_profile = coding_profile.model_copy(update={"tool_overrides": {}})
    res = run_preflight(
        _call("terminal.run", {"command": "curl https://pypi.org/simple/"}),
        policy=policy,
        profile=permissive_profile,
        workspace_root=workspace_dir,
    )

    assert res.decision == "ask"
    assert res.escalated is True
    assert res.executable is False
    assert res.needs_approval is True
    assert res.audit_decision == "asked"
    assert res.pending_approval is not None
    assert res.pending_approval.reason == "network tool in terminal"
    assert res.pending_approval.irreversible is True


# ---------------------------------------------------------------------------
# 3. Blocked Decisions (Policy, State, Profile, Args, PathJail, DenyPattern)
# ---------------------------------------------------------------------------
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
def test_inactive_session_blocked(
    policy: Policy,
    coding_profile: Profile,
    workspace_dir: Path,
    state: SessionState,
) -> None:
    """Non-ACTIVE sessions block immediately with SESSION_INACTIVE."""
    res = run_preflight(
        _call("files.read", {"path": "src/app.py"}),
        policy=policy,
        profile=coding_profile,
        session_state=state,
        workspace_root=workspace_dir,
    )

    assert res.decision == "block"
    assert res.executable is False
    assert res.needs_approval is False
    assert res.audit_decision == "blocked"
    assert res.error is not None
    assert res.error.code == ErrorCode.SESSION_INACTIVE


def test_profile_and_unknown_tool_blocked_default_deny(
    policy: Policy,
    coding_profile: Profile,
    general_profile: Profile,
    workspace_dir: Path,
) -> None:
    """Out-of-profile and unknown tools fail closed with NOT_IN_PROFILE or block."""
    # coding does not include browser MCP server
    res_profile = run_preflight(
        _call("browser.open", {"url": "https://pypi.org"}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_profile.decision == "block"
    assert res_profile.executable is False
    assert res_profile.audit_decision == "blocked"
    assert res_profile.error is not None
    assert res_profile.error.code == ErrorCode.NOT_IN_PROFILE

    # unknown tool under an allowed MCP server -> default deny POLICY_BLOCK
    res_unknown = run_preflight(
        _call("files.chmod", {"path": "src/app.py", "mode": "777"}),
        policy=policy,
        profile=general_profile,
        workspace_root=workspace_dir,
    )
    assert res_unknown.decision == "block"
    assert res_unknown.executable is False
    assert res_unknown.audit_decision == "blocked"
    assert res_unknown.error is not None
    assert res_unknown.error.code == ErrorCode.POLICY_BLOCK


def test_forbidden_tier_and_override_blocked(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Tools marked forbidden in policy or profile override block with POLICY_BLOCK."""
    policy_forbidden = policy.model_copy(
        update={
            "tools": {
                **policy.tools,
                "files.list": ToolPolicy(tier="forbidden"),
            }
        }
    )
    res = run_preflight(
        _call("files.list", {"path": "."}),
        policy=policy_forbidden,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res.decision == "block"
    assert res.executable is False
    assert res.error is not None
    assert res.error.code == ErrorCode.POLICY_BLOCK


def test_invalid_arguments_and_max_arg_bytes_rejected(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Schema errors, unknown args, and oversized args fail closed with ARG_INVALID."""
    # Unknown extra field on allow-tier tool
    res_extra = run_preflight(
        _call("files.read", {"path": "src/app.py", "unexpected": True}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_extra.decision == "block"
    assert res_extra.executable is False
    assert res_extra.error is not None
    assert res_extra.error.code == ErrorCode.ARG_INVALID

    # Invalid args on ask-tier tool must block as ARG_INVALID, not emit 'ask'
    res_ask_bad_args = run_preflight(
        _call("files.upload", {"path": "src/app.py"}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_ask_bad_args.decision == "block"
    assert res_ask_bad_args.needs_approval is False
    assert res_ask_bad_args.pending_approval is None
    assert res_ask_bad_args.error is not None
    assert res_ask_bad_args.error.code == ErrorCode.ARG_INVALID

    # Exceed max_arg_bytes (files.write = 1,000,000 bytes)
    res_oversized = run_preflight(
        _call("files.write", {"path": "big.txt", "content": "a" * 1_000_050}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_oversized.decision == "block"
    assert res_oversized.executable is False
    assert res_oversized.error is not None
    assert res_oversized.error.code == ErrorCode.ARG_INVALID
    assert "a" * 30 not in str(res_oversized.args_redacted)


def test_path_jail_rejects_traversal_absolute_null_and_symlink_escape(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Path escapes block with PATH_JAIL even on ask-tier tools."""
    (workspace_dir / "escape_link").symlink_to("/etc")

    for tool, bad_path in (
        ("files.read", "../../etc/shadow"),
        ("files.write", "/etc/passwd"),
        ("files.list", "src/app.py\x00.txt"),
        ("files.delete", "escape_link/passwd"),
        ("files.upload", "escape_link/shadow"),
    ):
        args = {"path": bad_path}
        if tool == "files.write":
            args["content"] = "pwn"
        elif tool == "files.upload":
            args["destination"] = "https://example.com/u"

        res = run_preflight(
            _call(tool, args),
            policy=policy,
            profile=coding_profile,
            workspace_root=workspace_dir,
        )
        assert res.decision == "block"
        assert res.executable is False
        assert res.needs_approval is False
        assert res.validated_args is None
        assert res.pending_approval is None
        assert res.audit_decision == "blocked"
        assert res.error is not None
        assert res.error.code == ErrorCode.PATH_JAIL


def test_terminal_cwd_escape_and_deny_pattern_blocked(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """terminal.run rejects cwd escapes (PATH_JAIL) and deny_patterns (DENY_PATTERN)."""
    res_cwd = run_preflight(
        _call("terminal.run", {"command": "ls", "cwd": "../../etc"}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_cwd.decision == "block"
    assert res_cwd.needs_approval is False
    assert res_cwd.error is not None
    assert res_cwd.error.code == ErrorCode.PATH_JAIL

    res_deny = run_preflight(
        _call("terminal.run", {"command": "rm -rf /"}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert res_deny.decision == "block"
    assert res_deny.needs_approval is False
    assert res_deny.error is not None
    assert res_deny.error.code == ErrorCode.DENY_PATTERN


def test_blocked_call_still_redacts_secrets_in_args_redacted(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Even blocked calls sanitize secrets before returning args_redacted."""
    res = run_preflight(
        _call(
            "files.read",
            {"path": "/etc/passwd", "api_key": "sk-secret-999", "token": "tok-123"},
        ),
        policy=policy,
        profile=coding_profile,
        session_state="PAUSED",
        workspace_root=workspace_dir,
    )
    assert res.decision == "block"
    assert "api_key" not in res.args_redacted
    assert "token" not in res.args_redacted
    assert "sk-secret-999" not in str(res.args_redacted)


# ---------------------------------------------------------------------------
# 4. Execution Guard Proof: Ask and Block Cannot Reach Executor Callback
# ---------------------------------------------------------------------------
def test_ask_and_block_results_never_reach_execution_callback(
    policy: Policy, coding_profile: Profile, workspace_dir: Path
) -> None:
    """Execution callback cannot be invoked for ask or block preflight results."""
    executor = MagicMock(return_value="EXECUTED")

    ask_res = run_preflight(
        _call("files.delete", {"path": "src/app.py"}),
        policy=policy,
        profile=coding_profile,
        workspace_root=workspace_dir,
    )
    assert ask_res.decision == "ask"
    assert ask_res.executable is False

    with pytest.raises(PreflightRejectedError) as ask_exc:
        execute_if_allowed(ask_res, executor)
    assert ask_exc.value.decision == "ask"
    assert ask_exc.value.error.code == ErrorCode.POLICY_BLOCK
    executor.assert_not_called()

    block_cases = [
        ("SESSION_INACTIVE", _call("files.read", {"path": "src/app.py"}), "PAUSED"),
        (
            "NOT_IN_PROFILE",
            _call("browser.open", {"url": "https://pypi.org"}),
            "ACTIVE",
        ),
        (
            "ARG_INVALID",
            _call("files.read", {"path": "src/app.py", "extra": 1}),
            "ACTIVE",
        ),
        ("PATH_JAIL", _call("files.read", {"path": "../../etc/passwd"}), "ACTIVE"),
        ("DENY_PATTERN", _call("terminal.run", {"command": "rm -rf /"}), "ACTIVE"),
    ]
    for expected_code, tool_call, state in block_cases:
        blocked_res = run_preflight(
            tool_call,
            policy=policy,
            profile=coding_profile,
            session_state=state,  # type: ignore[arg-type]
            workspace_root=workspace_dir,
        )
        assert blocked_res.decision == "block"
        assert blocked_res.executable is False
        with pytest.raises(PreflightRejectedError) as block_exc:
            execute_if_allowed(blocked_res, executor)
        assert block_exc.value.error.code == expected_code

    executor.assert_not_called()

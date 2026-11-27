"""Unit tests for Pydantic models in control-plane/contracts.py."""

import pytest
from control_plane.contracts import (
    ApprovalRequest,
    ApprovalResponse,
    AuditEntry,
    ErrorBody,
    ErrorCode,
    EventEnvelope,
    Plan,
    PlanStep,
    Policy,
    ProblemFix,
    ProblemReport,
    Profile,
    SessionSummary,
    ToolCall,
    ToolResult,
    UndoRecord,
)
from pydantic import ValidationError


def test_error_codes():
    expected_codes = {
        "SESSION_INACTIVE",
        "NOT_IN_PROFILE",
        "POLICY_BLOCK",
        "ARG_INVALID",
        "PATH_JAIL",
        "DENY_PATTERN",
        "USER_DENIED",
        "APPROVAL_TIMEOUT",
        "BUDGET_EXCEEDED",
        "LOOP_DETECTED",
        "TIMEOUT",
        "UPSTREAM_ERROR",
        "AUTH_ERROR",
        "FATAL",
    }
    actual_codes = {code.value for code in ErrorCode}
    assert actual_codes == expected_codes


def test_error_body():
    err = ErrorBody(code="PATH_JAIL", message="Path outside workspace root")
    assert err.code == "PATH_JAIL"
    assert err.details is None

    err_with_details = ErrorBody(
        code="ARG_INVALID",
        message="Schema validation failed",
        details={"path": "required"},
    )
    assert err_with_details.details == {"path": "required"}


def test_session_summary():
    summary = SessionSummary(
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        state="ACTIVE",
        profile="coding",
        created_at="2026-11-27T01:00:00Z",
    )
    assert summary.model_dump() == {
        "session_id": "s_01J8ABCDEFGH1234567890ABCD",
        "state": "ACTIVE",
        "profile": "coding",
        "created_at": "2026-11-27T01:00:00Z",
    }

    with pytest.raises(ValidationError):
        SessionSummary(
            session_id="s_01J8ABCDEFGH1234567890ABCD",
            state="ACTIVE",
            profile="coding",
            created_at="2026-11-27T01:00:00Z",
            container_id="sess_123",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        SessionSummary(
            session_id="s_01J8ABCDEFGH1234567890ABCD",
            state="UNKNOWN",  # type: ignore[arg-type]
            profile="coding",
            created_at="2026-11-27T01:00:00Z",
        )


def test_tool_call():
    call = ToolCall(
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        call_id="call_1",
        tool="files.write",
        args={"path": "main.py", "content": "print('hello')"},
    )
    assert call.tool == "files.write"
    assert call.args["path"] == "main.py"

    # extra fields should be rejected
    with pytest.raises(ValidationError):
        ToolCall(
            session_id="s_123",
            call_id="c_1",
            tool="files.read",
            args={},
            extra_field="disallowed",
        )


def test_tool_result():
    res = ToolResult(
        call_id="call_1",
        ok=True,
        decision="allow",
        result={"bytes": 14},
        audit_seq=1,
    )
    assert res.ok is True
    assert res.audit_seq == 1
    assert res.decision == "allow"
    assert res.error is None


def test_policy_schema():
    policy_data = {
        "version": 1,
        "defaults": {
            "unknown_tool": "block",
            "approval_timeout_s": 120,
            "output_cap_bytes": 200000,
            "tool_timeout_s": 30,
        },
        "tiers": {
            "read": {"decision": "allow", "log": True},
            "reversible": {"decision": "allow", "log": True, "undo": "capture"},
            "sensitive": {"decision": "ask"},
            "forbidden": {"decision": "block"},
        },
        "tools": {
            "files.list": {"tier": "read", "path_jail": "/workspace"},
            "files.read": {"tier": "read", "path_jail": "/workspace"},
            "files.write": {
                "tier": "reversible",
                "path_jail": "/workspace",
                "max_arg_bytes": 1000000,
            },
            "files.delete": {"tier": "sensitive", "path_jail": "/workspace"},
            "files.upload": {"tier": "sensitive"},
            "terminal.run": {
                "tier": "reversible",
                "timeout_s": 30,
                "deny_patterns": ["rm -rf /", ":(){", "mkfs", "dd if=", "> /dev/sd"],
            },
        },
        "escalations": [
            {
                "when": {"tool": "terminal.run", "matches": "(curl|wget|ssh|scp)\\b"},
                "decision": "ask",
                "reason": "network tool in terminal",
            },
            {
                "when": {"tool": "*", "output_bytes_gt": 200000},
                "action": "truncate",
            },
        ],
    }
    policy = Policy.model_validate(policy_data)
    assert policy.version == 1
    assert policy.defaults.unknown_tool == "block"
    assert "terminal.run" in policy.tools
    assert policy.tools["terminal.run"].deny_patterns[0] == "rm -rf /"
    assert len(policy.escalations) == 2

    # unknown_tool must be "block" only
    invalid_policy = dict(policy_data)
    invalid_policy["defaults"] = dict(policy_data["defaults"])
    invalid_policy["defaults"]["unknown_tool"] = "allow"
    with pytest.raises(ValidationError):
        Policy.model_validate(invalid_policy)


def test_profile_schema():
    profile_data = {
        "name": "coding",
        "role": "coder",
        "system_prompt_file": "prompts/coder.md",
        "image": "pocket/py-node:1.0",
        "resources": {
            "cpus": 2.0,
            "memory": "2g",
            "pids": 512,
            "ttl_minutes": 60,
        },
        "budget": {"max_tool_calls": 200, "max_usd": 1.00},
        "mcp_servers": {
            "terminal": {},
            "files": {"root": "/workspace"},
            "desktop": {},
        },
        "egress_allowlist": [
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
            "github.com",
        ],
        "tool_overrides": {"terminal.run": {"tier": "sensitive"}},
    }
    profile = Profile.model_validate(profile_data)
    assert profile.name == "coding"
    assert profile.role == "coder"
    assert profile.resources.cpus == 2.0
    assert "github.com" in profile.egress_allowlist

    # role must be one of "assistant", "coder", "routine"
    invalid_profile = dict(profile_data)
    invalid_profile["role"] = "superadmin"
    with pytest.raises(ValidationError):
        Profile.model_validate(invalid_profile)


def test_approval_request_and_response():
    req = ApprovalRequest(
        approval_id="a_01J8ABCDEFGH1234567890ABCD",
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        call_id="call_42",
        tool="files.upload",
        tier="sensitive",
        args_redacted={"path": "report.pdf"},
        reason="sensitive tool requires approval",
        irreversible=True,
        warnings=["Cannot be undone"],
        expires_at="2026-11-25T12:00:00Z",
    )
    assert req.approval_id.startswith("a_")
    assert req.irreversible is True

    resp = ApprovalResponse(
        approval_id="a_01J8ABCDEFGH1234567890ABCD",
        approve=True,
    )
    assert resp.approve is True


def test_event_envelope():
    envelope = EventEnvelope(
        seq=1,
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        ts="2026-11-25T12:00:00Z",
        type="tool_call",
        data={"call_id": "call_1", "tool": "files.read"},
    )
    assert envelope.seq == 1
    assert envelope.type == "tool_call"


def test_audit_entry():
    for decision in (
        "allowed",
        "asked",
        "asked_approved",
        "asked_denied",
        "blocked",
        "dry_run",
    ):
        entry = AuditEntry(
            seq=1,
            session_id="s_01J8ABCDEFGH1234567890ABCD",
            ts="2026-11-25T12:00:00Z",
            tool="files.write",
            args_redacted={"path": "test.txt"},
            decision=decision,
            reason="OK",
            tier="reversible",
            prev_hash="0000000000000000000000000000000000000000000000000000000000000000",
            hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        )
        assert entry.decision == decision
        assert entry.seq == 1


def test_plan_and_problem_report_and_undo():
    plan = Plan(
        plan_id="p_01J8ABCDEFGH1234567890ABCD",
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        summary="Create file",
        profile="coding",
        steps=[PlanStep(tool="files.write", tier="reversible")],
        scopes=["workspace:write"],
        plan_hash="hash123",
    )
    assert len(plan.steps) == 1

    problem = ProblemReport(
        what_failed="command timed out",
        why="upstream server unresponsive",
        tried=["retry 1", "retry 2"],
        fixes=[ProblemFix(label="Retry now", action="retry")],
    )
    assert problem.fixes[0].action == "retry"

    undo = UndoRecord(
        undo_id="u_01J8ABCDEFGH1234567890ABCD",
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        audit_seq=5,
        tool="files.write",
        inverse_tool="files.delete",
        inverse_args={"path": "created.txt"},
        state="available",
    )
    assert undo.state == "available"

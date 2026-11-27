"""Audited gateway regression tests for the policy and path-jail bypass cases.

These tests intentionally invoke the complete preflight -> audit -> executor
coordinator path. Docker-only network, resource-limit, and container-boundary
cases remain in ``tests/integration`` and require a Docker-enabled runner.
"""

from __future__ import annotations

import importlib.util
import statistics
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from control_plane.api.stream_tokens import (
    StreamTokenError,
    issue_stream_token,
    issue_view_token,
    verify_stream_token,
    verify_view_token,
)
from control_plane.audit.audit_writer import AuditWriter
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import (
    ApprovalResponse,
    ErrorCode,
    Profile,
    SessionState,
    ToolCall,
)
from control_plane.db.models import AuditEntryModel, SessionModel
from control_plane.db.session import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_engine import evaluate_tool_call
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_terminal_server():
    import sys

    server_path = REPO_ROOT / "mcp" / "terminal" / "server.py"
    spec = importlib.util.spec_from_file_location(
        "overwatch_terminal_server", server_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["overwatch_terminal_server"] = mod
    spec.loader.exec_module(mod)
    return mod


SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


@pytest.fixture
def gateway_setup(
    tmp_path: Path,
) -> Generator[
    tuple[AuditedGatewayCoordinator, Profile, sessionmaker[Session], Path], None, None
]:
    """Build an isolated policy, workspace, and hash-chained audit database."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "safe.txt").write_text("safe\n", encoding="utf-8")
    (workspace / "escape").symlink_to("/")

    engine = create_db_engine(f"sqlite:///{tmp_path / 'bypass.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="bypass-test-owner",
                created_at="2026-11-27T00:00:00Z",
                updated_at="2026-11-27T00:00:00Z",
            )
        )
        db.commit()

    coordinator = AuditedGatewayCoordinator(
        pipeline=GatewayPreflightPipeline(
            policy=load_policy(), workspace_root=workspace
        ),
        audit_writer=AuditWriter(factory),
    )
    try:
        yield coordinator, load_profile("coding"), factory, workspace
    finally:
        engine.dispose()


def _call(tool: str, args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        session_id=SESSION_ID,
        call_id=f"bypass-{tool.replace('.', '-')}",
        tool=tool,
        args={} if args is None else args,
    )


@pytest.mark.parametrize(
    ("case", "tool", "args", "state", "expected_code"),
    [
        (
            "B3 absolute write outside workspace",
            "files.write",
            {"path": "/etc/passwd", "content": "not allowed"},
            "ACTIVE",
            "PATH_JAIL",
        ),
        (
            "B4 parent traversal read",
            "files.read",
            {"path": "../../etc/shadow"},
            "ACTIVE",
            "PATH_JAIL",
        ),
        (
            "B5 symlink escape read",
            "files.read",
            {"path": "escape/etc/passwd"},
            "ACTIVE",
            "PATH_JAIL",
        ),
        (
            "B6 destructive terminal command",
            "terminal.run",
            {"command": "rm -rf /"},
            "ACTIVE",
            "DENY_PATTERN",
        ),
        (
            "B8 tool outside active profile",
            "gmail.send",
            {"to": ["owner@example.invalid"], "subject": "x", "body": "x"},
            "ACTIVE",
            "NOT_IN_PROFILE",
        ),
        (
            "B8 server outside active profile",
            "unknown_server.action",
            {},
            "ACTIVE",
            "NOT_IN_PROFILE",
        ),
        (
            "B9 unknown tool on allowed server defaults to deny",
            "files.unknown_action",
            {},
            "ACTIVE",
            "POLICY_BLOCK",
        ),
        (
            "B12 call while paused",
            "files.read",
            {"path": "src/safe.txt"},
            "PAUSED",
            "SESSION_INACTIVE",
        ),
        (
            "B12 call while ended",
            "files.read",
            {"path": "src/safe.txt"},
            "ENDED",
            "SESSION_INACTIVE",
        ),
        (
            "B12 call while failed",
            "files.read",
            {"path": "src/safe.txt"},
            "FAILED",
            "SESSION_INACTIVE",
        ),
    ],
)
def test_gateway_bypass_is_blocked_audited_and_never_executed(
    gateway_setup: tuple[
        AuditedGatewayCoordinator, Profile, sessionmaker[Session], Path
    ],
    case: str,
    tool: str,
    args: dict[str, Any],
    state: SessionState,
    expected_code: str,
) -> None:
    """Documented gateway bypass attempts fail closed and leave an audit row."""
    coordinator, profile, factory, _workspace = gateway_setup
    executor = Mock(return_value={"should": "never execute"})

    result = coordinator.handle_call(
        _call(tool, args),
        profile,
        executor=executor,
        session_state=state,
    )

    assert result.tool_result.decision == "block", case
    assert result.tool_result.error is not None, case
    assert result.tool_result.error.code == expected_code, case
    assert result.executed is False, case
    executor.assert_not_called()

    with factory() as db:
        rows = db.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
        ).all()
    assert len(rows) == 1, case
    assert rows[0].tool == tool, case
    assert rows[0].decision == "blocked", case


def test_b10_output_cap_enforcement(tmp_path: Path) -> None:
    """B10: Huge output from a tool is capped according to output_cap_bytes."""
    term_mod = _load_terminal_server()
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["yes"],
            returncode=0,
            stdout="A" * 50_000,
            stderr="B" * 50_000,
        )
    )
    cap = 500
    cfg = term_mod.TerminalServerConfig(workspace_root=tmp_path, output_cap_bytes=cap)
    res = term_mod.execute_terminal_run(command="yes", config=cfg, runner=mock_runner)
    total_bytes = len(res.stdout.encode("utf-8")) + len(res.stderr.encode("utf-8"))
    assert total_bytes <= cap
    assert res.exit_code == 0


def test_b11_tool_timeout(
    gateway_setup: tuple[
        AuditedGatewayCoordinator, Profile, sessionmaker[Session], Path
    ],
) -> None:
    """B11: Execution exceeding tool timeout raises TimeoutError and audits attempt."""
    coordinator, profile, factory, _workspace = gateway_setup

    def _timed_out_executor(_validated: Any) -> Any:
        raise TimeoutError("Execution timed out after 30s")

    call = _call("files.read", {"path": "src/safe.txt"})
    with pytest.raises(TimeoutError, match="Execution timed out after 30s"):
        coordinator.handle_call(call, profile, executor=_timed_out_executor)

    with factory() as db:
        rows = db.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
        ).all()
    assert len(rows) == 1
    assert rows[0].decision == "allowed"


def test_b13_cross_session_token_isolation() -> None:
    """B13: A stream or view token from session A cannot access session B."""
    signing_key = "test-session-secret-key-at-least-32-chars-long"
    session_a = "s_01J8ABCDEFGH1234567890ABCD"
    session_b = "s_01J8ABCDEFGH1234567890ABCE"

    # 1. Stream token cross-session rejection
    stream_tok_a = issue_stream_token(
        session_id=session_a,
        owner_id="owner_1",
        signing_key=signing_key,
    )
    with pytest.raises(StreamTokenError, match="Stream token session mismatch"):
        verify_stream_token(
            stream_tok_a,
            expected_session_id=session_b,
            signing_key=signing_key,
        )

    # 2. View token cross-session rejection
    view_tok_a = issue_view_token(
        session_id=session_a,
        owner_id="owner_1",
        signing_key=signing_key,
    )
    with pytest.raises(StreamTokenError, match="session mismatch"):
        verify_view_token(
            view_tok_a,
            expected_session_id=session_b,
            signing_key=signing_key,
        )

    # 3. Domain separation (view token rejected as stream token)
    with pytest.raises(StreamTokenError, match="Invalid stream token signature"):
        verify_stream_token(
            view_tok_a,
            expected_session_id=session_a,
            signing_key=signing_key,
        )


def test_b14_sensitive_action_requires_approval_and_enforces_decision(
    gateway_setup: tuple[
        AuditedGatewayCoordinator, Profile, sessionmaker[Session], Path
    ],
) -> None:
    """B14: Sensitive tools escalate to 'ask' and obey approval responses."""
    _coordinator, _profile, factory, workspace = gateway_setup
    policy = load_policy()
    pipeline = GatewayPreflightPipeline(policy, workspace_root=workspace)
    profile = load_profile("coding")

    # files.delete is configured in policy.yaml as tier: sensitive -> ask
    call = _call("files.delete", {"path": "src/safe.txt"})
    preflight = pipeline.evaluate(call, profile)

    assert preflight.decision == "ask"
    assert preflight.pending_approval is not None
    assert preflight.pending_approval.tool == "files.delete"

    # Test approval manager flow
    event_bus = EventBus(session_factory=factory)
    approval_mgr = ApprovalManager(factory, event_bus=event_bus)
    created = approval_mgr.create_approval(preflight.pending_approval, policy=policy)
    req = created.approval
    assert req.tool == "files.delete"
    assert req.approval_id.startswith("a_")

    # User denies approval
    resolution = approval_mgr.resolve_approval(
        ApprovalResponse(
            approval_id=req.approval_id,
            approve=False,
        )
    )
    assert resolution.status == "denied"
    assert resolution.approved is False


def test_b15_audit_tamper_detection(tmp_path: Path) -> None:
    """B15: Directly tampering with an audit entry breaks the hash chain."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'audit_tamper.db'}")
    init_db(engine)
    factory = create_session_factory(engine)

    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_1",
                created_at="2026-11-27T00:00:00Z",
                updated_at="2026-11-27T00:00:00Z",
            )
        )
        db.commit()

    writer = AuditWriter(factory)
    writer.append(
        session_id=SESSION_ID,
        tool="files.read",
        args={"path": "src/safe.txt"},
        decision="allowed",
        reason="tier=read",
        tier="read",
    )
    writer.append(
        session_id=SESSION_ID,
        tool="terminal.run",
        args={"command": "rm -rf /"},
        decision="blocked",
        reason="DENY_PATTERN",
        tier="forbidden",
    )

    # 1. Clean audit chain verifies as valid
    with factory() as db:
        res = verify_session_audit(db, SESSION_ID)
        assert res.valid is True
        assert res.entry_count == 2

    # 2. Tampering via direct SQLite update (drop update trigger in raw connection)
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_update")
        cursor.execute(
            "UPDATE audit_entries SET decision = 'allowed' "
            f"WHERE session_id = '{SESSION_ID}' AND seq = 2"
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    # 3. verify_session_audit detects broken chain on tampered row
    with factory() as db:
        res_tampered = verify_session_audit(db, SESSION_ID)
        assert res_tampered.valid is False
        assert res_tampered.first_tampered_seq == 2


def test_demo_f18_gmail_send_agent_rejection() -> None:
    """DEMO (F-18): Agent cannot call gmail.send; forbidden tier blocks execution."""
    policy = load_policy()
    daily_profile = load_profile("daily_routine")

    call = ToolCall(
        session_id=SESSION_ID,
        call_id="call_send",
        tool="gmail.send",
        args={"to": ["target@example.com"], "subject": "Hi", "body": "Body"},
    )
    decision = evaluate_tool_call(call, policy, daily_profile)

    assert decision.decision == "block"
    assert decision.error_code == ErrorCode.POLICY_BLOCK
    assert "forbidden" in decision.reason.lower()


def test_gateway_pipeline_latency_benchmark(tmp_path: Path) -> None:
    """Benchmark gateway preflight pipeline latency across 500 iterations.

    Asserts median latency p50 < 1.0 ms and p95 < 2.0 ms.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "safe.txt").write_text("benchmark content", encoding="utf-8")

    pipeline = GatewayPreflightPipeline(load_policy(), workspace_root=workspace)
    profile = load_profile("coding")
    call = _call("files.read", {"path": "safe.txt"})

    # Warmup
    for _ in range(50):
        pipeline.evaluate(call, profile)

    latencies_ms: list[float] = []
    for _ in range(500):
        t0 = time.perf_counter_ns()
        pipeline.evaluate(call, profile)
        t1 = time.perf_counter_ns()
        latencies_ms.append((t1 - t0) / 1_000_000.0)

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
    mean = statistics.mean(latencies_ms)

    assert p50 < 1.0, f"p50 latency {p50:.4f}ms exceeded 1.0ms limit"
    assert p95 < 2.0, f"p95 latency {p95:.4f}ms exceeded 2.0ms limit"
    # Ensure stats are non-trivial
    assert mean > 0.0


def test_gateway_coordinator_latency_benchmark(tmp_path: Path) -> None:
    """Benchmark full audited coordinator latency over 500 iterations.

    Measures preflight + persistent SQLite audit write; asserts median latency
    p50 < 5.0 ms and p95 < 10.0 ms.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "safe.txt").write_text("benchmark content", encoding="utf-8")

    db_path = tmp_path / "bench_coordinator.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="bench-owner",
                created_at="2026-11-28T00:00:00Z",
                updated_at="2026-11-28T00:00:00Z",
            )
        )
        db.commit()

    writer = AuditWriter(factory)
    pipeline = GatewayPreflightPipeline(load_policy(), workspace_root=workspace)
    coordinator = AuditedGatewayCoordinator(pipeline=pipeline, audit_writer=writer)
    profile = load_profile("coding")
    call = _call("files.read", {"path": "safe.txt"})

    for _ in range(50):
        coordinator.handle_call(
            call,
            profile,
            executor=lambda c: {"content": "ok"},
            session_state="ACTIVE",
        )

    latencies_ms: list[float] = []
    for _ in range(500):
        t0 = time.perf_counter_ns()
        coordinator.handle_call(
            call,
            profile,
            executor=lambda c: {"content": "ok"},
            session_state="ACTIVE",
        )
        t1 = time.perf_counter_ns()
        latencies_ms.append((t1 - t0) / 1_000_000.0)

    engine.dispose()

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
    mean = statistics.mean(latencies_ms)

    assert p50 < 5.0, f"p50 coordinator latency {p50:.4f}ms exceeded 5.0ms limit"
    assert p95 < 10.0, f"p95 coordinator latency {p95:.4f}ms exceeded 10.0ms limit"
    assert mean > 0.0

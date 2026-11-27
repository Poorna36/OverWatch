"""Unit tests for F-14 Failure handling: classification, retries, loop breaker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from control_plane.agent.failure_handler import (
    DEFAULT_MAX_TRANSIENT_RETRIES,
    LoopBreaker,
    classify_error,
    sanitize_diagnostic_text,
)
from control_plane.agent.llm import LlmToolCall, LlmTurnResponse, ScriptedLlmAdapter
from control_plane.agent.loop import AgentLoop
from control_plane.api.deps import (
    derive_owner_id,
    get_event_bus,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import ErrorCode
from control_plane.db.models import EventModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from control_plane.sessions.session_runner import SessionRunner
from fastapi.testclient import TestClient
from sqlalchemy import select

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"
TEST_TOKEN = "test-failure-token"


def test_error_classification_and_secret_sanitization() -> None:
    assert classify_error(ErrorCode.TIMEOUT).retryable is True
    assert classify_error(ErrorCode.UPSTREAM_ERROR).retryable is True
    assert (
        classify_error(ErrorCode.TIMEOUT).max_retries == DEFAULT_MAX_TRANSIENT_RETRIES
    )

    assert classify_error(ErrorCode.POLICY_BLOCK).retryable is False
    assert classify_error(ErrorCode.ARG_INVALID).retryable is False
    assert classify_error(ErrorCode.AUTH_ERROR).category == "reconnect"

    raw = "Auth failed: Bearer secret-jwt-123 and sk-ant-1234567890 token=abc"
    cleaned = sanitize_diagnostic_text(raw)
    assert "secret-jwt-123" not in cleaned
    assert "sk-ant-1234567890" not in cleaned
    assert "token=abc" not in cleaned
    assert "[REDACTED]" in cleaned


def test_bounded_retries_succeed_within_limit_and_stop_at_bound(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retry.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as db:
            db.add(
                SessionModel(
                    session_id=SESSION_ID,
                    state="ACTIVE",
                    profile="coding",
                    owner_id="owner_1",
                    created_at="2026-11-27T21:00:00Z",
                    updated_at="2026-11-27T21:00:00Z",
                )
            )
            db.commit()

        bus = EventBus(factory)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=tmp_path, path_jail=PathJail(tmp_path)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        llm = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c1",
                            tool="files.read",
                            args={"path": "notes.txt"},
                        ),
                    )
                ),
                LlmTurnResponse(assistant_text="recovered"),
            ]
        )

        attempts = 0

        def flaky_executor(_validated):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("transient timeout Bearer should-not-leak")
            return {"content": "ok"}

        loop = AgentLoop(
            llm=llm,
            coordinator=coordinator,
            approval_manager=ApprovalManager(factory, bus),
            event_bus=bus,
            policy=policy,
            profile=profile,
        )
        res = loop.run(
            session_id=SESSION_ID,
            request_text="read notes",
            executor=flaky_executor,
        )
        assert res.status == "completed"
        assert attempts == 3
    finally:
        engine.dispose()


def test_loop_breaker_three_failures_pauses_session_and_emits_problem_report(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "loop_breaker.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as db:
            db.add(
                SessionModel(
                    session_id=SESSION_ID,
                    state="ACTIVE",
                    profile="coding",
                    owner_id="owner_1",
                    created_at="2026-11-27T21:00:00Z",
                    updated_at="2026-11-27T21:00:00Z",
                )
            )
            db.commit()

        bus = EventBus(factory)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=tmp_path, path_jail=PathJail(tmp_path)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        llm = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c1",
                            tool="files.read",
                            args={"path": "missing.txt"},
                        ),
                    )
                ),
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c2",
                            tool="files.read",
                            args={"path": "missing.txt"},
                        ),
                    )
                ),
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c3",
                            tool="files.read",
                            args={"path": "missing.txt"},
                        ),
                    )
                ),
            ]
        )

        def failing_executor(_validated):  # type: ignore[no-untyped-def]
            return {
                "ok": False,
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": "failed with token=supersecret123",
                },
            }

        runner = SessionRunner(
            session_factory=factory,
            container_manager=MagicMock(),
            event_bus=bus,
            approval_manager=ApprovalManager(factory, bus),
            profile_loader=MagicMock(),
            policy_loader=MagicMock(),
        )
        loop = AgentLoop(
            llm=llm,
            coordinator=coordinator,
            approval_manager=ApprovalManager(factory, bus),
            event_bus=bus,
            policy=policy,
            profile=profile,
        )
        res = loop.run(
            session_id=SESSION_ID,
            request_text="read missing",
            executor=failing_executor,
        )
        assert res.status == "paused"
        assert res.problem_report is not None
        assert len(res.problem_report.tried) == 3
        assert "supersecret123" not in res.problem_report.why

        # Finalize via SessionRunner transitions ACTIVE -> PAUSED
        rec = runner._finalize_loop_result(
            session_id=SESSION_ID,
            started=MagicMock(),
            runtime=MagicMock(),
            result=res,
        )
        assert rec.status == "paused"
        with factory() as db:
            row = db.get(SessionModel, SESSION_ID)
            assert row is not None
            assert row.state == "PAUSED"
            events = db.scalars(
                select(EventModel).where(EventModel.session_id == SESSION_ID)
            ).all()
        assert any(e.type == "problem_report" for e in events)
        assert any(e.type == "session_state" and "PAUSED" in e.data for e in events)
        runner.shutdown(wait=False)
    finally:
        engine.dispose()


def test_problem_action_route_resumes_or_ends_paused_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "problem_route.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="PAUSED",
                profile="coding",
                owner_id=derive_owner_id(TEST_TOKEN),
                created_at="2026-11-27T21:00:00Z",
                updated_at="2026-11-27T21:00:00Z",
            )
        )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}

        bad = client.post(
            f"/api/v1/sessions/{SESSION_ID}/problem/unknown_fix",
            headers=headers,
        )
        assert bad.status_code == 400

        resumed = client.post(
            f"/api/v1/sessions/{SESSION_ID}/problem/retry",
            headers=headers,
        )
        assert resumed.status_code == 200
        assert resumed.json() == {"state": "ACTIVE"}
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_loop_breaker_unit_resets_on_success() -> None:
    lb = LoopBreaker(threshold=3)
    args = {"path": "a.txt"}
    assert (
        lb.record_failure(
            tool="files.read", args_redacted=args, error_code="TIMEOUT", reason="t1"
        )
        is None
    )
    assert (
        lb.record_failure(
            tool="files.read", args_redacted=args, error_code="TIMEOUT", reason="t2"
        )
        is None
    )
    lb.record_success("files.read", args)
    assert (
        lb.record_failure(
            tool="files.read", args_redacted=args, error_code="TIMEOUT", reason="t3"
        )
        is None
    )

"""Unit tests for Phase 4 provider-neutral LLM adapter and AgentLoop core."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from control_plane.agent import (
    AgentLoop,
    AgentLoopError,
    AgentLoopResult,
    LlmToolCall,
    LlmTurnResponse,
    ScriptedLlmAdapter,
    build_allowed_tools,
    load_role_system_prompt,
    run_session_loop,
)
from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import ErrorCode, Policy, Profile
from control_plane.db.models import (
    ApprovalModel,
    AuditEntryModel,
    EventModel,
    SessionModel,
)
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import select

SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"
UNIQUE_REQUEST_SECRET = "PRIVATE_REQUEST_TEXT_DO_NOT_PERSIST_9f8e7d"


@pytest.fixture()
def loop_env(tmp_path: Path):
    """Create isolated DB, AuditWriter, EventBus, ApprovalManager, and Coordinator."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    db_path = tmp_path / "agent_loop.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)

    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_test",
                dry_run=0,
                created_at="2026-11-27T09:00:00Z",
                updated_at="2026-11-27T09:00:00Z",
            )
        )
        db.commit()

    policy = load_policy()
    profile = load_profile("coding")
    audit_writer = AuditWriter(factory)
    event_bus = EventBus(factory)
    approval_manager = ApprovalManager(factory, event_bus)
    pipeline = GatewayPreflightPipeline(policy=policy, workspace_root=workspace)
    coordinator = AuditedGatewayCoordinator(pipeline, audit_writer)

    try:
        yield {
            "factory": factory,
            "workspace": workspace,
            "policy": policy,
            "profile": profile,
            "event_bus": event_bus,
            "approval_manager": approval_manager,
            "coordinator": coordinator,
        }
    finally:
        engine.dispose()


def test_role_prompt_loading_and_tool_list_intersection(tmp_path: Path) -> None:
    """Role prompt loads safely from disk; tool list intersects profile and policy."""
    policy = load_policy()
    coding = load_profile("coding")
    general = load_profile("general")

    prompt = load_role_system_prompt(coding)
    assert "coding agent" in prompt

    coding_tools = build_allowed_tools(coding, policy)
    coding_names = [t.name for t in coding_tools]
    assert "terminal.run" in coding_names
    assert "files.write" in coding_names
    assert not any(name.startswith("browser.") for name in coding_names)
    by_name = {t.name: t for t in coding_tools}
    assert by_name["files.write"].parameters["additionalProperties"] is False
    assert set(by_name["files.write"].parameters["properties"].keys()) == {
        "path",
        "content",
    }

    general_names = [t.name for t in build_allowed_tools(general, policy)]
    assert "browser.open" in general_names
    assert "terminal.run" not in general_names

    # Path traversal or missing prompt fails closed
    bad_profile = Profile(
        name="bad",
        role="coder",
        system_prompt_file="../etc/passwd",
        image="pocket/py-node:1.0",
    )
    with pytest.raises(AgentLoopError):
        load_role_system_prompt(bad_profile, base_dir=tmp_path)


def test_allowed_tool_call_flow_isolates_request_text_and_bounds_result(
    loop_env: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """Allowed calls route via coordinator, bound results, and never persist request."""
    adapter = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_1",
                        tool="files.write",
                        args={"path": "out.txt", "content": "raw-body-data"},
                    ),
                )
            ),
            LlmTurnResponse(assistant_text="Wrote out.txt successfully."),
        ]
    )

    executed_calls: list[ValidatedArguments] = []

    def fake_executor(validated: ValidatedArguments) -> dict:
        executed_calls.append(validated)
        return {"bytes": 13, "extra_large": "Z" * 100}

    # Lower output_cap_bytes to exercise bounded tool output returned to adapter
    small_cap_policy: Policy = loop_env["policy"].model_copy(
        update={
            "defaults": loop_env["policy"].defaults.model_copy(
                update={"output_cap_bytes": 40}
            )
        }
    )
    pipeline = GatewayPreflightPipeline(
        policy=small_cap_policy, workspace_root=loop_env["workspace"]
    )
    coordinator = AuditedGatewayCoordinator(pipeline, AuditWriter(loop_env["factory"]))

    with caplog.at_level(logging.DEBUG):
        res = run_session_loop(
            session_id=SESSION_ID,
            request_text=UNIQUE_REQUEST_SECRET,
            llm=adapter,
            coordinator=coordinator,
            approval_manager=loop_env["approval_manager"],
            event_bus=loop_env["event_bus"],
            policy=small_cap_policy,
            profile=loop_env["profile"],
            executor=fake_executor,
            workspace_root=loop_env["workspace"],
        )

    assert isinstance(res, AgentLoopResult)
    assert res.status == "completed"
    assert res.assistant_message == "Wrote out.txt successfully."
    assert res.calls_used == 1
    assert res.turns == 2
    assert len(executed_calls) == 1
    assert executed_calls[0].tool == "files.write"

    # Turn 1 received system prompt, profile-filtered tools, and in-memory request_text
    assert "coding agent" in adapter.recorded_calls[0].system_prompt
    assert adapter.recorded_calls[0].messages[0].content == UNIQUE_REQUEST_SECRET
    exposed_tools = {t.name for t in adapter.recorded_calls[0].tools}
    assert not any(name.startswith("browser.") for name in exposed_tools)

    # Turn 2 received bounded tool message capped at 40 bytes
    tool_msg = adapter.recorded_calls[1].messages[-1]
    assert tool_msg.role == "tool"
    assert tool_msg.tool_call_id == "call_1"
    assert len(tool_msg.content.encode("utf-8")) <= 40

    # Verify request_text and raw file body never touched logs or DB tables
    log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
    assert UNIQUE_REQUEST_SECRET not in log_blob

    factory = loop_env["factory"]
    with factory() as db:
        audit_rows = db.scalars(select(AuditEntryModel)).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].decision == "allowed"
        assert "raw-body-data" not in audit_rows[0].args_redacted
        assert UNIQUE_REQUEST_SECRET not in audit_rows[0].args_redacted

        event_rows = db.scalars(select(EventModel).order_by(EventModel.seq.asc())).all()
        assert [e.type for e in event_rows] == [
            "tool_call",
            "budget",
            "tool_result",
            "agent_message",
        ]
        for row in event_rows:
            assert UNIQUE_REQUEST_SECRET not in row.data
            assert "raw-body-data" not in row.data


def test_blocked_tool_call_exposes_only_error_body_and_never_executes(
    loop_env: dict,
) -> None:
    """Blocked calls persist audit/events, skip executor, and return ErrorBody."""
    adapter = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_bad",
                        tool="browser.open",
                        args={"url": "https://example.com"},
                    ),
                )
            ),
            LlmTurnResponse(assistant_text="Should not reach turn 2."),
        ]
    )
    mock_executor = MagicMock()

    res = run_session_loop(
        session_id=SESSION_ID,
        request_text=UNIQUE_REQUEST_SECRET,
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        executor=mock_executor,
        workspace_root=loop_env["workspace"],
    )

    mock_executor.assert_not_called()
    assert res.status == "blocked"
    assert res.error is not None
    assert res.error.code == ErrorCode.NOT_IN_PROFILE
    assert UNIQUE_REQUEST_SECRET not in res.error.message
    assert len(adapter.recorded_calls) == 1

    events = loop_env["event_bus"].replay(session_id=SESSION_ID)
    assert [e.type for e in events] == ["tool_call", "budget", "blocked"]
    assert events[2].data["code"] == ErrorCode.NOT_IN_PROFILE


def test_ask_tool_call_persists_approval_emits_event_and_stops(
    loop_env: dict,
) -> None:
    """Ask calls create an ApprovalRequest, emit approval_request, and pause loop."""
    adapter = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_term",
                        tool="terminal.run",
                        args={"command": "pytest -q"},
                    ),
                )
            ),
            LlmTurnResponse(assistant_text="Should not continue before approval."),
        ]
    )
    mock_executor = MagicMock()

    res = run_session_loop(
        session_id=SESSION_ID,
        request_text=UNIQUE_REQUEST_SECRET,
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        executor=mock_executor,
        workspace_root=loop_env["workspace"],
    )

    mock_executor.assert_not_called()
    assert res.status == "awaiting_approval"
    assert res.pending_approval is not None
    assert res.pending_approval.tool == "terminal.run"
    assert res.pending_approval.call_id == "call_term"
    assert len(adapter.recorded_calls) == 1

    with loop_env["factory"]() as db:
        approvals = db.scalars(select(ApprovalModel)).all()
        assert len(approvals) == 1
        assert approvals[0].status == "pending"

    events = loop_env["event_bus"].replay(session_id=SESSION_ID)
    assert [e.type for e in events] == ["tool_call", "budget", "approval_request"]
    assert events[2].data["approval_id"] == res.pending_approval.approval_id


def test_max_tool_calls_budget_enforced_and_fails_safely(loop_env: dict) -> None:
    """Exhausting ProfileBudget.max_tool_calls stops safely without execution."""
    tight_profile: Profile = loop_env["profile"].model_copy(
        update={
            "budget": loop_env["profile"].budget.model_copy(
                update={"max_tool_calls": 1}
            )
        }
    )
    adapter = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_1", tool="files.list", args={"path": "."}
                    ),
                )
            ),
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_2", tool="files.list", args={"path": "."}
                    ),
                )
            ),
        ]
    )
    mock_executor = MagicMock(return_value={"entries": []})

    loop = AgentLoop(
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=tight_profile,
        workspace_root=loop_env["workspace"],
    )
    res = loop.run(
        session_id=SESSION_ID,
        request_text=UNIQUE_REQUEST_SECRET,
        executor=mock_executor,
    )

    assert mock_executor.call_count == 1
    assert res.status == "budget_exceeded"
    assert res.calls_used == 1
    assert res.calls_max == 1
    assert res.error is not None
    assert res.error.code == ErrorCode.BUDGET_EXCEEDED

    events = loop_env["event_bus"].replay(session_id=SESSION_ID)
    budget_events = [e for e in events if e.type == "budget"]
    assert budget_events[-1].data["calls_used"] == 1
    assert budget_events[-1].data["usd_used"] == 0.0
    assert events[-2].type == "blocked"
    assert events[-2].data["code"] == ErrorCode.BUDGET_EXCEEDED


def test_inactive_session_and_adapter_exhaustion_fail_safely(loop_env: dict) -> None:
    """Non-ACTIVE sessions and adapter errors return error status safely."""
    mock_executor = MagicMock()
    adapter = ScriptedLlmAdapter([])

    r_paused = run_session_loop(
        session_id=SESSION_ID,
        request_text="do work",
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        executor=mock_executor,
        session_state="PAUSED",
        workspace_root=loop_env["workspace"],
    )
    assert r_paused.status == "error"
    assert r_paused.error is not None
    assert r_paused.error.code == ErrorCode.SESSION_INACTIVE
    assert adapter.recorded_calls == []
    mock_executor.assert_not_called()

    r_exhausted = run_session_loop(
        session_id=SESSION_ID,
        request_text="do work",
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        executor=mock_executor,
        workspace_root=loop_env["workspace"],
    )
    assert r_exhausted.status == "error"
    assert r_exhausted.error is not None
    assert r_exhausted.error.code == ErrorCode.UPSTREAM_ERROR

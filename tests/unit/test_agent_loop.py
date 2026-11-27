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
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import ApprovalResponse, ErrorCode, Policy, Profile
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


def test_resume_after_approval_executes_once_and_completes_remaining_calls(
    loop_env: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approved continuation revalidates, runs once, finishes batch, and completes."""
    adapter = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="call_ask_1",
                        tool="terminal.run",
                        args={"command": "curl https://pypi.org/simple/"},
                    ),
                    LlmToolCall(
                        call_id="call_after_2",
                        tool="files.write",
                        args={"path": "after.txt", "content": "secret-file-body"},
                    ),
                )
            ),
            lambda _sys, msgs, _tools: (
                LlmTurnResponse(assistant_text="All done after approval.")
                if any(
                    m.role == "tool" and m.tool_call_id == "call_ask_1" for m in msgs
                )
                and any(
                    m.role == "tool" and m.tool_call_id == "call_after_2" for m in msgs
                )
                else LlmTurnResponse(assistant_text="Missing tool results")
            ),
        ]
    )
    executed: list[ValidatedArguments] = []

    def fake_executor(validated: ValidatedArguments) -> dict:
        executed.append(validated)
        if validated.tool == "terminal.run":
            return {"exit_code": 0, "stdout": "curl ok", "stderr": ""}
        return {"bytes": 16}

    loop = AgentLoop(
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        workspace_root=loop_env["workspace"],
    )

    with caplog.at_level(logging.DEBUG):
        paused = loop.run(
            session_id=SESSION_ID,
            request_text=UNIQUE_REQUEST_SECRET,
            executor=fake_executor,
        )
        assert paused.status == "awaiting_approval"
        assert paused.pending_approval is not None
        assert paused.continuation is not None
        assert UNIQUE_REQUEST_SECRET not in repr(paused)
        assert UNIQUE_REQUEST_SECRET not in repr(paused.continuation)
        assert "curl https://pypi.org/simple/" not in repr(paused.continuation)
        assert executed == []

        resolved = loop_env["approval_manager"].resolve_approval(
            ApprovalResponse(
                approval_id=paused.pending_approval.approval_id,
                approve=True,
            )
        )
        resumed = loop.resume_after_approval(
            continuation=paused.continuation,
            approved=resolved.approved,
            timed_out=resolved.timed_out,
            executor=fake_executor,
        )

    assert resumed.status == "completed"
    assert resumed.assistant_message == "All done after approval."
    assert resumed.calls_used == 2
    assert [c.tool for c in executed] == ["terminal.run", "files.write"]
    assert executed[0].args["command"] == "curl https://pypi.org/simple/"

    assert UNIQUE_REQUEST_SECRET not in caplog.text
    with loop_env["factory"]() as db:
        assert verify_session_audit(db, SESSION_ID).valid is True
        audits = db.scalars(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == SESSION_ID)
            .order_by(AuditEntryModel.seq.asc())
        ).all()
        assert [(a.tool, a.decision) for a in audits] == [
            ("terminal.run", "asked"),
            ("terminal.run", "asked_approved"),
            ("files.write", "allowed"),
        ]
        for a in audits:
            assert UNIQUE_REQUEST_SECRET not in a.args_redacted
            assert "secret-file-body" not in a.args_redacted


def test_resume_after_denial_and_timeout_never_execute_tool(loop_env: dict) -> None:
    """Denied or timed-out approvals emit tool_result errors and resume safely."""
    for is_timeout, expected_code in [
        (False, ErrorCode.USER_DENIED),
        (True, ErrorCode.APPROVAL_TIMEOUT),
    ]:
        seen_tool_contents: list[str] = []

        def _turn2(_sys, msgs, _tools) -> LlmTurnResponse:
            seen_tool_contents.append(msgs[-1].content)
            return LlmTurnResponse(assistant_text=f"Handled {expected_code.value}.")

        adapter = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id=f"call_{expected_code.value}",
                            tool="terminal.run",
                            args={"command": "curl https://example.com"},
                        ),
                    )
                ),
                _turn2,
            ]
        )
        mock_executor = MagicMock()
        loop = AgentLoop(
            llm=adapter,
            coordinator=loop_env["coordinator"],
            approval_manager=loop_env["approval_manager"],
            event_bus=loop_env["event_bus"],
            policy=loop_env["policy"],
            profile=loop_env["profile"],
            workspace_root=loop_env["workspace"],
        )

        paused = loop.run(
            session_id=SESSION_ID,
            request_text="fetch data",
            executor=mock_executor,
        )
        assert paused.continuation is not None

        resumed = loop.resume_after_approval(
            continuation=paused.continuation,
            approved=False,
            timed_out=is_timeout,
            executor=mock_executor,
        )

        mock_executor.assert_not_called()
        assert resumed.status == "completed"
        assert resumed.assistant_message == f"Handled {expected_code.value}."
        assert len(seen_tool_contents) == 1
        assert expected_code.value in seen_tool_contents[0]


def test_resume_revalidates_edited_args_and_post_approval_preflight_blocks(
    loop_env: dict,
) -> None:
    """Edited args and workspace drift pass through gateway preflight before exec."""
    ws: Path = loop_env["workspace"]
    target = ws / "delete_me.txt"
    target.write_text("hi", encoding="utf-8")

    # 1. Symlink created after 'ask' must block during post-approval preflight
    adapter1 = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_del",
                        tool="files.delete",
                        args={"path": "delete_me.txt"},
                    ),
                )
            ),
        ]
    )
    mock_exec = MagicMock()
    loop1 = AgentLoop(
        llm=adapter1,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        workspace_root=ws,
    )
    paused1 = loop1.run(
        session_id=SESSION_ID, request_text="rm file", executor=mock_exec
    )
    assert paused1.continuation is not None

    outside = ws.parent / "host_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)

    blocked_res = loop1.resume_after_approval(
        continuation=paused1.continuation,
        approved=True,
        executor=mock_exec,
    )
    mock_exec.assert_not_called()
    assert blocked_res.status == "blocked"
    assert blocked_res.error is not None
    assert blocked_res.error.code == ErrorCode.PATH_JAIL

    # 2. Unsafe edited_args (deny pattern) is blocked before execution
    adapter2 = ScriptedLlmAdapter(
        [
            LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_term",
                        tool="terminal.run",
                        args={"command": "curl https://pypi.org/simple/"},
                    ),
                )
            ),
            LlmTurnResponse(assistant_text="Safe edited command ran."),
        ]
    )
    loop2 = AgentLoop(
        llm=adapter2,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        workspace_root=ws,
    )
    paused2 = loop2.run(
        session_id=SESSION_ID, request_text="run cmd", executor=mock_exec
    )
    assert paused2.continuation is not None

    bad_edit_res = loop2.resume_after_approval(
        continuation=paused2.continuation,
        approved=True,
        edited_args={"command": "rm -rf /workspace"},
        executor=mock_exec,
    )
    mock_exec.assert_not_called()
    assert bad_edit_res.status == "blocked"
    assert bad_edit_res.error is not None
    assert bad_edit_res.error.code == ErrorCode.DENY_PATTERN

    # 3. Valid edited_args re-enters preflight/validation and executes normalized args
    ran: list[ValidatedArguments] = []
    good_edit_res = loop2.resume_after_approval(
        continuation=paused2.continuation,
        approved=True,
        edited_args={"command": "echo safe-edited"},
        executor=lambda v: (ran.append(v), {"exit_code": 0, "stdout": "ok"})[1],
    )
    assert good_edit_res.status == "completed"
    assert len(ran) == 1
    assert ran[0].args == {"command": "echo safe-edited"}


def test_continue_with_messages_and_drain_queue_preserve_memory_only_text(
    loop_env: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Queued and follow-up messages continue conversation without persisting text."""
    queued_secret = "QUEUED_MSG_DO_NOT_PERSIST_111"
    followup_secret = "FOLLOWUP_MSG_DO_NOT_PERSIST_222"
    queued: list[str] = []

    def _drain() -> list[str]:
        items = list(queued)
        queued.clear()
        return items

    def _turn1(_sys, _msgs, _tools) -> LlmTurnResponse:
        # Simulate a message arriving while Turn 1 is in progress.
        queued.append(queued_secret)
        return LlmTurnResponse(assistant_text="Initial reply.")

    adapter = ScriptedLlmAdapter(
        [
            _turn1,
            LlmTurnResponse(assistant_text="Queued reply."),
            LlmTurnResponse(assistant_text="Followup reply."),
        ]
    )
    mock_executor = MagicMock()
    loop = AgentLoop(
        llm=adapter,
        coordinator=loop_env["coordinator"],
        approval_manager=loop_env["approval_manager"],
        event_bus=loop_env["event_bus"],
        policy=loop_env["policy"],
        profile=loop_env["profile"],
        workspace_root=loop_env["workspace"],
    )

    with caplog.at_level(logging.DEBUG):
        res1 = loop.run(
            session_id=SESSION_ID,
            request_text=UNIQUE_REQUEST_SECRET,
            executor=mock_executor,
            drain_messages=_drain,
        )
        assert res1.status == "completed"
        assert res1.assistant_message == "Queued reply."
        assert res1.snapshot is not None
        assert UNIQUE_REQUEST_SECRET not in repr(res1.snapshot)
        assert queued_secret not in repr(res1.snapshot)

        # Turn 2 received assistant(Initial reply.) + user(queued_secret)
        turn2_roles = [(m.role, m.content) for m in adapter.recorded_calls[1].messages]
        assert turn2_roles == [
            ("user", UNIQUE_REQUEST_SECRET),
            ("assistant", "Initial reply."),
            ("user", queued_secret),
        ]

        res2 = loop.continue_with_messages(
            snapshot=res1.snapshot,
            user_messages=[followup_secret],
            executor=mock_executor,
        )
        assert res2.status == "completed"
        assert res2.assistant_message == "Followup reply."
        turn3_roles = [(m.role, m.content) for m in adapter.recorded_calls[2].messages]
        assert turn3_roles[-2:] == [
            ("assistant", "Queued reply."),
            ("user", followup_secret),
        ]

    log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in (UNIQUE_REQUEST_SECRET, queued_secret, followup_secret):
        assert secret not in log_blob
        for ev in loop_env["event_bus"].replay(session_id=SESSION_ID):
            assert secret not in str(ev.data)

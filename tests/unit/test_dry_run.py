"""Unit tests for F-12 Dry-run preview and drift/policy-checked Apply."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_plane.agent.llm import LlmToolCall, LlmTurnResponse, ScriptedLlmAdapter
from control_plane.agent.loop import AgentLoop
from control_plane.audit.audit_writer import AuditWriter
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
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.dry_run import DryRunManager
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import select

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 21, 25, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_dry_run_executes_reads_suppresses_writes_and_checks_drift_on_apply(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "readme.txt").write_text("hello dry run\n", encoding="utf-8")
    (ws / "target.txt").write_text("before\n", encoding="utf-8")
    (ws / "drifted.txt").write_text("initial\n", encoding="utf-8")

    db_path = tmp_path / "dry_run.db"
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
                    dry_run=1,
                    created_at="2026-11-27T21:25:00Z",
                    updated_at="2026-11-27T21:25:00Z",
                )
            )
            db.commit()

        bus = EventBus(factory)
        clock = _FakeClock()
        approval_mgr = ApprovalManager(factory, bus, clock=clock)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=ws, path_jail=PathJail(ws)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        dry_run_mgr = DryRunManager()

        def reader(rel_path: str) -> tuple[bool, str | None]:
            p = ws / rel_path
            if not p.is_file():
                return False, None
            return True, p.read_text(encoding="utf-8")

        executed_calls: list[str] = []

        def executor(validated: ValidatedArguments) -> Any:
            executed_calls.append(validated.tool)
            if validated.tool == "files.read":
                return {"content": (ws / str(validated.args["path"])).read_text()}
            if validated.tool == "files.write":
                p = ws / str(validated.args["path"])
                p.write_text(str(validated.args["content"]), encoding="utf-8")
                return {"bytes": len(str(validated.args["content"]))}
            if validated.tool == "files.delete":
                (ws / str(validated.args["path"])).unlink()
                return {"deleted": True}
            raise AssertionError(f"Unexpected tool {validated.tool}")

        llm = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c1",
                            tool="files.read",
                            args={"path": "readme.txt"},
                        ),
                        LlmToolCall(
                            call_id="c2",
                            tool="files.write",
                            args={
                                "path": "target.txt",
                                "content": "SECRET_BODY_after\n",
                            },
                        ),
                        LlmToolCall(
                            call_id="c3",
                            tool="files.write",
                            args={
                                "path": "drifted.txt",
                                "content": "should not overwrite\n",
                            },
                        ),
                        LlmToolCall(
                            call_id="c4",
                            tool="files.delete",
                            args={"path": "readme.txt"},
                        ),
                    )
                ),
                LlmTurnResponse(assistant_text="dry run preview complete"),
            ]
        )

        loop = AgentLoop(
            llm=llm,
            coordinator=coordinator,
            approval_manager=approval_mgr,
            event_bus=bus,
            policy=policy,
            profile=profile,
            file_reader=reader,
            dry_run=True,
            dry_run_manager=dry_run_mgr,
        )
        res = loop.run(
            session_id=SESSION_ID,
            request_text="preview changes",
            executor=executor,
        )
        assert res.status == "completed"
        # Only the read executed during preview; writes did NOT execute
        assert executed_calls == ["files.read"]
        assert (ws / "target.txt").read_text(encoding="utf-8") == "before\n"
        assert (ws / "readme.txt").is_file()

        with factory() as db:
            events = db.scalars(
                select(EventModel).where(EventModel.session_id == SESSION_ID)
            ).all()
            audits = db.scalars(
                select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
            ).all()
        diff_events = [e for e in events if e.type == "dry_run_diff"]
        assert len(diff_events) == 3
        assert all("SECRET_BODY_after" not in e.data for e in diff_events)
        assert [a.decision for a in audits] == [
            "allowed",
            "dry_run",
            "dry_run",
            "dry_run",
        ]

        # Simulate external drift on drifted.txt before Apply
        (ws / "drifted.txt").write_text("changed externally\n", encoding="utf-8")

        apply_res = dry_run_mgr.apply(
            session_id=SESSION_ID,
            coordinator=coordinator,
            approval_manager=approval_mgr,
            profile=profile,
            policy=policy,
            executor=executor,
            reader=reader,
            get_session_state=lambda: "ACTIVE",
        )
        # target.txt applied (1); drifted.txt & files.delete flagged (2)
        assert apply_res.applied == 1
        assert apply_res.flagged == 2
        assert len(apply_res.pending_approvals) == 1
        assert (ws / "target.txt").read_text(encoding="utf-8") == "SECRET_BODY_after\n"
        assert (ws / "drifted.txt").read_text(
            encoding="utf-8"
        ) == "changed externally\n"
        assert (ws / "readme.txt").is_file()

        with factory() as db:
            assert (
                db.get(ApprovalModel, apply_res.pending_approvals[0].approval_id)
                is not None
            )
    finally:
        engine.dispose()

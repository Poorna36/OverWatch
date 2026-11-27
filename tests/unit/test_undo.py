"""Unit tests for F-13 Undo of reversible files.write operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from control_plane.agent.llm import LlmToolCall, LlmTurnResponse, ScriptedLlmAdapter
from control_plane.agent.loop import AgentLoop
from control_plane.audit.audit_writer import AuditWriter
from control_plane.db.models import AuditEntryModel, SessionModel, UndoRecordModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from control_plane.sessions.undo_manager import (
    UndoConflictError,
    UndoManager,
    UndoPolicyError,
)
from sqlalchemy import inspect, select

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 21, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _setup_workspace_and_db(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "notes.txt").write_text("original content\n", encoding="utf-8")

    db_path = tmp_path / "undo.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_1",
                created_at="2026-11-27T21:15:00Z",
                updated_at="2026-11-27T21:15:00Z",
            )
        )
        db.commit()
    return ws, engine, factory


def _make_fs_executor(ws: Path):
    def _reader(rel_path: str) -> tuple[bool, str | None]:
        target = ws / rel_path
        if not target.is_file():
            return False, None
        return True, target.read_text(encoding="utf-8")

    def _executor(validated: ValidatedArguments) -> Any:
        if validated.tool == "files.write":
            target = ws / str(validated.args["path"])
            content = str(validated.args["content"])
            target.write_text(content, encoding="utf-8")
            return {"bytes": len(content.encode("utf-8"))}
        if validated.tool == "files.delete":
            target = ws / str(validated.args["path"])
            target.unlink()
            return {"deleted": True}
        raise AssertionError(f"Unexpected tool {validated.tool}")

    return _reader, _executor


def test_undo_restores_prior_file_and_audits_both_actions(tmp_path: Path) -> None:
    ws, engine, factory = _setup_workspace_and_db(tmp_path)
    try:
        reader, executor = _make_fs_executor(ws)
        bus = EventBus(factory)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=ws, path_jail=PathJail(ws)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        undo_mgr = UndoManager(factory, clock=_FakeClock())

        llm = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c1",
                            tool="files.write",
                            args={"path": "notes.txt", "content": "updated content\n"},
                        ),
                    )
                ),
                LlmTurnResponse(assistant_text="done"),
            ]
        )
        loop = AgentLoop(
            llm=llm,
            coordinator=coordinator,
            approval_manager=ApprovalManager(factory, bus),
            event_bus=bus,
            policy=policy,
            profile=profile,
            undo_manager=undo_mgr,
            file_reader=reader,
        )
        res = loop.run(
            session_id=SESSION_ID, request_text="update notes", executor=executor
        )
        assert res.status == "completed"
        assert (ws / "notes.txt").read_text(encoding="utf-8") == "updated content\n"

        with factory() as db:
            records = db.scalars(select(UndoRecordModel)).all()
        assert len(records) == 1
        assert records[0].state == "available"
        assert records[0].inverse_tool == "files.write"

        undone = undo_mgr.undo_all(
            session_id=SESSION_ID,
            coordinator=coordinator,
            profile=profile,
            executor=executor,
            reader=reader,
        )
        assert undone == 1
        assert (ws / "notes.txt").read_text(encoding="utf-8") == "original content\n"

        with factory() as db:
            audits = db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_ID)
                .order_by(AuditEntryModel.seq.asc())
            ).all()
            updated_rec = db.get(UndoRecordModel, records[0].undo_id)
        assert len(audits) == 2
        assert all(a.tool == "files.write" and a.decision == "allowed" for a in audits)
        assert updated_rec is not None and updated_rec.state == "used"
    finally:
        engine.dispose()


def test_undo_rejects_drifted_or_missing_file_without_unsafe_overwrite(
    tmp_path: Path,
) -> None:
    ws, engine, factory = _setup_workspace_and_db(tmp_path)
    try:
        reader, executor = _make_fs_executor(ws)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=ws, path_jail=PathJail(ws)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        undo_mgr = UndoManager(factory, clock=_FakeClock())

        cap = undo_mgr.capture_before_write(
            path="notes.txt", new_content="v2\n", reader=reader
        )
        (ws / "notes.txt").write_text("v2\n", encoding="utf-8")
        rec = undo_mgr.record_write(session_id=SESSION_ID, audit_seq=1, capture=cap)

        # Simulate external modification (drift)
        (ws / "notes.txt").write_text("externally edited\n", encoding="utf-8")
        with pytest.raises(UndoConflictError, match="changed"):
            undo_mgr.undo_step(
                session_id=SESSION_ID,
                undo_id=rec.undo_id,
                coordinator=coordinator,
                profile=profile,
                executor=executor,
                reader=reader,
            )
        assert (ws / "notes.txt").read_text(encoding="utf-8") == "externally edited\n"

        # Simulate missing file
        (ws / "notes.txt").unlink()
        with pytest.raises(UndoConflictError, match="missing"):
            undo_mgr.undo_step(
                session_id=SESSION_ID,
                undo_id=rec.undo_id,
                coordinator=coordinator,
                profile=profile,
                executor=executor,
                reader=reader,
            )
    finally:
        engine.dispose()


def test_undo_enforces_normal_policy_and_path_jail_on_restoration(
    tmp_path: Path,
) -> None:
    ws, engine, factory = _setup_workspace_and_db(tmp_path)
    try:
        reader, executor = _make_fs_executor(ws)
        policy = load_policy()
        profile = load_profile("coding")
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=ws, path_jail=PathJail(ws)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        undo_mgr = UndoManager(factory, clock=_FakeClock())

        # Writing a brand-new file has inverse_tool="files.delete" (sensitive -> ask)
        cap = undo_mgr.capture_before_write(
            path="brand_new.txt", new_content="hello", reader=reader
        )
        (ws / "brand_new.txt").write_text("hello", encoding="utf-8")
        rec = undo_mgr.record_write(session_id=SESSION_ID, audit_seq=1, capture=cap)
        assert rec.inverse_tool == "files.delete"

        with pytest.raises(UndoPolicyError, match="ask"):
            undo_mgr.undo_step(
                session_id=SESSION_ID,
                undo_id=rec.undo_id,
                coordinator=coordinator,
                profile=profile,
                executor=executor,
                reader=reader,
            )
        assert (ws / "brand_new.txt").is_file()
    finally:
        engine.dispose()


def test_alembic_migration_0005_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_undo.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "undo_records" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(cfg, "0004_add_plans_table")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "undo_records" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

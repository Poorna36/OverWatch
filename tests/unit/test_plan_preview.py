"""Unit tests for F-11 Plan preview generation, hashing, approval, and escalation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from control_plane.api.deps import (
    get_session_factory,
    get_session_runner,
)
from control_plane.api.main import app
from control_plane.contracts import ErrorCode, Plan, PlanStep, ToolCall
from control_plane.db.models import PlanModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.plan_service import (
    PlanService,
    PlanValidationError,
    build_plan_for_profile,
    compute_plan_hash,
)
from control_plane.sessions.profile_loader import ProfileLoader, load_profile
from control_plane.sessions.session_runner import SessionRunner
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TOKEN = "test-plan-token"
SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 20, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _FakeLlm:
    def next_turn(self, **_kwargs):  # type: ignore[no-untyped-def]
        from control_plane.agent.llm import LlmTurnResponse

        return LlmTurnResponse(assistant_text="done")


def test_build_plan_produces_stable_hash_and_never_stores_prompt() -> None:
    profile = load_profile("coding")
    policy = load_policy()
    now = datetime(2026, 11, 27, 20, 0, 0, tzinfo=UTC)

    p1 = build_plan_for_profile(
        session_id=SESSION_ID,
        profile=profile,
        policy=policy,
        plan_id="p_01J8ABCDEFGH1234567890ABCD",
        now=now,
    )
    p2 = build_plan_for_profile(
        session_id=SESSION_ID,
        profile=profile,
        policy=policy,
        plan_id="p_01J8ABCDEFGH1234567890ABCD",
        now=now,
    )
    assert p1.plan_hash == p2.plan_hash
    assert p1.plan_hash == compute_plan_hash(
        plan_id=p1.plan_id,
        session_id=p1.session_id,
        summary=p1.summary,
        profile=p1.profile,
        steps=p1.steps,
        scopes=p1.scopes,
        est_budget_usd=p1.est_budget_usd,
    )
    assert len(p1.steps) >= 1


def test_plan_service_persists_verifies_hash_and_edits(tmp_path: Path) -> None:
    db_path = tmp_path / "plans.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as db:
            db.add(
                SessionModel(
                    session_id=SESSION_ID,
                    state="PLANNING",
                    profile="coding",
                    owner_id="owner_1",
                    created_at="2026-11-27T20:00:00Z",
                    updated_at="2026-11-27T20:00:00Z",
                )
            )
            db.commit()

        svc = PlanService(factory, clock=_FakeClock())
        plan = svc.create_plan(
            session_id=SESSION_ID,
            profile=load_profile("coding"),
            policy=load_policy(),
        )
        loaded = svc.get_plan(session_id=SESSION_ID, plan_id=plan.plan_id)
        assert loaded == plan

        edited = svc.edit_plan(
            session_id=SESSION_ID,
            plan_id=plan.plan_id,
            remove_steps=[0],
        )
        assert len(edited.steps) == len(plan.steps) - 1
        assert edited.plan_hash != plan.plan_hash

        # Tampering with stored plan_hash fails closed
        with factory() as db:
            row = db.get(PlanModel, plan.plan_id)
            assert row is not None
            row.plan_hash = "0" * 64
            db.commit()

        with pytest.raises(PlanValidationError, match="integrity"):
            svc.get_plan(session_id=SESSION_ID, plan_id=plan.plan_id)
    finally:
        engine.dispose()


def test_off_plan_calls_escalate_to_ask_while_normal_blocks_stay_blocked(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = load_profile("coding")
    policy = load_policy()
    pipeline = GatewayPreflightPipeline(
        policy,
        workspace_root=ws,
        path_jail=PathJail(ws),
    )

    # Plan only includes files.read
    approved_plan = Plan(
        plan_id="p_01J8ABCDEFGH1234567890ABCD",
        session_id=SESSION_ID,
        summary="Read files only",
        profile=profile.name,
        steps=[PlanStep(tool="files.read", tier="read")],
        scopes=["mcp:files"],
        plan_hash="a" * 64,
    )

    # Planned call -> allow
    on_plan = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c1",
            tool="files.read",
            args={"path": "hello.txt"},
        ),
        profile,
        approved_plan=approved_plan,
    )
    assert on_plan.decision == "allow"
    assert on_plan.executable is True

    # Off-plan call (files.write is normally allow in coding) -> escalated to ask
    off_plan = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c2",
            tool="files.write",
            args={"path": "out.txt", "content": "hi"},
        ),
        profile,
        approved_plan=approved_plan,
    )
    assert off_plan.decision == "ask"
    assert off_plan.executable is False
    assert off_plan.escalated is True
    assert "off-plan" in off_plan.reason

    # Normal gateway checks still block path jail escape even if tool is in plan
    escape = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c3",
            tool="files.read",
            args={"path": "/etc/passwd"},
        ),
        profile,
        approved_plan=approved_plan,
    )
    assert escape.decision == "block"
    assert escape.error is not None
    assert escape.error.code == ErrorCode.PATH_JAIL


def test_plan_preview_routes_create_edit_and_approve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "plan_routes.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _FakeClock()

    container_mgr = MagicMock()
    container_mgr.client = MagicMock()
    container_mgr.create_session.return_value = MagicMock(
        container_id="cid_1",
        ip_address="172.28.0.2",
    )
    runtime = MagicMock()
    runtime.build_workspace_path_jail.return_value = PathJail(tmp_path)

    runner = SessionRunner(
        session_factory=factory,
        container_manager=container_mgr,
        event_bus=bus,
        approval_manager=ApprovalManager(factory, bus, clock=clock),
        profile_loader=ProfileLoader(),
        policy_loader=load_policy if False else MagicMock(load=load_policy),
        runtime_factory=lambda **_kw: runtime,
        llm_factory=lambda: _FakeLlm(),
        clock=clock,
    )

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_session_runner] = lambda: runner
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}

        create_resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "request_text": "SECRET_PROMPT_DO_NOT_PERSIST",
                "profile": "coding",
                "plan_preview": True,
            },
        )
        assert create_resp.status_code == 200
        body = create_resp.json()
        assert body["state"] == "AWAITING_PLAN_APPROVAL"
        plan = body["plan"]
        session_id = body["session_id"]
        assert "SECRET_PROMPT_DO_NOT_PERSIST" not in plan["summary"]

        edit_resp = client.post(
            f"/api/v1/sessions/{session_id}/plan/edit",
            headers=headers,
            json={"plan_id": plan["plan_id"], "remove_steps": [0]},
        )
        assert edit_resp.status_code == 200
        edited_plan = edit_resp.json()["plan"]
        assert len(edited_plan["steps"]) == len(plan["steps"]) - 1

        approve_resp = client.post(
            f"/api/v1/sessions/{session_id}/plan/approve",
            headers=headers,
            json={"plan_id": plan["plan_id"]},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json() == {"state": "ACTIVE"}

        rec = runner.wait_for_session(session_id, timeout=5.0)
        assert rec is not None
        assert rec.status == "completed"
    finally:
        app.dependency_overrides.clear()
        runner.shutdown(wait=True)
        engine.dispose()


def test_alembic_migration_0004_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_plans.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "plans" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(cfg, "0003_add_device_push_tokens")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "plans" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

"""Unit tests for F-17 Budget meter: real usage, explicit pricing, and limits."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from control_plane.agent.budget import (
    BudgetAccountingError,
    BudgetMeter,
    ModelPricing,
    load_pricing_from_env,
)
from control_plane.agent.llm import (
    LlmTurnResponse,
    LlmUsage,
    ScriptedLlmAdapter,
)
from control_plane.agent.loop import AgentLoop
from control_plane.agent.openai_adapter import _parse_responses_output
from control_plane.api.deps import derive_owner_id, get_event_bus, get_session_factory
from control_plane.api.main import app
from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import ErrorCode
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from fastapi.testclient import TestClient

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"
TEST_TOKEN = "test-budget-token"


def test_pricing_loader_rejects_invalid_json_and_never_invents_prices() -> None:
    assert load_pricing_from_env("") == {}

    with pytest.raises(BudgetAccountingError):
        load_pricing_from_env("{not json")

    with pytest.raises(BudgetAccountingError):
        load_pricing_from_env(json.dumps({"gpt-4o": {"input_per_1m_usd": -1}}))

    table = load_pricing_from_env(
        json.dumps(
            {
                "gpt-4o-mini": {
                    "input_per_1m_usd": 0.15,
                    "output_per_1m_usd": 0.60,
                }
            }
        )
    )
    assert table["gpt-4o-mini"].input_per_1m_usd == 0.15
    assert table["gpt-4o-mini"].output_per_1m_usd == 0.60


def test_budget_meter_fails_clearly_when_strict_accounting_cannot_compute() -> None:
    meter = BudgetMeter(
        calls_max=10,
        usd_max=0.50,
        pricing={"gpt-4o": ModelPricing(2.5, 10.0)},
        require_usd_accounting=True,
    )
    with pytest.raises(BudgetAccountingError, match="missing"):
        meter.record_llm_usage(None)

    with pytest.raises(BudgetAccountingError, match="No explicit pricing"):
        meter.record_llm_usage(
            LlmUsage(input_tokens=100, output_tokens=50, model="unknown-model")
        )

    # Non-strict mode never reports fake 0.0 when usage is absent
    lenient = BudgetMeter(calls_max=10, usd_max=0.50, require_usd_accounting=False)
    assert lenient.record_llm_usage(None) is None
    assert lenient.usd_used is None


def test_openai_response_extracts_real_provider_usage() -> None:
    fake_resp = SimpleNamespace(
        error=None,
        status="completed",
        model="gpt-4o-mini",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="hello")],
            )
        ],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )
    parsed = _parse_responses_output(fake_resp, {}, {}, model="gpt-4o-mini")
    assert parsed.usage == LlmUsage(
        input_tokens=1200, output_tokens=300, model="gpt-4o-mini"
    )


def test_agent_loop_enforces_usd_budget_with_real_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "budget.db"
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
                    owner_id=derive_owner_id(TEST_TOKEN),
                    created_at="2026-11-27T21:30:00Z",
                    updated_at="2026-11-27T21:30:00Z",
                )
            )
            db.commit()

        bus = EventBus(factory)
        policy = load_policy()
        profile = load_profile("coding")
        profile = profile.model_copy(
            update={"budget": profile.budget.model_copy(update={"max_usd": 0.01})}
        )
        pipeline = GatewayPreflightPipeline(
            policy, workspace_root=tmp_path, path_jail=PathJail(tmp_path)
        )
        coordinator = AuditedGatewayCoordinator(
            pipeline=pipeline, audit_writer=AuditWriter(factory)
        )
        pricing = {
            "gpt-4o": ModelPricing(input_per_1m_usd=10.0, output_per_1m_usd=20.0)
        }

        # Turn costs (1000/1e6)*10 + (1000/1e6)*20 = 0.03 > 0.01 max_usd
        llm = ScriptedLlmAdapter(
            [
                LlmTurnResponse(
                    assistant_text="expensive",
                    usage=LlmUsage(
                        input_tokens=1000, output_tokens=1000, model="gpt-4o"
                    ),
                )
            ]
        )
        loop = AgentLoop(
            llm=llm,
            coordinator=coordinator,
            approval_manager=ApprovalManager(factory, bus),
            event_bus=bus,
            policy=policy,
            profile=profile,
            pricing=pricing,
            require_usd_accounting=True,
        )
        res = loop.run(
            session_id=SESSION_ID,
            request_text="do work",
            executor=lambda _v: {},
        )
        assert res.status == "budget_exceeded"
        assert res.error is not None
        assert res.error.code == ErrorCode.BUDGET_EXCEEDED

        app.dependency_overrides[get_session_factory] = lambda: factory
        app.dependency_overrides[get_event_bus] = lambda: bus
        try:
            client = TestClient(app)
            resp = client.get(
                f"/api/v1/budget/{SESSION_ID}",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["usd_used"] == pytest.approx(0.03)
            assert data["usd_max"] == 0.01
        finally:
            app.dependency_overrides.clear()
    finally:
        engine.dispose()

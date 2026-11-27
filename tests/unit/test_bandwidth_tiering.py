"""Unit and WebSocket integration tests for F-15 adaptive bandwidth tiering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from control_plane.api.bandwidth import BandwidthController, get_bandwidth_controller
from control_plane.api.deps import derive_owner_id, get_event_bus, get_session_factory
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock, get_stream_poll_interval
from control_plane.api.stream_tokens import issue_stream_token
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

TEST_TOKEN = "test-bandwidth-token"
SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"
BASE_NOW = datetime(2026, 11, 27, 2, 0, 0, tzinfo=UTC)


def test_bandwidth_controller_hysteresis_and_measurements() -> None:
    """Downgrades immediately; upgrades only after hysteresis + cooldown."""
    ctrl = BandwidthController(upgrade_stable_samples=3, upgrade_cooldown_seconds=5.0)
    t0 = BASE_NOW

    assert ctrl.get_tier(SESSION_ID) == "video"

    # Invalid inputs rejected
    for bad in (-1.0, float("nan"), float("inf"), True, "500", 20_000_000.0):
        with pytest.raises(ValueError):
            ctrl.record(SESSION_ID, bad, now=t0)

    # Fast downgrade: video -> screenshots at < 1500 kbps
    tr1 = ctrl.record(SESSION_ID, 600.0, now=t0)
    assert tr1 is not None
    assert tr1.previous_tier == "video"
    assert tr1.tier == "screenshots"

    # Hysteresis: 1600 kbps is above 1500 but below 1800 -> stays screenshots
    assert ctrl.record(SESSION_ID, 1600.0, now=t0 + timedelta(seconds=6)) is None
    assert ctrl.get_tier(SESSION_ID) == "screenshots"

    # Fast downgrade: screenshots -> text at < 150 kbps
    tr2 = ctrl.record(SESSION_ID, 90.0, now=t0 + timedelta(seconds=7))
    assert tr2 is not None
    assert tr2.previous_tier == "screenshots"
    assert tr2.tier == "text"

    # Slow stepwise upgrade: text -> screenshots (>= 200 kbps, 3 samples, 5s cooldown)
    assert ctrl.record(SESSION_ID, 250.0, now=t0 + timedelta(seconds=8)) is None
    assert ctrl.record(SESSION_ID, 250.0, now=t0 + timedelta(seconds=9)) is None
    # 3rd sample before 5s cooldown -> still text
    assert ctrl.record(SESSION_ID, 250.0, now=t0 + timedelta(seconds=10)) is None
    # 4th sample after 5s cooldown -> upgrades to screenshots
    tr3 = ctrl.record(SESSION_ID, 250.0, now=t0 + timedelta(seconds=13))
    assert tr3 is not None
    assert tr3.previous_tier == "text"
    assert tr3.tier == "screenshots"

    # Measurements are recorded with timestamps and active tier
    measurements = ctrl.get_measurements(SESSION_ID)
    assert len(measurements) == 7
    assert measurements[0].kbps == 600.0
    assert measurements[0].tier == "screenshots"
    assert measurements[-1].tier == "screenshots"


def test_stream_throughput_publishes_and_replays_tier_change_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS throughput reports emit tier_change events and replay via last_seq."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "bw_stream.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    owner_id = derive_owner_id(TEST_TOKEN)

    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                created_at="2026-11-27T02:00:00Z",
                updated_at="2026-11-27T02:00:00Z",
            )
        )
        db.commit()

    bus = EventBus(factory)
    ctrl = BandwidthController(upgrade_stable_samples=2, upgrade_cooldown_seconds=0.0)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    app.dependency_overrides[get_bandwidth_controller] = lambda: ctrl
    app.dependency_overrides[get_stream_poll_interval] = lambda: 0.01
    app.dependency_overrides[get_stream_clock] = lambda: (lambda: BASE_NOW)

    token = issue_stream_token(
        session_id=SESSION_ID,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    try:
        client = TestClient(app)
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_ID}/stream?token={token}"
        ) as ws:
            ws.send_json({"type": "hello", "last_seq": 0})
            ws.send_json({"type": "throughput", "kbps": 500.0})
            ev1 = ws.receive_json()
            assert ev1["type"] == "tier_change"
            assert ev1["seq"] == 1
            assert ev1["data"] == {
                "tier": "screenshots",
                "previous_tier": "video",
                "kbps": 500.0,
            }

            ws.send_json({"type": "throughput", "kbps": 80.0})
            ev2 = ws.receive_json()
            assert ev2["type"] == "tier_change"
            assert ev2["seq"] == 2
            assert ev2["data"] == {
                "tier": "text",
                "previous_tier": "screenshots",
                "kbps": 80.0,
            }

        # Reconnect with last_seq=1 replays only seq=2 tier_change
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_ID}/stream?token={token}"
        ) as ws2:
            ws2.send_json({"type": "hello", "last_seq": 1})
            replayed = ws2.receive_json()
            assert replayed["seq"] == 2
            assert replayed["type"] == "tier_change"
            assert replayed["data"]["tier"] == "text"

        # Invalid throughput frame closes WS with 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/api/v1/sessions/{SESSION_ID}/stream?token={token}"
            ) as ws3:
                ws3.send_json({"type": "hello", "last_seq": 2})
                ws3.send_json({"type": "throughput", "kbps": -5})
                ws3.receive_json()
        assert exc_info.value.code == 1008
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

"""Unit tests for Phase 4 session control routes and container pause/resume."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import docker.errors
import pytest
from control_plane.api.deps import (
    derive_owner_id,
    get_container_manager,
    get_event_bus,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock
from control_plane.contracts import ErrorCode
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.sessions.container_manager import (
    DockerResourceError,
    SessionContainerManager,
)
from fastapi.testclient import TestClient

TEST_TOKEN = "test-session-control-token"
SESSION_OWNED = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_OTHER = "s_01ABCDEFGHJKMNPTVW98765432"
SESSION_MISSING = "s_01ABCDEFGHJKMNPTVW00000000"
BASE_NOW = datetime(2026, 11, 27, 2, 0, 0, tzinfo=UTC)


@pytest.fixture()
def control_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated SQLite DB, EventBus, mock container manager, and TestClient."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "control_sessions.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    mock_mgr = MagicMock(spec=SessionContainerManager)

    owner_id = derive_owner_id(TEST_TOKEN)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_OWNED,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                dry_run=0,
                created_at="2026-11-27T02:00:00Z",
                updated_at="2026-11-27T02:00:00Z",
            )
        )
        db.add(
            SessionModel(
                session_id=SESSION_OTHER,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_someone_else",
                dry_run=0,
                created_at="2026-11-27T02:00:00Z",
                updated_at="2026-11-27T02:00:00Z",
            )
        )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    app.dependency_overrides[get_container_manager] = lambda: mock_mgr
    app.dependency_overrides[get_stream_clock] = lambda: (lambda: BASE_NOW)

    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "bus": bus,
            "container_manager": mock_mgr,
            "headers": {"Authorization": f"Bearer {TEST_TOKEN}"},
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _set_state(factory, session_id: str, state: str) -> None:
    with factory() as db:
        row = db.get(SessionModel, session_id)
        assert row is not None
        row.state = state
        db.commit()


def _get_row(factory, session_id: str) -> SessionModel:
    with factory() as db:
        row = db.get(SessionModel, session_id)
        assert row is not None
        return row


def test_control_routes_require_valid_bearer_token(
    control_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing, wrong, or unset APP_TOKEN rejects all 4 control routes."""
    client: TestClient = control_env["client"]
    endpoints = [
        (f"/api/v1/sessions/{SESSION_OWNED}/pause", None),
        (f"/api/v1/sessions/{SESSION_OWNED}/resume", None),
        (f"/api/v1/sessions/{SESSION_OWNED}/takeover", {"enabled": True}),
        (f"/api/v1/sessions/{SESSION_OWNED}/end", None),
    ]

    for url, body in endpoints:
        for headers in [{}, {"Authorization": "Bearer wrong"}]:
            resp = client.post(url, json=body, headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    monkeypatch.delenv("APP_TOKEN", raising=False)
    for url, body in endpoints:
        resp = client.post(url, json=body, headers=control_env["headers"])
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR


def test_control_routes_enforce_ownership_without_leaking(
    control_env: dict[str, Any],
) -> None:
    """Non-owned and missing sessions return the identical 404 response."""
    client: TestClient = control_env["client"]
    headers = control_env["headers"]

    for suffix, body in [
        ("pause", None),
        ("resume", None),
        ("takeover", {"enabled": True}),
        ("end", None),
    ]:
        r_other = client.post(
            f"/api/v1/sessions/{SESSION_OTHER}/{suffix}", json=body, headers=headers
        )
        r_missing = client.post(
            f"/api/v1/sessions/{SESSION_MISSING}/{suffix}", json=body, headers=headers
        )
        assert r_other.status_code == 404
        assert r_missing.status_code == 404
        assert r_other.json() == r_missing.json()
        assert r_other.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE


def test_pause_resume_takeover_and_idempotent_retries(
    control_env: dict[str, Any],
) -> None:
    """Pause, resume, takeover, and idempotent retries behave as documented."""
    client: TestClient = control_env["client"]
    factory = control_env["factory"]
    bus: EventBus = control_env["bus"]
    mgr: MagicMock = control_env["container_manager"]
    headers = control_env["headers"]

    # Pause ACTIVE -> PAUSED
    r_pause = client.post(f"/api/v1/sessions/{SESSION_OWNED}/pause", headers=headers)
    assert r_pause.status_code == 200
    assert r_pause.json() == {"state": "PAUSED"}
    mgr.pause_session.assert_called_once_with(SESSION_OWNED)
    assert _get_row(factory, SESSION_OWNED).state == "PAUSED"

    # Idempotent retry of pause and takeover(enabled=True) when already PAUSED
    r_pause_again = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/pause", headers=headers
    )
    r_takeover_on_again = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/takeover",
        json={"enabled": True},
        headers=headers,
    )
    assert r_pause_again.json() == {"state": "PAUSED"}
    assert r_takeover_on_again.json() == {"state": "PAUSED"}
    assert mgr.pause_session.call_count == 1

    # Resume PAUSED -> ACTIVE
    r_resume = client.post(f"/api/v1/sessions/{SESSION_OWNED}/resume", headers=headers)
    assert r_resume.status_code == 200
    assert r_resume.json() == {"state": "ACTIVE"}
    mgr.resume_session.assert_called_once_with(SESSION_OWNED)
    assert _get_row(factory, SESSION_OWNED).state == "ACTIVE"

    # Idempotent retry of resume and takeover(enabled=False) when already ACTIVE
    r_resume_again = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/resume", headers=headers
    )
    r_takeover_off_again = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/takeover",
        json={"enabled": False},
        headers=headers,
    )
    assert r_resume_again.json() == {"state": "ACTIVE"}
    assert r_takeover_off_again.json() == {"state": "ACTIVE"}
    assert mgr.resume_session.call_count == 1

    # Takeover enabled=True pauses, enabled=False resumes with documented event reasons
    r_to_on = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/takeover",
        json={"enabled": True},
        headers=headers,
    )
    assert r_to_on.status_code == 200
    assert r_to_on.json() == {"state": "PAUSED"}

    r_to_off = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/takeover",
        json={"enabled": False},
        headers=headers,
    )
    assert r_to_off.status_code == 200
    assert r_to_off.json() == {"state": "ACTIVE"}

    events = bus.replay(session_id=SESSION_OWNED)
    assert [(e.type, e.data) for e in events] == [
        ("session_state", {"state": "PAUSED"}),
        ("session_state", {"state": "ACTIVE"}),
        ("session_state", {"state": "PAUSED", "reason": "takeover enabled"}),
        ("session_state", {"state": "ACTIVE", "reason": "takeover disabled"}),
    ]


@pytest.mark.parametrize(
    "bad_state",
    [
        "CREATED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "STARTING",
        "ENDING",
        "ENDED",
        "FAILED",
    ],
)
def test_invalid_state_transitions_rejected(
    control_env: dict[str, Any], bad_state: str
) -> None:
    """Pause/resume/takeover reject non-transitionable session states with 409."""
    client: TestClient = control_env["client"]
    factory = control_env["factory"]
    mgr: MagicMock = control_env["container_manager"]
    headers = control_env["headers"]

    _set_state(factory, SESSION_OWNED, bad_state)

    for url, body in [
        (f"/api/v1/sessions/{SESSION_OWNED}/pause", None),
        (f"/api/v1/sessions/{SESSION_OWNED}/resume", None),
        (f"/api/v1/sessions/{SESSION_OWNED}/takeover", {"enabled": True}),
        (f"/api/v1/sessions/{SESSION_OWNED}/takeover", {"enabled": False}),
    ]:
        resp = client.post(url, json=body, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

    mgr.pause_session.assert_not_called()
    mgr.resume_session.assert_not_called()


def test_container_pause_resume_failures_preserve_state(
    control_env: dict[str, Any],
) -> None:
    """Container errors on pause/resume fail safely without mutating DB or emitting."""
    client: TestClient = control_env["client"]
    factory = control_env["factory"]
    bus: EventBus = control_env["bus"]
    mgr: MagicMock = control_env["container_manager"]
    headers = control_env["headers"]

    mgr.pause_session.side_effect = DockerResourceError("docker pause failed")
    r_pause = client.post(f"/api/v1/sessions/{SESSION_OWNED}/pause", headers=headers)
    assert r_pause.status_code == 500
    assert r_pause.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR
    assert _get_row(factory, SESSION_OWNED).state == "ACTIVE"

    _set_state(factory, SESSION_OWNED, "PAUSED")
    mgr.resume_session.side_effect = DockerResourceError("docker unpause failed")
    r_resume = client.post(f"/api/v1/sessions/{SESSION_OWNED}/resume", headers=headers)
    assert r_resume.status_code == 500
    assert r_resume.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR
    assert _get_row(factory, SESSION_OWNED).state == "PAUSED"
    assert bus.replay(session_id=SESSION_OWNED) == []


def test_end_session_teardown_and_failure_safety(
    control_env: dict[str, Any],
) -> None:
    """End moves to ENDING, runs teardown, and sets ENDED after teardown."""
    client: TestClient = control_env["client"]
    factory = control_env["factory"]
    bus: EventBus = control_env["bus"]
    mgr: MagicMock = control_env["container_manager"]
    headers = control_env["headers"]

    # Teardown failure leaves session in ENDING and emits no session_ended
    mgr.teardown_session.side_effect = DockerResourceError("volume busy")
    r_fail = client.post(f"/api/v1/sessions/{SESSION_OWNED}/end", headers=headers)
    assert r_fail.status_code == 500
    assert r_fail.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR

    mid_row = _get_row(factory, SESSION_OWNED)
    assert mid_row.state == "ENDING"
    assert mid_row.receipt_url is None
    assert [(e.type, e.data) for e in bus.replay(session_id=SESSION_OWNED)] == [
        ("session_state", {"state": "ENDING"})
    ]

    # Retry end after teardown succeeds completes transition to ENDED
    mgr.teardown_session.side_effect = None
    r_ok = client.post(f"/api/v1/sessions/{SESSION_OWNED}/end", headers=headers)
    assert r_ok.status_code == 200
    assert r_ok.json() == {"state": "ENDED"}

    expected_receipt = f"/api/v1/sessions/{SESSION_OWNED}/receipt"
    end_row = _get_row(factory, SESSION_OWNED)
    assert end_row.state == "ENDED"
    assert end_row.receipt_url == expected_receipt

    events = bus.replay(session_id=SESSION_OWNED)
    assert [(e.type, e.data) for e in events] == [
        ("session_state", {"state": "ENDING"}),
        ("session_state", {"state": "ENDED"}),
        ("session_ended", {"receipt_url": expected_receipt}),
    ]

    # Idempotent retry on ENDED does not re-run teardown
    mgr.teardown_session.reset_mock()
    r_retry = client.post(f"/api/v1/sessions/{SESSION_OWNED}/end", headers=headers)
    assert r_retry.status_code == 200
    assert r_retry.json() == {"state": "ENDED"}
    mgr.teardown_session.assert_not_called()


def test_session_container_manager_pause_and_resume_methods() -> None:
    """SessionContainerManager pause_session/resume_session call Docker safely."""
    mock_docker = MagicMock()
    container = MagicMock()
    container.status = "running"
    container.attrs = {"State": {"Running": True, "Paused": False}}
    mock_docker.containers.get.return_value = container

    mgr = SessionContainerManager(docker_client=mock_docker)

    mgr.pause_session(SESSION_OWNED)
    container.pause.assert_called_once()

    # Already paused -> idempotent
    container.status = "paused"
    container.attrs = {"State": {"Running": True, "Paused": True}}
    mgr.pause_session(SESSION_OWNED)
    assert container.pause.call_count == 1

    mgr.resume_session(SESSION_OWNED)
    container.unpause.assert_called_once()

    # Missing container raises DockerResourceError
    mock_docker.containers.get.side_effect = docker.errors.NotFound("missing")
    with pytest.raises(DockerResourceError):
        mgr.pause_session(SESSION_OWNED)


def test_post_session_message_auth_ownership_validation_and_states(
    control_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /message enforces auth, identical 404 ownership, validation, and states."""
    client: TestClient = control_env["client"]
    factory = control_env["factory"]
    headers = control_env["headers"]
    url = f"/api/v1/sessions/{SESSION_OWNED}/message"

    # Auth failures -> 401 AUTH_ERROR
    for bad_headers in [{}, {"Authorization": "Bearer wrong-token"}]:
        resp = client.post(url, json={"text": "hi"}, headers=bad_headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    monkeypatch.delenv("APP_TOKEN", raising=False)
    r_unset = client.post(url, json={"text": "hi"}, headers=headers)
    assert r_unset.status_code == 401
    assert r_unset.json()["error"]["code"] == ErrorCode.AUTH_ERROR
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)

    # Ownership check -> identical 404 for non-owned and missing
    r_other = client.post(
        f"/api/v1/sessions/{SESSION_OTHER}/message",
        json={"text": "hi"},
        headers=headers,
    )
    r_missing = client.post(
        f"/api/v1/sessions/{SESSION_MISSING}/message",
        json={"text": "hi"},
        headers=headers,
    )
    assert r_other.status_code == 404
    assert r_missing.status_code == 404
    assert r_other.json() == r_missing.json()
    assert r_other.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

    # Body validation -> extra fields rejected (422), blank text rejected (400)
    r_extra = client.post(url, json={"text": "hi", "extra": 1}, headers=headers)
    assert r_extra.status_code == 422

    r_blank = client.post(url, json={"text": "   "}, headers=headers)
    assert r_blank.status_code == 400
    assert r_blank.json()["error"]["code"] == ErrorCode.ARG_INVALID

    # Non-ACTIVE states rejected with 409 SESSION_INACTIVE
    for bad_state in (
        "CREATED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "STARTING",
        "PAUSED",
        "ENDING",
        "ENDED",
        "FAILED",
    ):
        _set_state(factory, SESSION_OWNED, bad_state)
        r_state = client.post(url, json={"text": "hi"}, headers=headers)
        assert r_state.status_code == 409
        assert r_state.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

    # ACTIVE without in-memory runtime/context (e.g. after restart) fails closed
    _set_state(factory, SESSION_OWNED, "ACTIVE")
    r_no_runtime = client.post(url, json={"text": "hi"}, headers=headers)
    assert r_no_runtime.status_code == 409
    assert r_no_runtime.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

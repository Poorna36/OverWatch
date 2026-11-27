"""Focused unit tests for Phase 4 WebSocket stream endpoint & tokens (F-10)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import yaml
from control_plane.api.deps import (
    derive_owner_id,
    get_event_bus,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock, get_stream_poll_interval
from control_plane.api.stream_tokens import (
    STREAM_TOKEN_TTL_SECONDS,
    StreamTokenError,
    issue_stream_token,
    verify_stream_token,
)
from control_plane.contracts import EventEnvelope
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

TEST_TOKEN = "test-demo-app-token"
SESSION_OWNED = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_OTHER = "s_01ABCDEFGHJKMNPTVW98765432"
SESSION_MISSING = "s_01ABCDEFGHJKMNPTVW00000000"
BASE_NOW = datetime(2026, 11, 26, 22, 30, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def stream_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated SQLite DB, EventBus, fake clock, and TestClient."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "ws_stream.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _FakeClock(BASE_NOW)

    owner_id = derive_owner_id(TEST_TOKEN)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_OWNED,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                dry_run=0,
                created_at="2026-11-26T22:00:00Z",
                updated_at="2026-11-26T22:00:00Z",
            )
        )
        db.add(
            SessionModel(
                session_id=SESSION_OTHER,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_someone_else",
                dry_run=0,
                created_at="2026-11-26T22:00:00Z",
                updated_at="2026-11-26T22:00:00Z",
            )
        )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    app.dependency_overrides[get_stream_poll_interval] = lambda: 0.01
    app.dependency_overrides[get_stream_clock] = lambda: clock

    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "bus": bus,
            "clock": clock,
            "owner_id": owner_id,
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Stream Token Signing, Expiry, Scope, and Session Binding
# ---------------------------------------------------------------------------
def test_stream_token_signing_lifetime_and_session_binding() -> None:
    """Tokens validate signature, 15m TTL, scope, owner, and session binding."""
    owner_id = derive_owner_id(TEST_TOKEN)
    token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    assert TEST_TOKEN not in token

    claims = verify_stream_token(
        token,
        expected_session_id=SESSION_OWNED,
        signing_key=TEST_TOKEN,
        now=BASE_NOW + timedelta(minutes=14, seconds=59),
    )
    assert claims.session_id == SESSION_OWNED
    assert claims.owner_id == owner_id
    assert claims.scope == "stream"
    assert claims.exp - claims.iat == STREAM_TOKEN_TTL_SECONDS

    # Exact expiry (15m) fails closed
    with pytest.raises(StreamTokenError, match="expired"):
        verify_stream_token(
            token,
            expected_session_id=SESSION_OWNED,
            signing_key=TEST_TOKEN,
            now=BASE_NOW + timedelta(seconds=STREAM_TOKEN_TTL_SECONDS),
        )

    # Session mismatch fails closed
    with pytest.raises(StreamTokenError, match="mismatch"):
        verify_stream_token(
            token,
            expected_session_id=SESSION_OTHER,
            signing_key=TEST_TOKEN,
            now=BASE_NOW,
        )

    # Wrong signing key fails closed
    with pytest.raises(StreamTokenError, match="signature"):
        verify_stream_token(
            token,
            expected_session_id=SESSION_OWNED,
            signing_key="wrong-app-token",
            now=BASE_NOW,
        )

    # Tampered signature / payload fails closed
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(StreamTokenError, match="signature"):
        verify_stream_token(
            tampered,
            expected_session_id=SESSION_OWNED,
            signing_key=TEST_TOKEN,
            now=BASE_NOW,
        )

    # Reject TTL exceeding documented 15 minutes
    with pytest.raises(StreamTokenError, match="ttl_seconds"):
        issue_stream_token(
            session_id=SESSION_OWNED,
            owner_id=owner_id,
            signing_key=TEST_TOKEN,
            ttl_seconds=STREAM_TOKEN_TTL_SECONDS + 1,
            now=BASE_NOW,
        )


# ---------------------------------------------------------------------------
# 2. WebSocket Auth, Ownership, and Session State Checks
# ---------------------------------------------------------------------------
def test_ws_rejects_missing_expired_mismatched_and_non_owned_tokens(
    stream_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid, expired, mismatched, unowned, or missing-APP_TOKEN fail closed."""
    client: TestClient = stream_env["client"]
    clock: _FakeClock = stream_env["clock"]
    owner_id: str = stream_env["owner_id"]

    owned_token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )

    # Missing or malformed query token
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/sessions/{SESSION_OWNED}/stream"):
            pass

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token=invalid"
        ):
            pass

    # Session mismatch (owned token used against another session URL)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OTHER}/stream?token={owned_token}"
        ):
            pass

    # Token for SESSION_OTHER with caller owner_id fails when DB owner differs
    token_for_other = issue_stream_token(
        session_id=SESSION_OTHER,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OTHER}/stream?token={token_for_other}"
        ):
            pass

    # Nonexistent session rejected even with validly signed token
    token_for_missing = issue_stream_token(
        session_id=SESSION_MISSING,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_MISSING}/stream?token={token_for_missing}"
        ):
            pass

    # Expired token rejected
    clock.now = BASE_NOW + timedelta(seconds=STREAM_TOKEN_TTL_SECONDS)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token={owned_token}"
        ):
            pass

    # Unset APP_TOKEN rejected
    clock.now = BASE_NOW
    monkeypatch.delenv("APP_TOKEN", raising=False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token={owned_token}"
        ):
            pass


@pytest.mark.parametrize("inactive_state", ["ENDING", "ENDED", "FAILED"])
def test_ws_rejects_inactive_sessions(
    stream_env: dict[str, Any],
    inactive_state: str,
) -> None:
    """Terminating/terminated sessions fail closed even with a fresh token."""
    client: TestClient = stream_env["client"]
    factory = stream_env["factory"]
    owner_id: str = stream_env["owner_id"]

    with factory() as db:
        row = db.get(SessionModel, SESSION_OWNED)
        assert row is not None
        row.state = inactive_state
        db.commit()

    token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token={token}"
        ):
            pass


# ---------------------------------------------------------------------------
# 3. Required Hello, Replay Order, Live Delivery, and Control Frames
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_first_payload",
    [
        "not-json",
        '{"type":"ping"}',
        '{"type":"throughput","kbps":1200}',
        '{"type":"hello","last_seq":-1}',
        '{"type":"hello","last_seq":true}',
        '{"type":"hello","last_seq":0,"extra":"x"}',
    ],
)
def test_ws_requires_valid_hello_as_first_message(
    stream_env: dict[str, Any],
    bad_first_payload: str,
) -> None:
    """Any first message other than a valid hello frame closes with 1008."""
    client: TestClient = stream_env["client"]
    owner_id: str = stream_env["owner_id"]
    token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token={token}"
        ) as ws:
            ws.send_text(bad_first_payload)
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_ws_replays_from_last_seq_streams_live_and_accepts_ping_throughput(
    stream_env: dict[str, Any],
) -> None:
    """Replays seq > last_seq in order, streams newly persisted events, handles ping."""
    client: TestClient = stream_env["client"]
    bus: EventBus = stream_env["bus"]
    owner_id: str = stream_env["owner_id"]

    bus.publish(
        session_id=SESSION_OWNED,
        event_type="session_state",
        data={"state": "STARTING"},
    )
    bus.publish(
        session_id=SESSION_OWNED,
        event_type="session_state",
        data={"state": "ACTIVE"},
    )
    bus.publish(
        session_id=SESSION_OWNED,
        event_type="tool_call",
        data={
            "call_id": "call_1",
            "tool": "files.write",
            "decision": "allow",
            "args_redacted": {"path": "main.py", "api_key": "secret-val"},
        },
    )

    token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{SESSION_OWNED}/stream?token={token}"
    ) as ws:
        ws.send_json({"type": "hello", "last_seq": 1})

        frame2 = ws.receive_json()
        frame3 = ws.receive_json()

        env2 = EventEnvelope.model_validate(frame2)
        env3 = EventEnvelope.model_validate(frame3)
        assert (env2.seq, env2.type) == (2, "session_state")
        assert (env3.seq, env3.type) == (3, "tool_call")
        assert "api_key" not in env3.data["args_redacted"]

        # Accepted client heartbeat / bandwidth frames must not emit non-contract frames
        ws.send_json({"type": "ping"})
        ws.send_json({"type": "throughput", "kbps": 1450.5})

        # Persist a new event after handshake; must arrive live in seq order
        published = bus.publish(
            session_id=SESSION_OWNED,
            event_type="agent_message",
            data={"text": "task continuing"},
        )
        frame4 = ws.receive_json()
        env4 = EventEnvelope.model_validate(frame4)
        assert env4.seq == published.seq == 4
        assert env4.type == "agent_message"
        assert env4.data == {"text": "task continuing"}


def test_ws_rejects_malformed_runtime_frame_and_cleans_up(
    stream_env: dict[str, Any],
) -> None:
    """Malformed frames after hello close with 1008 and reconnect still works."""
    client: TestClient = stream_env["client"]
    bus: EventBus = stream_env["bus"]
    owner_id: str = stream_env["owner_id"]

    bus.publish(
        session_id=SESSION_OWNED,
        event_type="session_state",
        data={"state": "ACTIVE"},
    )
    token = issue_stream_token(
        session_id=SESSION_OWNED,
        owner_id=owner_id,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/v1/sessions/{SESSION_OWNED}/stream?token={token}"
        ) as ws:
            ws.send_json({"type": "hello"})
            replay1 = EventEnvelope.model_validate(ws.receive_json())
            assert replay1.seq == 1

            ws.send_json({"type": "throughput", "kbps": -10})
            ws.receive_text()

    assert exc_info.value.code == 1008

    # Reconnect cleanly after disconnect cleanup and continue streaming
    bus.publish(
        session_id=SESSION_OWNED,
        event_type="agent_message",
        data={"text": "reconnected ok"},
    )
    with client.websocket_connect(
        f"/api/v1/sessions/{SESSION_OWNED}/stream?token={token}"
    ) as ws:
        ws.send_json({"type": "hello", "last_seq": 1})
        replay2 = EventEnvelope.model_validate(ws.receive_json())
        assert replay2.seq == 2
        assert replay2.data == {"text": "reconnected ok"}


def test_stream_code_never_logs_tokens_or_query_strings() -> None:
    """Static sanity check that stream modules contain no logging of tokens."""
    stream_src = (
        Path(__file__).resolve().parents[2] / "control-plane" / "api" / "stream.py"
    ).read_text(encoding="utf-8")
    tokens_src = (
        Path(__file__).resolve().parents[2]
        / "control-plane"
        / "api"
        / "stream_tokens.py"
    ).read_text(encoding="utf-8")
    for text in (stream_src, tokens_src):
        assert "print(" not in text
        assert "logger" not in text
        assert "logging." not in text


def test_production_launch_configs_disable_uvicorn_access_log() -> None:
    """Dockerfile, Compose, and README launch Uvicorn with --no-access-log."""
    repo_root = Path(__file__).resolve().parents[2]

    dockerfile_lines = (
        (repo_root / "control-plane" / "Dockerfile")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    cmd_lines = [line for line in dockerfile_lines if line.startswith("CMD ")]
    assert len(cmd_lines) == 1
    cmd_args = json.loads(cmd_lines[0][4:].strip())
    assert cmd_args[0] == "uvicorn"
    assert "control_plane.api.main:app" in cmd_args
    assert "--no-access-log" in cmd_args

    compose_data = yaml.safe_load(
        (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    )
    cp_service = compose_data["services"]["control-plane"]
    cp_cmd = cp_service["command"]
    assert cp_cmd[0] == "uvicorn"
    assert "control_plane.api.main:app" in cp_cmd
    assert "--no-access-log" in cp_cmd

    readme_lines = (repo_root / "README.md").read_text(encoding="utf-8").splitlines()
    uvicorn_lines = [
        line.strip() for line in readme_lines if "uvicorn " in line.strip()
    ]
    assert len(uvicorn_lines) >= 1
    for cmd_line in uvicorn_lines:
        assert "--no-access-log" in cmd_line


def test_uvicorn_no_access_log_suppresses_access_logs_and_keeps_errors() -> None:
    """access_log=False disables uvicorn.access while preserving uvicorn.error."""
    logger_names = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
    saved = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in logger_names
    }
    try:
        cfg = uvicorn.Config(
            "control_plane.api.main:app",
            host="0.0.0.0",
            port=8000,
            access_log=False,
        )
        cfg.configure_logging()
        access_logger = logging.getLogger("uvicorn.access")
        error_logger = logging.getLogger("uvicorn.error")
        assert access_logger.handlers == []
        assert access_logger.hasHandlers() is False
        assert access_logger.propagate is False
        assert error_logger.hasHandlers() is True
        assert error_logger.isEnabledFor(logging.ERROR) is True
    finally:
        for name, (handlers, level, propagate) in saved.items():
            lg = logging.getLogger(name)
            lg.handlers = handlers
            lg.setLevel(level)
            lg.propagate = propagate

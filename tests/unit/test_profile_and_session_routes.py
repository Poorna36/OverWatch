"""Unit tests for Phase 4 read-only profiles and session detail routes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from control_plane.api.deps import (
    derive_owner_id,
    get_policy_loader,
    get_profile_loader,
    get_session_factory,
    get_session_runner,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock
from control_plane.api.stream_tokens import verify_stream_token
from control_plane.contracts import ErrorCode
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.gateway.mcp_runtime import McpTransportError
from control_plane.gateway.policy_loader import PolicyLoader
from control_plane.sessions.profile_loader import ProfileLoader
from fastapi.testclient import TestClient

TEST_TOKEN = "test-profile-session-token"
SESSION_ACTIVE = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_OTHER = "s_01ABCDEFGHJKMNPTVW98765432"
SESSION_MISSING = "s_01ABCDEFGHJKMNPTVW00000000"
BASE_NOW = datetime(2026, 11, 27, 1, 0, 0, tzinfo=UTC)


@pytest.fixture()
def read_api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated SQLite DB, env config, clock, and TestClient."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://overwatch.example.com:8443")

    db_path = tmp_path / "api_sessions.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    owner_id = derive_owner_id(TEST_TOKEN)

    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ACTIVE,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                dry_run=0,
                created_at="2026-11-27T01:00:00Z",
                updated_at="2026-11-27T01:00:00Z",
            )
        )
        db.add(
            SessionModel(
                session_id=SESSION_OTHER,
                state="ACTIVE",
                profile="general",
                owner_id="owner_someone_else",
                dry_run=0,
                created_at="2026-11-27T01:00:00Z",
                updated_at="2026-11-27T01:00:00Z",
            )
        )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_stream_clock] = lambda: (lambda: BASE_NOW)

    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "owner_id": owner_id,
            "headers": {"Authorization": f"Bearer {TEST_TOKEN}"},
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_profiles_and_session_detail_require_auth(
    read_api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing, invalid, or unset APP_TOKEN rejects both endpoints with AUTH_ERROR."""
    client: TestClient = read_api_env["client"]
    urls = [
        "/api/v1/profiles",
        "/api/v1/sessions",
        f"/api/v1/sessions/{SESSION_ACTIVE}",
    ]

    for url in urls:
        for headers in [{}, {"Authorization": "Bearer wrong-token"}]:
            resp = client.get(url, headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    monkeypatch.delenv("APP_TOKEN", raising=False)
    for url in urls:
        resp = client.get(url, headers=read_api_env["headers"])
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR


def test_get_profiles_filters_tools_by_server_and_policy_intersection(
    read_api_env: dict[str, Any], tmp_path: Path
) -> None:
    """GET /profiles returns only tools present in both profile servers and policy."""
    client: TestClient = read_api_env["client"]
    headers = read_api_env["headers"]

    resp = client.get("/api/v1/profiles", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    by_name = {p["name"]: p for p in data["profiles"]}

    assert "coding" in by_name
    assert "general" in by_name

    coding = by_name["coding"]
    assert set(coding.keys()) == {"name", "role", "tools"}
    assert coding["role"] == "coder"
    assert "terminal.run" in coding["tools"]
    assert "files.write" in coding["tools"]
    assert "desktop.click" in coding["tools"]
    assert not any(t.startswith("browser.") for t in coding["tools"])

    general = by_name["general"]
    assert general["role"] == "assistant"
    assert "browser.open" in general["tools"]
    assert "terminal.run" not in general["tools"]

    # Custom injected loaders verifying strict intersection in both directions
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "custom.yaml").write_text(
        "name: custom\n"
        "role: routine\n"
        "image: pocket/py-node:1.0\n"
        "mcp_servers:\n"
        "  files: { root: /workspace }\n"
        "  custom_unlisted_server: {}\n",
        encoding="utf-8",
    )
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "version: 1\n"
        "tiers:\n"
        "  read: { decision: allow }\n"
        "tools:\n"
        "  files.read: { tier: read }\n"
        "  terminal.run: { tier: read }\n",
        encoding="utf-8",
    )

    app.dependency_overrides[get_profile_loader] = lambda: ProfileLoader(profiles_dir)
    app.dependency_overrides[get_policy_loader] = lambda: PolicyLoader(policy_file)

    r_custom = client.get("/api/v1/profiles", headers=headers)
    assert r_custom.status_code == 200
    assert r_custom.json() == {
        "profiles": [{"name": "custom", "role": "routine", "tools": ["files.read"]}]
    }


def test_session_detail_ownership_prevents_existence_leak(
    read_api_env: dict[str, Any],
) -> None:
    """Non-owned and nonexistent sessions return the exact same 404 response."""
    client: TestClient = read_api_env["client"]
    headers = read_api_env["headers"]

    r_other = client.get(f"/api/v1/sessions/{SESSION_OTHER}", headers=headers)
    r_missing = client.get(f"/api/v1/sessions/{SESSION_MISSING}", headers=headers)

    assert r_other.status_code == 404
    assert r_missing.status_code == 404
    assert r_other.json() == r_missing.json()
    assert r_other.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE


def test_active_session_issues_verified_stream_url_without_logging_token(
    read_api_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Active sessions issue valid wss/ws stream_url, omit view_url, log no token."""
    client: TestClient = read_api_env["client"]
    headers = read_api_env["headers"]
    owner_id: str = read_api_env["owner_id"]

    with caplog.at_level(logging.DEBUG):
        resp_wss = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}", headers=headers)

    assert resp_wss.status_code == 200
    body = resp_wss.json()
    assert set(body.keys()) == {
        "session_id",
        "state",
        "profile",
        "created_at",
        "stream_url",
    }
    assert "view_url" not in body
    assert body["session_id"] == SESSION_ACTIVE
    assert body["state"] == "ACTIVE"
    assert body["profile"] == "coding"

    parsed = urlsplit(body["stream_url"])
    assert parsed.scheme == "wss"
    assert parsed.netloc == "overwatch.example.com:8443"
    assert parsed.path == f"/api/v1/sessions/{SESSION_ACTIVE}/stream"

    token = parse_qs(parsed.query)["token"][0]
    claims = verify_stream_token(
        token,
        expected_session_id=SESSION_ACTIVE,
        signing_key=TEST_TOKEN,
        now=BASE_NOW,
    )
    assert claims.owner_id == owner_id
    assert claims.session_id == SESSION_ACTIVE

    # Verify token & raw APP_TOKEN never appeared in logs
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert token not in log_text
    assert TEST_TOKEN not in log_text

    # Also verify http -> ws scheme conversion with path prefix
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000/proxy/")
    resp_ws = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}", headers=headers)
    assert resp_ws.status_code == 200
    parsed_ws = urlsplit(resp_ws.json()["stream_url"])
    assert parsed_ws.scheme == "ws"
    assert parsed_ws.netloc == "127.0.0.1:8000"
    assert parsed_ws.path == f"/proxy/api/v1/sessions/{SESSION_ACTIVE}/stream"


@pytest.mark.parametrize("inactive_state", ["ENDING", "ENDED", "FAILED"])
def test_inactive_session_omits_stream_url_and_view_url(
    read_api_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    inactive_state: str,
) -> None:
    """Terminating/terminated sessions omit stream_url and view_url."""
    client: TestClient = read_api_env["client"]
    factory = read_api_env["factory"]
    headers = read_api_env["headers"]

    # Should succeed without PUBLIC_BASE_URL since no stream_url is issued
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    with factory() as db:
        row = db.get(SessionModel, SESSION_ACTIVE)
        assert row is not None
        row.state = inactive_state
        db.commit()

    resp = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "session_id": SESSION_ACTIVE,
        "state": inactive_state,
        "profile": "coding",
        "created_at": "2026-11-27T01:00:00Z",
    }
    assert "stream_url" not in body
    assert "view_url" not in body


@pytest.mark.parametrize(
    "bad_url",
    [
        None,
        "",
        "   ",
        "ws://localhost:8000",
        "ftp://example.com",
        "not-a-url",
        "http://user:pass@localhost:8000",
        "https://example.com?foo=bar",
        "https://example.com#frag",
        "https://example.com /space",
    ],
)
def test_invalid_public_base_url_fails_closed(
    read_api_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    bad_url: str | None,
) -> None:
    """Missing or malformed PUBLIC_BASE_URL fails closed for active sessions."""
    client: TestClient = read_api_env["client"]
    headers = read_api_env["headers"]

    if bad_url is None:
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("PUBLIC_BASE_URL", bad_url)

    resp = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}", headers=headers)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR


def test_list_sessions_empty_for_owner(
    read_api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller with no owned sessions receives an empty sessions list."""
    monkeypatch.setenv("APP_TOKEN", "brand-new-caller-token")
    headers = {"Authorization": "Bearer brand-new-caller-token"}

    resp = read_api_env["client"].get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_list_sessions_isolates_owner_shape_and_orders_newest_first(
    read_api_env: dict[str, Any],
) -> None:
    """GET /sessions isolates owner, returns summary fields only, newest first."""
    client: TestClient = read_api_env["client"]
    factory = read_api_env["factory"]
    owner_id: str = read_api_env["owner_id"]
    headers = read_api_env["headers"]

    s_oldest = "s_01ABCDEFGHJKMNPTVW00000001"
    s_tie_low = "s_01ABCDEFGHJKMNPTVW00000002"
    s_tie_high = "s_01ABCDEFGHJKMNPTVW00000003"
    s_subsec = "s_01ABCDEFGHJKMNPTVW00000004"
    s_newest = "s_01ABCDEFGHJKMNPTVW00000005"
    s_other_new = "s_01ABCDEFGHJKMNPTVW99999999"

    with factory() as db:
        # Remove seeded active session to test exact custom ordering set cleanly
        existing_owned = db.get(SessionModel, SESSION_ACTIVE)
        if existing_owned is not None:
            existing_owned.created_at = "2026-11-27T01:05:00Z"
        db.add_all(
            [
                SessionModel(
                    session_id=s_oldest,
                    state="ENDED",
                    profile="general",
                    owner_id=owner_id,
                    container_id="sess_old_container",
                    container_ip="172.30.0.3",
                    receipt_url="https://overwatch.example.com/receipt/old",
                    created_at="2026-11-27T00:10:00Z",
                    updated_at="2026-11-27T00:20:00Z",
                ),
                SessionModel(
                    session_id=s_tie_low,
                    state="PAUSED",
                    profile="coding",
                    owner_id=owner_id,
                    created_at="2026-11-27T01:00:00Z",
                    updated_at="2026-11-27T01:00:00Z",
                ),
                SessionModel(
                    session_id=s_tie_high,
                    state="ACTIVE",
                    profile="coding",
                    owner_id=owner_id,
                    created_at="2026-11-27T01:00:00Z",
                    updated_at="2026-11-27T01:00:00Z",
                ),
                SessionModel(
                    session_id=s_subsec,
                    state="STARTING",
                    profile="coding",
                    owner_id=owner_id,
                    created_at="2026-11-27T01:00:00.500000Z",
                    updated_at="2026-11-27T01:00:00.500000Z",
                ),
                SessionModel(
                    session_id=s_newest,
                    state="FAILED",
                    profile="general",
                    owner_id=owner_id,
                    created_at="2026-11-27T02:00:00Z",
                    updated_at="2026-11-27T02:01:00Z",
                ),
                SessionModel(
                    session_id=s_other_new,
                    state="ACTIVE",
                    profile="coding",
                    owner_id="owner_someone_else",
                    created_at="2026-11-27T03:00:00Z",
                    updated_at="2026-11-27T03:00:00Z",
                ),
            ]
        )
        db.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert list(payload.keys()) == ["sessions"]

    sessions = payload["sessions"]
    assert [s["session_id"] for s in sessions] == [
        s_newest,
        SESSION_ACTIVE,
        s_subsec,
        s_tie_high,
        s_tie_low,
        s_oldest,
    ]
    assert SESSION_OTHER not in {s["session_id"] for s in sessions}
    assert s_other_new not in {s["session_id"] for s in sessions}

    for item in sessions:
        assert set(item.keys()) == {"session_id", "state", "profile", "created_at"}
        assert "container_id" not in item
        assert "container_ip" not in item
        assert "receipt_url" not in item
        assert "owner_id" not in item
        assert "stream_url" not in item
        assert "view_url" not in item
        assert "request_text" not in item


def test_screenshot_route_auth_ownership_inactive_and_jpeg_output(
    read_api_env: dict[str, Any],
) -> None:
    client: TestClient = read_api_env["client"]
    headers = read_api_env["headers"]
    factory = read_api_env["factory"]

    class _FakeRuntime:
        def __init__(self, payload: bytes | Exception) -> None:
            self._payload = payload

        def capture_screenshot_jpeg(self) -> bytes:
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    class _FakeRunner:
        def __init__(self, runtime: _FakeRuntime | None) -> None:
            self._runtime = runtime

        def get_runtime(self, session_id: str) -> _FakeRuntime | None:
            if session_id == SESSION_ACTIVE:
                return self._runtime
            return None

    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\xff\xd9"
    holder: dict[str, Any] = {"runner": _FakeRunner(_FakeRuntime(fake_jpeg))}
    app.dependency_overrides[get_session_runner] = lambda: holder["runner"]

    # Unauthenticated -> 401
    unauth = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}/screenshot")
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == ErrorCode.AUTH_ERROR.value

    # Missing and non-owned -> identical 404
    missing = client.get(
        f"/api/v1/sessions/{SESSION_MISSING}/screenshot", headers=headers
    )
    non_owned = client.get(
        f"/api/v1/sessions/{SESSION_OTHER}/screenshot", headers=headers
    )
    assert missing.status_code == 404
    assert non_owned.status_code == 404
    assert missing.json() == non_owned.json()

    # Valid JPEG response
    ok = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}/screenshot", headers=headers)
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/jpeg")
    assert ok.headers.get("cache-control") == "no-store"
    assert ok.content == fake_jpeg

    # Runtime failure -> 502 UPSTREAM_ERROR
    holder["runner"] = _FakeRunner(_FakeRuntime(McpTransportError("scrot failed")))
    err = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}/screenshot", headers=headers)
    assert err.status_code == 502
    assert err.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR.value

    # Missing runtime -> 409 SESSION_INACTIVE
    holder["runner"] = _FakeRunner(None)
    no_rt = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}/screenshot", headers=headers)
    assert no_rt.status_code == 409
    assert no_rt.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE.value

    # Ended session -> 409 SESSION_INACTIVE
    with factory() as db:
        row = db.get(SessionModel, SESSION_ACTIVE)
        assert row is not None
        row.state = "ENDED"
        db.commit()
    holder["runner"] = _FakeRunner(_FakeRuntime(fake_jpeg))
    ended = client.get(f"/api/v1/sessions/{SESSION_ACTIVE}/screenshot", headers=headers)
    assert ended.status_code == 409
    assert ended.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE.value

"""Unit tests for F-18 Calendar + Gmail connectors, vault, OAuth, and revocation."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from control_plane.api.deps import (
    derive_owner_id,
    get_google_connector_client,
    get_google_oauth_service,
    get_google_vault,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.connectors.google_oauth import (
    DRAFT_ONLY_SCOPES,
    GoogleOAuthService,
    OAuthFlowError,
)
from control_plane.connectors.google_tools import GoogleConnectorClient
from control_plane.contracts import ErrorCode, ToolCall
from control_plane.db.models import AuditEntryModel, ConnectionModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from control_plane.vault.google_vault import (
    GoogleConnectorVault,
    VaultAuthError,
    VaultCipher,
    VaultConfigError,
)
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TOKEN = "test-f18-app-token"
TEST_VAULT_KEY = base64.b64encode(b"v" * 32).decode("ascii")
SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 27, 21, 35, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_vault_encrypts_secrets_and_fails_closed_without_vault_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VAULT_KEY", raising=False)
    with pytest.raises(VaultConfigError):
        VaultCipher().encrypt_json({"access_token": "tok"})

    db_path = tmp_path / "vault.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        vault = GoogleConnectorVault(
            factory, cipher=VaultCipher(TEST_VAULT_KEY), clock=_FakeClock()
        )
        summary = vault.store_tokens(
            owner_id="owner_1",
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
            scopes=DRAFT_ONLY_SCOPES,
            token_mode="linked",
        )
        assert summary.status == "active"

        with factory() as db:
            row = db.scalar(select(ConnectionModel))
        assert row is not None
        assert "secret-access-token" not in row.secret_enc
        assert "secret-refresh-token" not in row.secret_enc

        tokens = vault.get_tokens(owner_id="owner_1")
        assert tokens.access_token == "secret-access-token"
        assert tokens.refresh_token == "secret-refresh-token"
        assert "secret-access-token" not in repr(tokens)
    finally:
        engine.dispose()


def test_daily_routine_profile_allows_drafts_and_blocks_agent_gmail_send() -> None:
    profile = load_profile("daily_routine")
    policy = load_policy()
    pipeline = GatewayPreflightPipeline(policy)

    read_cal = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c1",
            tool="calendar.list_events",
            args={"max_results": 5},
        ),
        profile,
    )
    assert read_cal.decision == "allow"

    draft_mail = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c2",
            tool="gmail.create_draft",
            args={"to": "user@example.com", "subject": "Hello", "body": "Draft body"},
        ),
        profile,
    )
    assert draft_mail.decision == "allow"

    agent_send = pipeline.evaluate(
        ToolCall(
            session_id=SESSION_ID,
            call_id="c3",
            tool="gmail.send",
            args={"draft_id": "draft_123"},
        ),
        profile,
    )
    assert agent_send.decision == "block"
    assert agent_send.error is not None
    assert agent_send.error.code == ErrorCode.POLICY_BLOCK


def test_oauth_flow_user_send_and_disconnect_revocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("VAULT_KEY", TEST_VAULT_KEY)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/api/v1/connections/google/callback",
    )

    db_path = tmp_path / "f18_api.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    owner_id = derive_owner_id(TEST_TOKEN)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="daily_routine",
                owner_id=owner_id,
                created_at="2026-11-27T21:35:00Z",
                updated_at="2026-11-27T21:35:00Z",
            )
        )
        db.commit()

    revoked_tokens: list[str] = []
    sent_drafts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(
                200,
                json={
                    "access_token": "mock-access-tok",
                    "refresh_token": "mock-refresh-tok",
                    "scope": " ".join(DRAFT_ONLY_SCOPES),
                },
            )
        if "oauth2.googleapis.com/revoke" in url:
            revoked_tokens.append(request.content.decode("utf-8"))
            return httpx.Response(200, json={})
        if "drafts/send" in url:
            payload = json.loads(request.content.decode("utf-8"))
            sent_drafts.append(payload["id"])
            return httpx.Response(200, json={"id": "msg_sent_1", "labelIds": ["SENT"]})
        if "calendar/v3/calendars/primary/events" in url:
            return httpx.Response(200, json={"items": [{"id": "evt_1"}]})
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    vault = GoogleConnectorVault(factory, clock=_FakeClock())
    oauth = GoogleOAuthService(vault=vault, http_client=mock_http)
    conn_client = GoogleConnectorClient(vault=vault, http_client=mock_http)

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_google_vault] = lambda: vault
    app.dependency_overrides[get_google_oauth_service] = lambda: oauth
    app.dependency_overrides[get_google_connector_client] = lambda: conn_client
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}

        start_resp = client.post("/api/v1/connections/google/start", headers=headers)
        assert start_resp.status_code == 200
        state = start_resp.json()["state"]
        assert "gmail.send" not in start_resp.json()["auth_url"]

        cb_resp = client.post(
            "/api/v1/connections/google/callback",
            headers=headers,
            json={"code": "auth-code-123", "state": state},
        )
        assert cb_resp.status_code == 200
        assert cb_resp.json()["status"] == "active"

        list_resp = client.get("/api/v1/connections", headers=headers)
        assert list_resp.status_code == 200
        assert "mock-access-tok" not in list_resp.text

        # Gateway-validated tool execution works while connected
        pipeline = GatewayPreflightPipeline(load_policy())
        preflight = pipeline.evaluate(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c1",
                tool="calendar.list_events",
                args={},
            ),
            load_profile("daily_routine"),
        )
        assert preflight.validated_args is not None
        cal_res = conn_client.execute_tool(
            owner_id=owner_id, validated=preflight.validated_args
        )
        assert cal_res == {"items": [{"id": "evt_1"}]}

        # User-originated send works
        send_resp = client.post(
            f"/api/v1/sessions/{SESSION_ID}/drafts/draft_99/send",
            headers=headers,
        )
        assert send_resp.status_code == 200
        assert sent_drafts == ["draft_99"]

        with factory() as db:
            audits = db.scalars(select(AuditEntryModel)).all()
        assert len(audits) == 1
        assert audits[0].tool == "gmail.send"

        # Disconnect revokes grant and blocks subsequent connector calls immediately
        disc_resp = client.delete("/api/v1/connections/google", headers=headers)
        assert disc_resp.status_code == 200
        assert len(revoked_tokens) == 1

        send_after = client.post(
            f"/api/v1/sessions/{SESSION_ID}/drafts/draft_99/send",
            headers=headers,
        )
        assert send_after.status_code == 401
        assert send_after.json()["error"]["code"] == ErrorCode.AUTH_ERROR
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_teardown_revokes_session_scoped_tokens_and_preserves_linked(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "teardown.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        vault = GoogleConnectorVault(
            factory, cipher=VaultCipher(TEST_VAULT_KEY), clock=_FakeClock()
        )
        vault.store_tokens(
            owner_id="owner_sess",
            access_token="sess-tok",
            token_mode="session",
            session_id=SESSION_ID,
        )
        vault.store_tokens(
            owner_id="owner_linked",
            access_token="linked-tok",
            refresh_token="linked-refresh",
            token_mode="linked",
        )

        assert (
            vault.teardown_session_tokens(owner_id="owner_sess", session_id=SESSION_ID)
            == 1
        )
        with pytest.raises(VaultAuthError):
            vault.get_tokens(owner_id="owner_sess")

        assert (
            vault.teardown_session_tokens(
                owner_id="owner_linked", session_id=SESSION_ID
            )
            == 0
        )
        assert (
            vault.get_tokens(owner_id="owner_linked").refresh_token == "linked-refresh"
        )
    finally:
        engine.dispose()


def test_alembic_migration_0006_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_conn.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "connections" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(cfg, "0005_add_undo_records_table")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "connections" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_oauth_state_ttl_and_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/callback")
    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode("ascii"))

    engine = create_db_engine(f"sqlite:///{tmp_path / 'oauth_ttl.db'}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    try:
        vault = GoogleConnectorVault(session_factory)
        service = GoogleOAuthService(
            vault=vault,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200,
                        json={"access_token": "acc", "scope": "compose"},
                    )
                )
            ),
            state_ttl_s=0.05,
        )

        # 1. State expiration
        started = service.start_oauth(owner_id="owner_1")
        state = started["state"]
        time.sleep(0.08)
        with pytest.raises(OAuthFlowError, match="expired"):
            service.complete_oauth(owner_id="owner_1", code="code-1", state=state)

        # 2. Replay prevention
        started_fresh = service.start_oauth(owner_id="owner_1")
        state_fresh = started_fresh["state"]
        service.complete_oauth(owner_id="owner_1", code="code-1", state=state_fresh)
        with pytest.raises(OAuthFlowError, match="Invalid or expired"):
            service.complete_oauth(owner_id="owner_1", code="code-2", state=state_fresh)
    finally:
        engine.dispose()


def test_oauth_redirect_uri_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/callback")
    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode("ascii"))

    engine = create_db_engine(f"sqlite:///{tmp_path / 'oauth_redirect.db'}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    try:
        vault = GoogleConnectorVault(session_factory)
        service = GoogleOAuthService(
            vault=vault,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200,
                        json={"access_token": "acc", "scope": "compose"},
                    )
                )
            ),
        )

        # Mismatched redirect_uri on start
        with pytest.raises(OAuthFlowError, match="Invalid redirect_uri"):
            service.start_oauth(
                owner_id="owner_1", redirect_uri="http://attacker.com/evil"
            )

        # Matching redirect_uri on start
        started = service.start_oauth(
            owner_id="owner_1", redirect_uri="http://localhost:8000/callback"
        )
        state = started["state"]

        # Mismatched redirect_uri on complete
        with pytest.raises(OAuthFlowError, match="Invalid redirect_uri"):
            service.complete_oauth(
                owner_id="owner_1",
                code="code-1",
                state=state,
                redirect_uri="http://attacker.com/evil",
            )
    finally:
        engine.dispose()


def test_migration_downgrade_retention_guards(tmp_path: Path) -> None:
    db_path = tmp_path / "retention_guards.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as db:
            db.execute(
                sa.text(
                    "INSERT INTO sessions (session_id, state, profile, owner_id, "
                    "created_at, updated_at) VALUES ('s_1', 'ACTIVE', 'general', "
                    "'owner_1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            )
            db.execute(
                sa.text(
                    "INSERT INTO plans (plan_id, session_id, summary, profile, "
                    "steps_json, scopes_json, plan_hash, status, created_at, "
                    "updated_at) VALUES ('p_1', 's_1', 'test plan', 'general', "
                    "'[]', '[]', 'hash123', 'pending', "
                    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            )
            db.commit()

        # Attempting downgrade of 0004 with populated plans table must fail
        with pytest.raises(RuntimeError, match="Cannot downgrade migration 0004"):
            command.downgrade(cfg, "0003_add_device_push_tokens")
    finally:
        engine.dispose()

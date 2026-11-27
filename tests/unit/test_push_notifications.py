"""Unit tests for F-06 push-token registration, encryption, and notifications."""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from alembic.config import Config
from control_plane.agent.loop import AgentLoopResult
from control_plane.api.deps import (
    derive_owner_id,
    get_push_notification_service,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.contracts import Policy
from control_plane.db.models import ApprovalModel, DevicePushTokenModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.pipeline import PendingApprovalData
from control_plane.gateway.policy_loader import load_policy
from control_plane.notifications.push import (
    ExpoPushSender,
    PushDeliveryResult,
    PushMessage,
    PushNotificationService,
    PushTokenCipher,
    PushTokenConfigError,
    PushTokenStore,
)
from control_plane.sessions.profile_loader import load_profile
from control_plane.sessions.session_runner import SessionRunner, StartedSession
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_APP_TOKEN = "test-push-app-token"
TEST_PUSH_KEY = base64.b64encode(b"p" * 32).decode("ascii")
OTHER_PUSH_KEY = base64.b64encode(b"q" * 32).decode("ascii")
SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"


class _FakeClock:
    def __init__(self, initial: datetime | None = None) -> None:
        self.now = initial or datetime(2026, 11, 27, 19, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _RecordingSender:
    def __init__(
        self,
        *,
        invalid_tokens: tuple[str, ...] = (),
        raise_exc: Exception | None = None,
    ) -> None:
        self.batches: list[list[PushMessage]] = []
        self.invalid_tokens = invalid_tokens
        self.raise_exc = raise_exc

    def send(self, messages):  # type: ignore[no-untyped-def]
        batch = list(messages)
        self.batches.append(batch)
        if self.raise_exc is not None:
            raise self.raise_exc
        invalid = tuple(m.to for m in batch if m.to in self.invalid_tokens)
        sent = len(batch) - len(invalid)
        return PushDeliveryResult(
            sent_count=sent,
            invalid_tokens=invalid,
            failed_count=len(invalid),
        )


@pytest.fixture()
def push_db(tmp_path: Path):
    db_path = tmp_path / "push.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def push_api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_TOKEN", TEST_APP_TOKEN)
    monkeypatch.setenv("PUSH_TOKEN_KEY", TEST_PUSH_KEY)
    db_path = tmp_path / "push_api.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    clock = _FakeClock()
    sender = _RecordingSender()
    store = PushTokenStore(factory, cipher=PushTokenCipher(), clock=clock)
    service = PushNotificationService(store=store, sender=sender)

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_push_notification_service] = lambda: service
    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "store": store,
            "sender": sender,
            "service": service,
            "clock": clock,
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_register_push_token_requires_auth(push_api_env) -> None:
    client: TestClient = push_api_env["client"]
    resp = client.post(
        "/api/v1/devices/push-token",
        json={"token": "ExponentPushToken[abc123]"},
    )
    assert resp.status_code == 401

    bad = client.post(
        "/api/v1/devices/push-token",
        headers={"Authorization": "Bearer wrong-token"},
        json={"token": "ExponentPushToken[abc123]"},
    )
    assert bad.status_code == 401


def test_register_push_token_stores_encrypted_and_scoped_to_owner(
    push_api_env,
) -> None:
    client: TestClient = push_api_env["client"]
    factory = push_api_env["factory"]
    raw_token = "ExponentPushToken[secret-device-token-123]"

    resp = client.post(
        "/api/v1/devices/push-token",
        headers={"Authorization": f"Bearer {TEST_APP_TOKEN}"},
        json={"token": raw_token},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    expected_owner = derive_owner_id(TEST_APP_TOKEN)
    with factory() as db:
        rows = db.scalars(select(DevicePushTokenModel)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.owner_id == expected_owner
    assert raw_token not in row.token_enc
    assert raw_token not in row.token_hash
    assert row.revoked_at is None
    assert PushTokenCipher(TEST_PUSH_KEY).decrypt(row.token_enc) == raw_token


def test_register_push_token_fails_closed_without_push_token_key(
    push_api_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUSH_TOKEN_KEY", raising=False)
    monkeypatch.setenv("VAULT_KEY", TEST_PUSH_KEY)  # Must NOT fall back to VAULT_KEY
    client: TestClient = push_api_env["client"]
    factory = push_api_env["factory"]

    resp = client.post(
        "/api/v1/devices/push-token",
        headers={"Authorization": f"Bearer {TEST_APP_TOKEN}"},
        json={"token": "ExponentPushToken[no-key]"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "UPSTREAM_ERROR"
    assert "no-key" not in resp.text

    with factory() as db:
        assert db.scalars(select(DevicePushTokenModel)).all() == []


def test_push_token_cipher_rejects_invalid_keys_and_vault_key_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUSH_TOKEN_KEY", raising=False)
    monkeypatch.setenv("VAULT_KEY", TEST_PUSH_KEY)

    with pytest.raises(PushTokenConfigError):
        PushTokenCipher().encrypt("ExponentPushToken[x]")

    with pytest.raises(PushTokenConfigError):
        PushTokenCipher("not-valid-base64!!!").encrypt("ExponentPushToken[x]")

    short_key = base64.b64encode(b"short").decode("ascii")
    with pytest.raises(PushTokenConfigError):
        PushTokenCipher(short_key).encrypt("ExponentPushToken[x]")

    enc = PushTokenCipher(TEST_PUSH_KEY).encrypt("ExponentPushToken[x]")
    with pytest.raises(PushTokenConfigError):
        PushTokenCipher(OTHER_PUSH_KEY).decrypt(enc)


def test_register_push_token_rejects_invalid_payloads(push_api_env) -> None:
    client: TestClient = push_api_env["client"]
    headers = {"Authorization": f"Bearer {TEST_APP_TOKEN}"}

    for bad_token in ("", "  ", "tok\nwith\nnewline", "tok with space"):
        resp = client.post(
            "/api/v1/devices/push-token",
            headers=headers,
            json={"token": bad_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "ARG_INVALID"


def test_token_replacement_and_revocation(push_db) -> None:
    clock = _FakeClock()
    store = PushTokenStore(
        push_db,
        cipher=PushTokenCipher(TEST_PUSH_KEY),
        clock=clock,
    )
    tok = "ExponentPushToken[replaceable]"

    tid1 = store.register_token(owner_id="owner_a", token=tok)
    assert store.list_active_tokens("owner_a") == [tok]

    assert store.revoke_token(owner_id="owner_a", token=tok) is True
    assert store.list_active_tokens("owner_a") == []

    # Re-registering the same token safely reactivates/replaces without duplicates.
    tid2 = store.register_token(owner_id="owner_b", token=tok)
    assert tid2 == tid1
    assert store.list_active_tokens("owner_a") == []
    assert store.list_active_tokens("owner_b") == [tok]


def test_expo_sender_reports_device_not_registered_and_service_purges_token(
    push_db,
) -> None:
    valid_tok = "ExponentPushToken[valid]"
    dead_tok = "ExponentPushToken[dead]"
    store = PushTokenStore(push_db, cipher=PushTokenCipher(TEST_PUSH_KEY))
    store.register_token(owner_id="owner_1", token=valid_tok)
    store.register_token(owner_id="owner_1", token=dead_tok)

    captured_payloads: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        tickets = []
        for item in body:
            if item["to"] == dead_tok:
                tickets.append(
                    {
                        "status": "error",
                        "message": "not registered",
                        "details": {"error": "DeviceNotRegistered"},
                    }
                )
            else:
                tickets.append({"status": "ok", "id": "ticket-1"})
        return httpx.Response(200, json={"data": tickets})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = ExpoPushSender(timeout_s=2.0, http_client=http_client)
    service = PushNotificationService(store=store, sender=sender)

    result = service.notify_session_completed(
        owner_id="owner_1",
        session_id=SESSION_ID,
    )
    assert result.sent_count == 1
    assert result.invalid_tokens == (dead_tok,)
    assert len(captured_payloads) == 1
    assert store.list_active_tokens("owner_1") == [valid_tok]


def test_push_tokens_are_never_logged_or_included_in_repr(
    push_db,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_tok = "ExponentPushToken[ultra-secret-token-xyz]"
    store = PushTokenStore(push_db, cipher=PushTokenCipher(TEST_PUSH_KEY))
    store.register_token(owner_id="owner_1", token=secret_tok)

    msg = PushMessage(
        to=secret_tok,
        title="Approval required",
        body="files.upload (sensitive) requires your approval.",
        data={"type": "approval_request", "session_id": SESSION_ID},
    )
    assert secret_tok not in repr(msg)

    failing_sender = _RecordingSender(raise_exc=httpx.ReadTimeout("timed out"))
    service = PushNotificationService(store=store, sender=failing_sender)
    with caplog.at_level(logging.WARNING):
        res = service.notify_session_completed(
            owner_id="owner_1",
            session_id=SESSION_ID,
        )
    assert res.failed_count == 1
    assert secret_tok not in caplog.text

    # Also test decryption failure logging redaction
    bad_store = PushTokenStore(push_db, cipher=PushTokenCipher(OTHER_PUSH_KEY))
    with caplog.at_level(logging.WARNING):
        assert bad_store.list_active_tokens("owner_1") == []
    assert secret_tok not in caplog.text


def test_approval_notifications_best_effort_and_not_sent_on_denial_or_timeout(
    push_db,
) -> None:
    clock = _FakeClock()
    with push_db() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_1",
                created_at="2026-11-27T19:00:00Z",
                updated_at="2026-11-27T19:00:00Z",
            )
        )
        db.commit()

    store = PushTokenStore(push_db, cipher=PushTokenCipher(TEST_PUSH_KEY), clock=clock)
    tok = "ExponentPushToken[owner-1-device]"
    store.register_token(owner_id="owner_1", token=tok)
    store.register_token(owner_id="owner_other", token="ExponentPushToken[other]")

    failing_sender = _RecordingSender(raise_exc=RuntimeError("push gateway down"))
    service = PushNotificationService(store=store, sender=failing_sender)
    bus = EventBus(push_db)
    manager = ApprovalManager(
        session_factory=push_db,
        event_bus=bus,
        clock=clock,
        push_service=service,
    )
    policy: Policy = load_policy()
    pending = PendingApprovalData(
        session_id=SESSION_ID,
        call_id="c_01",
        tool="files.upload",
        tier="sensitive",
        args_redacted={"path": "/workspace/report.txt"},
        reason="external upload",
        irreversible=True,
    )

    # Even though sender raises, approval + event persistence still succeed.
    created = manager.create_approval(pending, policy=policy)
    assert created.approval.approval_id.startswith("a_")
    assert len(failing_sender.batches) == 1
    assert [m.to for m in failing_sender.batches[0]] == [tok]
    assert failing_sender.batches[0][0].data["type"] == "approval_request"
    assert tok not in str(created.event.model_dump())

    with push_db() as db:
        assert db.get(ApprovalModel, created.approval.approval_id) is not None

    # Denial must NOT send a push notification.
    from control_plane.contracts import ApprovalResponse

    denied = manager.resolve_approval(
        ApprovalResponse(approval_id=created.approval.approval_id, approve=False)
    )
    assert denied.status == "denied"
    assert len(failing_sender.batches) == 1

    # Timeout must NOT send a push notification.
    manager.create_approval(pending, policy=policy)
    assert len(failing_sender.batches) == 2
    clock.now = clock.now.replace(year=2027)
    expired = manager.expire_due(session_id=SESSION_ID)
    assert len(expired) == 1
    assert expired[0].status == "timed_out"
    assert len(failing_sender.batches) == 2


def test_session_runner_notifies_on_completion_and_ignores_push_failure(
    push_db,
) -> None:
    with push_db() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_1",
                created_at="2026-11-27T19:00:00Z",
                updated_at="2026-11-27T19:00:00Z",
            )
        )
        db.commit()

    store = PushTokenStore(push_db, cipher=PushTokenCipher(TEST_PUSH_KEY))
    tok = "ExponentPushToken[session-owner]"
    store.register_token(owner_id="owner_1", token=tok)
    sender = _RecordingSender(raise_exc=RuntimeError("network unreachable"))
    service = PushNotificationService(store=store, sender=sender)

    runner = SessionRunner(
        session_factory=push_db,
        container_manager=MagicMock(),
        event_bus=EventBus(push_db),
        approval_manager=MagicMock(),
        profile_loader=MagicMock(),
        policy_loader=MagicMock(),
        push_service=service,
    )
    started = StartedSession(
        session_id=SESSION_ID,
        state="ACTIVE",
        profile=load_profile("coding"),
        policy=load_policy(),
        container_info=MagicMock(),
        created_at="2026-11-27T19:00:00Z",
        llm=MagicMock(),
    )

    completed_rec = runner._finalize_loop_result(
        session_id=SESSION_ID,
        started=started,
        runtime=MagicMock(),
        result=AgentLoopResult(
            session_id=SESSION_ID,
            status="completed",
            snapshot=MagicMock(),
        ),
    )
    assert completed_rec.status == "completed"
    assert len(sender.batches) == 1
    assert sender.batches[0][0].to == tok
    assert sender.batches[0][0].data == {
        "type": "session_completed",
        "session_id": SESSION_ID,
    }

    # Blocked/error outcomes must NOT trigger session_completed push.
    blocked_rec = runner._finalize_loop_result(
        session_id=SESSION_ID,
        started=started,
        runtime=MagicMock(),
        result=AgentLoopResult(session_id=SESSION_ID, status="blocked"),
    )
    assert blocked_rec.status == "blocked"
    assert len(sender.batches) == 1
    runner.shutdown(wait=False)


def test_alembic_migration_0003_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_push.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "device_push_tokens" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(cfg, "0002_add_asked_audit_decision")
    engine = create_db_engine(f"sqlite:///{db_path}")
    try:
        assert "device_push_tokens" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

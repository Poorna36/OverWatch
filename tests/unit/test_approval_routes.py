"""Unit tests for Phase 4 approval REST routes (control_plane.api.approvals)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from control_plane.api.deps import (
    derive_owner_id,
    get_approval_manager,
    get_session_factory,
    get_session_runner,
)
from control_plane.api.main import app
from control_plane.audit import audit_writer
from control_plane.audit.audit_writer import AuditWriterError
from control_plane.contracts import ErrorCode, Policy
from control_plane.db.models import ApprovalModel, EventModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.pipeline import PendingApprovalData
from fastapi.testclient import TestClient
from sqlalchemy import select

TEST_TOKEN = "test-demo-app-token"
SESSION_OWNED = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_OTHER = "s_01ABCDEFGHJKMNPTVW98765432"
SESSION_MISSING = "s_01ABCDEFGHJKMNPTVW00000000"
UNKNOWN_APPROVAL_ID = "a_01ABCDEFGHJKMNPTVW99999999"


class _FakeClock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated temp SQLite DB, ApprovalManager, and TestClient."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "api_approvals.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _FakeClock(datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC))
    manager = ApprovalManager(session_factory=factory, event_bus=bus, clock=clock)

    owner_id = derive_owner_id(TEST_TOKEN)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_OWNED,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                dry_run=0,
                created_at="2026-11-26T12:00:00Z",
                updated_at="2026-11-26T12:00:00Z",
            )
        )
        db.add(
            SessionModel(
                session_id=SESSION_OTHER,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_someone_else",
                dry_run=0,
                created_at="2026-11-26T12:00:00Z",
                updated_at="2026-11-26T12:00:00Z",
            )
        )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_approval_manager] = lambda: manager

    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "manager": manager,
            "bus": bus,
            "clock": clock,
            "headers": {"Authorization": f"Bearer {TEST_TOKEN}"},
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create_pending(
    manager: ApprovalManager,
    *,
    session_id: str = SESSION_OWNED,
    approval_id: str | None = None,
    timeout_s: int = 120,
    now: datetime | None = None,
) -> str:
    policy = Policy.model_validate(
        {"version": 1, "defaults": {"approval_timeout_s": timeout_s}}
    )
    res = manager.create_approval(
        PendingApprovalData(
            session_id=session_id,
            call_id="call_1",
            tool="files.delete",
            tier="sensitive",
            args_redacted={"path": "secret.txt"},
            reason="sensitive action",
            irreversible=True,
        ),
        policy=policy,
        approval_id=approval_id,
        now=now,
    )
    return res.approval.approval_id


def test_health_endpoints_remain_public_when_app_token_unset(
    api_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health checks stay public even when APP_TOKEN is unset."""
    monkeypatch.delenv("APP_TOKEN", raising=False)
    client: TestClient = api_env["client"]

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_missing_wrong_and_unset_bearer_token_rejected(
    api_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both endpoints require a valid Authorization: Bearer <APP_TOKEN> header."""
    client: TestClient = api_env["client"]
    aid = _create_pending(api_env["manager"])

    for headers in [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer wrong-token"},
    ]:
        r_get = client.get(
            f"/api/v1/sessions/{SESSION_OWNED}/approvals", headers=headers
        )
        assert r_get.status_code == 401
        assert r_get.json()["error"]["code"] == ErrorCode.AUTH_ERROR

        r_post = client.post(
            f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
            json={"approval_id": aid, "approve": True},
            headers=headers,
        )
        assert r_post.status_code == 401
        assert r_post.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    # Unset APP_TOKEN must also reject
    monkeypatch.delenv("APP_TOKEN", raising=False)
    r_unset = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals",
        headers=api_env["headers"],
    )
    assert r_unset.status_code == 401
    assert r_unset.json()["error"]["code"] == ErrorCode.AUTH_ERROR


def test_non_owned_and_missing_session_return_identical_404(api_env) -> None:
    """Non-owned sessions fail with the same 404 shape as nonexistent sessions."""
    client: TestClient = api_env["client"]
    headers = api_env["headers"]
    other_aid = _create_pending(api_env["manager"], session_id=SESSION_OTHER)

    r_other = client.get(f"/api/v1/sessions/{SESSION_OTHER}/approvals", headers=headers)
    r_missing = client.get(
        f"/api/v1/sessions/{SESSION_MISSING}/approvals", headers=headers
    )
    assert r_other.status_code == 404
    assert r_missing.status_code == 404
    assert r_other.json() == r_missing.json()
    assert r_other.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

    r_post_other = client.post(
        f"/api/v1/sessions/{SESSION_OTHER}/approvals/{other_aid}",
        json={"approval_id": other_aid, "approve": True},
        headers=headers,
    )
    assert r_post_other.status_code == 404
    assert r_post_other.json() == r_missing.json()


def test_list_approvals_pending_filtering_and_due_expiration(api_env) -> None:
    """GET expires due approvals first and filters by pending query parameter."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    clock: _FakeClock = api_env["clock"]
    headers = api_env["headers"]

    aid_due = _create_pending(
        manager,
        approval_id="a_01ABCDEFGHJKMNPTVW00000001",
        timeout_s=10,
    )
    aid_active = _create_pending(
        manager,
        approval_id="a_01ABCDEFGHJKMNPTVW00000002",
        timeout_s=120,
    )

    # Advance clock past aid_due's 10s expiry, before aid_active's 120s expiry
    clock.now = clock.now + timedelta(seconds=30)

    r_pending = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals?pending=true",
        headers=headers,
    )
    assert r_pending.status_code == 200
    pending_list = r_pending.json()["approvals"]
    assert [item["approval_id"] for item in pending_list] == [aid_active]

    r_all = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals?pending=false",
        headers=headers,
    )
    assert r_all.status_code == 200
    all_list = r_all.json()["approvals"]
    assert [item["approval_id"] for item in all_list] == [aid_due, aid_active]


def test_post_approval_approve_and_deny(api_env) -> None:
    """POST approves or denies pending approvals and returns {ok: true}."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    bus: EventBus = api_env["bus"]
    headers = api_env["headers"]

    aid_ok = _create_pending(manager, approval_id="a_01ABCDEFGHJKMNPTVW00000003")
    aid_no = _create_pending(manager, approval_id="a_01ABCDEFGHJKMNPTVW00000004")

    r_approve = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid_ok}",
        json={"approval_id": aid_ok, "approve": True},
        headers=headers,
    )
    assert r_approve.status_code == 200
    assert r_approve.json() == {"ok": True}

    r_deny = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid_no}",
        json={"approval_id": aid_no, "approve": False},
        headers=headers,
    )
    assert r_deny.status_code == 200
    assert r_deny.json() == {"ok": True}

    events = bus.replay(session_id=SESSION_OWNED)
    resolved_events = [e for e in events if e.type == "approval_resolved"]
    assert len(resolved_events) == 2
    assert resolved_events[0].data == {
        "approval_id": aid_ok,
        "approved": True,
        "by": "user",
    }
    assert resolved_events[1].data == {
        "approval_id": aid_no,
        "approved": False,
        "by": "user",
    }


def test_post_path_body_mismatch_unknown_and_cross_session_approval(api_env) -> None:
    """Mismatched body ID, unknown approval, and cross-session approval fail safely."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    headers = api_env["headers"]

    aid_owned = _create_pending(manager, session_id=SESSION_OWNED)
    aid_other = _create_pending(manager, session_id=SESSION_OTHER)

    r_mismatch = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid_owned}",
        json={"approval_id": UNKNOWN_APPROVAL_ID, "approve": True},
        headers=headers,
    )
    assert r_mismatch.status_code == 400
    assert r_mismatch.json()["error"]["code"] == ErrorCode.ARG_INVALID

    r_unknown = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{UNKNOWN_APPROVAL_ID}",
        json={"approval_id": UNKNOWN_APPROVAL_ID, "approve": True},
        headers=headers,
    )
    assert r_unknown.status_code == 404
    assert r_unknown.json()["error"]["code"] == ErrorCode.POLICY_BLOCK

    r_cross = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid_other}",
        json={"approval_id": aid_other, "approve": True},
        headers=headers,
    )
    assert r_cross.status_code == 404
    assert r_cross.json() == r_unknown.json()


def test_post_expired_approval_returns_approval_timeout(api_env) -> None:
    """Resolving an expired approval returns APPROVAL_TIMEOUT and times it out."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    clock: _FakeClock = api_env["clock"]
    headers = api_env["headers"]

    aid = _create_pending(manager, timeout_s=15)
    clock.now = clock.now + timedelta(seconds=20)

    r_exp = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True},
        headers=headers,
    )
    assert r_exp.status_code == 409
    assert r_exp.json()["error"]["code"] == ErrorCode.APPROVAL_TIMEOUT

    # Second attempt after already timed out also returns APPROVAL_TIMEOUT
    r_again = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True},
        headers=headers,
    )
    assert r_again.status_code == 409
    assert r_again.json()["error"]["code"] == ErrorCode.APPROVAL_TIMEOUT


def test_post_rejects_extra_edited_args_and_ended_session(api_env) -> None:
    """Contract rejects unmodeled edited_args and ended sessions fail closed."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    factory = api_env["factory"]
    headers = api_env["headers"]

    aid = _create_pending(manager)

    # Extra fields (such as uncontracted edited_args) fail schema validation (422)
    r_extra = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True, "edited_args": {"path": "x"}},
        headers=headers,
    )
    assert r_extra.status_code == 422

    # Terminal session state rejects approval resolution without resuming
    with factory() as db:
        row = db.get(SessionModel, SESSION_OWNED)
        assert row is not None
        row.state = "ENDED"
        db.commit()

    r_ended = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True},
        headers=headers,
    )
    assert r_ended.status_code == 409
    assert r_ended.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE


def test_audit_or_event_persistence_failure_blocks_resumption(
    api_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit or event failure during resolution prevents SessionRunner resumption."""
    client: TestClient = api_env["client"]
    manager: ApprovalManager = api_env["manager"]
    factory = api_env["factory"]
    headers = api_env["headers"]

    aid = _create_pending(manager)
    resumed_calls: list[Any] = []

    class _SpyRunner:
        def on_approval_resolved(self, resolution: Any) -> None:
            resumed_calls.append(resolution)

    app.dependency_overrides[get_session_runner] = lambda: _SpyRunner()

    # 1. Audit persistence failure rolls back approval and never resumes
    def _fail_audit(*_args: Any, **_kwargs: Any):
        raise AuditWriterError("simulated audit failure")

    monkeypatch.setattr(
        "control_plane.gateway.approval_manager.append_audit_entry",
        _fail_audit,
    )
    r_audit_fail = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True},
        headers=headers,
    )
    assert r_audit_fail.status_code == 500
    assert resumed_calls == []
    with factory() as db:
        row = db.get(ApprovalModel, aid)
        assert row is not None and row.status == "pending"

    # 2. EventBus publish failure after DB commit returns 500 and never resumes
    monkeypatch.setattr(
        "control_plane.gateway.approval_manager.append_audit_entry",
        audit_writer.append_audit_entry,
    )

    def _fail_publish(**_kwargs: Any):
        raise EventBusError("simulated event bus failure")

    monkeypatch.setattr(api_env["bus"], "publish", _fail_publish)
    r_event_fail = client.post(
        f"/api/v1/sessions/{SESSION_OWNED}/approvals/{aid}",
        json={"approval_id": aid, "approve": True},
        headers=headers,
    )
    assert r_event_fail.status_code == 500
    assert resumed_calls == []
    with factory() as db:
        events = db.scalars(
            select(EventModel).where(EventModel.session_id == SESSION_OWNED)
        ).all()
        assert [e.type for e in events] == ["approval_request"]
        assert json.loads(events[0].data)["approval_id"] == aid

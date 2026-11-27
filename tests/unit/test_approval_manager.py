"""Focused unit tests for Phase 4 approval lifecycle manager (F-06)."""

from __future__ import annotations

import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from control_plane.audit import audit_writer
from control_plane.audit.audit_writer import AuditWriter, AuditWriterError
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import ApprovalRequest, ApprovalResponse, Policy, ToolCall
from control_plane.db.models import (
    ApprovalModel,
    AuditEntryModel,
    EventModel,
    SessionModel,
)
from control_plane.db.session import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.approval_manager import (
    ApprovalAlreadyResolvedError,
    ApprovalEventPublishError,
    ApprovalManager,
    ApprovalManagerError,
    ApprovalNotFoundError,
    InvalidApprovalIdError,
    generate_approval_id,
    validate_approval_id,
)
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.pipeline import GatewayPreflightPipeline, PendingApprovalData
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"
BASE_TIME = datetime(2026, 11, 26, 22, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy()


@pytest.fixture(scope="module")
def coding_profile():
    return load_profile("coding")


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return ws


@pytest.fixture
def db_factory(
    tmp_path: Path,
) -> Generator[sessionmaker[Session], None, None]:
    """Isolated SQLite database with schema, triggers, and an ACTIVE session."""
    db_path = tmp_path / "approvals.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        s.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_test",
                created_at="2026-11-26T22:00:00Z",
                updated_at="2026-11-26T22:00:00Z",
            )
        )
        s.commit()
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def event_bus(db_factory: sessionmaker[Session]) -> EventBus:
    return EventBus(db_factory)


@pytest.fixture
def manager(db_factory: sessionmaker[Session], event_bus: EventBus) -> ApprovalManager:
    return ApprovalManager(db_factory, event_bus, clock=lambda: BASE_TIME)


def _sample_pending(
    *,
    args_redacted: dict[str, Any] | None = None,
    call_id: str = "call_1",
) -> PendingApprovalData:
    return PendingApprovalData(
        session_id=SESSION_ID,
        call_id=call_id,
        tool="files.upload",
        tier="sensitive",
        args_redacted=(
            {"path": "src/main.py", "destination": "https://example.com/upload"}
            if args_redacted is None
            else args_redacted
        ),
        reason="network egress / upload requires human approval",
        irreversible=True,
    )


# ---------------------------------------------------------------------------
# 1. ID Generation & Validation
# ---------------------------------------------------------------------------
def test_approval_id_generation_and_validation() -> None:
    generated = generate_approval_id(BASE_TIME)
    assert generated.startswith("a_")
    assert len(generated) == 28
    assert validate_approval_id(generated) == generated
    assert validate_approval_id(generated.lower()) == generated

    for bad in (
        "",
        "s_01J8ABCDEFGH1234567890ABCD",
        "p_01J8ABCDEFGH1234567890ABCD",
        "a_too_short",
        "a_01J8ABCDEFGH1234567890ABCDX",
        "a_81J8ABCDEFGH1234567890ABCD",  # first ULID char must be 0-7
        "a_01I8ABCDEFGH1234567890ABCD",  # 'I' invalid in Crockford base32
        "a_01L8ABCDEFGH1234567890ABCD",  # 'L' invalid in Crockford base32
        "a_../01J8ABCDEFGH1234567890",
        "a_01J8ABCDEFGH1234567890ABC\n",
        123,
    ):
        with pytest.raises(InvalidApprovalIdError):
            validate_approval_id(bad)


# ---------------------------------------------------------------------------
# 2. Approval Creation, Schema Adherence & Redaction
# ---------------------------------------------------------------------------
def test_create_approval_persists_card_and_emits_contract_event(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> None:
    """create_approval stores pending ApprovalModel and emits approval_request."""
    long_dest = "https://uploads.example.com/" + ("x" * 240)
    pending = PendingApprovalData(
        session_id=SESSION_ID,
        call_id="call_create_1",
        tool="files.upload",
        tier="sensitive",
        args_redacted={
            "path": "src/main.py",
            "destination": long_dest,
            "content": "SHOULD NOT LEAK",
            "api_key": "sk-secret-value",
        },
        reason="external upload requires approval",
        irreversible=True,
    )

    created = manager.create_approval(
        pending,
        policy=policy,
        warnings=["Uploaded artifact leaves sandbox"],
    )

    req = created.approval
    assert isinstance(req, ApprovalRequest)
    assert validate_approval_id(req.approval_id) == req.approval_id
    assert req.session_id == SESSION_ID
    assert req.call_id == "call_create_1"
    assert req.tool == "files.upload"
    assert req.tier == "sensitive"
    assert req.irreversible is True
    assert req.warnings == ["Uploaded artifact leaves sandbox"]
    assert "api_key" not in req.args_redacted
    assert req.args_redacted["content"] == "[REDACTED 15 bytes]"
    assert len(req.args_redacted["destination"]) == 200

    expected_exp = (
        (BASE_TIME + timedelta(seconds=policy.defaults.approval_timeout_s))
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert req.expires_at == expected_exp

    # Check DB persistence matches contract/model strictly
    with db_factory() as s:
        row = s.get(ApprovalModel, req.approval_id)
        assert row is not None
        assert row.status == "pending"
        assert row.resolved_by is None
        assert row.resolved_at is None
        assert row.irreversible == 1
        assert row.expires_at == expected_exp
        stored_args = json.loads(row.args_redacted)
        assert "api_key" not in stored_args
        assert stored_args["content"] == "[REDACTED 15 bytes]"
        assert "SHOULD NOT LEAK" not in row.args_redacted
        assert "sk-secret-value" not in row.args_redacted

    # EventBus payload matches CONTRACTS.md §9 ApprovalRequest
    assert created.event.type == "approval_request"
    assert created.event.data == req.model_dump()
    replayed = event_bus.replay(session_id=SESSION_ID)
    assert [e.type for e in replayed] == ["approval_request"]
    assert ApprovalRequest.model_validate(replayed[0].data) == req


def test_create_approval_preserves_existing_body_redaction_counts(
    policy: Policy,
    coding_profile,
    workspace_dir: Path,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> None:
    """Pipeline -> coordinator -> approval_manager preserves exact redacted counts."""
    long_dest = "https://uploads.example.com/" + ("z" * 240)
    coordinator = AuditedGatewayCoordinator(
        pipeline=GatewayPreflightPipeline(policy, workspace_root=workspace_dir),
        audit_writer=AuditWriter(db_factory),
    )
    coord_res = coordinator.handle_call(
        ToolCall(
            session_id=SESSION_ID,
            call_id="call_pipe_1",
            tool="files.upload",
            args={"path": "./src/main.py", "destination": long_dest},
        ),
        coding_profile,
        executor=lambda _v: pytest.fail("Executor must never run during ask"),
    )
    assert coord_res.preflight.pending_approval is not None

    mgr = ApprovalManager(db_factory, event_bus, clock=lambda: BASE_TIME)
    created = mgr.create_approval(coord_res.preflight.pending_approval, policy=policy)
    assert created.approval.args_redacted["path"] == "src/main.py"
    assert len(created.approval.args_redacted["destination"]) == 200


def test_create_approval_fails_closed_on_missing_session_or_bad_timeout(
    manager: ApprovalManager,
    policy: Policy,
) -> None:
    missing_session = PendingApprovalData(
        session_id="s_01J8ABCDEFGH1234567890ZZZZ",
        call_id="call_x",
        tool="files.upload",
        tier="sensitive",
        args_redacted={"path": "src/main.py"},
        reason="ask",
        irreversible=True,
    )
    with pytest.raises(ApprovalManagerError, match="does not exist"):
        manager.create_approval(missing_session, policy=policy)

    bad_policy = policy.model_copy(
        update={
            "defaults": policy.defaults.model_copy(update={"approval_timeout_s": 0})
        }
    )
    with pytest.raises(ApprovalManagerError, match="approval_timeout_s"):
        manager.create_approval(_sample_pending(), policy=bad_policy)


# ---------------------------------------------------------------------------
# 3. Approve, Deny, and Coordinator -> Approval Audit Chain Integration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("approve_flag", "expected_status", "expected_audit_decision"),
    [
        (True, "approved", "asked_approved"),
        (False, "denied", "asked_denied"),
    ],
)
def test_resolve_approval_approved_and_denied(
    policy: Policy,
    coding_profile,
    workspace_dir: Path,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
    approve_flag: bool,
    expected_status: str,
    expected_audit_decision: str,
) -> None:
    """Resolve before expiry updates DB, appends audit, emits event, no exec."""
    exec_calls = 0

    def executor(_v) -> None:
        nonlocal exec_calls
        exec_calls += 1
        raise AssertionError("Approval handling must never invoke executor")

    coordinator = AuditedGatewayCoordinator(
        pipeline=GatewayPreflightPipeline(policy, workspace_root=workspace_dir),
        audit_writer=AuditWriter(db_factory),
    )
    ask_outcome = coordinator.handle_call(
        ToolCall(
            session_id=SESSION_ID,
            call_id="call_ask_resolve",
            tool="files.upload",
            args={"path": "src/main.py", "destination": "https://example.com/u"},
        ),
        coding_profile,
        executor=executor,
    )
    assert ask_outcome.preflight.pending_approval is not None
    assert ask_outcome.audit_entry.seq == 1
    assert ask_outcome.audit_entry.decision == "asked"

    mgr = ApprovalManager(db_factory, event_bus, clock=lambda: BASE_TIME)
    created = mgr.create_approval(ask_outcome.preflight.pending_approval, policy=policy)

    resolve_time = BASE_TIME + timedelta(seconds=30)
    resolved = mgr.resolve_approval(
        ApprovalResponse(
            approval_id=created.approval.approval_id,
            approve=approve_flag,
        ),
        now=resolve_time,
    )

    assert exec_calls == 0
    assert resolved.approval_id == created.approval.approval_id
    assert resolved.session_id == SESSION_ID
    assert resolved.status == expected_status
    assert resolved.approved is approve_flag
    assert resolved.resolved_by == "user"
    assert resolved.timed_out is False
    assert resolved.resolved_at == resolve_time.isoformat().replace("+00:00", "Z")
    assert resolved.audit_entry.seq == 2
    assert resolved.audit_entry.decision == expected_audit_decision
    assert resolved.audit_entry.prev_hash == ask_outcome.audit_entry.hash

    assert resolved.event is not None
    assert resolved.event.type == "approval_resolved"
    assert resolved.event.data == {
        "approval_id": created.approval.approval_id,
        "approved": approve_flag,
        "by": "user",
    }

    with db_factory() as s:
        row = s.get(ApprovalModel, created.approval.approval_id)
        assert row is not None
        assert row.status == expected_status
        assert row.resolved_by == "user"
        assert row.resolved_at == resolved.resolved_at

        report = verify_session_audit(s, SESSION_ID)
        assert report.valid is True
        assert report.entry_count == 2


# ---------------------------------------------------------------------------
# 4. Timeout Behavior (At/After Expiry Response & expire_due)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("offset_s", [0, 15])
def test_response_at_or_after_expiry_times_out_and_denies_even_if_approve_true(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    offset_s: int,
) -> None:
    """Late approve=True at/after expires_at becomes timed_out + asked_denied."""
    created = manager.create_approval(_sample_pending(), policy=policy, now=BASE_TIME)
    at_or_after_expiry = BASE_TIME + timedelta(
        seconds=policy.defaults.approval_timeout_s + offset_s
    )

    res = manager.resolve_approval(
        ApprovalResponse(approval_id=created.approval.approval_id, approve=True),
        now=at_or_after_expiry,
    )

    assert res.status == "timed_out"
    assert res.approved is False
    assert res.resolved_by == "timeout"
    assert res.timed_out is True
    assert res.audit_entry.decision == "asked_denied"
    assert res.audit_entry.reason == "approval timed out"
    assert res.event is not None
    assert res.event.type == "approval_resolved"
    assert res.event.data == {
        "approval_id": created.approval.approval_id,
        "approved": False,
        "by": "timeout",
    }

    with db_factory() as s:
        row = s.get(ApprovalModel, created.approval.approval_id)
        assert row is not None
        assert row.status == "timed_out"
        assert row.resolved_by == "timeout"
        assert verify_session_audit(s, SESSION_ID).valid is True


def test_expire_due_only_expires_due_pending_approvals(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> None:
    """expire_due expires due pending cards only, leaving future/resolved intact."""
    t0 = BASE_TIME
    t_future_create = BASE_TIME + timedelta(seconds=60)

    a_due = manager.create_approval(
        _sample_pending(call_id="call_due"), policy=policy, now=t0
    )
    a_future = manager.create_approval(
        _sample_pending(call_id="call_future"), policy=policy, now=t_future_create
    )
    a_already_denied = manager.create_approval(
        _sample_pending(call_id="call_denied"), policy=policy, now=t0
    )
    manager.resolve_approval(
        ApprovalResponse(
            approval_id=a_already_denied.approval.approval_id,
            approve=False,
        ),
        now=t0 + timedelta(seconds=10),
    )

    # At t0 + 120s: a_due expires (t0+120s), while a_future stays pending (t0+180s).
    sweep_time = t0 + timedelta(seconds=policy.defaults.approval_timeout_s)
    expired = manager.expire_due(session_id=SESSION_ID, now=sweep_time)

    assert [r.approval_id for r in expired] == [a_due.approval.approval_id]
    r = expired[0]
    assert r.status == "timed_out"
    assert r.approved is False
    assert r.resolved_by == "timeout"
    assert r.audit_entry.decision == "asked_denied"
    assert r.event is not None
    assert r.event.data == {
        "approval_id": a_due.approval.approval_id,
        "approved": False,
        "by": "timeout",
    }

    with db_factory() as s:
        due_row = s.get(ApprovalModel, a_due.approval.approval_id)
        future_row = s.get(ApprovalModel, a_future.approval.approval_id)
        denied_row = s.get(ApprovalModel, a_already_denied.approval.approval_id)
        assert due_row is not None and due_row.status == "timed_out"
        assert future_row is not None and future_row.status == "pending"
        assert denied_row is not None and denied_row.status == "denied"
        assert verify_session_audit(s, SESSION_ID).valid is True

    resolved_events = [
        e
        for e in event_bus.replay(session_id=SESSION_ID)
        if e.type == "approval_resolved"
    ]
    assert [e.data["approval_id"] for e in resolved_events] == [
        a_already_denied.approval.approval_id,
        a_due.approval.approval_id,
    ]


# ---------------------------------------------------------------------------
# 5. Unknown ID, Duplicate Resolution, and Concurrency Safety
# ---------------------------------------------------------------------------
def test_unknown_and_duplicate_resolution_fail_closed(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> None:
    missing_id = generate_approval_id(BASE_TIME)
    with pytest.raises(ApprovalNotFoundError):
        manager.resolve_approval(
            ApprovalResponse(approval_id=missing_id, approve=True),
            now=BASE_TIME,
        )

    created = manager.create_approval(_sample_pending(), policy=policy, now=BASE_TIME)
    manager.resolve_approval(
        ApprovalResponse(approval_id=created.approval.approval_id, approve=True),
        now=BASE_TIME + timedelta(seconds=5),
    )

    with pytest.raises(ApprovalAlreadyResolvedError):
        manager.resolve_approval(
            ApprovalResponse(approval_id=created.approval.approval_id, approve=False),
            now=BASE_TIME + timedelta(seconds=6),
        )

    with db_factory() as s:
        audit_rows = s.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
        ).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].decision == "asked_approved"

    assert len(event_bus.replay(session_id=SESSION_ID)) == 2


def test_concurrent_resolution_only_succeeds_once(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> None:
    """Racing threads resolving the same approval produce exactly one outcome."""
    created = manager.create_approval(_sample_pending(), policy=policy, now=BASE_TIME)
    aid = created.approval.approval_id
    resolve_ts = BASE_TIME + timedelta(seconds=10)

    def attempt(approve: bool) -> str:
        try:
            res = manager.resolve_approval(
                ApprovalResponse(approval_id=aid, approve=approve),
                now=resolve_ts,
            )
            return f"ok:{res.status}"
        except ApprovalAlreadyResolvedError:
            return "already_resolved"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, [True, False, True, False, True, False]))

    successes = [o for o in outcomes if o.startswith("ok:")]
    rejections = [o for o in outcomes if o == "already_resolved"]
    assert len(successes) == 1
    assert len(rejections) == 5

    with db_factory() as s:
        entries = s.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
        ).all()
        assert len(entries) == 1
        assert verify_session_audit(s, SESSION_ID).valid is True

    events = event_bus.replay(session_id=SESSION_ID)
    assert [e.type for e in events] == ["approval_request", "approval_resolved"]


# ---------------------------------------------------------------------------
# 6. Audit Rollback & Post-Commit Event Failure Reporting
# ---------------------------------------------------------------------------
def test_audit_failure_rolls_back_approval_resolution(
    manager: ApprovalManager,
    policy: Policy,
    db_factory: sessionmaker[Session],
    event_bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If audit write fails during resolution, approval returns to pending cleanly."""
    created = manager.create_approval(_sample_pending(), policy=policy, now=BASE_TIME)
    aid = created.approval.approval_id

    def broken_append_audit_entry(*args: Any, **kwargs: Any):
        raise AuditWriterError("simulated disk full during audit append")

    monkeypatch.setattr(
        "control_plane.gateway.approval_manager.append_audit_entry",
        broken_append_audit_entry,
    )

    with pytest.raises(ApprovalManagerError, match="Audit persistence failed"):
        manager.resolve_approval(
            ApprovalResponse(approval_id=aid, approve=True),
            now=BASE_TIME + timedelta(seconds=5),
        )

    # Approval row rolled back cleanly; no resolved audit row or resolved event emitted
    with db_factory() as s:
        row = s.get(ApprovalModel, aid)
        assert row is not None
        assert row.status == "pending"
        assert row.resolved_by is None
        assert row.resolved_at is None
        assert (
            s.scalars(
                select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
            ).all()
            == []
        )

    assert [e.type for e in event_bus.replay(session_id=SESSION_ID)] == [
        "approval_request"
    ]

    # Restore real append_audit_entry and confirm approval can still be resolved cleanly
    monkeypatch.setattr(
        "control_plane.gateway.approval_manager.append_audit_entry",
        audit_writer.append_audit_entry,
    )
    recovered = manager.resolve_approval(
        ApprovalResponse(approval_id=aid, approve=False),
        now=BASE_TIME + timedelta(seconds=6),
    )
    assert recovered.status == "denied"
    assert recovered.audit_entry.decision == "asked_denied"


def test_event_publish_failure_after_commit_reports_committed_resolution(
    policy: Policy,
    db_factory: sessionmaker[Session],
) -> None:
    """Post-commit EventBus error keeps resolution/audit intact and reports state."""

    class FailingEventBus:
        def __init__(self, delegate: EventBus) -> None:
            self._delegate = delegate
            self.fail_on_resolved = False

        def publish(self, **kwargs: Any):
            if (
                self.fail_on_resolved
                and kwargs.get("event_type") == "approval_resolved"
            ):
                raise EventBusError("simulated websocket event table write failure")
            return self._delegate.publish(**kwargs)

    real_bus = EventBus(db_factory)
    flaky_bus = FailingEventBus(real_bus)
    mgr = ApprovalManager(db_factory, flaky_bus, clock=lambda: BASE_TIME)  # type: ignore[arg-type]

    created = mgr.create_approval(_sample_pending(), policy=policy, now=BASE_TIME)
    flaky_bus.fail_on_resolved = True

    with pytest.raises(ApprovalEventPublishError) as exc_info:
        mgr.resolve_approval(
            ApprovalResponse(approval_id=created.approval.approval_id, approve=True),
            now=BASE_TIME + timedelta(seconds=10),
        )

    err = exc_info.value
    assert err.committed_result is not None
    assert err.committed_result.status == "approved"
    assert err.committed_result.audit_entry.decision == "asked_approved"

    # Approval + audit remain committed; only event emission failed
    with db_factory() as s:
        row = s.get(ApprovalModel, created.approval.approval_id)
        assert row is not None
        assert row.status == "approved"
        assert verify_session_audit(s, SESSION_ID).entry_count == 1
        events = s.scalars(
            select(EventModel).where(EventModel.session_id == SESSION_ID)
        ).all()
        assert [e.type for e in events] == ["approval_request"]

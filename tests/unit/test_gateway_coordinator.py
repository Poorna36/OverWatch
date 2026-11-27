"""Focused unit tests for audited gateway coordinator and migration 0002."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from control_plane.audit.audit_writer import (
    AuditWriter,
    AuditWriterError,
    sanitize_audit_args,
)
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import (
    ErrorCode,
    Policy,
    Profile,
    SessionState,
    ToolCall,
)
from control_plane.db.models import AuditEntryModel, SessionModel
from control_plane.db.session import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import (
    AuditedGatewayCoordinator,
    GatewayCoordinatorError,
    coordinate_tool_call,
)
from control_plane.gateway.pipeline import (
    GatewayPreflightPipeline,
    GatewayPreflightResult,
)
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"
CALL_SEQ = 0


def _call(tool: str, args: dict[str, Any] | None = None) -> ToolCall:
    global CALL_SEQ
    CALL_SEQ += 1
    return ToolCall(
        session_id=SESSION_ID,
        call_id=f"call_{CALL_SEQ}",
        tool=tool,
        args={} if args is None else args,
    )


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy()


@pytest.fixture(scope="module")
def coding_profile() -> Profile:
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
    """Isolated SQLite database with schema, triggers, and active session row."""
    db_path = tmp_path / "coordinator.db"
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
def coordinator(
    policy: Policy,
    workspace_dir: Path,
    db_factory: sessionmaker[Session],
) -> AuditedGatewayCoordinator:
    return AuditedGatewayCoordinator(
        pipeline=GatewayPreflightPipeline(policy=policy, workspace_root=workspace_dir),
        audit_writer=AuditWriter(db_factory),
    )


# ---------------------------------------------------------------------------
# 1. Migration 0002: constraint upgrade, hash & trigger preservation, downgrade
# ---------------------------------------------------------------------------
def test_migration_0002_preserves_existing_hashes_and_guards_downgrade(
    tmp_path: Path,
) -> None:
    """Upgrading 0001->0002 keeps hashes/triggers and blocks ask downgrade."""
    db_path = tmp_path / "mig_0002.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_url)

    # Start on already-applied 0001 migration
    command.upgrade(cfg, "0001_initial_core_tables")
    engine = create_db_engine(db_url)
    factory = create_session_factory(engine)
    writer = AuditWriter(factory)

    with factory() as s:
        s.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_mig",
                created_at="2026-11-26T22:00:00Z",
                updated_at="2026-11-26T22:00:00Z",
            )
        )
        s.commit()

    entry1 = writer.append(
        session_id=SESSION_ID,
        tool="files.read",
        args={"path": "src/main.py"},
        decision="allowed",
        reason="policy allow",
        tier="read",
    )
    entry2 = writer.append(
        session_id=SESSION_ID,
        tool="terminal.run",
        args={"command": "rm -rf /"},
        decision="blocked",
        reason="deny pattern matched",
        tier="reversible",
    )

    # 0001 rejects 'asked'
    with pytest.raises(AuditWriterError):
        writer.append(
            session_id=SESSION_ID,
            tool="files.upload",
            args={"path": "src/main.py"},
            decision="asked",
            reason="sensitive action",
            tier="sensitive",
        )

    # Upgrade to 0002
    command.upgrade(cfg, "0002_add_asked_audit_decision")

    # Pre-existing hashes and index/triggers remain intact
    with factory() as s:
        rows = s.scalars(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == SESSION_ID)
            .order_by(AuditEntryModel.seq.asc())
        ).all()
        assert [(r.seq, r.decision, r.hash) for r in rows] == [
            (1, "allowed", entry1.hash),
            (2, "blocked", entry2.hash),
        ]
        # Append-only triggers remain active
        with pytest.raises(DatabaseError, match="UPDATE not allowed"):
            s.execute(
                text(
                    "UPDATE audit_entries SET reason = 'tampered' "
                    "WHERE session_id = :sid AND seq = 1"
                ),
                {"sid": SESSION_ID},
            )
        s.rollback()
        with pytest.raises(DatabaseError, match="DELETE not allowed"):
            s.execute(
                text("DELETE FROM audit_entries WHERE session_id = :sid AND seq = 1"),
                {"sid": SESSION_ID},
            )
        s.rollback()
        assert verify_session_audit(s, SESSION_ID).valid is True

    indexes = {idx["name"] for idx in inspect(engine).get_indexes("audit_entries")}
    assert "idx_audit_lookup" in indexes

    # Appending 'asked' now succeeds and preserves chain integrity
    entry3 = writer.append(
        session_id=SESSION_ID,
        tool="files.upload",
        args={"path": "src/main.py", "destination": "https://example.com"},
        decision="asked",
        reason="approval required",
        tier="sensitive",
    )
    assert entry3.seq == 3
    assert entry3.decision == "asked"
    assert entry3.prev_hash == entry2.hash
    with factory() as s:
        assert verify_session_audit(s, SESSION_ID).valid is True

    # Downgrade MUST fail safely because an 'asked' row exists
    with pytest.raises(RuntimeError, match="decision='asked'"):
        command.downgrade(cfg, "0001_initial_core_tables")

    with factory() as s:
        count = s.execute(text("SELECT COUNT(*) FROM audit_entries")).scalar()
        assert count == 3
        assert verify_session_audit(s, SESSION_ID).valid is True

    engine.dispose()


def test_migration_0002_downgrade_succeeds_without_asked_rows(tmp_path: Path) -> None:
    """Downgrading 0002->0001 succeeds cleanly when zero 'asked' rows exist."""
    db_path = tmp_path / "mig_downgrade_ok.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")
    engine = create_db_engine(db_url)
    factory = create_session_factory(engine)
    writer = AuditWriter(factory)

    with factory() as s:
        s.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_mig",
                created_at="2026-11-26T22:00:00Z",
                updated_at="2026-11-26T22:00:00Z",
            )
        )
        s.commit()

    entry = writer.append(
        session_id=SESSION_ID,
        tool="files.read",
        args={"path": "src/main.py"},
        decision="allowed",
        reason="ok",
        tier="read",
    )

    command.downgrade(cfg, "0001_initial_core_tables")

    with factory() as s:
        row = s.scalar(
            select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
        )
        assert row is not None
        assert row.hash == entry.hash
        assert verify_session_audit(s, SESSION_ID).valid is True
        with pytest.raises(DatabaseError, match="DELETE not allowed"):
            s.execute(text("DELETE FROM audit_entries"))
        s.rollback()
        with pytest.raises(IntegrityError):
            s.add(
                AuditEntryModel(
                    session_id=SESSION_ID,
                    seq=2,
                    ts="2026-11-26T22:01:00Z",
                    tool="files.upload",
                    args_redacted="{}",
                    decision="asked",
                    reason="approval",
                    tier="sensitive",
                    prev_hash=entry.hash,
                    hash="0" * 64,
                )
            )
            s.commit()
        s.rollback()

    engine.dispose()


# ---------------------------------------------------------------------------
# 2. Coordinator execution, audit ordering, and no-execute guarantees
# ---------------------------------------------------------------------------
def test_allowed_call_persists_audit_before_executor_runs(
    coordinator: AuditedGatewayCoordinator,
    coding_profile: Profile,
    db_factory: sessionmaker[Session],
) -> None:
    """Allowed call writes its 'allowed' audit row BEFORE running executor."""
    observed_db_rows_at_exec_time: list[tuple[int, str, str]] = []
    seen_validated: list[ValidatedArguments] = []

    def executor(vargs: ValidatedArguments) -> dict[str, Any]:
        seen_validated.append(vargs)
        with db_factory() as s:
            rows = s.scalars(
                select(AuditEntryModel).where(AuditEntryModel.session_id == SESSION_ID)
            ).all()
            observed_db_rows_at_exec_time.extend(
                (r.seq, r.decision, r.args_redacted) for r in rows
            )
        return {"written": vargs.args["path"]}

    secret_body = "TOP SECRET CONTENT\n" * 30
    res = coordinator.handle_call(
        _call(
            "files.write",
            {
                "path": "./src/out.txt",
                "content": secret_body,
            },
        ),
        coding_profile,
        executor=executor,
    )

    # Audit row already existed when executor entered
    assert len(observed_db_rows_at_exec_time) == 1
    seq_at_exec, decision_at_exec, raw_args_json = observed_db_rows_at_exec_time[0]
    assert seq_at_exec == 1
    assert decision_at_exec == "allowed"
    assert "TOP SECRET CONTENT" not in raw_args_json
    assert f"[REDACTED {len(secret_body.encode('utf-8'))} bytes]" in raw_args_json

    assert res.executed is True
    assert res.audit_entry.seq == 1
    assert res.audit_entry.decision == "allowed"
    assert res.audit_entry.tier == "reversible"
    assert res.audit_entry.args_redacted == {
        "path": "src/out.txt",
        "content": f"[REDACTED {len(secret_body.encode('utf-8'))} bytes]",
    }
    assert res.tool_result.ok is True
    assert res.tool_result.decision == "allow"
    assert res.tool_result.audit_seq == 1
    assert res.tool_result.result == {"written": "src/out.txt"}
    assert len(seen_validated) == 1
    assert seen_validated[0].args["path"] == "src/out.txt"


def test_ask_call_audits_asked_and_never_invokes_executor(
    coordinator: AuditedGatewayCoordinator,
    coding_profile: Profile,
    db_factory: sessionmaker[Session],
) -> None:
    """Sensitive/escalated 'ask' calls write 'asked' row and never invoke executor."""
    called = 0

    def forbidden_executor(_vargs: ValidatedArguments) -> None:
        nonlocal called
        called += 1
        raise AssertionError("Executor must never run on 'ask' decision")

    long_dest = "https://uploads.example.com/" + ("z" * 240)
    res = coordinator.handle_call(
        _call(
            "files.upload",
            {
                "path": "./src/main.py",
                "destination": long_dest,
            },
        ),
        coding_profile,
        executor=forbidden_executor,
    )

    assert called == 0
    assert res.executed is False
    assert res.preflight.decision == "ask"
    assert res.preflight.needs_approval is True
    assert res.preflight.pending_approval is not None
    assert res.preflight.pending_approval.args_redacted["path"] == "src/main.py"
    assert len(res.preflight.pending_approval.args_redacted["destination"]) == 200

    assert res.audit_entry.decision == "asked"
    assert res.audit_entry.tier == "sensitive"
    assert len(res.audit_entry.args_redacted["destination"]) == 200

    assert res.tool_result.ok is False
    assert res.tool_result.decision == "ask"
    assert res.tool_result.result is None
    assert res.tool_result.error is None
    assert res.tool_result.audit_seq == res.audit_entry.seq

    with db_factory() as s:
        assert verify_session_audit(s, SESSION_ID).valid is True


@pytest.mark.parametrize(
    ("session_state", "tool", "args", "expected_code"),
    [
        ("PAUSED", "files.read", {"path": "src/main.py"}, ErrorCode.SESSION_INACTIVE),
        ("ACTIVE", "gmail.send", {"to": "a@b.com"}, ErrorCode.NOT_IN_PROFILE),
        ("ACTIVE", "files.nonexistent", {}, ErrorCode.POLICY_BLOCK),
        ("ACTIVE", "files.read", {"path": "../etc/passwd"}, ErrorCode.PATH_JAIL),
        (
            "ACTIVE",
            "files.read",
            {"path": "src/main.py", "api_token": "tok_secret"},
            ErrorCode.ARG_INVALID,
        ),
        (
            "ACTIVE",
            "terminal.run",
            {"command": "rm -rf /workspace"},
            ErrorCode.DENY_PATTERN,
        ),
    ],
)
def test_blocked_calls_audit_blocked_and_never_invoke_executor(
    coordinator: AuditedGatewayCoordinator,
    coding_profile: Profile,
    db_factory: sessionmaker[Session],
    session_state: SessionState,
    tool: str,
    args: dict[str, Any],
    expected_code: ErrorCode,
) -> None:
    """Every block path persists 'blocked' with redacted args and skips executor."""
    called = 0

    def forbidden_executor(_vargs: ValidatedArguments) -> None:
        nonlocal called
        called += 1
        raise AssertionError("Executor must never run on 'block' decision")

    res = coordinator.handle_call(
        _call(tool, args),
        coding_profile,
        executor=forbidden_executor,
        session_state=session_state,
    )

    assert called == 0
    assert res.executed is False
    assert res.audit_entry.decision == "blocked"
    assert "api_token" not in res.audit_entry.args_redacted
    assert res.tool_result.ok is False
    assert res.tool_result.decision == "block"
    assert res.tool_result.error is not None
    assert res.tool_result.error.code == expected_code
    assert res.tool_result.audit_seq == res.audit_entry.seq

    with db_factory() as s:
        assert verify_session_audit(s, SESSION_ID).valid is True


# ---------------------------------------------------------------------------
# 3. Fail-closed guarantees and mixed session hash-chain verification
# ---------------------------------------------------------------------------
def test_audit_persistence_failure_prevents_execution(
    policy: Policy,
    coding_profile: Profile,
    workspace_dir: Path,
    tmp_path: Path,
) -> None:
    """If AuditWriter fails, coordinator fails closed without executing."""
    db_path = tmp_path / "missing_session.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)

    executed = 0

    def executor(_vargs: ValidatedArguments) -> dict[str, Any]:
        nonlocal executed
        executed += 1
        return {"ok": True}

    try:
        with pytest.raises(AuditWriterError):
            coordinate_tool_call(
                _call("files.read", {"path": "src/main.py"}),
                policy=policy,
                profile=coding_profile,
                audit_writer=AuditWriter(factory),
                executor=executor,
                workspace_root=workspace_dir,
            )
        assert executed == 0
        with factory() as s:
            assert s.execute(text("SELECT COUNT(*) FROM audit_entries")).scalar() == 0
    finally:
        engine.dispose()


def test_inconsistent_preflight_audit_decision_fails_closed(
    coding_profile: Profile,
    db_factory: sessionmaker[Session],
) -> None:
    """Coordinator rejects mismatched audit_decision before writing/executing."""

    class TamperedPipeline:
        def evaluate(
            self,
            call: ToolCall,
            _profile: Profile,
            *,
            session_state: SessionState = "ACTIVE",
            workspace_root: Path | str | None = None,
        ) -> GatewayPreflightResult:
            return GatewayPreflightResult(
                call_id=call.call_id,
                session_id=call.session_id,
                tool=call.tool,
                decision="allow",
                tier="read",
                reason="tampered",
                executable=True,
                audit_decision=None,
            )

    called = 0

    def executor(_vargs: ValidatedArguments) -> None:
        nonlocal called
        called += 1

    coord = AuditedGatewayCoordinator(
        pipeline=TamperedPipeline(),  # type: ignore[arg-type]
        audit_writer=AuditWriter(db_factory),
    )

    with pytest.raises(GatewayCoordinatorError):
        coord.handle_call(
            _call("files.read", {"path": "src/main.py"}),
            coding_profile,
            executor=executor,
        )

    assert called == 0
    with db_factory() as s:
        assert s.execute(text("SELECT COUNT(*) FROM audit_entries")).scalar() == 0


def test_mixed_decisions_produce_verifiable_hash_chain(
    coordinator: AuditedGatewayCoordinator,
    coding_profile: Profile,
    db_factory: sessionmaker[Session],
) -> None:
    """Allow -> ask -> block -> allow sequence verifies end-to-end cleanly."""
    r1 = coordinator.handle_call(
        _call("files.read", {"path": "src/main.py"}),
        coding_profile,
        executor=lambda v: {"path": v.args["path"], "content": "print('ok')\n"},
    )
    r2 = coordinator.handle_call(
        _call(
            "terminal.run",
            {"command": "curl https://pypi.org/simple/"},
        ),
        coding_profile.model_copy(update={"tool_overrides": {}}),
        executor=lambda _v: pytest.fail("executor must not run for ask"),
    )
    r3 = coordinator.handle_call(
        _call("files.read", {"path": "../secret.txt"}),
        coding_profile,
        executor=lambda _v: pytest.fail("executor must not run for block"),
    )
    r4 = coordinator.handle_call(
        _call("files.write", {"path": "notes.txt", "content": "hello"}),
        coding_profile,
        executor=lambda v: {"written": v.args["path"]},
    )

    assert [r.audit_entry.seq for r in (r1, r2, r3, r4)] == [1, 2, 3, 4]
    assert [r.audit_entry.decision for r in (r1, r2, r3, r4)] == [
        "allowed",
        "asked",
        "blocked",
        "allowed",
    ]
    assert [r.executed for r in (r1, r2, r3, r4)] == [True, False, False, True]

    with db_factory() as s:
        report = verify_session_audit(s, SESSION_ID)
        assert report.valid is True
        assert report.entry_count == 4
        # Ensure sanitized write body preserves byte placeholder (no double redaction)
        assert r4.audit_entry.args_redacted == sanitize_audit_args(
            {"path": "notes.txt", "content": "hello"}
        )

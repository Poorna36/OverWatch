"""Focused unit tests for Phase 0 database models, triggers, and migrations."""

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
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
from control_plane.sessions.profile_loader import load_profile
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command


@pytest.fixture
def temp_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Yield a database session connected to an isolated SQLite file with triggers."""
    db_path = tmp_path / "test_overwatch.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_db_engine(db_url)
    init_db(engine)
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_alembic_migration_lifecycle(tmp_path: Path):
    """Initial Alembic migration must upgrade and downgrade cleanly."""
    repo_root = Path(__file__).resolve().parents[2]
    alembic_ini = repo_root / "alembic.ini"
    test_db = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{test_db}"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"sessions", "audit_entries", "events", "approvals"}.issubset(tables)

    # Downgrade back to base
    command.downgrade(alembic_cfg, "base")
    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names())
    assert not {"sessions", "audit_entries", "events", "approvals"}.intersection(
        remaining_tables
    )
    engine.dispose()


def test_session_ownership_and_constraints(temp_db: Session):
    """Sessions must require owner_id and validate lifecycle state."""
    session = SessionModel(
        session_id="s_01J8ABCDEFGH1234567890ABCD",
        state="CREATED",
        profile="general",
        owner_id="caller_device_123",
        dry_run=0,
        created_at="2026-11-26T14:00:00Z",
        updated_at="2026-11-26T14:00:00Z",
    )
    temp_db.add(session)
    temp_db.commit()

    retrieved = temp_db.scalar(
        select(SessionModel).where(
            SessionModel.session_id == "s_01J8ABCDEFGH1234567890ABCD"
        )
    )
    assert retrieved is not None
    assert retrieved.owner_id == "caller_device_123"
    assert retrieved.state == "CREATED"
    assert retrieved.profile == "general"

    # Verify invalid state fails check constraint
    invalid_session = SessionModel(
        session_id="s_invalid_state",
        state="UNKNOWN_STATE",
        profile="general",
        owner_id="caller_device_123",
        dry_run=0,
        created_at="2026-11-26T14:00:00Z",
        updated_at="2026-11-26T14:00:00Z",
    )
    temp_db.add(invalid_session)
    with pytest.raises(IntegrityError):
        temp_db.commit()
    temp_db.rollback()

    # Verify columns do not store prohibited dynamic URLs or raw request text
    columns = {c.name for c in SessionModel.__table__.columns}
    assert "stream_url" not in columns
    assert "view_url" not in columns
    assert "request_text" not in columns


def test_session_delete_rejected_by_trigger(temp_db: Session):
    """Deleting a session must be rejected by the sessions_prevent_delete trigger."""
    session = SessionModel(
        session_id="s_delete_test",
        state="ACTIVE",
        profile="coding",
        owner_id="caller_x",
        created_at="2026-11-26T14:00:00Z",
        updated_at="2026-11-26T14:00:00Z",
    )
    temp_db.add(session)
    temp_db.commit()

    retrieved = temp_db.scalar(
        select(SessionModel).where(SessionModel.session_id == "s_delete_test")
    )
    assert retrieved is not None
    temp_db.delete(retrieved)

    with pytest.raises(IntegrityError) as exc_info:
        temp_db.commit()
    assert "Session records are permanent: DELETE not allowed" in str(exc_info.value)
    temp_db.rollback()


def test_alembic_downgrade_refuses_populated_data(tmp_path: Path):
    """Alembic downgrade must abort when sessions or audit entries exist."""
    repo_root = Path(__file__).resolve().parents[2]
    alembic_ini = repo_root / "alembic.ini"
    test_db = tmp_path / "populated_alembic_test.db"
    db_url = f"sqlite:///{test_db}"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_url)
    session_factory = create_session_factory(engine)
    with session_factory() as s:
        session = SessionModel(
            session_id="s_populated",
            state="ENDED",
            profile="coding",
            owner_id="caller_y",
            created_at="2026-11-26T14:00:00Z",
            updated_at="2026-11-26T14:00:00Z",
        )
        s.add(session)
        s.commit()

    # Attempt downgrade with populated session row -> must raise RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        command.downgrade(alembic_cfg, "base")
    assert "populated data exists" in str(exc_info.value)
    assert "Audit and session records are permanently retained" in str(exc_info.value)

    # Verify tables still exist
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"sessions", "audit_entries", "events", "approvals"}.issubset(tables)

    engine.dispose()


def test_foreign_key_enforcement(temp_db: Session):
    """Foreign keys must be enforced; orphan child rows must fail."""
    orphan_audit = AuditEntryModel(
        session_id="s_nonexistent",
        seq=1,
        ts="2026-11-26T14:00:00Z",
        tool="files.read",
        args_redacted="{}",
        decision="allowed",
        reason="POLICY_ALLOW",
        prev_hash="genesis",
        hash="abc123hash",
    )
    temp_db.add(orphan_audit)
    with pytest.raises(IntegrityError):
        temp_db.commit()
    temp_db.rollback()

    orphan_event = EventModel(
        session_id="s_nonexistent",
        seq=1,
        ts="2026-11-26T14:00:00Z",
        type="session_state",
        data="{}",
    )
    temp_db.add(orphan_event)
    with pytest.raises(IntegrityError):
        temp_db.commit()
    temp_db.rollback()

    orphan_approval = ApprovalModel(
        approval_id="a_01J8ABCDEFGH1234567890ABCD",
        session_id="s_nonexistent",
        call_id="call_1",
        tool="terminal.run",
        tier="sensitive",
        args_redacted="{}",
        reason="sudo command",
        irreversible=1,
        expires_at="2026-11-26T15:00:00Z",
        status="pending",
    )
    temp_db.add(orphan_approval)
    with pytest.raises(IntegrityError):
        temp_db.commit()
    temp_db.rollback()


def test_audit_immutability_triggers(temp_db: Session):
    """Audit entries must reject UPDATE and DELETE statements via SQLite triggers."""
    session = SessionModel(
        session_id="s_audit_test",
        state="ACTIVE",
        profile="coding",
        owner_id="caller_1",
        created_at="2026-11-26T14:00:00Z",
        updated_at="2026-11-26T14:00:00Z",
    )
    temp_db.add(session)
    temp_db.commit()

    audit = AuditEntryModel(
        session_id="s_audit_test",
        seq=1,
        ts="2026-11-26T14:00:01Z",
        tool="files.write",
        args_redacted='{"path": "/workspace/main.py"}',
        decision="allowed",
        reason="POLICY_ALLOW",
        prev_hash="genesis",
        hash="audit_hash_1",
    )
    temp_db.add(audit)
    temp_db.commit()

    # Attempt UPDATE -> trigger must raise abort
    audit.decision = "blocked"
    with pytest.raises(IntegrityError) as exc_update:
        temp_db.commit()
    assert "Audit log entries are immutable: UPDATE not allowed" in str(
        exc_update.value
    )
    temp_db.rollback()

    # Attempt DELETE -> trigger must raise abort
    retrieved = temp_db.scalar(
        select(AuditEntryModel).where(
            AuditEntryModel.session_id == "s_audit_test",
            AuditEntryModel.seq == 1,
        )
    )
    assert retrieved is not None
    temp_db.delete(retrieved)
    with pytest.raises(IntegrityError) as exc_delete:
        temp_db.commit()
    assert "Audit log entries are immutable: DELETE not allowed" in str(
        exc_delete.value
    )
    temp_db.rollback()


def test_events_and_approvals_persistence(temp_db: Session):
    """Events and approvals must persist correctly and support replay ordering."""
    session = SessionModel(
        session_id="s_event_test",
        state="ACTIVE",
        profile="general",
        owner_id="caller_2",
        created_at="2026-11-26T14:00:00Z",
        updated_at="2026-11-26T14:00:00Z",
    )
    temp_db.add(session)
    temp_db.commit()

    # Add sequenced events
    e1 = EventModel(
        session_id="s_event_test",
        seq=1,
        ts="2026-11-26T14:00:01Z",
        type="session_state",
        data='{"state": "STARTING"}',
    )
    e2 = EventModel(
        session_id="s_event_test",
        seq=2,
        ts="2026-11-26T14:00:02Z",
        type="tool_call",
        data='{"tool": "browser.open"}',
    )
    temp_db.add_all([e1, e2])
    temp_db.commit()

    replayed = list(
        temp_db.scalars(
            select(EventModel)
            .where(EventModel.session_id == "s_event_test")
            .order_by(EventModel.seq)
        )
    )
    assert len(replayed) == 2
    assert replayed[0].seq == 1
    assert replayed[0].type == "session_state"
    assert replayed[1].seq == 2
    assert replayed[1].type == "tool_call"

    # Add and resolve an approval card
    approval = ApprovalModel(
        approval_id="a_01J8TESTAPPROVAL00000000001",
        session_id="s_event_test",
        call_id="call_99",
        tool="desktop.click",
        tier="sensitive",
        args_redacted='{"x": 100, "y": 200}',
        reason="Sensitive desktop action",
        irreversible=1,
        expires_at="2026-11-26T15:00:00Z",
        status="pending",
    )
    temp_db.add(approval)
    temp_db.commit()

    # Resolve approval
    approval.status = "approved"
    approval.resolved_by = "user"
    approval.resolved_at = "2026-11-26T14:05:00Z"
    temp_db.commit()

    resolved = temp_db.scalar(
        select(ApprovalModel).where(
            ApprovalModel.approval_id == "a_01J8TESTAPPROVAL00000000001"
        )
    )
    assert resolved is not None
    assert resolved.status == "approved"
    assert resolved.resolved_by == "user"


def test_general_and_coding_profiles_and_prompts():
    """Verify general and coding profiles load with valid prompts."""
    general = load_profile("general")
    assert general.name == "general"
    assert general.role == "assistant"
    assert general.image == "pocket/py-node:1.0"
    assert general.system_prompt_file == "prompts/assistant.md"
    assert "duckduckgo.com" in general.egress_allowlist
    assert "google.com" in general.egress_allowlist

    coding = load_profile("coding")
    assert coding.name == "coding"
    assert coding.role == "coder"
    assert coding.image == "pocket/py-node:1.0"
    assert coding.system_prompt_file == "prompts/coder.md"

    repo_root = Path(__file__).resolve().parents[2]
    assert (repo_root / general.system_prompt_file).is_file()
    assert (repo_root / coding.system_prompt_file).is_file()

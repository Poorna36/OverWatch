"""Focused unit tests for Phase 2 read-only audit chain verification (F-07)."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from control_plane.audit.audit_writer import GENESIS_PREV_HASH, AuditWriter
from control_plane.audit.verify_audit import (
    verify_all_audits,
    verify_audit,
    verify_audit_entries,
    verify_session_audit,
)
from control_plane.db.models import AuditEntryModel, SessionModel
from control_plane.db.session import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SESSION_ID = "s_01J8ABCDEFGH1234567890ABCD"
OTHER_SESSION = "s_01J8ABCDEFGH1234567890ABCE"


@pytest.fixture
def populated_db(
    tmp_path: Path,
) -> Generator[tuple[Path, sessionmaker[Session]], None, None]:
    """Create a SQLite DB with two sessions and valid chained audit entries."""
    db_path = tmp_path / "verify_audit.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)

    with factory() as db:
        db.add_all(
            [
                SessionModel(
                    session_id=SESSION_ID,
                    state="ACTIVE",
                    profile="coding",
                    owner_id="owner_1",
                    created_at="2026-11-26T14:00:00Z",
                    updated_at="2026-11-26T14:00:00Z",
                ),
                SessionModel(
                    session_id=OTHER_SESSION,
                    state="ENDED",
                    profile="general",
                    owner_id="owner_2",
                    created_at="2026-11-26T14:00:00Z",
                    updated_at="2026-11-26T14:10:00Z",
                ),
            ]
        )
        db.commit()

    writer = AuditWriter(factory)
    writer.append(
        session_id=SESSION_ID,
        tool="files.list",
        args={"path": "."},
        decision="allowed",
        reason="Tier read allowed",
        tier="read",
        duration_ms=5.0,
        ts="2026-11-26T14:00:01Z",
    )
    writer.append(
        session_id=SESSION_ID,
        tool="files.write",
        args={"path": "src/main.py", "content": "print('hi')"},
        decision="allowed",
        reason="Tier reversible allowed",
        tier="reversible",
        duration_ms=14.0,
        ts="2026-11-26T14:00:02Z",
    )
    writer.append(
        session_id=SESSION_ID,
        tool="terminal.run",
        args={"command": "rm -rf /"},
        decision="blocked",
        reason="DENY_PATTERN",
        tier="forbidden",
        duration_ms=1.0,
        ts="2026-11-26T14:00:03Z",
    )

    try:
        yield db_path, factory
    finally:
        engine.dispose()


def _bypass_triggers_and_exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    """Simulate out-of-band DB tampering by dropping immutability triggers."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_entries_prevent_delete")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Valid Chains & Read-Only Guarantees
# ---------------------------------------------------------------------------
def test_valid_chain_and_empty_session_verify_cleanly(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Valid chains and empty session logs verify cleanly with zero issues."""
    _, factory = populated_db
    with factory() as db:
        res_a = verify_session_audit(db, SESSION_ID)
        res_b = verify_audit(db, OTHER_SESSION)

    assert res_a.valid is True
    assert res_a.entry_count == 3
    assert res_a.first_tampered_seq is None
    assert res_a.latest_hash is not None
    assert res_a.issues == []
    assert res_a.error is None

    assert res_b.valid is True
    assert res_b.entry_count == 0
    assert res_b.latest_hash is None


def test_verify_all_audits_and_missing_session_check(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """verify_all_audits checks all sessions; missing session fails when required."""
    _, factory = populated_db
    with factory() as db:
        all_results = verify_all_audits(db)
        missing = verify_session_audit(
            db, "s_01J8MISSING000000000000000", require_session_exists=True
        )

    assert set(all_results.keys()) == {SESSION_ID, OTHER_SESSION}
    assert all_results[SESSION_ID].valid is True
    assert all_results[OTHER_SESSION].valid is True
    assert missing.valid is False
    assert missing.issues[0].code == "SESSION_NOT_FOUND"


def test_verification_never_modifies_stored_records(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Verifying stored rows performs no writes or mutations."""
    _, factory = populated_db
    with factory() as db:
        before = [
            (r.seq, r.decision, r.args_redacted, r.prev_hash, r.hash)
            for r in db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_ID)
                .order_by(AuditEntryModel.seq.asc())
            )
        ]
        result = verify_session_audit(db, SESSION_ID)
        assert not db.dirty
        assert not db.new
        assert not db.deleted
        after = [
            (r.seq, r.decision, r.args_redacted, r.prev_hash, r.hash)
            for r in db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_ID)
                .order_by(AuditEntryModel.seq.asc())
            )
        ]

    assert result.valid is True
    assert before == after


# ---------------------------------------------------------------------------
# 2. Tamper Detection (TESTING.md B15 & §1)
# ---------------------------------------------------------------------------
def test_tampered_row_field_detected(populated_db: tuple[Path, sessionmaker[Session]]):
    """Modifying a stored field breaks hash verification at the tampered seq."""
    db_path, factory = populated_db
    _bypass_triggers_and_exec(
        db_path,
        "UPDATE audit_entries SET decision = 'allowed' "
        "WHERE session_id = ? AND seq = 3",
        (SESSION_ID,),
    )

    with factory() as db:
        result = verify_session_audit(db, SESSION_ID)

    assert result.valid is False
    assert result.entry_count == 3
    assert result.first_tampered_seq == 3
    assert any(issue.code == "HASH_MISMATCH" for issue in result.issues)


def test_tampered_earlier_row_breaks_chain_even_if_hash_not_changed(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Modifying row 1 without updating hashes flags row 1 HASH_MISMATCH."""
    db_path, factory = populated_db
    _bypass_triggers_and_exec(
        db_path,
        "UPDATE audit_entries SET reason = 'tampered reason' "
        "WHERE session_id = ? AND seq = 1",
        (SESSION_ID,),
    )

    with factory() as db:
        result = verify_session_audit(db, SESSION_ID)

    assert result.valid is False
    assert result.first_tampered_seq == 1
    assert any(
        issue.seq == 1 and issue.code == "HASH_MISMATCH" for issue in result.issues
    )


def test_tampered_prev_hash_or_genesis_detected(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Corrupting genesis or intermediate prev_hash is flagged accurately."""
    db_path, factory = populated_db
    _bypass_triggers_and_exec(
        db_path,
        "UPDATE audit_entries SET prev_hash = ? WHERE session_id = ? AND seq = 1",
        ("f" * 64, SESSION_ID),
    )

    with factory() as db:
        result = verify_session_audit(db, SESSION_ID)

    assert result.valid is False
    assert result.first_tampered_seq == 1
    assert any(issue.code == "GENESIS_HASH_MISMATCH" for issue in result.issues)
    assert GENESIS_PREV_HASH == "0" * 64


def test_deleted_middle_row_detected_as_sequence_gap_and_prev_mismatch(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Deleting row 2 creates a sequence gap and broken prev_hash link on row 3."""
    db_path, factory = populated_db
    _bypass_triggers_and_exec(
        db_path,
        "DELETE FROM audit_entries WHERE session_id = ? AND seq = 2",
        (SESSION_ID,),
    )

    with factory() as db:
        result = verify_session_audit(db, SESSION_ID)

    assert result.valid is False
    assert result.first_tampered_seq == 3
    codes = {issue.code for issue in result.issues}
    assert "SEQUENCE_MISMATCH" in codes
    assert "PREV_HASH_MISMATCH" in codes


def test_corrupt_args_json_detected(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Malformed JSON in args_redacted fails verification."""
    db_path, factory = populated_db
    _bypass_triggers_and_exec(
        db_path,
        "UPDATE audit_entries SET args_redacted = '{bad_json' "
        "WHERE session_id = ? AND seq = 2",
        (SESSION_ID,),
    )

    with factory() as db:
        result = verify_session_audit(db, SESSION_ID)

    assert result.valid is False
    assert result.first_tampered_seq == 2
    assert any(issue.code == "INVALID_ARGS_JSON" for issue in result.issues)


def test_verify_audit_entries_in_memory_detects_hash_tamper(
    populated_db: tuple[Path, sessionmaker[Session]],
):
    """Pure in-memory verify_audit_entries detects tampered AuditEntry instances."""
    _, factory = populated_db
    with factory() as db:
        rows = list(
            db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_ID)
                .order_by(AuditEntryModel.seq.asc())
            )
        )
        assert verify_audit_entries(rows, session_id=SESSION_ID).valid is True

    fake = AuditEntryModel(
        session_id=SESSION_ID,
        seq=1,
        ts="2026-11-26T14:00:01Z",
        tool="files.list",
        args_redacted='{"path":"."}',
        decision="allowed",
        reason="Tier read allowed",
        tier="read",
        undo_id=None,
        duration_ms=5.0,
        prev_hash=GENESIS_PREV_HASH,
        hash="deadbeef" * 8,
    )
    res = verify_audit_entries([fake], session_id=SESSION_ID)
    assert res.valid is False
    assert res.first_tampered_seq == 1
    assert res.issues[0].code == "HASH_MISMATCH"

"""Focused unit tests for Phase 2 hash-chained audit writer (F-07)."""

from __future__ import annotations

import json
import math
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from control_plane.audit.audit_writer import (
    GENESIS_PREV_HASH,
    MAX_ARG_STRING_LEN,
    AuditWriter,
    AuditWriterError,
    append_audit_entry,
    canonical_json,
    compute_audit_hash,
    sanitize_audit_args,
)
from control_plane.contracts import AuditEntry
from control_plane.db.models import AuditEntryModel, SessionModel
from control_plane.db.session import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

SESSION_A = "s_01J8ABCDEFGH1234567890ABCD"
SESSION_B = "s_01J8ABCDEFGH1234567890ABCE"


@pytest.fixture
def db_factory(
    tmp_path: Path,
) -> Generator[sessionmaker[Session], None, None]:
    """Yield a sessionmaker backed by an isolated SQLite file with triggers."""
    db_path = tmp_path / "audit_writer.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        s.add_all(
            [
                SessionModel(
                    session_id=SESSION_A,
                    state="ACTIVE",
                    profile="coding",
                    owner_id="owner_a",
                    created_at="2026-11-26T14:00:00Z",
                    updated_at="2026-11-26T14:00:00Z",
                ),
                SessionModel(
                    session_id=SESSION_B,
                    state="ACTIVE",
                    profile="general",
                    owner_id="owner_b",
                    created_at="2026-11-26T14:00:00Z",
                    updated_at="2026-11-26T14:00:00Z",
                ),
            ]
        )
        s.commit()
    try:
        yield factory
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Redaction & Sanitization Rules (SECURITY.md §5, CONTRACTS.md §11)
# ---------------------------------------------------------------------------
def test_secret_keys_stripped_recursively():
    """Keys matching (token|secret|password|authorization|api[_-]?key) are stripped."""
    raw = {
        "path": "src/app.py",
        "token": "tok_live_123",
        "access_token": "eyJhbGciOi...",
        "clientSecret": "shhh",
        "user_password": "pw!",
        "Authorization": "Bearer abc",
        "api_key": "sk-123",
        "api-key": "sk-456",
        "APIKEY": "sk-789",
        "nested": {
            "ok": "keep",
            "refresh_token": "drop",
            "headers": [{"authorization": "drop", "accept": "application/json"}],
        },
    }

    sanitized = sanitize_audit_args(raw)

    assert sanitized == {
        "path": "src/app.py",
        "nested": {
            "ok": "keep",
            "headers": [{"accept": "application/json"}],
        },
    }
    serialized = json.dumps(sanitized)
    for secret in ("tok_live_123", "eyJhbGciOi", "shhh", "pw!", "Bearer abc", "sk-"):
        assert secret not in serialized


def test_long_strings_truncated_to_200_chars():
    """Strings over 200 chars are truncated cleanly to 200 chars."""
    exact_200 = "x" * 200
    over_200 = "y" * 250
    nested_long = "z" * 350

    sanitized = sanitize_audit_args(
        {
            "exact": exact_200,
            "over": over_200,
            "nested": {"items": [nested_long]},
        }
    )

    assert sanitized["exact"] == exact_200
    assert len(sanitized["over"]) == MAX_ARG_STRING_LEN
    assert sanitized["over"].endswith("...")
    assert len(sanitized["nested"]["items"][0]) == MAX_ARG_STRING_LEN


def test_full_file_and_email_bodies_never_persisted():
    """Even short file contents and email bodies are redacted before persistence."""
    short_file_secret = "print('PRIVATE_CODE_LINE')"
    short_email_body = "Hi, confidential salary info is $500k."

    sanitized_file = sanitize_audit_args(
        {"path": "secret.py", "content": short_file_secret}
    )
    sanitized_email = sanitize_audit_args(
        {
            "to": "alice@example.com",
            "subject": "Confidential",
            "body": short_email_body,
            "html_body": "<p>HTML body</p>",
            "raw_email": "MIME raw payload",
        }
    )

    assert sanitized_file["path"] == "secret.py"
    assert short_file_secret not in json.dumps(sanitized_file)
    assert sanitized_file["content"].startswith("[REDACTED ")

    assert sanitized_email["to"] == "alice@example.com"
    assert short_email_body not in json.dumps(sanitized_email)
    assert "<p>HTML body</p>" not in json.dumps(sanitized_email)
    assert "MIME raw payload" not in json.dumps(sanitized_email)


def test_non_finite_or_invalid_args_fail_closed():
    """Non-finite floats and non-mapping args raise AuditWriterError."""
    with pytest.raises(AuditWriterError):
        sanitize_audit_args({"timeout": math.nan})

    with pytest.raises(AuditWriterError):
        sanitize_audit_args(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Hash Chaining, Per-Session Sequencing, and Canonical JSON
# ---------------------------------------------------------------------------
def test_canonical_json_is_deterministic_across_key_orders():
    """Canonical JSON ignores dict insertion order and omits whitespace."""
    a = canonical_json({"b": 2, "a": {"y": 1, "x": "hello"}})
    b = canonical_json({"a": {"x": "hello", "y": 1}, "b": 2})
    assert a == b
    assert a == '{"a":{"x":"hello","y":1},"b":2}'


def test_append_entries_chains_hashes_and_per_session_sequences(
    db_factory: sessionmaker[Session],
):
    """Entries append monotonically per session and follow the hash formula."""
    writer = AuditWriter(db_factory)

    e1 = writer.append(
        session_id=SESSION_A,
        tool="files.write",
        args={"path": "main.py", "content": "x = 1\n", "api_key": "secret-val"},
        decision="allowed",
        reason="Tier reversible allowed",
        tier="reversible",
        duration_ms=12,
        ts="2026-11-26T14:00:01Z",
    )
    e2 = writer.append(
        session_id=SESSION_A,
        tool="files.delete",
        args={"path": "old.tmp"},
        decision="asked_approved",
        reason="User tapped approve",
        tier="sensitive",
        duration_ms=250.5,
        ts="2026-11-26T14:00:02Z",
    )
    e_other = writer.append(
        session_id=SESSION_B,
        tool="browser.open",
        args={"url": "https://pypi.org"},
        decision="allowed",
        reason="Tier read allowed",
        tier="read",
        ts="2026-11-26T14:00:03Z",
    )

    assert isinstance(e1, AuditEntry)
    assert e1.seq == 1
    assert e1.prev_hash == GENESIS_PREV_HASH
    assert len(e1.hash) == 64
    assert e1.duration_ms == 12.0
    assert "api_key" not in e1.args_redacted
    assert "x = 1" not in json.dumps(e1.args_redacted)

    assert e2.seq == 2
    assert e2.prev_hash == e1.hash
    assert e2.hash == compute_audit_hash(
        seq=2,
        session_id=SESSION_A,
        ts="2026-11-26T14:00:02Z",
        tool="files.delete",
        args_redacted={"path": "old.tmp"},
        decision="asked_approved",
        reason="User tapped approve",
        tier="sensitive",
        undo_id=None,
        duration_ms=250.5,
        prev_hash=e1.hash,
    )

    # Independent session starts its own sequence at 1 with GENESIS_PREV_HASH
    assert e_other.seq == 1
    assert e_other.prev_hash == GENESIS_PREV_HASH

    with db_factory() as db:
        rows = list(
            db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_A)
                .order_by(AuditEntryModel.seq.asc())
            )
        )
        assert [r.seq for r in rows] == [1, 2]
        assert rows[0].args_redacted == canonical_json(e1.args_redacted)


def test_missing_session_fails_closed(db_factory: sessionmaker[Session]):
    """Appending for a nonexistent session fails closed with AuditWriterError."""
    writer = AuditWriter(db_factory)
    with pytest.raises(AuditWriterError) as exc_info:
        writer.append(
            session_id="s_01J8MISSING000000000000000",
            tool="files.list",
            args={"path": "."},
            decision="allowed",
            reason="POLICY_ALLOW",
            tier="read",
        )
    assert "does not exist" in str(exc_info.value)


def test_concurrent_appends_assign_unique_contiguous_sequences(
    db_factory: sessionmaker[Session],
):
    """Concurrent transactional appends produce monotonic contiguous seq & hashes."""
    writer = AuditWriter(db_factory)
    total = 12

    def write_one(i: int) -> AuditEntry:
        return writer.append(
            session_id=SESSION_A,
            tool="files.read",
            args={"path": f"file_{i}.txt"},
            decision="allowed",
            reason="POLICY_ALLOW",
            tier="read",
            ts="2026-11-26T14:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write_one, range(total)))

    seqs = sorted(entry.seq for entry in results)
    assert seqs == list(range(1, total + 1))

    with db_factory() as db:
        rows = list(
            db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == SESSION_A)
                .order_by(AuditEntryModel.seq.asc())
            )
        )
        assert len(rows) == total
        assert rows[0].prev_hash == GENESIS_PREV_HASH
        for i in range(1, total):
            assert rows[i].prev_hash == rows[i - 1].hash


def test_append_audit_entry_with_caller_transaction(
    db_factory: sessionmaker[Session],
):
    """append_audit_entry(..., commit=False) flushes inside caller transaction."""
    with db_factory() as db:
        entry = append_audit_entry(
            db,
            session_id=SESSION_A,
            tool="terminal.run",
            args={"command": "pytest"},
            decision="blocked",
            reason="POLICY_BLOCK",
            tier="forbidden",
            commit=False,
        )
        assert entry.seq == 1
        db.commit()

    with db_factory() as db:
        row = db.scalar(
            select(AuditEntryModel).where(
                AuditEntryModel.session_id == SESSION_A,
                AuditEntryModel.seq == 1,
            )
        )
        assert row is not None
        assert row.hash == entry.hash

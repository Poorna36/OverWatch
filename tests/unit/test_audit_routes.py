"""Unit tests for Phase 4 audit REST routes (control_plane.api.audit)."""

from __future__ import annotations

from pathlib import Path

import pytest
from control_plane.api.deps import derive_owner_id, get_session_factory
from control_plane.api.main import app
from control_plane.audit.audit_writer import GENESIS_PREV_HASH, AuditWriter
from control_plane.contracts import ErrorCode
from control_plane.db.models import AuditEntryModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from fastapi.testclient import TestClient
from sqlalchemy import text

TEST_TOKEN = "test-audit-app-token"
SESSION_OWNED = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_EMPTY = "s_01ABCDEFGHJKMNPTVW23456789"
SESSION_OTHER = "s_01ABCDEFGHJKMNPTVW98765432"
SESSION_MISSING = "s_01ABCDEFGHJKMNPTVW00000000"


@pytest.fixture()
def audit_api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create an isolated temp SQLite DB with seeded sessions and AuditWriter."""
    monkeypatch.setenv("APP_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "api_audit.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    writer = AuditWriter(factory)

    owner_id = derive_owner_id(TEST_TOKEN)
    with factory() as db:
        for sid, oid in [
            (SESSION_OWNED, owner_id),
            (SESSION_EMPTY, owner_id),
            (SESSION_OTHER, "owner_someone_else"),
        ]:
            db.add(
                SessionModel(
                    session_id=sid,
                    state="ACTIVE",
                    profile="coding",
                    owner_id=oid,
                    dry_run=0,
                    created_at="2026-11-26T12:00:00Z",
                    updated_at="2026-11-26T12:00:00Z",
                )
            )
        db.commit()

    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        client = TestClient(app)
        yield {
            "client": client,
            "engine": engine,
            "factory": factory,
            "writer": writer,
            "headers": {"Authorization": f"Bearer {TEST_TOKEN}"},
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_entries(writer: AuditWriter, session_id: str = SESSION_OWNED):
    e1 = writer.append(
        session_id=session_id,
        tool="files.read",
        args={"path": "README.md", "api_key": "secret-123"},
        decision="allowed",
        reason="tier=read -> allow",
        tier="read",
        duration_ms=5.0,
    )
    e2 = writer.append(
        session_id=session_id,
        tool="terminal.run",
        args={"command": "rm -rf /"},
        decision="blocked",
        reason="DENY_PATTERN",
        tier="reversible",
    )
    e3 = writer.append(
        session_id=session_id,
        tool="files.write",
        args={"path": "a.txt", "content": "full file body"},
        decision="allowed",
        reason="tier=reversible -> allow",
        tier="reversible",
        duration_ms=12.5,
    )
    return [e1, e2, e3]


def test_audit_endpoints_require_valid_bearer_token(
    audit_api_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three audit endpoints reject missing, wrong, or unset APP_TOKEN."""
    client: TestClient = audit_api_env["client"]
    urls = [
        f"/api/v1/sessions/{SESSION_OWNED}/audit",
        f"/api/v1/sessions/{SESSION_OWNED}/receipt",
        f"/api/v1/audit/verify/{SESSION_OWNED}",
    ]

    for url in urls:
        for headers in [{}, {"Authorization": "Bearer wrong"}]:
            resp = client.get(url, headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    monkeypatch.delenv("APP_TOKEN", raising=False)
    for url in urls:
        resp = client.get(url, headers=audit_api_env["headers"])
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.AUTH_ERROR


def test_non_owned_and_missing_sessions_return_identical_404(audit_api_env) -> None:
    """Non-owned sessions return the exact same 404 shape as missing sessions."""
    client: TestClient = audit_api_env["client"]
    headers = audit_api_env["headers"]
    _seed_entries(audit_api_env["writer"], SESSION_OTHER)

    pairs = [
        (
            f"/api/v1/sessions/{SESSION_OTHER}/audit",
            f"/api/v1/sessions/{SESSION_MISSING}/audit",
        ),
        (
            f"/api/v1/sessions/{SESSION_OTHER}/receipt",
            f"/api/v1/sessions/{SESSION_MISSING}/receipt",
        ),
        (
            f"/api/v1/audit/verify/{SESSION_OTHER}",
            f"/api/v1/audit/verify/{SESSION_MISSING}",
        ),
    ]
    for other_url, missing_url in pairs:
        r_other = client.get(other_url, headers=headers)
        r_missing = client.get(missing_url, headers=headers)
        assert r_other.status_code == 404
        assert r_missing.status_code == 404
        assert r_other.json() == r_missing.json()
        assert r_other.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE


def test_list_audit_filtering_cursor_and_validation(audit_api_env) -> None:
    """GET /audit filters by decision, applies exclusive after_seq, rejects bad args."""
    client: TestClient = audit_api_env["client"]
    headers = audit_api_env["headers"]
    _seed_entries(audit_api_env["writer"])

    # Empty query params (?decision=&after_seq=) return all entries
    r_all = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/audit?decision=&after_seq=",
        headers=headers,
    )
    assert r_all.status_code == 200
    entries = r_all.json()["entries"]
    assert [e["seq"] for e in entries] == [1, 2, 3]
    # Redaction preserved
    assert "api_key" not in entries[0]["args_redacted"]
    assert entries[2]["args_redacted"]["content"].startswith("[REDACTED ")

    # Filter by decision
    r_blocked = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/audit?decision=blocked",
        headers=headers,
    )
    assert r_blocked.status_code == 200
    assert [e["seq"] for e in r_blocked.json()["entries"]] == [2]

    # Exclusive after_seq cursor (seq > after_seq)
    r_after = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/audit?after_seq=1",
        headers=headers,
    )
    assert r_after.status_code == 200
    assert [e["seq"] for e in r_after.json()["entries"]] == [2, 3]

    # Combined filter + cursor
    r_combo = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/audit?decision=allowed&after_seq=1",
        headers=headers,
    )
    assert r_combo.status_code == 200
    assert [e["seq"] for e in r_combo.json()["entries"]] == [3]

    # Valid 'asked' decision filter from AuditDecision returns 200 (empty here)
    r_asked = client.get(
        f"/api/v1/sessions/{SESSION_OWNED}/audit?decision=asked",
        headers=headers,
    )
    assert r_asked.status_code == 200
    assert r_asked.json() == {"entries": []}

    # Invalid decision and negative/non-int after_seq
    for bad_query in [
        "decision=bogus",
        "after_seq=-1",
        "after_seq=x",
    ]:
        r_bad = client.get(
            f"/api/v1/sessions/{SESSION_OWNED}/audit?{bad_query}",
            headers=headers,
        )
        assert r_bad.status_code == 400
        assert r_bad.json()["error"]["code"] == ErrorCode.ARG_INVALID


def test_receipt_contents_and_empty_chain(audit_api_env) -> None:
    """GET /receipt returns entries and chain_head_hash (or GENESIS_PREV_HASH)."""
    client: TestClient = audit_api_env["client"]
    headers = audit_api_env["headers"]
    seeded = _seed_entries(audit_api_env["writer"])

    r_pop = client.get(f"/api/v1/sessions/{SESSION_OWNED}/receipt", headers=headers)
    assert r_pop.status_code == 200
    body = r_pop.json()
    assert set(body.keys()) == {"entries", "chain_head_hash"}
    assert [e["seq"] for e in body["entries"]] == [1, 2, 3]
    assert body["chain_head_hash"] == seeded[-1].hash

    r_empty = client.get(f"/api/v1/sessions/{SESSION_EMPTY}/receipt", headers=headers)
    assert r_empty.status_code == 200
    empty_body = r_empty.json()
    assert empty_body == {
        "entries": [],
        "chain_head_hash": GENESIS_PREV_HASH,
    }


def test_verify_audit_valid_and_tampered_chain_readonly(audit_api_env) -> None:
    """GET /audit/verify/{id} omits broken_at when valid and reports first break."""
    client: TestClient = audit_api_env["client"]
    headers = audit_api_env["headers"]
    engine = audit_api_env["engine"]
    _seed_entries(audit_api_env["writer"])

    r_valid = client.get(f"/api/v1/audit/verify/{SESSION_OWNED}", headers=headers)
    assert r_valid.status_code == 200
    assert r_valid.json() == {"valid": True}
    assert "broken_at" not in r_valid.json()

    # Empty chain is also valid
    r_empty = client.get(f"/api/v1/audit/verify/{SESSION_EMPTY}", headers=headers)
    assert r_empty.status_code == 200
    assert r_empty.json() == {"valid": True}

    # Simulate direct DB tampering on seq=2 by temporarily dropping update trigger
    with engine.connect() as conn:
        conn.exec_driver_sql("DROP TRIGGER audit_entries_prevent_update")
        conn.execute(
            text(
                "UPDATE audit_entries SET reason = 'tampered' "
                "WHERE session_id = :sid AND seq = 2"
            ),
            {"sid": SESSION_OWNED},
        )
        conn.commit()

    r_tampered = client.get(f"/api/v1/audit/verify/{SESSION_OWNED}", headers=headers)
    assert r_tampered.status_code == 200
    assert r_tampered.json() == {"valid": False, "broken_at": 2}


def test_malformed_stored_audit_row_fails_safely(audit_api_env) -> None:
    """Malformed stored audit rows fail safely with 500 UPSTREAM_ERROR."""
    client: TestClient = audit_api_env["client"]
    headers = audit_api_env["headers"]
    factory = audit_api_env["factory"]

    with factory() as db:
        db.add(
            AuditEntryModel(
                session_id=SESSION_OWNED,
                seq=1,
                ts="2026-11-26T12:00:00Z",
                tool="files.read",
                args_redacted="{not-valid-json",
                decision="allowed",
                reason="ok",
                tier="read",
                prev_hash=GENESIS_PREV_HASH,
                hash="0" * 64,
            )
        )
        db.commit()

    for url in [
        f"/api/v1/sessions/{SESSION_OWNED}/audit",
        f"/api/v1/sessions/{SESSION_OWNED}/receipt",
    ]:
        resp = client.get(url, headers=headers)
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == ErrorCode.UPSTREAM_ERROR

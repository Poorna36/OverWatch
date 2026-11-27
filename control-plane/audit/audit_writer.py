"""Append-only, hash-chained audit writer (Phase 2, F-07).

Persists gateway decisions into the SQLite ``audit_entries`` table using the
``AuditEntry`` contract and ``AuditEntryModel`` ORM mapping.

Implementation details where specs leave choices open:
- **Genesis ``prev_hash``**: Entry ``seq == 1`` for a session uses 64 lowercase
  ASCII zeros (``"0" * 64``), matching standard SHA-256 hex digest length.
- **Canonical JSON**: ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)`` encoded as UTF-8.
- **Hash formula**: ``sha256(prev_hash + canonical_json(entry without hash))``
  over all ``AuditEntry`` fields except ``hash`` (with ``duration_ms`` stored
  and hashed as ``float`` when non-null so SQLite ``REAL`` round-trips identically).
- **Redaction & sanitization**:
  - Keys matching ``(token|secret|password|authorization|api[_-]?key)``
    (case-insensitive) are stripped recursively from ``args_redacted``.
  - Keys representing file or email bodies (e.g. ``content``, ``body``,
    ``html_body``, ``file_content``) are replaced with a byte-count placeholder
    so full file/email bodies are never persisted regardless of length.
  - All surviving strings longer than 200 characters are truncated to 200
    characters (197 chars + ``"..."``).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_plane.contracts import AuditDecision, AuditEntry, Tier
from control_plane.db.models import AuditEntryModel, SessionModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

# 64-char zero hex genesis prev_hash for seq == 1 of each session audit chain
GENESIS_PREV_HASH = "0" * 64
SHA256_HEX_LENGTH = 64

MAX_ARG_STRING_LEN = 200
TRUNCATION_SUFFIX = "..."

# Case-insensitive key regex per docs/SECURITY.md §5
SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|api[_-]?key)",
    re.IGNORECASE,
)

# File and email body arg keys whose full values must never be persisted (§11)
BODY_KEY_PATTERN = re.compile(
    r"^(content|file_content|body|email_body|html_body|text_body|"
    r"raw_body|message_body|body_html|body_text|raw_email)$",
    re.IGNORECASE,
)


class AuditWriterError(Exception):
    """Raised when an audit entry cannot be validated or persisted safely."""


def format_utc_iso(dt: datetime | None = None) -> str:
    """Return a UTC ISO 8601 timestamp string."""
    now = datetime.now(UTC) if dt is None else dt.astimezone(UTC)
    return now.isoformat().replace("+00:00", "Z")


def canonical_json(data: Any) -> str:
    """Serialize data into deterministic compact canonical JSON."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def truncate_string(value: str, max_len: int = MAX_ARG_STRING_LEN) -> str:
    """Truncate strings strictly exceeding max_len while staying within max_len."""
    if len(value) <= max_len:
        return value
    if max_len <= len(TRUNCATION_SUFFIX):
        return value[:max_len]
    return value[: max_len - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


_REDACTED_BODY_RE = re.compile(r"^\[REDACTED(?: \d+ bytes)?\]$")


def _redact_body_value(value: Any) -> str:
    """Return a short non-sensitive placeholder for file/email body arguments."""
    if isinstance(value, str):
        if _REDACTED_BODY_RE.fullmatch(value):
            return value
        byte_len = len(value.encode("utf-8"))
        return f"[REDACTED {byte_len} bytes]"
    if isinstance(value, (bytes, bytearray)):
        return f"[REDACTED {len(value)} bytes]"
    return "[REDACTED]"


def _sanitize_value(value: Any) -> Any:
    """Recursively sanitize non-secret values and enforce 200-char string limits."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AuditWriterError(
                f"Non-finite float value is not permitted in audit args: {value!r}"
            )
        return value
    if isinstance(value, str):
        return truncate_string(value)
    if isinstance(value, (bytes, bytearray)):
        return f"[REDACTED {len(value)} bytes]"
    if isinstance(value, Path):
        return truncate_string(str(value))
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    raise AuditWriterError(
        f"Unsupported argument type in audit log: {type(value).__name__}"
    )


def _sanitize_mapping(mapping: Mapping[Any, Any]) -> dict[str, Any]:
    """Strip secret keys, redact file/email body fields, and truncate strings."""
    cleaned: dict[str, Any] = {}
    for raw_key, val in mapping.items():
        if not isinstance(raw_key, str):
            raise AuditWriterError(
                f"Audit argument keys must be strings, got {type(raw_key).__name__}."
            )
        if SECRET_KEY_PATTERN.search(raw_key):
            continue
        if BODY_KEY_PATTERN.match(raw_key):
            cleaned[raw_key] = _redact_body_value(val)
            continue
        cleaned[raw_key] = _sanitize_value(val)
    return cleaned


def sanitize_audit_args(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Sanitize tool call arguments for ``AuditEntry.args_redacted`` persistence.

    Rules enforced:
    1. Strip any key (recursively) matching
       ``(token|secret|password|authorization|api[_-]?key)``.
    2. Never persist full file or email bodies (keys such as ``content`` or
       ``body`` are replaced with a byte-count placeholder).
    3. Truncate every surviving string longer than 200 characters to 200 chars.
    """
    if args is None:
        return {}
    if not isinstance(args, Mapping):
        raise AuditWriterError(
            f"Audit args must be a mapping or None, got {type(args).__name__}."
        )
    return _sanitize_mapping(args)


def audit_entry_hash_payload(
    *,
    seq: int,
    session_id: str,
    ts: str,
    tool: str,
    args_redacted: dict[str, Any],
    decision: AuditDecision,
    reason: str,
    tier: Tier | None,
    undo_id: str | None,
    duration_ms: float | None,
    prev_hash: str,
) -> dict[str, Any]:
    """Build canonical entry mapping (all ``AuditEntry`` fields except ``hash``)."""
    norm_duration: float | None = None
    if duration_ms is not None:
        norm_duration = float(duration_ms)
        if not math.isfinite(norm_duration):
            raise AuditWriterError(f"duration_ms must be finite, got {duration_ms!r}")

    return {
        "seq": seq,
        "session_id": session_id,
        "ts": ts,
        "tool": tool,
        "args_redacted": args_redacted,
        "decision": decision,
        "reason": reason,
        "tier": tier,
        "undo_id": undo_id,
        "duration_ms": norm_duration,
        "prev_hash": prev_hash,
    }


def compute_audit_hash(
    *,
    seq: int,
    session_id: str,
    ts: str,
    tool: str,
    args_redacted: dict[str, Any],
    decision: AuditDecision,
    reason: str,
    tier: Tier | None,
    undo_id: str | None,
    duration_ms: float | None,
    prev_hash: str,
) -> str:
    """Compute ``sha256(prev_hash + canonical_json(entry without hash))``."""
    payload = audit_entry_hash_payload(
        seq=seq,
        session_id=session_id,
        ts=ts,
        tool=tool,
        args_redacted=args_redacted,
        decision=decision,
        reason=reason,
        tier=tier,
        undo_id=undo_id,
        duration_ms=duration_ms,
        prev_hash=prev_hash,
    )
    canonical = canonical_json(payload)
    digest_input = (prev_hash + canonical).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def _begin_immediate_if_sqlite(db: Session) -> None:
    """Acquire SQLite write lock before sequence lookup when starting a txn."""
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "sqlite" and not db.in_transaction():
        db.execute(text("PRAGMA busy_timeout = 5000"))
        db.execute(text("BEGIN IMMEDIATE"))


def append_audit_entry(
    db: Session,
    *,
    session_id: str,
    tool: str,
    args: Mapping[str, Any] | None = None,
    decision: AuditDecision,
    reason: str,
    tier: Tier | None = None,
    undo_id: str | None = None,
    duration_ms: float | None = None,
    ts: str | None = None,
    commit: bool = True,
) -> AuditEntry:
    """Append a single hash-chained audit entry for ``session_id`` transactionally.

    Reads the latest sequence number and hash for the session, computes
    ``seq = last_seq + 1`` (or ``1`` with ``GENESIS_PREV_HASH``), sanitizes
    ``args`` into ``args_redacted``, computes ``hash``, validates against the
    ``AuditEntry`` contract, and inserts a new ``AuditEntryModel`` row.

    Raises:
        AuditWriterError: On invalid arguments, missing session, contract
            validation failure, or database error (rolls back when ``commit=True``).
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise AuditWriterError("session_id must be a non-empty string.")
    if not isinstance(tool, str) or not tool.strip():
        raise AuditWriterError("tool must be a non-empty string.")
    if not isinstance(reason, str) or not reason.strip():
        raise AuditWriterError("reason must be a non-empty string.")

    entry_ts = format_utc_iso() if ts is None else ts
    if not isinstance(entry_ts, str) or not entry_ts.strip():
        raise AuditWriterError("ts must be a non-empty UTC ISO 8601 string.")

    sanitized_args = sanitize_audit_args(args)
    args_json = canonical_json(sanitized_args)

    norm_duration: float | None = None
    if duration_ms is not None:
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
            raise AuditWriterError(
                f"duration_ms must be numeric or None, "
                f"got {type(duration_ms).__name__}."
            )
        norm_duration = float(duration_ms)
        if not math.isfinite(norm_duration) or norm_duration < 0:
            raise AuditWriterError(
                f"duration_ms must be non-negative and finite, got {duration_ms!r}."
            )

    try:
        if commit:
            _begin_immediate_if_sqlite(db)

        last_row = db.scalar(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == session_id)
            .order_by(AuditEntryModel.seq.desc())
            .limit(1)
        )

        if last_row is None:
            session_exists = db.scalar(
                select(SessionModel.session_id).where(
                    SessionModel.session_id == session_id
                )
            )
            if session_exists is None:
                raise AuditWriterError(
                    f"Cannot append audit entry: session '{session_id}' does not exist."
                )
            next_seq = 1
            prev_hash = GENESIS_PREV_HASH
        else:
            next_seq = last_row.seq + 1
            prev_hash = last_row.hash

        entry_hash = compute_audit_hash(
            seq=next_seq,
            session_id=session_id,
            ts=entry_ts,
            tool=tool,
            args_redacted=sanitized_args,
            decision=decision,
            reason=reason,
            tier=tier,
            undo_id=undo_id,
            duration_ms=norm_duration,
            prev_hash=prev_hash,
        )

        contract_entry = AuditEntry.model_validate(
            {
                "seq": next_seq,
                "session_id": session_id,
                "ts": entry_ts,
                "tool": tool,
                "args_redacted": sanitized_args,
                "decision": decision,
                "reason": reason,
                "tier": tier,
                "undo_id": undo_id,
                "duration_ms": norm_duration,
                "prev_hash": prev_hash,
                "hash": entry_hash,
            }
        )

        db_row = AuditEntryModel(
            session_id=contract_entry.session_id,
            seq=contract_entry.seq,
            ts=contract_entry.ts,
            tool=contract_entry.tool,
            args_redacted=args_json,
            decision=contract_entry.decision,
            reason=contract_entry.reason,
            tier=contract_entry.tier,
            undo_id=contract_entry.undo_id,
            duration_ms=contract_entry.duration_ms,
            prev_hash=contract_entry.prev_hash,
            hash=contract_entry.hash,
        )
        db.add(db_row)
        db.flush()
        if commit:
            db.commit()
        return contract_entry

    except AuditWriterError:
        if commit:
            db.rollback()
        raise
    except (SQLAlchemyError, ValueError, TypeError) as exc:
        if commit:
            db.rollback()
        raise AuditWriterError(
            f"Failed to append audit entry for session '{session_id}': {exc}"
        ) from exc


class AuditWriter:
    """Transactional append-only writer bound to a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(
        self,
        *,
        session_id: str,
        tool: str,
        args: Mapping[str, Any] | None = None,
        decision: AuditDecision,
        reason: str,
        tier: Tier | None = None,
        undo_id: str | None = None,
        duration_ms: float | None = None,
        ts: str | None = None,
    ) -> AuditEntry:
        """Append a sanitized, hash-chained audit entry in its own transaction."""
        with self._session_factory() as db:
            return append_audit_entry(
                db,
                session_id=session_id,
                tool=tool,
                args=args,
                decision=decision,
                reason=reason,
                tier=tier,
                undo_id=undo_id,
                duration_ms=duration_ms,
                ts=ts,
                commit=True,
            )

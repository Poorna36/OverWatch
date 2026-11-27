"""Read-only verification of per-session hash-chained audit logs (Phase 2, F-07).

Verifies sequence contiguity, ``prev_hash`` linking back to
``GENESIS_PREV_HASH``, contract schema validity, and row SHA-256 hashes without
modifying any stored database records.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from control_plane.audit.audit_writer import (
    GENESIS_PREV_HASH,
    canonical_json,
    compute_audit_hash,
)
from control_plane.contracts import AuditEntry
from control_plane.db.models import AuditEntryModel, SessionModel
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AuditChainIssue:
    """Describes a single detected integrity failure in a session audit chain."""

    seq: int | None
    code: str
    message: str
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class AuditVerificationResult:
    """Result summary for verifying a session's hash-chained audit trail."""

    session_id: str
    valid: bool
    entry_count: int
    first_tampered_seq: int | None = None
    latest_hash: str | None = None
    issues: list[AuditChainIssue] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        """Return the first detected error message, or None when valid."""
        return self.issues[0].message if self.issues else None


def _parse_entry_args(
    raw_args: dict[str, Any] | str,
    *,
    seq: int,
) -> tuple[dict[str, Any] | None, AuditChainIssue | None]:
    """Parse stored ``args_redacted`` without modifying the stored value."""
    if isinstance(raw_args, dict):
        return raw_args, None
    if not isinstance(raw_args, str):
        return None, AuditChainIssue(
            seq=seq,
            code="INVALID_ARGS_JSON",
            message=f"seq {seq}: args_redacted must be JSON text or dict.",
        )
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return None, AuditChainIssue(
            seq=seq,
            code="INVALID_ARGS_JSON",
            message=f"seq {seq}: malformed args_redacted JSON: {exc}",
        )
    if not isinstance(parsed, dict):
        return None, AuditChainIssue(
            seq=seq,
            code="INVALID_ARGS_JSON",
            message=(
                f"seq {seq}: args_redacted must decode to a JSON object, "
                f"got {type(parsed).__name__}."
            ),
        )
    return parsed, None


def verify_audit_entries(
    entries: Sequence[AuditEntryModel | AuditEntry],
    *,
    session_id: str,
) -> AuditVerificationResult:
    """Verify sequence numbers, chain links, and SHA-256 hashes over entries.

    Pure read-only verification; does not modify entries or database state.
    """
    issues: list[AuditChainIssue] = []
    expected_seq = 1
    expected_prev_hash = GENESIS_PREV_HASH
    latest_hash: str | None = None

    for entry in entries:
        seq = entry.seq
        entry_session_id = entry.session_id

        if entry_session_id != session_id:
            issues.append(
                AuditChainIssue(
                    seq=seq,
                    code="SESSION_ID_MISMATCH",
                    message=(
                        f"seq {seq}: row session_id {entry_session_id!r} does not "
                        f"match expected {session_id!r}."
                    ),
                    expected=session_id,
                    actual=entry_session_id,
                )
            )

        if seq != expected_seq:
            issues.append(
                AuditChainIssue(
                    seq=seq,
                    code="SEQUENCE_MISMATCH",
                    message=(
                        f"Expected sequence number {expected_seq}, got {seq} "
                        "(gap, duplicate, or out-of-order entry)."
                    ),
                    expected=str(expected_seq),
                    actual=str(seq),
                )
            )

        if not hmac.compare_digest(str(entry.prev_hash), expected_prev_hash):
            issue_code = (
                "GENESIS_HASH_MISMATCH" if expected_seq == 1 else "PREV_HASH_MISMATCH"
            )
            issues.append(
                AuditChainIssue(
                    seq=seq,
                    code=issue_code,
                    message=(
                        f"seq {seq}: prev_hash does not match expected "
                        f"{'genesis' if expected_seq == 1 else 'preceding entry'} hash."
                    ),
                    expected=expected_prev_hash,
                    actual=str(entry.prev_hash),
                )
            )

        args_dict, parse_issue = _parse_entry_args(entry.args_redacted, seq=seq)
        if parse_issue is not None:
            issues.append(parse_issue)
        elif args_dict is not None:
            if isinstance(entry, AuditEntryModel):
                # Also verify stored text matches canonical JSON representation
                expected_args_text = canonical_json(args_dict)
                if entry.args_redacted != expected_args_text:
                    issues.append(
                        AuditChainIssue(
                            seq=seq,
                            code="NON_CANONICAL_ARGS",
                            message=f"seq {seq}: args_redacted is not canonical JSON.",
                            expected=expected_args_text,
                            actual=entry.args_redacted,
                        )
                    )

            try:
                validated = AuditEntry.model_validate(
                    {
                        "seq": seq,
                        "session_id": entry_session_id,
                        "ts": entry.ts,
                        "tool": entry.tool,
                        "args_redacted": args_dict,
                        "decision": entry.decision,
                        "reason": entry.reason,
                        "tier": entry.tier,
                        "undo_id": entry.undo_id,
                        "duration_ms": entry.duration_ms,
                        "prev_hash": entry.prev_hash,
                        "hash": entry.hash,
                    }
                )
            except ValidationError as exc:
                issues.append(
                    AuditChainIssue(
                        seq=seq,
                        code="INVALID_CONTRACT",
                        message=(
                            f"seq {seq}: AuditEntry contract validation failed: {exc}"
                        ),
                    )
                )
            else:
                expected_hash = compute_audit_hash(
                    seq=validated.seq,
                    session_id=validated.session_id,
                    ts=validated.ts,
                    tool=validated.tool,
                    args_redacted=validated.args_redacted,
                    decision=validated.decision,
                    reason=validated.reason,
                    tier=validated.tier,
                    undo_id=validated.undo_id,
                    duration_ms=validated.duration_ms,
                    prev_hash=validated.prev_hash,
                )
                if not hmac.compare_digest(validated.hash, expected_hash):
                    issues.append(
                        AuditChainIssue(
                            seq=seq,
                            code="HASH_MISMATCH",
                            message=(
                                f"seq {seq}: entry hash mismatch "
                                "(record contents tampered or corrupted)."
                            ),
                            expected=expected_hash,
                            actual=validated.hash,
                        )
                    )

        latest_hash = str(entry.hash)
        expected_prev_hash = latest_hash
        expected_seq = (
            seq if isinstance(seq, int) and seq >= expected_seq else expected_seq
        ) + 1

    first_tampered = issues[0].seq if issues else None
    return AuditVerificationResult(
        session_id=session_id,
        valid=len(issues) == 0,
        entry_count=len(entries),
        first_tampered_seq=first_tampered,
        latest_hash=latest_hash,
        issues=issues,
    )


def verify_session_audit(
    db: Session,
    session_id: str,
    *,
    require_session_exists: bool = False,
) -> AuditVerificationResult:
    """Query and verify the audit chain for ``session_id`` read-only."""
    if require_session_exists:
        session_exists = db.scalar(
            select(SessionModel.session_id).where(SessionModel.session_id == session_id)
        )
        if session_exists is None:
            return AuditVerificationResult(
                session_id=session_id,
                valid=False,
                entry_count=0,
                first_tampered_seq=None,
                issues=[
                    AuditChainIssue(
                        seq=None,
                        code="SESSION_NOT_FOUND",
                        message=f"Session '{session_id}' does not exist.",
                    )
                ],
            )

    rows = list(
        db.scalars(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == session_id)
            .order_by(AuditEntryModel.seq.asc())
        )
    )
    return verify_audit_entries(rows, session_id=session_id)


def verify_all_audits(db: Session) -> dict[str, AuditVerificationResult]:
    """Verify hash chains for every session present in DB read-only."""
    session_ids = set(db.scalars(select(SessionModel.session_id)))
    audit_session_ids = set(db.scalars(select(AuditEntryModel.session_id).distinct()))
    all_ids = sorted(session_ids | audit_session_ids)
    return {
        sid: verify_session_audit(db, sid, require_session_exists=False)
        for sid in all_ids
    }


verify_audit = verify_session_audit

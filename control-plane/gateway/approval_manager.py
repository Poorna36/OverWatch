"""Phase 4 approval lifecycle manager (F-06).

Manages persistent human-in-the-loop approval cards backed by the SQLite
``approvals`` table, transactionally linked hash-chained ``audit_entries``
outcomes (``"asked_approved"`` / ``"asked_denied"``), and sequenced WebSocket
events via ``EventBus`` (``approval_request`` / ``approval_resolved``).

Security & consistency guarantees:
- Strictly validates and generates ``a_``-prefixed Crockford Base32 ULIDs.
- Reuses already-redacted ``PendingApprovalData.args_redacted`` (defensively
  re-sanitized) and stores only existing ``ApprovalModel`` / ``ApprovalRequest``
  fields.
- Never executes tools; approval resolution records user or timeout decisions
  only. Any subsequent tool execution must re-enter gateway preflight.
- Resolution status updates and resolved audit entries commit in a single
  SQLite transaction under an in-process write lock and ``BEGIN IMMEDIATE``;
  an audit persistence failure rolls back the approval update.
- Event publication happens immediately after DB commit. Because SQLite and
  ``EventBus`` execute in separate commits, if ``EventBus.publish`` fails after
  the approval transaction commits, the committed approval/audit rows are NOT
  rolled back and ``ApprovalEventPublishError`` is raised with the committed
  state attached (at-least-once / fail-loud semantics, not silently undone).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from control_plane.audit.audit_writer import (
    AuditWriterError,
    _begin_immediate_if_sqlite,
    append_audit_entry,
    canonical_json,
    format_utc_iso,
    sanitize_audit_args,
    truncate_string,
)
from control_plane.contracts import (
    ApprovalRequest,
    ApprovalResponse,
    AuditDecision,
    AuditEntry,
    EventEnvelope,
    Policy,
    Tier,
)
from control_plane.db.models import ApprovalModel, SessionModel
from control_plane.events.event_bus import EventBus, EventBusError
from control_plane.gateway.pipeline import PendingApprovalData
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

APPROVAL_ID_PREFIX = "a_"
ULID_LENGTH = 26
ULID_REGEX = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_FORBIDDEN_ID_CHARS = frozenset("\x00\n\r\t/\\ ")

ApprovalStatus = Literal["pending", "approved", "denied", "timed_out"]
ApprovalResolver = Literal["user", "timeout"]


class ApprovalManagerError(Exception):
    """Base error for fail-closed approval lifecycle failures."""


class InvalidApprovalIdError(ApprovalManagerError):
    """Raised when an approval ID fails strict prefixed-ULID validation."""


class ApprovalNotFoundError(ApprovalManagerError):
    """Raised when a referenced approval ID does not exist."""


class ApprovalAlreadyResolvedError(ApprovalManagerError):
    """Raised when attempting to resolve a non-pending approval."""


class ApprovalEventPublishError(ApprovalManagerError):
    """Raised when DB commit succeeded but EventBus publication failed.

    Persisted DB state (approval creation or resolution + audit entry) is
    deliberately preserved; callers must treat event delivery as incomplete
    rather than assuming rollback or exactly-once delivery.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: Exception,
        committed_result: Any = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.committed_result = committed_result


@dataclass(frozen=True)
class ApprovalCreationResult:
    """Persisted approval card and emitted ``approval_request`` event."""

    approval: ApprovalRequest
    event: EventEnvelope[dict[str, Any]]


@dataclass(frozen=True)
class ApprovalResolutionResult:
    """Resolved approval state, hash-chained audit entry, and emitted event."""

    approval_id: str
    session_id: str
    status: Literal["approved", "denied", "timed_out"]
    approved: bool
    resolved_by: ApprovalResolver
    resolved_at: str
    timed_out: bool
    audit_entry: AuditEntry
    event: EventEnvelope[dict[str, Any]] | None = None


def _encode_crockford_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_approval_id(now: datetime | None = None) -> str:
    """Generate a contract-compliant ``a_``-prefixed 26-char ULID."""
    dt = _normalize_utc(datetime.now(UTC) if now is None else now)
    timestamp_ms = int(dt.timestamp() * 1000)
    if timestamp_ms < 0 or timestamp_ms >= (1 << 48):
        raise ApprovalManagerError("Timestamp outside valid 48-bit ULID range.")
    ts_part = _encode_crockford_base32(timestamp_ms, 10)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_part = _encode_crockford_base32(rand_int, 16)
    return f"{APPROVAL_ID_PREFIX}{ts_part}{rand_part}"


def validate_approval_id(approval_id: Any) -> str:
    """Validate and canonicalize an ``a_``-prefixed ULID, failing closed."""
    if not isinstance(approval_id, str):
        raise InvalidApprovalIdError(
            f"Approval ID must be a string, got {type(approval_id).__name__}."
        )
    if not approval_id:
        raise InvalidApprovalIdError("Approval ID must not be empty.")
    if any(ch in _FORBIDDEN_ID_CHARS for ch in approval_id):
        raise InvalidApprovalIdError(
            f"Approval ID contains forbidden characters: {approval_id!r}"
        )
    if ".." in approval_id:
        raise InvalidApprovalIdError(
            f"Path traversal sequence detected in approval ID: {approval_id!r}"
        )
    if not approval_id.startswith(APPROVAL_ID_PREFIX):
        raise InvalidApprovalIdError(
            f"Approval ID must start with {APPROVAL_ID_PREFIX!r}: {approval_id!r}"
        )

    ulid_part = approval_id[len(APPROVAL_ID_PREFIX) :]
    if len(ulid_part) != ULID_LENGTH:
        raise InvalidApprovalIdError(
            f"Approval ID must be 28 chars ('a_' + 26-char ULID), "
            f"got {len(approval_id)}: {approval_id!r}"
        )

    upper_ulid = ulid_part.upper()
    if not ULID_REGEX.match(upper_ulid):
        raise InvalidApprovalIdError(
            f"Approval ID has invalid ULID format: {approval_id!r}"
        )
    return f"{APPROVAL_ID_PREFIX}{upper_ulid}"


def _normalize_utc(dt: datetime) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ApprovalManagerError("Timestamps must be timezone-aware datetimes.")
    return dt.astimezone(UTC)


def _parse_utc_iso(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ApprovalManagerError(f"Invalid UTC ISO timestamp: {raw!r}")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ApprovalManagerError(f"Invalid UTC ISO timestamp {raw!r}: {exc}") from exc
    return _normalize_utc(parsed)


def _parse_stored_args(raw_json: str, approval_id: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ApprovalManagerError(
            f"Corrupt args_redacted JSON for approval '{approval_id}': {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ApprovalManagerError(
            f"Stored args_redacted for '{approval_id}' must be a JSON object."
        )
    return parsed


def _parse_stored_warnings(raw_json: str | None, approval_id: str) -> list[str] | None:
    if raw_json is None:
        return None
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ApprovalManagerError(
            f"Corrupt warnings JSON for approval '{approval_id}': {exc}"
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise ApprovalManagerError(
            f"Stored warnings for '{approval_id}' must be a JSON list of strings."
        )
    return parsed


def _sanitize_warnings(warnings: list[str] | None) -> list[str] | None:
    if warnings is None:
        return None
    if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        raise ApprovalManagerError("warnings must be a list of strings or None.")
    return [truncate_string(w) for w in warnings]


class ApprovalManager:
    """Transactional persistence and event coordination for approval cards."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_bus: EventBus,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable.")
        if not hasattr(event_bus, "publish") or not callable(event_bus.publish):
            raise TypeError("event_bus must provide a callable publish(...) method.")
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._write_lock = threading.Lock()

    def _current_utc(self, now: datetime | None) -> datetime:
        return _normalize_utc(self._clock() if now is None else now)

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        """Load and validate an existing ``ApprovalRequest`` by ID."""
        valid_id = validate_approval_id(approval_id)
        with self._session_factory() as db:
            row = db.get(ApprovalModel, valid_id)
            if row is None:
                raise ApprovalNotFoundError(f"Approval '{valid_id}' does not exist.")
            args_redacted = _parse_stored_args(row.args_redacted, valid_id)
            warnings = _parse_stored_warnings(row.warnings, valid_id)
            return ApprovalRequest(
                approval_id=row.approval_id,
                session_id=row.session_id,
                call_id=row.call_id,
                tool=row.tool,
                tier=row.tier,  # type: ignore[arg-type]
                args_redacted=args_redacted,
                reason=row.reason,
                irreversible=bool(row.irreversible),
                warnings=warnings,
                expires_at=row.expires_at,
            )

    def create_approval(
        self,
        pending: PendingApprovalData,
        *,
        policy: Policy,
        warnings: list[str] | None = None,
        approval_id: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalCreationResult:
        """Persist a new pending approval card and emit ``approval_request``."""
        if not isinstance(pending, PendingApprovalData):
            raise TypeError(
                f"Expected PendingApprovalData, got {type(pending).__name__}."
            )
        if not isinstance(policy, Policy):
            raise TypeError(f"Expected contract Policy, got {type(policy).__name__}.")

        timeout_s = policy.defaults.approval_timeout_s
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, int)
            or timeout_s <= 0
        ):
            raise ApprovalManagerError(
                "Policy approval_timeout_s must be a positive integer, "
                f"got {timeout_s!r}."
            )

        now_dt = self._current_utc(now)
        expires_at = format_utc_iso(now_dt + timedelta(seconds=timeout_s))
        valid_id = (
            generate_approval_id(now_dt)
            if approval_id is None
            else validate_approval_id(approval_id)
        )
        clean_warnings = _sanitize_warnings(warnings)

        try:
            sanitized_args = sanitize_audit_args(pending.args_redacted)
            request = ApprovalRequest(
                approval_id=valid_id,
                session_id=pending.session_id,
                call_id=pending.call_id,
                tool=pending.tool,
                tier=pending.tier,
                args_redacted=sanitized_args,
                reason=truncate_string(pending.reason),
                irreversible=pending.irreversible,
                warnings=clean_warnings,
                expires_at=expires_at,
            )
            args_json = canonical_json(dict(request.args_redacted))
            warnings_json = (
                None
                if request.warnings is None
                else canonical_json(list(request.warnings))
            )
        except (AuditWriterError, ValidationError, TypeError, ValueError) as exc:
            raise ApprovalManagerError(f"Invalid approval payload: {exc}") from exc

        with self._write_lock:
            with self._session_factory() as db:
                try:
                    _begin_immediate_if_sqlite(db)
                    session_exists = db.scalar(
                        select(SessionModel.session_id).where(
                            SessionModel.session_id == request.session_id
                        )
                    )
                    if session_exists is None:
                        raise ApprovalManagerError(
                            f"Cannot create approval: session '{request.session_id}' "
                            "does not exist."
                        )

                    db.add(
                        ApprovalModel(
                            approval_id=request.approval_id,
                            session_id=request.session_id,
                            call_id=request.call_id,
                            tool=request.tool,
                            tier=request.tier,
                            args_redacted=args_json,
                            reason=request.reason,
                            irreversible=1 if request.irreversible else 0,
                            warnings=warnings_json,
                            expires_at=request.expires_at,
                            status="pending",
                            resolved_by=None,
                            resolved_at=None,
                        )
                    )
                    db.flush()
                    db.commit()
                except ApprovalManagerError:
                    db.rollback()
                    raise
                except SQLAlchemyError as exc:
                    db.rollback()
                    raise ApprovalManagerError(
                        f"Failed to persist approval '{request.approval_id}': {exc}"
                    ) from exc

        try:
            event = self._event_bus.publish(
                session_id=request.session_id,
                event_type="approval_request",
                data=request,
            )
        except (EventBusError, Exception) as exc:
            raise ApprovalEventPublishError(
                f"Approval '{request.approval_id}' was persisted, but publishing "
                f"'approval_request' failed: {exc}",
                cause=exc,
                committed_result=request,
            ) from exc

        return ApprovalCreationResult(approval=request, event=event)

    def resolve_approval(
        self,
        response: ApprovalResponse,
        *,
        now: datetime | None = None,
    ) -> ApprovalResolutionResult:
        """Resolve a pending approval once, write audit outcome, and emit event."""
        if not isinstance(response, ApprovalResponse):
            raise TypeError(
                f"Expected contract ApprovalResponse, got {type(response).__name__}."
            )

        valid_id = validate_approval_id(response.approval_id)
        now_dt = self._current_utc(now)
        committed = self._resolve_committed(
            approval_id=valid_id,
            requested_approve=response.approve,
            now_dt=now_dt,
            force_timeout=False,
        )
        return self._publish_resolved_event(committed)

    def expire_due(
        self,
        *,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> list[ApprovalResolutionResult]:
        """Time out all pending approvals whose ``expires_at <= now``."""
        now_dt = self._current_utc(now)

        with self._session_factory() as db:
            stmt = (
                select(ApprovalModel.approval_id, ApprovalModel.expires_at)
                .where(ApprovalModel.status == "pending")
                .order_by(
                    ApprovalModel.expires_at.asc(),
                    ApprovalModel.approval_id.asc(),
                )
            )
            if session_id is not None:
                stmt = stmt.where(ApprovalModel.session_id == session_id)
            candidates = db.execute(stmt).all()

        due_ids: list[str] = []
        for candidate_id, raw_expires_at in candidates:
            if now_dt >= _parse_utc_iso(raw_expires_at):
                due_ids.append(candidate_id)

        results: list[ApprovalResolutionResult] = []
        for candidate_id in due_ids:
            try:
                committed = self._resolve_committed(
                    approval_id=candidate_id,
                    requested_approve=False,
                    now_dt=now_dt,
                    force_timeout=True,
                )
            except ApprovalAlreadyResolvedError:
                # Lost a concurrent resolution race between scan and write lock; skip.
                continue
            results.append(
                self._publish_resolved_event(committed, prior_results=results)
            )

        return results

    def _resolve_committed(
        self,
        *,
        approval_id: str,
        requested_approve: bool,
        now_dt: datetime,
        force_timeout: bool,
    ) -> ApprovalResolutionResult:
        """Atomically update approval status + append hash-chained audit entry."""
        resolved_ts = format_utc_iso(now_dt)

        with self._write_lock:
            with self._session_factory() as db:
                try:
                    _begin_immediate_if_sqlite(db)

                    row = db.get(ApprovalModel, approval_id)
                    if row is None:
                        raise ApprovalNotFoundError(
                            f"Approval '{approval_id}' does not exist."
                        )
                    if row.status != "pending":
                        raise ApprovalAlreadyResolvedError(
                            f"Approval '{approval_id}' is already resolved "
                            f"(status={row.status!r})."
                        )

                    expires_dt = _parse_utc_iso(row.expires_at)
                    timed_out = force_timeout or (now_dt >= expires_dt)

                    if timed_out:
                        next_status: Literal["approved", "denied", "timed_out"] = (
                            "timed_out"
                        )
                        approved = False
                        resolved_by: ApprovalResolver = "timeout"
                        audit_decision: AuditDecision = "asked_denied"
                        audit_reason = "approval timed out"
                    elif requested_approve:
                        next_status = "approved"
                        approved = True
                        resolved_by = "user"
                        audit_decision = "asked_approved"
                        audit_reason = "approved by user"
                    else:
                        next_status = "denied"
                        approved = False
                        resolved_by = "user"
                        audit_decision = "asked_denied"
                        audit_reason = "denied by user"

                    update_res = db.execute(
                        update(ApprovalModel)
                        .where(
                            ApprovalModel.approval_id == approval_id,
                            ApprovalModel.status == "pending",
                        )
                        .values(
                            status=next_status,
                            resolved_by=resolved_by,
                            resolved_at=resolved_ts,
                        )
                    )
                    if update_res.rowcount != 1:
                        raise ApprovalAlreadyResolvedError(
                            f"Approval '{approval_id}' was resolved concurrently."
                        )

                    parsed_args = _parse_stored_args(row.args_redacted, approval_id)
                    tier_val: Tier = row.tier  # type: ignore[assignment]
                    audit_entry = append_audit_entry(
                        db,
                        session_id=row.session_id,
                        tool=row.tool,
                        args=parsed_args,
                        decision=audit_decision,
                        reason=audit_reason,
                        tier=tier_val,
                        ts=resolved_ts,
                        commit=False,
                    )

                    db.commit()
                    return ApprovalResolutionResult(
                        approval_id=row.approval_id,
                        session_id=row.session_id,
                        status=next_status,
                        approved=approved,
                        resolved_by=resolved_by,
                        resolved_at=resolved_ts,
                        timed_out=timed_out,
                        audit_entry=audit_entry,
                        event=None,
                    )
                except (ApprovalManagerError, AuditWriterError) as exc:
                    db.rollback()
                    if isinstance(exc, ApprovalManagerError):
                        raise
                    raise ApprovalManagerError(
                        f"Audit persistence failed while resolving approval "
                        f"'{approval_id}': {exc}"
                    ) from exc
                except SQLAlchemyError as exc:
                    db.rollback()
                    raise ApprovalManagerError(
                        "Database failure while resolving approval "
                        f"'{approval_id}': {exc}"
                    ) from exc

    def _publish_resolved_event(
        self,
        committed: ApprovalResolutionResult,
        *,
        prior_results: list[ApprovalResolutionResult] | None = None,
    ) -> ApprovalResolutionResult:
        """Emit ``approval_resolved`` per CONTRACTS.md §9 after commit."""
        payload: dict[str, Any] = {
            "approval_id": committed.approval_id,
            "approved": committed.approved,
            "by": committed.resolved_by,
        }
        try:
            event = self._event_bus.publish(
                session_id=committed.session_id,
                event_type="approval_resolved",
                data=payload,
                ts=committed.resolved_at,
            )
        except (EventBusError, Exception) as exc:
            raise ApprovalEventPublishError(
                f"Approval '{committed.approval_id}' resolved "
                f"(status={committed.status!r}, "
                f"audit_seq={committed.audit_entry.seq}) and committed, but publishing "
                f"'approval_resolved' failed: {exc}",
                cause=exc,
                committed_result=(
                    committed if prior_results is None else [*prior_results, committed]
                ),
            ) from exc

        return ApprovalResolutionResult(
            approval_id=committed.approval_id,
            session_id=committed.session_id,
            status=committed.status,
            approved=committed.approved,
            resolved_by=committed.resolved_by,
            resolved_at=committed.resolved_at,
            timed_out=committed.timed_out,
            audit_entry=committed.audit_entry,
            event=event,
        )

"""F-13 Undo manager for reversible ``files.write`` operations.

Preserves prior file contents in memory (never persisting raw file bodies into
SQLite), checks for file drift/missing files before restoring, and routes every
restoration through ``AuditedGatewayCoordinator`` so workspace ``PathJail``,
policy checks, and hash-chained audit logging remain enforced.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from control_plane.audit.audit_writer import format_utc_iso
from control_plane.contracts import Profile, SessionState, ToolCall, UndoRecord
from control_plane.db.models import UndoRecordModel
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

UNDO_ID_PREFIX = "u_"
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class UndoError(Exception):
    """Base error for F-13 undo operations."""


class UndoNotFoundError(UndoError):
    """Raised when an undo record or in-memory snapshot is missing."""


class UndoConflictError(UndoError):
    """Raised when target file drifted or disappeared since the original write."""


class UndoPolicyError(UndoError):
    """Raised when gateway policy blocks or requires approval for restoration."""


def _encode_crockford_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_undo_id(now: datetime | None = None) -> str:
    """Generate a contract-valid ``u_``-prefixed 26-character Crockford ULID."""
    dt = datetime.now(UTC) if now is None else now.astimezone(UTC)
    timestamp_ms = int(dt.timestamp() * 1000)
    ts_part = _encode_crockford_base32(timestamp_ms, 10)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_part = _encode_crockford_base32(rand_int, 16)
    return f"{UNDO_ID_PREFIX}{ts_part}{rand_part}"


def sha256_text(content: str) -> str:
    """Compute SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UndoCapture:
    """In-memory snapshot captured immediately before ``files.write``."""

    path: str
    prior_exists: bool
    prior_content: str | None
    written_sha256: str


FileReader = Callable[[str], tuple[bool, str | None]]


class UndoManager:
    """Coordinates pre-write state capture, persistence, and safe restoration."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._lock = threading.Lock()
        self._snapshots: dict[str, UndoCapture] = {}

    def _now_iso(self) -> str:
        return format_utc_iso(self._clock())

    def capture_before_write(
        self,
        *,
        path: str,
        new_content: str,
        reader: FileReader,
    ) -> UndoCapture:
        """Read current file state before ``files.write`` executes."""
        prior_exists, prior_content = reader(path)
        return UndoCapture(
            path=path,
            prior_exists=prior_exists,
            prior_content=prior_content,
            written_sha256=sha256_text(new_content),
        )

    def record_write(
        self,
        *,
        session_id: str,
        audit_seq: int,
        capture: UndoCapture,
    ) -> UndoRecord:
        """Persist ``UndoRecordModel`` metadata and retain prior content in memory."""
        now_dt = self._clock()
        now_iso = format_utc_iso(now_dt)
        undo_id = generate_undo_id(now_dt)
        inverse_tool = "files.write" if capture.prior_exists else "files.delete"

        with self._session_factory() as db:
            db.add(
                UndoRecordModel(
                    undo_id=undo_id,
                    session_id=session_id,
                    audit_seq=audit_seq,
                    tool="files.write",
                    inverse_tool=inverse_tool,
                    target_path=capture.path,
                    prior_exists=1 if capture.prior_exists else 0,
                    written_sha256=capture.written_sha256,
                    state="available",
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            db.commit()

        with self._lock:
            self._snapshots[undo_id] = capture

        return UndoRecord(
            undo_id=undo_id,
            session_id=session_id,
            audit_seq=audit_seq,
            tool="files.write",
            inverse_tool=inverse_tool,
            inverse_args={"path": capture.path},
            state="available",
        )

    def undo_step(
        self,
        *,
        session_id: str,
        undo_id: str,
        coordinator: AuditedGatewayCoordinator,
        profile: Profile,
        executor: Callable[[ValidatedArguments], Any],
        reader: FileReader,
        session_state: SessionState = "ACTIVE",
    ) -> bool:
        """Safely undo one ``files.write`` through the audited gateway."""
        with self._session_factory() as db:
            row = db.scalar(
                select(UndoRecordModel).where(
                    UndoRecordModel.session_id == session_id,
                    UndoRecordModel.undo_id == undo_id,
                )
            )
            if row is None:
                raise UndoNotFoundError(
                    f"Undo record '{undo_id}' not found for session '{session_id}'."
                )
            if row.state != "available":
                raise UndoConflictError(
                    f"Undo record '{undo_id}' is not available (state={row.state})."
                )
            target_path = row.target_path
            prior_exists = bool(row.prior_exists)
            written_sha256 = row.written_sha256
            inverse_tool = row.inverse_tool

        with self._lock:
            capture = self._snapshots.get(undo_id)
        if prior_exists and (capture is None or capture.prior_content is None):
            raise UndoNotFoundError(
                f"In-memory prior content for undo '{undo_id}' is unavailable."
            )

        # Drift / missing-file check before touching workspace
        current_exists, current_content = reader(target_path)
        if not current_exists or current_content is None:
            raise UndoConflictError(f"Cannot undo '{target_path}': file is missing.")
        if sha256_text(current_content) != written_sha256:
            raise UndoConflictError(
                f"Cannot undo '{target_path}': file changed since original write."
            )

        args: dict[str, Any] = {"path": target_path}
        if inverse_tool == "files.write":
            assert capture is not None and capture.prior_content is not None
            args["content"] = capture.prior_content

        call = ToolCall(
            session_id=session_id,
            call_id=f"undo_{undo_id}",
            tool=inverse_tool,
            args=args,
        )
        audited = coordinator.handle_call(
            call,
            profile,
            executor=executor,
            session_state=session_state,
        )
        if not audited.executed:
            reason = (
                audited.tool_result.error.message
                if audited.tool_result.error is not None
                else f"decision={audited.preflight.decision}"
            )
            raise UndoPolicyError(
                f"Undo restoration blocked or requires approval: {reason}"
            )

        now_iso = self._now_iso()
        with self._session_factory() as db:
            db_row = db.get(UndoRecordModel, undo_id)
            if db_row is not None:
                db_row.state = "used"
                db_row.updated_at = now_iso
                db.commit()
        with self._lock:
            self._snapshots.pop(undo_id, None)
        return True

    def undo_all(
        self,
        *,
        session_id: str,
        coordinator: AuditedGatewayCoordinator,
        profile: Profile,
        executor: Callable[[ValidatedArguments], Any],
        reader: FileReader,
        session_state: SessionState = "ACTIVE",
    ) -> int:
        """Undo all available records for ``session_id`` in reverse audit order."""
        with self._session_factory() as db:
            rows = db.scalars(
                select(UndoRecordModel.undo_id)
                .where(
                    UndoRecordModel.session_id == session_id,
                    UndoRecordModel.state == "available",
                )
                .order_by(UndoRecordModel.audit_seq.desc())
            ).all()

        undone = 0
        for uid in rows:
            if self.undo_step(
                session_id=session_id,
                undo_id=uid,
                coordinator=coordinator,
                profile=profile,
                executor=executor,
                reader=reader,
                session_state=session_state,
            ):
                undone += 1
        return undone

    def clear_session_snapshots(self, session_id: str) -> None:
        """Expire available records and drop in-memory prior contents at teardown."""
        now_iso = self._now_iso()
        with self._session_factory() as db:
            rows = db.scalars(
                select(UndoRecordModel).where(
                    UndoRecordModel.session_id == session_id,
                    UndoRecordModel.state == "available",
                )
            ).all()
            ids = [r.undo_id for r in rows]
            for r in rows:
                r.state = "expired"
                r.updated_at = now_iso
            if rows:
                db.commit()
        with self._lock:
            for uid in ids:
                self._snapshots.pop(uid, None)

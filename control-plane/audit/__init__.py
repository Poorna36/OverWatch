"""Hash-chained audit log writer and verifier."""

from control_plane.audit.audit_writer import (
    BODY_KEY_PATTERN,
    GENESIS_PREV_HASH,
    MAX_ARG_STRING_LEN,
    SECRET_KEY_PATTERN,
    AuditWriter,
    AuditWriterError,
    append_audit_entry,
    audit_entry_hash_payload,
    canonical_json,
    compute_audit_hash,
    format_utc_iso,
    sanitize_audit_args,
    truncate_string,
)
from control_plane.audit.verify_audit import (
    AuditChainIssue,
    AuditVerificationResult,
    verify_all_audits,
    verify_audit,
    verify_audit_entries,
    verify_session_audit,
)

__all__ = [
    "AuditChainIssue",
    "AuditVerificationResult",
    "AuditWriter",
    "AuditWriterError",
    "BODY_KEY_PATTERN",
    "GENESIS_PREV_HASH",
    "MAX_ARG_STRING_LEN",
    "SECRET_KEY_PATTERN",
    "append_audit_entry",
    "audit_entry_hash_payload",
    "canonical_json",
    "compute_audit_hash",
    "format_utc_iso",
    "sanitize_audit_args",
    "truncate_string",
    "verify_all_audits",
    "verify_audit",
    "verify_audit_entries",
    "verify_session_audit",
]

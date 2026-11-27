"""F-14 Failure handling: error classification, bounded retries, and loop breaker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from control_plane.audit.audit_writer import canonical_json, truncate_string
from control_plane.contracts import ErrorCode, ProblemFix, ProblemReport

DEFAULT_MAX_TRANSIENT_RETRIES = 2
DEFAULT_LOOP_THRESHOLD = 3

ErrorCategory = Literal["transient", "fixable", "policy", "reconnect", "fatal"]

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*\S+"),
)


def sanitize_diagnostic_text(text: str, *, max_len: int = 200) -> str:
    """Redact potential secrets from diagnostic strings and bound length."""
    cleaned = str(text)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return truncate_string(cleaned, max_len=max_len)


@dataclass(frozen=True)
class ErrorClassification:
    """Retry and UX classification for a contract ``ErrorCode``."""

    code: ErrorCode
    category: ErrorCategory
    retryable: bool
    max_retries: int


def classify_error(code: ErrorCode | str) -> ErrorClassification:
    """Classify an ``ErrorCode`` per ``docs/CONTRACTS.md`` §2."""
    try:
        resolved = code if isinstance(code, ErrorCode) else ErrorCode(str(code))
    except ValueError:
        return ErrorClassification(
            code=ErrorCode.FATAL,
            category="fatal",
            retryable=False,
            max_retries=0,
        )

    if resolved in (ErrorCode.TIMEOUT, ErrorCode.UPSTREAM_ERROR):
        return ErrorClassification(
            code=resolved,
            category="transient",
            retryable=True,
            max_retries=DEFAULT_MAX_TRANSIENT_RETRIES,
        )
    if resolved == ErrorCode.ARG_INVALID:
        return ErrorClassification(
            code=resolved,
            category="fixable",
            retryable=False,
            max_retries=0,
        )
    if resolved == ErrorCode.AUTH_ERROR:
        return ErrorClassification(
            code=resolved,
            category="reconnect",
            retryable=False,
            max_retries=0,
        )
    if resolved in (
        ErrorCode.SESSION_INACTIVE,
        ErrorCode.NOT_IN_PROFILE,
        ErrorCode.POLICY_BLOCK,
        ErrorCode.PATH_JAIL,
        ErrorCode.DENY_PATTERN,
        ErrorCode.USER_DENIED,
        ErrorCode.APPROVAL_TIMEOUT,
    ):
        return ErrorClassification(
            code=resolved,
            category="policy",
            retryable=False,
            max_retries=0,
        )
    return ErrorClassification(
        code=resolved,
        category="fatal",
        retryable=False,
        max_retries=0,
    )


def _default_fixes_for_category(category: ErrorCategory) -> list[ProblemFix]:
    if category == "reconnect":
        return [
            ProblemFix(label="Reconnect account", action="reconnect"),
            ProblemFix(label="Skip step", action="skip_step"),
            ProblemFix(label="End session", action="end_session"),
        ]
    return [
        ProblemFix(label="Retry step", action="retry"),
        ProblemFix(label="Skip step", action="skip_step"),
        ProblemFix(label="End session", action="end_session"),
    ]


class LoopBreaker:
    """Tracks repeated failing tool calls and emits a redacted ``ProblemReport``."""

    def __init__(self, *, threshold: int = DEFAULT_LOOP_THRESHOLD) -> None:
        if threshold < 2:
            raise ValueError("threshold must be >= 2.")
        self.threshold = threshold
        self._counts: dict[str, int] = {}
        self._tried: dict[str, list[str]] = {}

    @staticmethod
    def signature(tool: str, args_redacted: dict[str, Any]) -> str:
        return f"{tool}:{canonical_json(args_redacted)}"

    def record_success(self, tool: str, args_redacted: dict[str, Any]) -> None:
        sig = self.signature(tool, args_redacted)
        self._counts.pop(sig, None)
        self._tried.pop(sig, None)

    def record_failure(
        self,
        *,
        tool: str,
        args_redacted: dict[str, Any],
        error_code: ErrorCode | str,
        reason: str,
    ) -> ProblemReport | None:
        """Record a failure; return ``ProblemReport`` when ``threshold`` is reached."""
        sig = self.signature(tool, args_redacted)
        count = self._counts.get(sig, 0) + 1
        self._counts[sig] = count

        classification = classify_error(error_code)
        safe_reason = sanitize_diagnostic_text(reason)
        tried_list = self._tried.setdefault(sig, [])
        tried_list.append(
            f"Attempt {count}: {tool} failed ({classification.code.value}): "
            f"{safe_reason}"
        )

        if count < self.threshold:
            return None

        return ProblemReport(
            what_failed=f"Tool '{tool}' failed {count} times with identical arguments.",
            why=f"{classification.code.value}: {safe_reason}",
            tried=list(tried_list),
            fixes=_default_fixes_for_category(classification.category),
        )

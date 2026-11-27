"""Phase 2 gateway tool argument validation (F-02).

Validates MVP tool arguments against ``docs/CONTRACTS.md`` §4 schemas,
``docs/SECURITY.md`` §§4–5 rules, ``ToolPolicy.max_arg_bytes``, workspace
``PathJail``, and ``terminal.run`` ``deny_patterns`` from ``policy.yaml``.

Uses existing ``ErrorCode`` values only:
- ``ARG_INVALID``: schema, type, extra field, range, or ``max_arg_bytes`` failure.
- ``PATH_JAIL``: workspace path traversal, absolute path, or symlink escape.
- ``DENY_PATTERN``: ``terminal.run`` command matched a policy deny pattern.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from control_plane.contracts import ErrorBody, ErrorCode, ToolPolicy
from control_plane.gateway.path_jail import PathJail, PathJailError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ArgumentValidationError(Exception):
    """Raised when tool arguments fail schema, size, path-jail, or deny checks."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_error_body(self) -> ErrorBody:
        """Return a contract-compliant ``ErrorBody``."""
        return ErrorBody(
            code=self.code,
            message=self.message,
            details=self.details,
        )


@dataclass(frozen=True)
class ValidatedArguments:
    """Validated tool arguments and normalized workspace paths."""

    tool: str
    args: dict[str, Any]
    resolved_paths: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strict per-tool argument schemas mirroring docs/CONTRACTS.md §4
# ---------------------------------------------------------------------------
class _StrictArgsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FilesListArgs(_StrictArgsModel):
    path: str


class FilesReadArgs(_StrictArgsModel):
    path: str
    max_bytes: int | None = Field(default=None, ge=1)


class FilesWriteArgs(_StrictArgsModel):
    path: str
    content: str


class FilesDeleteArgs(_StrictArgsModel):
    path: str


class FilesUploadArgs(_StrictArgsModel):
    path: str
    destination: str = Field(min_length=1)

    @field_validator("destination")
    @classmethod
    def _destination_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("destination cannot be blank.")
        return value


class TerminalRunArgs(_StrictArgsModel):
    command: str = Field(min_length=1)
    cwd: str | None = None
    timeout_s: int | float | None = Field(default=None, gt=0)

    @field_validator("command")
    @classmethod
    def _command_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command cannot be blank.")
        return value

    @field_validator("timeout_s")
    @classmethod
    def _timeout_finite(cls, value: int | float | None) -> int | float | None:
        if value is not None and not math.isfinite(float(value)):
            raise ValueError("timeout_s must be finite.")
        return value


class BrowserOpenArgs(_StrictArgsModel):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _valid_http_url(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("url cannot be empty or padded with whitespace.")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be an http or https URL with a host.")
        return value


class BrowserReadPageArgs(_StrictArgsModel):
    max_chars: int | None = Field(default=None, ge=1)


class BrowserScreenshotArgs(_StrictArgsModel):
    pass


class DesktopScreenshotArgs(_StrictArgsModel):
    pass


class DesktopClickArgs(_StrictArgsModel):
    x: int | float
    y: int | float
    button: Literal["left", "right"] | None = None

    @field_validator("x", "y")
    @classmethod
    def _finite_non_negative(cls, value: int | float) -> int | float:
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError("coordinates must be finite and non-negative.")
        return value


class DesktopTypeArgs(_StrictArgsModel):
    text: str


class DesktopKeyArgs(_StrictArgsModel):
    combo: str = Field(min_length=1)

    @field_validator("combo")
    @classmethod
    def _combo_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("combo cannot be blank.")
        return value


_TOOL_ARG_MODELS: dict[str, type[_StrictArgsModel]] = {
    "files.list": FilesListArgs,
    "files.read": FilesReadArgs,
    "files.write": FilesWriteArgs,
    "files.delete": FilesDeleteArgs,
    "files.upload": FilesUploadArgs,
    "terminal.run": TerminalRunArgs,
    "browser.open": BrowserOpenArgs,
    "browser.read_page": BrowserReadPageArgs,
    "browser.screenshot": BrowserScreenshotArgs,
    "desktop.screenshot": DesktopScreenshotArgs,
    "desktop.click": DesktopClickArgs,
    "desktop.type": DesktopTypeArgs,
    "desktop.key": DesktopKeyArgs,
}

# Workspace path fields per tool (all paths relative to /workspace per CONTRACTS.md §4)
_TOOL_PATH_FIELDS: dict[str, tuple[str, ...]] = {
    "files.list": ("path",),
    "files.read": ("path",),
    "files.write": ("path",),
    "files.delete": ("path",),
    "files.upload": ("path",),
    "terminal.run": ("cwd",),
}


def _check_arg_size(args: dict[str, Any], max_arg_bytes: int) -> None:
    """Enforce ToolPolicy.max_arg_bytes against serialized UTF-8 payload size."""
    try:
        serialized = json.dumps(args, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ArgumentValidationError(
            ErrorCode.ARG_INVALID,
            f"Tool arguments are not JSON-serializable: {exc}",
        ) from exc

    if len(serialized) > max_arg_bytes:
        raise ArgumentValidationError(
            ErrorCode.ARG_INVALID,
            f"Tool arguments size ({len(serialized)} bytes) exceeds max_arg_bytes "
            f"({max_arg_bytes} bytes).",
            details={"arg_bytes": len(serialized), "max_arg_bytes": max_arg_bytes},
        )


def _check_deny_patterns(command: str, deny_patterns: list[str]) -> None:
    """Reject terminal commands matching any configured deny pattern."""
    for pattern in deny_patterns:
        if pattern and pattern in command:
            raise ArgumentValidationError(
                ErrorCode.DENY_PATTERN,
                f"Command matched deny pattern {pattern!r}.",
                details={"pattern": pattern},
            )


def validate_tool_arguments(
    tool: str,
    args: dict[str, Any],
    *,
    tool_policy: ToolPolicy | None = None,
    path_jail: PathJail | None = None,
) -> ValidatedArguments:
    """Validate tool arguments against schemas, byte caps, path jail, and deny rules.

    Raises:
        ArgumentValidationError: With ``ErrorCode.ARG_INVALID``, ``PATH_JAIL``,
            or ``DENY_PATTERN``.
    """
    if not isinstance(args, dict):
        raise ArgumentValidationError(
            ErrorCode.ARG_INVALID,
            f"Tool args must be a mapping, got {type(args).__name__}.",
        )

    model_cls = _TOOL_ARG_MODELS.get(tool)
    if model_cls is None:
        raise ArgumentValidationError(
            ErrorCode.ARG_INVALID,
            f"No argument schema defined for tool '{tool}'.",
            details={"tool": tool},
        )

    if tool_policy is not None and tool_policy.max_arg_bytes is not None:
        _check_arg_size(args, tool_policy.max_arg_bytes)

    try:
        validated_model = model_cls.model_validate(args)
    except ValidationError as exc:
        raise ArgumentValidationError(
            ErrorCode.ARG_INVALID,
            f"Argument validation failed for '{tool}': {exc}",
            details={"tool": tool},
        ) from exc

    normalized_args = validated_model.model_dump(exclude_none=True)

    # Apply workspace PathJail to path fields (or any tool with path_jail configured)
    path_fields = list(_TOOL_PATH_FIELDS.get(tool, ()))
    if tool_policy is not None and tool_policy.path_jail and "path" not in path_fields:
        if "path" in normalized_args:
            path_fields.append("path")

    resolved_paths: dict[str, str] = {}
    if any(field_name in normalized_args for field_name in path_fields):
        jail = path_jail
        if jail is None:
            jail_root = (
                tool_policy.path_jail
                if tool_policy is not None and tool_policy.path_jail
                else "/workspace"
            )
            try:
                jail = PathJail(jail_root)
            except PathJailError as exc:
                raise ArgumentValidationError(
                    ErrorCode.PATH_JAIL,
                    exc.message,
                    details={"path": exc.path, "reason": exc.reason},
                ) from exc

        for field_name in path_fields:
            if field_name in normalized_args:
                raw_val = normalized_args[field_name]
                try:
                    rel_path = jail.relative_path(raw_val)
                except PathJailError as exc:
                    raise ArgumentValidationError(
                        ErrorCode.PATH_JAIL,
                        exc.message,
                        details={
                            "field": field_name,
                            "path": exc.path,
                            "reason": exc.reason,
                        },
                    ) from exc
                normalized_args[field_name] = rel_path
                resolved_paths[field_name] = rel_path

    # Apply terminal.run deny_patterns
    if tool == "terminal.run" and tool_policy is not None and tool_policy.deny_patterns:
        _check_deny_patterns(normalized_args["command"], tool_policy.deny_patterns)

    return ValidatedArguments(
        tool=tool,
        args=normalized_args,
        resolved_paths=resolved_paths,
    )


class ArgumentValidator:
    """Reusable validator bound to an optional session PathJail."""

    def __init__(self, path_jail: PathJail | None = None) -> None:
        self.path_jail = path_jail

    def validate(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        tool_policy: ToolPolicy | None = None,
    ) -> ValidatedArguments:
        """Validate ``args`` for ``tool`` using this validator's PathJail."""
        return validate_tool_arguments(
            tool=tool,
            args=args,
            tool_policy=tool_policy,
            path_jail=self.path_jail,
        )

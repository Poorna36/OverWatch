"""Workspace path-jail helper for the Phase 2 MCP gateway (F-02, F-07).

Resolves tool path arguments strictly within a session's ``/workspace`` root
per ``docs/CONTRACTS.md`` §4 and ``docs/SECURITY.md`` §4.

Security guarantees:
- Enforces the relative-path contract: all tool paths must be relative to the
  session workspace root; absolute paths (including ``/etc/passwd`` and
  ``/workspace/...``) are rejected with ``ErrorCode.PATH_JAIL``.
- Rejects null bytes, ASCII control characters, backslashes, and Unicode
  homoglyph/fullwidth dot or slash normalization tricks.
- Rejects ``..`` traversal segments both before and after normalization.
- Resolves existing path components and the final path to detect and reject any
  symlink (including intermediate, chained, dangling, or cyclic symlinks) that
  resolves outside the workspace root.
- Always fails closed with ``PathJailError`` (``code = ErrorCode.PATH_JAIL``).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from control_plane.contracts import ErrorBody, ErrorCode

DEFAULT_WORKSPACE_ROOT = Path("/workspace")

# Unicode slash/dot homoglyphs not always collapsed identically across layers
_FORBIDDEN_UNICODE_CHARS = {
    "\u2024",  # ONE DOT LEADER
    "\u2025",  # TWO DOT LEADER
    "\u2026",  # HORIZONTAL ELLIPSIS
    "\u2044",  # FRACTION SLASH
    "\u2215",  # DIVISION SLASH
    "\u29f5",  # REVERSE SOLIDUS OPERATOR
    "\u29f8",  # BIG SOLIDUS
    "\u29f9",  # BIG REVERSE SOLIDUS
    "\uff0e",  # FULLWIDTH FULL STOP
    "\uff0f",  # FULLWIDTH SOLIDUS
    "\uff3c",  # FULLWIDTH REVERSE SOLIDUS
}


class PathJailError(Exception):
    """Raised when a tool path violates the session workspace path jail."""

    code: str = ErrorCode.PATH_JAIL

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ErrorCode.PATH_JAIL
        self.message = message
        self.path = path
        self.reason = reason

    def to_error_body(self) -> ErrorBody:
        """Return a contract-compliant ``ErrorBody`` for gateway responses."""
        details: dict[str, Any] = {}
        if self.path is not None:
            details["path"] = self.path
        if self.reason is not None:
            details["reason"] = self.reason
        return ErrorBody(
            code=self.code,
            message=self.message,
            details=details or None,
        )


def _validate_workspace_root(workspace_root: Path | str) -> Path:
    """Validate and resolve the workspace jail root directory."""
    if not isinstance(workspace_root, (Path, str)):
        raise PathJailError(
            f"Workspace root must be Path or str, got {type(workspace_root).__name__}.",
            reason="invalid_root",
        )

    root_str = str(workspace_root)
    if not root_str.strip():
        raise PathJailError(
            "Workspace root cannot be empty.",
            reason="invalid_root",
        )

    if "\x00" in root_str:
        raise PathJailError(
            "Null byte detected in workspace root.",
            reason="null_byte",
        )

    try:
        resolved_root = Path(workspace_root).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathJailError(
            f"Failed to resolve workspace root {root_str!r}: {exc}",
            reason="invalid_root",
        ) from exc

    if resolved_root.exists() and not resolved_root.is_dir():
        raise PathJailError(
            f"Workspace root must be a directory: {resolved_root}",
            reason="invalid_root",
        )

    return resolved_root


def _validate_relative_input(raw_path: str) -> str:
    """Validate raw tool path against relative-path and character rules."""
    if not isinstance(raw_path, str):
        raise PathJailError(
            f"Tool path must be a string, got {type(raw_path).__name__}.",
            reason="invalid_type",
        )

    if "\x00" in raw_path:
        raise PathJailError(
            "Null byte detected in tool path.",
            path=raw_path,
            reason="null_byte",
        )

    for ch in raw_path:
        code_pt = ord(ch)
        if code_pt < 0x20 or code_pt == 0x7F:
            raise PathJailError(
                f"Control character {ch!r} is forbidden in tool path.",
                path=raw_path,
                reason="control_char",
            )
        if ch in _FORBIDDEN_UNICODE_CHARS:
            raise PathJailError(
                f"Suspicious Unicode path character {ch!r} rejected.",
                path=raw_path,
                reason="unicode_trick",
            )

    if not raw_path.strip():
        raise PathJailError(
            "Tool path cannot be empty.",
            path=raw_path,
            reason="empty_path",
        )

    if raw_path != raw_path.strip():
        raise PathJailError(
            f"Tool path cannot have leading or trailing whitespace: {raw_path!r}",
            path=raw_path,
            reason="whitespace_padding",
        )

    if "\\" in raw_path:
        raise PathJailError(
            f"Backslashes are forbidden in tool path: {raw_path!r}",
            path=raw_path,
            reason="backslash",
        )

    nfkc = unicodedata.normalize("NFKC", raw_path)
    if "\x00" in nfkc:
        raise PathJailError(
            "Null byte detected after Unicode normalization.",
            path=raw_path,
            reason="null_byte",
        )
    if "\\" in nfkc:
        raise PathJailError(
            f"Backslash introduced after Unicode normalization: {raw_path!r}",
            path=raw_path,
            reason="unicode_trick",
        )

    # Reject Unicode characters that NFKC-collapse into '/' or '.' homoglyphs
    for orig_ch, norm_ch in zip(raw_path, nfkc, strict=False):
        if norm_ch in ("/", ".") and orig_ch != norm_ch:
            raise PathJailError(
                f"Unicode homoglyph {orig_ch!r} rejected in tool path.",
                path=raw_path,
                reason="unicode_trick",
            )

    # Enforce relative-path contract (docs/CONTRACTS.md §4)
    if raw_path.startswith("/") or nfkc.startswith("/"):
        raise PathJailError(
            f"Absolute paths are forbidden; paths must be relative to /workspace: "
            f"{raw_path!r}",
            path=raw_path,
            reason="absolute_path",
        )

    posix_path = PurePosixPath(raw_path)
    nfkc_posix = PurePosixPath(nfkc)
    win_path = PureWindowsPath(raw_path)

    if posix_path.is_absolute() or nfkc_posix.is_absolute():
        raise PathJailError(
            f"Absolute paths are forbidden; paths must be relative to /workspace: "
            f"{raw_path!r}",
            path=raw_path,
            reason="absolute_path",
        )

    if win_path.is_absolute() or win_path.drive:
        raise PathJailError(
            f"Drive-prefixed or absolute paths are forbidden: {raw_path!r}",
            path=raw_path,
            reason="absolute_path",
        )

    if ".." in posix_path.parts or ".." in nfkc_posix.parts:
        raise PathJailError(
            f"Path traversal ('..') is forbidden in tool path: {raw_path!r}",
            path=raw_path,
            reason="traversal",
        )

    return raw_path


def resolve_workspace_path(
    raw_path: str,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
) -> Path:
    """Resolve a tool path strictly within ``workspace_root``.

    Args:
        raw_path: Relative tool path per ``docs/CONTRACTS.md`` §4.
        workspace_root: Session workspace root (defaults to ``/workspace``).

    Returns:
        Canonical resolved ``Path`` guaranteed to reside within ``workspace_root``.

    Raises:
        PathJailError: Always raised with ``code == ErrorCode.PATH_JAIL`` on
            invalid input, traversal, absolute paths, null bytes, or symlink
            escapes outside the workspace root.
    """
    resolved_root = _validate_workspace_root(workspace_root)
    cleaned = _validate_relative_input(raw_path)
    rel_posix = PurePosixPath(cleaned)

    # Walk each component so intermediate symlinks cannot escape and bounce back
    current = resolved_root
    for part in rel_posix.parts:
        if part in ("", "."):
            continue
        current = current / part
        if current.is_symlink() or current.exists():
            try:
                resolved_step = current.resolve()
            except (OSError, RuntimeError) as exc:
                raise PathJailError(
                    f"Failed to resolve path component safely: {exc}",
                    path=raw_path,
                    reason="resolution_error",
                ) from exc

            if not resolved_step.is_relative_to(resolved_root):
                raise PathJailError(
                    f"Path {raw_path!r} escapes workspace root {resolved_root} "
                    "via symlink.",
                    path=raw_path,
                    reason="symlink_escape",
                )

    try:
        resolved_target = (resolved_root / rel_posix).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathJailError(
            f"Failed to resolve path {raw_path!r}: {exc}",
            path=raw_path,
            reason="resolution_error",
        ) from exc

    if not resolved_target.is_relative_to(resolved_root):
        raise PathJailError(
            f"Resolved path {resolved_target} escapes workspace root {resolved_root}.",
            path=raw_path,
            reason="outside_workspace",
        )

    return resolved_target


def normalize_workspace_relative_path(
    raw_path: str,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
) -> str:
    """Validate and return the canonical POSIX path relative to ``workspace_root``."""
    resolved_root = _validate_workspace_root(workspace_root)
    resolved_target = resolve_workspace_path(raw_path, workspace_root=resolved_root)
    rel = resolved_target.relative_to(resolved_root)
    return "." if rel == Path(".") else rel.as_posix()


class PathJail:
    """Session-scoped path jail bound to a session's workspace directory."""

    def __init__(self, workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT) -> None:
        self._root = _validate_workspace_root(workspace_root)

    @property
    def root(self) -> Path:
        """Canonical resolved workspace root path."""
        return self._root

    def resolve(self, raw_path: str) -> Path:
        """Resolve ``raw_path`` strictly inside this jail's workspace root."""
        return resolve_workspace_path(raw_path, workspace_root=self._root)

    def relative_path(self, raw_path: str) -> str:
        """Return canonical workspace-relative POSIX string for ``raw_path``."""
        return normalize_workspace_relative_path(raw_path, workspace_root=self._root)

    def container_path(
        self,
        raw_path: str,
        container_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    ) -> str:
        """Map a jailed relative path to its canonical container path string."""
        rel = self.relative_path(raw_path)
        container_base = PurePosixPath(str(container_root))
        if rel == ".":
            return container_base.as_posix()
        return (container_base / rel).as_posix()


def validate_workspace_relative_input(raw_path: str) -> str:
    """Validate a raw workspace path lexically against traversal/absolute tricks."""
    return _validate_relative_input(raw_path)

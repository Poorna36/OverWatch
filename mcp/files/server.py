"""Phase 3 in-house files MCP server (F-03).

Exposes the four contract-defined workspace file tools via the installed MCP
Python SDK (``MCPServer``):
- ``files.list``
- ``files.read``
- ``files.write``
- ``files.delete``

Security boundaries:
- Every path argument is validated against ``control_plane.gateway.path_jail.PathJail``
  and ``control_plane.gateway.argument_validation.validate_tool_arguments``,
  enforcing the relative-path contract and blocking traversal, absolute paths,
  null bytes, and symlink escapes with ``ErrorCode.PATH_JAIL``.
- File opens use ``O_NOFOLLOW`` where available and never expose resolved host
  directory prefixes in tool outputs or error messages.
- Gateway-level policy tier checks, approvals, and audit logging remain the
  responsibility of the control-plane MCP gateway.
- ``files.upload`` is intentionally omitted until ``destination`` / ``url``
  contract semantics are specified in ``docs/CONTRACTS.md``.
- Runs over ``stdio`` transport; gateway-to-container transport details over the
  ``mgmt`` network are left unspecified by the docs and not invented here.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from control_plane.contracts import ErrorCode, ToolPolicy
from control_plane.gateway.argument_validation import (
    ArgumentValidationError,
    ValidatedArguments,
    validate_tool_arguments,
)
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

DEFAULT_OUTPUT_CAP_BYTES: int = 200_000
DEFAULT_FILES_WRITE_MAX_ARG_BYTES: int = 1_000_000


class FilesServerError(Exception):
    """Base error raised by the files MCP server."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class FileEntry(BaseModel):
    """Directory entry item matching ``docs/CONTRACTS.md`` §4 ``files.list``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["file", "dir", "symlink", "other"]
    size: int


class FilesListResult(BaseModel):
    """Exact ``files.list`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    entries: list[FileEntry]


class FilesReadResult(BaseModel):
    """Exact ``files.read`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    content: str


class FilesWriteResult(BaseModel):
    """Exact ``files.write`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    bytes: int


class FilesDeleteResult(BaseModel):
    """Exact ``files.delete`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    deleted: bool


@dataclass(frozen=True)
class FilesServerConfig:
    """Runtime configuration for the files MCP server."""

    workspace_root: Path = DEFAULT_WORKSPACE_ROOT
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES
    write_max_arg_bytes: int = DEFAULT_FILES_WRITE_MAX_ARG_BYTES


def _build_jail(config: FilesServerConfig) -> PathJail:
    """Build a ``PathJail`` for ``config.workspace_root`` safely."""
    try:
        return PathJail(config.workspace_root)
    except PathJailError as exc:
        raise FilesServerError(
            ErrorCode.PATH_JAIL,
            f"Invalid workspace root configuration ({exc.reason or 'invalid_root'}).",
        ) from exc


def _resolve_tool_target(
    tool: str,
    raw_args: dict[str, Any],
    config: FilesServerConfig,
    *,
    tool_policy: ToolPolicy | None = None,
) -> tuple[PathJail, Path, ValidatedArguments]:
    """Validate arguments and resolve the jailed workspace path safely."""
    jail = _build_jail(config)
    try:
        validated = validate_tool_arguments(
            tool,
            raw_args,
            tool_policy=tool_policy,
            path_jail=jail,
        )
    except ArgumentValidationError as exc:
        if exc.code == ErrorCode.PATH_JAIL:
            reason = (
                exc.details.get("reason")
                if isinstance(exc.details, dict)
                else "path_jail"
            )
            raise FilesServerError(
                ErrorCode.PATH_JAIL,
                f"Path rejected by workspace jail ({reason}).",
            ) from exc
        raise FilesServerError(exc.code, exc.message) from exc

    try:
        target = jail.resolve(validated.args["path"])
    except PathJailError as exc:
        raise FilesServerError(
            ErrorCode.PATH_JAIL,
            f"Path rejected by workspace jail ({exc.reason or 'path_jail'}).",
        ) from exc

    return jail, target, validated


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate ``text`` so its UTF-8 encoding does not exceed ``max_bytes``."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _entry_kind(st_mode: int) -> Literal["file", "dir", "symlink", "other"]:
    """Map POSIX ``lstat`` mode bits into a safe contract entry type."""
    if stat.S_ISDIR(st_mode):
        return "dir"
    if stat.S_ISLNK(st_mode):
        return "symlink"
    if stat.S_ISREG(st_mode):
        return "file"
    return "other"


def _read_fd_bounded(fd: int, limit: int) -> bytes:
    """Read at most ``limit`` bytes from an already-open file descriptor."""
    chunks: list[bytes] = []
    remaining = max(0, limit)
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def execute_files_list(
    *,
    path: str,
    config: FilesServerConfig | None = None,
) -> FilesListResult:
    """List directory entries strictly within the jailed workspace."""
    cfg = config or FilesServerConfig()
    _, target, validated = _resolve_tool_target("files.list", {"path": path}, cfg)

    if not target.exists():
        raise FilesServerError(
            ErrorCode.ARG_INVALID,
            f"Directory not found: {validated.args['path']!r}.",
        )
    if not target.is_dir():
        raise FilesServerError(
            ErrorCode.ARG_INVALID,
            f"Path is not a directory: {validated.args['path']!r}.",
        )

    try:
        with os.scandir(target) as scan:
            dir_items = sorted(scan, key=lambda item: item.name)
    except OSError as exc:
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to list directory {validated.args['path']!r}: "
            f"{exc.strerror or exc}",
        ) from exc

    entries: list[FileEntry] = []
    cap_bytes = max(0, cfg.output_cap_bytes)
    # Account for minimal JSON wrapper {"entries":[]} when enforcing output cap
    used_bytes = len(b'{"entries":[]}')
    for item in dir_items:
        try:
            st = item.stat(follow_symlinks=False)
        except OSError:
            continue
        entry = FileEntry(
            name=item.name,
            type=_entry_kind(st.st_mode),
            size=int(st.st_size),
        )
        encoded_len = len(
            json.dumps(
                entry.model_dump(), separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        )
        sep_len = 1 if entries else 0
        if cap_bytes > 0 and used_bytes + sep_len + encoded_len > cap_bytes:
            break
        entries.append(entry)
        used_bytes += sep_len + encoded_len

    return FilesListResult(entries=entries)


def execute_files_read(
    *,
    path: str,
    max_bytes: int | None = None,
    config: FilesServerConfig | None = None,
) -> FilesReadResult:
    """Read UTF-8 text from a jailed workspace file up to bounded byte limits."""
    cfg = config or FilesServerConfig()
    raw_args: dict[str, Any] = {"path": path}
    if max_bytes is not None:
        raw_args["max_bytes"] = max_bytes

    _, target, validated = _resolve_tool_target("files.read", raw_args, cfg)

    if not target.exists():
        raise FilesServerError(
            ErrorCode.ARG_INVALID,
            f"File not found: {validated.args['path']!r}.",
        )
    if not target.is_file():
        raise FilesServerError(
            ErrorCode.ARG_INVALID,
            f"Path is not a regular file: {validated.args['path']!r}.",
        )

    effective_cap = max(0, cfg.output_cap_bytes)
    if "max_bytes" in validated.args:
        effective_cap = min(int(validated.args["max_bytes"]), effective_cap)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FilesServerError(
                ErrorCode.PATH_JAIL,
                "Path rejected by workspace jail (symlink_race).",
            ) from exc
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to open file {validated.args['path']!r}: {exc.strerror or exc}",
        ) from exc

    try:
        raw_bytes = _read_fd_bounded(fd, effective_cap)
    except OSError as exc:
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to read file {validated.args['path']!r}: {exc.strerror or exc}",
        ) from exc
    finally:
        os.close(fd)

    text = _truncate_utf8(raw_bytes.decode("utf-8", errors="replace"), effective_cap)
    return FilesReadResult(content=text)


def execute_files_write(
    *,
    path: str,
    content: str,
    config: FilesServerConfig | None = None,
) -> FilesWriteResult:
    """Write UTF-8 text to a jailed workspace file and return byte count."""
    cfg = config or FilesServerConfig()
    write_policy = ToolPolicy(
        tier="reversible",
        path_jail="/workspace",
        max_arg_bytes=cfg.write_max_arg_bytes,
    )
    jail, target, validated = _resolve_tool_target(
        "files.write",
        {"path": path, "content": content},
        cfg,
        tool_policy=write_policy,
    )

    if target == jail.root or (target.exists() and target.is_dir()):
        raise FilesServerError(
            ErrorCode.ARG_INVALID,
            f"Cannot write to a directory path: {validated.args['path']!r}.",
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
    except OSError as exc:
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to prepare parent directory for {validated.args['path']!r}: "
            f"{exc.strerror or exc}",
        ) from exc

    if not resolved_parent.is_relative_to(jail.root):
        raise FilesServerError(
            ErrorCode.PATH_JAIL,
            "Path rejected by workspace jail (outside_workspace).",
        )

    encoded = validated.args["content"].encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FilesServerError(
                ErrorCode.PATH_JAIL,
                "Path rejected by workspace jail (symlink_race).",
            ) from exc
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to open file {validated.args['path']!r} for write: "
            f"{exc.strerror or exc}",
        ) from exc

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
    except OSError as exc:
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to write file {validated.args['path']!r}: {exc.strerror or exc}",
        ) from exc

    return FilesWriteResult(bytes=len(encoded))


def execute_files_delete(
    *,
    path: str,
    config: FilesServerConfig | None = None,
) -> FilesDeleteResult:
    """Delete a file or empty directory strictly within the jailed workspace."""
    cfg = config or FilesServerConfig()
    jail, target, validated = _resolve_tool_target("files.delete", {"path": path}, cfg)

    if target == jail.root:
        raise FilesServerError(
            ErrorCode.PATH_JAIL,
            "Cannot delete workspace root directory.",
        )

    if not target.exists() and not target.is_symlink():
        return FilesDeleteResult(deleted=False)

    try:
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        if exc.errno == errno.ENOTEMPTY:
            raise FilesServerError(
                ErrorCode.ARG_INVALID,
                f"Cannot delete non-empty directory: {validated.args['path']!r}.",
            ) from exc
        raise FilesServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to delete {validated.args['path']!r}: {exc.strerror or exc}",
        ) from exc

    return FilesDeleteResult(deleted=True)


def create_files_server(config: FilesServerConfig | None = None) -> MCPServer:
    """Build and return an ``MCPServer`` exposing MVP workspace file tools."""
    cfg = config or FilesServerConfig(
        workspace_root=Path(
            os.environ.get("OVERWATCH_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
        ),
        output_cap_bytes=int(
            os.environ.get("OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES))
        ),
        write_max_arg_bytes=int(
            os.environ.get(
                "OVERWATCH_FILES_WRITE_MAX_ARG_BYTES",
                str(DEFAULT_FILES_WRITE_MAX_ARG_BYTES),
            )
        ),
    )

    server = MCPServer(
        name="files",
        instructions="Workspace-jailed file operations inside /workspace.",
    )

    @server.tool(
        name="files.list",
        description="List entries in a directory inside /workspace.",
        structured_output=True,
    )
    def files_list(path: str) -> FilesListResult:
        """List directory entries inside the jailed workspace."""
        try:
            return execute_files_list(path=path, config=cfg)
        except FilesServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="files.read",
        description="Read a UTF-8 file inside /workspace up to max_bytes.",
        structured_output=True,
    )
    def files_read(path: str, max_bytes: int | None = None) -> FilesReadResult:
        """Read text from a file inside the jailed workspace."""
        try:
            return execute_files_read(path=path, max_bytes=max_bytes, config=cfg)
        except FilesServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="files.write",
        description="Write UTF-8 content to a file inside /workspace.",
        structured_output=True,
    )
    def files_write(path: str, content: str) -> FilesWriteResult:
        """Write text to a file inside the jailed workspace."""
        try:
            return execute_files_write(path=path, content=content, config=cfg)
        except FilesServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="files.delete",
        description="Delete a file or empty directory inside /workspace.",
        structured_output=True,
    )
    def files_delete(path: str) -> FilesDeleteResult:
        """Delete a file or empty directory inside the jailed workspace."""
        try:
            return execute_files_delete(path=path, config=cfg)
        except FilesServerError as exc:
            raise ToolError(str(exc)) from exc

    return server


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point running the files MCP server over stdio transport."""
    parser = argparse.ArgumentParser(
        description="OverWatch workspace-jailed files MCP server."
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help=(
            "Session workspace root directory "
            "(defaults to OVERWATCH_WORKSPACE_ROOT or /workspace)."
        ),
    )
    args = parser.parse_args(argv)
    cfg: FilesServerConfig | None = None
    if args.workspace_root is not None:
        cfg = FilesServerConfig(
            workspace_root=args.workspace_root,
            output_cap_bytes=int(
                os.environ.get(
                    "OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES)
                )
            ),
            write_max_arg_bytes=int(
                os.environ.get(
                    "OVERWATCH_FILES_WRITE_MAX_ARG_BYTES",
                    str(DEFAULT_FILES_WRITE_MAX_ARG_BYTES),
                )
            ),
        )
    create_files_server(config=cfg).run(transport="stdio")


if __name__ == "__main__":
    main()

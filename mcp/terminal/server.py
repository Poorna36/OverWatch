"""Phase 3 in-house terminal MCP server (F-03).

Exposes ``terminal.run`` via the official MCP Python SDK (``MCPServer``).
Executes commands strictly as an argv list with ``shell=False``, keeps working
directories jailed inside ``/workspace`` via ``PathJail``, and enforces
per-call timeouts and output byte caps.

Note:
    Gateway-level checks (session ACTIVE state, profile server membership,
    ``deny_patterns``, approvals, and audit logging) remain the responsibility
    of the MCP gateway. This server provides defense-in-depth inside the
    non-root session container.
"""

from __future__ import annotations

import math
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from control_plane.contracts import ErrorCode
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

DEFAULT_TIMEOUT_S: float = 30.0
DEFAULT_OUTPUT_CAP_BYTES: int = 200_000


class TerminalServerError(Exception):
    """Base error raised by the terminal MCP server."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class TerminalRunResult(BaseModel):
    """Exact ``terminal.run`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TerminalServerConfig:
    """Runtime configuration for the terminal MCP server."""

    workspace_root: Path = DEFAULT_WORKSPACE_ROOT
    default_timeout_s: float = DEFAULT_TIMEOUT_S
    max_timeout_s: float = DEFAULT_TIMEOUT_S
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate ``text`` so its UTF-8 encoding is at most ``max_bytes`` bytes."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _cap_streams(stdout: str, stderr: str, cap_bytes: int) -> tuple[str, str]:
    """Ensure combined UTF-8 byte length of stdout and stderr does not exceed cap."""
    if cap_bytes <= 0:
        return "", ""
    out_bytes = stdout.encode("utf-8", errors="replace")
    err_bytes = stderr.encode("utf-8", errors="replace")
    if len(out_bytes) + len(err_bytes) <= cap_bytes:
        return stdout, stderr

    # Allocate half to stderr (capped to its actual length) and remainder to stdout
    err_budget = min(len(err_bytes), cap_bytes // 2)
    out_budget = cap_bytes - err_budget
    if len(out_bytes) < out_budget:
        err_budget = min(len(err_bytes), cap_bytes - len(out_bytes))
    return (
        _truncate_utf8(stdout, out_budget),
        _truncate_utf8(stderr, err_budget),
    )


def _parse_command_argv(command: str) -> list[str]:
    """Tokenize ``command`` into an argv list without invoking a shell."""
    if not isinstance(command, str) or not command.strip():
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            "command must be a non-empty string.",
        )
    if "\x00" in command:
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            "Null byte detected in command.",
        )
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            f"Invalid command quoting: {exc}",
        ) from exc

    if not argv:
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            "command produced an empty argv list.",
        )
    return argv


def _resolve_timeout(
    requested: int | float | None,
    *,
    default_timeout_s: float,
    max_timeout_s: float,
) -> float:
    """Validate and clamp ``timeout_s`` to the configured policy ceiling."""
    if requested is None:
        return default_timeout_s
    if isinstance(requested, bool) or not isinstance(requested, (int, float)):
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            f"timeout_s must be a positive number, got {type(requested).__name__}.",
        )
    val = float(requested)
    if not math.isfinite(val) or val <= 0:
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            "timeout_s must be finite and greater than 0.",
        )
    return min(val, max_timeout_s)


def _resolve_cwd(cwd: str | None, jail: PathJail) -> Path:
    """Resolve ``cwd`` inside ``jail`` and verify it is an existing directory."""
    if cwd is None:
        target = jail.root
    else:
        try:
            target = jail.resolve(cwd)
        except PathJailError as exc:
            raise TerminalServerError(ErrorCode.PATH_JAIL, exc.message) from exc

    if not target.is_dir():
        raise TerminalServerError(
            ErrorCode.ARG_INVALID,
            f"Working directory does not exist or is not a directory: {cwd or '.'}",
        )
    return target


def execute_terminal_run(
    *,
    command: str,
    cwd: str | None = None,
    timeout_s: int | float | None = None,
    config: TerminalServerConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> TerminalRunResult:
    """Validate arguments, jail ``cwd``, and run ``command`` with ``shell=False``."""
    cfg = config or TerminalServerConfig()
    try:
        jail = PathJail(cfg.workspace_root)
    except PathJailError as exc:
        raise TerminalServerError(ErrorCode.PATH_JAIL, exc.message) from exc

    argv = _parse_command_argv(command)
    work_dir = _resolve_cwd(cwd, jail)
    effective_timeout = _resolve_timeout(
        timeout_s,
        default_timeout_s=cfg.default_timeout_s,
        max_timeout_s=cfg.max_timeout_s,
    )

    try:
        completed = runner(
            argv,
            cwd=str(work_dir),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=effective_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TerminalServerError(
            ErrorCode.TIMEOUT,
            f"Command timed out after {effective_timeout}s.",
        ) from exc
    except OSError as exc:
        raise TerminalServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to execute command '{argv[0]}': {exc}",
        ) from exc
    except subprocess.SubprocessError as exc:
        raise TerminalServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Subprocess error running '{argv[0]}': {exc}",
        ) from exc

    stdout, stderr = _cap_streams(
        completed.stdout or "",
        completed.stderr or "",
        cfg.output_cap_bytes,
    )
    return TerminalRunResult(
        exit_code=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
    )


def create_terminal_server(
    config: TerminalServerConfig | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> MCPServer:
    """Build and return an ``MCPServer`` exposing ``terminal.run``."""
    cfg = config or TerminalServerConfig(
        workspace_root=Path(
            os.environ.get("OVERWATCH_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
        ),
        default_timeout_s=float(
            os.environ.get("OVERWATCH_TERMINAL_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        ),
        max_timeout_s=float(
            os.environ.get("OVERWATCH_TERMINAL_MAX_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        ),
        output_cap_bytes=int(
            os.environ.get("OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES))
        ),
    )

    server = MCPServer(
        name="terminal",
        instructions="Constrained non-root terminal execution inside /workspace.",
    )

    @server.tool(
        name="terminal.run",
        description="Execute a command as argv (shell=False) inside /workspace.",
        structured_output=True,
    )
    def terminal_run(
        command: str,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run a command inside the jailed workspace directory."""
        try:
            result = execute_terminal_run(
                command=command,
                cwd=cwd,
                timeout_s=timeout_s,
                config=cfg,
                runner=runner,
            )
        except TerminalServerError as exc:
            raise ToolError(str(exc)) from exc
        return result.model_dump()

    return server


if __name__ == "__main__":
    create_terminal_server().run(transport="stdio")

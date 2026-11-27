"""Phase 3 in-house desktop MCP server (F-03).

Exposes the four contract-defined desktop tools via the installed MCP Python SDK
(``MCPServer``):
- ``desktop.screenshot``
- ``desktop.click``
- ``desktop.type``
- ``desktop.key``

Security boundaries:
- Runs inside the non-root session container; does not mount host paths,
  expose credentials, access Docker, or make network calls.
- Invokes ``xdotool`` and ``scrot`` strictly as argv lists with ``shell=False``
  and ``--`` option terminators so user inputs cannot become extra flags or
  shell commands.
- Reads ``DISPLAY`` from config/environment and fails safely if unavailable.
- Enforces per-call timeouts and ``output_cap_bytes`` on screenshots.
- Gateway-level policy decisions, approvals, and audit logging remain the
  responsibility of the control-plane MCP gateway.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from control_plane.contracts import ErrorCode
from control_plane.gateway.argument_validation import (
    ArgumentValidationError,
    validate_tool_arguments,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

DEFAULT_TIMEOUT_S: float = 30.0
DEFAULT_OUTPUT_CAP_BYTES: int = 200_000

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KEY_COMBO_RE = re.compile(r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*\Z")
_DISPLAY_RE = re.compile(r"^[A-Za-z0-9._-]*:[0-9]+(?:\.[0-9]+)?\Z")


class DesktopServerError(Exception):
    """Base error raised by the desktop MCP server."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class DesktopScreenshotResult(BaseModel):
    """Exact ``desktop.screenshot`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    image_b64: str
    width: int
    height: int


class DesktopActionResult(BaseModel):
    """Exact ``desktop.click|type|key`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True


@dataclass(frozen=True)
class DesktopServerConfig:
    """Runtime configuration for the desktop MCP server."""

    display: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES


def _resolve_display(config: DesktopServerConfig) -> str:
    """Resolve and validate the X11 ``DISPLAY`` value; fail safely if unavailable."""
    raw = config.display if config.display is not None else os.environ.get("DISPLAY")
    if not isinstance(raw, str) or not raw.strip():
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            "X11 DISPLAY is not configured or unavailable.",
        )
    if "\x00" in raw or raw != raw.strip() or any(ord(c) < 0x20 for c in raw):
        raise DesktopServerError(
            ErrorCode.ARG_INVALID,
            "Invalid characters in DISPLAY configuration.",
        )
    if not _DISPLAY_RE.match(raw):
        raise DesktopServerError(
            ErrorCode.ARG_INVALID,
            f"Invalid X11 DISPLAY format {raw!r}.",
        )
    return raw


def _validate_args(tool: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Validate arguments against the contract schemas in argument_validation."""
    try:
        validated = validate_tool_arguments(tool, raw_args)
    except ArgumentValidationError as exc:
        raise DesktopServerError(exc.code, exc.message) from exc
    return validated.args


def _parse_png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Extract ``(width, height)`` from a valid PNG's IHDR header."""
    if len(png_bytes) < 24 or not png_bytes.startswith(_PNG_SIGNATURE):
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            "Captured screenshot is not a valid PNG image.",
        )
    if png_bytes[12:16] != b"IHDR":
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            "Captured PNG is missing the IHDR header.",
        )
    width, height = struct.unpack(">II", png_bytes[16:24])
    if width <= 0 or height <= 0:
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Invalid PNG dimensions: {width}x{height}.",
        )
    return width, height


def _run_desktop_subprocess(
    argv: list[str],
    *,
    display: str,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> subprocess.CompletedProcess[bytes]:
    """Execute a desktop utility via ``argv`` with ``shell=False`` and timeout."""
    env = dict(os.environ)
    env["DISPLAY"] = display

    try:
        completed = runner(
            argv,
            shell=False,
            env=env,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DesktopServerError(
            ErrorCode.TIMEOUT,
            f"Desktop command '{argv[0]}' timed out after {timeout_s}s.",
        ) from exc
    except OSError as exc:
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to execute '{argv[0]}': {exc}",
        ) from exc
    except subprocess.SubprocessError as exc:
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Subprocess error running '{argv[0]}': {exc}",
        ) from exc

    if completed.returncode != 0:
        stderr_msg = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        detail = f": {stderr_msg}" if stderr_msg else ""
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            (
                f"Desktop command '{argv[0]}' failed "
                f"(exit {completed.returncode}){detail}."
            ),
        )
    return completed


def execute_desktop_screenshot(
    *,
    config: DesktopServerConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    temp_dir: Path | str | None = None,
) -> DesktopScreenshotResult:
    """Capture an X11 PNG screenshot and enforce ``output_cap_bytes``."""
    cfg = config or DesktopServerConfig()
    _validate_args("desktop.screenshot", {})
    display = _resolve_display(cfg)

    scratch_dir = str(temp_dir) if temp_dir is not None else "/tmp"
    tmp_file = tempfile.NamedTemporaryFile(
        prefix="ow_shot_",
        suffix=".png",
        dir=scratch_dir,
        delete=False,
    )
    tmp_path = Path(tmp_file.name)
    tmp_file.close()

    try:
        _run_desktop_subprocess(
            ["scrot", "-o", "--", str(tmp_path)],
            display=display,
            timeout_s=cfg.timeout_s,
            runner=runner,
        )
        try:
            png_bytes = tmp_path.read_bytes()
        except OSError as exc:
            raise DesktopServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Failed to read captured screenshot: {exc}",
            ) from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    width, height = _parse_png_dimensions(png_bytes)
    image_b64 = base64.b64encode(png_bytes).decode("ascii")
    result = DesktopScreenshotResult(
        image_b64=image_b64,
        width=width,
        height=height,
    )

    payload_bytes = len(
        json.dumps(result.model_dump(), separators=(",", ":")).encode("utf-8")
    )
    if cfg.output_cap_bytes > 0 and payload_bytes > cfg.output_cap_bytes:
        raise DesktopServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"Screenshot payload ({payload_bytes} bytes) exceeds output cap "
            f"({cfg.output_cap_bytes} bytes).",
        )

    return result


def execute_desktop_click(
    *,
    x: int | float,
    y: int | float,
    button: Literal["left", "right"] | None = None,
    config: DesktopServerConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> DesktopActionResult:
    """Move the pointer to ``(x, y)`` and click ``left`` (1) or ``right`` (3)."""
    cfg = config or DesktopServerConfig()
    raw_args: dict[str, Any] = {"x": x, "y": y}
    if button is not None:
        raw_args["button"] = button
    args = _validate_args("desktop.click", raw_args)
    display = _resolve_display(cfg)

    px = str(int(round(float(args["x"]))))
    py = str(int(round(float(args["y"]))))
    btn_code = "3" if args.get("button") == "right" else "1"

    _run_desktop_subprocess(
        ["xdotool", "mousemove", "--sync", "--", px, py],
        display=display,
        timeout_s=cfg.timeout_s,
        runner=runner,
    )
    _run_desktop_subprocess(
        ["xdotool", "click", "--", btn_code],
        display=display,
        timeout_s=cfg.timeout_s,
        runner=runner,
    )
    return DesktopActionResult(ok=True)


def execute_desktop_type(
    *,
    text: str,
    config: DesktopServerConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> DesktopActionResult:
    """Type literal text into the focused X11 window via ``xdotool``."""
    cfg = config or DesktopServerConfig()
    args = _validate_args("desktop.type", {"text": text})
    typed_text: str = args["text"]
    if "\x00" in typed_text:
        raise DesktopServerError(
            ErrorCode.ARG_INVALID,
            "Null byte is forbidden in desktop.type text.",
        )

    display = _resolve_display(cfg)
    _run_desktop_subprocess(
        ["xdotool", "type", "--clearmodifiers", "--", typed_text],
        display=display,
        timeout_s=cfg.timeout_s,
        runner=runner,
    )
    return DesktopActionResult(ok=True)


def execute_desktop_key(
    *,
    combo: str,
    config: DesktopServerConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> DesktopActionResult:
    """Send a validated X11 key or key combo (e.g. ``ctrl+c``) via ``xdotool``."""
    cfg = config or DesktopServerConfig()
    args = _validate_args("desktop.key", {"combo": combo})
    combo_val: str = args["combo"]

    if "\x00" in combo_val or not _KEY_COMBO_RE.match(combo_val):
        raise DesktopServerError(
            ErrorCode.ARG_INVALID,
            (
                f"Invalid key combo {combo_val!r}: "
                "expected X11 key tokens separated by '+'."
            ),
        )

    display = _resolve_display(cfg)
    _run_desktop_subprocess(
        ["xdotool", "key", "--clearmodifiers", "--", combo_val],
        display=display,
        timeout_s=cfg.timeout_s,
        runner=runner,
    )
    return DesktopActionResult(ok=True)


def create_desktop_server(
    config: DesktopServerConfig | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> MCPServer:
    """Build and return an ``MCPServer`` exposing the four desktop tools."""
    cfg = config or DesktopServerConfig(
        display=os.environ.get("DISPLAY"),
        timeout_s=float(
            os.environ.get("OVERWATCH_DESKTOP_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        ),
        output_cap_bytes=int(
            os.environ.get("OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES))
        ),
    )

    server = MCPServer(
        name="desktop",
        instructions="Non-root X11 desktop automation inside the session container.",
    )

    @server.tool(
        name="desktop.screenshot",
        description="Capture a PNG screenshot of the session X11 display.",
        structured_output=True,
    )
    def desktop_screenshot() -> dict[str, Any]:
        """Capture a screenshot of the configured X11 display."""
        try:
            return execute_desktop_screenshot(config=cfg, runner=runner).model_dump()
        except DesktopServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="desktop.click",
        description="Move the pointer to (x, y) and click left or right button.",
        structured_output=True,
    )
    def desktop_click(
        x: float,
        y: float,
        button: Literal["left", "right"] | None = None,
    ) -> dict[str, Any]:
        """Click at (x, y) on the configured X11 display."""
        try:
            return execute_desktop_click(
                x=x, y=y, button=button, config=cfg, runner=runner
            ).model_dump()
        except DesktopServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="desktop.type",
        description="Type literal text into the active window on the X11 display.",
        structured_output=True,
    )
    def desktop_type(text: str) -> dict[str, Any]:
        """Type literal text on the configured X11 display."""
        try:
            return execute_desktop_type(
                text=text, config=cfg, runner=runner
            ).model_dump()
        except DesktopServerError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="desktop.key",
        description="Press a key or key combination (e.g. ctrl+c) on the X11 display.",
        structured_output=True,
    )
    def desktop_key(combo: str) -> dict[str, Any]:
        """Send a key combination on the configured X11 display."""
        try:
            return execute_desktop_key(
                combo=combo, config=cfg, runner=runner
            ).model_dump()
        except DesktopServerError as exc:
            raise ToolError(str(exc)) from exc

    return server


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point running the desktop MCP server over stdio transport."""
    parser = argparse.ArgumentParser(
        description="OverWatch session-scoped desktop MCP server."
    )
    parser.add_argument(
        "--display",
        type=str,
        default=None,
        help="X11 DISPLAY string (defaults to DISPLAY environment variable).",
    )
    args = parser.parse_args(argv)
    cfg = DesktopServerConfig(
        display=args.display if args.display is not None else os.environ.get("DISPLAY"),
        timeout_s=float(
            os.environ.get("OVERWATCH_DESKTOP_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        ),
        output_cap_bytes=int(
            os.environ.get("OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES))
        ),
    )
    create_desktop_server(config=cfg).run(transport="stdio")


if __name__ == "__main__":
    main()

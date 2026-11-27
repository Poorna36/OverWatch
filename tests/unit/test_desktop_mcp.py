"""Unit tests for the Phase 3 in-house desktop MCP server (mcp/desktop/server.py)."""

from __future__ import annotations

import base64
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
from control_plane.contracts import ErrorCode
from mcp.server.mcpserver.exceptions import ToolError

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "mcp" / "desktop" / "server.py"


def _load_desktop_server_module():
    """Load mcp/desktop/server.py without shadowing the installed 'mcp' SDK."""
    spec = importlib.util.spec_from_file_location(
        "overwatch_desktop_server", SERVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["overwatch_desktop_server"] = module
    spec.loader.exec_module(module)
    return module


desk_mod = _load_desktop_server_module()
DesktopServerConfig = desk_mod.DesktopServerConfig
DesktopServerError = desk_mod.DesktopServerError
DesktopScreenshotResult = desk_mod.DesktopScreenshotResult
DesktopActionResult = desk_mod.DesktopActionResult
create_desktop_server = desk_mod.create_desktop_server
execute_desktop_screenshot = desk_mod.execute_desktop_screenshot
execute_desktop_click = desk_mod.execute_desktop_click
execute_desktop_type = desk_mod.execute_desktop_type
execute_desktop_key = desk_mod.execute_desktop_key


def _make_fake_png(width: int = 800, height: int = 600, extra: bytes = b"") -> bytes:
    """Build minimal valid PNG bytes with an IHDR chunk for dimensions."""
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_chunk = struct.pack(">I", 13) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + ihdr_chunk + extra


def test_desktop_click_xdotool_argv_and_shell_false() -> None:
    """desktop.click invokes xdotool via argv lists with shell=False and DISPLAY."""
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
    )
    cfg = DesktopServerConfig(display=":99")

    res = execute_desktop_click(
        x=120, y=240, button="right", config=cfg, runner=mock_runner
    )
    assert isinstance(res, DesktopActionResult)
    assert res.model_dump() == {"ok": True}
    assert mock_runner.call_count == 2

    move_call, click_call = mock_runner.call_args_list
    assert move_call.args[0] == ["xdotool", "mousemove", "--sync", "--", "120", "240"]
    assert move_call.kwargs["shell"] is False
    assert move_call.kwargs["env"]["DISPLAY"] == ":99"

    assert click_call.args[0] == ["xdotool", "click", "--", "3"]
    assert click_call.kwargs["shell"] is False


def test_desktop_type_option_injection_and_metachars_safe() -> None:
    """desktop.type terminates options with '--' and passes text literally."""
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
    )
    cfg = DesktopServerConfig(display=":99")

    tricky_text = "--clearmodifiers --window 0; rm -rf / && $(id)"
    res = execute_desktop_type(text=tricky_text, config=cfg, runner=mock_runner)
    assert res.model_dump() == {"ok": True}

    argv = mock_runner.call_args.args[0]
    assert argv == ["xdotool", "type", "--clearmodifiers", "--", tricky_text]
    assert mock_runner.call_args.kwargs["shell"] is False


def test_desktop_key_valid_and_rejected_inputs() -> None:
    """desktop.key allows valid X11 combos and rejects option/shell injection."""
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
    )
    cfg = DesktopServerConfig(display=":99")

    res = execute_desktop_key(combo="ctrl+shift+C", config=cfg, runner=mock_runner)
    assert res.model_dump() == {"ok": True}
    assert mock_runner.call_args.args[0] == [
        "xdotool",
        "key",
        "--clearmodifiers",
        "--",
        "ctrl+shift+C",
    ]
    assert mock_runner.call_args.kwargs["shell"] is False

    for bad_combo in [
        "--help",
        "-e",
        "ctrl+c; rm -rf /",
        "ctrl + c",
        "Return\n",
        "ctrl+",
        "+a",
        "a\x00b",
    ]:
        with pytest.raises(DesktopServerError) as exc_info:
            execute_desktop_key(combo=bad_combo, config=cfg, runner=mock_runner)
        assert exc_info.value.code == ErrorCode.ARG_INVALID


def test_missing_or_invalid_display_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable or malformed DISPLAY fails safely without invoking xdotool."""
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_runner = MagicMock()

    with pytest.raises(DesktopServerError) as exc_missing:
        execute_desktop_click(
            x=10,
            y=20,
            config=DesktopServerConfig(display=None),
            runner=mock_runner,
        )
    assert exc_missing.value.code == ErrorCode.UPSTREAM_ERROR
    mock_runner.assert_not_called()

    for bad_display in ["", "  ", ":0\n", ":0;evil", ":0\x00"]:
        with pytest.raises(DesktopServerError) as exc_bad:
            execute_desktop_type(
                text="hi",
                config=DesktopServerConfig(display=bad_display),
                runner=mock_runner,
            )
        assert exc_bad.value.code in (ErrorCode.ARG_INVALID, ErrorCode.UPSTREAM_ERROR)
    mock_runner.assert_not_called()


def test_invalid_click_and_type_inputs_rejected() -> None:
    """Out-of-range coordinates, invalid buttons, and null-byte text fail."""
    cfg = DesktopServerConfig(display=":99")
    mock_runner = MagicMock()

    for kwargs in [
        {"x": -1, "y": 10},
        {"x": 10, "y": float("nan")},
        {"x": True, "y": 10},
        {"x": 10, "y": 10, "button": "middle"},
    ]:
        with pytest.raises(DesktopServerError) as exc_info:
            execute_desktop_click(config=cfg, runner=mock_runner, **kwargs)
        assert exc_info.value.code == ErrorCode.ARG_INVALID

    with pytest.raises(DesktopServerError) as exc_type:
        execute_desktop_type(text="bad\x00text", config=cfg, runner=mock_runner)
    assert exc_type.value.code == ErrorCode.ARG_INVALID
    mock_runner.assert_not_called()


def test_xdotool_failures_and_timeouts() -> None:
    """Non-zero exit, OSError, and TimeoutExpired fail safely with documented codes."""
    cfg = DesktopServerConfig(display=":99", timeout_s=5.0)

    nonzero_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["xdotool"], returncode=1, stdout=b"", stderr=b"Can't open display"
        )
    )
    with pytest.raises(DesktopServerError) as exc_nz:
        execute_desktop_key(combo="Return", config=cfg, runner=nonzero_runner)
    assert exc_nz.value.code == ErrorCode.UPSTREAM_ERROR

    oserr_runner = MagicMock(side_effect=FileNotFoundError("xdotool not found"))
    with pytest.raises(DesktopServerError) as exc_os:
        execute_desktop_type(text="hi", config=cfg, runner=oserr_runner)
    assert exc_os.value.code == ErrorCode.UPSTREAM_ERROR

    timeout_runner = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd=["xdotool"], timeout=5.0)
    )
    with pytest.raises(DesktopServerError) as exc_to:
        execute_desktop_click(x=0, y=0, config=cfg, runner=timeout_runner)
    assert exc_to.value.code == ErrorCode.TIMEOUT


def test_screenshot_encoding_dimensions_and_output_cap(tmp_path: Path) -> None:
    """desktop.screenshot returns base64 PNG + dimensions and enforces output cap."""
    png_data = _make_fake_png(1280, 720, extra=b"payload")

    def fake_scrot(argv, **kwargs):
        assert argv[0:3] == ["scrot", "-o", "--"]
        assert kwargs["shell"] is False
        assert kwargs["env"]["DISPLAY"] == ":99"
        Path(argv[3]).write_bytes(png_data)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=b"", stderr=b""
        )

    cfg = DesktopServerConfig(display=":99", output_cap_bytes=200_000)
    res = execute_desktop_screenshot(config=cfg, runner=fake_scrot, temp_dir=tmp_path)

    assert isinstance(res, DesktopScreenshotResult)
    assert res.width == 1280
    assert res.height == 720
    assert base64.b64decode(res.image_b64) == png_data
    assert set(res.model_dump().keys()) == {"image_b64", "width", "height"}
    # Temp file cleaned up
    assert list(tmp_path.iterdir()) == []

    # Output cap exceeded -> fails safely
    small_cfg = DesktopServerConfig(display=":99", output_cap_bytes=50)
    with pytest.raises(DesktopServerError) as exc_cap:
        execute_desktop_screenshot(
            config=small_cfg, runner=fake_scrot, temp_dir=tmp_path
        )
    assert exc_cap.value.code == ErrorCode.UPSTREAM_ERROR
    assert "output cap" in exc_cap.value.message

    # Invalid PNG bytes -> fails safely
    def bad_scrot(argv, **kwargs):
        Path(argv[3]).write_bytes(b"not-a-png")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=b"", stderr=b""
        )

    with pytest.raises(DesktopServerError) as exc_png:
        execute_desktop_screenshot(config=cfg, runner=bad_scrot, temp_dir=tmp_path)
    assert exc_png.value.code == ErrorCode.UPSTREAM_ERROR


def test_mcp_sdk_tool_schemas_and_calls(tmp_path: Path) -> None:
    """MCPServer exposes all 4 desktop tools with exact contract shapes."""
    png_data = _make_fake_png(640, 480)

    def fake_runner(argv, **kwargs):
        if argv[0] == "scrot":
            Path(argv[3]).write_bytes(png_data)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=b"", stderr=b""
        )

    cfg = DesktopServerConfig(display=":99")
    server = create_desktop_server(config=cfg, runner=fake_runner)

    async def _exercise() -> None:
        tools = await server.list_tools()
        by_name = {t.name: t for t in tools}
        assert set(by_name.keys()) == {
            "desktop.screenshot",
            "desktop.click",
            "desktop.type",
            "desktop.key",
        }
        assert (
            set(by_name["desktop.screenshot"].input_schema["properties"].keys())
            == set()
        )
        assert set(by_name["desktop.click"].input_schema["properties"].keys()) == {
            "x",
            "y",
            "button",
        }
        assert by_name["desktop.type"].input_schema["required"] == ["text"]
        assert by_name["desktop.key"].input_schema["required"] == ["combo"]

        shot_res = await server.call_tool("desktop.screenshot", {})
        assert shot_res.is_error is False
        assert shot_res.structured_content == {
            "image_b64": base64.b64encode(png_data).decode("ascii"),
            "width": 640,
            "height": 480,
        }

        click_res = await server.call_tool(
            "desktop.click", {"x": 50, "y": 60, "button": "left"}
        )
        assert click_res.structured_content == {"ok": True}

        type_res = await server.call_tool("desktop.type", {"text": "hello"})
        assert type_res.structured_content == {"ok": True}

        key_res = await server.call_tool("desktop.key", {"combo": "Return"})
        assert key_res.structured_content == {"ok": True}

        with pytest.raises(ToolError) as err_exc:
            await server.call_tool("desktop.key", {"combo": "--bad"})
        assert ErrorCode.ARG_INVALID in str(err_exc.value)

    anyio.run(_exercise)


def test_entrypoint_launches_from_repo_root() -> None:
    """Launching mcp/desktop/server.py from repo root does not shadow mcp SDK."""
    proc = subprocess.run(
        [sys.executable, str(SERVER_PATH), "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OverWatch session-scoped desktop MCP server" in proc.stdout

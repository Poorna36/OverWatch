"""Unit tests for the Phase 3 in-house terminal MCP server (mcp/terminal/server.py)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
from control_plane.contracts import ErrorCode
from mcp.server.mcpserver.exceptions import ToolError

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "mcp" / "terminal" / "server.py"


def _load_terminal_server_module():
    """Load mcp/terminal/server.py without shadowing the installed 'mcp' SDK."""
    spec = importlib.util.spec_from_file_location(
        "overwatch_terminal_server", SERVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["overwatch_terminal_server"] = module
    spec.loader.exec_module(module)
    return module


term_mod = _load_terminal_server_module()
TerminalServerConfig = term_mod.TerminalServerConfig
TerminalServerError = term_mod.TerminalServerError
TerminalRunResult = term_mod.TerminalRunResult
create_terminal_server = term_mod.create_terminal_server
execute_terminal_run = term_mod.execute_terminal_run


def test_valid_execution_and_nonzero_exit_code(tmp_path: Path) -> None:
    """Valid command executes in workspace root and returns exact contract fields."""
    sub = tmp_path / "proj"
    sub.mkdir()
    cfg = TerminalServerConfig(workspace_root=tmp_path)

    res = execute_terminal_run(
        command='python3 -c "import os; print(os.getcwd())"',
        cwd="proj",
        config=cfg,
    )
    assert isinstance(res, TerminalRunResult)
    assert res.model_dump() == {
        "exit_code": 0,
        "stdout": f"{sub.resolve()}\n",
        "stderr": "",
    }

    fail_res = execute_terminal_run(
        command="python3 -c \"import sys; sys.stderr.write('oops'); sys.exit(3)\"",
        config=cfg,
    )
    assert fail_res.exit_code == 3
    assert fail_res.stdout == ""
    assert fail_res.stderr == "oops"


def test_shell_metacharacters_passed_as_literal_argv(tmp_path: Path) -> None:
    """Shell operators (;, &&, |, $()) are passed as argv text with shell=False."""
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
    )
    cfg = TerminalServerConfig(workspace_root=tmp_path)

    execute_terminal_run(
        command="echo hello; rm -rf / && $(id) | cat",
        config=cfg,
        runner=mock_runner,
    )

    mock_runner.assert_called_once()
    args, kwargs = mock_runner.call_args
    assert args[0] == ["echo", "hello;", "rm", "-rf", "/", "&&", "$(id)", "|", "cat"]
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(tmp_path.resolve())

    # Also verify with real 'echo' that metacharacters are not interpreted by a shell
    real_res = execute_terminal_run(
        command="echo hello; whoami && $(id)",
        config=cfg,
    )
    assert real_res.exit_code == 0
    assert real_res.stdout.strip() == "hello; whoami && $(id)"


@pytest.mark.parametrize(
    "bad_cwd",
    [
        "../etc",
        "sub/../../etc",
        "/etc",
        "/workspace/sub",
        "bad\x00dir",
    ],
)
def test_cwd_path_jail_rejects_escapes(tmp_path: Path, bad_cwd: str) -> None:
    """Working directories outside workspace_root are rejected with PATH_JAIL."""
    cfg = TerminalServerConfig(workspace_root=tmp_path)
    with pytest.raises(TerminalServerError) as exc_info:
        execute_terminal_run(command="echo hi", cwd=bad_cwd, config=cfg)
    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_cwd_symlink_escape_rejected(tmp_path: Path) -> None:
    """Symlinked cwd pointing outside workspace_root is rejected with PATH_JAIL."""
    outside = tmp_path.parent / "outside_dir_for_term_test"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape_dir"
    link.symlink_to(outside)

    cfg = TerminalServerConfig(workspace_root=tmp_path)
    try:
        with pytest.raises(TerminalServerError) as exc_info:
            execute_terminal_run(command="echo hi", cwd="escape_dir", config=cfg)
        assert exc_info.value.code == ErrorCode.PATH_JAIL
    finally:
        outside.rmdir()


def test_missing_cwd_and_invalid_command_rejected(tmp_path: Path) -> None:
    """Missing cwd or empty/malformed command input fails with ARG_INVALID."""
    cfg = TerminalServerConfig(workspace_root=tmp_path)

    with pytest.raises(TerminalServerError) as exc_cwd:
        execute_terminal_run(command="echo hi", cwd="does_not_exist", config=cfg)
    assert exc_cwd.value.code == ErrorCode.ARG_INVALID

    for bad_cmd in ["", "   ", "echo 'unclosed", "echo\x00hi"]:
        with pytest.raises(TerminalServerError) as exc_cmd:
            execute_terminal_run(command=bad_cmd, config=cfg)
        assert exc_cmd.value.code == ErrorCode.ARG_INVALID


def test_timeout_handling_and_policy_clamping(tmp_path: Path) -> None:
    """Timeouts fail safely with ErrorCode.TIMEOUT and timeout_s is clamped."""
    mock_runner = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd=["sleep", "60"], timeout=10.0)
    )
    cfg = TerminalServerConfig(
        workspace_root=tmp_path, default_timeout_s=30.0, max_timeout_s=10.0
    )

    with pytest.raises(TerminalServerError) as exc_info:
        execute_terminal_run(
            command="sleep 60",
            timeout_s=999.0,
            config=cfg,
            runner=mock_runner,
        )
    assert exc_info.value.code == ErrorCode.TIMEOUT
    assert mock_runner.call_args.kwargs["timeout"] == 10.0

    for bad_timeout in [0, -1, float("nan"), float("inf"), True]:
        with pytest.raises(TerminalServerError) as exc_bad:
            execute_terminal_run(
                command="echo hi", timeout_s=bad_timeout, config=cfg  # type: ignore[arg-type]
            )
        assert exc_bad.value.code == ErrorCode.ARG_INVALID


def test_process_error_fails_safely(tmp_path: Path) -> None:
    """Missing binary or OSError fails safely with ErrorCode.UPSTREAM_ERROR."""
    mock_runner = MagicMock(side_effect=FileNotFoundError("No such file"))
    cfg = TerminalServerConfig(workspace_root=tmp_path)

    with pytest.raises(TerminalServerError) as exc_info:
        execute_terminal_run(
            command="nonexistent_bin",
            config=cfg,
            runner=mock_runner,
        )
    assert exc_info.value.code == ErrorCode.UPSTREAM_ERROR


def test_output_truncation_enforces_cap(tmp_path: Path) -> None:
    """Combined stdout + stderr is capped to output_cap_bytes."""
    mock_runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["cat"],
            returncode=0,
            stdout="A" * 150,
            stderr="B" * 150,
        )
    )
    cfg = TerminalServerConfig(workspace_root=tmp_path, output_cap_bytes=100)

    res = execute_terminal_run(command="cat", config=cfg, runner=mock_runner)
    total_bytes = len(res.stdout.encode("utf-8")) + len(res.stderr.encode("utf-8"))
    assert total_bytes <= 100
    assert res.exit_code == 0
    assert set(res.model_dump().keys()) == {"exit_code", "stdout", "stderr"}


def test_mcp_sdk_tool_contract_and_error_handling(tmp_path: Path) -> None:
    """MCPServer exposes terminal.run with exact contract input/output shapes."""
    cfg = TerminalServerConfig(workspace_root=tmp_path)
    server = create_terminal_server(config=cfg)

    async def _exercise() -> None:
        tools = await server.list_tools()
        assert [t.name for t in tools] == ["terminal.run"]
        schema = tools[0].input_schema
        assert schema["required"] == ["command"]
        assert set(schema["properties"].keys()) == {"command", "cwd", "timeout_s"}

        ok_res = await server.call_tool("terminal.run", {"command": "echo mcp_ok"})
        assert ok_res.is_error is False
        assert ok_res.structured_content == {
            "exit_code": 0,
            "stdout": "mcp_ok\n",
            "stderr": "",
        }

        with pytest.raises(ToolError) as err_exc:
            await server.call_tool(
                "terminal.run", {"command": "echo nope", "cwd": "../escape"}
            )
        assert ErrorCode.PATH_JAIL in str(err_exc.value)

    anyio.run(_exercise)

"""Focused unit tests for per-session Docker-exec stdio MCP runtime."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import anyio
import docker.errors
import pytest
from control_plane.agent.llm import (
    LlmMessage,
    LlmToolCall,
    LlmTurnResponse,
)
from control_plane.agent.loop import AgentLoop
from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import ErrorCode, Policy, Profile, ToolCall
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.mcp_runtime import (
    MCP_SERVER_COMMANDS,
    ContainerPathJail,
    McpContainerError,
    McpRuntimeClosedError,
    McpToolExecutionError,
    McpTransportError,
    McpUnsupportedServerError,
    SessionMcpRuntime,
    encode_docker_frame,
)
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _SocketBackedWriteStream:
    """Writes MCP ServerSession stdout JSON-RPC lines as Docker stdout frames."""

    def __init__(self, server_sock: socket.socket) -> None:
        self._sock = server_sock

    async def send(self, session_message: SessionMessage) -> None:
        line = (
            session_message.message.model_dump_json(by_alias=True, exclude_unset=True)
            + "\n"
        ).encode("utf-8")
        frame = encode_docker_frame(1, line)
        await anyio.to_thread.run_sync(self._sock.sendall, frame)

    async def aclose(self) -> None:
        pass

    async def __aenter__(self) -> _SocketBackedWriteStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


def _bridge_mcp_server_over_docker_socket(
    mcp_server: MCPServer,
    server_sock: socket.socket,
    *,
    stderr_noise: bytes | None = None,
) -> threading.Thread:
    """Run a real ``MCPServer`` in-thread communicating over a Docker-framed sock."""
    from mcp import types

    async def _serve() -> None:
        if stderr_noise:
            await anyio.to_thread.run_sync(
                server_sock.sendall, encode_docker_frame(2, stderr_noise)
            )
        loop = asyncio.get_running_loop()
        read_writer, read_stream = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](0)
        write_stream = _SocketBackedWriteStream(server_sock)

        def _read_stdin() -> None:
            buf = ""
            try:
                while True:
                    chunk = server_sock.recv(4096)
                    if not chunk:
                        break
                    lines = (buf + chunk.decode("utf-8")).split("\n")
                    buf = lines.pop()
                    for line in lines:
                        if line.strip():
                            msg = types.jsonrpc_message_adapter.validate_json(
                                line.strip(), by_name=False
                            )
                            asyncio.run_coroutine_threadsafe(
                                read_writer.send(SessionMessage(msg)),
                                loop,
                            ).result()
            except Exception:
                pass
            finally:
                try:
                    asyncio.run_coroutine_threadsafe(read_writer.aclose(), loop).result(
                        timeout=1.0
                    )
                except Exception:
                    pass

        async with read_stream:
            reader = threading.Thread(target=_read_stdin, daemon=True)
            reader.start()
            try:
                await mcp_server._lowlevel_server.run(
                    read_stream,
                    write_stream,
                    mcp_server._lowlevel_server.create_initialization_options(),
                )
            finally:
                try:
                    server_sock.close()
                except OSError:
                    pass
                reader.join(timeout=2.0)

    thread = threading.Thread(target=lambda: anyio.run(_serve), daemon=True)
    thread.start()
    return thread


class _FakeContainer:
    def __init__(
        self,
        *,
        cid: str = "cid_session_1",
        status: str = "running",
        labels: dict[str, str] | None = None,
    ) -> None:
        self.id = cid
        self.status = status
        self.labels = (
            {
                "overwatch.managed": "true",
                "overwatch.session_id": SESSION_ID,
            }
            if labels is None
            else labels
        )
        self.attrs = {
            "State": {"Status": status, "Running": status == "running"},
            "Config": {"Labels": dict(self.labels)},
        }


class _FakeDockerClient:
    """In-memory Docker SDK client stub dispatching exec stdio over socketpairs."""

    def __init__(
        self,
        container: _FakeContainer | None = None,
        *,
        container_error: Exception | None = None,
        server_builders: dict[str, Callable[[], MCPServer]] | None = None,
        probe_exit_codes: dict[str, list[int]] | None = None,
    ) -> None:
        self.container = container or _FakeContainer()
        self.container_error = container_error
        self.server_builders = server_builders or {}
        self.probe_exit_codes = {
            k: list(v) for k, v in (probe_exit_codes or {}).items()
        }
        self.exec_creates: list[dict[str, Any]] = []
        self.exec_starts: list[dict[str, Any]] = []
        self.client_sockets: list[socket.socket] = []
        self.server_threads: list[threading.Thread] = []
        self._exec_seq = 0
        self._exec_records: dict[str, dict[str, Any]] = {}

        self.containers = MagicMock()
        self.containers.get.side_effect = self._get_container

        self.api = MagicMock()
        self.api.exec_create.side_effect = self._exec_create
        self.api.exec_start.side_effect = self._exec_start
        self.api.exec_inspect.side_effect = self._exec_inspect

    def _get_container(self, ref: str) -> _FakeContainer:
        if self.container_error is not None:
            raise self.container_error
        return self.container

    def _exec_create(
        self, container: str, cmd: list[str], **kwargs: Any
    ) -> dict[str, str]:
        self._exec_seq += 1
        exec_id = f"exec_{self._exec_seq}"
        rec = {"container": container, "cmd": list(cmd), **kwargs, "exec_id": exec_id}
        self.exec_creates.append(rec)
        self._exec_records[exec_id] = rec
        return {"Id": exec_id}

    def _exec_start(self, exec_id: str, **kwargs: Any) -> Any:
        rec = self._exec_records[exec_id]
        self.exec_starts.append({"exec_id": exec_id, **kwargs})
        cmd = rec["cmd"]
        if kwargs.get("socket"):
            for server_name, expected_cmd in MCP_SERVER_COMMANDS.items():
                if tuple(cmd) == expected_cmd:
                    builder = self.server_builders[server_name]
                    c_sock, s_sock = socket.socketpair()
                    self.client_sockets.append(c_sock)
                    srv = builder()
                    t = _bridge_mcp_server_over_docker_socket(
                        srv, s_sock, stderr_noise=b"server starting\n"
                    )
                    self.server_threads.append(t)
                    return c_sock
            raise AssertionError(f"Unexpected stdio command: {cmd}")
        return b""

    def _exec_inspect(self, exec_id: str) -> dict[str, Any]:
        rec = self._exec_records[exec_id]
        prog = rec["cmd"][0]
        if prog in self.probe_exit_codes and self.probe_exit_codes[prog]:
            return {"ExitCode": self.probe_exit_codes[prog].pop(0), "Pid": 420}
        return {"ExitCode": 0, "Pid": 420 + int(exec_id.split("_")[1])}


def _make_stateful_browser_mock() -> MCPServer:
    """In-memory stateful MCPServer verifying browser.open -> browser.read_page."""
    srv = MCPServer(name="browser")
    state: dict[str, str] = {"title": "", "body": ""}

    @srv.tool(name="browser.open", structured_output=True)
    def browser_open(url: str) -> dict[str, Any]:
        state["title"] = f"Title for {url}"
        state["body"] = f"Loaded content from {url}"
        return {"title": state["title"], "status": 200}

    @srv.tool(name="browser.read_page", structured_output=True)
    def browser_read_page(max_chars: int | None = None) -> dict[str, Any]:
        text = state["body"]
        if max_chars is not None:
            text = text[:max_chars]
        return {"text": text}

    return srv


# ---------------------------------------------------------------------------
# 1. Container Validation, Server Selection & Fixed Command Construction
# ---------------------------------------------------------------------------
def test_rejects_missing_stopped_and_mismatched_containers() -> None:
    """verify_container fails closed on missing, stopped, or wrong-label container."""
    profile = load_profile("coding")

    client_not_found = _FakeDockerClient(
        container_error=docker.errors.NotFound("missing")
    )
    rt_missing = SessionMcpRuntime(
        docker_client=client_not_found, session_id=SESSION_ID, profile=profile
    )
    try:
        with pytest.raises(McpContainerError, match="not found"):
            rt_missing.verify_container()
    finally:
        rt_missing.close()

    for bad_status, bad_labels, match_msg in [
        (
            "exited",
            {"overwatch.managed": "true", "overwatch.session_id": SESSION_ID},
            "not running",
        ),
        (
            "running",
            {"overwatch.session_id": SESSION_ID},
            "overwatch.managed=true",
        ),
        (
            "running",
            {
                "overwatch.managed": "true",
                "overwatch.session_id": "s_01ABCDEFGHJKMNPTVW99999999",
            },
            "does not match",
        ),
        (
            "running",
            {
                "overwatch.managed": "true",
                "overwatch.session_id": SESSION_ID,
                "overwatch.profile": "general",
            },
            "profile label",
        ),
    ]:
        client = _FakeDockerClient(
            container=_FakeContainer(status=bad_status, labels=bad_labels)
        )
        rt = SessionMcpRuntime(
            docker_client=client, session_id=SESSION_ID, profile=profile
        )
        try:
            with pytest.raises(McpContainerError, match=match_msg):
                rt.verify_container()
            assert client.exec_creates == []
        finally:
            rt.close()


def test_rejects_profile_disallowed_and_unsupported_servers() -> None:
    """Tools outside profile or on unsupported servers fail closed without exec."""
    coding_profile = load_profile("coding")
    client = _FakeDockerClient()
    rt = SessionMcpRuntime(
        docker_client=client, session_id=SESSION_ID, profile=coding_profile
    )
    try:
        with pytest.raises(
            McpUnsupportedServerError, match="not permitted"
        ) as exc_info:
            rt.call_tool("browser.open", {"url": "https://example.com"})
        assert exc_info.value.code == ErrorCode.NOT_IN_PROFILE
        assert client.exec_creates == []
    finally:
        rt.close()

    custom_profile = Profile.model_validate(
        {
            "name": "calendar_only",
            "role": "routine",
            "system_prompt_file": "prompts/coder.md",
            "image": "pocket/py-node:1.0",
            "mcp_servers": {"calendar": {}},
        }
    )
    client2 = _FakeDockerClient(
        container=_FakeContainer(
            labels={
                "overwatch.managed": "true",
                "overwatch.session_id": SESSION_ID,
            }
        )
    )
    rt2 = SessionMcpRuntime(
        docker_client=client2, session_id=SESSION_ID, profile=custom_profile
    )
    try:
        with pytest.raises(
            McpUnsupportedServerError, match="not packaged or supported"
        ) as exc2:
            rt2.call_tool("calendar.list_events", {})
        assert exc2.value.code == ErrorCode.UPSTREAM_ERROR
        assert client2.exec_creates == []
    finally:
        rt2.close()


# ---------------------------------------------------------------------------
# 2. Persistent Stateful Calls, Fixed Commands, and Tool Result Conversion
# ---------------------------------------------------------------------------
def test_persistent_stateful_browser_and_fixed_exec_commands(tmp_path: Path) -> None:
    """browser.open -> browser.read_page reuses one exec client & fixed argv."""
    files_mod = _load_module("test_rt_files_srv", "mcp/files/server.py")
    term_mod = _load_module("test_rt_term_srv", "mcp/terminal/server.py")

    ws = tmp_path / "workspace"
    ws.mkdir()

    general_profile = load_profile("general")
    coding_profile = load_profile("coding")

    client_gen = _FakeDockerClient(
        server_builders={
            "browser": _make_stateful_browser_mock,
            "files": lambda: files_mod.create_files_server(
                files_mod.FilesServerConfig(workspace_root=ws)
            ),
        }
    )
    rt_gen = SessionMcpRuntime(
        docker_client=client_gen,
        session_id=SESSION_ID,
        profile=general_profile,
    )
    try:
        open_res = rt_gen.call_tool("browser.open", {"url": "https://docs.python.org"})
        assert open_res == {"title": "Title for https://docs.python.org", "status": 200}

        read_res = rt_gen.call_tool("browser.read_page", {"max_chars": 25})
        assert read_res == {"text": "Loaded content from https"}

        # Verify ONLY ONE persistent exec process was created for browser
        browser_execs = [
            e
            for e in client_gen.exec_creates
            if tuple(e["cmd"]) == MCP_SERVER_COMMANDS["browser"]
        ]
        assert len(browser_execs) == 1
        assert browser_execs[0]["user"] == "1000:1000"
        assert browser_execs[0]["workdir"] == "/workspace"
        assert browser_execs[0]["tty"] is False
        assert browser_execs[0]["privileged"] is False
    finally:
        rt_gen.close()

    # Terminal server uses fixed argv even when command contains shell metacharacters
    client_code = _FakeDockerClient(
        server_builders={
            "terminal": lambda: term_mod.create_terminal_server(
                term_mod.TerminalServerConfig(workspace_root=ws)
            )
        }
    )
    rt_code = SessionMcpRuntime(
        docker_client=client_code, session_id=SESSION_ID, profile=coding_profile
    )
    try:
        res = rt_code.call_tool("terminal.run", {"command": "echo hello; $(id)"})
        assert res["exit_code"] == 0
        assert res["stdout"] == "hello; $(id)\n"
        assert tuple(client_code.exec_creates[0]["cmd"]) == (
            MCP_SERVER_COMMANDS["terminal"]
        )
        assert "hello" not in json.dumps(client_code.exec_creates)
    finally:
        rt_code.close()


# ---------------------------------------------------------------------------
# 3. Desktop Xvfb/Openbox Setup (No KasmVNC)
# ---------------------------------------------------------------------------
def test_desktop_starts_xvfb_and_openbox_and_sets_display() -> None:
    """Desktop launch ensures Xvfb + openbox (never KasmVNC) and sets DISPLAY."""
    coding_profile = load_profile("coding")
    fake_desktop = MCPServer(name="desktop")

    @fake_desktop.tool(name="desktop.click", structured_output=True)
    def desktop_click(x: float, y: float, button: str | None = None) -> dict[str, Any]:
        return {"ok": True}

    client = _FakeDockerClient(
        server_builders={"desktop": lambda: fake_desktop},
        # First xset probe -> not running (1); second probe -> ready (0)
        probe_exit_codes={"xset": [1, 0]},
    )
    rt = SessionMcpRuntime(
        docker_client=client,
        session_id=SESSION_ID,
        profile=coding_profile,
        display=":99",
    )
    try:
        out = rt.call_tool("desktop.click", {"x": 10, "y": 20})
        assert out == {"ok": True}

        cmds = [rec["cmd"] for rec in client.exec_creates]
        assert cmds[0] == ["xset", "-display", ":99", "q"]
        assert cmds[1] == ["mkdir", "-p", "-m", "1777", "/tmp/.X11-unix"]
        assert cmds[2] == [
            "Xvfb",
            ":99",
            "-screen",
            "0",
            "1280x720x24",
            "-nolisten",
            "tcp",
        ]
        assert cmds[3] == ["xset", "-display", ":99", "q"]
        assert cmds[4] == ["openbox"]
        assert client.exec_creates[4]["environment"] == {"DISPLAY": ":99"}
        assert tuple(cmds[5]) == MCP_SERVER_COMMANDS["desktop"]
        assert client.exec_creates[5]["environment"] == {"DISPLAY": ":99"}

        # Never starts or references KasmVNC
        assert all("kasm" not in json.dumps(c).lower() for c in cmds)
    finally:
        rt.close()

    # Shutdown cleanly sends TERM to recorded Xvfb/openbox PIDs
    kill_cmds = [rec["cmd"] for rec in client.exec_creates if rec["cmd"][0] == "kill"]
    assert len(kill_cmds) == 2


def test_desktop_fails_closed_if_xvfb_never_becomes_ready() -> None:
    """If Xvfb never passes xset readiness check, desktop fails closed."""
    coding_profile = load_profile("coding")
    client = _FakeDockerClient(probe_exit_codes={"xset": [1, 1, 1, 1, 1]})
    rt = SessionMcpRuntime(
        docker_client=client,
        session_id=SESSION_ID,
        profile=coding_profile,
        display_wait_s=0.05,
    )
    try:
        with pytest.raises(McpTransportError, match="Timed out waiting for Xvfb"):
            rt.call_tool("desktop.screenshot", {})
        assert not any(
            tuple(r["cmd"]) == MCP_SERVER_COMMANDS["desktop"]
            for r in client.exec_creates
        )
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# 4. Tool Errors, Transport Failures & Shutdown Semantics
# ---------------------------------------------------------------------------
def test_mcp_tool_errors_and_transport_failures_fail_closed(tmp_path: Path) -> None:
    """MCP is_error preserves ErrorCode; broken exec socket fails closed."""
    files_mod = _load_module("test_rt_files_srv_err", "mcp/files/server.py")
    ws = tmp_path / "ws"
    ws.mkdir()

    coding_profile = load_profile("coding")
    client = _FakeDockerClient(
        server_builders={
            "files": lambda: files_mod.create_files_server(
                files_mod.FilesServerConfig(workspace_root=ws)
            )
        }
    )
    rt = SessionMcpRuntime(
        docker_client=client, session_id=SESSION_ID, profile=coding_profile
    )
    try:
        with pytest.raises(McpToolExecutionError) as err:
            rt.call_tool("files.read", {"path": "../etc/passwd"})
        assert err.value.code == ErrorCode.PATH_JAIL

        # Simulate container exec crash mid-session
        for sock in client.client_sockets:
            sock.close()
        with pytest.raises(McpTransportError):
            rt.call_tool("files.list", {"path": "."})
    finally:
        rt.close()

    with pytest.raises(McpRuntimeClosedError):
        rt.call_tool("files.list", {"path": "."})


def test_empty_socket_close_fails_closed_at_startup() -> None:
    """If exec socket hits immediate EOF or APIError, startup fails closed."""
    coding_profile = load_profile("coding")

    class _ImmediateEofClient(_FakeDockerClient):
        def _exec_start(self, exec_id: str, **kwargs: Any) -> Any:
            return io.BytesIO(b"")

    client = _ImmediateEofClient()
    rt = SessionMcpRuntime(
        docker_client=client, session_id=SESSION_ID, profile=coding_profile
    )
    try:
        with pytest.raises(McpTransportError):
            rt.call_tool("files.list", {"path": "."})
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# 5. Compatibility with AuditedGatewayCoordinator and AgentLoop
# ---------------------------------------------------------------------------
def test_coordinator_and_agent_loop_execute_through_runtime(
    tmp_path: Path,
) -> None:
    """SessionMcpRuntime plugs directly into AuditedGatewayCoordinator & AgentLoop."""
    files_mod = _load_module("test_rt_files_srv_loop", "mcp/files/server.py")
    ws = tmp_path / "ws"
    ws.mkdir()

    policy: Policy = load_policy()
    profile: Profile = load_profile("coding")

    db_path = tmp_path / "loop_runtime.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        s.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_test",
                created_at="2026-11-27T10:00:00Z",
                updated_at="2026-11-27T10:00:00Z",
            )
        )
        s.commit()

    client = _FakeDockerClient(
        server_builders={
            "files": lambda: files_mod.create_files_server(
                files_mod.FilesServerConfig(workspace_root=ws)
            )
        }
    )

    class _ScriptedLlm:
        def __init__(self) -> None:
            self._step = 0

        def next_turn(
            self,
            *,
            system_prompt: str,
            messages: list[LlmMessage],
            tools: Any,
        ) -> LlmTurnResponse:
            self._step += 1
            if self._step == 1:
                return LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c1",
                            tool="files.write",
                            args={"path": "hello.txt", "content": "from-runtime"},
                        ),
                    )
                )
            if self._step == 2:
                return LlmTurnResponse(
                    tool_calls=(
                        LlmToolCall(
                            call_id="c2",
                            tool="files.read",
                            args={"path": "hello.txt"},
                        ),
                    )
                )
            assert any(
                m.role == "tool" and "from-runtime" in m.content for m in messages
            )
            return LlmTurnResponse(assistant_text="Verified hello.txt via Docker MCP.")

    runtime = SessionMcpRuntime(
        docker_client=client, session_id=SESSION_ID, profile=profile
    )
    try:
        coordinator = AuditedGatewayCoordinator(
            pipeline=GatewayPreflightPipeline(policy, workspace_root=ws),
            audit_writer=AuditWriter(factory),
        )
        event_bus = EventBus(factory)
        loop = AgentLoop(
            llm=_ScriptedLlm(),
            coordinator=coordinator,
            approval_manager=ApprovalManager(factory, event_bus),
            event_bus=event_bus,
            policy=policy,
            profile=profile,
            prompts_base_dir=REPO_ROOT,
            workspace_root=ws,
        )
        res = loop.run(
            session_id=SESSION_ID,
            request_text="Write hello.txt and read it back",
            executor=runtime,
        )
        assert res.status == "completed"
        assert res.calls_used == 2
        assert res.assistant_message == "Verified hello.txt via Docker MCP."
        assert (ws / "hello.txt").read_text(encoding="utf-8") == "from-runtime"

        # Coordinator blocks disallowed calls before runtime is ever invoked
        exec_count_before = len(client.exec_creates)
        blocked = coordinator.handle_call(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_block",
                tool="files.read",
                args={"path": "../secret.txt"},
            ),
            profile,
            executor=runtime,
        )
        assert blocked.executed is False
        assert blocked.tool_result.error is not None
        assert blocked.tool_result.error.code == ErrorCode.PATH_JAIL
        assert len(client.exec_creates) == exec_count_before
    finally:
        runtime.close()
        engine.dispose()


def test_container_path_jail_validates_container_symlinks_without_host_root(
    tmp_path: Path,
) -> None:
    """Preflight uses container `/workspace` symlinks even if host path differs."""
    container_ws = tmp_path / "container_workspace"
    container_ws.mkdir()
    (container_ws / "inside.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "host_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (container_ws / "escape_link").symlink_to(outside)

    from control_plane.gateway.path_jail import PathJail, PathJailError

    real_jail = PathJail(container_ws)

    class _ContainerJailDocker(_FakeDockerClient):
        def _exec_start(self, exec_id: str, **kwargs: Any) -> Any:
            rec = self._exec_records[exec_id]
            cmd = rec["cmd"]
            if cmd[:2] == ["/opt/overwatch/.venv/bin/python", "-c"]:
                self._exec_records[exec_id] = rec
                target = (rec.get("environment") or {})["OVERWATCH_TARGET_PATH"]
                try:
                    rel = real_jail.relative_path(target)
                except PathJailError as exc:
                    rec["_exit_code"] = 2
                    return (
                        json.dumps(
                            {"ok": False, "reason": exc.reason, "message": str(exc)}
                        ).encode("utf-8"),
                        b"",
                    )
                rec["_exit_code"] = 0
                return (json.dumps({"ok": True, "rel": rel}).encode("utf-8"), b"")
            return super()._exec_start(exec_id, **kwargs)

        def _exec_inspect(self, exec_id: str) -> dict[str, Any]:
            rec = self._exec_records[exec_id]
            if "_exit_code" in rec:
                return {"ExitCode": rec["_exit_code"], "Pid": 555}
            return super()._exec_inspect(exec_id)

    client = _ContainerJailDocker()
    profile = load_profile("general")
    policy = load_policy()
    # Non-existent host workspace root proves preflight doesn't read the host fs.
    pipeline = GatewayPreflightPipeline(
        policy,
        workspace_root=tmp_path / "nonexistent_host_workspace",
        path_jail=ContainerPathJail(
            runtime := SessionMcpRuntime(
                docker_client=client,
                session_id=SESSION_ID,
                profile=profile,
            )
        ),
    )
    try:
        allowed = pipeline.evaluate(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_ok",
                tool="files.read",
                args={"path": "./inside.txt"},
            ),
            profile,
        )
        assert allowed.decision == "allow"
        assert allowed.validated_args is not None
        assert allowed.validated_args.args == {"path": "inside.txt"}

        escaped = pipeline.evaluate(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_sym",
                tool="files.read",
                args={"path": "escape_link"},
            ),
            profile,
        )
        assert escaped.decision == "block"
        assert escaped.error is not None
        assert escaped.error.code == ErrorCode.PATH_JAIL

        # Lexical traversal rejects immediately without hitting Docker exec.
        execs_before = len(client.exec_creates)
        traversal = pipeline.evaluate(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_trav",
                tool="files.read",
                args={"path": "../etc/passwd"},
            ),
            profile,
        )
        assert traversal.decision == "block"
        assert traversal.error is not None
        assert traversal.error.code == ErrorCode.PATH_JAIL
        assert len(client.exec_creates) == execs_before
    finally:
        runtime.close()


def test_capture_screenshot_jpeg_lifecycle_and_validation() -> None:
    """capture_screenshot_jpeg ensures display, validates JPEG bytes, and cleans up."""
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\xff\xd9"
    client = _FakeDockerClient()
    # xset fails first (not running), then succeeds after Xvfb starts
    client.probe_exit_codes["xset"] = [1, 0]

    orig_exec_start = client._exec_start
    capture_state = {"mode": "ok"}

    def _custom_exec_start(exec_id: str, **kwargs: Any) -> Any:
        rec = client._exec_records[exec_id]
        if rec["cmd"][0] == "python3":
            client.exec_starts.append({"exec_id": exec_id, **kwargs})
            if capture_state["mode"] == "ok":
                return fake_jpeg
            if capture_state["mode"] == "bad_bytes":
                return b"not-a-jpeg"
            return b""
        return orig_exec_start(exec_id, **kwargs)

    client.api.exec_start.side_effect = _custom_exec_start
    profile = load_profile("coding")
    runtime = SessionMcpRuntime(
        docker_client=client,
        session_id=SESSION_ID,
        profile=profile,
    )
    try:
        frame = runtime.capture_screenshot_jpeg()
        assert frame == fake_jpeg
        cmds = [rec["cmd"][0] for rec in client.exec_creates]
        assert "Xvfb" in cmds
        assert "openbox" in cmds
        assert "python3" in cmds

        capture_state["mode"] = "bad_bytes"
        with pytest.raises(McpTransportError, match="invalid JPEG"):
            runtime.capture_screenshot_jpeg()

        client.probe_exit_codes["python3"] = [1]
        capture_state["mode"] = "ok"
        with pytest.raises(McpTransportError, match="Screenshot capture failed"):
            runtime.capture_screenshot_jpeg()
    finally:
        runtime.close()

    kill_cmds = [rec["cmd"] for rec in client.exec_creates if rec["cmd"][0] == "kill"]
    assert len(kill_cmds) == 2

    with pytest.raises(McpRuntimeClosedError):
        runtime.capture_screenshot_jpeg()

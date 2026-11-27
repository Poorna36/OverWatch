"""Unit tests for Phase 4 session creation route and SessionRunner."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import socket
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import anyio
import docker.errors
import pytest
from control_plane.agent.llm import (
    LlmAdapter,
    LlmMessage,
    LlmToolCall,
    LlmTurnResponse,
)
from control_plane.api.deps import (
    derive_owner_id,
    get_approval_manager,
    get_container_manager,
    get_event_bus,
    get_llm_factory,
    get_policy_loader,
    get_profile_loader,
    get_runtime_registry,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock
from control_plane.contracts import ErrorCode
from control_plane.db.models import (
    ApprovalModel,
    AuditEntryModel,
    EventModel,
    SessionModel,
)
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.mcp_runtime import MCP_SERVER_COMMANDS, encode_docker_frame
from control_plane.gateway.path_jail import PathJail, PathJailError
from control_plane.gateway.policy_loader import PolicyLoader
from control_plane.sessions.container_manager import SessionContainerManager
from control_plane.sessions.profile_loader import ProfileLoader
from control_plane.sessions.session_runner import (
    RuntimeRegistry,
    SessionRunner,
    SessionRunnerError,
)
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_TOKEN = "test-session-create-secret-token"
ULID_SESSION_RE = re.compile(r"^s_[0-9A-HJKMNP-TV-Z]{26}$")
SECRET_REQUEST_TEXT = "SECRET_REQ_TEXT_DO_NOT_PERSIST_9911"


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
        await anyio.to_thread.run_sync(self._sock.sendall, encode_docker_frame(1, line))

    async def aclose(self) -> None:
        pass

    async def __aenter__(self) -> _SocketBackedWriteStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


def _bridge_mcp_server_over_docker_socket(
    mcp_server: MCPServer,
    server_sock: socket.socket,
) -> threading.Thread:
    """Run a real ``MCPServer`` in-thread over a Docker-framed socketpair."""
    from mcp import types

    async def _serve() -> None:
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
                            send_coro = read_writer.send(SessionMessage(msg))
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    send_coro, loop
                                ).result(timeout=2.0)
                            except Exception:
                                send_coro.close()
                                raise
            except Exception:
                pass
            finally:
                close_coro = read_writer.aclose()
                try:
                    asyncio.run_coroutine_threadsafe(close_coro, loop)
                except Exception:
                    close_coro.close()

        async with read_stream:
            reader_thread = threading.Thread(target=_read_stdin, daemon=True)
            reader_thread.start()
            try:
                init_opts = mcp_server._lowlevel_server.create_initialization_options()
                await mcp_server._lowlevel_server.run(
                    read_stream, write_stream, init_opts
                )
            finally:
                try:
                    server_sock.close()
                except OSError:
                    pass
                reader_thread.join(timeout=2.0)

    thread = threading.Thread(target=lambda: anyio.run(_serve), daemon=True)
    thread.start()
    return thread


def _make_stub_terminal_server(called_commands: list[str]) -> MCPServer:
    srv = MCPServer(name="terminal")

    @srv.tool(name="terminal.run", structured_output=True)
    def terminal_run(
        command: str,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        del cwd, timeout_s
        called_commands.append(command)
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "truncated": False}

    return srv


def _make_stub_desktop_server() -> MCPServer:
    srv = MCPServer(name="desktop")

    @srv.tool(name="desktop.screenshot", structured_output=True)
    def desktop_screenshot(region: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "image_base64": "",
            "width": 1,
            "height": 1,
            "ts": "2026-11-27T00:00:00Z",
        }

    return srv


def _make_stub_browser_server() -> MCPServer:
    srv = MCPServer(name="browser")

    @srv.tool(name="browser.open", structured_output=True)
    def browser_open(url: str) -> dict[str, Any]:
        return {"url": url, "title": "Stub", "status_code": 200}

    return srv


class _FakeContainerObj:
    """In-memory Docker Container stub supporting start/stop/remove/reload/labels."""

    def __init__(
        self,
        *,
        container_id: str,
        name: str,
        labels: dict[str, str],
        network_name: str,
        ip_address: str = "172.30.0.10",
    ) -> None:
        self.id = container_id
        self.name = name
        self.labels = dict(labels)
        self.status = "created"
        self.network_name = network_name
        self.ip_address = ip_address
        self.started_threads: list[str] = []
        self.stopped = False
        self.removed = False
        self._networks_meta: dict[str, dict[str, str]] = {
            network_name: {"IPAddress": ip_address}
        }
        self._refresh_attrs()

    def _refresh_attrs(self) -> None:
        self.attrs = {
            "State": {
                "Status": self.status,
                "Running": self.status in ("running", "paused"),
                "Paused": self.status == "paused",
            },
            "Config": {"Labels": dict(self.labels)},
            "NetworkSettings": {"Networks": self._networks_meta},
        }

    def start(self) -> None:
        self.started_threads.append(threading.current_thread().name)
        self.status = "running"
        self._refresh_attrs()

    def pause(self) -> None:
        self.status = "paused"
        self._refresh_attrs()

    def unpause(self) -> None:
        self.status = "running"
        self._refresh_attrs()

    def reload(self) -> None:
        self._refresh_attrs()

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True
        self.status = "exited"
        self._refresh_attrs()

    def remove(self, force: bool = True) -> None:
        self.removed = True


class _FakeNetworkObj:
    def __init__(self, name: str) -> None:
        self.id = f"net_{name}"
        self.name = name
        self.attrs = {"Name": name, "Internal": True, "Options": {}, "Labels": {}}
        self.containers: list[Any] = []
        self.removed = False

    def connect(self, container: Any, *_args: Any, **kwargs: Any) -> None:
        ipv4 = kwargs.get("ipv4_address")
        if ipv4 and isinstance(getattr(container, "attrs", None), dict):
            ns = container.attrs.setdefault("NetworkSettings", {})
            nets = ns.setdefault("Networks", {})
            nets[self.name] = {"IPAddress": ipv4}

    def disconnect(self, container: Any, *_args: Any, **_kwargs: Any) -> None:
        if isinstance(getattr(container, "attrs", None), dict):
            nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            nets.pop(self.name, None)

    def reload(self) -> None:
        pass

    def remove(self) -> None:
        self.removed = True


class _FakeVolumeObj:
    def __init__(self, name: str) -> None:
        self.id = name
        self.name = name
        self.attrs = {"Name": name, "Labels": {}}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeDockerEnv:
    """Full in-memory Docker SDK client for container lifecycle + MCP exec."""

    def __init__(
        self,
        container_ws: Path,
        *,
        fail_container_create: bool = False,
        fail_mcp_server: str | None = None,
        on_container_create: Callable[[], None] | None = None,
        on_mcp_exec: Callable[[str], None] | None = None,
    ) -> None:
        self.container_ws = container_ws
        self.real_jail = PathJail(container_ws)
        self.fail_container_create = fail_container_create
        self.fail_mcp_server = fail_mcp_server
        self.on_container_create = on_container_create
        self.on_mcp_exec = on_mcp_exec

        self.terminal_commands: list[str] = []
        files_mod = _load_module("mcp_files_srv_runner_test", "mcp/files/server.py")
        self.server_builders: dict[str, Callable[[], MCPServer]] = {
            "files": lambda: files_mod.create_files_server(
                files_mod.FilesServerConfig(workspace_root=self.container_ws)
            ),
            "terminal": lambda: _make_stub_terminal_server(self.terminal_commands),
            "desktop": _make_stub_desktop_server,
            "browser": _make_stub_browser_server,
        }

        self._containers: dict[str, _FakeContainerObj] = {}
        proxy = _FakeContainerObj(
            container_id="cid_proxy",
            name="overwatch-egress-proxy",
            labels={},
            network_name="bridge",
            ip_address="172.18.0.2",
        )
        proxy.status = "running"
        proxy._refresh_attrs()
        self._containers["overwatch-egress-proxy"] = proxy
        self._containers[proxy.id] = proxy

        self._networks: dict[str, _FakeNetworkObj] = {}
        self._volumes: dict[str, _FakeVolumeObj] = {}
        self._exec_seq = 0
        self._exec_records: dict[str, dict[str, Any]] = {}
        self.exec_creates: list[dict[str, Any]] = []
        self.teardown_calls = 0

        self.containers = MagicMock()
        self.containers.create.side_effect = self._create_container
        self.containers.get.side_effect = self._get_container
        self.containers.list.side_effect = lambda **kw: list(self._containers.values())

        self.networks = MagicMock()
        self.networks.get.side_effect = self._get_network
        self.networks.create.side_effect = self._create_network
        self.networks.list.side_effect = lambda **kw: []

        self.volumes = MagicMock()
        self.volumes.get.side_effect = self._get_volume
        self.volumes.create.side_effect = self._create_volume

        self.images = MagicMock()
        self.images.get.side_effect = lambda name: MagicMock(id=f"img_{name}")

        self.api = MagicMock()
        self.api._version = "1.45"
        self.api.exec_create.side_effect = self._exec_create
        self.api.exec_start.side_effect = self._exec_start
        self.api.exec_inspect.side_effect = self._exec_inspect

    def _create_container(self, **kwargs: Any) -> _FakeContainerObj:
        if self.on_container_create is not None:
            self.on_container_create()
        if self.fail_container_create:
            raise docker.errors.APIError("Docker daemon failed creating container")
        name = kwargs["name"]
        cid = f"cid_{name}"
        net_name = kwargs.get("network", "bridge")
        endpoint_cfg = (kwargs.get("networking_config") or {}).get(net_name, {})
        ipv4 = (endpoint_cfg.get("IPAMConfig") or {}).get("IPv4Address", "172.30.0.3")
        obj = _FakeContainerObj(
            container_id=cid,
            name=name,
            labels=kwargs.get("labels", {}),
            network_name=net_name,
            ip_address=ipv4,
        )
        self._containers[cid] = obj
        self._containers[name] = obj
        return obj

    def _get_container(self, ref: str) -> _FakeContainerObj:
        if ref not in self._containers:
            raise docker.errors.NotFound(f"Container {ref} not found")
        return self._containers[ref]

    def _get_network(self, name: str) -> _FakeNetworkObj:
        if name not in self._networks:
            raise docker.errors.NotFound(f"Network {name} not found")
        return self._networks[name]

    def _create_network(self, name: str, **_kwargs: Any) -> _FakeNetworkObj:
        net = _FakeNetworkObj(name)
        self._networks[name] = net
        return net

    def _get_volume(self, name: str) -> _FakeVolumeObj:
        if name not in self._volumes:
            raise docker.errors.NotFound(f"Volume {name} not found")
        return self._volumes[name]

    def _create_volume(self, name: str, **_kwargs: Any) -> _FakeVolumeObj:
        vol = _FakeVolumeObj(name)
        self._volumes[name] = vol
        return vol

    def _exec_create(
        self, container: str, cmd: list[str], **kwargs: Any
    ) -> dict[str, str]:
        self._exec_seq += 1
        exec_id = f"exec_{self._exec_seq}"
        rec = {"container": container, "cmd": list(cmd), **kwargs, "exec_id": exec_id}
        self._exec_records[exec_id] = rec
        self.exec_creates.append(rec)
        return {"Id": exec_id}

    def _exec_start(self, exec_id: str, **kwargs: Any) -> Any:
        rec = self._exec_records[exec_id]
        cmd = rec["cmd"]
        if cmd[:2] == ["/opt/overwatch/.venv/bin/python", "-c"]:
            target = (rec.get("environment") or {})["OVERWATCH_TARGET_PATH"]
            try:
                rel = self.real_jail.relative_path(target)
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

        if kwargs.get("socket"):
            for server_name, expected_cmd in MCP_SERVER_COMMANDS.items():
                if tuple(cmd) == expected_cmd:
                    if self.on_mcp_exec is not None:
                        self.on_mcp_exec(server_name)
                    if self.fail_mcp_server == server_name:
                        raise docker.errors.APIError(
                            f"MCP exec failed starting {server_name}"
                        )
                    c_sock, s_sock = socket.socketpair()
                    srv = self.server_builders[server_name]()
                    _bridge_mcp_server_over_docker_socket(srv, s_sock)
                    return c_sock
            raise AssertionError(f"Unexpected stdio command: {cmd}")
        return b""

    def _exec_inspect(self, exec_id: str) -> dict[str, Any]:
        rec = self._exec_records[exec_id]
        if "_exit_code" in rec:
            return {"ExitCode": rec["_exit_code"], "Pid": 500}
        return {"ExitCode": 0, "Pid": 500}


class _ScriptedLlm(LlmAdapter):
    """Deterministic scripted LLM that optionally blocks on a threading.Event."""

    def __init__(
        self,
        handler: Callable[[Sequence[LlmMessage], Any], LlmTurnResponse],
    ) -> None:
        self._handler = handler
        self.observed_threads: list[threading.Thread] = []

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Any,
    ) -> LlmTurnResponse:
        del system_prompt
        self.observed_threads.append(threading.current_thread())
        return self._handler(messages, tools)


class _Clock:
    def __init__(self) -> None:
        self._current = datetime(2026, 11, 27, 4, 0, 0, tzinfo=UTC)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            val = self._current
            self._current = self._current + timedelta(seconds=1)
            return val


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated DB, EventBus, ContainerManager, RuntimeRegistry, and TestClient."""
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    ws = tmp_path / "container_ws"
    ws.mkdir()

    db_path = tmp_path / "runner.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _Clock()
    approval_mgr = ApprovalManager(factory, bus, clock=clock)
    docker_env = _FakeDockerEnv(ws)
    container_mgr = SessionContainerManager(docker_client=docker_env)
    registry = RuntimeRegistry(max_workers=4)

    current_llm_factory: dict[str, Callable[[], LlmAdapter]] = {
        "fn": lambda: _ScriptedLlm(
            lambda _msgs, _tools: LlmTurnResponse(assistant_text="Done.")
        )
    }

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    app.dependency_overrides[get_container_manager] = lambda: container_mgr
    app.dependency_overrides[get_approval_manager] = lambda: approval_mgr
    app.dependency_overrides[get_profile_loader] = lambda: ProfileLoader(
        REPO_ROOT / "profiles"
    )
    app.dependency_overrides[get_policy_loader] = lambda: PolicyLoader(
        REPO_ROOT / "policy.yaml"
    )
    app.dependency_overrides[get_runtime_registry] = lambda: registry
    app.dependency_overrides[get_llm_factory] = lambda: current_llm_factory["fn"]
    app.dependency_overrides[get_stream_clock] = lambda: clock

    try:
        client = TestClient(app)
        yield {
            "client": client,
            "factory": factory,
            "bus": bus,
            "container_mgr": container_mgr,
            "docker_env": docker_env,
            "registry": registry,
            "workspace": ws,
            "set_llm_factory": lambda fn: current_llm_factory.__setitem__("fn", fn),
            "headers": {"Authorization": f"Bearer {APP_TOKEN}"},
            "owner_id": derive_owner_id(APP_TOKEN),
        }
    finally:
        # Shut down active test runtimes & executor before disposing DB engine.
        with registry.lock:
            pending_entries = list(registry.pending_approvals.values())
            registry.pending_approvals.clear()
        for entry in pending_entries:
            if entry.timer is not None:
                entry.timer.cancel()
        for sid in list(registry.runtimes.keys()):
            rt = registry.runtimes.pop(sid, None)
            if rt is not None:
                rt.close()
        registry.is_shutdown = True
        registry.executor.shutdown(wait=True)
        app.dependency_overrides.clear()
        engine.dispose()


def _wait_future(
    registry: RuntimeRegistry, session_id: str, timeout: float = 5.0
) -> Any:
    with registry.lock:
        fut = registry.futures.get(session_id)
    assert fut is not None, f"No background future recorded for {session_id}"
    return fut.result(timeout=timeout)


def test_post_sessions_validation_and_auth(api_env: dict[str, Any]) -> None:
    """POST /api/v1/sessions enforces bearer auth, request_text, dry_run, profile."""
    client: TestClient = api_env["client"]
    headers: dict[str, str] = api_env["headers"]

    unauth = client.post("/api/v1/sessions", json={"request_text": "hello"})
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == ErrorCode.AUTH_ERROR

    empty = client.post(
        "/api/v1/sessions", headers=headers, json={"request_text": "   "}
    )
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == ErrorCode.ARG_INVALID

    dry = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"request_text": "do work", "dry_run": True},
    )
    assert dry.status_code == 400
    assert dry.json()["error"]["code"] == ErrorCode.POLICY_BLOCK

    bad_profile = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"request_text": "do work", "profile": "unknown-prof"},
    )
    assert bad_profile.status_code == 400
    assert bad_profile.json()["error"]["code"] == ErrorCode.ARG_INVALID


def test_post_sessions_default_profile_execution_and_no_request_text_leak(
    api_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default 'general' profile runs MCP tools without persisting/logging request."""
    caplog.set_level(logging.DEBUG)
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    factory = api_env["factory"]
    ws: Path = api_env["workspace"]
    main_thread = threading.current_thread()

    turns: list[int] = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turns[0] += 1
        if turns[0] == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_write",
                        tool="files.write",
                        args={"path": "out.txt", "content": "written-by-runner"},
                    ),
                )
            )
        if turns[0] == 2:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_read",
                        tool="files.read",
                        args={"path": "out.txt"},
                    ),
                )
            )
        assert any(
            m.role == "tool" and "written-by-runner" in m.content for m in messages
        )
        return LlmTurnResponse(assistant_text="All verified.")

    llm = _ScriptedLlm(_handler)
    api_env["set_llm_factory"](lambda: llm)

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": SECRET_REQUEST_TEXT},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert ULID_SESSION_RE.fullmatch(body["session_id"])
    assert body == {"session_id": body["session_id"], "state": "ACTIVE"}

    session_id = body["session_id"]
    record = _wait_future(registry, session_id)
    assert record.status == "completed"
    assert (ws / "out.txt").read_text(encoding="utf-8") == "written-by-runner"

    # LLM turns ran off the FastAPI main thread.
    assert llm.observed_threads
    assert all(t is not main_thread for t in llm.observed_threads)

    with factory() as db:
        row = db.get(SessionModel, session_id)
        assert row is not None
        assert row.owner_id == api_env["owner_id"]
        assert row.profile == "general"
        assert row.state == "ACTIVE"
        assert row.container_id is not None
        assert row.container_ip is not None
        assert row.container_ip.endswith(".3")

        events = db.scalars(
            select(EventModel)
            .where(EventModel.session_id == session_id)
            .order_by(EventModel.seq.asc())
        ).all()
        states = [
            json.loads(ev.data)["state"] for ev in events if ev.type == "session_state"
        ]
        assert states[:3] == ["CREATED", "STARTING", "ACTIVE"]
        assert any(ev.type == "agent_message" for ev in events)

        audits = db.scalars(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == session_id)
            .order_by(AuditEntryModel.seq.asc())
        ).all()
        assert [(a.tool, a.decision) for a in audits] == [
            ("files.write", "allowed"),
            ("files.read", "allowed"),
        ]

        # request_text must never be persisted anywhere in SQLite tables or logs.
        all_rows = (
            [f"{row.profile}:{row.state}:{row.container_id}:{row.container_ip}"]
            + [f"{ev.type}:{ev.data}" for ev in events]
            + [f"{a.tool}:{a.args_redacted}" for a in audits]
        )
        for text_blob in all_rows:
            assert SECRET_REQUEST_TEXT not in text_blob

    assert SECRET_REQUEST_TEXT not in caplog.text

    # Ending session closes runtime and tears down container.
    end_resp = client.post(
        f"/api/v1/sessions/{session_id}/end", headers=api_env["headers"]
    )
    assert end_resp.status_code == 200
    assert end_resp.json() == {"state": "ENDED"}
    assert session_id not in registry.runtimes


def test_session_runner_blocks_container_symlink_escape_via_path_jail(
    api_env: dict[str, Any],
) -> None:
    """In-container symlink escapes are blocked in preflight with PATH_JAIL."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    factory = api_env["factory"]
    ws: Path = api_env["workspace"]

    outside_secret = ws.parent / "host_only_secret.txt"
    outside_secret.write_text("do-not-read", encoding="utf-8")
    (ws / "bad_link").symlink_to(outside_secret)

    api_env["set_llm_factory"](
        lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_sym",
                        tool="files.read",
                        args={"path": "bad_link"},
                    ),
                )
            )
        )
    )

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Read bad_link"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    record = _wait_future(registry, session_id)
    assert record.status == "blocked"
    assert record.error_code == ErrorCode.PATH_JAIL

    with factory() as db:
        audits = db.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == session_id)
        ).all()
        assert [(a.tool, a.decision) for a in audits] == [("files.read", "blocked")]


def test_post_sessions_coding_profile_and_startup_ordering(
    api_env: dict[str, Any],
) -> None:
    """Explicit 'coding' profile marks ACTIVE only after container and MCP start."""
    client: TestClient = api_env["client"]
    factory = api_env["factory"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    registry: RuntimeRegistry = api_env["registry"]

    observed_active_at_create: list[bool] = []
    observed_servers_started: list[str] = []

    def _snapshot_db_active() -> bool:
        with factory() as db:
            rows = db.scalars(select(SessionModel)).all()
            return any(r.state == "ACTIVE" for r in rows)

    docker_env.on_container_create = lambda: observed_active_at_create.append(
        _snapshot_db_active()
    )

    def _on_mcp(server_name: str) -> None:
        observed_active_at_create.append(_snapshot_db_active())
        observed_servers_started.append(server_name)

    docker_env.on_mcp_exec = _on_mcp

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Say hi", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    _wait_future(registry, session_id)

    assert observed_servers_started == ["terminal", "files", "desktop"]
    assert observed_active_at_create == [False, False, False, False]

    with factory() as db:
        row = db.get(SessionModel, session_id)
        assert row is not None
        assert row.profile == "coding"
        assert row.state == "ACTIVE"


@pytest.mark.parametrize(
    ("fail_mode",),
    [("container",), ("mcp",)],
)
def test_startup_failure_cleans_up_marks_failed_and_hides_request_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_mode: str,
) -> None:
    """Container/MCP failure cleans up, persists FAILED, and hides request_text."""
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    ws = tmp_path / "fail_ws"
    ws.mkdir()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'fail.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _Clock()
    approval_mgr = ApprovalManager(factory, bus, clock=clock)
    docker_env = _FakeDockerEnv(
        ws,
        fail_container_create=(fail_mode == "container"),
        fail_mcp_server=("files" if fail_mode == "mcp" else None),
    )
    container_mgr = SessionContainerManager(docker_client=docker_env)
    teardown_spy = MagicMock(wraps=container_mgr.teardown_session)
    monkeypatch.setattr(container_mgr, "teardown_session", teardown_spy)

    registry = RuntimeRegistry(max_workers=2)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_event_bus] = lambda: bus
    app.dependency_overrides[get_container_manager] = lambda: container_mgr
    app.dependency_overrides[get_approval_manager] = lambda: approval_mgr
    app.dependency_overrides[get_profile_loader] = lambda: ProfileLoader(
        REPO_ROOT / "profiles"
    )
    app.dependency_overrides[get_policy_loader] = lambda: PolicyLoader(
        REPO_ROOT / "policy.yaml"
    )
    app.dependency_overrides[get_runtime_registry] = lambda: registry
    app.dependency_overrides[get_llm_factory] = lambda: (
        lambda: _ScriptedLlm(lambda _m, _t: LlmTurnResponse(assistant_text="ok"))
    )
    app.dependency_overrides[get_stream_clock] = lambda: clock

    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {APP_TOKEN}"},
            json={"request_text": SECRET_REQUEST_TEXT, "profile": "coding"},
        )
        assert resp.status_code == 500
        err_body = resp.json()
        assert err_body["error"]["code"] == ErrorCode.UPSTREAM_ERROR
        assert SECRET_REQUEST_TEXT not in json.dumps(err_body)
        assert teardown_spy.call_count == 1
        assert registry.runtimes == {}

        with factory() as db:
            rows = db.scalars(select(SessionModel)).all()
            assert len(rows) == 1
            assert rows[0].state == "FAILED"
            events = db.scalars(select(EventModel).order_by(EventModel.seq.asc())).all()
            states = [
                json.loads(ev.data)["state"]
                for ev in events
                if ev.type == "session_state"
            ]
            assert states == ["CREATED", "STARTING", "FAILED"]
            for ev in events:
                assert SECRET_REQUEST_TEXT not in ev.data
    finally:
        registry.is_shutdown = True
        registry.executor.shutdown(wait=True)
        app.dependency_overrides.clear()
        engine.dispose()


def test_awaiting_approval_keeps_session_active_and_runtime_open(
    api_env: dict[str, Any],
) -> None:
    """Approval pause keeps session ACTIVE and runtime open without executing."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    factory = api_env["factory"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]

    api_env["set_llm_factory"](
        lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_term_ask",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        )
    )

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Run echo", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    record = _wait_future(registry, session_id)
    assert record.status == "awaiting_approval"
    assert record.pending_approval_id is not None

    # Ask call must NOT have been executed automatically, and runtime stays open.
    assert docker_env.terminal_commands == []
    assert registry.runtimes.get(session_id) is not None

    with factory() as db:
        row = db.get(SessionModel, session_id)
        assert row is not None
        assert row.state == "ACTIVE"
        approval = db.get(ApprovalModel, record.pending_approval_id)
        assert approval is not None
        assert approval.status == "pending"
        assert approval.tool == "terminal.run"

        audits = db.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == session_id)
        ).all()
        assert [(a.tool, a.decision) for a in audits] == [("terminal.run", "asked")]


def test_post_sessions_does_not_block_event_loop_on_slow_startup_or_llm(
    api_env: dict[str, Any],
) -> None:
    """start_session runs in worker thread and AgentLoop runs on runner executor."""
    client: TestClient = api_env["client"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    registry: RuntimeRegistry = api_env["registry"]
    main_thread = threading.current_thread()

    create_threads: list[threading.Thread] = []
    docker_env.on_container_create = lambda: create_threads.append(
        threading.current_thread()
    )

    llm_entered = threading.Event()
    release_llm = threading.Event()

    def _slow_handler(
        _msgs: list[LlmMessage], _tools: list[dict[str, Any]]
    ) -> LlmTurnResponse:
        llm_entered.set()
        release_llm.wait(timeout=3.0)
        return LlmTurnResponse(assistant_text="Completed after release")

    llm = _ScriptedLlm(_slow_handler)
    api_env["set_llm_factory"](lambda: llm)

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Slow turn test"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    # Route returned before slow LLM turn finished.
    assert llm_entered.wait(timeout=3.0)
    assert create_threads and create_threads[0] is not main_thread
    assert llm.observed_threads and llm.observed_threads[0] is not main_thread

    # Subsequent GET /sessions succeeds promptly while AgentLoop is still blocked.
    list_resp = client.get("/api/v1/sessions", headers=api_env["headers"])
    assert list_resp.status_code == 200
    assert any(s["session_id"] == session_id for s in list_resp.json()["sessions"])

    release_llm.set()
    rec = _wait_future(registry, session_id)
    assert rec.status == "completed"


def test_approval_continuation_executes_once_and_rejects_duplicate_and_mismatch(
    api_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approving resumes the waiting run, executes once, and ignores duplicates."""
    caplog.set_level(logging.DEBUG)
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    factory = api_env["factory"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]

    turn_num = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turn_num[0] += 1
        if turn_num[0] == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_term_ok",
                        tool="terminal.run",
                        args={"command": "curl https://pypi.org/simple/"},
                    ),
                )
            )
        assert any(
            m.role == "tool" and m.tool_call_id == "c_term_ok" and "ok" in m.content
            for m in messages
        )
        return LlmTurnResponse(assistant_text="Approved command completed.")

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": SECRET_REQUEST_TEXT, "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    paused = _wait_future(registry, session_id)
    assert paused.status == "awaiting_approval"
    assert paused.pending_approval_id is not None
    aid = paused.pending_approval_id
    assert docker_env.terminal_commands == []

    # Mismatched session_id on SessionRunner fails closed without consuming entry
    runner = SessionRunner(
        session_factory=factory,
        container_manager=api_env["container_mgr"],
        event_bus=api_env["bus"],
        approval_manager=ApprovalManager(factory, api_env["bus"]),
        profile_loader=ProfileLoader(REPO_ROOT / "profiles"),
        policy_loader=PolicyLoader(REPO_ROOT / "policy.yaml"),
        registry=registry,
    )
    from control_plane.audit.audit_writer import AuditWriter

    dummy_audit = AuditWriter(factory).append(
        session_id=session_id,
        tool="terminal.run",
        args={"command": "curl https://pypi.org/simple/"},
        decision="asked_approved",
        reason="dummy",
        tier="sensitive",
    )
    from control_plane.gateway.approval_manager import ApprovalResolutionResult

    with pytest.raises(SessionRunnerError):
        runner.on_approval_resolved(
            ApprovalResolutionResult(
                approval_id=aid,
                session_id="s_01ABCDEFGHJKMNPTVW00000000",
                status="approved",
                approved=True,
                resolved_by="user",
                resolved_at="2026-11-27T04:00:05Z",
                timed_out=False,
                audit_entry=dummy_audit,
            )
        )

    # Approve via REST route -> resumes session & executes exactly once
    r_approve = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": True},
    )
    assert r_approve.status_code == 200
    assert r_approve.json() == {"ok": True}

    final_rec = _wait_future(registry, session_id)
    assert final_rec.status == "completed"
    assert docker_env.terminal_commands == ["curl https://pypi.org/simple/"]

    # Duplicate resolution returns 409 and never re-executes
    r_dup = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": True},
    )
    assert r_dup.status_code == 409
    assert r_dup.json()["error"]["code"] == ErrorCode.POLICY_BLOCK
    assert docker_env.terminal_commands == ["curl https://pypi.org/simple/"]
    assert SECRET_REQUEST_TEXT not in caplog.text


def test_denial_resumes_agent_without_executing_tool(api_env: dict[str, Any]) -> None:
    """Denying an approval emits USER_DENIED, resumes LLM, and skips execution."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    factory = api_env["factory"]

    turn_num = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turn_num[0] += 1
        if turn_num[0] == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_term_deny",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        assert any(
            m.role == "tool" and ErrorCode.USER_DENIED.value in m.content
            for m in messages
        )
        return LlmTurnResponse(assistant_text="Understood, did not run curl.")

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Fetch page", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    paused = _wait_future(registry, session_id)
    assert paused.status == "awaiting_approval"
    aid = paused.pending_approval_id
    assert aid is not None

    r_deny = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": False},
    )
    assert r_deny.status_code == 200

    final_rec = _wait_future(registry, session_id)
    assert final_rec.status == "completed"
    assert docker_env.terminal_commands == []

    with factory() as db:
        events = db.scalars(
            select(EventModel)
            .where(EventModel.session_id == session_id)
            .order_by(EventModel.seq.asc())
        ).all()
        tool_results = [json.loads(e.data) for e in events if e.type == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["ok"] is False
        assert tool_results[0]["error"]["code"] == ErrorCode.USER_DENIED.value


def test_timeout_without_polling_wakes_waiting_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deadline timer expires pending approval and wakes run without client polling."""
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    ws = tmp_path / "timeout_ws"
    ws.mkdir()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'timeout.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    real_clock = lambda: datetime.now(UTC)  # noqa: E731
    approval_mgr = ApprovalManager(factory, bus, clock=real_clock)
    docker_env = _FakeDockerEnv(ws)
    container_mgr = SessionContainerManager(docker_client=docker_env)
    registry = RuntimeRegistry(max_workers=4)

    base_policy = PolicyLoader(REPO_ROOT / "policy.yaml").load()
    short_policy = base_policy.model_copy(
        update={
            "defaults": base_policy.defaults.model_copy(
                update={"approval_timeout_s": 1}
            )
        }
    )

    class _ShortPolicyLoader:
        def load(self):
            return short_policy

    turn_num = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turn_num[0] += 1
        if turn_num[0] == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_timeout",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        assert any(
            m.role == "tool" and ErrorCode.APPROVAL_TIMEOUT.value in m.content
            for m in messages
        )
        return LlmTurnResponse(assistant_text="Timed out safely.")

    runner = SessionRunner(
        session_factory=factory,
        container_manager=container_mgr,
        event_bus=bus,
        approval_manager=approval_mgr,
        profile_loader=ProfileLoader(REPO_ROOT / "profiles"),
        policy_loader=_ShortPolicyLoader(),  # type: ignore[arg-type]
        llm_factory=lambda: _ScriptedLlm(_handler),
        clock=real_clock,
        registry=registry,
    )

    try:
        started = runner.start_session(
            owner_id=derive_owner_id(APP_TOKEN),
            request_text="Run curl",
            profile_name="coding",
        )
        runner.dispatch_agent_run(started=started, request_text="Run curl")
        paused = runner.wait_for_session(started.session_id, timeout=5.0)
        assert paused is not None
        assert paused.status == "awaiting_approval"
        aid = paused.pending_approval_id
        assert aid is not None

        # Wait without calling GET /approvals or POST /approvals
        deadline = datetime.now(UTC) + timedelta(seconds=5)
        while datetime.now(UTC) < deadline:
            rec = runner.get_run_record(started.session_id)
            if rec is not None and rec.status == "completed":
                break
            threading.Event().wait(0.05)

        final_rec = runner.wait_for_session(started.session_id, timeout=5.0)
        assert final_rec is not None
        assert final_rec.status == "completed"
        assert docker_env.terminal_commands == []

        with factory() as db:
            row = db.get(ApprovalModel, aid)
            assert row is not None and row.status == "timed_out"
            audits = db.scalars(
                select(AuditEntryModel)
                .where(AuditEntryModel.session_id == started.session_id)
                .order_by(AuditEntryModel.seq.asc())
            ).all()
            assert [(a.tool, a.decision) for a in audits] == [
                ("terminal.run", "asked"),
                ("terminal.run", "asked_denied"),
            ]
    finally:
        runner.shutdown(wait=True)
        engine.dispose()


def test_session_end_while_waiting_cancels_timer_and_prevents_execution(
    api_env: dict[str, Any],
) -> None:
    """Ending a session while awaiting approval cancels timer and blocks resume."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]

    api_env["set_llm_factory"](
        lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_end_wait",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        )
    )

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Wait then end", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    paused = _wait_future(registry, session_id)
    assert paused.status == "awaiting_approval"
    aid = paused.pending_approval_id
    assert aid is not None

    with registry.lock:
        entry = registry.pending_approvals.get(aid)
        assert entry is not None
        timer = entry.timer
        assert timer is not None

    end_resp = client.post(
        f"/api/v1/sessions/{session_id}/end", headers=api_env["headers"]
    )
    assert end_resp.status_code == 200
    assert end_resp.json() == {"state": "ENDED"}

    # Pending continuation and timer were cleaned up
    with registry.lock:
        assert aid not in registry.pending_approvals
    assert timer.finished.is_set()

    # Late approval after end returns 409 SESSION_INACTIVE and never executes
    late_resp = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": True},
    )
    assert late_resp.status_code == 409
    assert late_resp.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE
    assert docker_env.terminal_commands == []


def test_missing_in_memory_continuation_after_restart_fails_closed(
    api_env: dict[str, Any],
) -> None:
    """After process restart (empty registry), approval fails closed without exec."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    factory = api_env["factory"]

    api_env["set_llm_factory"](
        lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_restart",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        )
    )

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": SECRET_REQUEST_TEXT, "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    paused = _wait_future(registry, session_id)
    aid = paused.pending_approval_id
    assert aid is not None

    # Simulate control-plane restart with fresh empty RuntimeRegistry
    restarted_registry = RuntimeRegistry(max_workers=2)
    app.dependency_overrides[get_runtime_registry] = lambda: restarted_registry

    try:
        r_approve = client.post(
            f"/api/v1/sessions/{session_id}/approvals/{aid}",
            headers=api_env["headers"],
            json={"approval_id": aid, "approve": True},
        )
        assert r_approve.status_code == 200
        assert docker_env.terminal_commands == []
        rec = restarted_registry.run_records.get(session_id)
        assert rec is not None
        assert rec.status == "error"
        assert rec.error_code == ErrorCode.FATAL.value

        with factory() as db:
            row = db.get(ApprovalModel, aid)
            assert row is not None and row.status == "approved"
    finally:
        restarted_registry.is_shutdown = True
        restarted_registry.executor.shutdown(wait=True)
        app.dependency_overrides[get_runtime_registry] = lambda: registry


def test_post_approval_preflight_rejection_on_paused_session(
    api_env: dict[str, Any],
) -> None:
    """Approving while session is PAUSED blocks via preflight without execution."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    factory = api_env["factory"]

    api_env["set_llm_factory"](
        lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_paused_ok",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        )
    )

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Pause then approve", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    paused = _wait_future(registry, session_id)
    aid = paused.pending_approval_id
    assert aid is not None

    # Pause session via control route
    r_pause = client.post(
        f"/api/v1/sessions/{session_id}/pause", headers=api_env["headers"]
    )
    assert r_pause.status_code == 200

    r_approve = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": True},
    )
    assert r_approve.status_code == 200

    rec = _wait_future(registry, session_id)
    assert rec.status == "blocked"
    assert rec.error_code == ErrorCode.SESSION_INACTIVE.value
    assert docker_env.terminal_commands == []

    with factory() as db:
        audits = db.scalars(
            select(AuditEntryModel)
            .where(AuditEntryModel.session_id == session_id)
            .order_by(AuditEntryModel.seq.asc())
        ).all()
        assert [(a.tool, a.decision) for a in audits] == [
            ("terminal.run", "asked"),
            ("terminal.run", "asked_approved"),
            ("terminal.run", "blocked"),
        ]


def test_session_runner_shutdown_cancels_pending_approval_timers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SessionRunner.shutdown() cancels waiting approval timers without hanging."""
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    ws = tmp_path / "shutdown_ws"
    ws.mkdir()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'shutdown.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    bus = EventBus(factory)
    clock = _Clock()
    approval_mgr = ApprovalManager(factory, bus, clock=clock)
    docker_env = _FakeDockerEnv(ws)
    container_mgr = SessionContainerManager(docker_client=docker_env)
    registry = RuntimeRegistry(max_workers=2)

    runner = SessionRunner(
        session_factory=factory,
        container_manager=container_mgr,
        event_bus=bus,
        approval_manager=approval_mgr,
        profile_loader=ProfileLoader(REPO_ROOT / "profiles"),
        policy_loader=PolicyLoader(REPO_ROOT / "policy.yaml"),
        llm_factory=lambda: _ScriptedLlm(
            lambda _m, _t: LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_wait_sd",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        ),
        clock=clock,
        registry=registry,
    )

    try:
        started = runner.start_session(
            owner_id=derive_owner_id(APP_TOKEN),
            request_text="Wait for shutdown",
            profile_name="coding",
        )
        runner.dispatch_agent_run(started=started, request_text="Wait for shutdown")
        paused = runner.wait_for_session(started.session_id, timeout=5.0)
        assert paused is not None
        assert paused.status == "awaiting_approval"
        aid = paused.pending_approval_id
        assert aid is not None
        with registry.lock:
            timer = registry.pending_approvals[aid].timer
            assert timer is not None

        runner.shutdown(wait=True)
        assert timer.finished.is_set()
        assert registry.pending_approvals == {}
        assert registry.runtimes == {}
        assert docker_env.terminal_commands == []
    finally:
        if not registry.is_shutdown:
            runner.shutdown(wait=True)
        engine.dispose()


def test_post_message_delivers_followup_without_leaking_text_or_creating_runtime(
    api_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """POST /message delivers follow-up to the same runtime and never persists text."""
    caplog.set_level(logging.DEBUG)
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]
    docker_env: _FakeDockerEnv = api_env["docker_env"]
    factory = api_env["factory"]
    ws: Path = api_env["workspace"]

    followup_secret = "SECRET_FOLLOWUP_TEXT_DO_NOT_PERSIST_5522"
    turn_num = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turn_num[0] += 1
        if turn_num[0] == 1:
            return LlmTurnResponse(assistant_text="Ready for follow-up.")
        if turn_num[0] == 2:
            assert any(
                m.role == "user" and m.content == followup_secret for m in messages
            )
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_followup_write",
                        tool="files.write",
                        args={"path": "followup.txt", "content": "ok-followup"},
                    ),
                )
            )
        return LlmTurnResponse(assistant_text="Follow-up complete.")

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": SECRET_REQUEST_TEXT},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    rec1 = _wait_future(registry, session_id)
    assert rec1.status == "completed"
    original_runtime = registry.runtimes[session_id]
    create_calls_before = docker_env.containers.create.call_count

    msg_resp = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": followup_secret},
    )
    assert msg_resp.status_code == 200
    assert msg_resp.json() == {"ok": True}

    rec2 = _wait_future(registry, session_id)
    assert rec2.status == "completed"
    assert (ws / "followup.txt").read_text(encoding="utf-8") == "ok-followup"
    assert registry.runtimes[session_id] is original_runtime
    assert docker_env.containers.create.call_count == create_calls_before

    with factory() as db:
        events = db.scalars(
            select(EventModel).where(EventModel.session_id == session_id)
        ).all()
        audits = db.scalars(
            select(AuditEntryModel).where(AuditEntryModel.session_id == session_id)
        ).all()
        approvals = db.scalars(
            select(ApprovalModel).where(ApprovalModel.session_id == session_id)
        ).all()
        row = db.get(SessionModel, session_id)
        assert row is not None
        for ev in events:
            assert followup_secret not in ev.data
            assert SECRET_REQUEST_TEXT not in ev.data
        for audit in audits:
            assert followup_secret not in audit.args_redacted
            assert SECRET_REQUEST_TEXT not in audit.args_redacted
        assert approvals == []
        assert followup_secret not in (row.receipt_url or "")

    assert followup_secret not in caplog.text
    assert SECRET_REQUEST_TEXT not in caplog.text


def test_post_message_queues_during_running_turn_without_concurrent_turns(
    api_env: dict[str, Any],
) -> None:
    """Message sent while turn is running queues safely and runs after turn 1."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]

    turn1_entered = threading.Event()
    release_turn1 = threading.Event()
    active_turns = 0
    max_concurrent = 0
    turn_lock = threading.Lock()
    queued_secret = "QUEUED_DURING_TURN_1_SECRET_8899"

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        nonlocal active_turns, max_concurrent
        with turn_lock:
            active_turns += 1
            max_concurrent = max(max_concurrent, active_turns)
        try:
            if not turn1_entered.is_set():
                turn1_entered.set()
                assert release_turn1.wait(timeout=3.0)
                return LlmTurnResponse(assistant_text="Turn 1 done.")
            assert any(
                m.role == "user" and m.content == queued_secret for m in messages
            )
            return LlmTurnResponse(assistant_text="Processed queued message.")
        finally:
            with turn_lock:
                active_turns -= 1

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Initial running turn"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert turn1_entered.wait(timeout=3.0)

    msg_resp = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": queued_secret},
    )
    assert msg_resp.status_code == 200
    assert msg_resp.json() == {"ok": True}

    # Release Turn 1; same AgentLoop drains queued message and runs Turn 2 serially.
    release_turn1.set()
    rec = _wait_future(registry, session_id)
    assert rec.status == "completed"
    assert max_concurrent == 1

    events = api_env["bus"].replay(session_id=session_id)
    agent_msgs = [e.data["text"] for e in events if e.type == "agent_message"]
    assert agent_msgs == ["Turn 1 done.", "Processed queued message."]
    for ev in events:
        assert queued_secret not in json.dumps(ev.data)


def test_post_message_rejected_while_awaiting_approval_and_allowed_after(
    api_env: dict[str, Any],
) -> None:
    """Message while awaiting approval returns 409; works after approval completes."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]

    turn = [0]

    def _handler(messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turn[0] += 1
        if turn[0] == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_ask",
                        tool="terminal.run",
                        args={"command": "curl https://example.com"},
                    ),
                )
            )
        if turn[0] == 2:
            return LlmTurnResponse(assistant_text="Denied handled.")
        assert any(m.role == "user" and m.content == "after denial" for m in messages)
        return LlmTurnResponse(assistant_text="After denial reply.")

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Ask first", "profile": "coding"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    paused = _wait_future(registry, session_id)
    assert paused.status == "awaiting_approval"
    aid = paused.pending_approval_id
    assert aid is not None

    # While awaiting approval -> rejected with 409 POLICY_BLOCK
    r_msg_wait = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": "premature message"},
    )
    assert r_msg_wait.status_code == 409
    assert r_msg_wait.json()["error"]["code"] == ErrorCode.POLICY_BLOCK

    # Deny approval -> run completes -> follow-up message now succeeds
    r_deny = client.post(
        f"/api/v1/sessions/{session_id}/approvals/{aid}",
        headers=api_env["headers"],
        json={"approval_id": aid, "approve": False},
    )
    assert r_deny.status_code == 200
    assert _wait_future(registry, session_id).status == "completed"

    r_msg_ok = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": "after denial"},
    )
    assert r_msg_ok.status_code == 200
    assert _wait_future(registry, session_id).status == "completed"


def test_session_end_drops_queued_messages_and_prevents_subsequent_messages(
    api_env: dict[str, Any],
) -> None:
    """Ending session while turn runs clears queued messages and rejects new ones."""
    client: TestClient = api_env["client"]
    registry: RuntimeRegistry = api_env["registry"]

    turn1_entered = threading.Event()
    release_turn1 = threading.Event()
    turns_observed: list[int] = []

    def _handler(_messages: Sequence[LlmMessage], _tools: Any) -> LlmTurnResponse:
        turns_observed.append(len(_messages))
        if not turn1_entered.is_set():
            turn1_entered.set()
            assert release_turn1.wait(timeout=3.0)
        return LlmTurnResponse(assistant_text="Turn 1 finished.")

    api_env["set_llm_factory"](lambda: _ScriptedLlm(_handler))

    resp = client.post(
        "/api/v1/sessions",
        headers=api_env["headers"],
        json={"request_text": "Start then end"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert turn1_entered.wait(timeout=3.0)

    r_q = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": "queued before end"},
    )
    assert r_q.status_code == 200

    # End session while turn 1 is still blocked -> clears queue and closes runtime
    r_end = client.post(
        f"/api/v1/sessions/{session_id}/end", headers=api_env["headers"]
    )
    assert r_end.status_code == 200
    assert r_end.json() == {"state": "ENDED"}

    with registry.lock:
        assert session_id not in registry.session_states

    release_turn1.set()
    _wait_future(registry, session_id)
    # Turn 2 must never have run for the dropped queued message
    assert len(turns_observed) == 1

    # Sending message after ENDED returns 409 SESSION_INACTIVE
    r_after = client.post(
        f"/api/v1/sessions/{session_id}/message",
        headers=api_env["headers"],
        json={"text": "too late"},
    )
    assert r_after.status_code == 409
    assert r_after.json()["error"]["code"] == ErrorCode.SESSION_INACTIVE

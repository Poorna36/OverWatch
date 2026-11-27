"""Per-session Docker exec stdio MCP runtime for the Phase 3/4 gateway.

Connects ``AuditedGatewayCoordinator`` and ``AgentLoop`` to the in-container MCP
servers packaged inside the hardened session image via an injected Docker SDK
client and ``docker exec`` multiplexed stdio sockets.

Security & deployment boundaries:
- Never mounts ``/var/run/docker.sock``, exposes MCP server ports, routes
  around Squid, or invokes the ``docker`` CLI.
- Deployment still requires supplying a usable injected Docker SDK client (e.g.,
  connected over an authenticated/TLS remote Docker endpoint or controller
  boundary); remote Docker API authentication configuration is outside this
  module's scope.
- Validates container running state and ``overwatch.managed`` /
  ``overwatch.session_id`` (and optional ``overwatch.profile``) labels before
  launching any exec process.
- Launches only servers enabled in the trusted ``Profile.mcp_servers`` using
  fixed in-image commands from ``images/README.md`` (never shell strings or
  user/model-controlled command arguments).
- Keeps one persistent MCP client session per server for the session lifetime
  so stateful servers (such as ``browser.open`` -> ``browser.read_page``)
  preserve state across calls, and cleanly closes exec streams on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
import struct
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import anyio
import docker.errors
from control_plane.contracts import ErrorCode, Profile
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
    validate_workspace_relative_input,
)
from control_plane.sessions.container_manager import (
    session_container_name,
    validate_session_id,
)
from mcp.shared.message import SessionMessage

from mcp import ClientSession, types

# Fixed in-image MCP server commands (images/README.md).
MCP_SERVER_COMMANDS: dict[str, tuple[str, ...]] = {
    "terminal": (
        "/opt/overwatch/.venv/bin/python",
        "/opt/overwatch/mcp/terminal/server.py",
    ),
    "files": (
        "/opt/overwatch/.venv/bin/python",
        "/opt/overwatch/mcp/files/server.py",
    ),
    "desktop": (
        "/opt/overwatch/.venv/bin/python",
        "/opt/overwatch/mcp/desktop/server.py",
    ),
    "browser": (
        "/opt/overwatch/.venv/bin/python",
        "/opt/overwatch/mcp/browser/server.py",
    ),
}

DEFAULT_CONTAINER_USER = "1000:1000"
DEFAULT_CONTAINER_WORKDIR = "/workspace"
DEFAULT_DESKTOP_DISPLAY = ":99"
DEFAULT_DESKTOP_SCREEN = "1280x720x24"
DEFAULT_STARTUP_TIMEOUT_S = 15.0
DEFAULT_CALL_TIMEOUT_S = 45.0
DEFAULT_DISPLAY_WAIT_S = 5.0
DEFAULT_DISPLAY_POLL_S = 0.1

_DOCKER_FRAME_HEADER = struct.Struct(">BxxxI")
_DOCKER_STDOUT_STREAM = 1
_DOCKER_STDERR_STREAM = 2
_MAX_STDERR_BYTES = 4096

_DISPLAY_RE = re.compile(r"^[A-Za-z0-9._-]*:[0-9]+(?:\.[0-9]+)?\Z")
_ERROR_PREFIX_RE = re.compile(r"\[([A-Z0-9_]+)\]\s*(.*)$")
_VALID_ERROR_CODES: frozenset[str] = frozenset(code.value for code in ErrorCode)


class McpRuntimeError(Exception):
    """Base fail-closed error raised by ``SessionMcpRuntime``."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.UPSTREAM_ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class McpRuntimeClosedError(McpRuntimeError):
    """Raised when attempting to execute after the runtime has closed."""

    def __init__(self, message: str = "Session MCP runtime is closed.") -> None:
        super().__init__(message, code=ErrorCode.SESSION_INACTIVE)


class McpUnsupportedServerError(McpRuntimeError):
    """Raised when a tool requires an unlisted or unsupported MCP server."""


class McpContainerError(McpRuntimeError):
    """Raised when target container lookup, state, or label checks fail."""


class McpTransportError(McpRuntimeError):
    """Raised when Docker exec stdio communication or startup fails."""


class McpToolExecutionError(McpRuntimeError):
    """Raised when an MCP server tool returns ``is_error=True`` or invalid JSON."""


def _validate_display(display: str) -> str:
    if not isinstance(display, str) or not _DISPLAY_RE.match(display):
        raise McpRuntimeError(
            f"Invalid X11 DISPLAY setting {display!r}.",
            code=ErrorCode.ARG_INVALID,
        )
    return display


def _recv_some(sock: Any, size: int) -> bytes:
    """Read up to ``size`` bytes from a Docker exec socket or stream double."""
    if hasattr(sock, "recv"):
        chunk = sock.recv(size)
    elif hasattr(sock, "read"):
        chunk = sock.read(size)
    else:
        raise McpTransportError("Docker exec socket supports neither recv nor read.")
    if chunk is None:
        return b""
    if isinstance(chunk, bytearray):
        return bytes(chunk)
    if not isinstance(chunk, bytes):
        raise McpTransportError(
            f"Expected bytes from Docker exec socket, got {type(chunk).__name__}."
        )
    return chunk


def _read_exact(sock: Any, length: int) -> bytes:
    """Read exactly ``length`` bytes from ``sock`` or return empty on clean EOF."""
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = _recv_some(sock, remaining)
        if not chunk:
            if not chunks:
                return b""
            raise McpTransportError("Truncated Docker exec stream frame.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(sock: Any, data: bytes) -> None:
    """Write all bytes in ``data`` to a Docker exec socket or stream double."""
    if hasattr(sock, "sendall"):
        sock.sendall(data)
        return
    # urllib3/http.client wraps raw sockets in read-only SocketIO ("rb"); prefer
    # its underlying socket._sock.sendall(...) before falling back to .write().
    inner = getattr(sock, "_sock", None)
    if inner is not None and hasattr(inner, "sendall"):
        inner.sendall(data)
        return
    if hasattr(sock, "write") and (not hasattr(sock, "writable") or sock.writable()):
        view = memoryview(data)
        offset = 0
        while offset < len(data):
            written = sock.write(view[offset:])
            if written is None:
                offset = len(data)
            elif written <= 0:
                raise McpTransportError("Zero-byte write to Docker exec socket.")
            else:
                offset += written
        if hasattr(sock, "flush"):
            sock.flush()
        return
    raise McpTransportError("Docker exec socket does not support writing.")


def _close_exec_socket(sock: Any) -> None:
    """Shut down write half (sending stdin EOF) and close socket/response."""
    if sock is None:
        return
    for target in (sock, getattr(sock, "_sock", None)):
        if target is not None and hasattr(target, "shutdown"):
            with contextlib.suppress(OSError, ValueError):
                target.shutdown(socket.SHUT_WR)
            break
    response = getattr(sock, "_response", None)
    inner = getattr(sock, "_sock", None)
    if hasattr(sock, "close"):
        with contextlib.suppress(OSError, ValueError):
            sock.close()
    if inner is not None and inner is not sock and hasattr(inner, "close"):
        with contextlib.suppress(OSError, ValueError):
            inner.close()
    if response is not None and hasattr(response, "close"):
        with contextlib.suppress(OSError, ValueError):
            response.close()


def encode_docker_frame(stream_type: int, payload: bytes) -> bytes:
    """Encode one Docker multiplexed exec stream frame (helper & tests)."""
    return _DOCKER_FRAME_HEADER.pack(stream_type, len(payload)) + payload


def _iter_docker_stdout_and_stderr(
    sock: Any,
    stderr_tail: deque[bytes],
) -> Any:
    """Yield decoded stdout chunks while capturing bounded stderr diagnostics."""
    captured_bytes = 0
    while True:
        header = _read_exact(sock, _DOCKER_FRAME_HEADER.size)
        if not header:
            break
        stream_type, payload_len = _DOCKER_FRAME_HEADER.unpack(header)
        if payload_len == 0:
            continue
        payload = _read_exact(sock, payload_len)
        if len(payload) < payload_len:
            raise McpTransportError("Truncated Docker exec payload frame.")
        if stream_type == _DOCKER_STDOUT_STREAM:
            yield payload
        elif stream_type == _DOCKER_STDERR_STREAM:
            stderr_tail.append(payload)
            captured_bytes += len(payload)
            while captured_bytes > _MAX_STDERR_BYTES and stderr_tail:
                dropped = stderr_tail.popleft()
                captured_bytes -= len(dropped)


@asynccontextmanager
async def _docker_exec_stdio_transport(
    sock: Any,
    stderr_tail: deque[bytes],
) -> AsyncIterator[tuple[Any, Any]]:
    """Expose MCP ``(read_stream, write_stream)`` over a Docker exec socket."""
    read_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    def _reader_thread() -> None:
        buffer = ""
        try:
            for chunk in _iter_docker_stdout_and_stderr(sock, stderr_tail):
                if stop_event.is_set():
                    break
                text = chunk.decode("utf-8", errors="replace")
                lines = (buffer + text).split("\n")
                buffer = lines.pop()
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        msg = types.jsonrpc_message_adapter.validate_json(
                            stripped, by_name=False
                        )
                        item: SessionMessage | Exception = SessionMessage(msg)
                    except ValueError as exc:
                        item = exc
                    fut = asyncio.run_coroutine_threadsafe(read_writer.send(item), loop)
                    fut.result()
        except Exception as exc:
            if not stop_event.is_set() and not loop.is_closed():
                with contextlib.suppress(Exception):
                    asyncio.run_coroutine_threadsafe(
                        read_writer.send(
                            McpTransportError(f"Docker exec stream failed: {exc}")
                        ),
                        loop,
                    ).result(timeout=1.0)
        finally:
            if not loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(read_writer.close)

    reader = threading.Thread(
        target=_reader_thread,
        name="overwatch-mcp-exec-reader",
        daemon=True,
    )
    reader.start()

    async def _writer_task() -> None:
        try:
            async with write_reader:
                async for session_message in write_reader:
                    raw = (
                        session_message.message.model_dump_json(
                            by_alias=True, exclude_unset=True
                        )
                        + "\n"
                    ).encode("utf-8")
                    await anyio.to_thread.run_sync(_write_all, sock, raw)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            pass
        except (OSError, ValueError, McpTransportError) as exc:
            with contextlib.suppress(Exception):
                await read_writer.send(
                    McpTransportError(f"Failed writing to Docker exec stdin: {exc}")
                )
        finally:
            with contextlib.suppress(Exception):
                await read_writer.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_writer_task)
        try:
            yield read_stream, write_stream
        finally:
            stop_event.set()
            with contextlib.suppress(Exception):
                await write_stream.aclose()
            with contextlib.suppress(Exception):
                await read_stream.aclose()
            await anyio.to_thread.run_sync(_close_exec_socket, sock)
            await anyio.to_thread.run_sync(reader.join, 2.0)
            tg.cancel_scope.cancel()


@dataclass
class _ServerHandle:
    server_name: str
    session: ClientSession
    stack: AsyncExitStack
    stderr_tail: deque[bytes]


def _extract_container_state_and_labels(
    container: Any,
) -> tuple[bool, dict[str, str]]:
    """Extract running flag and label map from a Docker SDK Container object."""
    status = getattr(container, "status", None)
    attrs = getattr(container, "attrs", None)
    running = False
    if isinstance(status, str) and status.lower() == "running":
        running = True
    elif isinstance(attrs, Mapping):
        state = attrs.get("State")
        if isinstance(state, Mapping):
            state_running = state.get("Running")
            state_status = state.get("Status")
            if isinstance(state_running, bool):
                running = state_running
            elif isinstance(state_status, str) and state_status.lower() == "running":
                running = True

    raw_labels = getattr(container, "labels", None)
    if not isinstance(raw_labels, Mapping) and isinstance(attrs, Mapping):
        cfg = attrs.get("Config")
        if isinstance(cfg, Mapping):
            raw_labels = cfg.get("Labels")

    labels: dict[str, str] = {}
    if isinstance(raw_labels, Mapping):
        for k, v in raw_labels.items():
            if isinstance(k, str) and isinstance(v, str):
                labels[k] = v
    return running, labels


def _parse_call_tool_result(
    tool_name: str, result: types.CallToolResult
) -> dict[str, Any]:
    """Convert an MCP ``CallToolResult`` into a contract dict or raise on error."""
    if result.is_error:
        text_parts: list[str] = []
        for item in result.content:
            text_val = getattr(item, "text", None)
            if isinstance(text_val, str) and text_val.strip():
                text_parts.append(text_val.strip())
        raw_msg = "; ".join(text_parts) if text_parts else f"{tool_name} failed."
        code = ErrorCode.UPSTREAM_ERROR
        message = raw_msg
        match = _ERROR_PREFIX_RE.search(raw_msg)
        if match and match.group(1) in _VALID_ERROR_CODES:
            code = ErrorCode(match.group(1))
            message = match.group(2) or raw_msg
        raise McpToolExecutionError(message, code=code)

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured

    for item in result.content:
        text_val = getattr(item, "text", None)
        if isinstance(text_val, str) and text_val.strip():
            try:
                parsed = json.loads(text_val)
            except json.JSONDecodeError as exc:
                raise McpToolExecutionError(
                    f"Tool '{tool_name}' returned non-JSON text payload."
                ) from exc
            if isinstance(parsed, dict):
                return parsed

    raise McpToolExecutionError(
        f"Tool '{tool_name}' returned no structured dict payload."
    )


_CONTAINER_PATH_JAIL_SCRIPT = (
    "import json, os, sys\n"
    "from control_plane.gateway.path_jail import PathJail, PathJailError\n"
    "try:\n"
    '    rel = PathJail("/workspace").relative_path('
    'os.environ["OVERWATCH_TARGET_PATH"])\n'
    '    print(json.dumps({"ok": True, "rel": rel}))\n'
    "except PathJailError as exc:\n"
    '    print(json.dumps({"ok": False, "reason": exc.reason or "path_jail", '
    '"message": exc.message}))\n'
    "    sys.exit(2)\n"
)


class ContainerPathJail(PathJail):
    """Gateway ``PathJail`` backed by the session container's ``/workspace``."""

    def __init__(
        self,
        runtime: SessionMcpRuntime,
        container_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    ) -> None:
        root_str = str(container_root).strip()
        if not root_str or "\x00" in root_str:
            raise PathJailError(
                "Container workspace root cannot be empty.",
                reason="invalid_root",
            )
        posix_root = PurePosixPath(root_str)
        if not posix_root.is_absolute():
            raise PathJailError(
                f"Container workspace root must be absolute: {root_str!r}",
                reason="invalid_root",
            )
        self._runtime = runtime
        self._root = Path(posix_root)

    def relative_path(self, raw_path: str) -> str:
        """Validate ``raw_path`` lexically and resolve symlinks inside container."""
        validate_workspace_relative_input(raw_path)
        return self._runtime.resolve_workspace_relative_path(raw_path)

    def resolve(self, raw_path: str) -> Path:
        """Return canonical POSIX container path inside ``/workspace``."""
        rel = self.relative_path(raw_path)
        base = PurePosixPath(self._root.as_posix())
        return Path(base if rel == "." else base / rel)


_MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"
_SCREENSHOT_CAPTURE_ARGV: tuple[str, ...] = (
    "python3",
    "-c",
    (
        "import os, subprocess, sys, tempfile\n"
        "with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:\n"
        "    path = f.name\n"
        "try:\n"
        "    rc = subprocess.run(\n"
        "        ['scrot', '-o', '--', path],\n"
        "        check=False,\n"
        "        stdout=subprocess.DEVNULL,\n"
        "        stderr=subprocess.PIPE,\n"
        "    )\n"
        "    if rc.returncode != 0:\n"
        "        sys.stderr.buffer.write(rc.stderr)\n"
        "        sys.exit(rc.returncode)\n"
        "    with open(path, 'rb') as img:\n"
        "        sys.stdout.buffer.write(img.read())\n"
        "finally:\n"
        "    if os.path.exists(path):\n"
        "        os.unlink(path)\n"
    ),
)


class SessionMcpRuntime:
    """Manages persistent Docker-exec stdio MCP sessions for one sandbox container."""

    def __init__(
        self,
        *,
        docker_client: Any,
        session_id: str,
        profile: Profile,
        container_id: str | None = None,
        display: str = DEFAULT_DESKTOP_DISPLAY,
        startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
        display_wait_s: float = DEFAULT_DISPLAY_WAIT_S,
    ) -> None:
        if docker_client is None or not hasattr(docker_client, "containers"):
            raise TypeError("docker_client must be a Docker SDK-compatible client.")
        if not isinstance(profile, Profile):
            raise TypeError(f"Expected contract Profile, got {type(profile).__name__}.")
        self._client = docker_client
        self._session_id = validate_session_id(session_id)
        self._profile = profile
        self._container_ref = container_id or session_container_name(self._session_id)
        self._display = _validate_display(display)
        self._startup_timeout_s = float(startup_timeout_s)
        self._call_timeout_s = float(call_timeout_s)
        self._display_wait_s = float(display_wait_s)

        self._lock = threading.RLock()
        self._closed = False
        self._verified_container_id: str | None = None
        self._desktop_ready = False
        self._desktop_bg_pids: list[int] = []
        self._servers: dict[str, _ServerHandle] = {}

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name=f"overwatch-mcp-loop-{self._session_id}",
            daemon=True,
        )
        self._loop_thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_servers(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._servers.keys())

    def __enter__(self) -> SessionMcpRuntime:
        self.verify_container()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def __call__(self, validated_args: ValidatedArguments) -> dict[str, Any]:
        """Execute one gateway-validated tool call (coordinator executor API)."""
        return self.execute(validated_args)

    def execute(self, validated_args: ValidatedArguments) -> dict[str, Any]:
        """Route a ``ValidatedArguments`` call to its persistent MCP server."""
        if not isinstance(validated_args, ValidatedArguments):
            raise TypeError(
                f"Expected ValidatedArguments, got {type(validated_args).__name__}."
            )
        return self.call_tool(validated_args.tool, validated_args.args)

    def verify_container(self) -> str:
        """Verify target container is running and labeled for this exact session."""
        with self._lock:
            if self._closed:
                raise McpRuntimeClosedError()
            try:
                container = self._client.containers.get(self._container_ref)
            except docker.errors.NotFound as exc:
                raise McpContainerError(
                    f"Session container '{self._container_ref}' was not found.",
                    code=ErrorCode.FATAL,
                ) from exc
            except docker.errors.APIError as exc:
                raise McpContainerError(
                    f"Failed inspecting session container '{self._container_ref}': "
                    f"{exc}",
                    code=ErrorCode.FATAL,
                ) from exc

            running, labels = _extract_container_state_and_labels(container)
            if not running:
                raise McpContainerError(
                    f"Session container '{self._container_ref}' is not running.",
                    code=ErrorCode.SESSION_INACTIVE,
                )

            if labels.get("overwatch.managed") != "true":
                raise McpContainerError(
                    f"Container '{self._container_ref}' is missing "
                    "'overwatch.managed=true' label.",
                    code=ErrorCode.FATAL,
                )
            label_sid = labels.get("overwatch.session_id")
            if label_sid != self._session_id:
                raise McpContainerError(
                    f"Container '{self._container_ref}' session label "
                    f"{label_sid!r} does not match session '{self._session_id}'.",
                    code=ErrorCode.FATAL,
                )
            label_profile = labels.get("overwatch.profile")
            if label_profile is not None and label_profile != self._profile.name:
                raise McpContainerError(
                    f"Container '{self._container_ref}' profile label "
                    f"{label_profile!r} does not match profile "
                    f"'{self._profile.name}'.",
                    code=ErrorCode.FATAL,
                )

            container_id = getattr(container, "id", None) or self._container_ref
            self._verified_container_id = str(container_id)
            return self._verified_container_id

    def _run_exec_probe(
        self,
        container_id: str,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
    ) -> int:
        """Run a short fixed-argv command inside container and return exit code."""
        try:
            created = self._client.api.exec_create(
                container_id,
                list(argv),
                stdin=False,
                stdout=True,
                stderr=True,
                tty=False,
                privileged=False,
                user=DEFAULT_CONTAINER_USER,
                environment=env,
                workdir=DEFAULT_CONTAINER_WORKDIR,
            )
            exec_id = created["Id"]
            self._client.api.exec_start(exec_id, detach=False, tty=False)
            info = self._client.api.exec_inspect(exec_id)
        except (docker.errors.APIError, KeyError, TypeError) as exc:
            raise McpTransportError(
                f"Docker exec probe failed for {argv[0]!r}: {exc}"
            ) from exc

        exit_code = info.get("ExitCode")
        return int(exit_code) if isinstance(exit_code, int) else 1

    def _run_exec_capture(
        self,
        container_id: str,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Run a short fixed-argv command inside container and capture stdout."""
        try:
            created = self._client.api.exec_create(
                container_id,
                list(argv),
                stdin=False,
                stdout=True,
                stderr=False,
                tty=False,
                privileged=False,
                user=DEFAULT_CONTAINER_USER,
                environment=env,
                workdir=DEFAULT_CONTAINER_WORKDIR,
            )
            exec_id = created["Id"]
            output = self._client.api.exec_start(exec_id, detach=False, tty=False)
            info = self._client.api.exec_inspect(exec_id)
        except (docker.errors.APIError, KeyError, TypeError) as exc:
            raise McpTransportError(
                f"Docker exec capture failed for {argv[0]!r}: {exc}"
            ) from exc

        exit_code = info.get("ExitCode")
        code = int(exit_code) if isinstance(exit_code, int) else 1
        if output is None:
            raw = b""
        elif isinstance(output, (bytes, bytearray)):
            raw = bytes(output)
        else:
            raise McpTransportError(
                f"Unexpected exec output type from {argv[0]!r}: {type(output).__name__}"
            )
        return code, raw

    def _start_detached_exec(
        self,
        container_id: str,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
    ) -> int | None:
        """Start a detached fixed-argv background process and return PID if known."""
        try:
            created = self._client.api.exec_create(
                container_id,
                list(argv),
                stdin=False,
                stdout=False,
                stderr=False,
                tty=False,
                privileged=False,
                user=DEFAULT_CONTAINER_USER,
                environment=env,
                workdir=DEFAULT_CONTAINER_WORKDIR,
            )
            exec_id = created["Id"]
            self._client.api.exec_start(exec_id, detach=True, tty=False)
            info = self._client.api.exec_inspect(exec_id)
        except (docker.errors.APIError, KeyError, TypeError) as exc:
            raise McpTransportError(
                f"Failed starting container background process {argv[0]!r}: {exc}"
            ) from exc

        pid = info.get("Pid")
        if isinstance(pid, int) and pid > 1:
            return pid
        return None

    def ensure_desktop_display(self) -> str:
        """Ensure Xvfb + openbox are running inside the container (no KasmVNC)."""
        with self._lock:
            if self._closed:
                raise McpRuntimeClosedError()
            if self._desktop_ready:
                return self._display

            container_id = self.verify_container()

            # Reuse display if already alive inside this session container.
            if (
                self._run_exec_probe(
                    container_id, ["xset", "-display", self._display, "q"]
                )
                == 0
            ):
                self._desktop_ready = True
                return self._display

            # Ensure X11 Unix socket directory exists inside writable /tmp tmpfs.
            self._run_exec_probe(
                container_id, ["mkdir", "-p", "-m", "1777", "/tmp/.X11-unix"]
            )

            xvfb_pid = self._start_detached_exec(
                container_id,
                [
                    "Xvfb",
                    self._display,
                    "-screen",
                    "0",
                    DEFAULT_DESKTOP_SCREEN,
                    "-nolisten",
                    "tcp",
                ],
            )
            if xvfb_pid is not None:
                self._desktop_bg_pids.append(xvfb_pid)

            deadline = time.monotonic() + self._display_wait_s
            ready = False
            while time.monotonic() < deadline:
                if (
                    self._run_exec_probe(
                        container_id, ["xset", "-display", self._display, "q"]
                    )
                    == 0
                ):
                    ready = True
                    break
                time.sleep(DEFAULT_DISPLAY_POLL_S)

            if not ready:
                raise McpTransportError(
                    f"Timed out waiting for Xvfb display {self._display!r} inside "
                    f"session container '{self._container_ref}'."
                )

            openbox_pid = self._start_detached_exec(
                container_id,
                ["openbox"],
                env={"DISPLAY": self._display},
            )
            if openbox_pid is not None:
                self._desktop_bg_pids.append(openbox_pid)

            self._desktop_ready = True
            return self._display

    def capture_screenshot_jpeg(self) -> bytes:
        """Capture a live JPEG screenshot from the session container's display."""
        with self._lock:
            if self._closed:
                raise McpRuntimeClosedError()
            display = self.ensure_desktop_display()
            container_id = self.verify_container()

        exit_code, raw = self._run_exec_capture(
            container_id,
            _SCREENSHOT_CAPTURE_ARGV,
            env={"DISPLAY": display},
        )
        if exit_code != 0:
            raise McpTransportError(
                f"Screenshot capture failed inside container '{self._container_ref}' "
                f"(exit={exit_code})."
            )
        if (
            not raw
            or len(raw) > _MAX_SCREENSHOT_BYTES
            or not raw.startswith(_JPEG_MAGIC)
        ):
            raise McpTransportError(
                f"Container '{self._container_ref}' returned invalid JPEG "
                "screenshot data."
            )
        return raw

    def _validate_server_allowed(self, server_name: str) -> tuple[str, ...]:
        if server_name not in self._profile.mcp_servers:
            raise McpUnsupportedServerError(
                f"MCP server '{server_name}' is not permitted by profile "
                f"'{self._profile.name}'.",
                code=ErrorCode.NOT_IN_PROFILE,
            )
        command = MCP_SERVER_COMMANDS.get(server_name)
        if command is None:
            raise McpUnsupportedServerError(
                f"MCP server '{server_name}' is not packaged or supported in the "
                "session container runtime.",
                code=ErrorCode.UPSTREAM_ERROR,
            )
        return command

    def start_server(self, server_name: str) -> None:
        """Start (if not already connected) the persistent MCP client for server."""
        with self._lock:
            if self._closed:
                raise McpRuntimeClosedError()
            if server_name in self._servers:
                return
            command = self._validate_server_allowed(server_name)
            container_id = self.verify_container()

            env: dict[str, str] | None = None
            if server_name == "desktop":
                display = self.ensure_desktop_display()
                env = {"DISPLAY": display}

            try:
                created = self._client.api.exec_create(
                    container_id,
                    list(command),
                    stdin=True,
                    stdout=True,
                    stderr=True,
                    tty=False,
                    privileged=False,
                    user=DEFAULT_CONTAINER_USER,
                    environment=env,
                    workdir=DEFAULT_CONTAINER_WORKDIR,
                )
                exec_id = created["Id"]
                sock = self._client.api.exec_start(
                    exec_id,
                    detach=False,
                    tty=False,
                    stream=False,
                    socket=True,
                )
            except (docker.errors.APIError, KeyError, TypeError) as exc:
                raise McpTransportError(
                    f"Failed launching '{server_name}' MCP exec stream: {exc}"
                ) from exc

            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._open_client_session(server_name, sock),
                    self._loop,
                )
                handle = fut.result(timeout=self._startup_timeout_s)
            except McpRuntimeError:
                _close_exec_socket(sock)
                raise
            except Exception as exc:
                _close_exec_socket(sock)
                raise McpTransportError(
                    f"Failed initializing '{server_name}' MCP session: {exc}"
                ) from exc

            self._servers[server_name] = handle

    async def _open_client_session(
        self,
        server_name: str,
        sock: Any,
    ) -> _ServerHandle:
        stderr_tail: deque[bytes] = deque()
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                _docker_exec_stdio_transport(sock, stderr_tail)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            await session.list_tools()
        except Exception as exc:
            await stack.aclose()
            stderr_hint = (
                b"".join(stderr_tail).decode("utf-8", errors="replace").strip()
            )
            suffix = f" (stderr: {stderr_hint})" if stderr_hint else ""
            raise McpTransportError(
                f"MCP '{server_name}' initialization failed{suffix}: {exc}"
            ) from exc

        return _ServerHandle(
            server_name=server_name,
            session=session,
            stack=stack,
            stderr_tail=stderr_tail,
        )

    def _drop_server(self, server_name: str) -> None:
        with self._lock:
            handle = self._servers.pop(server_name, None)
        if handle is not None and self._loop.is_running():
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(
                    handle.stack.aclose(), self._loop
                ).result(timeout=2.0)

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a contract-named MCP tool inside the running session container."""
        if not isinstance(tool_name, str) or "." not in tool_name:
            raise McpUnsupportedServerError(
                f"Invalid MCP tool name {tool_name!r}.",
                code=ErrorCode.POLICY_BLOCK,
            )
        server_name, _, action = tool_name.partition(".")
        if not server_name or not action:
            raise McpUnsupportedServerError(
                f"Invalid MCP tool name {tool_name!r}.",
                code=ErrorCode.POLICY_BLOCK,
            )

        with self._lock:
            if self._closed:
                raise McpRuntimeClosedError()
            if server_name not in self._servers:
                self.start_server(server_name)
            handle = self._servers[server_name]

        payload_args = {} if arguments is None else dict(arguments)
        try:
            fut = asyncio.run_coroutine_threadsafe(
                handle.session.call_tool(tool_name, payload_args),
                self._loop,
            )
            result = fut.result(timeout=self._call_timeout_s)
        except TimeoutError as exc:
            self._drop_server(server_name)
            raise McpTransportError(
                f"MCP call '{tool_name}' timed out after {self._call_timeout_s}s.",
                code=ErrorCode.TIMEOUT,
            ) from exc
        except McpRuntimeError:
            raise
        except Exception as exc:
            self._drop_server(server_name)
            raise McpTransportError(
                f"MCP transport error calling '{tool_name}': {exc}"
            ) from exc

        return _parse_call_tool_result(tool_name, result)

    def resolve_workspace_relative_path(self, raw_path: str) -> str:
        """Validate ``raw_path`` lexically and resolve symlinks inside container."""
        validate_workspace_relative_input(raw_path)
        container_id = self.verify_container()

        try:
            created = self._client.api.exec_create(
                container_id,
                [
                    "/opt/overwatch/.venv/bin/python",
                    "-c",
                    _CONTAINER_PATH_JAIL_SCRIPT,
                ],
                stdin=False,
                stdout=True,
                stderr=True,
                tty=False,
                privileged=False,
                user=DEFAULT_CONTAINER_USER,
                environment={"OVERWATCH_TARGET_PATH": raw_path},
                workdir=DEFAULT_CONTAINER_WORKDIR,
            )
            exec_id = created["Id"]
            raw_out = self._client.api.exec_start(
                exec_id, detach=False, tty=False, demux=True
            )
            info = self._client.api.exec_inspect(exec_id)
        except (docker.errors.APIError, KeyError, TypeError) as exc:
            raise PathJailError(
                f"Failed to resolve workspace path inside session container: {exc}",
                path=raw_path,
                reason="resolution_error",
            ) from exc

        stdout_bytes: bytes
        if isinstance(raw_out, tuple) and len(raw_out) == 2:
            stdout_bytes = raw_out[0] or b""
        elif isinstance(raw_out, bytes):
            stdout_bytes = raw_out
        else:
            stdout_bytes = b""

        try:
            payload = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise PathJailError(
                "Malformed response resolving path inside session container.",
                path=raw_path,
                reason="resolution_error",
            ) from exc

        if not isinstance(payload, dict):
            raise PathJailError(
                "Invalid response resolving path inside session container.",
                path=raw_path,
                reason="resolution_error",
            )

        exit_code = info.get("ExitCode")
        if exit_code == 2 and payload.get("ok") is False:
            reason = payload.get("reason")
            msg = payload.get("message")
            raise PathJailError(
                str(msg or "Path rejected by container workspace jail."),
                path=raw_path,
                reason=str(reason or "path_jail"),
            )

        if exit_code != 0 or payload.get("ok") is not True:
            raise PathJailError(
                "Container workspace path resolution failed.",
                path=raw_path,
                reason="resolution_error",
            )

        rel = payload.get("rel")
        if not isinstance(rel, str) or not rel:
            raise PathJailError(
                "Invalid normalized relative path from container.",
                path=raw_path,
                reason="resolution_error",
            )
        if rel != ".":
            validate_workspace_relative_input(rel)
        return rel

    def build_workspace_path_jail(self) -> ContainerPathJail:
        """Build a gateway ``PathJail`` bound to this session container's /workspace."""
        return ContainerPathJail(self)

    def close(self) -> None:
        """Close all persistent MCP client sessions, exec streams, and loop."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handles = list(self._servers.values())
            self._servers.clear()
            bg_pids = list(reversed(self._desktop_bg_pids))
            self._desktop_bg_pids.clear()
            container_id = self._verified_container_id

        for handle in handles:
            if self._loop.is_running():
                with contextlib.suppress(Exception):
                    asyncio.run_coroutine_threadsafe(
                        handle.stack.aclose(), self._loop
                    ).result(timeout=3.0)

        if bg_pids and container_id is not None:
            for pid in bg_pids:
                with contextlib.suppress(Exception):
                    self._run_exec_probe(container_id, ["kill", "-TERM", str(pid)])

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread.is_alive():
            self._loop_thread.join(timeout=3.0)
        if not self._loop.is_closed():
            self._loop.close()

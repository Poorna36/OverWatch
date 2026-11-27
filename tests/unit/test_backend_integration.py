"""Focused backend integration & regression tests across Docker client,
session startup, approvals, timeouts, message delivery, replay, and shutdown.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import docker.errors
import pytest
from control_plane.agent.llm import (
    LlmAdapter,
    LlmMessage,
    LlmToolCall,
    LlmTurnResponse,
    ToolDefinition,
)
from control_plane.agent.loop import AgentLoopResult, PendingApprovalContinuation
from control_plane.api.deps import (
    get_approval_manager,
    get_container_manager,
    get_event_bus,
    get_llm_factory,
    get_policy_loader,
    get_profile_loader,
    get_public_base_url,
    get_runtime_registry,
    get_session_factory,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.contracts import ErrorCode
from control_plane.db.models import ApprovalModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.policy_loader import PolicyLoader
from control_plane.sessions.container_manager import SessionContainerManager
from control_plane.sessions.docker_client import DockerClientConfigError
from control_plane.sessions.egress_policy import EgressPolicyManager
from control_plane.sessions.profile_loader import ProfileLoader
from control_plane.sessions.session_runner import (
    RuntimeRegistry,
    SessionRunner,
    StartedSession,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_TOKEN = "integration-test-bearer-token"


class _StubContainer:
    def __init__(
        self,
        container_id: str,
        name: str,
        labels: dict[str, str],
        network_name: str,
        ip_address: str,
    ) -> None:
        self.id = container_id
        self.name = name
        self.labels = dict(labels)
        self.status = "created"
        self._networks_meta = {network_name: {"IPAddress": ip_address}}
        self.stopped = False
        self.removed = False
        self._refresh()

    def _refresh(self) -> None:
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
        self.status = "running"
        self._refresh()

    def pause(self) -> None:
        self.status = "paused"
        self._refresh()

    def unpause(self) -> None:
        self.status = "running"
        self._refresh()

    def reload(self) -> None:
        self._refresh()

    def stop(self, timeout: int = 5) -> None:
        del timeout
        self.stopped = True
        self.status = "exited"
        self._refresh()

    def remove(self, force: bool = True) -> None:
        del force
        self.removed = True


class _StubNetwork:
    def __init__(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.id = f"net_{name}"
        self.name = name
        self.labels = dict(labels or {})
        self.containers: list[Any] = []
        self.removed = False
        self.attrs = {
            "Name": name,
            "Internal": True,
            "Options": {},
            "Labels": self.labels,
        }

    def connect(self, container: Any, *_args: Any, **kwargs: Any) -> None:
        ipv4 = kwargs.get("ipv4_address")
        if hasattr(container, "_networks_meta") and ipv4:
            container._networks_meta[self.name] = {"IPAddress": ipv4}
        if container not in self.containers:
            self.containers.append(container)

    def disconnect(self, container: Any, force: bool = True) -> None:
        del force
        if container in self.containers:
            self.containers.remove(container)

    def reload(self) -> None:
        pass

    def remove(self) -> None:
        self.removed = True


class _StubVolume:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def remove(self, force: bool = True) -> None:
        del force
        self.removed = True


def _build_stub_docker_client() -> MagicMock:
    client = MagicMock(spec=docker.DockerClient)
    client.api = MagicMock()
    client.api._version = "1.45"
    client.ping.return_value = True

    containers: dict[str, _StubContainer] = {}
    networks: dict[str, _StubNetwork] = {}
    volumes: dict[str, _StubVolume] = {}
    proxy = _StubContainer(
        "proxy_id",
        "overwatch-egress-proxy",
        {},
        "mgmt",
        "172.30.0.2",
    )
    proxy.status = "running"
    proxy._refresh()
    containers["overwatch-egress-proxy"] = proxy

    img = MagicMock()
    img.id = "sha256:stub-image"
    client.images.get.return_value = img

    def create_vol(name: str, **_kwargs: Any) -> _StubVolume:
        v = _StubVolume(name)
        volumes[name] = v
        return v

    def get_vol(name: str) -> _StubVolume:
        if name not in volumes or volumes[name].removed:
            raise docker.errors.NotFound(name)
        return volumes[name]

    def create_net(name: str, **kwargs: Any) -> _StubNetwork:
        net = _StubNetwork(name, labels=kwargs.get("labels"))
        networks[name] = net
        return net

    def get_net(name: str) -> _StubNetwork:
        if name not in networks or networks[name].removed:
            raise docker.errors.NotFound(name)
        return networks[name]

    def create_container(**kwargs: Any) -> _StubContainer:
        name = kwargs["name"]
        labels = dict(kwargs.get("labels") or {})
        net_name = kwargs.get("network", "")
        net_cfg = (kwargs.get("networking_config") or {}).get(net_name) or {}
        ip_addr = (net_cfg.get("IPAMConfig") or {}).get("IPv4Address", "172.30.1.3")
        c = _StubContainer(f"cid_{name}", name, labels, net_name, ip_addr)
        containers[name] = c
        containers[c.id] = c
        return c

    def get_container(name_or_id: str) -> _StubContainer:
        if name_or_id not in containers or containers[name_or_id].removed:
            raise docker.errors.NotFound(name_or_id)
        return containers[name_or_id]

    client.volumes.create.side_effect = create_vol
    client.volumes.get.side_effect = get_vol
    client.networks.create.side_effect = create_net
    client.networks.get.side_effect = get_net
    client.networks.list.return_value = []
    client.containers.create.side_effect = create_container
    client.containers.get.side_effect = get_container
    return client


class _InMemoryWorkspaceRuntime:
    """In-memory executor implementing the runtime surface expected by SessionRunner."""

    def __init__(self, workspace_dir: Path) -> None:
        self._ws = workspace_dir
        self._ws.mkdir(parents=True, exist_ok=True)
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify_container(self) -> str:
        return "cid_stub"

    def start_server(self, _server_name: str) -> None:
        pass

    def build_workspace_path_jail(self) -> PathJail:
        return PathJail(self._ws)

    def __call__(self, validated_args: Any) -> dict[str, Any]:
        tool = validated_args.tool
        args = dict(validated_args.args)
        self.calls.append((tool, args))
        if tool == "files.write":
            rel = str(args["path"])
            target = (self._ws / rel).resolve()
            target.write_text(str(args["content"]), encoding="utf-8")
            return {"bytes": len(str(args["content"]).encode("utf-8"))}
        if tool == "files.read":
            rel = str(args["path"])
            target = (self._ws / rel).resolve()
            return {"content": target.read_text(encoding="utf-8")}
        if tool == "files.delete":
            rel = str(args["path"])
            target = (self._ws / rel).resolve()
            target.unlink(missing_ok=True)
            return {"deleted": True, "path": rel}
        if tool == "terminal.run":
            return {
                "exit_code": 0,
                "stdout": f"ran:{args['command']}",
                "stderr": "",
                "truncated": False,
            }
        raise AssertionError(f"Unexpected tool call: {tool}")

    def close(self) -> None:
        self.closed = True


class _ScriptedLlm(LlmAdapter):
    """Thread-safe scripted LLM recording all user/tool messages seen per turn."""

    def __init__(
        self,
        handler: Callable[[int, Sequence[LlmMessage]], LlmTurnResponse],
    ) -> None:
        self._handler = handler
        self._turn = 0
        self.seen_messages: list[tuple[LlmMessage, ...]] = []

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolDefinition],
    ) -> LlmTurnResponse:
        del system_prompt, tools
        self._turn += 1
        self.seen_messages.append(tuple(messages))
        return self._handler(self._turn, messages)


def _setup_api_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    docker_factory: Callable[[], docker.DockerClient],
    llm_factory: Callable[[], LlmAdapter],
    runtime_box: list[_InMemoryWorkspaceRuntime] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[Any, SessionContainerManager, RuntimeRegistry, SessionRunner]:
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    db_path = tmp_path / "integration.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    event_bus = EventBus(session_factory)
    clock_fn = clock or (lambda: datetime.now(UTC))
    approval_manager = ApprovalManager(session_factory, event_bus, clock=clock_fn)
    acl_path = tmp_path / "squid" / "sessions.conf"
    egress_mgr = EgressPolicyManager(acl_path=acl_path)
    container_mgr = SessionContainerManager(
        docker_client_factory=docker_factory,
        profiles_dir=REPO_ROOT / "profiles",
        egress_policy_manager=egress_mgr,
    )
    registry = RuntimeRegistry()
    profile_loader = ProfileLoader(profiles_dir=REPO_ROOT / "profiles")
    policy_loader = PolicyLoader(policy_path=REPO_ROOT / "policy.yaml")

    def _runtime_factory(**kwargs: Any) -> Any:
        del kwargs
        rt = _InMemoryWorkspaceRuntime(tmp_path / "ws")
        if runtime_box is not None:
            runtime_box.append(rt)
        return rt

    runner = SessionRunner(
        session_factory=session_factory,
        container_manager=container_mgr,
        event_bus=event_bus,
        approval_manager=approval_manager,
        profile_loader=profile_loader,
        policy_loader=policy_loader,
        runtime_factory=_runtime_factory,
        llm_factory=llm_factory,
        clock=clock_fn,
        prompts_base_dir=REPO_ROOT,
        registry=registry,
    )

    from control_plane.api.deps import get_session_runner

    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_event_bus] = lambda: event_bus
    app.dependency_overrides[get_approval_manager] = lambda: approval_manager
    app.dependency_overrides[get_container_manager] = lambda: container_mgr
    app.dependency_overrides[get_profile_loader] = lambda: profile_loader
    app.dependency_overrides[get_policy_loader] = lambda: policy_loader
    app.dependency_overrides[get_llm_factory] = lambda: llm_factory
    app.dependency_overrides[get_runtime_registry] = lambda: registry
    app.dependency_overrides[get_public_base_url] = lambda: "http://testserver"
    app.dependency_overrides[get_stream_clock] = lambda: clock_fn
    app.dependency_overrides[get_session_runner] = lambda: runner

    return session_factory, container_mgr, registry, runner


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {APP_TOKEN}"}


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 4.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for condition")


def test_e2e_backend_lifecycle_create_tool_approve_message_replay_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise full backend lifecycle across REST, WS replay, approvals, and audit."""
    factory_calls = 0
    stub_docker = _build_stub_docker_client()

    def _docker_factory() -> docker.DockerClient:
        nonlocal factory_calls
        factory_calls += 1
        return stub_docker

    def _llm_handler(turn: int, messages: Sequence[LlmMessage]) -> LlmTurnResponse:
        if turn == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_write",
                        tool="files.write",
                        args={"path": "hello.txt", "content": "from-integration"},
                    ),
                )
            )
        if turn == 2:
            # Sensitive tool requiring user approval
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_del",
                        tool="files.delete",
                        args={"path": "hello.txt"},
                    ),
                )
            )
        if turn == 3:
            return LlmTurnResponse(assistant_text="Approved deletion complete.")
        if turn == 4:
            assert any(
                m.role == "user" and "follow-up check" in m.content for m in messages
            )
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_write_2",
                        tool="files.write",
                        args={"path": "after.txt", "content": "queued-ok"},
                    ),
                )
            )
        return LlmTurnResponse(assistant_text="Follow-up completed.")

    llm = _ScriptedLlm(_llm_handler)
    runtimes: list[_InMemoryWorkspaceRuntime] = []
    session_factory, container_mgr, registry, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=_docker_factory,
        llm_factory=lambda: llm,
        runtime_box=runtimes,
    )

    try:
        # Lazy check: docker_client_factory must not run until session creation
        assert factory_calls == 0

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "write then delete file", "profile": "coding"},
            )
            assert resp.status_code == 200
            body = resp.json()
            sid = body["session_id"]
            assert body["state"] == "ACTIVE"
            assert factory_calls == 1

            # Wait for agent to reach awaiting_approval on files.delete
            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "awaiting_approval"
                )
            )

            approvals_resp = client.get(
                f"/api/v1/sessions/{sid}/approvals?pending=true",
                headers=_auth_headers(),
            )
            assert approvals_resp.status_code == 200
            pending = approvals_resp.json()["approvals"]
            assert len(pending) == 1
            aid = pending[0]["approval_id"]
            assert pending[0]["tool"] == "files.delete"

            # Approve pending tool call -> resumes AgentLoop off-loop
            resolve_resp = client.post(
                f"/api/v1/sessions/{sid}/approvals/{aid}",
                headers=_auth_headers(),
                json={"approval_id": aid, "approve": True},
            )
            assert resolve_resp.status_code == 200
            assert resolve_resp.json() == {"ok": True}

            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "completed"
                )
            )
            assert (tmp_path / "ws" / "hello.txt").exists() is False

            # Send follow-up message after turn completion
            msg_resp = client.post(
                f"/api/v1/sessions/{sid}/message",
                headers=_auth_headers(),
                json={"text": "follow-up check"},
            )
            assert msg_resp.status_code == 200
            assert msg_resp.json() == {"ok": True}

            _wait_until(
                lambda: (tmp_path / "ws" / "after.txt").exists()
                and runner.get_run_record(sid) is not None
                and runner.get_run_record(sid).status == "completed"
                and len(llm.seen_messages) >= 5
            )
            assert (tmp_path / "ws" / "after.txt").read_text(
                encoding="utf-8"
            ) == "queued-ok"

            # Verify detail stream_url + WebSocket hello event replay
            detail_resp = client.get(
                f"/api/v1/sessions/{sid}",
                headers=_auth_headers(),
            )
            assert detail_resp.status_code == 200
            stream_url = detail_resp.json()["stream_url"]
            ws_path = stream_url.replace("ws://testserver", "")

            replayed_events: list[dict[str, Any]] = []
            with client.websocket_connect(ws_path) as ws:
                ws.send_json({"type": "hello", "last_seq": 0})
                while True:
                    frame = ws.receive_json()
                    replayed_events.append(frame)
                    if (
                        frame["type"] == "agent_message"
                        and frame["data"]["text"] == "Follow-up completed."
                    ):
                        break

            event_types = [ev["type"] for ev in replayed_events]
            assert [ev["seq"] for ev in replayed_events] == list(
                range(1, len(replayed_events) + 1)
            )
            assert "session_state" in event_types
            assert "approval_request" in event_types
            assert "approval_resolved" in event_types
            assert "tool_call" in event_types
            assert "tool_result" in event_types
            assert "agent_message" in event_types

            # End session -> closes runtime, tears down Docker, emits session_ended
            end_resp = client.post(
                f"/api/v1/sessions/{sid}/end",
                headers=_auth_headers(),
            )
            assert end_resp.status_code == 200
            assert end_resp.json() == {"state": "ENDED"}
            assert runtimes[0].closed is True
            assert container_mgr.subnet_pool.active_count == 0

            # Audit hash chain verifies cleanly
            with session_factory() as db:
                audit_check = verify_session_audit(db, sid)
                assert audit_check.valid is True
                assert audit_check.entry_count >= 3
                row = db.get(SessionModel, sid)
                assert row is not None and row.state == "ENDED"
    finally:
        runner.shutdown(wait=True)


def test_e2e_approval_denial_and_timeout_flows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify both explicit user denial and deadline timer expiration fail closed."""
    stub_docker = _build_stub_docker_client()

    def _deny_handler(turn: int, _messages: Sequence[LlmMessage]) -> LlmTurnResponse:
        if turn == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_ask_deny",
                        tool="files.delete",
                        args={"path": "keep.txt"},
                    ),
                )
            )
        return LlmTurnResponse(assistant_text="Handled denial safely.")

    llm = _ScriptedLlm(_deny_handler)
    _, _, _, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "delete keep.txt", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]

            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "awaiting_approval"
                )
            )
            aid = runner.get_run_record(sid).pending_approval_id
            assert aid is not None

            deny_resp = client.post(
                f"/api/v1/sessions/{sid}/approvals/{aid}",
                headers=_auth_headers(),
                json={"approval_id": aid, "approve": False},
            )
            assert deny_resp.status_code == 200
            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "completed"
                )
            )
            assert any(
                m.role == "tool" and "USER_DENIED" in m.content
                for m in llm.seen_messages[-1]
            )
            assert (
                client.post(
                    f"/api/v1/sessions/{sid}/end", headers=_auth_headers()
                ).status_code
                == 200
            )
    finally:
        runner.shutdown(wait=True)


def test_approval_timeout_deadline_wakes_session_and_rejects_late_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When approval expires, deadline timer resumes loop and late POST returns 409."""
    stub_docker = _build_stub_docker_client()
    base_now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
    clock_state = {"now": base_now}

    def _timeout_handler(turn: int, _messages: Sequence[LlmMessage]) -> LlmTurnResponse:
        if turn == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_timeout",
                        tool="files.delete",
                        args={"path": "keep.txt"},
                    ),
                )
            )
        return LlmTurnResponse(assistant_text="Timed out safely.")

    llm = _ScriptedLlm(_timeout_handler)
    session_factory, _, registry, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
        clock=lambda: clock_state["now"],
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "delete keep.txt", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]

            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "awaiting_approval"
                )
            )
            aid = runner.get_run_record(sid).pending_approval_id
            assert aid is not None

            # Advance clock past 120s timeout and invoke deadline callback directly
            clock_state["now"] = base_now + timedelta(seconds=125)
            runner._on_approval_deadline(sid, aid)

            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "completed"
                )
            )
            assert any(
                m.role == "tool" and "APPROVAL_TIMEOUT" in m.content
                for m in llm.seen_messages[-1]
            )

            late_resp = client.post(
                f"/api/v1/sessions/{sid}/approvals/{aid}",
                headers=_auth_headers(),
                json={"approval_id": aid, "approve": True},
            )
            assert late_resp.status_code == 409
            assert late_resp.json()["error"]["code"] == ErrorCode.APPROVAL_TIMEOUT.value

            with session_factory() as db:
                row = db.scalar(
                    select(ApprovalModel).where(ApprovalModel.approval_id == aid)
                )
                assert row is not None and row.status == "timed_out"
    finally:
        runner.shutdown(wait=True)


def test_regression_finalize_awaiting_approval_does_not_clobber_early_resumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: early approval resolution preserves is_running=True.

    Previously, if on_approval_resolved ran before run_session_agent finished
    _finalize_loop_result, _finalize_loop_result unconditionally set is_running=False,
    blocking POST /sessions/{id}/message during the resumed run.
    """
    stub_docker = _build_stub_docker_client()
    resume_started = threading.Event()
    allow_resume_finish = threading.Event()

    def _handler(turn: int, messages: Sequence[LlmMessage]) -> LlmTurnResponse:
        if turn == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_ask",
                        tool="files.delete",
                        args={"path": "x.txt"},
                    ),
                )
            )
        if turn == 2:
            resume_started.set()
            assert allow_resume_finish.wait(timeout=3.0)
            return LlmTurnResponse(assistant_text="Resumed turn done.")
        assert any(m.role == "user" and "mid-resume msg" in m.content for m in messages)
        return LlmTurnResponse(assistant_text="Queued during resume processed.")

    llm = _ScriptedLlm(_handler)
    session_factory, _, registry, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "delete x.txt", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]
            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "awaiting_approval"
                )
            )
            aid = runner.get_run_record(sid).pending_approval_id
            assert aid is not None

            # Simulate the exact race: resolve & start resumption, THEN invoke
            # _finalize_loop_result with the stale awaiting_approval result while
            # the resumed run is actively in progress.
            with session_factory() as db:
                row = db.get(SessionModel, sid)
                assert row is not None
            resolve_resp = client.post(
                f"/api/v1/sessions/{sid}/approvals/{aid}",
                headers=_auth_headers(),
                json={"approval_id": aid, "approve": True},
            )
            assert resolve_resp.status_code == 200
            assert resume_started.wait(timeout=3.0)

            approval_obj = runner._approval_manager.get_approval(aid)
            stale_result = AgentLoopResult(
                session_id=sid,
                status="awaiting_approval",
                pending_approval=approval_obj,
                continuation=PendingApprovalContinuation(
                    session_id=sid,
                    approval_id=aid,
                    pending_call=MagicMock(),
                    system_prompt="",
                    tools=(),
                    messages=(),
                    remaining_tool_calls=(),
                    calls_used=1,
                    calls_max=10,
                    turns=1,
                ),
            )
            started_stub = StartedSession(
                session_id=sid,
                state="ACTIVE",
                profile=ProfileLoader(REPO_ROOT / "profiles").load("coding"),
                policy=PolicyLoader(REPO_ROOT / "policy.yaml").load(),
                container_info=MagicMock(),
                created_at="2026-11-27T00:00:00Z",
                llm=llm,
            )
            runner._finalize_loop_result(
                session_id=sid,
                started=started_stub,
                runtime=runner.get_runtime(sid),  # type: ignore[arg-type]
                result=stale_result,
            )

            # ctx.is_running must STILL be True so message queuing succeeds!
            msg_resp = client.post(
                f"/api/v1/sessions/{sid}/message",
                headers=_auth_headers(),
                json={"text": "mid-resume msg"},
            )
            assert msg_resp.status_code == 200
            allow_resume_finish.set()

            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "completed"
                    and len(llm.seen_messages) >= 3
                )
            )
    finally:
        allow_resume_finish.set()
        runner.shutdown(wait=True)


def test_regression_end_session_not_overwritten_by_failed_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: worker error after /end must not revert ENDED->FAILED."""
    stub_docker = _build_stub_docker_client()
    in_turn = threading.Event()
    release_turn = threading.Event()

    def _exploding_handler(
        _turn: int, _messages: Sequence[LlmMessage]
    ) -> LlmTurnResponse:
        in_turn.set()
        assert release_turn.wait(timeout=3.0)
        raise RuntimeError("runtime closed mid-turn")

    llm = _ScriptedLlm(_exploding_handler)
    session_factory, _, _, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "run and get ended", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]
            assert in_turn.wait(timeout=3.0)

            end_resp = client.post(
                f"/api/v1/sessions/{sid}/end",
                headers=_auth_headers(),
            )
            assert end_resp.status_code == 200
            assert end_resp.json() == {"state": "ENDED"}

            release_turn.set()
            rec = runner.wait_for_session(sid, timeout=3.0)
            assert rec is not None and rec.status == "error"

            with session_factory() as db:
                row = db.get(SessionModel, sid)
                assert row is not None and row.state == "ENDED"
    finally:
        release_turn.set()
        runner.shutdown(wait=True)


def test_docker_client_failure_fails_closed_then_recovers_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insecure/broken Docker client fails closed on startup and recovers once fixed."""
    stub_docker = _build_stub_docker_client()
    should_fail = {"flag": True}

    def _flaky_factory() -> docker.DockerClient:
        if should_fail["flag"]:
            raise DockerClientConfigError("Insecure tcp:// endpoint rejected")
        return stub_docker

    llm = _ScriptedLlm(lambda _t, _m: LlmTurnResponse(assistant_text="Recovered!"))
    session_factory, container_mgr, _, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=_flaky_factory,
        llm_factory=lambda: llm,
    )

    try:
        with TestClient(app) as client:
            fail_resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "hello", "profile": "coding"},
            )
            assert fail_resp.status_code == 500
            assert container_mgr._client is None

            should_fail["flag"] = False
            ok_resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "hello again", "profile": "coding"},
            )
            assert ok_resp.status_code == 200
            sid = ok_resp.json()["session_id"]
            rec = runner.wait_for_session(sid, timeout=3.0)
            assert rec is not None and rec.status == "completed"
            assert (
                client.post(
                    f"/api/v1/sessions/{sid}/end", headers=_auth_headers()
                ).status_code
                == 200
            )
    finally:
        runner.shutdown(wait=True)


def test_regression_atomic_failed_update_does_not_overwrite_concurrent_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic ACTIVE->FAILED update never overwrites ENDED/PAUSED sessions."""
    stub_docker = _build_stub_docker_client()
    llm = _ScriptedLlm(lambda _t, _m: LlmTurnResponse(assistant_text="ok"))
    session_factory, _, _, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "hello", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]
            assert runner.wait_for_session(sid, timeout=3.0).status == "completed"

            end_resp = client.post(
                f"/api/v1/sessions/{sid}/end",
                headers=_auth_headers(),
            )
            assert end_resp.status_code == 200

            # Even if a racing worker attempts to mark FAILED after ENDED,
            # the conditional UPDATE WHERE state='ACTIVE' is a safe no-op.
            assert (
                runner._mark_failed_if_active(sid, reason="late worker error") is False
            )
            with session_factory() as db:
                row = db.get(SessionModel, sid)
                assert row is not None and row.state == "ENDED"
    finally:
        runner.shutdown(wait=True)


def test_regression_post_end_approval_listing_does_not_clobber_run_record_with_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expiring approvals via GET /approvals after /end does not set FATAL."""
    stub_docker = _build_stub_docker_client()
    clock_state = {"now": datetime(2026, 11, 27, 10, 0, 0, tzinfo=UTC)}
    llm = _ScriptedLlm(
        lambda _t, _m: LlmTurnResponse(
            tool_calls=(
                LlmToolCall(
                    call_id="c_ask",
                    tool="files.delete",
                    args={"path": "temp.txt"},
                ),
            )
        )
    )
    session_factory, _, _, runner = _setup_api_harness(
        tmp_path,
        monkeypatch,
        docker_factory=lambda: stub_docker,
        llm_factory=lambda: llm,
        clock=lambda: clock_state["now"],
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sessions",
                headers=_auth_headers(),
                json={"request_text": "delete temp.txt", "profile": "coding"},
            )
            assert resp.status_code == 200
            sid = resp.json()["session_id"]
            _wait_until(
                lambda: (
                    (rec := runner.get_run_record(sid)) is not None
                    and rec.status == "awaiting_approval"
                )
            )
            aid = runner.get_run_record(sid).pending_approval_id
            assert aid is not None

            end_resp = client.post(
                f"/api/v1/sessions/{sid}/end",
                headers=_auth_headers(),
            )
            assert end_resp.status_code == 200
            assert end_resp.json() == {"state": "ENDED"}

            # Advance clock past approval_timeout_s and poll GET /approvals.
            clock_state["now"] = clock_state["now"] + timedelta(seconds=300)
            list_resp = client.get(
                f"/api/v1/sessions/{sid}/approvals?pending=true",
                headers=_auth_headers(),
            )
            assert list_resp.status_code == 200
            assert list_resp.json() == {"approvals": []}

            with session_factory() as db:
                approval_row = db.get(ApprovalModel, aid)
                assert approval_row is not None and approval_row.status == "timed_out"

            rec_after = runner.get_run_record(sid)
            assert rec_after is not None
            assert rec_after.status == "awaiting_approval"
            assert rec_after.error_code is None
    finally:
        runner.shutdown(wait=True)

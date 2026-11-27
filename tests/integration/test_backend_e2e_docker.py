"""Real Docker-backed end-to-end backend lifecycle integration test.

Skips cleanly when Docker or the required local images
(``pocket/py-node:1.0`` and ``ubuntu/squid:5.2-22.04_beta``) are unavailable.

Exercises the full backend path through FastAPI REST + WebSocket routes:
1. ``POST /api/v1/sessions`` -> ``SessionRunner`` -> ``SessionContainerManager``
   using ``create_docker_client`` -> real hardened ``pocket/py-node:1.0``
   container + temporary Squid proxy + real ``SessionMcpRuntime`` servers.
2. Tool execution (``files.write`` allowed) and sensitive tool approval
   (``files.delete`` asked -> ``GET /approvals`` -> ``POST /approvals/{id}``).
3. Follow-up user message delivery via ``POST /api/v1/sessions/{id}/message``
   executing ``files.read`` inside the running container.
4. WebSocket ``hello`` event replay on ``/api/v1/sessions/{id}/stream``.
5. ``POST /api/v1/sessions/{id}/end`` tearing down container, network, volume,
   and Squid ACL entry, plus audit hash-chain verification.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import docker
import pytest
from control_plane.agent.llm import (
    LlmAdapter,
    LlmMessage,
    LlmToolCall,
    LlmTurnResponse,
    ToolDefinition,
)
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
    get_session_runner,
)
from control_plane.api.main import app
from control_plane.api.stream import get_stream_clock
from control_plane.audit.verify_audit import verify_session_audit
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.policy_loader import PolicyLoader
from control_plane.sessions.container_manager import SessionContainerManager
from control_plane.sessions.docker_client import create_docker_client
from control_plane.sessions.egress_policy import EgressPolicyManager
from control_plane.sessions.profile_loader import ProfileLoader
from control_plane.sessions.session_runner import RuntimeRegistry, SessionRunner
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_IMAGE = "pocket/py-node:1.0"
SQUID_IMAGE = "ubuntu/squid:5.2-22.04_beta"
SQUID_CONF = REPO_ROOT / "infra" / "squid.conf"
SQUID_ENTRYPOINT = REPO_ROOT / "infra" / "entrypoint.sh"
APP_TOKEN = "docker-e2e-test-bearer-token"


def _check_docker_ready():
    try:
        client = create_docker_client()
    except Exception as exc:
        return None, f"Docker unavailable: {exc}"

    missing = []
    for image in (SESSION_IMAGE, SQUID_IMAGE):
        try:
            client.images.get(image)
        except Exception:
            missing.append(image)
    if missing:
        return None, f"Required images not built locally: {', '.join(missing)}"
    return client, None


_docker_client, _skip_reason = _check_docker_ready()

pytestmark = pytest.mark.skipif(
    _skip_reason is not None,
    reason=_skip_reason or "",
)


class _ScriptedDockerE2ELlm(LlmAdapter):
    def __init__(self) -> None:
        self._turn = 0

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolDefinition],
    ) -> LlmTurnResponse:
        del system_prompt, tools
        self._turn += 1
        if self._turn == 1:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_write_1",
                        tool="files.write",
                        args={"path": "keep.txt", "content": "persisted-in-container"},
                    ),
                    LlmToolCall(
                        call_id="c_write_2",
                        tool="files.write",
                        args={"path": "temp.txt", "content": "delete-me"},
                    ),
                )
            )
        if self._turn == 2:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_del_temp",
                        tool="files.delete",
                        args={"path": "temp.txt"},
                    ),
                )
            )
        if self._turn == 3:
            return LlmTurnResponse(
                assistant_text="Created keep.txt and deleted temp.txt."
            )
        if self._turn == 4:
            return LlmTurnResponse(
                tool_calls=(
                    LlmToolCall(
                        call_id="c_read_keep",
                        tool="files.read",
                        args={"path": "keep.txt"},
                    ),
                )
            )
        # Ensure follow-up files.read output was received from real container
        assert any(
            m.role == "tool" and "persisted-in-container" in m.content for m in messages
        )
        return LlmTurnResponse(assistant_text="Verified keep.txt contents.")


@pytest.fixture()
def temp_squid_proxy():
    assert _docker_client is not None
    client = _docker_client
    proxy_name = f"ow-e2e-proxy-{uuid.uuid4().hex[:10]}"
    tmpdir = tempfile.TemporaryDirectory(prefix="ow_e2e_acl_")
    acl_dir = Path(tmpdir.name)
    acl_dir.chmod(0o755)
    acl_path = acl_dir / "sessions.conf"
    acl_path.write_text(
        "# Overwatch Squid Dynamic Session ACLs - No active sessions\n",
        encoding="utf-8",
    )

    squid = None
    try:
        squid = client.containers.run(
            SQUID_IMAGE,
            name=proxy_name,
            entrypoint=["/entrypoint.sh"],
            volumes={
                str(SQUID_CONF): {"bind": "/etc/squid/squid.conf", "mode": "ro"},
                str(SQUID_ENTRYPOINT): {"bind": "/entrypoint.sh", "mode": "ro"},
                str(acl_dir): {"bind": "/etc/squid/conf.d", "mode": "ro"},
            },
            detach=True,
            remove=False,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            squid.reload()
            if (
                squid.status == "running"
                and "Squid running with PID" in squid.logs().decode()
            ):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Temporary Squid proxy failed to become ready.")

        yield {
            "client": client,
            "proxy_name": proxy_name,
            "acl_path": acl_path,
        }
    finally:
        if squid is not None:
            try:
                squid.remove(force=True)
            except Exception:
                pass
        tmpdir.cleanup()


def test_real_docker_backend_e2e_session_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_squid_proxy: dict[str, Any],
) -> None:
    """Full backend E2E flow backed by real hardened Docker container + MCP servers."""
    monkeypatch.setenv("APP_TOKEN", APP_TOKEN)
    db_path = tmp_path / "e2e_docker.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    event_bus = EventBus(session_factory)
    clock_fn = lambda: datetime.now(UTC)  # noqa: E731
    approval_manager = ApprovalManager(session_factory, event_bus, clock=clock_fn)
    egress_mgr = EgressPolicyManager(acl_path=temp_squid_proxy["acl_path"])
    container_mgr = SessionContainerManager(
        docker_client_factory=create_docker_client,
        profiles_dir=REPO_ROOT / "profiles",
        egress_policy_manager=egress_mgr,
        proxy_container_name=temp_squid_proxy["proxy_name"],
    )
    registry = RuntimeRegistry()
    profile_loader = ProfileLoader(profiles_dir=REPO_ROOT / "profiles")
    policy_loader = PolicyLoader(policy_path=REPO_ROOT / "policy.yaml")
    llm = _ScriptedDockerE2ELlm()

    runner = SessionRunner(
        session_factory=session_factory,
        container_manager=container_mgr,
        event_bus=event_bus,
        approval_manager=approval_manager,
        profile_loader=profile_loader,
        policy_loader=policy_loader,
        llm_factory=lambda: llm,
        clock=clock_fn,
        prompts_base_dir=REPO_ROOT,
        registry=registry,
    )

    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_event_bus] = lambda: event_bus
    app.dependency_overrides[get_approval_manager] = lambda: approval_manager
    app.dependency_overrides[get_container_manager] = lambda: container_mgr
    app.dependency_overrides[get_profile_loader] = lambda: profile_loader
    app.dependency_overrides[get_policy_loader] = lambda: policy_loader
    app.dependency_overrides[get_llm_factory] = lambda: (lambda: llm)
    app.dependency_overrides[get_runtime_registry] = lambda: registry
    app.dependency_overrides[get_public_base_url] = lambda: "http://testserver"
    app.dependency_overrides[get_stream_clock] = lambda: clock_fn
    app.dependency_overrides[get_session_runner] = lambda: runner

    headers = {"Authorization": f"Bearer {APP_TOKEN}"}
    sid: str | None = None
    try:
        with TestClient(app) as client:
            create_resp = client.post(
                "/api/v1/sessions",
                headers=headers,
                json={
                    "request_text": "write, delete, then verify",
                    "profile": "coding",
                },
            )
            assert create_resp.status_code == 200
            sid = create_resp.json()["session_id"]

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                rec = runner.get_run_record(sid)
                if rec is not None and rec.status == "awaiting_approval":
                    break
                time.sleep(0.05)
            else:
                rec = runner.get_run_record(sid)
                raise AssertionError(f"Timed out waiting for awaiting_approval: {rec}")

            approvals_resp = client.get(
                f"/api/v1/sessions/{sid}/approvals?pending=true",
                headers=headers,
            )
            assert approvals_resp.status_code == 200
            aid = approvals_resp.json()["approvals"][0]["approval_id"]

            resolve_resp = client.post(
                f"/api/v1/sessions/{sid}/approvals/{aid}",
                headers=headers,
                json={"approval_id": aid, "approve": True},
            )
            assert resolve_resp.status_code == 200

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                rec = runner.get_run_record(sid)
                if rec is not None and rec.status == "completed":
                    break
                time.sleep(0.05)
            else:
                rec = runner.get_run_record(sid)
                raise AssertionError(
                    f"Timed out waiting for completed after approval: {rec}"
                )

            msg_resp = client.post(
                f"/api/v1/sessions/{sid}/message",
                headers=headers,
                json={"text": "read keep.txt"},
            )
            assert msg_resp.status_code == 200

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                rec = runner.get_run_record(sid)
                if rec is not None and rec.status == "completed" and llm._turn >= 5:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(
                    "Timed out waiting for follow-up message completion"
                )

            detail_resp = client.get(f"/api/v1/sessions/{sid}", headers=headers)
            assert detail_resp.status_code == 200
            ws_path = detail_resp.json()["stream_url"].replace("ws://testserver", "")
            events: list[dict[str, Any]] = []
            with client.websocket_connect(ws_path) as ws:
                ws.send_json({"type": "hello", "last_seq": 0})
                while True:
                    frame = ws.receive_json()
                    events.append(frame)
                    if (
                        frame["type"] == "agent_message"
                        and frame["data"]["text"] == "Verified keep.txt contents."
                    ):
                        break

            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            assert any(e["type"] == "approval_resolved" for e in events)

            end_resp = client.post(f"/api/v1/sessions/{sid}/end", headers=headers)
            assert end_resp.status_code == 200
            assert end_resp.json() == {"state": "ENDED"}

        docker_client = temp_squid_proxy["client"]
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(f"sess_{sid}")
        with pytest.raises(docker.errors.NotFound):
            docker_client.networks.get(f"sess_net_{sid}")
        with pytest.raises(docker.errors.NotFound):
            docker_client.volumes.get(f"ws_{sid}")
        assert sid not in temp_squid_proxy["acl_path"].read_text(encoding="utf-8")

        with session_factory() as db:
            audit_res = verify_session_audit(db, sid)
            assert audit_res.valid is True
            assert audit_res.entry_count >= 4
            row = db.get(SessionModel, sid)
            assert row is not None and row.state == "ENDED"
    finally:
        app.dependency_overrides.clear()
        if sid is not None:
            runner.close_runtime(sid)
            try:
                container_mgr.teardown_session(sid)
            except Exception:
                pass
        runner.shutdown(wait=True)
        engine.dispose()

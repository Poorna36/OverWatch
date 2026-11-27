"""Docker-backed integration smoke test for ``SessionMcpRuntime``.

Skips cleanly when Docker or the local ``pocket/py-node:1.0`` image is
unavailable. Runs a real hardened session container and exercises gateway
``AuditedGatewayCoordinator`` -> ``SessionMcpRuntime`` -> ``docker exec`` stdio
for ``files``, ``terminal``, and ``desktop`` (auto-starting Xvfb + openbox).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from control_plane.audit.audit_writer import AuditWriter
from control_plane.contracts import ToolCall
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.mcp_runtime import (
    MCP_SERVER_COMMANDS,
    SessionMcpRuntime,
)
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile

SESSION_IMAGE = "pocket/py-node:1.0"
SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"


def _check_docker_and_image():
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return None, f"Docker unavailable: {exc}"

    try:
        client.images.get(SESSION_IMAGE)
    except Exception:
        return None, f"Required image not built locally: {SESSION_IMAGE}"

    return client, None


_docker_client, _skip_reason = _check_docker_and_image()

pytestmark = pytest.mark.skipif(
    _skip_reason is not None,
    reason=_skip_reason or "",
)


def test_session_mcp_runtime_executes_hardened_container_tools(
    tmp_path: Path,
) -> None:
    """Coordinator + SessionMcpRuntime run real files, terminal, desktop tools."""
    assert _docker_client is not None
    client = _docker_client
    suffix = uuid.uuid4().hex[:8]
    cname = f"ow_rt_smoke_{suffix}"
    vol_name = f"ws_rt_smoke_{suffix}"

    policy = load_policy()
    profile = load_profile("coding")

    db_path = tmp_path / "rt_int.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        s.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_int",
                created_at="2026-11-27T10:30:00Z",
                updated_at="2026-11-27T10:30:00Z",
            )
        )
        s.commit()

    # Placeholder workspace root for host-side PathJail argument normalization.
    host_ws = tmp_path / "host_ws"
    host_ws.mkdir()

    volume = client.volumes.create(name=vol_name)
    container = client.containers.run(
        SESSION_IMAGE,
        command=["sleep", "infinity"],
        detach=True,
        name=cname,
        user="1000:1000",
        read_only=True,
        tmpfs={"/tmp": "rw,size=256m"},
        volumes={vol_name: {"bind": "/workspace", "mode": "rw"}},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        pids_limit=256,
        mem_limit="1g",
        nano_cpus=1_000_000_000,
        network_mode="none",
        labels={
            "overwatch.managed": "true",
            "overwatch.session_id": SESSION_ID,
            "overwatch.profile": "coding",
        },
    )

    runtime = SessionMcpRuntime(
        docker_client=client,
        session_id=SESSION_ID,
        profile=profile,
        container_id=container.id,
    )
    try:
        coordinator = AuditedGatewayCoordinator(
            pipeline=GatewayPreflightPipeline(policy, workspace_root=host_ws),
            audit_writer=AuditWriter(factory),
        )

        w_res = coordinator.handle_call(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_write",
                tool="files.write",
                args={"path": "greeting.txt", "content": "hello-docker-mcp"},
            ),
            profile,
            executor=runtime,
        )
        assert w_res.executed is True
        assert w_res.tool_result.result == {"bytes": 16}

        r_res = coordinator.handle_call(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_read",
                tool="files.read",
                args={"path": "greeting.txt"},
            ),
            profile,
            executor=runtime,
        )
        assert r_res.executed is True
        assert r_res.tool_result.result == {"content": "hello-docker-mcp"}

        t_res = runtime.call_tool("terminal.run", {"command": "cat greeting.txt"})
        assert t_res["exit_code"] == 0
        assert t_res["stdout"] == "hello-docker-mcp"

        d_res = coordinator.handle_call(
            ToolCall(
                session_id=SESSION_ID,
                call_id="c_shot",
                tool="desktop.screenshot",
                args={},
            ),
            profile,
            executor=runtime,
        )
        assert d_res.executed is True
        shot = d_res.tool_result.result
        assert shot["width"] == 1280
        assert shot["height"] == 720
        assert len(shot["image_b64"]) > 20

        assert set(runtime.active_servers) == {"files", "terminal", "desktop"}
        assert "browser" in MCP_SERVER_COMMANDS
    finally:
        runtime.close()
        engine.dispose()
        try:
            container.remove(force=True)
        except Exception:
            pass
        try:
            volume.remove(force=True)
        except Exception:
            pass

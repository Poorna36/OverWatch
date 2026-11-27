"""Docker-backed integration smoke test for ``SessionMcpRuntime``.

Skips cleanly when Docker or the local ``pocket/py-node:1.0`` image is
unavailable. Runs a real hardened session container and exercises gateway
``AuditedGatewayCoordinator`` -> ``SessionMcpRuntime`` -> ``docker exec`` stdio
for ``files``, ``terminal``, and ``desktop`` (auto-starting Xvfb + openbox).
"""

from __future__ import annotations

import struct
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from control_plane.api.deps import (
    derive_owner_id,
    get_session_factory,
    get_session_runner,
    get_stream_clock,
)
from control_plane.api.main import app
from control_plane.api.stream_tokens import verify_view_token
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
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SESSION_IMAGE = "pocket/py-node:1.0"
SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"
TEST_APP_TOKEN = "docker-live-view-test-token"
BASE_NOW = datetime(2026, 11, 27, 2, 0, 0, tzinfo=UTC)


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

        jpeg_bytes = runtime.capture_screenshot_jpeg()
        assert jpeg_bytes.startswith(b"\xff\xd8\xff")
        assert len(jpeg_bytes) > 100

        html_bytes, html_mime = runtime.read_view_asset("vnc.html")
        assert html_mime.startswith("text/html")
        assert b"KasmVNC" in html_bytes

        bridge = runtime.open_view_ws_bridge()
        try:
            op, ver = bridge.recv_frame()
            assert op == 0x2
            assert ver.startswith(b"RFB 003.008\n")

            bridge.send_frame(0x2, b"RFB 003.008\n")
            op, sec = bridge.recv_frame()
            assert op == 0x2
            assert 1 in list(sec[1 : 1 + sec[0]])

            bridge.send_frame(0x2, bytes([1]))
            op, res = bridge.recv_frame()
            assert op == 0x2
            assert res == b"\x00\x00\x00\x00"

            bridge.send_frame(0x2, bytes([1]))
            op, sinit = bridge.recv_frame()
            assert op == 0x2
            w, h = struct.unpack("!HH", sinit[:4])
            assert (w, h) == (1280, 720)

            bridge.send_frame(0x2, struct.pack("!BBHHHH", 3, 0, 0, 0, 1280, 720))
            op, fb_upd = bridge.recv_frame()
            assert op == 0x2
            assert fb_upd[0] == 0  # FramebufferUpdate
            num_rects = struct.unpack("!H", fb_upd[2:4])[0]
            assert num_rects > 0
            assert len(fb_upd) > 4
        finally:
            bridge.close()
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


def test_live_view_browser_acceptance_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browser live-view acceptance: assets, cookie exchange, WS RFB screen data."""
    assert _docker_client is not None
    client = _docker_client
    suffix = uuid.uuid4().hex[:8]
    cname = f"ow_live_view_acc_{suffix}"
    vol_name = f"ws_live_view_acc_{suffix}"
    session_id = f"s_01{suffix.upper()}0000000000000000"[:28]

    public_base = "https://overwatch.example.com:8443"
    monkeypatch.setenv("APP_TOKEN", TEST_APP_TOKEN)
    monkeypatch.setenv("PUBLIC_BASE_URL", public_base)

    profile = load_profile("coding")
    db_path = tmp_path / "live_acc.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    owner_id = derive_owner_id(TEST_APP_TOKEN)

    with factory() as s:
        s.add(
            SessionModel(
                session_id=session_id,
                state="ACTIVE",
                profile="coding",
                owner_id=owner_id,
                created_at="2026-11-27T10:30:00Z",
                updated_at="2026-11-27T10:30:00Z",
            )
        )
        s.commit()

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
            "overwatch.session_id": session_id,
            "overwatch.profile": "coding",
        },
    )

    runtime = SessionMcpRuntime(
        docker_client=client,
        session_id=session_id,
        profile=profile,
        container_id=container.id,
    )

    class _RunnerStub:
        def get_runtime(self, sid: str) -> SessionMcpRuntime | None:
            return runtime if sid == session_id else None

    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_session_runner] = lambda: _RunnerStub()
    app.dependency_overrides[get_stream_clock] = lambda: (lambda: BASE_NOW)

    try:
        api_client = TestClient(app, base_url=public_base)
        headers = {"Authorization": f"Bearer {TEST_APP_TOKEN}"}

        # 1. GET /sessions/{id} returns tokenized view_url
        detail_resp = api_client.get(f"/api/v1/sessions/{session_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert "view_url" in detail
        view_url = detail["view_url"]
        parsed = urlsplit(view_url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "overwatch.example.com:8443"
        assert parsed.path == f"/api/v1/sessions/{session_id}/view"
        token = parse_qs(parsed.query)["token"][0]

        claims = verify_view_token(
            token,
            expected_session_id=session_id,
            signing_key=TEST_APP_TOKEN,
            now=BASE_NOW,
        )
        assert claims.owner_id == owner_id
        assert claims.session_id == session_id
        assert claims.scope == "view"

        # 2. Token exchange for path-scoped cookie
        entry_resp = api_client.get(
            f"/api/v1/sessions/{session_id}/view?token={token}",
            follow_redirects=False,
        )
        assert entry_resp.status_code == 302
        assert (
            entry_resp.headers["location"]
            == f"/api/v1/sessions/{session_id}/view/vnc.html"
        )
        set_cookie = entry_resp.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "secure" in set_cookie.lower()
        assert (
            "SameSite=strict" in set_cookie.lower()
            or "samesite=strict" in set_cookie.lower()
        )
        assert f"Path=/api/v1/sessions/{session_id}/view" in set_cookie

        # 3. Trusted static asset serving through the view path
        html_resp = api_client.get(f"/api/v1/sessions/{session_id}/view/vnc.html")
        assert html_resp.status_code == 200
        assert b"KasmVNC" in html_resp.content
        assert html_resp.headers["x-content-type-options"] == "nosniff"
        assert html_resp.headers["cache-control"] == "no-store"
        assert html_resp.headers["x-frame-options"] == "SAMEORIGIN"
        assert "Content-Security-Policy" in html_resp.headers

        # Path traversal and unpermitted extensions rejected
        assert (
            api_client.get(
                f"/api/v1/sessions/{session_id}/view/../etc/passwd"
            ).status_code
            == 404
        )
        assert (
            api_client.get(f"/api/v1/sessions/{session_id}/view/secret.pem").status_code
            == 404
        )

        # 4. WebSocket security: foreign Origin and missing Origin rejected
        with pytest.raises(WebSocketDisconnect) as exc_foreign:
            with api_client.websocket_connect(
                f"/api/v1/sessions/{session_id}/view/websockify",
                headers={
                    "Origin": "https://attacker.example",
                    "Cookie": f"overwatch_view={token}",
                },
            ):
                pass
        assert exc_foreign.value.code == 1008

        with pytest.raises(WebSocketDisconnect) as exc_missing:
            with api_client.websocket_connect(
                f"/api/v1/sessions/{session_id}/view/websockify",
                headers={"Cookie": f"overwatch_view={token}"},
            ):
                pass
        assert exc_missing.value.code == 1008

        # 5. Live browser WebSocket connection + RFB exchange + live screen data
        with api_client.websocket_connect(
            f"/api/v1/sessions/{session_id}/view/websockify",
            headers={
                "Origin": public_base,
                "Cookie": f"overwatch_view={token}",
            },
            subprotocols=["binary"],
        ) as ws:
            # ServerVersion
            ver = ws.receive_bytes()
            assert ver.startswith(b"RFB 003.008\n")

            # ClientVersion
            ws.send_bytes(b"RFB 003.008\n")

            # SecurityTypes
            sec = ws.receive_bytes()
            assert 1 in list(sec[1 : 1 + sec[0]])

            # Client selects None (1)
            ws.send_bytes(bytes([1]))

            # SecurityResult OK
            res = ws.receive_bytes()
            assert res == b"\x00\x00\x00\x00"

            # ClientInit (shared=1)
            ws.send_bytes(bytes([1]))

            # ServerInit
            sinit = ws.receive_bytes()
            w, h = struct.unpack("!HH", sinit[:4])
            assert (w, h) == (1280, 720)

            # FramebufferUpdateRequest
            ws.send_bytes(struct.pack("!BBHHHH", 3, 0, 0, 0, w, h))

            # FramebufferUpdate with live screen data
            fb_upd = ws.receive_bytes()
            assert fb_upd[0] == 0  # FramebufferUpdate message-type
            num_rects = struct.unpack("!H", fb_upd[2:4])[0]
            assert num_rects > 0
            assert len(fb_upd) > 4

        # 6. Disconnect cleanup: verify bridge is closed and discarded from runtime
        for _ in range(50):
            if len(runtime._view_bridges) == 0:
                break
            time.sleep(0.05)
        assert len(runtime._view_bridges) == 0

        # 7. Cross-session isolation: view token and cookie cannot access other sessions
        other_session_id = "sess_diff_isolation"
        assert api_client.get(
            f"/api/v1/sessions/{other_session_id}/view?token={token}",
            follow_redirects=False,
        ).status_code in (401, 404)
        assert api_client.get(
            f"/api/v1/sessions/{other_session_id}/view/vnc.html",
            headers={"Cookie": f"overwatch_view={token}"},
        ).status_code in (401, 404)
        with pytest.raises(WebSocketDisconnect) as exc_cross:
            with api_client.websocket_connect(
                f"/api/v1/sessions/{other_session_id}/view/websockify",
                headers={
                    "Origin": public_base,
                    "Cookie": f"overwatch_view={token}",
                },
                subprotocols=["binary"],
            ):
                pass
        assert exc_cross.value.code == 1008

        # 8. Preserved screenshot fallback on same display without conflict
        shot_resp = api_client.get(
            f"/api/v1/sessions/{session_id}/screenshot",
            headers=headers,
        )
        assert shot_resp.status_code == 200
        assert shot_resp.headers["content-type"].startswith("image/jpeg")
        assert shot_resp.headers.get("cache-control") == "no-store"
        assert shot_resp.content.startswith(b"\xff\xd8\xff")
    finally:
        app.dependency_overrides.clear()
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

"""Integration tests for the session-to-Squid egress path.

Opt-in: requires Docker available from the host and the ``pocket/py-node:1.0``
image built locally.  Skip gracefully with a clear reason if either is missing.

RUN:
    pytest tests/integration/test_session_egress.py -v

or, to include in the full suite:
    OVERWATCH_INTEGRATION=1 pytest tests/ -v

Design:
- The session manager (SessionContainerManager) is exercised from the host
  with a real Docker client injected.  No Docker socket is mounted into any
  container.
- A temporary Squid proxy is launched for each test module run (session-scoped
  fixture) with a temporary ACL directory that is not shared with the production
  proxy or any persistent volume.
- ACL file contents are written by EgressPolicyManager on the host and picked
  up by the proxy's entrypoint watcher.
- All containers, networks, volumes, and ACL files are removed in fixture
  teardown, even if the test body raises.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SQUID_IMAGE = "ubuntu/squid:5.2-22.04_beta"
SESSION_IMAGE = "pocket/py-node:1.0"

REPO_ROOT = Path(__file__).resolve().parents[2]
SQUID_CONF = REPO_ROOT / "infra" / "squid.conf"
SQUID_ENTRYPOINT = REPO_ROOT / "infra" / "entrypoint.sh"
PROFILES_DIR = REPO_ROOT / "profiles"

# Valid test session IDs: 's_' + 26-char uppercase Crockford Base32 ULID.
# First character 0–7 per CONTRACTS.md.  Excludes I, L, O, U per Crockford.
_TEST_SESSION_ID_A = "s_01ABCDEFGHJKMNPTVW12345678"
_TEST_SESSION_ID_B = "s_01ABCDEFGHJKMNPTVW98765432"

# How long (seconds) to wait for Squid's watcher to reload after policy write
SQUID_RELOAD_WAIT = 2.5

# Strict timeouts for HTTP requests executed inside the session container
CURL_CONNECT_TIMEOUT = 5
CURL_MAX_TIME = 12

# How long to wait for the Squid container to become ready after start
SQUID_STARTUP_WAIT = 3.0


# ---------------------------------------------------------------------------
# Opt-in guard
# ---------------------------------------------------------------------------
def _check_docker_and_images():
    """Return (client, skip_reason) – skip_reason is None when ready."""
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return None, f"Docker unavailable: {exc}"

    # Check both required images exist locally
    missing = []
    for tag in (SESSION_IMAGE, SQUID_IMAGE):
        try:
            client.images.get(tag)
        except Exception:
            missing.append(tag)

    if missing:
        return None, f"Required images not built locally: {', '.join(missing)}"

    return client, None


_docker_client, _skip_reason = _check_docker_and_images()

pytestmark = pytest.mark.skipif(
    _skip_reason is not None,
    reason=_skip_reason or "",
)


# ---------------------------------------------------------------------------
# Module-scoped temporary Squid proxy fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def squid_proxy():
    """Start a temporary Squid proxy for the integration test module.

    Yields:
        dict with keys:
            client          – docker.DockerClient
            proxy_name      – container name
            acl_path        – Path to the ACL file (host-side)
            tmpdir          – TemporaryDirectory (kept alive for scope duration)
    """

    client = _docker_client  # already validated

    proxy_name = f"ow-test-proxy-{uuid.uuid4().hex[:10]}"
    tmpdir = tempfile.TemporaryDirectory(prefix="ow_int_")
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

        # Wait for Squid startup
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            squid.reload()
            if squid.status == "running":
                logs = squid.logs().decode()
                if "Squid running with PID" in logs:
                    break
            time.sleep(0.3)
        else:
            raise RuntimeError(
                "Squid proxy did not become ready in 10 s. "
                f"Logs:\n{squid.logs().decode()}"
            )

        time.sleep(SQUID_STARTUP_WAIT)

        yield {
            "client": client,
            "proxy_name": proxy_name,
            "acl_path": acl_path,
            "tmpdir": tmpdir,
        }

    finally:
        if squid is not None:
            try:
                squid.remove(force=True)
            except Exception:
                pass
        tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Per-test session fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def session_manager(squid_proxy):
    """Create a SessionContainerManager bound to the temporary Squid proxy.

    Yields the manager; tears down any surviving sessions on exit.
    """
    from control_plane.sessions.container_manager import SessionContainerManager
    from control_plane.sessions.egress_policy import EgressPolicyManager

    client = squid_proxy["client"]
    acl_path = squid_proxy["acl_path"]
    proxy_name = squid_proxy["proxy_name"]

    policy_mgr = EgressPolicyManager(acl_path=acl_path)
    mgr = SessionContainerManager(
        docker_client=client,
        profiles_dir=PROFILES_DIR,
        egress_policy_manager=policy_mgr,
        proxy_container_name=proxy_name,
    )

    created_sessions: list[str] = []

    class _Wrapper:
        manager = mgr

        def create_session(self, session_id: str, profile: str = "coding"):
            info = mgr.create_session(session_id, profile)
            created_sessions.append(session_id)
            return info

        def teardown_session(self, session_id: str):
            try:
                mgr.teardown_session(session_id)
            finally:
                if session_id in created_sessions:
                    created_sessions.remove(session_id)

    wrapper = _Wrapper()
    try:
        yield wrapper
    finally:
        for sid in list(created_sessions):
            try:
                mgr.teardown_session(sid)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _curl_via_proxy(
    container, url: str, *, timeout: int = CURL_MAX_TIME
) -> tuple[int, str]:
    """Run curl inside a container via the egress proxy. Return (exit_code, output)."""
    cmd = [
        "curl",
        "-s",
        "-I",
        "--connect-timeout",
        str(CURL_CONNECT_TIMEOUT),
        "--max-time",
        str(timeout),
        "-x",
        "http://egress-proxy:3128",
        url,
    ]
    result = container.exec_run(cmd, user="1000:1000")
    return result.exit_code, result.output.decode(errors="replace")


def _curl_direct(container, url: str, *, timeout: int = 4) -> tuple[int, str]:
    """Run curl inside a container bypassing the proxy. Return (exit_code, output)."""
    cmd = [
        "curl",
        "-s",
        "--noproxy",
        "*",
        "--connect-timeout",
        "2",
        "--max-time",
        str(timeout),
        url,
    ]
    result = container.exec_run(cmd, user="1000:1000")
    return result.exit_code, result.output.decode(errors="replace")


def _wait_for_squid_reload(squid_proxy, session_id: str, timeout: float = 6.0):
    """Block until the proxy ACL file contains the session's ACL rule or timeout."""
    deadline = time.monotonic() + timeout
    proxy_name = squid_proxy["proxy_name"]
    client = squid_proxy["client"]
    squid = client.containers.get(proxy_name)

    while time.monotonic() < deadline:
        logs = squid.logs().decode()
        if (
            f"sess_{session_id}" in logs
            and "Squid reloaded successfully" in logs.split(f"sess_{session_id}")[-1]
        ):
            break
        # Also check: if the acl line is there the reload is done
        acl_path = squid_proxy["acl_path"]
        content = acl_path.read_text(encoding="utf-8")
        if session_id in content:
            # Give Squid a brief moment to process the reload
            time.sleep(0.5)
            break
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestSessionEgressIntegration:
    """Integration tests for the real session-to-Squid egress enforcement path."""

    def test_allowlisted_domain_succeeds_through_proxy(
        self, session_manager, squid_proxy
    ):
        """A request to a profile-allowlisted domain is ALLOWED through the proxy."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        _wait_for_squid_reload(squid_proxy, _TEST_SESSION_ID_A)

        container = client.containers.get(info.container_name)

        # pypi.org is in the coding profile's egress_allowlist
        exit_code, output = _curl_via_proxy(container, "https://pypi.org")

        # curl exit 0 means CONNECT tunnel established (HTTP 200) or HTTP 2xx
        assert exit_code == 0, (
            f"Expected curl to succeed for allowlisted domain pypi.org "
            f"(exit {exit_code}).\nOutput:\n{output}"
        )
        assert (
            "200" in output or "HTTP/1.1 200" in output
        ), f"Expected HTTP 200 for allowlisted domain. Got:\n{output}"

    def test_non_allowlisted_domain_denied_by_proxy(self, session_manager, squid_proxy):
        """A request to a domain NOT in the profile allowlist is DENIED by Squid."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        _wait_for_squid_reload(squid_proxy, _TEST_SESSION_ID_A)

        container = client.containers.get(info.container_name)

        # example.com is NOT in the coding profile's egress_allowlist
        exit_code, output = _curl_via_proxy(container, "http://example.com")

        assert "403" in output or "ERR_ACCESS_DENIED" in output, (
            f"Expected Squid to deny non-allowlisted domain example.com. "
            f"Got exit_code={exit_code}, output:\n{output}"
        )

    def test_direct_outbound_bypassing_proxy_fails(self, session_manager, squid_proxy):
        """Direct outbound (bypassing proxy) fails from the internal network."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)

        container = client.containers.get(info.container_name)

        # Attempt to reach Cloudflare DNS IP directly, skipping proxy.
        # The session network is internal=True, so direct external routes are
        # blocked by the Docker network driver.
        exit_code, output = _curl_direct(container, "http://1.1.1.1")

        # curl exit code 7 = failed to connect; non-zero means direct access blocked
        assert exit_code != 0, (
            f"Expected direct outbound request to fail on internal network "
            f"(exit {exit_code}).\nOutput:\n{output}"
        )

    def test_teardown_removes_container_network_volume_and_acl_entry(
        self, session_manager, squid_proxy
    ):
        """Teardown removes all Docker resources and clears the ACL entry."""
        import docker

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        _wait_for_squid_reload(squid_proxy, _TEST_SESSION_ID_A)

        c_name = info.container_name
        n_name = info.network_name
        v_name = info.volume_name
        session_id = info.session_id

        # Confirm resources exist before teardown
        client.containers.get(c_name)  # raises NotFound if missing
        client.networks.get(n_name)
        client.volumes.get(v_name)

        # Confirm ACL entry was written
        acl_content = squid_proxy["acl_path"].read_text(encoding="utf-8")
        assert session_id in acl_content, "Session ACL entry not found before teardown"

        # Tear down
        session_manager.teardown_session(session_id)
        # Give Squid watcher a moment to reload the now-empty ACL
        time.sleep(SQUID_RELOAD_WAIT)

        # Container must be gone
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(c_name)

        # Network must be gone
        with pytest.raises(docker.errors.NotFound):
            client.networks.get(n_name)

        # Volume must be gone
        with pytest.raises(docker.errors.NotFound):
            client.volumes.get(v_name)

        # ACL entry must be absent from the policy file
        acl_content_after = squid_proxy["acl_path"].read_text(encoding="utf-8")
        assert (
            session_id not in acl_content_after
        ), "Session ACL entry still present in ACL file after teardown"

    def test_session_acl_entry_is_scoped_to_session_ip(
        self, session_manager, squid_proxy
    ):
        """The ACL file has a source-IP ACL scoped to the session container's IP."""

        client = squid_proxy["client"]  # noqa: F841
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        _wait_for_squid_reload(squid_proxy, _TEST_SESSION_ID_A)

        acl_content = squid_proxy["acl_path"].read_text(encoding="utf-8")

        # Policy must reference this session's IP
        assert info.ip_address is not None, "Session IP address is None"
        assert (
            info.ip_address in acl_content
        ), f"Session IP {info.ip_address!r} not found in Squid ACL:\n{acl_content}"

        # Policy must reference all coding profile allowlist domains
        for domain in [
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
            "github.com",
        ]:
            assert (
                domain in acl_content
            ), f"Allowlisted domain {domain!r} not found in Squid ACL:\n{acl_content}"

        # Policy must include an explicit deny for the session's source IP (fail-closed)
        session_src_acl = f"sess_{info.session_id}_src"
        assert (
            f"http_access deny {session_src_acl}" in acl_content
        ), f"Explicit per-session deny not found in ACL:\n{acl_content}"

    def test_proxy_is_accessible_from_session_container(
        self, session_manager, squid_proxy
    ):
        """The egress-proxy alias resolves inside the session container."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)

        container = client.containers.get(info.container_name)

        # Use getent to resolve the alias—should return an IP, not fail
        result = container.exec_run(
            ["getent", "hosts", "egress-proxy"],
            user="1000:1000",
        )
        output = result.output.decode(errors="replace").strip()
        assert result.exit_code == 0 and output, (
            f"'egress-proxy' alias not resolvable inside session container. "
            f"Exit: {result.exit_code}, output: {output!r}"
        )

    def test_session_container_uses_proxy_env_vars(self, session_manager, squid_proxy):
        """HTTP_PROXY and HTTPS_PROXY point to egress-proxy:3128 in the container."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        container = client.containers.get(info.container_name)

        result = container.exec_run(["env"], user="1000:1000")
        env_output = result.output.decode(errors="replace")

        assert (
            "HTTP_PROXY=http://egress-proxy:3128" in env_output
        ), f"HTTP_PROXY not configured. env:\n{env_output}"
        assert (
            "HTTPS_PROXY=http://egress-proxy:3128" in env_output
        ), f"HTTPS_PROXY not configured. env:\n{env_output}"

    def test_session_container_runs_as_non_root(self, session_manager, squid_proxy):
        """The session container runs as UID 1000 (non-root)."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        container = client.containers.get(info.container_name)

        result = container.exec_run(["id", "-u"])
        uid = result.output.decode(errors="replace").strip()
        assert uid == "1000", f"Expected UID 1000, got {uid!r}"

    def test_no_docker_socket_inside_session_container(
        self, session_manager, squid_proxy
    ):
        """The Docker socket is never mounted inside the session container."""

        client = squid_proxy["client"]
        info = session_manager.create_session(_TEST_SESSION_ID_A)
        container = client.containers.get(info.container_name)

        result = container.exec_run(["ls", "/var/run/docker.sock"])
        assert (
            result.exit_code != 0
        ), "Docker socket /var/run/docker.sock is accessible inside session container!"

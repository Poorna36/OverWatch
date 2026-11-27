"""Focused unit tests for the Docker client factory.

Tests control_plane.sessions.docker_client.

Covers:
- Module-level import does NOT connect to Docker.
- Injected client is returned as-is (no extra validation).
- Host-local defaults: unix:// and absent DOCKER_HOST both succeed.
- Valid TLS configuration (tcp:// + DOCKER_TLS_VERIFY=1 + DOCKER_CERT_PATH).
- Rejection of plain tcp:// without TLS.
- Rejection of tcp:// + TLS but missing DOCKER_CERT_PATH.
- Rejection of tcp:// + TLS + DOCKER_CERT_PATH but incomplete cert files.
- Rejection of unsupported schemes.
- Docker daemon ping failure raises DockerClientConfigError.
- Lazy singleton: get_default_docker_client creates once, caches, and resets.
- SessionContainerManager.client uses injected factory correctly.
- SessionContainerManager.client caches after first creation.
- SessionContainerManager.client wraps DockerClientConfigError as DockerResourceError.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import the module under test -- this must NOT connect to Docker.
# Any connection at import time would cause this module-level statement to fail
# in CI environments without Docker.
# ---------------------------------------------------------------------------
import control_plane.sessions.docker_client as dc_module
import docker
import docker.errors
import pytest
from control_plane.sessions.container_manager import (
    DockerResourceError,
    SessionContainerManager,
)
from control_plane.sessions.docker_client import (
    DockerClientConfigError,
    _require_tls_cert_path,
    create_docker_client,
    get_default_docker_client,
    reset_default_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cert_dir(tmp_path: Path, *, missing: list[str] | None = None) -> Path:
    """Create a temporary TLS cert directory with the required files."""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    for fname in ("ca.pem", "cert.pem", "key.pem"):
        if missing and fname in missing:
            continue
        (cert_dir / fname).write_text(f"placeholder {fname}")
    return cert_dir


# ---------------------------------------------------------------------------
# 1. No connection at import time
# ---------------------------------------------------------------------------


def test_import_does_not_connect():
    """Importing docker_client must not attempt a Docker connection."""
    # If the import above succeeded without raising, this test passes.
    # We also verify no live client is cached on the module.
    assert dc_module._default_client is None


# ---------------------------------------------------------------------------
# 2. Injected client passthrough
# ---------------------------------------------------------------------------


def test_injected_client_returned_directly():
    """create_docker_client is not called when a client is explicitly injected."""
    mock_client = MagicMock(spec=docker.DockerClient)
    mgr = SessionContainerManager(docker_client=mock_client)
    assert mgr.client is mock_client


# ---------------------------------------------------------------------------
# 3. Host-local defaults (unix socket / absent DOCKER_HOST)
# ---------------------------------------------------------------------------


def test_host_local_absent_docker_host(monkeypatch):
    """When DOCKER_HOST is not set, docker.from_env() is used (local socket path)."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    mock_client = MagicMock(spec=docker.DockerClient)

    with patch("docker.from_env", return_value=mock_client) as from_env_mock:
        client = create_docker_client(ping=False)

    from_env_mock.assert_called_once()
    assert client is mock_client


def test_host_local_unix_socket(monkeypatch):
    """Explicit unix:// DOCKER_HOST uses docker.from_env() (local socket path)."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    mock_client = MagicMock(spec=docker.DockerClient)

    with patch("docker.from_env", return_value=mock_client) as from_env_mock:
        client = create_docker_client(ping=False)

    from_env_mock.assert_called_once()
    assert client is mock_client


# ---------------------------------------------------------------------------
# 4. Valid TLS configuration
# ---------------------------------------------------------------------------


def test_valid_tls_configuration(tmp_path, monkeypatch):
    """tcp:// + TLS_VERIFY=1 + valid DOCKER_CERT_PATH succeeds."""
    cert_dir = _make_cert_dir(tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(cert_dir))

    mock_client = MagicMock(spec=docker.DockerClient)

    with patch("docker.DockerClient", return_value=mock_client):
        client = create_docker_client(ping=False)

    assert client is mock_client


def test_valid_tls_explicit_params(tmp_path, monkeypatch):
    """Explicit parameter overrides work alongside env vars."""
    cert_dir = _make_cert_dir(tmp_path)
    # Clear env to ensure explicit params are used
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    mock_client = MagicMock(spec=docker.DockerClient)

    with patch("docker.DockerClient", return_value=mock_client):
        client = create_docker_client(
            docker_host="tcp://10.0.0.2:2376",
            tls_verify=True,
            cert_path=str(cert_dir),
            ping=False,
        )

    assert client is mock_client


# ---------------------------------------------------------------------------
# 5. Rejection: plain tcp:// without TLS
# ---------------------------------------------------------------------------


def test_reject_plain_tcp_no_tls(monkeypatch):
    """Plain tcp:// endpoint without DOCKER_TLS_VERIFY=1 must raise."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2376")
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    with pytest.raises(DockerClientConfigError, match="Insecure Docker endpoint"):
        create_docker_client(ping=False)


def test_reject_plain_tcp_tls_verify_zero(monkeypatch):
    """tcp:// with DOCKER_TLS_VERIFY=0 must raise."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "0")
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    with pytest.raises(DockerClientConfigError, match="Insecure Docker endpoint"):
        create_docker_client(ping=False)


# ---------------------------------------------------------------------------
# 6. Rejection: tcp:// + TLS but missing DOCKER_CERT_PATH
# ---------------------------------------------------------------------------


def test_reject_tcp_tls_no_cert_path(monkeypatch):
    """tcp:// + DOCKER_TLS_VERIFY=1 but no DOCKER_CERT_PATH must raise."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.delenv("DOCKER_CERT_PATH", raising=False)

    with pytest.raises(DockerClientConfigError, match="DOCKER_CERT_PATH"):
        create_docker_client(ping=False)


# ---------------------------------------------------------------------------
# 7. Rejection: incomplete TLS cert files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_file", ["ca.pem", "cert.pem", "key.pem"])
def test_reject_incomplete_cert_dir(tmp_path, monkeypatch, missing_file):
    """Missing any one of the three TLS files must raise DockerClientConfigError."""
    cert_dir = _make_cert_dir(tmp_path, missing=[missing_file])
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(cert_dir))

    with pytest.raises(DockerClientConfigError, match=missing_file):
        create_docker_client(ping=False)


def test_require_tls_cert_path_missing_all(tmp_path):
    """_require_tls_cert_path with empty dir lists all missing files."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(DockerClientConfigError) as exc_info:
        _require_tls_cert_path(str(empty_dir))

    msg = str(exc_info.value)
    assert "ca.pem" in msg
    assert "cert.pem" in msg
    assert "key.pem" in msg


# ---------------------------------------------------------------------------
# 8. Rejection: unsupported scheme
# ---------------------------------------------------------------------------


def test_reject_unsupported_scheme(monkeypatch):
    """ssh:// and other unsupported schemes must raise."""
    monkeypatch.setenv("DOCKER_HOST", "ssh://user@10.0.0.1")
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)

    with pytest.raises(
        DockerClientConfigError, match="Unsupported Docker endpoint scheme"
    ):
        create_docker_client(ping=False)


# ---------------------------------------------------------------------------
# 9. Ping failure raises DockerClientConfigError
# ---------------------------------------------------------------------------


def test_ping_failure_raises(monkeypatch):
    """When ping=True and daemon is unavailable, DockerClientConfigError is raised."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)

    mock_client = MagicMock(spec=docker.DockerClient)
    mock_client.ping.side_effect = docker.errors.DockerException("connection refused")

    with patch("docker.from_env", return_value=mock_client):
        with pytest.raises(
            DockerClientConfigError, match="Docker daemon is unavailable"
        ):
            create_docker_client(ping=True)


# ---------------------------------------------------------------------------
# 10. Lazy singleton: get_default_docker_client
# ---------------------------------------------------------------------------


def test_get_default_docker_client_creates_once(monkeypatch):
    """get_default_docker_client returns the same object on subsequent calls."""
    reset_default_client()
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)

    mock_client = MagicMock(spec=docker.DockerClient)
    call_count = 0

    def fake_from_env(**kwargs):
        nonlocal call_count
        call_count += 1
        return mock_client

    with patch("docker.from_env", side_effect=fake_from_env):
        c1 = get_default_docker_client()
        c2 = get_default_docker_client()

    assert c1 is c2
    assert call_count == 1


def test_reset_default_client_clears_cache(monkeypatch):
    """reset_default_client() causes the next call to create a fresh client."""
    reset_default_client()
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)

    mock_client_1 = MagicMock(spec=docker.DockerClient)
    mock_client_2 = MagicMock(spec=docker.DockerClient)
    clients = [mock_client_1, mock_client_2]

    def factory(**kwargs):
        return clients.pop(0)

    with patch("docker.from_env", side_effect=factory):
        c1 = get_default_docker_client()
        reset_default_client()
        c2 = get_default_docker_client()

    assert c1 is mock_client_1
    assert c2 is mock_client_2
    reset_default_client()


# ---------------------------------------------------------------------------
# 11. SessionContainerManager: injected factory
# ---------------------------------------------------------------------------


def test_manager_uses_injected_factory():
    """SessionContainerManager calls docker_client_factory on first .client access."""
    mock_client = MagicMock(spec=docker.DockerClient)
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return mock_client

    mgr = SessionContainerManager(docker_client_factory=factory)
    assert mgr._client is None  # not yet created

    result = mgr.client
    assert result is mock_client
    assert len(factory_calls) == 1


def test_manager_caches_client_after_first_access():
    """Subsequent accesses to .client do not call the factory again."""
    mock_client = MagicMock(spec=docker.DockerClient)
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return mock_client

    mgr = SessionContainerManager(docker_client_factory=factory)
    _ = mgr.client
    _ = mgr.client
    _ = mgr.client

    assert len(factory_calls) == 1


# ---------------------------------------------------------------------------
# 12. SessionContainerManager wraps DockerClientConfigError
# ---------------------------------------------------------------------------


def test_manager_wraps_config_error_as_docker_resource_error():
    """DockerClientConfigError from factory is wrapped as DockerResourceError."""

    def bad_factory():
        raise DockerClientConfigError("TLS not configured")

    mgr = SessionContainerManager(docker_client_factory=bad_factory)

    with pytest.raises(DockerResourceError, match="Failed to initialise Docker client"):
        _ = mgr.client


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level singleton is reset before and after each test."""
    reset_default_client()
    yield
    reset_default_client()

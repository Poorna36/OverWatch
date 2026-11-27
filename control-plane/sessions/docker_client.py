"""Lazy, injectable Docker client factory (Phase 1, F-01).

Creates a ``docker.DockerClient`` on first use, never at import time, so that
unit tests that do not need Docker can import any module in this package without
hitting the Docker daemon.

Supported connection modes
--------------------------
1. **Host-local development** (no ``DOCKER_HOST`` set, or ``DOCKER_HOST`` uses
   a Unix socket ``unix://...``):
   The Docker SDK's normal environment configuration is used (reads
   ``DOCKER_HOST``, ``DOCKER_CERT_PATH``, ``DOCKER_TLS_VERIFY``, etc.).  This
   works out of the box when running the control plane directly on the Docker host
   during development without any additional environment variables.

2. **Remote TCP with TLS** (``DOCKER_HOST=tcp://...`` and TLS env vars set):
   Full mutual-TLS verification is required.  All three of the following
   environment variables must be set, must point to readable files, and the
   ``DOCKER_TLS_VERIFY`` flag must be enabled:

   - ``DOCKER_HOST``       – remote endpoint, e.g. ``tcp://10.0.0.1:2376``
   - ``DOCKER_TLS_VERIFY`` – must be ``"1"``
   - ``DOCKER_CERT_PATH``  – directory containing ``ca.pem``, ``cert.pem``,
                             ``key.pem``

   The factory **fails closed** (raises ``DockerClientConfigError``) if:
   - The endpoint is a plain ``tcp://`` URL without ``DOCKER_TLS_VERIFY=1``.
   - Any required TLS file is missing or unreadable.
   - The Docker daemon is unavailable or returns an error on the first ping.

Security contract
-----------------
- Never connects at import time.
- Never falls back silently to an insecure endpoint.
- Never mounts ``/var/run/docker.sock`` into session containers; that is a
  separate invariant enforced by ``SessionContainerManager``.
- Caller-supplied ``docker_client`` kwargs (for TLS) are validated before use.
- Credentials are never stored in Docker labels, environment variables passed to
  managed containers, or committed files.

Compose note
------------
The ``docker-compose.yml`` Compose service does **not** mount the Docker socket,
so Compose-based session creation requires a separately configured authenticated
Docker endpoint (``DOCKER_HOST`` + TLS).  See the README and ``.env.example``
for the required environment variables.

Environment variables
---------------------
``DOCKER_HOST``
    Docker daemon endpoint.  Omit for host-local socket.
    For remote endpoints, use ``tcp://host:2376`` (TLS required).

``DOCKER_TLS_VERIFY``
    Set to ``"1"`` to enable TLS verification for TCP endpoints.
    Required when ``DOCKER_HOST`` is a ``tcp://`` URL.

``DOCKER_CERT_PATH``
    Path to a directory containing ``ca.pem``, ``cert.pem``, and ``key.pem``.
    Required when ``DOCKER_TLS_VERIFY=1``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import docker
import docker.errors
import docker.tls

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DockerClientConfigError(Exception):
    """Raised when the Docker client cannot be safely configured.

    This exception is *always* fatal: the daemon connection is refused rather
    than silently falling back to an insecure configuration.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TCP_SCHEME = "tcp://"
_UNIX_SCHEME = "unix://"
_NPIPE_SCHEME = "npipe://"

_TLS_FILES = ("ca.pem", "cert.pem", "key.pem")


def _is_tcp_host(host: str) -> bool:
    return host.strip().lower().startswith(_TCP_SCHEME)


def _is_local_socket(host: str | None) -> bool:
    """Return True when *host* is a Unix or Windows Named Pipe socket."""
    if host is None:
        return True  # SDK default: local socket
    stripped = host.strip().lower()
    return stripped.startswith(_UNIX_SCHEME) or stripped.startswith(_NPIPE_SCHEME)


def _require_tls_cert_path(cert_path: str) -> docker.tls.TLSConfig:
    """Build a TLSConfig from *cert_path*, failing closed if any file is unusable."""
    cert_dir = Path(cert_path)
    missing: list[str] = []
    unreadable: list[str] = []
    empty: list[str] = []

    for fname in _TLS_FILES:
        fpath = cert_dir / fname
        if not fpath.is_file():
            missing.append(fname)
            continue
        try:
            with fpath.open("rb") as fh:
                if not fh.read(1):
                    empty.append(fname)
        except OSError:
            unreadable.append(fname)

    problems: list[str] = []
    if missing:
        problems.append("missing: " + ", ".join(missing))
    if unreadable:
        problems.append("unreadable: " + ", ".join(unreadable))
    if empty:
        problems.append("empty: " + ", ".join(empty))

    if problems:
        raise DockerClientConfigError(
            f"DOCKER_CERT_PATH={cert_path!r} failed TLS validation ("
            + "; ".join(problems)
            + "). All three of ca.pem, cert.pem, and key.pem must exist, "
            "be readable, and be non-empty."
        )

    return docker.tls.TLSConfig(
        client_cert=(str(cert_dir / "cert.pem"), str(cert_dir / "key.pem")),
        ca_cert=str(cert_dir / "ca.pem"),
        verify=True,
    )


def _ping(client: docker.DockerClient) -> None:
    """Ping the daemon, translating connection errors into DockerClientConfigError."""
    try:
        client.ping()
    except docker.errors.DockerException as exc:
        raise DockerClientConfigError(
            f"Docker daemon is unavailable or returned an error: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise DockerClientConfigError(
            f"Unexpected error while contacting Docker daemon: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_docker_client(
    *,
    docker_host: str | None = None,
    tls_verify: bool | None = None,
    cert_path: str | None = None,
    ping: bool = True,
    **extra_kwargs: Any,
) -> docker.DockerClient:
    """Create and return a configured ``docker.DockerClient``.

    Parameters
    ----------
    docker_host:
        Override for ``DOCKER_HOST``.  Reads the environment variable when
        *None*.
    tls_verify:
        Override for ``DOCKER_TLS_VERIFY``.  Reads the environment variable
        when *None*.
    cert_path:
        Override for ``DOCKER_CERT_PATH``.  Reads the environment variable
        when *None*.
    ping:
        When ``True`` (default), call :py:meth:`docker.DockerClient.ping` to
        verify the daemon is reachable before returning.  Set to ``False`` only
        in tests that inject a mock client.
    **extra_kwargs:
        Forwarded verbatim to :class:`docker.DockerClient`.

    Returns
    -------
    docker.DockerClient
        A connected, authenticated client.

    Raises
    ------
    DockerClientConfigError
        If the endpoint is insecure, TLS configuration is incomplete, or the
        daemon is unavailable.
    """
    # Resolve configuration from env or explicit overrides
    resolved_host: str | None = docker_host or os.environ.get("DOCKER_HOST") or None

    # Resolve tls_verify: explicit param > env var > default False
    if tls_verify is None:
        env_verify = os.environ.get("DOCKER_TLS_VERIFY", "")
        tls_verify = env_verify.strip().lower() in ("1", "true", "yes")

    resolved_cert: str | None = cert_path or os.environ.get("DOCKER_CERT_PATH") or None

    # --- Security gate: reject plain TCP without TLS ---
    if resolved_host is not None and _is_tcp_host(resolved_host):
        if not tls_verify:
            raise DockerClientConfigError(
                f"Insecure Docker endpoint rejected: DOCKER_HOST={resolved_host!r} "
                "is a TCP endpoint but DOCKER_TLS_VERIFY is not set to '1'. "
                "Plain TCP endpoints are not permitted. "
                "Use a Unix socket for local development, or enable TLS for remote "
                "endpoints (DOCKER_TLS_VERIFY=1 and DOCKER_CERT_PATH=<dir>)."
            )

        if not resolved_cert:
            raise DockerClientConfigError(
                "DOCKER_HOST is a TCP endpoint and DOCKER_TLS_VERIFY=1, but "
                "DOCKER_CERT_PATH is not set. "
                "Provide the path to a directory containing ca.pem, cert.pem, "
                "and key.pem."
            )

        tls_config = _require_tls_cert_path(resolved_cert)
        try:
            client = docker.DockerClient(
                base_url=resolved_host,
                tls=tls_config,
                **extra_kwargs,
            )
        except docker.errors.DockerException as exc:
            raise DockerClientConfigError(
                f"Failed to create Docker client for {resolved_host!r}: {exc}"
            ) from exc

    elif _is_local_socket(resolved_host):
        # Refuse to silently bypass TLS verification when DOCKER_TLS_VERIFY=1.
        if tls_verify:
            raise DockerClientConfigError(
                "DOCKER_TLS_VERIFY is enabled, but DOCKER_HOST is not set to a "
                "tcp:// TLS endpoint. Set DOCKER_HOST=tcp://<host>:2376 and "
                "DOCKER_CERT_PATH=<dir>, or unset DOCKER_TLS_VERIFY for local "
                "host socket development."
            )

        # Host-local socket: use SDK default environment resolution.
        # DOCKER_HOST may be a unix:// or npipe:// URL, or absent (SDK default).
        try:
            client = docker.from_env(**extra_kwargs)
        except docker.errors.DockerException as exc:
            raise DockerClientConfigError(
                f"Failed to initialise Docker client from environment: {exc}"
            ) from exc

    else:
        # Unknown/unsupported scheme (e.g. ssh://) – fail closed.
        raise DockerClientConfigError(
            f"Unsupported Docker endpoint scheme in DOCKER_HOST={resolved_host!r}. "
            "Supported: Unix socket (default), npipe://, or tcp:// with TLS."
        )

    if ping:
        _ping(client)

    return client


# ---------------------------------------------------------------------------
# Module-level lazy singleton (used by default when no client is injected)
# ---------------------------------------------------------------------------

_default_client: docker.DockerClient | None = None
_default_client_error: DockerClientConfigError | None = None


def get_default_docker_client() -> docker.DockerClient:
    """Return the process-wide default Docker client, creating it on first call.

    Subsequent calls return the same client object.  The first call reads the
    current environment (``DOCKER_HOST``, ``DOCKER_TLS_VERIFY``,
    ``DOCKER_CERT_PATH``) and fails closed on insecure or misconfigured
    endpoints.

    Raises
    ------
    DockerClientConfigError
        On first call if the environment is not properly configured.
    """
    global _default_client, _default_client_error  # noqa: PLW0603

    if _default_client_error is not None:
        raise _default_client_error

    if _default_client is not None:
        return _default_client

    try:
        _default_client = create_docker_client()
    except DockerClientConfigError as exc:
        _default_client_error = exc
        raise

    return _default_client


def reset_default_client() -> None:
    """Reset the process-wide default client (for use in tests only).

    After calling this function the next call to :func:`get_default_docker_client`
    will re-read the environment and create a fresh client.
    """
    global _default_client, _default_client_error  # noqa: PLW0603
    _default_client = None
    _default_client_error = None

"""Session container lifecycle manager (Phase 1, F-01, F-08).

Creates and destroys hardened per-session Docker containers, internal networks,
and workspace volumes following docs/SECURITY.md, docs/ARCHITECTURE.md, and
docs/BUILD_PLAN.md.

Security Invariants:
1. Hardening (docs/SECURITY.md Section 2):
   - User: 1000:1000 (non-root UID/GID).
   - Root filesystem: read-only (read_only=True).
   - /tmp: tmpfs limited to 256m (rw,size=256m).
   - Linux capabilities: all dropped (cap_drop=["ALL"]).
   - Privilege escalation: disabled (security_opt=["no-new-privileges:true"]).
   - Resource limits: CPU, memory, and PID limits clamped to the largest limits
     explicitly used by shipped profiles (coding.yaml).
2. Isolation & Isolation Boundaries:
   - Dedicated workspace volume: ws_{session_id} mounted at /workspace:rw.
   - Dedicated internal network: sess_net_{session_id} with internal=True and
     dedicated non-overlapping subnets.
   - Non-overlapping IPAM subnets: Each session receives a unique subnet so that
     the shared Squid egress proxy can distinguish containers by unique source IP,
     preventing cross-application of allowlists.
   - Ports: never published (ports=None).
   - Host paths: never mounted (only the named workspace volume).
   - Privileged mode: never enabled (privileged=False).
   - Docker socket: never exposed (/var/run/docker.sock is never mounted).
3. Image Safety & Profile Trust:
   - Uses only the pinned image named by the validated profile.
   - Rejects unpinned tags (:latest or missing tag).
   - Never pulls images automatically. Fails closed if the image is missing locally.
   - Profiles must be loaded from trusted on-disk YAML configuration via ProfileLoader.
     Any in-memory Profile object must match the on-disk trusted profile exactly.
4. Secrets Safety:
   - Never passes secrets, tokens, or credentials into container environment,
     labels, or volume mounts.
5. Fail Closed:
   - Validates session IDs: must strictly match contract 's_' followed by a valid
     26-character Crockford Base32 ULID.
   - Rejects invalid IDs, unknown/untrusted profiles, or Docker errors.
   - Fails closed on duplicate source IPs across active sessions.
   - Handles partial creation failures safely by rolling back any allocated
     Docker resources (volumes, networks, containers), releasing allocated subnets,
     and unregistering egress policies.
6. Teardown Guarantees:
   - Idempotently removes container, network, and workspace volume.
   - Unregisters session policy from EgressPolicyManager and releases subnet.

Proxy & Egress Integration:
Per-session networks are created with internal=True (no direct external egress).
The running egress proxy container ('overwatch-egress-proxy') is connected to each
session network with alias 'egress-proxy', and the session container's HTTP_PROXY
and HTTPS_PROXY environment variables point to 'http://egress-proxy:3128'.
The session allowlist is registered with EgressPolicyManager before the container
is started, ensuring the proxy's default-deny policy remains effective if
registration or reload fails.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
import docker.constants
import docker.errors
import docker.types
from control_plane.contracts import ErrorCode, Profile
from control_plane.sessions.docker_client import (
    DockerClientConfigError,
    get_default_docker_client,
)
from control_plane.sessions.egress_policy import EgressPolicyManager
from control_plane.sessions.profile_loader import (
    ProfileLoader,
    ProfileLoaderError,
)

# ---------------------------------------------------------------------------
# Constants & Ceilings (from shipped profiles: coding.yaml, general.yaml)
# ---------------------------------------------------------------------------
MAX_CPUS: float = 2.0
MAX_MEMORY_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GiB ("2g")
MAX_PIDS: int = 512

MIN_CPUS: float = 0.1
MIN_MEMORY_BYTES: int = 64 * 1024 * 1024  # 64 MiB
MIN_PIDS: int = 16

# Contract session ID format (docs/CONTRACTS.md Section 1):
# 's_' prefix followed by a valid 26-character Crockford Base32 ULID.
# First character must be in range 0-7 to enforce 128-bit ULID limits.
SESSION_ID_PREFIX = "s_"
ULID_LENGTH = 26
ULID_REGEX = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# Characters forbidden in session IDs to prevent traversal, injection, or shell issues
FORBIDDEN_ID_CHARS = {"\n", "\r", "\t", "\x00", " ", ";", ":", "/", "\\", "@", "!"}

# Egress proxy integration constants (Phase 1, F-09, docs/SECURITY.md Section 2)
DEFAULT_PROXY_CONTAINER_NAME = "overwatch-egress-proxy"
DEFAULT_PROXY_NETWORK_ALIAS = "egress-proxy"
DEFAULT_PROXY_PORT = 3128
DEFAULT_PROXY_URL = f"http://{DEFAULT_PROXY_NETWORK_ALIAS}:{DEFAULT_PROXY_PORT}"

# Deterministic IPAM host octets within each /24 session subnet
GATEWAY_HOST_OCTET = 1
PROXY_HOST_OCTET = 2
SESSION_HOST_OCTET = 3


# ---------------------------------------------------------------------------
# Exception Hierarchy
# ---------------------------------------------------------------------------
class ContainerManagerError(Exception):
    """Base exception for container management operations."""

    code: str = ErrorCode.FATAL


class InvalidSessionIdError(ContainerManagerError):
    """Raised when a session ID fails validation."""

    code: str = ErrorCode.ARG_INVALID


class InvalidProfileError(ContainerManagerError):
    """Raised when a profile is invalid, missing, or has forbidden attributes."""

    code: str = ErrorCode.NOT_IN_PROFILE


class DockerResourceError(ContainerManagerError):
    """Raised when Docker API operations fail."""

    code: str = ErrorCode.UPSTREAM_ERROR


class DockerImageNotFoundError(DockerResourceError):
    """Raised when the requested profile image is not found locally."""

    code: str = ErrorCode.NOT_IN_PROFILE


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionContainerInfo:
    """Descriptor for an instantiated session container and its Docker resources."""

    session_id: str
    container_id: str
    container_name: str
    network_id: str
    network_name: str
    volume_name: str
    image: str
    profile_name: str
    ip_address: str | None = None
    subnet: str | None = None
    workspace_path: str = "/workspace"
    proxy_ip: str | None = None


# ---------------------------------------------------------------------------
# Subnet Allocator for Unique Source IPs
# ---------------------------------------------------------------------------
class SubnetPool:
    """Allocates and tracks non-overlapping subnets for active session networks.

    Ensures every active session container receives a unique source IP on a
    distinct subnet, allowing the shared Squid proxy to distinguish sessions
    and prevent cross-application of allowlists.

    Supports restart-safe reconciliation with existing Docker networks labeled
    'overwatch.managed=true', reserving active subnets and preventing collisions.
    """

    def __init__(self, base_prefix: str = "172.30", max_subnets: int = 254) -> None:
        self.base_prefix = base_prefix
        self.max_subnets = max_subnets
        self._allocated: dict[str, int] = {}  # session_id -> subnet index
        self._available: list[int] = list(range(1, max_subnets + 1))

    def reconcile(self, client: docker.DockerClient) -> None:
        """Reconcile subnet pool with existing managed Docker networks.

        Queries Docker for networks labeled 'overwatch.managed=true', validates each
        network's session ID and subnet CIDR, and reserves all active subnets so
        they are never reassigned to new sessions across daemon/manager restarts.

        Fails closed with DockerResourceError if:
        - Docker API fails to list networks
        - Network metadata is missing, malformed, or has invalid session ID
        - Network name does not match expected canonical sess_net_{session_id}
        - IPAM subnet is missing, malformed, or out of range [1, max_subnets]
        - Label subnet conflicts with IPAM subnet
        - Duplicate session IDs or duplicate subnets exist across Docker networks
        - Existing Docker network conflicts with current in-memory allocation

        Repeatable and idempotent when called multiple times.
        """
        try:
            networks = client.networks.list(filters={"label": "overwatch.managed=true"})
        except Exception as exc:
            raise DockerResourceError(
                "Failed to query existing Docker networks for subnet "
                f"reconciliation: {exc}"
            ) from exc

        docker_allocations: dict[str, int] = {}
        used_indices: dict[int, str] = {}  # subnet_idx -> session_id

        for net in networks:
            net_name = getattr(net, "name", None) or "unknown"

            # 1. Extract Labels
            labels: dict[str, Any] | None = None
            if hasattr(net, "attrs") and isinstance(net.attrs, dict):
                labels = net.attrs.get("Labels") or net.attrs.get("labels")
            if labels is None:
                labels = getattr(net, "labels", None)

            if not isinstance(labels, dict):
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' is missing Labels metadata."
                )

            # 2. Validate Session ID
            raw_session_id = labels.get("overwatch.session_id")
            if not raw_session_id or not isinstance(raw_session_id, str):
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' is missing required "
                    "'overwatch.session_id' label."
                )

            try:
                valid_id = validate_session_id(raw_session_id)
            except InvalidSessionIdError as exc:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' has invalid session ID "
                    f"'{raw_session_id}': {exc}"
                ) from exc

            # 3. Validate Canonical Network Name
            expected_name = session_network_name(valid_id)
            if net_name != expected_name:
                raise DockerResourceError(
                    f"Managed Docker network name '{net_name}' does not match expected "
                    f"canonical name '{expected_name}' for session '{valid_id}'."
                )

            # 4. Extract IPAM Subnet
            ipam_subnet: str | None = None
            ipam: Any = None
            if hasattr(net, "attrs") and isinstance(net.attrs, dict):
                ipam = net.attrs.get("IPAM") or net.attrs.get("ipam")
            if ipam is None:
                ipam = getattr(net, "ipam", None)

            if isinstance(ipam, dict):
                config = ipam.get("Config") or ipam.get("config")
                if (
                    isinstance(config, list)
                    and len(config) > 0
                    and isinstance(config[0], dict)
                ):
                    ipam_subnet = config[0].get("Subnet") or config[0].get("subnet")
            elif hasattr(ipam, "pool_configs") and getattr(ipam, "pool_configs"):
                first_pool = getattr(ipam, "pool_configs")[0]
                ipam_subnet = getattr(first_pool, "subnet", None)

            if not ipam_subnet or not isinstance(ipam_subnet, str):
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' is missing IPAM "
                    "subnet configuration."
                )

            # 5. Check Label Subnet Match (if label present)
            label_subnet = labels.get("overwatch.subnet")
            if label_subnet and str(label_subnet).strip() != str(ipam_subnet).strip():
                raise DockerResourceError(
                    f"Subnet mismatch in network '{net_name}': label "
                    f"'{label_subnet}' != IPAM '{ipam_subnet}'."
                )

            # 6. Validate Subnet Format and Range
            try:
                net_obj = ipaddress.ip_network(ipam_subnet.strip(), strict=True)
            except (ValueError, TypeError) as exc:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' has invalid IPAM subnet "
                    f"CIDR '{ipam_subnet}': {exc}"
                ) from exc

            if net_obj.prefixlen != 24:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' subnet '{ipam_subnet}' "
                    "is not a /24 network."
                )

            octets = str(net_obj.network_address).split(".")
            if len(octets) != 4:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' subnet '{ipam_subnet}' "
                    "is not an IPv4 network."
                )

            prefix = f"{octets[0]}.{octets[1]}"
            if prefix != self.base_prefix:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' subnet '{ipam_subnet}' "
                    f"prefix does not match allocator prefix '{self.base_prefix}'."
                )

            try:
                idx = int(octets[2])
            except ValueError as exc:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' subnet index '{octets[2]}' "
                    "is not an integer."
                ) from exc

            if idx < 1 or idx > self.max_subnets:
                raise DockerResourceError(
                    f"Managed Docker network '{net_name}' subnet index {idx} "
                    f"out of range [1, {self.max_subnets}]."
                )

            # 7. Check for Duplicate Session IDs or Subnets in Docker Networks
            if valid_id in docker_allocations:
                raise DockerResourceError(
                    f"Duplicate session ID '{valid_id}' found across managed "
                    "Docker networks."
                )
            if idx in used_indices:
                raise DockerResourceError(
                    f"Duplicate subnet index {idx} ('{ipam_subnet}') found "
                    f"across managed Docker networks (claimed by "
                    f"'{used_indices[idx]}' and '{valid_id}')."
                )

            docker_allocations[valid_id] = idx
            used_indices[idx] = valid_id

        # 8. Check for Conflicts with Current In-Memory State
        for s_id, s_idx in docker_allocations.items():
            if s_id in self._allocated and self._allocated[s_id] != s_idx:
                raise DockerResourceError(
                    f"Session '{s_id}' subnet index mismatch: allocator has "
                    f"{self._allocated[s_id]}, Docker network has {s_idx}."
                )
            for existing_id, existing_idx in self._allocated.items():
                if existing_id != s_id and existing_idx == s_idx:
                    raise DockerResourceError(
                        f"Subnet index {s_idx} conflict: assigned to '{s_id}' "
                        f"in Docker but held by '{existing_id}' in allocator."
                    )

        # 9. Reserve Discovered Allocations & Recompute Available Subnets
        for s_id, s_idx in docker_allocations.items():
            self._allocated[s_id] = s_idx

        allocated_indices = set(self._allocated.values())
        self._available = [
            i for i in range(1, self.max_subnets + 1) if i not in allocated_indices
        ]

    def allocate(self, session_id: str) -> tuple[str, str, str, str]:
        """Allocate a non-overlapping subnet and deterministic endpoint IPs.

        Host assignments within ``<base_prefix>.<idx>.0/24``:
        - ``.1``: network gateway
        - ``.2``: egress proxy container endpoint
        - ``.3``: session container endpoint

        Returns:
            (subnet_cidr, gateway_ip, proxy_ip, session_ip)
            e.g. ("172.30.1.0/24", "172.30.1.1", "172.30.1.2", "172.30.1.3")
        """
        if session_id in self._allocated:
            idx = self._allocated[session_id]
        else:
            if not self._available:
                raise DockerResourceError(
                    f"Subnet pool exhausted: cannot allocate non-overlapping subnet "
                    f"(max {self.max_subnets} active sessions)."
                )
            idx = self._available.pop(0)
            if idx in self._allocated.values():
                raise DockerResourceError(
                    f"Cannot establish that candidate subnet index {idx} is unused."
                )
            self._allocated[session_id] = idx

        subnet = f"{self.base_prefix}.{idx}.0/24"
        gateway = f"{self.base_prefix}.{idx}.{GATEWAY_HOST_OCTET}"
        proxy_ip = f"{self.base_prefix}.{idx}.{PROXY_HOST_OCTET}"
        session_ip = f"{self.base_prefix}.{idx}.{SESSION_HOST_OCTET}"
        return subnet, gateway, proxy_ip, session_ip

    def release(self, session_id: str) -> None:
        """Release the allocated subnet back to the available pool."""
        idx = self._allocated.pop(session_id, None)
        if idx is not None and idx not in self._available:
            self._available.append(idx)
            self._available.sort()

    def get_allocated_ip(self, session_id: str) -> str | None:
        """Get the deterministic session container IP assigned to a session."""
        idx = self._allocated.get(session_id)
        if idx is not None:
            return f"{self.base_prefix}.{idx}.{SESSION_HOST_OCTET}"
        return None

    def get_allocated_proxy_ip(self, session_id: str) -> str | None:
        """Get the deterministic proxy container IP assigned on a session network."""
        idx = self._allocated.get(session_id)
        if idx is not None:
            return f"{self.base_prefix}.{idx}.{PROXY_HOST_OCTET}"
        return None

    def is_allocated(self, session_id: str) -> bool:
        """Return True if session has an allocated subnet."""
        return session_id in self._allocated

    @property
    def allocated_sessions(self) -> list[str]:
        """List of session IDs with active subnet allocations."""
        return list(self._allocated.keys())

    @property
    def available_count(self) -> int:
        """Count of available subnet slots remaining."""
        return len(self._available)

    @property
    def active_count(self) -> int:
        """Count of currently allocated session subnets."""
        return len(self._allocated)


# ---------------------------------------------------------------------------
# Validation & Clamping Helpers
# ---------------------------------------------------------------------------
def validate_session_id(session_id: str) -> str:
    """Validate that a session ID meets the strict contract format.

    Rule (docs/CONTRACTS.md Section 1):
    's_' followed by a valid 26-character Crockford Base32 ULID.
    Rejects arbitrary names such as 's_test', 's_12345', path traversal,
    null bytes, and shell delimiters.

    Returns:
        The validated session ID string.

    Raises:
        InvalidSessionIdError: If validation fails (fail closed).
    """
    if not isinstance(session_id, str):
        raise InvalidSessionIdError(
            f"Session ID must be a string, got {type(session_id).__name__}."
        )

    cleaned = session_id.strip()

    if not cleaned or cleaned != session_id:
        raise InvalidSessionIdError(
            f"Session ID cannot be empty or contain whitespace: {session_id!r}"
        )

    if any(c in cleaned for c in FORBIDDEN_ID_CHARS):
        raise InvalidSessionIdError(
            f"Session ID contains forbidden characters: {cleaned!r}"
        )

    if ".." in cleaned:
        raise InvalidSessionIdError(
            f"Path traversal ('..') detected in session ID: {cleaned!r}"
        )

    if not cleaned.startswith(SESSION_ID_PREFIX):
        raise InvalidSessionIdError(
            f"Session ID must start with {SESSION_ID_PREFIX!r} prefix "
            f"per CONTRACTS.md: {cleaned!r}"
        )

    ulid_part = cleaned[len(SESSION_ID_PREFIX) :]
    if len(ulid_part) != ULID_LENGTH:
        raise InvalidSessionIdError(
            f"Session ID must be exactly 28 characters (prefix 's_' + 26-char ULID), "
            f"got {len(cleaned)} characters: {cleaned!r}"
        )

    upper_ulid = ulid_part.upper()
    if not ULID_REGEX.match(upper_ulid):
        raise InvalidSessionIdError(
            f"Session ID format invalid: {cleaned!r}. "
            "Expected 's_' followed by a valid 26-character Crockford Base32 ULID "
            "(first character 0-7, excluding I, L, O, U)."
        )

    return f"{SESSION_ID_PREFIX}{upper_ulid}"


def session_container_name(session_id: str) -> str:
    """Return the canonical Docker container name for a session."""
    valid_id = validate_session_id(session_id)
    return f"sess_{valid_id}"


def session_network_name(session_id: str) -> str:
    """Return the canonical Docker internal network name for a session."""
    valid_id = validate_session_id(session_id)
    return f"sess_net_{valid_id}"


def session_volume_name(session_id: str) -> str:
    """Return the canonical Docker workspace volume name for a session."""
    valid_id = validate_session_id(session_id)
    return f"ws_{valid_id}"


def validate_profile_image(image: str) -> str:
    """Validate that the container image is pinned to a specific non-latest tag.

    Rule (docs/SECURITY.md Section 2):
    Pinned image tags (no latest); images built from /images.
    """
    if not isinstance(image, str) or not image.strip():
        raise InvalidProfileError("Profile image must be a non-empty string.")

    cleaned = image.strip()

    if ":" not in cleaned:
        raise InvalidProfileError(
            f"Profile image '{cleaned}' must specify an explicit pinned tag "
            "(e.g. 'pocket/py-node:1.0')."
        )

    tag = cleaned.rsplit(":", 1)[1]
    if tag.lower() == "latest":
        raise InvalidProfileError(
            f"Profile image '{cleaned}' uses ':latest' tag, which is forbidden. "
            "Pinned tag required."
        )

    return cleaned


def parse_memory_to_bytes(mem: str | int | float) -> int:
    """Parse memory string (e.g. '2g', '512m', '1024k', or raw bytes) to bytes."""
    if isinstance(mem, (int, float)):
        return int(mem)

    s = str(mem).strip().lower()
    units = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024 * 1024,
        "mb": 1024 * 1024,
        "mib": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
        "gib": 1024 * 1024 * 1024,
    }

    for suffix, mult in sorted(units.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            num_str = s[: -len(suffix)].strip()
            try:
                return int(float(num_str) * mult)
            except ValueError as exc:
                raise InvalidProfileError(
                    f"Invalid memory value in profile: {mem!r}"
                ) from exc

    try:
        return int(s)
    except ValueError as exc:
        raise InvalidProfileError(f"Invalid memory value in profile: {mem!r}") from exc


def clamp_resources(
    requested_cpus: float,
    requested_memory: str | int | float,
    requested_pids: int,
) -> tuple[float, int, int]:
    """Clamp resource requests to boundaries established by shipped profiles.

    Returns:
        (clamped_cpus, clamped_memory_bytes, clamped_pids)
    """
    if requested_cpus <= 0:
        clamped_cpus = MIN_CPUS
    else:
        clamped_cpus = max(MIN_CPUS, min(float(requested_cpus), MAX_CPUS))

    mem_bytes = parse_memory_to_bytes(requested_memory)
    if mem_bytes <= 0:
        clamped_mem_bytes = MIN_MEMORY_BYTES
    else:
        clamped_mem_bytes = max(MIN_MEMORY_BYTES, min(mem_bytes, MAX_MEMORY_BYTES))

    if requested_pids <= 0:
        clamped_pids = MIN_PIDS
    else:
        clamped_pids = max(MIN_PIDS, min(int(requested_pids), MAX_PIDS))

    return clamped_cpus, clamped_mem_bytes, clamped_pids


def _inspect_container_network_ip(
    container: Any,
    network_name: str,
    expected_ip: str,
    role_label: str,
    *,
    require_runtime_ip: bool = False,
) -> str:
    """Reload container and verify its configured IP on network_name.

    Inspects both runtime IPAddress and configured IPAMConfig.IPv4Address under
    NetworkSettings.Networks[network_name]. Never guesses or falls back to an IP;
    fails closed with DockerResourceError if inspection fails, addresses are missing,
    or any observed address differs from expected_ip.
    """
    try:
        container.reload()
    except Exception as exc:
        raise DockerResourceError(
            f"Failed to reload {role_label} while verifying IP on network "
            f"'{network_name}': {exc}"
        ) from exc

    attrs = getattr(container, "attrs", None)
    if not isinstance(attrs, dict):
        raise DockerResourceError(
            f"Missing container attrs on {role_label} when verifying IP for "
            f"network '{network_name}'."
        )

    net_settings = attrs.get("NetworkSettings")
    networks = net_settings.get("Networks") if isinstance(net_settings, dict) else None
    net_entry = networks.get(network_name) if isinstance(networks, dict) else None
    if not isinstance(net_entry, dict):
        raise DockerResourceError(
            f"{role_label} has no endpoint metadata for network '{network_name}'."
        )

    runtime_ip_raw = net_entry.get("IPAddress")
    runtime_ip = runtime_ip_raw.strip() if isinstance(runtime_ip_raw, str) else ""

    ipam_cfg = net_entry.get("IPAMConfig")
    ipam_ip_raw = ipam_cfg.get("IPv4Address") if isinstance(ipam_cfg, dict) else None
    ipam_ip = ipam_ip_raw.strip() if isinstance(ipam_ip_raw, str) else ""

    if require_runtime_ip and not runtime_ip:
        raise DockerResourceError(
            f"Docker returned no active IPAddress for {role_label} on network "
            f"'{network_name}' (expected '{expected_ip}')."
        )

    observed = [ip for ip in (runtime_ip, ipam_ip) if ip]
    if not observed:
        raise DockerResourceError(
            f"Docker returned no configured or assigned IP address for {role_label} "
            f"on network '{network_name}' (expected '{expected_ip}')."
        )

    for ip_val in observed:
        try:
            ipaddress.ip_address(ip_val)
        except ValueError as exc:
            raise DockerResourceError(
                f"Invalid IP address format '{ip_val}' reported for {role_label} "
                f"on network '{network_name}': {exc}"
            ) from exc
        if ip_val != expected_ip:
            raise DockerResourceError(
                f"Unexpected IP address '{ip_val}' for {role_label} on network "
                f"'{network_name}': expected '{expected_ip}'."
            )

    return expected_ip


# ---------------------------------------------------------------------------
# Session Container Manager
# ---------------------------------------------------------------------------
class SessionContainerManager:
    """Manages Docker container lifecycle for isolated session environments."""

    def __init__(
        self,
        docker_client: docker.DockerClient | None = None,
        profiles_dir: Path | str | None = None,
        egress_policy_manager: EgressPolicyManager | None = None,
        subnet_pool: SubnetPool | None = None,
        proxy_container_name: str = DEFAULT_PROXY_CONTAINER_NAME,
        docker_client_factory: object | None = None,
    ) -> None:
        # Injected client takes priority; factory is used for lazy creation.
        # If neither is supplied, get_default_docker_client() is used on first access.
        self._client = docker_client
        self._docker_client_factory = docker_client_factory
        self._profiles_dir = profiles_dir
        self._profile_loader = ProfileLoader(profiles_dir=profiles_dir)
        self._egress_policy_manager = (
            egress_policy_manager
            if egress_policy_manager is not None
            else EgressPolicyManager()
        )
        self._subnet_pool = subnet_pool if subnet_pool is not None else SubnetPool()
        self._proxy_container_name = proxy_container_name
        self._active_sessions: dict[str, str] = {}  # session_id -> assigned source IP

    @property
    def proxy_container_name(self) -> str:
        """Return the name of the egress proxy container."""
        return self._proxy_container_name

    @property
    def client(self) -> docker.DockerClient:
        """Return the underlying DockerClient, creating it lazily when first accessed.

        Resolution order:
        1. An explicitly injected ``docker_client`` (from constructor or test).
        2. A ``docker_client_factory`` callable injected via the constructor.
        3. The process-wide default via
           :func:`~.docker_client.get_default_docker_client`,
           which reads ``DOCKER_HOST`` / ``DOCKER_TLS_VERIFY`` / ``DOCKER_CERT_PATH``
           and fails closed on insecure or incomplete configurations.

        Never connects at import time; never falls back to an insecure endpoint.
        """
        if self._client is not None:
            return self._client

        try:
            if self._docker_client_factory is not None:
                self._client = self._docker_client_factory()  # type: ignore[operator]
            else:
                self._client = get_default_docker_client()
        except DockerClientConfigError as exc:
            raise DockerResourceError(
                f"Failed to initialise Docker client: {exc}"
            ) from exc
        except Exception as exc:
            raise DockerResourceError(
                f"Unexpected error initialising Docker client: {exc}"
            ) from exc

        return self._client

    @property
    def egress_policy_manager(self) -> EgressPolicyManager:
        """Return the associated EgressPolicyManager instance."""
        return self._egress_policy_manager

    @property
    def subnet_pool(self) -> SubnetPool:
        """Return the subnet allocator instance."""
        return self._subnet_pool

    def reconcile_networks(self) -> None:
        """Reconcile subnet pool and session records with Docker networks.

        Queries Docker for networks labeled 'overwatch.managed=true', validates
        their metadata, and reserves their subnets so they are never reassigned
        across restarts.
        """
        self._subnet_pool.reconcile(self.client)
        active_ids = set(self._subnet_pool.allocated_sessions)
        self._active_sessions = {
            sid: ip for sid, ip in self._active_sessions.items() if sid in active_ids
        }
        for sid in active_ids:
            if sid not in self._active_sessions:
                ip = self._subnet_pool.get_allocated_ip(sid)
                if ip:
                    self._active_sessions[sid] = ip

    def _resolve_profile(self, profile: Profile | str) -> Profile:
        """Resolve and verify profile against trusted on-disk profiles.

        Security rule:
        Callers cannot bypass the configured profile on disk by supplying an
        in-memory Profile object with modified images, allowlists, or resources.
        Always loads the trusted profile from disk. If a Profile object is passed,
        it must match the on-disk profile exactly.
        """
        if isinstance(profile, str):
            try:
                return self._profile_loader.load(profile)
            except ProfileLoaderError as exc:
                raise InvalidProfileError(
                    f"Profile '{profile}' failed validation: {exc}"
                ) from exc

        if isinstance(profile, Profile):
            try:
                trusted = self._profile_loader.load(profile.name)
            except ProfileLoaderError as exc:
                raise InvalidProfileError(
                    f"Cannot verify profile '{profile.name}' against trusted "
                    f"on-disk configuration: {exc}"
                ) from exc

            # Strict equality check: custom object must match trusted file exactly
            if profile.model_dump() != trusted.model_dump():
                raise InvalidProfileError(
                    f"Custom Profile object for '{profile.name}' does not match "
                    "trusted on-disk configuration. Custom image or egress "
                    "allowlist bypass prohibited."
                )
            return trusted

        raise InvalidProfileError(
            f"Expected Profile instance or str profile name, got "
            f"{type(profile).__name__}."
        )

    def create_session(
        self,
        session_id: str,
        profile: Profile | str,
        start: bool = True,
    ) -> SessionContainerInfo:
        """Create a hardened session container, internal network, and workspace volume.

        Allocates a dedicated non-overlapping subnet, validates the actual source IP,
        and registers the session's allowlist with EgressPolicyManager.

        Args:
            session_id: Validated prefixed ULID ('s_' + 26-char ULID).
            profile: Profile name string or exact match Profile instance.
            start: Whether to start the container after creation (default True).

        Returns:
            SessionContainerInfo containing resource IDs and metadata.

        Raises:
            InvalidSessionIdError: If session_id is malformed or invalid ULID.
            InvalidProfileError: If profile is invalid, modified, or unpinned.
            DockerImageNotFoundError: If profile image does not exist locally.
            DockerResourceError: If any Docker API or IPAM operation fails.
        """
        valid_id = validate_session_id(session_id)
        valid_profile = self._resolve_profile(profile)
        validated_image = validate_profile_image(valid_profile.image)

        # Fail closed: Do NOT pull images automatically. Check local existence.
        try:
            self.client.images.get(validated_image)
        except docker.errors.ImageNotFound as exc:
            raise DockerImageNotFoundError(
                f"Profile image '{validated_image}' is not found locally. "
                "Automatic image pulling is prohibited."
            ) from exc
        except docker.errors.APIError as exc:
            raise DockerResourceError(
                f"Docker API error while inspecting image '{validated_image}': {exc}"
            ) from exc

        # Clamp CPU, memory, and PIDs to shipped profile maximums
        clamped_cpus, clamped_mem_bytes, clamped_pids = clamp_resources(
            requested_cpus=valid_profile.resources.cpus,
            requested_memory=valid_profile.resources.memory,
            requested_pids=valid_profile.resources.pids,
        )

        c_name = session_container_name(valid_id)
        n_name = session_network_name(valid_id)
        v_name = session_volume_name(valid_id)

        # Reconcile subnet pool with existing Docker networks before allocating
        self.reconcile_networks()

        if self._subnet_pool.is_allocated(valid_id):
            raise DockerResourceError(
                f"Session '{valid_id}' already has an active network allocation."
            )

        # Allocate non-overlapping subnet and deterministic endpoint IPs (.1/.2/.3)
        subnet_cidr, gateway_ip, expected_proxy_ip, expected_session_ip = (
            self._subnet_pool.allocate(valid_id)
        )

        labels = {
            "overwatch.managed": "true",
            "overwatch.session_id": valid_id,
            "overwatch.profile": valid_profile.name,
            "overwatch.subnet": subnet_cidr,
        }

        created_volume: Any = None
        created_network: Any = None
        created_container: Any = None
        proxy_connected: bool = False
        policy_registered: bool = False

        try:
            # Step 1: Create per-session workspace volume
            try:
                created_volume = self.client.volumes.create(
                    name=v_name,
                    labels=labels,
                )
            except docker.errors.APIError as exc:
                raise DockerResourceError(
                    f"Failed to create workspace volume '{v_name}': {exc}"
                ) from exc

            # Step 2: Create internal network with dedicated non-overlapping IPAM
            try:
                ipam_pool = docker.types.IPAMPool(
                    subnet=subnet_cidr,
                    gateway=gateway_ip,
                )
                ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])

                created_network = self.client.networks.create(
                    name=n_name,
                    internal=True,
                    driver="bridge",
                    ipam=ipam_config,
                    labels=labels,
                    check_duplicate=True,
                )
            except docker.errors.APIError as exc:
                raise DockerResourceError(
                    f"Failed to create internal session network '{n_name}' "
                    f"with subnet '{subnet_cidr}': {exc}"
                ) from exc

            # Step 3: Connect running egress proxy at explicit .2 IP
            try:
                proxy_container = self.client.containers.get(self._proxy_container_name)
            except docker.errors.NotFound as exc:
                raise DockerResourceError(
                    f"Egress proxy container '{self._proxy_container_name}' "
                    f"not found: {exc}"
                ) from exc
            except docker.errors.APIError as exc:
                raise DockerResourceError(
                    f"Failed looking up egress proxy container "
                    f"'{self._proxy_container_name}': {exc}"
                ) from exc

            # Verify proxy container is running
            proxy_status = getattr(proxy_container, "status", None)
            if proxy_status is not None and isinstance(proxy_status, str):
                if proxy_status.lower() not in ("running", ""):
                    raise DockerResourceError(
                        f"Egress proxy container '{self._proxy_container_name}' "
                        f"is not running (status: {proxy_status})."
                    )
            elif hasattr(proxy_container, "attrs") and isinstance(
                proxy_container.attrs, dict
            ):
                state = proxy_container.attrs.get("State", {})
                if isinstance(state, dict):
                    status = state.get("Status")
                    if (
                        status
                        and isinstance(status, str)
                        and status.lower() not in ("running", "")
                    ):
                        raise DockerResourceError(
                            f"Egress proxy container '{self._proxy_container_name}' "
                            f"is not running (status: {status})."
                        )

            try:
                created_network.connect(
                    proxy_container,
                    aliases=[DEFAULT_PROXY_NETWORK_ALIAS],
                    ipv4_address=expected_proxy_ip,
                )
                proxy_connected = True
            except docker.errors.APIError as exc:
                raise DockerResourceError(
                    f"Failed to connect egress proxy '{self._proxy_container_name}' "
                    f"to session network '{n_name}' at IP '{expected_proxy_ip}': {exc}"
                ) from exc

            actual_proxy_ip = _inspect_container_network_ip(
                proxy_container,
                n_name,
                expected_proxy_ip,
                f"egress proxy container '{self._proxy_container_name}'",
                require_runtime_ip=True,
            )

            # Step 4: Create container (unstarted) with explicit .3 endpoint IP
            api_version = docker.constants.DEFAULT_DOCKER_API_VERSION
            api_obj = getattr(self.client, "api", None)
            client_ver = (
                getattr(api_obj, "_version", None) if api_obj is not None else None
            )
            if isinstance(client_ver, str) and client_ver.strip():
                api_version = client_ver.strip()

            endpoint_config = docker.types.EndpointConfig(
                api_version,
                ipv4_address=expected_session_ip,
            )

            container_kwargs = {
                "image": validated_image,
                "name": c_name,
                "user": "1000:1000",
                "read_only": True,
                "tmpfs": {"/tmp": "rw,size=256m"},
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "nano_cpus": int(clamped_cpus * 1_000_000_000),
                "mem_limit": clamped_mem_bytes,
                "pids_limit": clamped_pids,
                "volumes": {v_name: {"bind": "/workspace", "mode": "rw"}},
                "network": n_name,
                "networking_config": {n_name: endpoint_config},
                "privileged": False,
                "ports": None,
                "labels": labels,
                "environment": {
                    "OVERWATCH_SESSION_ID": valid_id,
                    "OVERWATCH_PROFILE": valid_profile.name,
                    "HTTP_PROXY": DEFAULT_PROXY_URL,
                    "HTTPS_PROXY": DEFAULT_PROXY_URL,
                    "http_proxy": DEFAULT_PROXY_URL,
                    "https_proxy": DEFAULT_PROXY_URL,
                },
            }

            try:
                created_container = self.client.containers.create(**container_kwargs)
            except docker.errors.ImageNotFound as exc:
                raise DockerImageNotFoundError(
                    f"Image '{validated_image}' missing during container creation: "
                    f"{exc}"
                ) from exc
            except docker.errors.APIError as exc:
                raise DockerResourceError(
                    f"Failed to create session container '{c_name}': {exc}"
                ) from exc

            # Step 5: Inspect configured container IP (fail closed; never guess)
            actual_ip = _inspect_container_network_ip(
                created_container,
                n_name,
                expected_session_ip,
                f"session container '{c_name}'",
                require_runtime_ip=False,
            )

            if actual_ip == actual_proxy_ip:
                raise DockerResourceError(
                    f"Session container '{c_name}' IP '{actual_ip}' collides with "
                    f"egress proxy IP '{actual_proxy_ip}' on network '{n_name}'."
                )

            ip_obj = ipaddress.ip_address(actual_ip)

            # Ensure source IP is unique across all active sessions (prevent collision)
            for active_id, active_ip in self._active_sessions.items():
                if active_id != valid_id and active_ip == actual_ip:
                    raise DockerResourceError(
                        f"Source IP collision detected: session '{valid_id}' got IP "
                        f"'{actual_ip}' already in use by session '{active_id}'. "
                        "Failing closed to prevent egress policy cross-application."
                    )

            # Check that IP belongs to allocated non-overlapping subnet
            subnet_obj = ipaddress.ip_network(subnet_cidr)
            if ip_obj not in subnet_obj:
                raise DockerResourceError(
                    f"Container IP '{actual_ip}' does not belong to allocated "
                    f"subnet '{subnet_cidr}'."
                )

            # Step 6: Register allowlist with EgressPolicyManager before start
            try:
                self._egress_policy_manager.add_session(
                    session_id=valid_id,
                    source_ip=actual_ip,
                    allowed_domains=valid_profile.egress_allowlist,
                )
                self._active_sessions[valid_id] = actual_ip
                policy_registered = True
            except Exception as exc:
                raise DockerResourceError(
                    f"Failed to register egress allowlist policy for session "
                    f"'{valid_id}': {exc}"
                ) from exc

            # Step 7: Start container ONLY after policy registration succeeded
            if start:
                try:
                    created_container.start()
                except docker.errors.APIError as exc:
                    raise DockerResourceError(
                        f"Failed to start session container '{c_name}': {exc}"
                    ) from exc

                _inspect_container_network_ip(
                    created_container,
                    n_name,
                    expected_session_ip,
                    f"started session container '{c_name}'",
                    require_runtime_ip=True,
                )

            return SessionContainerInfo(
                session_id=valid_id,
                container_id=created_container.id,
                container_name=c_name,
                network_id=created_network.id,
                network_name=n_name,
                volume_name=v_name,
                image=validated_image,
                profile_name=valid_profile.name,
                ip_address=actual_ip,
                subnet=subnet_cidr,
                proxy_ip=actual_proxy_ip,
            )

        except Exception:
            # Safe rollback of any partially created resources
            if policy_registered:
                try:
                    self._egress_policy_manager.remove_session(valid_id)
                except Exception:
                    pass
            self._active_sessions.pop(valid_id, None)
            self._subnet_pool.release(valid_id)

            if created_container is not None:
                try:
                    created_container.remove(force=True)
                except Exception:
                    pass

            if proxy_connected and created_network is not None:
                try:
                    proxy_container = self.client.containers.get(
                        self._proxy_container_name
                    )
                    created_network.disconnect(proxy_container, force=True)
                except Exception:
                    pass

            if created_network is not None:
                try:
                    created_network.remove()
                except Exception:
                    pass

            if created_volume is not None:
                try:
                    created_volume.remove(force=True)
                except Exception:
                    pass
            raise

    def teardown_session(self, session_id: str) -> None:
        """Tear down all Docker resources and egress policy for a session.

        Guarantees:
        1. Stop and remove session container first so it cannot access proxy
           or external network while policy is torn down.
        2. Remove egress ACL policy from EgressPolicyManager.
        3. Disconnect the egress proxy container from the session network.
        4. Remove the session network (disconnecting any remaining containers).
        5. Remove the workspace volume.
        6. Release subnet allocation and tracking.

        Handles cleanup failures safely without leaving a live session container
        with proxy access.

        Raises:
            InvalidSessionIdError: If session_id fails validation.
            DockerResourceError: If Docker API operations fail unexpectedly.
        """
        valid_id = validate_session_id(session_id)
        c_name = session_container_name(valid_id)
        n_name = session_network_name(valid_id)
        v_name = session_volume_name(valid_id)

        errors: list[str] = []

        # 1. Stop and remove container first to prevent traffic during teardown
        try:
            try:
                container = self.client.containers.get(c_name)
                try:
                    container.stop(timeout=2)
                except Exception:
                    pass
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        except docker.errors.APIError as exc:
            errors.append(f"Failed to remove container '{c_name}': {exc}")

        # 2. Remove egress ACL policy and tracking
        try:
            self._egress_policy_manager.remove_session(valid_id)
        except Exception as exc:
            errors.append(f"Failed to remove egress policy for '{valid_id}': {exc}")

        self._active_sessions.pop(valid_id, None)
        self._subnet_pool.release(valid_id)

        # 3. Disconnect proxy from session network
        network = None
        try:
            network = self.client.networks.get(n_name)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as exc:
            errors.append(f"Failed to inspect network '{n_name}': {exc}")

        if network is not None:
            try:
                proxy_container = self.client.containers.get(self._proxy_container_name)
                try:
                    network.disconnect(proxy_container, force=True)
                except docker.errors.NotFound:
                    pass
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError as exc:
                errors.append(
                    f"Failed to disconnect proxy '{self._proxy_container_name}' "
                    f"from network '{n_name}': {exc}"
                )

        # 4. Remove session network (disconnecting any remaining containers)
        if network is not None:
            try:
                try:
                    network.reload()
                    for conn in getattr(network, "containers", []):
                        try:
                            network.disconnect(conn, force=True)
                        except Exception:
                            pass
                except Exception:
                    pass
                network.remove()
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError as exc:
                errors.append(f"Failed to remove network '{n_name}': {exc}")

        # 5. Remove volume
        try:
            try:
                volume = self.client.volumes.get(v_name)
                volume.remove(force=True)
            except docker.errors.NotFound:
                pass
        except docker.errors.APIError as exc:
            errors.append(f"Failed to remove volume '{v_name}': {exc}")

        if errors:
            raise DockerResourceError(
                f"Teardown errors for session '{valid_id}': " + "; ".join(errors)
            )

    def pause_session(self, session_id: str) -> None:
        """Pause the running Docker container for ``session_id`` (idempotent)."""
        valid_id = validate_session_id(session_id)
        c_name = session_container_name(valid_id)
        try:
            container = self.client.containers.get(c_name)
        except docker.errors.NotFound as exc:
            raise DockerResourceError(
                f"Session container '{c_name}' not found: {exc}"
            ) from exc
        except docker.errors.APIError as exc:
            raise DockerResourceError(
                f"Failed to inspect session container '{c_name}': {exc}"
            ) from exc

        status = getattr(container, "status", None)
        if isinstance(status, str) and status.lower() == "paused":
            return
        attrs = getattr(container, "attrs", None)
        if isinstance(attrs, dict) and attrs.get("State", {}).get("Paused") is True:
            return

        try:
            container.pause()
        except docker.errors.APIError as exc:
            raise DockerResourceError(
                f"Failed to pause session container '{c_name}': {exc}"
            ) from exc

    def resume_session(self, session_id: str) -> None:
        """Unpause the paused Docker container for ``session_id`` (idempotent)."""
        valid_id = validate_session_id(session_id)
        c_name = session_container_name(valid_id)
        try:
            container = self.client.containers.get(c_name)
        except docker.errors.NotFound as exc:
            raise DockerResourceError(
                f"Session container '{c_name}' not found: {exc}"
            ) from exc
        except docker.errors.APIError as exc:
            raise DockerResourceError(
                f"Failed to inspect session container '{c_name}': {exc}"
            ) from exc

        attrs = getattr(container, "attrs", None)
        if isinstance(attrs, dict) and attrs.get("State", {}).get("Paused") is False:
            if attrs.get("State", {}).get("Running") is True:
                return
        status = getattr(container, "status", None)
        if isinstance(status, str) and status.lower() == "running":
            return

        try:
            container.unpause()
        except docker.errors.APIError as exc:
            raise DockerResourceError(
                f"Failed to resume session container '{c_name}': {exc}"
            ) from exc


# Alias for convenience
ContainerManager = SessionContainerManager

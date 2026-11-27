"""Unit tests for session container lifecycle manager (Phase 1, F-01, F-08).

Uses a mocked Docker client so unit tests run cleanly without requiring Docker.
Tests verify:
- Container hardening options (user 1000:1000, read-only root, /tmp tmpfs 256m,
  capabilities dropped, no-new-privileges, internal network, workspace volume).
- Non-overlapping session subnets and source-IP uniqueness (preventing Squid proxy
  cross-application of allowlists).
- Automatic registration with EgressPolicyManager and deregistration on teardown.
- Strict contract session ID validation: 's_' + 26-char Crockford Base32 ULID.
  Rejection of arbitrary IDs ('s_test', 's_12345', control characters, etc.).
- Prevention of custom Profile object bypass (custom images or allowlists cannot
  bypass trusted on-disk profiles).
- Resource clamping to shipped profile ceilings (coding.yaml).
- Image safety: pinned tags required, no :latest, no automatic pulling.
- Partial creation failure rollback (volume/network/container cleanup on error).
- Safe and idempotent teardown.
- Forbidden settings enforcement (no privileged, no host mounts, no docker.sock).
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import docker.errors
import pytest
from control_plane.contracts import ProfileResources
from control_plane.sessions.container_manager import (
    DEFAULT_PROXY_CONTAINER_NAME,
    DEFAULT_PROXY_NETWORK_ALIAS,
    DEFAULT_PROXY_URL,
    MAX_CPUS,
    MAX_MEMORY_BYTES,
    MAX_PIDS,
    MIN_CPUS,
    MIN_MEMORY_BYTES,
    MIN_PIDS,
    ContainerManager,
    ContainerManagerError,
    DockerImageNotFoundError,
    DockerResourceError,
    InvalidProfileError,
    InvalidSessionIdError,
    SessionContainerInfo,
    SessionContainerManager,
    SubnetPool,
    clamp_resources,
    parse_memory_to_bytes,
    session_container_name,
    session_network_name,
    session_volume_name,
    validate_profile_image,
    validate_session_id,
)
from control_plane.sessions.egress_policy import (
    EgressPolicyManager,
    EgressPolicyPersistenceError,
)

# Valid contract-compliant session ULIDs
SESSION_ID_1 = "s_01J8ABCDEFGH1234567890ABCD"
SESSION_ID_2 = "s_01J8ABCDEFGH1234567890ABCE"
SESSION_ID_3 = "s_01J8ABCDEFGH1234567890ABCF"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo_profiles_dir() -> Path:
    """Path to the repository profiles directory."""
    return Path(__file__).resolve().parents[2] / "profiles"


@pytest.fixture
def mock_docker():
    """Create a fully configured mock DockerClient."""
    client = MagicMock(spec=docker.DockerClient)
    client.api = MagicMock()
    client.api._version = "1.45"

    # Mock Image
    mock_image = MagicMock()
    mock_image.id = "sha256:mock_image_id"
    client.images.get.return_value = mock_image

    # Mock Volume
    mock_volume = MagicMock()
    mock_volume.name = f"ws_{SESSION_ID_1}"
    client.volumes.create.return_value = mock_volume
    client.volumes.get.return_value = mock_volume

    # Mock Network
    mock_network = MagicMock()
    mock_network.id = "net_123456"
    mock_network.name = f"sess_net_{SESSION_ID_1}"
    mock_network.containers = []

    def network_create_side_effect(**kwargs):
        if "name" in kwargs:
            mock_network.name = kwargs["name"]
        return mock_network

    def network_connect_side_effect(container, *args, **kwargs):
        ipv4 = kwargs.get("ipv4_address")
        net_name = mock_network.name
        if ipv4 and isinstance(getattr(container, "attrs", None), dict):
            ns = container.attrs.setdefault("NetworkSettings", {})
            nets = ns.setdefault("Networks", {})
            nets[net_name] = {
                "IPAddress": ipv4,
                "IPAMConfig": {"IPv4Address": ipv4},
            }

    mock_network.connect.side_effect = network_connect_side_effect
    client.networks.create.return_value = mock_network
    client.networks.create.side_effect = network_create_side_effect
    client.networks.get.return_value = mock_network
    client.networks.list.return_value = []

    # Mock Proxy Container
    mock_proxy = MagicMock()
    mock_proxy.id = "proxy_123456"
    mock_proxy.name = DEFAULT_PROXY_CONTAINER_NAME
    mock_proxy.status = "running"
    mock_proxy.attrs = {
        "State": {"Status": "running"},
        "NetworkSettings": {"Networks": {}},
    }

    # Mock Session Container
    mock_container = MagicMock()
    mock_container.id = "cont_123456"
    mock_container.name = f"sess_{SESSION_ID_1}"
    mock_container.attrs = {"NetworkSettings": {"Networks": {}}}

    def container_create_side_effect(**kwargs):
        if "name" in kwargs:
            mock_container.name = kwargs["name"]
        net_name = kwargs.get("network")
        net_cfg = kwargs.get("networking_config") or {}
        if net_name and isinstance(net_cfg, dict) and net_name in net_cfg:
            ep = net_cfg[net_name]
            ipv4 = (
                ep.get("IPAMConfig", {}).get("IPv4Address")
                if isinstance(ep, dict)
                else None
            )
            if ipv4:
                ns = mock_container.attrs.setdefault("NetworkSettings", {})
                nets = ns.setdefault("Networks", {})
                nets[net_name] = {
                    "IPAddress": ipv4,
                    "IPAMConfig": {"IPv4Address": ipv4},
                }
        return mock_container

    client.containers.create.return_value = mock_container
    client.containers.create.side_effect = container_create_side_effect

    def get_container(name_or_id):
        if name_or_id in (DEFAULT_PROXY_CONTAINER_NAME, "proxy_123456"):
            return mock_proxy
        return mock_container

    client.containers.get.side_effect = get_container

    return client


# ---------------------------------------------------------------------------
# 1. Hardening Options Verification
# ---------------------------------------------------------------------------
def test_hardening_options_applied_strictly(mock_docker, repo_profiles_dir):
    """Verify that all required hardening flags from docs/SECURITY.md are passed."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    info = mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert isinstance(info, SessionContainerInfo)
    assert info.session_id == SESSION_ID_1
    assert info.container_name == f"sess_{SESSION_ID_1}"
    assert info.network_name == f"sess_net_{SESSION_ID_1}"
    assert info.volume_name == f"ws_{SESSION_ID_1}"
    assert info.ip_address == "172.30.1.3"
    assert info.proxy_ip == "172.30.1.2"
    assert info.subnet == "172.30.1.0/24"

    # Volume creation checks
    mock_docker.volumes.create.assert_called_once_with(
        name=f"ws_{SESSION_ID_1}",
        labels={
            "overwatch.managed": "true",
            "overwatch.session_id": SESSION_ID_1,
            "overwatch.profile": "coding",
            "overwatch.subnet": "172.30.1.0/24",
        },
    )

    # Network creation checks (internal=True and dedicated IPAM subnet)
    mock_docker.networks.create.assert_called_once()
    _, net_kwargs = mock_docker.networks.create.call_args
    assert net_kwargs["name"] == f"sess_net_{SESSION_ID_1}"
    assert net_kwargs["internal"] is True
    assert net_kwargs["driver"] == "bridge"
    assert net_kwargs["check_duplicate"] is True

    # Container creation checks (exact hardening options)
    mock_docker.containers.create.assert_called_once()
    _, kwargs = mock_docker.containers.create.call_args

    assert kwargs["image"] == "pocket/py-node:1.0"
    assert kwargs["name"] == f"sess_{SESSION_ID_1}"
    assert kwargs["user"] == "1000:1000"
    assert kwargs["read_only"] is True
    assert kwargs["tmpfs"] == {"/tmp": "rw,size=256m"}
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["nano_cpus"] == int(2.0 * 1_000_000_000)
    assert kwargs["mem_limit"] == 2 * 1024 * 1024 * 1024
    assert kwargs["pids_limit"] == 512
    assert kwargs["volumes"] == {
        f"ws_{SESSION_ID_1}": {"bind": "/workspace", "mode": "rw"}
    }
    assert kwargs["network"] == f"sess_net_{SESSION_ID_1}"
    assert (
        kwargs["networking_config"][f"sess_net_{SESSION_ID_1}"]["IPAMConfig"][
            "IPv4Address"
        ]
        == "172.30.1.3"
    )
    assert kwargs["privileged"] is False
    assert kwargs["ports"] is None

    # Environment must NOT contain secrets or tokens
    env = kwargs["environment"]
    for key in env:
        assert "token" not in key.lower()
        assert "secret" not in key.lower()
        assert "key" not in key.lower()
        assert "password" not in key.lower()

    # Container start called
    mock_docker.containers.create.return_value.start.assert_called_once()


def test_create_session_without_start(mock_docker, repo_profiles_dir):
    """When start=False, container is created but not started."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding", start=False)

    mock_docker.containers.create.return_value.start.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Source-IP Uniqueness & EgressPolicyManager Integration
# ---------------------------------------------------------------------------
def test_multiple_sessions_have_unique_non_overlapping_subnets_and_ips(
    mock_docker, repo_profiles_dir
):
    """Every active session receives a non-overlapping subnet and unique IP."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    # Set up container mock to return unique deterministic .3 IPs per network
    def container_create_side_effect(**kwargs):
        c = MagicMock()
        c.id = f"cont_{kwargs['name']}"
        c.name = kwargs["name"]
        net_name = kwargs["network"]
        if "ABCE" in net_name:
            ip = "172.30.2.3"
        else:
            ip = "172.30.1.3"
        c.attrs = {
            "NetworkSettings": {
                "Networks": {
                    net_name: {
                        "IPAddress": ip,
                        "IPAMConfig": {"IPv4Address": ip},
                    }
                }
            }
        }
        return c

    mock_docker.containers.create.side_effect = container_create_side_effect

    info1 = mgr.create_session(SESSION_ID_1, "coding")
    info2 = mgr.create_session(SESSION_ID_2, "general")

    # Assert subnets do not overlap
    assert info1.subnet != info2.subnet
    assert info1.subnet == "172.30.1.0/24"
    assert info2.subnet == "172.30.2.0/24"

    # Assert container source IPs are unique (.3) and proxy IPs are unique (.2)
    assert info1.ip_address != info2.ip_address
    assert info1.ip_address == "172.30.1.3"
    assert info2.ip_address == "172.30.2.3"
    assert info1.proxy_ip == "172.30.1.2"
    assert info2.proxy_ip == "172.30.2.2"

    # Assert both sessions registered with EgressPolicyManager
    rule1 = mgr.egress_policy_manager.get_session(SESSION_ID_1)
    rule2 = mgr.egress_policy_manager.get_session(SESSION_ID_2)
    assert rule1 is not None
    assert rule2 is not None
    assert rule1.source_ip == "172.30.1.3"
    assert rule2.source_ip == "172.30.2.3"
    assert rule1.source_ip != rule2.source_ip

    # coding egress allowlist registered in rule1
    assert "github.com" in rule1.allowed_domains
    # general egress allowlist registered in rule2
    assert "google.com" in rule2.allowed_domains

    # Assert 2 distinct active networks created with non-overlapping IPAM pools
    assert mock_docker.networks.create.call_count == 2
    calls = mock_docker.networks.create.call_args_list
    subnets_created = [c.kwargs["ipam"]["Config"][0]["Subnet"] for c in calls]
    assert len(set(subnets_created)) == 2


def test_teardown_removes_policy_and_releases_subnet(mock_docker, repo_profiles_dir):
    """Teardown unregisters egress policy and returns subnet to the pool."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is not None
    assert mgr.subnet_pool.active_count == 1

    # Teardown
    mgr.teardown_session(SESSION_ID_1)

    # Egress policy removed
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None
    # Subnet released
    assert mgr.subnet_pool.active_count == 0

    # Creating a new session can reuse the released subnet safely
    info_new = mgr.create_session(SESSION_ID_2, "coding")
    assert info_new.subnet == "172.30.1.0/24"


def test_source_ip_collision_fails_closed(mock_docker, repo_profiles_dir):
    """If an allocated IP is already held by another active session, fail closed."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    # Session 1 created with 172.30.1.3
    mgr.create_session(SESSION_ID_1, "coding")

    # Force active-session tracking collision even if SubnetPool were tricked
    mgr._active_sessions[SESSION_ID_1] = "172.30.2.3"

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_2, "general")

    assert "Source IP collision detected" in str(exc_info.value)
    # Session 2 must NOT be registered in EgressPolicyManager
    assert mgr.egress_policy_manager.get_session(SESSION_ID_2) is None


def test_egress_policy_registration_failure_rolls_back(mock_docker, repo_profiles_dir):
    """If registering with EgressPolicyManager fails, roll back all Docker resources."""
    mock_epm = MagicMock(spec=EgressPolicyManager)
    mock_epm.add_session.side_effect = ValueError("Invalid domain in allowlist")

    mgr = SessionContainerManager(
        docker_client=mock_docker,
        profiles_dir=repo_profiles_dir,
        egress_policy_manager=mock_epm,
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Failed to register egress allowlist policy" in str(exc_info.value)
    # Resources rolled back
    mock_docker.containers.create.return_value.remove.assert_called_once_with(
        force=True
    )
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    # Subnet released
    assert mgr.subnet_pool.active_count == 0


# ---------------------------------------------------------------------------
# 3. Custom Profile Object Bypass Prevention
# ---------------------------------------------------------------------------
def test_custom_profile_image_bypass_rejected(mock_docker, repo_profiles_dir):
    """Custom Profile object with an altered image is rejected."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    # Load legitimate profile, modify image to malicious image
    trusted_profile = mgr._profile_loader.load("coding")
    hacked_profile = trusted_profile.model_copy(
        update={"image": "evil/attacker-image:1.0"}
    )

    with pytest.raises(InvalidProfileError) as exc_info:
        mgr.create_session(SESSION_ID_1, profile=hacked_profile)

    assert "does not match trusted on-disk configuration" in str(exc_info.value)
    # No Docker resources created
    mock_docker.volumes.create.assert_not_called()
    mock_docker.networks.create.assert_not_called()
    mock_docker.containers.create.assert_not_called()


def test_custom_profile_allowlist_bypass_rejected(mock_docker, repo_profiles_dir):
    """Custom Profile object attempting to widen egress allowlist is rejected."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    trusted_profile = mgr._profile_loader.load("coding")
    widened_profile = trusted_profile.model_copy(
        update={"egress_allowlist": ["evil-exfiltration.com", "hacker.io"]}
    )

    with pytest.raises(InvalidProfileError) as exc_info:
        mgr.create_session(SESSION_ID_1, profile=widened_profile)

    assert "Custom image or egress allowlist bypass prohibited" in str(exc_info.value)
    mock_docker.containers.create.assert_not_called()


def test_custom_profile_resources_bypass_rejected(mock_docker, repo_profiles_dir):
    """Custom Profile object modifying resource limits is rejected."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    trusted_profile = mgr._profile_loader.load("coding")
    altered_profile = trusted_profile.model_copy(
        update={"resources": ProfileResources(cpus=8.0, memory="16g", pids=4096)}
    )

    with pytest.raises(InvalidProfileError):
        mgr.create_session(SESSION_ID_1, profile=altered_profile)


def test_exact_matching_profile_object_accepted(mock_docker, repo_profiles_dir):
    """A Profile object matching the on-disk file exactly is accepted."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    trusted_profile = mgr._profile_loader.load("coding")

    info = mgr.create_session(SESSION_ID_1, profile=trusted_profile)
    assert info.profile_name == "coding"


# ---------------------------------------------------------------------------
# 4. Strict Contract Session ID Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("valid_id", "expected"),
    [
        ("s_01J8ABCDEFGH1234567890ABCD", "s_01J8ABCDEFGH1234567890ABCD"),
        ("s_01J8K0X4A1B2C3D4E5F6G7H8J9", "s_01J8K0X4A1B2C3D4E5F6G7H8J9"),
        ("s_7ZZZZZZZZZZZZZZZZZZZZZZZZZ", "s_7ZZZZZZZZZZZZZZZZZZZZZZZZZ"),
        ("s_00000000000000000000000000", "s_00000000000000000000000000"),
        # Lowercase Crockford Base32 normalized to uppercase canonical form
        ("s_01j8abcdefgh1234567890abcd", "s_01J8ABCDEFGH1234567890ABCD"),
    ],
)
def test_valid_contract_session_ids(valid_id: str, expected: str):
    """Valid contract session IDs are accepted and normalized to canonical uppercase."""
    canonical = validate_session_id(valid_id)
    assert canonical == expected
    assert session_container_name(valid_id) == f"sess_{expected}"
    assert session_network_name(valid_id) == f"sess_net_{expected}"
    assert session_volume_name(valid_id) == f"ws_{expected}"


@pytest.mark.parametrize(
    "invalid_id",
    [
        "s_test",  # arbitrary short name rejected
        "s_12345",  # arbitrary short name rejected
        "s_demo-session-123",  # hyphens not allowed in Crockford ULID body
        "s_SESSION_UNDER_SCORE",  # underscores not allowed in ULID body
        # ULID first-character range violation (must be [0-7], rejecting 8 and 9)
        "s_81J8ABCDEFGH1234567890ABCD",  # s_8 followed by 25 valid ULID characters
        "s_80000000000000000000000000",  # s_8 followed by 25 valid zeros
        "s_91J8ABCDEFGH1234567890ABCD",  # s_9 followed by 25 valid ULID characters
        # Forbidden Crockford Base32 characters: I, L, O, U in uppercase
        "s_01J8ABCDEFGH1234567890ABCI",
        "s_01J8ABCDEFGH1234567890ABCL",
        "s_01J8ABCDEFGH1234567890ABCO",
        "s_01J8ABCDEFGH1234567890ABCU",
        # Forbidden Crockford Base32 characters: i, l, o, u in lowercase
        "s_01j8abcdefgh1234567890abci",
        "s_01j8abcdefgh1234567890abcl",
        "s_01j8abcdefgh1234567890abco",
        "s_01j8abcdefgh1234567890abcu",
        # Formatting, prefix, and traversal violations
        "01J8ABCDEFGH1234567890ABCD",  # missing 's_' prefix
        "p_01J8ABCDEFGH1234567890ABCD",  # plan prefix (p_), not session
        "a_01J8ABCDEFGH1234567890ABCD",  # approval prefix (a_), not session
        "s_../escape",  # directory traversal
        "s_path/with/slash",  # slashes
        "s_test;rm -rf /",  # shell separator
        "s_01J8\x00nullbyte1234567890",  # null byte
        "",
        "   ",
        None,
        12345,
    ],
)
def test_invalid_session_ids_rejected(invalid_id: str):
    """Arbitrary values and non-ULIDs fail closed with InvalidSessionIdError."""
    with pytest.raises(InvalidSessionIdError) as exc_info:
        validate_session_id(invalid_id)
    assert exc_info.value.code == "ARG_INVALID"


def test_create_session_normalizes_lowercase_id(mock_docker, repo_profiles_dir):
    """Lowercase session ID is normalized in Docker resource names and policy."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    lowercase_id = "s_01j8abcdefgh1234567890abcd"
    expected_uppercase = "s_01J8ABCDEFGH1234567890ABCD"

    info = mgr.create_session(lowercase_id, "coding")
    assert info.session_id == expected_uppercase
    assert info.container_name == f"sess_{expected_uppercase}"
    assert info.network_name == f"sess_net_{expected_uppercase}"
    assert info.volume_name == f"ws_{expected_uppercase}"

    # EgressPolicyManager registered with normalized uppercase ID
    assert mgr.egress_policy_manager.get_session(expected_uppercase) is not None


# ---------------------------------------------------------------------------
# 5. Resource Clamping to Shipped Profile Ceilings
# ---------------------------------------------------------------------------
def test_clamp_resources_helper_direct():
    """Verify clamp_resources helper handles units and numeric bounds directly."""
    cpus, mem, pids = clamp_resources(
        requested_cpus=1.5, requested_memory="1g", requested_pids=300
    )
    assert cpus == 1.5
    assert mem == 1024 * 1024 * 1024
    assert pids == 300

    # Excessive values clamped to MAX_CPUS, MAX_MEMORY_BYTES, MAX_PIDS
    cpus, mem, pids = clamp_resources(
        requested_cpus=16.0, requested_memory="32g", requested_pids=8192
    )
    assert cpus == MAX_CPUS
    assert mem == MAX_MEMORY_BYTES
    assert pids == MAX_PIDS

    # Negative / 0 values clamped to MIN_CPUS, MIN_MEMORY_BYTES, MIN_PIDS
    cpus, mem, pids = clamp_resources(
        requested_cpus=-1.0, requested_memory="0m", requested_pids=-5
    )
    assert cpus == MIN_CPUS
    assert mem == MIN_MEMORY_BYTES
    assert pids == MIN_PIDS


def test_parse_memory_to_bytes_units():
    """Test memory string parsing across supported units."""
    assert parse_memory_to_bytes("512m") == 512 * 1024 * 1024
    assert parse_memory_to_bytes("512mb") == 512 * 1024 * 1024
    assert parse_memory_to_bytes("1g") == 1024 * 1024 * 1024
    assert parse_memory_to_bytes("2gb") == 2 * 1024 * 1024 * 1024
    assert parse_memory_to_bytes("2048k") == 2048 * 1024
    assert parse_memory_to_bytes(1048576) == 1048576

    with pytest.raises(InvalidProfileError):
        parse_memory_to_bytes("invalid_memory")


# ---------------------------------------------------------------------------
# 6. Image Presence & No Automatic Pull
# ---------------------------------------------------------------------------
def test_validate_profile_image_pinned():
    """Pinned image tags are accepted."""
    assert validate_profile_image("pocket/py-node:1.0") == "pocket/py-node:1.0"
    assert validate_profile_image("custom/base:v2.1.3") == "custom/base:v2.1.3"


@pytest.mark.parametrize(
    "bad_image",
    [
        "pocket/py-node:latest",  # unpinned latest
        "pocket/py-node",  # missing tag
        "",  # empty
        "   ",
        None,
    ],
)
def test_validate_profile_image_unpinned_rejected(bad_image: str):
    """Unpinned images or ':latest' tags fail closed with InvalidProfileError."""
    with pytest.raises(InvalidProfileError):
        validate_profile_image(bad_image)


def test_image_not_found_locally_fails_closed_no_pull(mock_docker, repo_profiles_dir):
    """When profile image is not found locally, fail closed; never pull."""
    mock_docker.images.get.side_effect = docker.errors.ImageNotFound("Image not found")

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    with pytest.raises(DockerImageNotFoundError):
        mgr.create_session(SESSION_ID_1, "coding")

    # images.pull must never be called
    mock_docker.images.pull.assert_not_called()
    mock_docker.volumes.create.assert_not_called()
    mock_docker.networks.create.assert_not_called()
    mock_docker.containers.create.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Partial Creation Failure Rollback
# ---------------------------------------------------------------------------
def test_partial_failure_network_creation_fails(mock_docker, repo_profiles_dir):
    """When network creation fails, the already-created volume must be removed."""
    mock_docker.networks.create.side_effect = docker.errors.APIError(
        "Network driver failed"
    )

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Failed to create internal session network" in str(exc_info.value)
    # Created volume must be cleaned up
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    # Container was never created
    mock_docker.containers.create.assert_not_called()
    # Subnet released
    assert mgr.subnet_pool.active_count == 0


def test_partial_failure_container_creation_fails(mock_docker, repo_profiles_dir):
    """When container creation fails, both network and volume must be rolled back."""
    mock_docker.containers.create.side_effect = docker.errors.APIError(
        "Cannot create container"
    )

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Failed to create session container" in str(exc_info.value)
    # Network must be removed
    mock_docker.networks.create.return_value.remove.assert_called_once()
    # Volume must be removed
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    # Subnet released
    assert mgr.subnet_pool.active_count == 0


def test_partial_failure_container_start_fails(mock_docker, repo_profiles_dir):
    """When container start fails, container, network, and volume are all removed."""
    mock_docker.containers.create.return_value.start.side_effect = (
        docker.errors.APIError("Failed to start process")
    )

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert "Failed to start session container" in str(exc_info.value)
    # Container must be removed
    mock_docker.containers.create.return_value.remove.assert_called_once_with(
        force=True
    )
    # Network must be removed
    mock_docker.networks.create.return_value.remove.assert_called_once()
    # Volume must be removed
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    # Subnet released
    assert mgr.subnet_pool.active_count == 0


# ---------------------------------------------------------------------------
# 8. Teardown Operations
# ---------------------------------------------------------------------------
def test_teardown_session_full_success(mock_docker, repo_profiles_dir):
    """Teardown removes container, network, volume, and egress policy cleanly."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    mgr.teardown_session(SESSION_ID_1)

    # Container stopped and removed
    mock_docker.containers.get.assert_any_call(f"sess_{SESSION_ID_1}")
    mock_docker.containers.get.assert_any_call(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.containers.create.return_value.stop.assert_called_once_with(timeout=2)
    mock_docker.containers.create.return_value.remove.assert_called_once_with(
        force=True
    )

    # Network removed
    mock_docker.networks.get.assert_called_once_with(f"sess_net_{SESSION_ID_1}")
    mock_docker.networks.get.return_value.remove.assert_called_once()

    # Volume removed
    mock_docker.volumes.get.assert_called_once_with(f"ws_{SESSION_ID_1}")
    mock_docker.volumes.get.return_value.remove.assert_called_once_with(force=True)

    # Policy removed
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None


def test_teardown_session_idempotent_not_found(mock_docker):
    """Teardown ignores NotFound errors if resources are already deleted."""
    mock_docker.containers.get.side_effect = docker.errors.NotFound("No such container")
    mock_docker.networks.get.side_effect = docker.errors.NotFound("No such network")
    mock_docker.volumes.get.side_effect = docker.errors.NotFound("No such volume")

    mgr = SessionContainerManager(docker_client=mock_docker)
    # Must succeed without raising an error
    mgr.teardown_session(SESSION_ID_1)


def test_teardown_session_invalid_id():
    """Teardown with invalid session ID fails closed before any Docker operations."""
    mgr = SessionContainerManager(docker_client=MagicMock())
    with pytest.raises(InvalidSessionIdError):
        mgr.teardown_session("invalid/path/traversal")


def test_teardown_session_collects_errors(mock_docker):
    """Teardown attempts cleanup across all resources and reports collected errors."""
    mock_docker.containers.get.side_effect = docker.errors.APIError(
        "Container lock error"
    )
    mock_docker.networks.get.side_effect = docker.errors.APIError("Network busy")

    mgr = SessionContainerManager(docker_client=mock_docker)
    with pytest.raises(DockerResourceError) as exc_info:
        mgr.teardown_session(SESSION_ID_1)

    assert "Container lock error" in str(exc_info.value)
    assert "Network busy" in str(exc_info.value)
    # Volume removal was still attempted
    mock_docker.volumes.get.assert_called_once_with(f"ws_{SESSION_ID_1}")


# ---------------------------------------------------------------------------
# 9. Aliases and Exports
# ---------------------------------------------------------------------------
def test_container_manager_alias():
    """ContainerManager is an alias for SessionContainerManager."""
    assert ContainerManager is SessionContainerManager
    assert issubclass(InvalidSessionIdError, ContainerManagerError)
    assert issubclass(InvalidProfileError, ContainerManagerError)
    assert issubclass(DockerResourceError, ContainerManagerError)
    assert issubclass(DockerImageNotFoundError, DockerResourceError)


# ---------------------------------------------------------------------------
# 10. Restart-Safe Subnet Reconciliation and Pool Exhaustion
# ---------------------------------------------------------------------------
def make_mock_network(
    session_id: str,
    subnet_idx: int,
    base_prefix: str = "172.30",
    name: str | None = None,
    include_managed_label: bool = True,
    include_session_id_label: bool = True,
    include_subnet_label: bool = True,
    corrupt_ipam: bool = False,
    override_subnet: str | None = None,
) -> MagicMock:
    """Helper to build mock Docker Network objects with attributes."""
    net = MagicMock()
    subnet = override_subnet or f"{base_prefix}.{subnet_idx}.0/24"
    net.name = name or f"sess_net_{session_id}"

    labels: dict[str, str] = {}
    if include_managed_label:
        labels["overwatch.managed"] = "true"
    if include_session_id_label:
        labels["overwatch.session_id"] = session_id
    if include_subnet_label:
        labels["overwatch.subnet"] = subnet

    if corrupt_ipam:
        ipam: dict[str, Any] = {"Config": []}
    else:
        gateway = f"{base_prefix}.{subnet_idx}.1"
        ipam = {"Config": [{"Subnet": subnet, "Gateway": gateway}]}

    net.attrs = {
        "Name": net.name,
        "Labels": labels,
        "IPAM": ipam,
    }
    return net


def test_reconcile_reserves_existing_docker_subnets_on_startup():
    """Reconciling with existing Docker networks reserves their subnets."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=1)
    net3 = make_mock_network(SESSION_ID_3, subnet_idx=3)
    mock_client.networks.list.return_value = [net1, net3]

    pool = SubnetPool(base_prefix="172.30", max_subnets=254)
    pool.reconcile(mock_client)

    # Both subnets must be reserved in the pool
    assert pool.is_allocated(SESSION_ID_1)
    assert pool.is_allocated(SESSION_ID_3)
    assert pool.get_allocated_ip(SESSION_ID_1) == "172.30.1.3"
    assert pool.get_allocated_ip(SESSION_ID_3) == "172.30.3.3"
    assert pool.get_allocated_proxy_ip(SESSION_ID_1) == "172.30.1.2"
    assert pool.get_allocated_proxy_ip(SESSION_ID_3) == "172.30.3.2"
    assert pool.active_count == 2

    # Allocating a new session must receive the first unused subnet (idx 2), NOT 1 or 3
    subnet2, gateway2, proxy_ip2, session_ip2 = pool.allocate(SESSION_ID_2)
    assert subnet2 == "172.30.2.0/24"
    assert gateway2 == "172.30.2.1"
    assert proxy_ip2 == "172.30.2.2"
    assert session_ip2 == "172.30.2.3"


def test_repeated_reconciliation_is_idempotent_and_safe():
    """Calling reconcile multiple times is safe and maintains consistent state."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=1)
    net2 = make_mock_network(SESSION_ID_2, subnet_idx=2)
    mock_client.networks.list.return_value = [net1, net2]

    pool = SubnetPool(base_prefix="172.30", max_subnets=10)

    # Call reconcile 5 times in succession
    for _ in range(5):
        pool.reconcile(mock_client)

    assert pool.active_count == 2
    assert pool.allocated_sessions == [SESSION_ID_1, SESSION_ID_2]
    assert pool.available_count == 8

    # Allocate next session: gets index 3
    subnet, _, _, _ = pool.allocate(SESSION_ID_3)
    assert subnet == "172.30.3.0/24"
    assert pool.available_count == 7


def test_reconcile_fails_closed_missing_session_id_label():
    """Managed network missing overwatch.session_id label fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(
        SESSION_ID_1, subnet_idx=1, include_session_id_label=False
    )
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "missing required 'overwatch.session_id' label" in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "s_test",
        "s_12345",
        "s_81J8ABCDEFGH1234567890ABCD",
        "s_01J8ABCDEFGH1234567890ABCI",
        "s_01J8ABCDEFGH1234567890ABCL",
        "s_01J8ABCDEFGH1234567890ABCO",
        "s_01J8ABCDEFGH1234567890ABCU",
        "s_invalid/path",
    ],
)
def test_reconcile_fails_closed_invalid_session_id(invalid_id: str):
    """Managed network with an invalid session ID fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(
        session_id=invalid_id,
        subnet_idx=1,
        name=f"sess_net_{invalid_id}",
    )
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "invalid session ID" in str(exc_info.value)


def test_reconcile_fails_closed_canonical_name_mismatch():
    """Network whose name does not match sess_net_{session_id} fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(
        SESSION_ID_1, subnet_idx=1, name="sess_net_wrong_name_123"
    )
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "does not match expected canonical name" in str(exc_info.value)


def test_reconcile_fails_closed_missing_or_corrupt_ipam():
    """Network with corrupt or missing IPAM configuration fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(SESSION_ID_1, subnet_idx=1, corrupt_ipam=True)
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "missing IPAM subnet configuration" in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_subnet",
    [
        "invalid_cidr_string",
        "10.0.1.0/24",  # prefix mismatch (expected 172.30)
        "172.30.1.0/16",  # prefixlen mismatch (expected /24)
        "172.30.1.0/28",  # prefixlen mismatch (expected /24)
        "172.30.0.0/24",  # index 0 is out of range [1, 254]
        "172.30.255.0/24",  # index 255 is out of range [1, 254]
        "172.30.999.0/24",  # index 999 is out of range [1, 254]
    ],
)
def test_reconcile_fails_closed_invalid_subnet(bad_subnet: str):
    """Managed network with invalid subnet CIDR or prefix/range fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(
        SESSION_ID_1,
        subnet_idx=1,
        override_subnet=bad_subnet,
        include_subnet_label=False,
    )
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool(base_prefix="172.30", max_subnets=254)
    with pytest.raises(DockerResourceError):
        pool.reconcile(mock_client)


def test_reconcile_fails_closed_label_ipam_subnet_mismatch():
    """Network where label subnet conflicts with IPAM subnet fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    bad_net = make_mock_network(
        SESSION_ID_1,
        subnet_idx=1,
        override_subnet="172.30.1.0/24",
    )
    # Tamper with the label to create a mismatch
    bad_net.attrs["Labels"]["overwatch.subnet"] = "172.30.2.0/24"
    mock_client.networks.list.return_value = [bad_net]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "Subnet mismatch in network" in str(exc_info.value)


def test_reconcile_fails_closed_duplicate_session_id():
    """Two Docker networks claiming the same session ID fail closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=1)
    net2 = make_mock_network(SESSION_ID_1, subnet_idx=2)
    mock_client.networks.list.return_value = [net1, net2]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "Duplicate session ID" in str(exc_info.value)


def test_reconcile_fails_closed_duplicate_subnet_index():
    """Two Docker networks sharing the same subnet index fail closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=1)
    net2 = make_mock_network(SESSION_ID_2, subnet_idx=1)
    mock_client.networks.list.return_value = [net1, net2]

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "Duplicate subnet index" in str(exc_info.value)


def test_reconcile_fails_closed_in_memory_conflict():
    """Docker network conflicting with in-memory allocation fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=2)
    mock_client.networks.list.return_value = [net1]

    pool = SubnetPool()
    # In-memory already allocated SESSION_ID_1 to index 1
    pool._allocated[SESSION_ID_1] = 1

    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "subnet index mismatch" in str(exc_info.value)


def test_reconcile_fails_closed_docker_api_error():
    """Docker API error during listing networks fails closed."""
    mock_client = MagicMock(spec=docker.DockerClient)
    mock_client.networks.list.side_effect = docker.errors.APIError(
        "Docker daemon error"
    )

    pool = SubnetPool()
    with pytest.raises(DockerResourceError) as exc_info:
        pool.reconcile(mock_client)

    assert "Failed to query existing Docker networks" in str(exc_info.value)


def test_subnet_pool_exhaustion_fails_closed():
    """Allocating beyond pool capacity fails closed with DockerResourceError."""
    pool = SubnetPool(max_subnets=2)

    # Allocate slot 1 and 2
    sub1, _, _, _ = pool.allocate(SESSION_ID_1)
    sub2, _, _, _ = pool.allocate(SESSION_ID_2)
    assert sub1 == "172.30.1.0/24"
    assert sub2 == "172.30.2.0/24"

    # Attempt 3rd allocation when max_subnets=2
    with pytest.raises(DockerResourceError) as exc_info:
        pool.allocate(SESSION_ID_3)

    assert "Subnet pool exhausted" in str(exc_info.value)


def test_subnet_pool_exhaustion_after_reconciliation():
    """Pool exhaustion occurs when existing Docker networks fill pool capacity."""
    mock_client = MagicMock(spec=docker.DockerClient)
    net1 = make_mock_network(SESSION_ID_1, subnet_idx=1)
    net2 = make_mock_network(SESSION_ID_2, subnet_idx=2)
    mock_client.networks.list.return_value = [net1, net2]

    pool = SubnetPool(max_subnets=2)
    pool.reconcile(mock_client)

    assert pool.available_count == 0
    with pytest.raises(DockerResourceError) as exc_info:
        pool.allocate(SESSION_ID_3)

    assert "Subnet pool exhausted" in str(exc_info.value)


def test_manager_create_session_reconciles_and_avoids_existing_subnets(
    mock_docker, repo_profiles_dir
):
    """create_session reconciles before allocating, never reassigning active subnets."""
    # Pre-populate Docker with an existing network for SESSION_ID_1 on subnet 1
    existing_net = make_mock_network(SESSION_ID_1, subnet_idx=1)
    mock_docker.networks.list.return_value = [existing_net]

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    # Create SESSION_ID_2: should reconcile, avoid subnet 1, and allocate subnet 2
    info2 = mgr.create_session(SESSION_ID_2, "coding")
    assert info2.session_id == SESSION_ID_2
    assert info2.subnet == "172.30.2.0/24"
    assert info2.proxy_ip == "172.30.2.2"
    assert info2.ip_address == "172.30.2.3"

    # Attempting to create SESSION_ID_1 again should fail closed
    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "already has an active network allocation" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 11. Phase 1 Proxy Integration Tests
# ---------------------------------------------------------------------------
def test_proxy_lookup_and_connect_on_creation(mock_docker, repo_profiles_dir):
    """create_session looks up overwatch-egress-proxy and connects it to the network."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    # Proxy container looked up
    mock_docker.containers.get.assert_any_call(DEFAULT_PROXY_CONTAINER_NAME)

    # Proxy container connected to session network at .2 with alias egress-proxy
    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.networks.create.return_value.connect.assert_called_once_with(
        mock_proxy,
        aliases=[DEFAULT_PROXY_NETWORK_ALIAS],
        ipv4_address="172.30.1.2",
    )


def test_proxy_environment_variables_configured(mock_docker, repo_profiles_dir):
    """Session container receives HTTP_PROXY and HTTPS_PROXY pointing to proxy."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    _, kwargs = mock_docker.containers.create.call_args
    env = kwargs["environment"]
    assert env["HTTP_PROXY"] == DEFAULT_PROXY_URL
    assert env["HTTPS_PROXY"] == DEFAULT_PROXY_URL
    assert env["http_proxy"] == DEFAULT_PROXY_URL
    assert env["https_proxy"] == DEFAULT_PROXY_URL


def test_registration_before_start_ordering(mock_docker, repo_profiles_dir):
    """Allowlist must be registered with policy manager before container starts."""
    call_order: list[str] = []

    orig_create = mock_docker.containers.create.side_effect
    mock_container = mock_docker.containers.create.return_value

    def create_side_effect(**kwargs):
        call_order.append("container_created")
        return orig_create(**kwargs)

    mock_docker.containers.create.side_effect = create_side_effect

    def start_side_effect():
        call_order.append("container_started")

    mock_container.start.side_effect = start_side_effect

    mock_epm = MagicMock(spec=EgressPolicyManager)

    def add_session_side_effect(*args, **kwargs):
        call_order.append("policy_registered")
        # Ensure container was NOT started yet when policy registration executes
        assert "container_started" not in call_order
        assert "container_created" in call_order

    mock_epm.add_session.side_effect = add_session_side_effect

    mgr = SessionContainerManager(
        docker_client=mock_docker,
        profiles_dir=repo_profiles_dir,
        egress_policy_manager=mock_epm,
    )
    mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert call_order == [
        "container_created",
        "policy_registered",
        "container_started",
    ]


def test_registration_failure_leaves_container_unstarted_and_rolls_back(
    mock_docker, repo_profiles_dir
):
    """Registration or reload failure leaves container unstarted and rolls back."""
    mock_epm = MagicMock(spec=EgressPolicyManager)
    mock_epm.add_session.side_effect = EgressPolicyPersistenceError(
        "Squid configuration reload failure"
    )

    mgr = SessionContainerManager(
        docker_client=mock_docker,
        profiles_dir=repo_profiles_dir,
        egress_policy_manager=mock_epm,
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert "Failed to register egress allowlist policy" in str(exc_info.value)

    # Container must never have started (proxy default-deny remains effective)
    mock_docker.containers.create.return_value.start.assert_not_called()

    # Container removed force=True
    mock_docker.containers.create.return_value.remove.assert_called_once_with(
        force=True
    )

    # Proxy disconnected from session network
    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.networks.create.return_value.disconnect.assert_called_once_with(
        mock_proxy, force=True
    )

    # Network and volume removed
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


def test_proxy_not_found_fails_closed_and_rolls_back(mock_docker, repo_profiles_dir):
    """If egress proxy container is missing, fail closed without creating container."""

    def get_container_side_effect(name_or_id):
        if name_or_id == DEFAULT_PROXY_CONTAINER_NAME:
            raise docker.errors.NotFound("Proxy container not found")
        return MagicMock()

    mock_docker.containers.get.side_effect = get_container_side_effect

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Egress proxy container 'overwatch-egress-proxy' not found" in str(
        exc_info.value
    )

    # Session container never created
    mock_docker.containers.create.assert_not_called()

    # Network and volume cleaned up
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


def test_proxy_not_running_fails_closed(mock_docker, repo_profiles_dir):
    """If egress proxy container exists but is not running, fail closed."""
    exited_proxy = MagicMock()
    exited_proxy.status = "exited"
    exited_proxy.attrs = {"State": {"Status": "exited"}}

    def get_container_side_effect(name_or_id):
        if name_or_id == DEFAULT_PROXY_CONTAINER_NAME:
            return exited_proxy
        return MagicMock()

    mock_docker.containers.get.side_effect = get_container_side_effect

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "is not running" in str(exc_info.value)
    mock_docker.containers.create.assert_not_called()
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


def test_proxy_connect_failure_fails_closed_and_rolls_back(
    mock_docker, repo_profiles_dir
):
    """If connecting proxy to session network fails, fail closed and rollback."""
    mock_docker.networks.create.return_value.connect.side_effect = (
        docker.errors.APIError("Network endpoint collision")
    )

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Failed to connect egress proxy" in str(exc_info.value)
    mock_docker.containers.create.assert_not_called()
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


def test_proxy_disconnect_on_teardown(mock_docker, repo_profiles_dir):
    """teardown_session disconnects the proxy container from the session network."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    mgr.teardown_session(SESSION_ID_1)

    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.networks.get.return_value.disconnect.assert_any_call(
        mock_proxy, force=True
    )


def test_teardown_failure_still_removes_policy_and_disconnects_proxy(
    mock_docker, repo_profiles_dir
):
    """Cleanup failure on container still removes ACL policy and disconnects proxy."""
    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    mgr.create_session(SESSION_ID_1, "coding")

    # Simulate container removal failure
    session_container = mock_docker.containers.create.return_value
    session_container.remove.side_effect = docker.errors.APIError(
        "Container removal locked"
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.teardown_session(SESSION_ID_1)

    assert "Container removal locked" in str(exc_info.value)

    # ACL policy was still removed (no live session container with proxy access)
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None

    # Proxy disconnect was still executed
    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.networks.get.return_value.disconnect.assert_any_call(
        mock_proxy, force=True
    )


def test_custom_proxy_container_name(mock_docker, repo_profiles_dir):
    """SessionContainerManager respects custom proxy container name."""
    custom_proxy = MagicMock()
    custom_proxy.name = "my-custom-squid"
    custom_proxy.status = "running"
    custom_proxy.attrs = {
        "State": {"Status": "running"},
        "NetworkSettings": {"Networks": {}},
    }

    def get_container_side_effect(name_or_id):
        if name_or_id == "my-custom-squid":
            return custom_proxy
        return mock_docker.containers.create.return_value

    mock_docker.containers.get.side_effect = get_container_side_effect

    mgr = SessionContainerManager(
        docker_client=mock_docker,
        profiles_dir=repo_profiles_dir,
        proxy_container_name="my-custom-squid",
    )
    assert mgr.proxy_container_name == "my-custom-squid"

    mgr.create_session(SESSION_ID_1, "coding")
    mock_docker.containers.get.assert_any_call("my-custom-squid")
    mock_docker.networks.create.return_value.connect.assert_called_once_with(
        custom_proxy,
        aliases=[DEFAULT_PROXY_NETWORK_ALIAS],
        ipv4_address="172.30.1.2",
    )


# ---------------------------------------------------------------------------
# 12. Deterministic IPAM Verification & Fail-Closed Inspection Tests
# ---------------------------------------------------------------------------
def test_unstarted_container_with_ipam_config_before_start_succeeds(
    mock_docker, repo_profiles_dir
):
    """Docker create populates IPAMConfig before start, and IPAddress on start."""
    mock_container = mock_docker.containers.create.return_value

    def create_unstarted(**kwargs):
        net_name = kwargs["network"]
        mock_container.attrs = {
            "NetworkSettings": {
                "Networks": {
                    net_name: {
                        "IPAddress": "",
                        "IPAMConfig": {"IPv4Address": "172.30.1.3"},
                    }
                }
            }
        }
        return mock_container

    def start_populates_runtime_ip():
        nets = mock_container.attrs["NetworkSettings"]["Networks"]
        net_name = next(iter(nets))
        nets[net_name]["IPAddress"] = "172.30.1.3"

    mock_docker.containers.create.side_effect = create_unstarted
    mock_container.start.side_effect = start_populates_runtime_ip

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )
    info = mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert info.proxy_ip == "172.30.1.2"
    assert info.ip_address == "172.30.1.3"
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1).source_ip == "172.30.1.3"
    mock_container.start.assert_called_once()


@pytest.mark.parametrize(
    "bad_networks",
    [
        {},  # network entry missing entirely
        {f"sess_net_{SESSION_ID_1}": {}},  # empty endpoint dict
        {f"sess_net_{SESSION_ID_1}": {"IPAddress": ""}},  # empty runtime IP, no IPAM
        {
            f"sess_net_{SESSION_ID_1}": {
                "IPAddress": "",
                "IPAMConfig": {"IPv4Address": ""},
            }
        },  # both empty
        {
            f"sess_net_{SESSION_ID_1}": {
                "IPAddress": "172.30.1.99",
            }
        },  # unexpected IP instead of .3
        {
            f"sess_net_{SESSION_ID_1}": {
                "IPAddress": "172.30.1.2",
            }
        },  # collided with proxy .2 instead of .3
        {
            f"sess_net_{SESSION_ID_1}": {
                "IPAddress": "",
                "IPAMConfig": {"IPv4Address": "172.30.1.50"},
            }
        },  # unexpected IPAMConfig IP
    ],
)
def test_empty_or_unexpected_session_ip_inspection_fails_closed(
    mock_docker, repo_profiles_dir, bad_networks
):
    """Missing, empty, or unexpected session container IP fails closed."""
    mock_container = mock_docker.containers.create.return_value

    def create_with_bad_ip(**kwargs):
        mock_container.attrs = {"NetworkSettings": {"Networks": bad_networks}}
        return mock_container

    mock_docker.containers.create.side_effect = create_with_bad_ip

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError):
        mgr.create_session(SESSION_ID_1, "coding", start=True)

    # Never started container or registered ACL policy
    mock_container.start.assert_not_called()
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None

    # Full rollback executed
    mock_container.remove.assert_called_once_with(force=True)
    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)
    mock_docker.networks.create.return_value.disconnect.assert_called_once_with(
        mock_proxy, force=True
    )
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


@pytest.mark.parametrize(
    "bad_proxy_networks",
    [
        {},  # proxy network metadata missing
        {f"sess_net_{SESSION_ID_1}": {"IPAddress": ""}},  # empty proxy IP
        {f"sess_net_{SESSION_ID_1}": {"IPAddress": "172.30.1.9"}},  # wrong proxy IP
    ],
)
def test_empty_or_unexpected_proxy_ip_inspection_fails_closed(
    mock_docker, repo_profiles_dir, bad_proxy_networks
):
    """Missing, empty, or unexpected proxy IP after connect fails closed."""
    mock_proxy = mock_docker.containers.get(DEFAULT_PROXY_CONTAINER_NAME)

    def connect_with_bad_ip(container, *args, **kwargs):
        container.attrs["NetworkSettings"]["Networks"] = bad_proxy_networks

    mock_docker.networks.create.return_value.connect.side_effect = connect_with_bad_ip

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError):
        mgr.create_session(SESSION_ID_1, "coding")

    mock_docker.containers.create.assert_not_called()
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None
    mock_docker.networks.create.return_value.disconnect.assert_called_once_with(
        mock_proxy, force=True
    )
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0


def test_session_container_reload_failure_fails_closed(mock_docker, repo_profiles_dir):
    """If container.reload() raises while inspecting IP, fail closed."""
    mock_container = mock_docker.containers.create.return_value
    mock_container.reload.side_effect = docker.errors.APIError("Daemon inspect error")

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding")

    assert "Failed to reload session container" in str(exc_info.value)
    mock_container.start.assert_not_called()
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None
    assert mgr.subnet_pool.active_count == 0


def test_started_container_missing_runtime_ip_fails_closed_and_rolls_back(
    mock_docker, repo_profiles_dir
):
    """If container starts without active IPAddress, fail closed and roll back."""
    mock_container = mock_docker.containers.create.return_value

    def create_unstarted(**kwargs):
        net_name = kwargs["network"]
        mock_container.attrs = {
            "NetworkSettings": {
                "Networks": {
                    net_name: {
                        "IPAddress": "",
                        "IPAMConfig": {"IPv4Address": "172.30.1.3"},
                    }
                }
            }
        }
        return mock_container

    # start() succeeds on mock, leaving IPAddress=""
    mock_docker.containers.create.side_effect = create_unstarted

    mgr = SessionContainerManager(
        docker_client=mock_docker, profiles_dir=repo_profiles_dir
    )

    with pytest.raises(DockerResourceError) as exc_info:
        mgr.create_session(SESSION_ID_1, "coding", start=True)

    assert "no active IPAddress" in str(exc_info.value)
    assert mgr.egress_policy_manager.get_session(SESSION_ID_1) is None
    mock_container.remove.assert_called_once_with(force=True)
    mock_docker.networks.create.return_value.remove.assert_called_once()
    mock_docker.volumes.create.return_value.remove.assert_called_once_with(force=True)
    assert mgr.subnet_pool.active_count == 0

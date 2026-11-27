"""Unit tests for the environment profile loader (F-09)."""

from pathlib import Path

import pytest
from control_plane.contracts import ErrorCode, Profile
from control_plane.sessions.profile_loader import (
    DEFAULT_PROFILES_DIR,
    ProfileLoader,
    ProfileLoaderError,
    ProfileNotFoundError,
    ProfilePathTraversalError,
    ProfileValidationError,
    list_profiles,
    load_profile,
    resolve_profile_path,
)


def test_exception_hierarchy():
    """All loader exceptions inherit from ProfileLoaderError with error codes."""
    assert issubclass(ProfileNotFoundError, ProfileLoaderError)
    assert issubclass(ProfilePathTraversalError, ProfileLoaderError)
    assert issubclass(ProfileValidationError, ProfileLoaderError)

    assert ProfileNotFoundError.code == ErrorCode.POLICY_BLOCK
    assert ProfilePathTraversalError.code == ErrorCode.PATH_JAIL
    assert ProfileValidationError.code == ErrorCode.ARG_INVALID


def test_load_profile_success_full(tmp_path: Path):
    """Load a complete profile matching CONTRACTS.md Section 6."""
    profile_content = """
name: coding
role: coder
system_prompt_file: prompts/coder.md
image: pocket/py-node:1.0
resources:
  cpus: 2.0
  memory: 2g
  pids: 512
  ttl_minutes: 60
budget:
  max_tool_calls: 200
  max_usd: 1.00
mcp_servers:
  terminal: {}
  files:
    root: /workspace
  desktop: {}
egress_allowlist:
  - pypi.org
  - files.pythonhosted.org
  - registry.npmjs.org
  - github.com
tool_overrides:
  terminal.run:
    tier: sensitive
"""
    profile_file = tmp_path / "coding.yaml"
    profile_file.write_text(profile_content, encoding="utf-8")

    profile = load_profile("coding", profiles_dir=tmp_path)
    assert isinstance(profile, Profile)
    assert profile.name == "coding"
    assert profile.role == "coder"
    assert profile.system_prompt_file == "prompts/coder.md"
    assert profile.image == "pocket/py-node:1.0"
    assert profile.resources.cpus == 2.0
    assert profile.resources.memory == "2g"
    assert profile.resources.pids == 512
    assert profile.resources.ttl_minutes == 60
    assert profile.budget.max_tool_calls == 200
    assert profile.budget.max_usd == 1.00
    assert "terminal" in profile.mcp_servers
    assert profile.mcp_servers["files"] == {"root": "/workspace"}
    assert "github.com" in profile.egress_allowlist
    assert profile.tool_overrides["terminal.run"] == {"tier": "sensitive"}


def test_load_profile_success_minimal_defaults(tmp_path: Path):
    """Minimal profile should apply contract default values."""
    profile_content = """
name: general
role: assistant
image: pocket/py-node:1.0
"""
    profile_file = tmp_path / "general.yaml"
    profile_file.write_text(profile_content, encoding="utf-8")

    profile = load_profile("general", profiles_dir=tmp_path)
    assert profile.name == "general"
    assert profile.role == "assistant"
    assert profile.image == "pocket/py-node:1.0"
    assert profile.system_prompt_file is None
    # Check default resources and budget
    assert profile.resources.cpus == 1.0
    assert profile.resources.memory == "1g"
    assert profile.resources.pids == 256
    assert profile.resources.ttl_minutes == 60
    assert profile.budget.max_tool_calls == 200
    assert profile.budget.max_usd == 1.00
    assert profile.mcp_servers == {}
    assert profile.egress_allowlist == []
    assert profile.tool_overrides == {}


def test_load_profile_name_resolution_and_extensions(tmp_path: Path):
    """Profile can be loaded by name with or without .yaml or .yml."""
    yaml_content = "name: alpha\nrole: assistant\nimage: pocket/base:1.0\n"
    (tmp_path / "alpha.yaml").write_text(yaml_content, encoding="utf-8")

    yml_content = "name: beta\nrole: routine\nimage: pocket/base:1.0\n"
    (tmp_path / "beta.yml").write_text(yml_content, encoding="utf-8")

    # Without extension
    p_alpha = load_profile("alpha", profiles_dir=tmp_path)
    assert p_alpha.name == "alpha"

    # With .yaml
    p_alpha_ext = load_profile("alpha.yaml", profiles_dir=tmp_path)
    assert p_alpha_ext.name == "alpha"

    # Without extension (.yml file)
    p_beta = load_profile("beta", profiles_dir=tmp_path)
    assert p_beta.name == "beta"

    # With .yml
    p_beta_ext = load_profile("beta.yml", profiles_dir=tmp_path)
    assert p_beta_ext.name == "beta"


def test_load_profile_not_found(tmp_path: Path):
    """Unknown profile must fail closed with ProfileNotFoundError and POLICY_BLOCK."""
    with pytest.raises(ProfileNotFoundError) as exc_info:
        load_profile("nonexistent_profile", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK
    assert "nonexistent_profile" in str(exc_info.value)


def test_load_profile_missing_directory(tmp_path: Path):
    """Non-existent profiles directory must fail closed with POLICY_BLOCK."""
    missing_dir = tmp_path / "does_not_exist"
    with pytest.raises(ProfileNotFoundError) as exc_info:
        load_profile("test", profiles_dir=missing_dir)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK


def test_load_profile_is_a_directory(tmp_path: Path):
    """A directory named like a profile must not be loaded as a file."""
    fake_profile_dir = tmp_path / "subdir.yaml"
    fake_profile_dir.mkdir()

    with pytest.raises(ProfileNotFoundError) as exc_info:
        load_profile("subdir", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK


def test_load_profile_empty_name(tmp_path: Path):
    """Empty or whitespace profile names must fail closed with POLICY_BLOCK."""
    with pytest.raises(ProfileNotFoundError) as exc_info:
        load_profile("", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK

    with pytest.raises(ProfileNotFoundError) as exc_info:
        load_profile("   ", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK


def test_reject_names_containing_path_separators(tmp_path: Path):
    """Names with '/' or '\\' rejected with PATH_JAIL even inside directory."""
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    nested_file = sub_dir / "nested.yaml"
    nested_file.write_text(
        "name: nested\nrole: assistant\nimage: pocket/base:1.0\n",
        encoding="utf-8",
    )

    # Reject forward slash even though sub/nested.yaml is physically inside tmp_path
    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile("sub/nested", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.PATH_JAIL
    assert "path separators" in str(exc_info.value)

    # Reject backslash
    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile(r"sub\nested", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.PATH_JAIL
    assert "path separators" in str(exc_info.value)

    # Reject leading or relative slash paths
    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile("/coding", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.PATH_JAIL

    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile("./coding", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_reject_path_traversal_dot_dot(tmp_path: Path):
    """Traversal using '..' must be rejected with ProfilePathTraversalError."""
    outside_dir = tmp_path.parent / "outside_profiles"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "secret.yaml"
    outside_file.write_text(
        "name: secret\nrole: coder\nimage: pocket/base:1.0\n",
        encoding="utf-8",
    )

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()

    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile("../outside_profiles/secret", profiles_dir=profiles_dir)
    assert exc_info.value.code == ErrorCode.PATH_JAIL

    with pytest.raises(ProfilePathTraversalError):
        load_profile("../../secret", profiles_dir=profiles_dir)

    with pytest.raises(ProfilePathTraversalError):
        load_profile("sub/../../secret", profiles_dir=profiles_dir)


def test_reject_path_traversal_absolute_path(tmp_path: Path):
    """Absolute paths must be rejected with ProfilePathTraversalError."""
    outside_file = tmp_path.parent / "outside.yaml"
    outside_file.write_text(
        "name: outside\nrole: assistant\nimage: pocket/base:1.0\n",
        encoding="utf-8",
    )

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()

    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile(str(outside_file), profiles_dir=profiles_dir)
    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_reject_path_traversal_symlink_escape(tmp_path: Path):
    """Symlinks escaping profiles directory must be rejected."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "leaked.yaml"
    outside_file.write_text(
        "name: leaked\nrole: coder\nimage: pocket/base:1.0\n",
        encoding="utf-8",
    )

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    symlink_file = profiles_dir / "leaked.yaml"
    symlink_file.symlink_to(outside_file)

    with pytest.raises(ProfilePathTraversalError) as exc_info:
        load_profile("leaked", profiles_dir=profiles_dir)
    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_reject_path_traversal_null_byte(tmp_path: Path):
    """Null bytes in profile name must be rejected."""
    with pytest.raises(ProfilePathTraversalError):
        load_profile("test\x00escape", profiles_dir=tmp_path)


def test_reject_invalid_yaml_syntax(tmp_path: Path):
    """Malformed YAML syntax must raise ProfileValidationError."""
    bad_yaml = "name: [unclosed list"
    (tmp_path / "broken.yaml").write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("broken", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.ARG_INVALID


def test_reject_yaml_not_a_mapping(tmp_path: Path):
    """Empty YAML, lists, or scalars must raise ProfileValidationError."""
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("empty", profiles_dir=tmp_path)
    assert exc_info.value.code == ErrorCode.ARG_INVALID

    (tmp_path / "list.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("list", profiles_dir=tmp_path)

    (tmp_path / "scalar.yaml").write_text("just_a_string", encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("scalar", profiles_dir=tmp_path)


def test_reject_missing_required_schema_fields(tmp_path: Path):
    """Missing required fields (image, role) must raise ProfileValidationError."""
    # Missing image
    no_image = "name: missing_img\nrole: coder\n"
    (tmp_path / "missing_img.yaml").write_text(no_image, encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("missing_img", profiles_dir=tmp_path)

    # Missing role
    no_role = "name: missing_role\nimage: pocket/base:1.0\n"
    (tmp_path / "missing_role.yaml").write_text(no_role, encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("missing_role", profiles_dir=tmp_path)


def test_reject_invalid_role(tmp_path: Path):
    """Invalid role literal must raise ProfileValidationError."""
    bad_role = "name: bad_role\nrole: superuser\nimage: pocket/base:1.0\n"
    (tmp_path / "bad_role.yaml").write_text(bad_role, encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("bad_role", profiles_dir=tmp_path)


def test_reject_extra_fields_forbidden(tmp_path: Path):
    """Extra fields must be rejected (extra='forbid' on Profile model)."""
    extra_field = (
        "name: extra\n"
        "role: assistant\n"
        "image: pocket/base:1.0\n"
        "unrecognized_key: true\n"
    )
    (tmp_path / "extra.yaml").write_text(extra_field, encoding="utf-8")
    with pytest.raises(ProfileValidationError):
        load_profile("extra", profiles_dir=tmp_path)


def test_reject_profile_name_mismatch(tmp_path: Path):
    """Profile name inside YAML must match the filename stem."""
    content = "name: actual_name\nrole: coder\nimage: pocket/base:1.0\n"
    (tmp_path / "mismatched_filename.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ProfileValidationError) as exc_info:
        load_profile("mismatched_filename", profiles_dir=tmp_path)
    assert "does not match filename stem" in str(exc_info.value)


def test_profile_loader_class_and_list_profiles(tmp_path: Path):
    """ProfileLoader class instance and list_profiles discovery functionality."""
    p1 = "name: alpha\nrole: assistant\nimage: pocket/base:1.0\n"
    (tmp_path / "alpha.yaml").write_text(p1, encoding="utf-8")

    p2 = "name: beta\nrole: routine\nimage: pocket/base:1.0\n"
    (tmp_path / "beta.yml").write_text(p2, encoding="utf-8")

    # Non-profile files that should be ignored
    (tmp_path / ".hidden.yaml").write_text(
        "name: hidden\nrole: assistant\nimage: x\n", encoding="utf-8"
    )
    (tmp_path / "readme.txt").write_text("not a profile", encoding="utf-8")

    loader = ProfileLoader(profiles_dir=tmp_path)
    assert loader.list_profiles() == ["alpha", "beta"]
    assert list_profiles(profiles_dir=tmp_path) == ["alpha", "beta"]

    profile = loader.load("alpha")
    assert profile.name == "alpha"
    assert loader.resolve_path("beta") == (tmp_path / "beta.yml").resolve()


def test_default_profiles_dir_configured():
    """Default profiles directory resolves to repo-relative /profiles."""
    assert DEFAULT_PROFILES_DIR.name == "profiles"
    resolved_path = resolve_profile_path("coding")
    assert resolved_path.parent == DEFAULT_PROFILES_DIR


def test_load_repo_coding_profile():
    """Load repository's profiles/coding.yaml and verify all contract values."""
    profile = load_profile("coding")
    assert profile.name == "coding"
    assert profile.role == "coder"
    assert profile.system_prompt_file == "prompts/coder.md"
    assert profile.image == "pocket/py-node:1.0"
    assert profile.resources.cpus == 2.0
    assert profile.resources.memory == "2g"
    assert profile.resources.pids == 512
    assert profile.resources.ttl_minutes == 60
    assert profile.budget.max_tool_calls == 200
    assert profile.budget.max_usd == 1.00
    assert "terminal" in profile.mcp_servers
    assert profile.mcp_servers["terminal"] == {}
    assert profile.mcp_servers["files"] == {"root": "/workspace"}
    assert profile.mcp_servers["desktop"] == {}
    assert profile.egress_allowlist == [
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "github.com",
    ]
    assert profile.tool_overrides == {
        "terminal.run": {"tier": "sensitive"},
    }

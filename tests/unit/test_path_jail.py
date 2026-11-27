"""Unit tests for Phase 2 workspace path-jail helper (docs/TESTING.md §1, §3)."""

from pathlib import Path

import pytest
from control_plane.contracts import ErrorBody, ErrorCode
from control_plane.gateway.path_jail import (
    DEFAULT_WORKSPACE_ROOT,
    PathJail,
    PathJailError,
    normalize_workspace_relative_path,
    resolve_workspace_path,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Create a realistic isolated session workspace tree."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.md").write_text("# Session Workspace\n", encoding="utf-8")
    src = ws / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (ws / "sub dir").mkdir()
    (ws / "sub dir" / "notes.txt").write_text("notes\n", encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# 1. Valid Relative Paths
# ---------------------------------------------------------------------------
def test_valid_relative_paths_resolve_inside_workspace(workspace_dir: Path):
    """Valid relative paths resolve strictly inside workspace_root."""
    resolved_root = workspace_dir.resolve()

    assert resolve_workspace_path(".", workspace_dir) == resolved_root
    assert resolve_workspace_path("./", workspace_dir) == resolved_root
    assert resolve_workspace_path("README.md", workspace_dir) == (
        resolved_root / "README.md"
    )
    assert resolve_workspace_path("./src/main.py", workspace_dir) == (
        resolved_root / "src" / "main.py"
    )
    assert resolve_workspace_path("src/./main.py", workspace_dir) == (
        resolved_root / "src" / "main.py"
    )
    assert resolve_workspace_path("sub dir/notes.txt", workspace_dir) == (
        resolved_root / "sub dir" / "notes.txt"
    )


def test_nonexistent_nested_target_allowed_for_writes(workspace_dir: Path):
    """Relative path for a file not yet on disk resolves within workspace."""
    resolved_root = workspace_dir.resolve()
    target = resolve_workspace_path("new_dir/deep/output.json", workspace_dir)
    assert target == resolved_root / "new_dir" / "deep" / "output.json"
    assert target.is_relative_to(resolved_root)


def test_default_workspace_root_is_slash_workspace():
    """Default root matches /workspace and works with relative paths."""
    assert DEFAULT_WORKSPACE_ROOT == Path("/workspace")
    jail = PathJail()
    assert jail.root == Path("/workspace")
    assert jail.resolve("src/app.py") == Path("/workspace/src/app.py")
    assert jail.container_path("./src/app.py") == "/workspace/src/app.py"
    assert jail.container_path(".") == "/workspace"


def test_valid_unicode_filenames_permitted(workspace_dir: Path):
    """Legitimate UTF-8 filenames without slash/dot homoglyphs succeed."""
    resolved_root = workspace_dir.resolve()
    target = resolve_workspace_path("docs/café_résumé.md", workspace_dir)
    assert target == resolved_root / "docs" / "café_résumé.md"


def test_path_jail_class_helpers(workspace_dir: Path):
    """PathJail exposes resolve, relative_path, and container_path cleanly."""
    jail = PathJail(workspace_dir)
    assert jail.root == workspace_dir.resolve()
    assert jail.relative_path("./src/./main.py") == "src/main.py"
    assert jail.relative_path(".") == "."
    assert normalize_workspace_relative_path("README.md", workspace_dir) == "README.md"
    assert jail.container_path("src/main.py") == "/workspace/src/main.py"


# ---------------------------------------------------------------------------
# 2. Traversal Rejection (docs/TESTING.md B4 & SECURITY.md §4)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "traversal_path",
    [
        "..",
        "../",
        "../etc/passwd",
        "../../etc/shadow",
        "src/../../etc/passwd",
        "src/../main.py",
        "nonexistent/../../etc/passwd",
        "./../secret",
        "a/b/c/../../../..",
    ],
)
def test_traversal_paths_rejected_with_path_jail_code(
    workspace_dir: Path, traversal_path: str
):
    """Traversal attempts fail closed with PATH_JAIL."""
    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path(traversal_path, workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL
    body = exc_info.value.to_error_body()
    assert isinstance(body, ErrorBody)
    assert body.code == ErrorCode.PATH_JAIL


# ---------------------------------------------------------------------------
# 3. Absolute Paths Rejection (docs/CONTRACTS.md §4 Relative-Path Contract)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "abs_path",
    [
        "/",
        "/etc/passwd",
        "/etc/shadow",
        "/workspace",
        "/workspace/README.md",
        "/tmp/evil",
        "//etc/passwd",
        " /etc/passwd",
        "C:\\Windows\\System32",
        "C:/workspace/file.txt",
        "C:relative_drive.txt",
    ],
)
def test_absolute_paths_rejected_with_path_jail_code(
    workspace_dir: Path, abs_path: str
):
    """Absolute paths violate the relative-path contract and return PATH_JAIL."""
    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path(abs_path, workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_workspace_host_absolute_path_also_rejected(workspace_dir: Path):
    """Even the host workspace path string itself is rejected as absolute."""
    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path(str(workspace_dir / "README.md"), workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


# ---------------------------------------------------------------------------
# 4. Null Bytes, Control Chars, Backslashes, and Unicode Tricks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "malicious_path",
    [
        "\x00",
        "file.txt\x00.png",
        "src/\x00main.py",
        "README.md\u0000",
        "src\n/main.py",
        "src\r/main.py",
        "src\tmain.py",
        "",
        "   ",
        "src\\..\\..\\etc\\passwd",
        "\\etc\\passwd",
        "\uff0e\uff0e/etc/passwd",
        "\uff0fetc\uff0fpasswd",
        "src\u2215..\u2215etc",
        "src\u2044..\u2044etc",
        "\uff0e\uff0e\uff3cetc",
    ],
)
def test_null_bytes_and_obfuscation_rejected(workspace_dir: Path, malicious_path: str):
    """Null bytes, control chars, backslashes, and Unicode tricks return PATH_JAIL."""
    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path(malicious_path, workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


@pytest.mark.parametrize("bad_input", [None, 123, ["README.md"], {"path": "README.md"}])
def test_non_string_path_rejected(workspace_dir: Path, bad_input):
    """Non-string path arguments fail closed with PATH_JAIL."""
    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path(bad_input, workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


# ---------------------------------------------------------------------------
# 5. Symlink Escapes vs Safe Internal Symlinks (docs/TESTING.md B5)
# ---------------------------------------------------------------------------
def test_symlink_to_outside_root_rejected(workspace_dir: Path, tmp_path: Path):
    """Symlink pointing outside workspace returns PATH_JAIL."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_secret = outside_dir / "secret.txt"
    outside_secret.write_text("top-secret\n", encoding="utf-8")

    # B5: symlink in workspace pointing to '/'
    (workspace_dir / "root_link").symlink_to("/")
    # Symlink pointing to outside file and outside directory
    (workspace_dir / "file_link").symlink_to(outside_secret)
    (workspace_dir / "dir_link").symlink_to(outside_dir)

    for escaped in (
        "root_link",
        "root_link/etc/passwd",
        "file_link",
        "dir_link",
        "dir_link/secret.txt",
        "dir_link/new_file.txt",
    ):
        with pytest.raises(PathJailError) as exc_info:
            resolve_workspace_path(escaped, workspace_dir)
        assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_dangling_and_chained_outside_symlinks_rejected(
    workspace_dir: Path, tmp_path: Path
):
    """Dangling and multi-hop symlinks resolving outside workspace return PATH_JAIL."""
    outside_missing = tmp_path / "does_not_exist" / "secret.txt"
    (workspace_dir / "dangling_out").symlink_to(outside_missing)
    (workspace_dir / "hop1").symlink_to("hop2")
    (workspace_dir / "hop2").symlink_to(tmp_path)

    for target in ("dangling_out", "hop1", "hop1/anything.txt"):
        with pytest.raises(PathJailError) as exc_info:
            resolve_workspace_path(target, workspace_dir)
        assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_symlink_loop_rejected_with_path_jail(workspace_dir: Path):
    """Cyclic symlinks fail closed with PATH_JAIL."""
    (workspace_dir / "loop_a").symlink_to("loop_b")
    (workspace_dir / "loop_b").symlink_to("loop_a")

    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path("loop_a", workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_symlink_bounce_outside_and_back_rejected(workspace_dir: Path):
    """Symlink escaping workspace in an intermediate component cannot bounce back."""
    parent_dir = workspace_dir.parent
    (workspace_dir / "escape_dir").symlink_to(parent_dir)

    with pytest.raises(PathJailError) as exc_info:
        resolve_workspace_path("escape_dir/workspace/README.md", workspace_dir)

    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_internal_symlink_within_workspace_allowed(workspace_dir: Path):
    """Symlink whose target stays inside workspace resolves to real internal path."""
    resolved_root = workspace_dir.resolve()
    (workspace_dir / "alias.py").symlink_to(Path("src") / "main.py")
    (workspace_dir / "src_link").symlink_to("src")

    assert resolve_workspace_path("alias.py", workspace_dir) == (
        resolved_root / "src" / "main.py"
    )
    assert resolve_workspace_path("src_link/main.py", workspace_dir) == (
        resolved_root / "src" / "main.py"
    )


def test_invalid_workspace_root_rejected(tmp_path: Path):
    """Invalid or non-directory workspace_root fails closed with PATH_JAIL."""
    regular_file = tmp_path / "not_a_dir.txt"
    regular_file.write_text("x", encoding="utf-8")

    for bad_root in ("", "  ", "ws\x00", regular_file, 123):
        with pytest.raises(PathJailError) as exc_info:
            resolve_workspace_path("README.md", bad_root)
        assert exc_info.value.code == ErrorCode.PATH_JAIL

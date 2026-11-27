"""Unit tests for control_plane.gateway.argument_validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from control_plane.contracts import ErrorCode
from control_plane.gateway.argument_validation import (
    ArgumentValidationError,
    ArgumentValidator,
    validate_tool_arguments,
)
from control_plane.gateway.path_jail import PathJail
from control_plane.gateway.policy_loader import load_policy


def test_valid_arguments_for_all_mvp_tools(tmp_path: Path) -> None:
    """All 13 MVP tools validate with their documented CONTRACT.md §4 schemas."""
    policy = load_policy()
    jail = PathJail(tmp_path)
    validator = ArgumentValidator(path_jail=jail)

    valid_cases: list[tuple[str, dict]] = [
        ("files.list", {"path": "."}),
        ("files.read", {"path": "src/app.py"}),
        ("files.read", {"path": "src/app.py", "max_bytes": 4096}),
        ("files.write", {"path": "out.txt", "content": "hello"}),
        ("files.delete", {"path": "out.txt"}),
        ("files.upload", {"path": "out.txt", "destination": "s3://bucket/out.txt"}),
        ("terminal.run", {"command": "pytest -q"}),
        (
            "terminal.run",
            {"command": "ls -la", "cwd": "subdir", "timeout_s": 10},
        ),
        ("browser.open", {"url": "https://pypi.org/project/pydantic/"}),
        ("browser.read_page", {}),
        ("browser.read_page", {"max_chars": 5000}),
        ("browser.screenshot", {}),
        ("desktop.screenshot", {}),
        ("desktop.click", {"x": 100, "y": 200}),
        ("desktop.click", {"x": 12.5, "y": 34.5, "button": "right"}),
        ("desktop.type", {"text": "hello world"}),
        ("desktop.key", {"combo": "ctrl+c"}),
    ]

    for tool, args in valid_cases:
        res = validator.validate(tool, args, tool_policy=policy.tools[tool])
        assert res.tool == tool


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # Missing required fields
        ("files.list", {}),
        ("files.read", {}),
        ("files.write", {"path": "a.txt"}),
        ("files.delete", {}),
        ("files.upload", {"path": "a.txt"}),
        ("terminal.run", {}),
        ("browser.open", {}),
        ("desktop.click", {"x": 10}),
        ("desktop.type", {}),
        ("desktop.key", {}),
        # Wrong value types (strict mode rejects coercions & booleans)
        ("files.list", {"path": 123}),
        ("files.read", {"path": "a.txt", "max_bytes": "100"}),
        ("files.read", {"path": "a.txt", "max_bytes": True}),
        ("files.read", {"path": "a.txt", "max_bytes": 0}),
        ("files.write", {"path": "a.txt", "content": 42}),
        ("terminal.run", {"command": "   "}),
        ("terminal.run", {"command": "ls", "timeout_s": -5}),
        ("terminal.run", {"command": "ls", "timeout_s": True}),
        ("browser.open", {"url": "ftp://example.com/file"}),
        ("browser.open", {"url": "not-a-url"}),
        ("browser.read_page", {"max_chars": 0}),
        ("desktop.click", {"x": -1, "y": 10}),
        ("desktop.click", {"x": True, "y": 10}),
        ("desktop.click", {"x": 10, "y": 20, "button": "middle"}),
        ("desktop.type", {"text": 99}),
        ("desktop.key", {"combo": "  "}),
        # Unknown tool
        ("unknown.tool", {"x": 1}),
    ],
)
def test_invalid_arguments_raise_arg_invalid(
    tmp_path: Path, tool: str, args: dict
) -> None:
    """Missing fields, wrong types, bad bounds, and unknown tools fail."""
    jail = PathJail(tmp_path)
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_tool_arguments(tool, args, path_jail=jail)
    assert exc_info.value.code == ErrorCode.ARG_INVALID


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("files.list", {"path": ".", "extra": 1}),
        ("files.read", {"path": "a.txt", "encoding": "utf-8"}),
        ("files.write", {"path": "a.txt", "content": "hi", "mode": "0777"}),
        ("terminal.run", {"command": "ls", "shell": True}),
        ("browser.screenshot", {"full_page": True}),
        ("desktop.screenshot", {"display": ":0"}),
        ("desktop.click", {"x": 1, "y": 2, "clicks": 2}),
    ],
)
def test_extra_fields_rejected(tmp_path: Path, tool: str, args: dict) -> None:
    """Unknown extra argument fields are strictly rejected with ARG_INVALID."""
    policy = load_policy()
    jail = PathJail(tmp_path)
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool, args, tool_policy=policy.tools[tool], path_jail=jail
        )
    assert exc_info.value.code == ErrorCode.ARG_INVALID


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("files.list", {"path": "../etc"}),
        ("files.read", {"path": "/etc/passwd"}),
        ("files.write", {"path": "/workspace/escape.txt", "content": "x"}),
        ("files.delete", {"path": "sub/../../secret"}),
        ("files.upload", {"path": "../secret", "destination": "https://example.com"}),
        ("terminal.run", {"command": "ls", "cwd": "../../etc"}),
    ],
)
def test_path_escapes_rejected_with_path_jail(
    tmp_path: Path, tool: str, args: dict
) -> None:
    """Path traversal and absolute paths fail with ErrorCode.PATH_JAIL."""
    policy = load_policy()
    jail = PathJail(tmp_path)
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_tool_arguments(
            tool, args, tool_policy=policy.tools[tool], path_jail=jail
        )
    assert exc_info.value.code == ErrorCode.PATH_JAIL


def test_symlink_escape_rejected_with_path_jail(tmp_path: Path) -> None:
    """A symlink pointing outside the workspace root is rejected with PATH_JAIL."""
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "leak.txt"
    link.symlink_to(outside)

    policy = load_policy()
    jail = PathJail(tmp_path)
    try:
        with pytest.raises(ArgumentValidationError) as exc_info:
            validate_tool_arguments(
                "files.read",
                {"path": "leak.txt"},
                tool_policy=policy.tools["files.read"],
                path_jail=jail,
            )
        assert exc_info.value.code == ErrorCode.PATH_JAIL
    finally:
        outside.unlink(missing_ok=True)


def test_max_arg_bytes_enforced(tmp_path: Path) -> None:
    """ToolPolicy.max_arg_bytes rejects oversized payloads with ARG_INVALID."""
    policy = load_policy()
    jail = PathJail(tmp_path)
    write_policy = policy.tools["files.write"]
    assert write_policy.max_arg_bytes == 1_000_000

    # Oversized content exceeds 1,000,000 bytes
    oversized = "a" * 1_000_001
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_tool_arguments(
            "files.write",
            {"path": "big.txt", "content": oversized},
            tool_policy=write_policy,
            path_jail=jail,
        )
    assert exc_info.value.code == ErrorCode.ARG_INVALID
    assert "max_arg_bytes" in exc_info.value.message


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "echo hi && rm -rf / --no-preserve-root",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/null",
        "cat data > /dev/sda",
    ],
)
def test_terminal_deny_patterns_rejected(tmp_path: Path, command: str) -> None:
    """terminal.run commands matching policy.yaml deny_patterns raise DENY_PATTERN."""
    policy = load_policy()
    jail = PathJail(tmp_path)
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_tool_arguments(
            "terminal.run",
            {"command": command},
            tool_policy=policy.tools["terminal.run"],
            path_jail=jail,
        )
    assert exc_info.value.code == ErrorCode.DENY_PATTERN

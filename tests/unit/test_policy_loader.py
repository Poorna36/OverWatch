"""Unit tests for control_plane.gateway.policy_loader and root policy.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from control_plane.contracts import ErrorCode, Policy, PolicyDefaults
from control_plane.gateway.policy_loader import (
    DEFAULT_POLICY_PATH,
    PolicyLoader,
    PolicyNotFoundError,
    PolicyValidationError,
    load_policy,
)


def test_load_repo_policy_yaml() -> None:
    """Load the repository root policy.yaml and verify documented contract fields."""
    assert DEFAULT_POLICY_PATH.name == "policy.yaml"
    assert DEFAULT_POLICY_PATH.is_file()

    policy = load_policy()
    assert isinstance(policy, Policy)
    assert policy.version == 1

    # Defaults match docs/CONTRACTS.md §5
    assert policy.defaults.unknown_tool == "block"
    assert policy.defaults.approval_timeout_s == 120
    assert policy.defaults.output_cap_bytes == 200000
    assert policy.defaults.tool_timeout_s == 30

    # Tiers
    assert set(policy.tiers.keys()) == {"read", "reversible", "sensitive", "forbidden"}
    assert policy.tiers["read"].decision == "allow"
    assert policy.tiers["read"].log is True
    assert policy.tiers["reversible"].decision == "allow"
    assert policy.tiers["reversible"].undo == "capture"
    assert policy.tiers["sensitive"].decision == "ask"
    assert policy.tiers["forbidden"].decision == "block"

    # Documented tools
    expected_tools = {
        "files.list",
        "files.read",
        "files.write",
        "files.delete",
        "files.upload",
        "terminal.run",
        "browser.open",
        "browser.read_page",
        "browser.screenshot",
        "desktop.screenshot",
        "desktop.click",
        "desktop.type",
        "desktop.key",
        "calendar.list_events",
        "calendar.create_event",
        "calendar.update_event",
        "calendar.delete_event",
        "gmail.search",
        "gmail.read_message",
        "gmail.create_draft",
        "gmail.send",
    }
    assert set(policy.tools.keys()) == expected_tools
    assert policy.tools["files.write"].max_arg_bytes == 1000000
    assert policy.tools["files.list"].path_jail == "/workspace"
    assert policy.tools["terminal.run"].timeout_s == 30
    assert policy.tools["terminal.run"].deny_patterns == [
        "rm -rf /",
        ":(){",
        "mkfs",
        "dd if=",
        "> /dev/sd",
    ]

    # Escalations
    assert len(policy.escalations) == 2
    assert policy.escalations[0].when == {
        "tool": "terminal.run",
        "matches": r"(curl|wget|ssh|scp)\b",
    }
    assert policy.escalations[0].decision == "ask"
    assert policy.escalations[0].reason == "network tool in terminal"
    assert policy.escalations[1].when == {"tool": "*", "output_bytes_gt": 200000}
    assert policy.escalations[1].action == "truncate"


def test_preserves_defaults_when_optional_sections_omitted(tmp_path: Path) -> None:
    """Omitting defaults or partial fields preserves Policy/PolicyDefaults defaults."""
    minimal = tmp_path / "policy.yaml"
    minimal.write_text(
        "version: 1\n"
        "defaults:\n"
        "  tool_timeout_s: 45\n"
        "tools:\n"
        "  files.read: { tier: read }\n",
        encoding="utf-8",
    )

    policy = PolicyLoader(minimal).load()
    assert policy.defaults.unknown_tool == PolicyDefaults().unknown_tool
    assert policy.defaults.approval_timeout_s == PolicyDefaults().approval_timeout_s
    assert policy.defaults.output_cap_bytes == PolicyDefaults().output_cap_bytes
    assert policy.defaults.tool_timeout_s == 45
    assert policy.tiers == {}
    assert policy.escalations == []
    assert policy.tools["files.read"].deny_patterns == []
    assert policy.tools["files.read"].path_jail is None


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    """Missing policy file raises PolicyNotFoundError."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(PolicyNotFoundError) as exc_info:
        load_policy(missing)
    assert exc_info.value.code == ErrorCode.POLICY_BLOCK

    with pytest.raises(PolicyNotFoundError):
        PolicyLoader("")


def test_invalid_yaml_fails_closed(tmp_path: Path) -> None:
    """Malformed YAML raises PolicyValidationError."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("version: 1\ntools:\n  - [unclosed\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError) as exc_info:
        load_policy(bad_yaml)
    assert exc_info.value.code == ErrorCode.ARG_INVALID


@pytest.mark.parametrize(
    "content",
    [
        "",
        "# comment only\n",
        "- item1\n- item2\n",
        "just_a_string\n",
        "42\n",
    ],
)
def test_non_mapping_yaml_fails_closed(tmp_path: Path, content: str) -> None:
    """Empty, list, or scalar YAML documents raise PolicyValidationError."""
    doc = tmp_path / "non_mapping.yaml"
    doc.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyValidationError) as exc_info:
        load_policy(doc)
    assert exc_info.value.code == ErrorCode.ARG_INVALID
    assert "YAML mapping" in str(exc_info.value)


@pytest.mark.parametrize(
    "yaml_body",
    [
        # TierConfig missing required 'decision'
        "version: 1\ntiers:\n  read: { log: true }\n",
        # ToolPolicy missing required 'tier'
        "version: 1\ntools:\n  files.list: { path_jail: /workspace }\n",
        # EscalationRule missing required 'when'
        "version: 1\nescalations:\n  - decision: ask\n    reason: missing when\n",
    ],
)
def test_missing_required_fields_fails_closed(tmp_path: Path, yaml_body: str) -> None:
    """Missing required nested fields raise PolicyValidationError."""
    doc = tmp_path / "missing_fields.yaml"
    doc.write_text(yaml_body, encoding="utf-8")

    with pytest.raises(PolicyValidationError) as exc_info:
        load_policy(doc)
    assert exc_info.value.code == ErrorCode.ARG_INVALID


@pytest.mark.parametrize(
    "yaml_body",
    [
        # Extra field at top level
        "version: 1\ninvented_top_key: true\n",
        # Extra field in defaults
        "version: 1\ndefaults:\n  unknown_tool: block\n  extra_default: 123\n",
        # Extra field in tier config
        "version: 1\ntiers:\n  read: { decision: allow, extra_tier_opt: 1 }\n",
        # Extra field in tool policy
        "version: 1\ntools:\n  files.list: { tier: read, extra_tool_opt: true }\n",
        # Extra field in escalation rule
        (
            "version: 1\nescalations:\n"
            "  - when: { tool: '*' }\n    decision: block\n    extra_esc: 1\n"
        ),
        # Invalid unknown_tool value (must be 'block')
        "version: 1\ndefaults:\n  unknown_tool: allow\n",
    ],
)
def test_forbidden_extra_fields_and_invalid_values_fail_closed(
    tmp_path: Path, yaml_body: str
) -> None:
    """Extra fields (extra='forbid') and invalid literals fail closed."""
    doc = tmp_path / "extra_fields.yaml"
    doc.write_text(yaml_body, encoding="utf-8")

    with pytest.raises(PolicyValidationError) as exc_info:
        load_policy(doc)
    assert exc_info.value.code == ErrorCode.ARG_INVALID

"""Unit tests for package imports and structure."""

import importlib


def test_package_imports():
    modules = [
        "mcp",
        "control_plane",
        "control_plane.api",
        "control_plane.api.main",
        "control_plane.contracts",
        "control_plane.agent",
        "control_plane.gateway",
        "control_plane.sessions",
        "control_plane.vault",
        "control_plane.audit",
        "control_plane.events",
    ]
    for module_name in modules:
        mod = importlib.import_module(module_name)
        assert mod is not None


def test_contracts_exports():
    from control_plane import contracts

    assert hasattr(contracts, "ToolCall")
    assert hasattr(contracts, "ToolResult")
    assert hasattr(contracts, "Policy")
    assert hasattr(contracts, "Profile")
    assert hasattr(contracts, "AuditEntry")

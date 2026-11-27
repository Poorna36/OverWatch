"""Gateway policy loader and validator for policy.yaml (Phase 2, F-02).

Loads and validates the gateway policy YAML using ``yaml.safe_load`` and the
``Policy`` Pydantic model from ``control_plane.contracts``.
Fails closed on missing files, invalid YAML, non-mapping documents, or schema
validation errors.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from control_plane.contracts import ErrorCode, Policy
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = Path(
    os.environ.get("OVERWATCH_POLICY_PATH") or (REPO_ROOT / "policy.yaml")
).resolve()


class PolicyLoaderError(Exception):
    """Base exception for all gateway policy loading failures."""

    code: str = ErrorCode.FATAL


class PolicyNotFoundError(PolicyLoaderError):
    """Raised when the policy file does not exist or cannot be located."""

    code: str = ErrorCode.POLICY_BLOCK


class PolicyValidationError(PolicyLoaderError):
    """Raised when policy YAML parsing or schema validation fails."""

    code: str = ErrorCode.ARG_INVALID


def _resolve_policy_path(policy_path: Path | str | None) -> Path:
    """Resolve and validate the target policy file path."""
    if policy_path is None:
        return DEFAULT_POLICY_PATH

    if isinstance(policy_path, str) and not policy_path.strip():
        raise PolicyNotFoundError("Policy file path must not be empty.")

    return Path(policy_path).resolve()


def load_policy(policy_path: Path | str | None = None) -> Policy:
    """Load and validate the gateway policy YAML file.

    Guarantees:
    - Fails closed if the file is missing or not a regular file.
    - Parses YAML strictly via ``yaml.safe_load``.
    - Rejects non-mapping YAML documents (empty/None, lists, scalars).
    - Validates against ``control_plane.contracts.Policy`` (preserving defaults
      and forbidding extra fields).

    Raises:
        PolicyNotFoundError: If the policy file is missing or path is empty.
        PolicyValidationError: If YAML is malformed, non-mapping, or fails
            Pydantic schema validation.
        PolicyLoaderError: If the file cannot be read.
    """
    target_path = _resolve_policy_path(policy_path)

    if not target_path.is_file():
        raise PolicyNotFoundError(f"Policy file not found at {target_path}.")

    try:
        content = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyLoaderError(
            f"Failed to read policy file from {target_path}: {exc}"
        ) from exc

    try:
        raw_data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise PolicyValidationError(
            f"Invalid YAML in policy file {target_path}: {exc}"
        ) from exc

    if not isinstance(raw_data, dict):
        got_type = type(raw_data).__name__
        raise PolicyValidationError(
            f"Policy file {target_path} must contain a YAML mapping, got {got_type}."
        )

    try:
        return Policy.model_validate(raw_data)
    except ValidationError as exc:
        raise PolicyValidationError(
            f"Schema validation failed for policy file {target_path}: {exc}"
        ) from exc


class PolicyLoader:
    """Configurable gateway policy loader."""

    def __init__(self, policy_path: Path | str | None = None) -> None:
        self.policy_path = _resolve_policy_path(policy_path)

    def load(self) -> Policy:
        """Load and validate the configured policy file."""
        return load_policy(self.policy_path)

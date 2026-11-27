"""Profile loader for MCP Pocket Computer environment profiles (F-09).

Loads and validates YAML profiles from the configured profiles directory
using yaml.safe_load and the Profile model from control_plane.contracts.
Fails closed on unknown profiles, invalid YAML, schema violations,
or directory traversal attempts.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from control_plane.contracts import ErrorCode, Profile
from pydantic import ValidationError

# Default profiles directory resolved relative to repository root: <repo_root>/profiles
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES_DIR = Path(
    os.environ.get("OVERWATCH_PROFILES_DIR")
    or os.environ.get("PROFILES_DIR")
    or (REPO_ROOT / "profiles")
).resolve()


class ProfileLoaderError(Exception):
    """Base exception for all profile loading failures."""


class ProfileNotFoundError(ProfileLoaderError):
    """Raised when a requested profile does not exist."""

    code: str = ErrorCode.POLICY_BLOCK


class ProfilePathTraversalError(ProfileLoaderError):
    """Raised when profile name attempts path traversal or escapes directory."""

    code: str = ErrorCode.PATH_JAIL


class ProfileValidationError(ProfileLoaderError):
    """Raised when profile YAML or schema validation fails."""

    code: str = ErrorCode.ARG_INVALID


def resolve_profile_path(name: str, profiles_dir: Path | str | None = None) -> Path:
    """Resolve a profile name to its canonical YAML file path under profiles_dir.

    Guarantees:
    - Rejects profile names containing path separators ('/' or '\\').
    - Rejects path traversal attempts ('..', absolute paths, null bytes).
    - Ensures resolved canonical path is strictly inside the profiles directory.
    - Appends .yaml if not already suffixed with .yaml or .yml.

    Raises:
        ProfileNotFoundError: If profile name is empty or invalid type.
        ProfilePathTraversalError: If traversal, separator, or boundary escape
            is detected.
    """
    if not isinstance(name, str) or not name.strip():
        raise ProfileNotFoundError("Profile name must be a non-empty string.")

    cleaned_name = name.strip()

    if "\x00" in cleaned_name:
        raise ProfilePathTraversalError("Null byte detected in profile name.")

    if "/" in cleaned_name or "\\" in cleaned_name:
        raise ProfilePathTraversalError(
            f"Profile name cannot contain path separators: {cleaned_name!r}"
        )

    raw_path = Path(cleaned_name)
    if raw_path.is_absolute():
        raise ProfilePathTraversalError(
            f"Absolute path not permitted for profile name: {cleaned_name!r}"
        )

    if ".." in raw_path.parts:
        raise ProfilePathTraversalError(
            f"Path traversal ('..') detected in profile name: {cleaned_name!r}"
        )

    base = (
        Path(profiles_dir).resolve()
        if profiles_dir is not None
        else DEFAULT_PROFILES_DIR
    )

    if cleaned_name.endswith((".yaml", ".yml")):
        candidate = (base / cleaned_name).resolve()
    else:
        yaml_candidate = (base / f"{cleaned_name}.yaml").resolve()
        yml_candidate = (base / f"{cleaned_name}.yml").resolve()
        if yaml_candidate.is_file():
            candidate = yaml_candidate
        elif yml_candidate.is_file():
            candidate = yml_candidate
        else:
            candidate = yaml_candidate

    # Guard against escaping the profiles root (including via symlinks)
    if not candidate.is_relative_to(base) or candidate == base:
        raise ProfilePathTraversalError(
            f"Resolved path {candidate} escapes profiles directory {base}."
        )

    return candidate


def load_profile(name: str, profiles_dir: Path | str | None = None) -> Profile:
    """Load and validate an environment profile by name.

    1. Resolves profile file under configured profiles directory.
    2. Rejects path traversal and outside paths (fail closed).
    3. Verifies file existence.
    4. Parses YAML with yaml.safe_load.
    5. Validates structure against Profile model.
    6. Ensures profile.name matches the filename stem.

    Raises:
        ProfileNotFoundError: If the profile file does not exist.
        ProfilePathTraversalError: If path traversal is attempted.
        ProfileValidationError: If YAML is malformed or schema validation fails.
        ProfileLoaderError: If file cannot be read.
    """
    resolved_path = resolve_profile_path(name, profiles_dir=profiles_dir)

    if not resolved_path.is_file():
        raise ProfileNotFoundError(f"Profile '{name}' not found at {resolved_path}.")

    try:
        content = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileLoaderError(
            f"Failed to read profile '{name}' from {resolved_path}: {exc}"
        ) from exc

    try:
        raw_data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ProfileValidationError(
            f"Invalid YAML in profile '{name}': {exc}"
        ) from exc

    if not isinstance(raw_data, dict):
        got_type = type(raw_data).__name__
        raise ProfileValidationError(
            f"Profile '{name}' must contain a YAML mapping, got {got_type}."
        )

    try:
        profile = Profile.model_validate(raw_data)
    except ValidationError as exc:
        raise ProfileValidationError(
            f"Schema validation failed for profile '{name}': {exc}"
        ) from exc

    expected_name = Path(resolved_path.name).stem
    if profile.name != expected_name:
        raise ProfileValidationError(
            f"Profile name '{profile.name}' in file does not match "
            f"filename stem '{expected_name}'."
        )

    return profile


def list_profiles(profiles_dir: Path | str | None = None) -> list[str]:
    """List available profile names under the profiles directory.

    Returns:
        Sorted list of profile name stems found directly under profiles_dir.
    """
    base = (
        Path(profiles_dir).resolve()
        if profiles_dir is not None
        else DEFAULT_PROFILES_DIR
    )

    if not base.is_dir():
        return []

    profiles: list[str] = []
    for entry in base.iterdir():
        if (
            entry.is_file()
            and entry.suffix in (".yaml", ".yml")
            and not entry.name.startswith(".")
        ):
            try:
                resolved = entry.resolve()
                if resolved.is_file() and resolved.is_relative_to(base):
                    profiles.append(entry.stem)
            except OSError:
                continue

    return sorted(profiles)


class ProfileLoader:
    """Configurable profile loader instance for session management."""

    def __init__(self, profiles_dir: Path | str | None = None) -> None:
        self.profiles_dir = (
            Path(profiles_dir).resolve()
            if profiles_dir is not None
            else DEFAULT_PROFILES_DIR
        )

    def resolve_path(self, name: str) -> Path:
        """Resolve a profile name to its file path."""
        return resolve_profile_path(name, self.profiles_dir)

    def load(self, name: str) -> Profile:
        """Load and validate a profile by name."""
        return load_profile(name, self.profiles_dir)

    def list_profiles(self) -> list[str]:
        """List all valid profiles in the configured directory."""
        return list_profiles(self.profiles_dir)

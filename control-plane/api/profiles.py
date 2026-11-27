"""Phase 4 read-only profiles REST route."""

from __future__ import annotations

from typing import Any

from control_plane.api.deps import (
    ApiError,
    get_policy_loader,
    get_profile_loader,
    require_owner_id,
)
from control_plane.contracts import ErrorCode, Policy, Profile
from control_plane.gateway.policy_loader import PolicyLoader, PolicyLoaderError
from control_plane.sessions.profile_loader import ProfileLoader, ProfileLoaderError
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/v1", tags=["profiles"])


def _allowed_tools_for_profile(profile: Profile, policy: Policy) -> list[str]:
    """Return sorted tools present in both profile mcp_servers and policy.tools."""
    allowed: list[str] = []
    for tool_name in sorted(policy.tools.keys()):
        if "." not in tool_name:
            continue
        server_name, action = tool_name.split(".", 1)
        if server_name and action and server_name in profile.mcp_servers:
            allowed.append(tool_name)
    return allowed


@router.get("/profiles")
def list_available_profiles(
    _owner_id: str = Depends(require_owner_id),
    profile_loader: ProfileLoader = Depends(get_profile_loader),
    policy_loader: PolicyLoader = Depends(get_policy_loader),
) -> dict[str, list[dict[str, Any]]]:
    """List validated profiles and their policy-filtered tool lists."""
    try:
        policy = policy_loader.load()
        names = profile_loader.list_profiles()
        profiles = [profile_loader.load(name) for name in names]
    except (PolicyLoaderError, ProfileLoaderError) as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            f"Failed to load profile or policy catalog: {exc}",
        ) from exc

    return {
        "profiles": [
            {
                "name": prof.name,
                "role": prof.role,
                "tools": _allowed_tools_for_profile(prof, policy),
            }
            for prof in profiles
        ]
    }

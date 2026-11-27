"""Device registration routes (F-06 push notifications).

Implements:
- ``POST /api/v1/devices/push-token``
"""

from __future__ import annotations

from control_plane.api.deps import (
    ApiError,
    get_push_notification_service,
    require_owner_id,
)
from control_plane.contracts import ErrorCode
from control_plane.notifications.push import (
    InvalidPushTokenError,
    PushNotificationService,
    PushTokenConfigError,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/v1", tags=["devices"])


class PushTokenRequest(BaseModel):
    """Request body for ``POST /api/v1/devices/push-token``."""

    model_config = ConfigDict(extra="forbid")

    token: str


@router.post("/devices/push-token")
def register_push_token(
    body: PushTokenRequest,
    owner_id: str = Depends(require_owner_id),
    push_service: PushNotificationService = Depends(get_push_notification_service),
) -> dict[str, bool]:
    """Register or refresh an encrypted device push token for the caller."""
    try:
        push_service.register_token(owner_id=owner_id, token=body.token)
    except InvalidPushTokenError as exc:
        raise ApiError(400, ErrorCode.ARG_INVALID, str(exc)) from exc
    except PushTokenConfigError as exc:
        raise ApiError(
            500,
            ErrorCode.UPSTREAM_ERROR,
            "Push token encryption is not configured.",
        ) from exc

    return {"ok": True}

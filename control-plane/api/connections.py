"""F-18 Connector OAuth, disconnect revocation, and user-originated send routes."""

from __future__ import annotations

from typing import Any

from control_plane.api.deps import (
    ApiError,
    get_google_connector_client,
    get_google_oauth_service,
    get_google_vault,
    get_session_factory,
    require_owner_id,
)
from control_plane.audit.audit_writer import AuditWriter
from control_plane.connectors.google_oauth import (
    GoogleOAuthService,
    OAuthConfigError,
    OAuthFlowError,
)
from control_plane.connectors.google_tools import (
    ConnectorExecutionError,
    GoogleConnectorClient,
)
from control_plane.contracts import ConnectorTokenMode, ErrorCode
from control_plane.db.models import SessionModel
from control_plane.vault.google_vault import (
    GoogleConnectorVault,
    VaultAuthError,
    VaultConfigError,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(prefix="/api/v1", tags=["connections"])


class _GoogleOAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_mode: ConnectorTokenMode = "linked"
    redirect_uri: str | None = None


class _GoogleOAuthCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    state: str
    redirect_uri: str | None = None


@router.get("/connections")
def list_connections_route(
    owner_id: str = Depends(require_owner_id),
    vault: GoogleConnectorVault = Depends(get_google_vault),
) -> dict[str, list[dict[str, Any]]]:
    """List connected providers for the authenticated caller (no secrets)."""
    items = vault.list_connections(owner_id=owner_id)
    return {
        "connections": [
            {
                "connection_id": item.connection_id,
                "provider": item.provider,
                "status": item.status,
                "token_mode": item.token_mode,
                "scopes": list(item.scopes),
                "updated_at": item.updated_at,
            }
            for item in items
        ]
    }


@router.post("/connections/google/start")
def start_google_oauth_route(
    payload: _GoogleOAuthStartRequest | None = None,
    owner_id: str = Depends(require_owner_id),
    oauth: GoogleOAuthService = Depends(get_google_oauth_service),
) -> dict[str, str]:
    """Begin test-user Google OAuth with draft-only scopes."""
    mode: ConnectorTokenMode = payload.token_mode if payload is not None else "linked"
    redirect_uri = payload.redirect_uri if payload is not None else None
    try:
        return oauth.start_oauth(
            owner_id=owner_id, token_mode=mode, redirect_uri=redirect_uri
        )
    except OAuthFlowError as exc:
        raise ApiError(400, ErrorCode.AUTH_ERROR, str(exc)) from exc
    except OAuthConfigError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc


@router.post("/connections/google/callback")
def complete_google_oauth_route(
    payload: _GoogleOAuthCallbackRequest,
    owner_id: str = Depends(require_owner_id),
    oauth: GoogleOAuthService = Depends(get_google_oauth_service),
) -> dict[str, Any]:
    """Complete Google OAuth code exchange and store encrypted tokens in vault."""
    try:
        summary = oauth.complete_oauth(
            owner_id=owner_id,
            code=payload.code,
            state=payload.state,
            redirect_uri=payload.redirect_uri,
        )
    except OAuthFlowError as exc:
        raise ApiError(400, ErrorCode.AUTH_ERROR, str(exc)) from exc
    except (OAuthConfigError, VaultConfigError) as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc
    return {
        "ok": True,
        "provider": summary.provider,
        "status": summary.status,
        "token_mode": summary.token_mode,
        "scopes": list(summary.scopes),
    }


@router.delete("/connections/google")
def disconnect_google_route(
    owner_id: str = Depends(require_owner_id),
    vault: GoogleConnectorVault = Depends(get_google_vault),
    oauth: GoogleOAuthService = Depends(get_google_oauth_service),
) -> dict[str, bool]:
    """Revoke Google grant immediately in vault and best-effort upstream."""
    token_to_revoke = vault.disconnect(owner_id=owner_id)
    oauth.revoke_remote(token_to_revoke)
    return {"ok": True}


@router.post("/sessions/{session_id}/drafts/{draft_id}/send")
def send_user_draft_route(
    session_id: str,
    draft_id: str,
    owner_id: str = Depends(require_owner_id),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    connector_client: GoogleConnectorClient = Depends(get_google_connector_client),
) -> dict[str, Any]:
    """User-originated send of an existing Gmail draft (never agent-callable)."""
    with session_factory() as db:
        row = db.scalar(
            select(SessionModel).where(
                SessionModel.session_id == session_id,
                SessionModel.owner_id == owner_id,
            )
        )
    if row is None:
        raise ApiError(404, ErrorCode.SESSION_INACTIVE, "Session not found.")

    try:
        result = connector_client.send_user_draft(owner_id=owner_id, draft_id=draft_id)
    except VaultAuthError as exc:
        raise ApiError(401, ErrorCode.AUTH_ERROR, str(exc)) from exc
    except VaultConfigError as exc:
        raise ApiError(500, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc
    except ConnectorExecutionError as exc:
        raise ApiError(502, ErrorCode.UPSTREAM_ERROR, str(exc)) from exc

    AuditWriter(session_factory).append(
        session_id=session_id,
        tool="gmail.send",
        args={"draft_id": draft_id, "origin": "user"},
        decision="asked_approved",
        reason="user-originated draft send",
        tier="sensitive",
    )
    return {"ok": True, "result": result}

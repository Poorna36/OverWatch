"""Test-user Google OAuth flow with draft-only scopes (F-18)."""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from control_plane.contracts import ConnectorTokenMode
from control_plane.vault.google_vault import ConnectionSummary, GoogleConnectorVault

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Draft-only scopes: compose/drafts + calendar events; never gmail.send
DRAFT_ONLY_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)


class OAuthConfigError(Exception):
    """Raised when Google OAuth client credentials are not configured."""


class OAuthFlowError(Exception):
    """Raised when OAuth state validation or token exchange fails."""


@dataclass(frozen=True)
class GoogleOAuthConfig:
    """Operator-supplied Google OAuth test-user credentials."""

    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> GoogleOAuthConfig:
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
        if not client_id or not client_secret or not redirect_uri:
            raise OAuthConfigError(
                "Google OAuth credentials are not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI."
            )
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )


class GoogleOAuthService:
    """Manages test-user OAuth authorization, token exchange, and revocation."""

    def __init__(
        self,
        *,
        vault: GoogleConnectorVault,
        http_client: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._vault = vault
        self._http_client = http_client
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending_states: dict[str, tuple[str, ConnectorTokenMode]] = {}

    def start_oauth(
        self,
        *,
        owner_id: str,
        token_mode: ConnectorTokenMode = "linked",
    ) -> dict[str, str]:
        """Generate a Google OAuth consent URL with draft-only scopes."""
        cfg = GoogleOAuthConfig.from_env()
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._pending_states[state] = (owner_id, token_mode)

        params = urlencode(
            {
                "client_id": cfg.client_id,
                "redirect_uri": cfg.redirect_uri,
                "response_type": "code",
                "scope": " ".join(DRAFT_ONLY_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return {"auth_url": f"{GOOGLE_AUTH_URL}?{params}", "state": state}

    def complete_oauth(
        self,
        *,
        owner_id: str,
        code: str,
        state: str,
    ) -> ConnectionSummary:
        """Verify state, exchange auth code, and store encrypted tokens in vault."""
        with self._lock:
            expected = self._pending_states.pop(state, None)
        if expected is None or expected[0] != owner_id:
            raise OAuthFlowError("Invalid or expired OAuth state.")
        token_mode = expected[1]
        cfg = GoogleOAuthConfig.from_env()

        form = {
            "code": code,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": cfg.redirect_uri,
            "grant_type": "authorization_code",
        }
        if self._http_client is not None:
            resp = self._http_client.post(
                GOOGLE_TOKEN_URL, data=form, timeout=self._timeout_s
            )
        else:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(GOOGLE_TOKEN_URL, data=form)

        if resp.status_code >= 400:
            raise OAuthFlowError(
                f"Google OAuth token exchange failed (HTTP {resp.status_code})."
            )
        body = resp.json()
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthFlowError("Google OAuth response missing access_token.")
        refresh_token = body.get("refresh_token")
        raw_scope = body.get("scope")
        scopes = (
            raw_scope.split()
            if isinstance(raw_scope, str) and raw_scope.strip()
            else list(DRAFT_ONLY_SCOPES)
        )
        return self._vault.store_tokens(
            owner_id=owner_id,
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            scopes=scopes,
            token_mode=token_mode,
        )

    def revoke_remote(self, token: str | None) -> None:
        """Best-effort Google OAuth token revocation on disconnect."""
        if not token:
            return
        try:
            if self._http_client is not None:
                self._http_client.post(
                    GOOGLE_REVOKE_URL, data={"token": token}, timeout=self._timeout_s
                )
            else:
                with httpx.Client(timeout=self._timeout_s) as client:
                    client.post(GOOGLE_REVOKE_URL, data={"token": token})
        except Exception:  # noqa: BLE001
            pass

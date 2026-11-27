"""Short-lived, session-scoped HMAC tokens for WebSocket/view stream access.

Tokens are derived from ``APP_TOKEN`` via domain-separated HMAC-SHA256,
bound to ``session_id`` and ``owner_id``, capped at 15 minutes
(``docs/SECURITY.md`` §6), and never persisted or logged.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

STREAM_TOKEN_TTL_SECONDS = 15 * 60
STREAM_TOKEN_VERSION = "v1"
STREAM_TOKEN_SCOPE = "stream"
_SIGNING_DOMAIN = b"overwatch:ws-stream-token:v1"
_MAX_CLOCK_SKEW_SECONDS = 60
_FORBIDDEN_ID_CHARS = frozenset("\x00\n\r\t ")


class StreamTokenError(Exception):
    """Raised when issuing or verifying a session-scoped stream token fails."""


@dataclass(frozen=True)
class StreamTokenClaims:
    """Verified claims extracted from a valid stream token."""

    session_id: str
    owner_id: str
    scope: Literal["stream"]
    iat: int
    exp: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(encoded: str) -> bytes:
    if not isinstance(encoded, str) or not encoded or "=" in encoded:
        raise StreamTokenError("Invalid stream token encoding.")
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise StreamTokenError("Invalid stream token encoding.") from exc


def _derive_hmac_key(signing_key: str) -> bytes:
    if not isinstance(signing_key, str) or not signing_key.strip():
        raise StreamTokenError("Stream signing key is not configured.")
    return hmac.new(
        signing_key.encode("utf-8"),
        _SIGNING_DOMAIN,
        hashlib.sha256,
    ).digest()


def _normalize_epoch_seconds(now: datetime | None) -> int:
    if now is None:
        return int(datetime.now(UTC).timestamp())
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise StreamTokenError("Clock timestamp must be a timezone-aware datetime.")
    return int(now.astimezone(UTC).timestamp())


def _validate_claim_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise StreamTokenError(f"Invalid {field_name} claim.")
    if any(ch in _FORBIDDEN_ID_CHARS for ch in value):
        raise StreamTokenError(f"Invalid {field_name} claim.")
    return value


def issue_stream_token(
    *,
    session_id: str,
    owner_id: str,
    signing_key: str,
    ttl_seconds: int = STREAM_TOKEN_TTL_SECONDS,
    now: datetime | None = None,
) -> str:
    """Issue a short-lived, session-scoped HMAC token bound to session and owner."""
    valid_session_id = _validate_claim_id(session_id, field_name="session_id")
    valid_owner_id = _validate_claim_id(owner_id, field_name="owner_id")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
        or ttl_seconds > STREAM_TOKEN_TTL_SECONDS
    ):
        raise StreamTokenError(
            "ttl_seconds must be an integer between 1 and "
            f"{STREAM_TOKEN_TTL_SECONDS}."
        )

    hmac_key = _derive_hmac_key(signing_key)
    iat = _normalize_epoch_seconds(now)
    exp = iat + ttl_seconds

    payload = {
        "exp": exp,
        "iat": iat,
        "owner_id": valid_owner_id,
        "scope": STREAM_TOKEN_SCOPE,
        "session_id": valid_session_id,
    }
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig_input = f"{STREAM_TOKEN_VERSION}.{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(hmac.new(hmac_key, sig_input, hashlib.sha256).digest())
    return f"{STREAM_TOKEN_VERSION}.{payload_b64}.{sig_b64}"


def verify_stream_token(
    token: str,
    *,
    expected_session_id: str,
    signing_key: str,
    now: datetime | None = None,
) -> StreamTokenClaims:
    """Verify signature, lifetime, scope, and session binding for a stream token."""
    _validate_claim_id(expected_session_id, field_name="session_id")
    if not isinstance(token, str) or not token:
        raise StreamTokenError("Missing stream token.")

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != STREAM_TOKEN_VERSION:
        raise StreamTokenError("Invalid stream token format.")

    _, payload_b64, sig_b64 = parts
    hmac_key = _derive_hmac_key(signing_key)
    sig_input = f"{STREAM_TOKEN_VERSION}.{payload_b64}".encode("ascii")
    expected_sig_b64 = _b64url_encode(
        hmac.new(hmac_key, sig_input, hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise StreamTokenError("Invalid stream token signature.")

    payload_raw = _b64url_decode(payload_b64)
    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StreamTokenError("Invalid stream token payload.") from exc

    if not isinstance(payload, dict):
        raise StreamTokenError("Invalid stream token payload.")

    expected_keys = {"exp", "iat", "owner_id", "scope", "session_id"}
    if set(payload.keys()) != expected_keys:
        raise StreamTokenError("Invalid stream token claims.")

    if payload.get("scope") != STREAM_TOKEN_SCOPE:
        raise StreamTokenError("Invalid stream token scope.")

    claim_session_id = _validate_claim_id(
        payload.get("session_id"), field_name="session_id"
    )
    if not hmac.compare_digest(claim_session_id, expected_session_id):
        raise StreamTokenError("Stream token session mismatch.")

    claim_owner_id = _validate_claim_id(payload.get("owner_id"), field_name="owner_id")

    iat = payload.get("iat")
    exp = payload.get("exp")
    if (
        isinstance(iat, bool)
        or isinstance(exp, bool)
        or not isinstance(iat, int)
        or not isinstance(exp, int)
    ):
        raise StreamTokenError("Invalid stream token timestamps.")

    lifetime = exp - iat
    if lifetime <= 0 or lifetime > STREAM_TOKEN_TTL_SECONDS:
        raise StreamTokenError("Invalid stream token lifetime.")

    now_epoch = _normalize_epoch_seconds(now)
    if iat > now_epoch + _MAX_CLOCK_SKEW_SECONDS:
        raise StreamTokenError("Stream token not yet valid.")
    if now_epoch >= exp:
        raise StreamTokenError("Stream token has expired.")

    return StreamTokenClaims(
        session_id=claim_session_id,
        owner_id=claim_owner_id,
        scope="stream",
        iat=iat,
        exp=exp,
    )

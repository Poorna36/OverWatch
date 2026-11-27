"""Push token encryption at rest and best-effort notification delivery (F-06).

Security invariants:
- Push tokens are encrypted at rest with the dedicated ``PUSH_TOKEN_KEY``
  environment variable (32-byte base64-encoded AES-256-GCM key). Never reuses
  ``VAULT_KEY`` and fails closed when ``PUSH_TOKEN_KEY`` is missing or invalid.
- Plaintext push tokens are never logged, returned by APIs, or placed in audit
  or WebSocket event payloads.
- Notification delivery uses bounded HTTP timeouts and best-effort semantics:
  provider or key errors never block or roll back approvals, session completion,
  or audit/event persistence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from control_plane.audit.audit_writer import format_utc_iso
from control_plane.contracts import ApprovalRequest
from control_plane.db.models import DevicePushTokenModel
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

DEFAULT_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
DEFAULT_PUSH_TIMEOUT_S = 5.0
MAX_PUSH_TOKEN_LENGTH = 512
_TOKEN_ID_PREFIX = "pt_"
_CIPHER_VERSION_PREFIX = "v1:"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class PushTokenError(Exception):
    """Base error for push-token storage and configuration failures."""


class PushTokenConfigError(PushTokenError):
    """Raised when ``PUSH_TOKEN_KEY`` is missing, malformed, or fails decryption."""


class InvalidPushTokenError(PushTokenError):
    """Raised when a submitted push token fails format validation."""


@dataclass(frozen=True)
class PushMessage:
    """Outbound push notification payload targeted at one device token."""

    to: str
    title: str
    body: str
    data: dict[str, Any]

    def __repr__(self) -> str:
        # Never include the plaintext push token in repr/logs.
        return f"PushMessage(title={self.title!r}, data={self.data!r})"


@dataclass(frozen=True)
class PushDeliveryResult:
    """Summary of a push delivery batch without exposing tokens in logs."""

    sent_count: int = 0
    invalid_tokens: tuple[str, ...] = ()
    failed_count: int = 0


class PushSender(Protocol):
    """Injectable transport for delivering push notification batches."""

    def send(self, messages: Sequence[PushMessage]) -> PushDeliveryResult: ...


def _encode_crockford_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def _generate_push_token_id(now: datetime | None = None) -> str:
    dt = datetime.now(UTC) if now is None else now.astimezone(UTC)
    timestamp_ms = int(dt.timestamp() * 1000)
    ts_part = _encode_crockford_base32(timestamp_ms, 10)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_part = _encode_crockford_base32(rand_int, 16)
    return f"{_TOKEN_ID_PREFIX}{ts_part}{rand_part}"


def validate_push_token(raw_token: Any) -> str:
    """Validate an opaque device push token without logging it."""
    if not isinstance(raw_token, str):
        raise InvalidPushTokenError("Push token must be a string.")
    if not raw_token or raw_token != raw_token.strip():
        raise InvalidPushTokenError(
            "Push token must be non-empty without leading or trailing whitespace."
        )
    if len(raw_token) > MAX_PUSH_TOKEN_LENGTH:
        raise InvalidPushTokenError(
            f"Push token exceeds maximum length ({MAX_PUSH_TOKEN_LENGTH})."
        )
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in raw_token):
        raise InvalidPushTokenError(
            "Push token must not contain whitespace or control characters."
        )
    return raw_token


def _decode_push_token_key(raw_key_str: str | None) -> bytes:
    """Decode and validate a 32-byte base64 ``PUSH_TOKEN_KEY``."""
    if raw_key_str is None or not raw_key_str.strip():
        raise PushTokenConfigError(
            "PUSH_TOKEN_KEY is not configured; cannot encrypt or decrypt push tokens."
        )
    candidate = raw_key_str.strip()
    try:
        if "-" in candidate or "_" in candidate:
            decoded = base64.urlsafe_b64decode(candidate.encode("ascii"))
        else:
            decoded = base64.b64decode(candidate.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise PushTokenConfigError(
            "PUSH_TOKEN_KEY must be valid base64 encoding of 32 bytes."
        ) from exc

    if len(decoded) != _KEY_BYTES:
        raise PushTokenConfigError(
            f"PUSH_TOKEN_KEY must decode to exactly {_KEY_BYTES} bytes, "
            f"got {len(decoded)}."
        )
    return decoded


class PushTokenCipher:
    """AES-256-GCM + HMAC-SHA256 cipher backed exclusively by ``PUSH_TOKEN_KEY``."""

    def __init__(self, key_b64: str | None = None) -> None:
        self._explicit_key = key_b64
        self._cached_key_str: str | None = None
        self._cached_raw_key: bytes | None = None

    def _raw_key(self) -> bytes:
        key_str = (
            self._explicit_key
            if self._explicit_key is not None
            else os.environ.get("PUSH_TOKEN_KEY")
        )
        if self._cached_raw_key is not None and key_str == self._cached_key_str:
            return self._cached_raw_key
        raw = _decode_push_token_key(key_str)
        self._cached_key_str = key_str
        self._cached_raw_key = raw
        return raw

    def encrypt(self, token: str) -> str:
        """Encrypt a validated push token into an authenticated ciphertext string."""
        raw_key = self._raw_key()
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(raw_key).encrypt(nonce, token.encode("utf-8"), None)
        encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{_CIPHER_VERSION_PREFIX}{encoded}"

    def decrypt(self, token_enc: str) -> str:
        """Decrypt a stored ciphertext string back into a push token."""
        if not isinstance(token_enc, str) or not token_enc.startswith(
            _CIPHER_VERSION_PREFIX
        ):
            raise PushTokenConfigError("Malformed encrypted push token payload.")
        raw_key = self._raw_key()
        encoded = token_enc[len(_CIPHER_VERSION_PREFIX) :]
        try:
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise PushTokenConfigError(
                "Malformed base64 in encrypted push token payload."
            ) from exc
        if len(payload) <= _NONCE_BYTES:
            raise PushTokenConfigError("Truncated encrypted push token payload.")
        nonce = payload[:_NONCE_BYTES]
        ciphertext = payload[_NONCE_BYTES:]
        try:
            plaintext = AESGCM(raw_key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise PushTokenConfigError(
                "Failed to authenticate encrypted push token."
            ) from exc
        return plaintext.decode("utf-8")

    def token_hash(self, token: str) -> str:
        """Compute a keyed HMAC-SHA256 digest for deduplication and revocation."""
        raw_key = self._raw_key()
        return hmac.new(raw_key, token.encode("utf-8"), hashlib.sha256).hexdigest()


class PushTokenStore:
    """SQLite-backed store for encrypted owner-scoped device push tokens."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        cipher: PushTokenCipher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = PushTokenCipher() if cipher is None else cipher
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock

    def _now_iso(self) -> str:
        return format_utc_iso(self._clock())

    def register_token(
        self,
        *,
        owner_id: str,
        token: str,
        now: datetime | None = None,
    ) -> str:
        """Encrypt and upsert a push token for ``owner_id``; returns ``token_id``."""
        if not owner_id or not isinstance(owner_id, str):
            raise ValueError("owner_id must be a non-empty string.")
        valid_token = validate_push_token(token)
        token_hash = self._cipher.token_hash(valid_token)
        token_enc = self._cipher.encrypt(valid_token)
        now_dt = self._clock() if now is None else now.astimezone(UTC)
        now_iso = format_utc_iso(now_dt)

        with self._session_factory() as db:
            existing = db.scalar(
                select(DevicePushTokenModel).where(
                    DevicePushTokenModel.token_hash == token_hash
                )
            )
            if existing is not None:
                existing.owner_id = owner_id
                existing.token_enc = token_enc
                existing.updated_at = now_iso
                existing.revoked_at = None
                db.commit()
                return existing.token_id

            token_id = _generate_push_token_id(now_dt)
            db.add(
                DevicePushTokenModel(
                    token_id=token_id,
                    owner_id=owner_id,
                    token_hash=token_hash,
                    token_enc=token_enc,
                    created_at=now_iso,
                    updated_at=now_iso,
                    revoked_at=None,
                )
            )
            db.commit()
            return token_id

    def revoke_token(
        self,
        *,
        owner_id: str,
        token: str,
        now: datetime | None = None,
    ) -> bool:
        """Mark an owner's push token as revoked without logging its value."""
        valid_token = validate_push_token(token)
        token_hash = self._cipher.token_hash(valid_token)
        now_iso = self._now_iso() if now is None else format_utc_iso(now)
        with self._session_factory() as db:
            res = db.execute(
                update(DevicePushTokenModel)
                .where(
                    DevicePushTokenModel.owner_id == owner_id,
                    DevicePushTokenModel.token_hash == token_hash,
                    DevicePushTokenModel.revoked_at.is_(None),
                )
                .values(revoked_at=now_iso, updated_at=now_iso)
            )
            db.commit()
            return bool(res.rowcount)

    def list_active_tokens(self, owner_id: str) -> list[str]:
        """Decrypt and return active push tokens belonging to ``owner_id``."""
        with self._session_factory() as db:
            rows = db.scalars(
                select(DevicePushTokenModel)
                .where(
                    DevicePushTokenModel.owner_id == owner_id,
                    DevicePushTokenModel.revoked_at.is_(None),
                )
                .order_by(DevicePushTokenModel.updated_at.desc())
            ).all()

        tokens: list[str] = []
        for row in rows:
            try:
                tokens.append(self._cipher.decrypt(row.token_enc))
            except PushTokenConfigError:
                logger.warning(
                    "Failed to decrypt stored push token_id=%s for owner_id=%s",
                    row.token_id,
                    owner_id,
                )
        return tokens

    def remove_invalid_tokens(self, tokens: Sequence[str]) -> int:
        """Delete tokens reported as invalid/unregistered by the push provider."""
        if not tokens:
            return 0
        hashes = [self._cipher.token_hash(tok) for tok in tokens if tok]
        if not hashes:
            return 0
        with self._session_factory() as db:
            res = db.execute(
                delete(DevicePushTokenModel).where(
                    DevicePushTokenModel.token_hash.in_(hashes)
                )
            )
            db.commit()
            return int(res.rowcount or 0)


class ExpoPushSender:
    """HTTP client for Expo Push API with bounded timeout."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        timeout_s: float = DEFAULT_PUSH_TIMEOUT_S,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive.")
        self._api_url = (
            api_url or os.environ.get("EXPO_PUSH_API_URL") or DEFAULT_EXPO_PUSH_URL
        )
        self._timeout_s = timeout_s
        self._http_client = http_client

    def send(self, messages: Sequence[PushMessage]) -> PushDeliveryResult:
        """Send push notifications via Expo Push API with a bounded timeout."""
        if not messages:
            return PushDeliveryResult()

        payload = [
            {
                "to": msg.to,
                "title": msg.title,
                "body": msg.body,
                "data": msg.data,
                "sound": "default",
            }
            for msg in messages
        ]

        if self._http_client is not None:
            response = self._http_client.post(
                self._api_url,
                json=payload,
                timeout=self._timeout_s,
            )
        else:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.post(self._api_url, json=payload)

        response.raise_for_status()
        body = response.json()
        tickets = body.get("data") if isinstance(body, dict) else None
        if not isinstance(tickets, list):
            return PushDeliveryResult(sent_count=len(messages))

        invalid_tokens: list[str] = []
        sent_count = 0
        failed_count = 0
        for msg, ticket in zip(messages, tickets, strict=False):
            if not isinstance(ticket, dict):
                failed_count += 1
                continue
            if ticket.get("status") == "ok":
                sent_count += 1
                continue
            failed_count += 1
            details = ticket.get("details")
            error_code = details.get("error") if isinstance(details, dict) else None
            if error_code == "DeviceNotRegistered":
                invalid_tokens.append(msg.to)

        return PushDeliveryResult(
            sent_count=sent_count,
            invalid_tokens=tuple(invalid_tokens),
            failed_count=failed_count,
        )


class PushNotificationService:
    """High-level coordinator for push token registration and best-effort delivery."""

    def __init__(
        self,
        *,
        store: PushTokenStore,
        sender: PushSender,
    ) -> None:
        self._store = store
        self._sender = sender

    def register_token(self, *, owner_id: str, token: str) -> str:
        """Validate, encrypt, and store a device push token for ``owner_id``."""
        return self._store.register_token(owner_id=owner_id, token=token)

    def revoke_token(self, *, owner_id: str, token: str) -> bool:
        """Revoke a device push token for ``owner_id``."""
        return self._store.revoke_token(owner_id=owner_id, token=token)

    def notify_approval_requested(
        self,
        *,
        owner_id: str,
        approval: ApprovalRequest,
    ) -> PushDeliveryResult:
        """Best-effort push notification for a newly created approval request."""
        return self._deliver_to_owner(
            owner_id=owner_id,
            title="Approval required",
            body=f"{approval.tool} ({approval.tier}) requires your approval.",
            data={
                "type": "approval_request",
                "session_id": approval.session_id,
                "approval_id": approval.approval_id,
                "tool": approval.tool,
                "tier": approval.tier,
                "irreversible": approval.irreversible,
            },
        )

    def notify_session_completed(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> PushDeliveryResult:
        """Best-effort push notification when a session run completes."""
        return self._deliver_to_owner(
            owner_id=owner_id,
            title="Session completed",
            body="Your OverWatch session has completed.",
            data={
                "type": "session_completed",
                "session_id": session_id,
            },
        )

    def _deliver_to_owner(
        self,
        *,
        owner_id: str,
        title: str,
        body: str,
        data: dict[str, Any],
    ) -> PushDeliveryResult:
        """Deliver notifications best-effort and purge provider-rejected tokens."""
        try:
            tokens = self._store.list_active_tokens(owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Push token lookup failed for owner_id=%s: %s",
                owner_id,
                type(exc).__name__,
            )
            return PushDeliveryResult()

        if not tokens:
            return PushDeliveryResult()

        messages = [
            PushMessage(to=token, title=title, body=body, data=data) for token in tokens
        ]
        try:
            result = self._sender.send(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Best-effort push delivery failed for owner_id=%s count=%d: %s",
                owner_id,
                len(messages),
                type(exc).__name__,
            )
            return PushDeliveryResult(failed_count=len(messages))

        if result.invalid_tokens:
            try:
                self._store.remove_invalid_tokens(result.invalid_tokens)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to purge invalid push tokens for owner_id=%s: %s",
                    owner_id,
                    type(exc).__name__,
                )
        return result

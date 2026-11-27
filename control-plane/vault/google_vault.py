"""Encrypted vault storage for F-18 Google Calendar & Gmail OAuth credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from control_plane.audit.audit_writer import canonical_json, format_utc_iso
from control_plane.contracts import ConnectorTokenMode
from control_plane.db.models import ConnectionModel
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

_KEY_BYTES = 32
_NONCE_BYTES = 12
_CIPHER_PREFIX = "v1:"
_REVOKED_MARKER = "REVOKED"


class VaultError(Exception):
    """Base error for connector vault operations."""


class VaultConfigError(VaultError):
    """Raised when ``VAULT_KEY`` is missing, invalid, or decryption fails."""


class VaultAuthError(VaultError):
    """Raised when a connector credential is missing or revoked (``AUTH_ERROR``)."""


@dataclass(frozen=True)
class GoogleTokens:
    """Decrypted in-memory-only OAuth tokens for one Google connection."""

    access_token: str
    refresh_token: str | None = None
    token_mode: ConnectorTokenMode = "linked"
    scopes: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            f"GoogleTokens(token_mode={self.token_mode!r}, "
            f"scopes={self.scopes!r}, has_refresh={self.refresh_token is not None})"
        )


@dataclass(frozen=True)
class ConnectionSummary:
    """Public connection status (never contains tokens or secrets)."""

    connection_id: str
    provider: str
    status: str
    token_mode: ConnectorTokenMode
    scopes: tuple[str, ...]
    updated_at: str


def _decode_vault_key(raw_key_str: str | None) -> bytes:
    if raw_key_str is None or not raw_key_str.strip():
        raise VaultConfigError(
            "VAULT_KEY is not configured; cannot encrypt or decrypt connector secrets."
        )
    candidate = raw_key_str.strip()
    try:
        if "-" in candidate or "_" in candidate:
            decoded = base64.urlsafe_b64decode(candidate.encode("ascii"))
        else:
            decoded = base64.b64decode(candidate.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise VaultConfigError(
            "VAULT_KEY must be valid base64 encoding of 32 bytes."
        ) from exc
    if len(decoded) != _KEY_BYTES:
        raise VaultConfigError(
            f"VAULT_KEY must decode to {_KEY_BYTES} bytes, got {len(decoded)}."
        )
    return decoded


class VaultCipher:
    """AES-256-GCM cipher backed exclusively by ``VAULT_KEY``."""

    def __init__(self, key_b64: str | None = None) -> None:
        self._explicit_key = key_b64

    def _raw_key(self) -> bytes:
        key_str = (
            self._explicit_key
            if self._explicit_key is not None
            else os.environ.get("VAULT_KEY")
        )
        return _decode_vault_key(key_str)

    def encrypt_json(self, payload: dict[str, object]) -> str:
        raw_key = self._raw_key()
        nonce = os.urandom(_NONCE_BYTES)
        plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ct = AESGCM(raw_key).encrypt(nonce, plaintext, None)
        return f"{_CIPHER_PREFIX}{base64.urlsafe_b64encode(nonce + ct).decode('ascii')}"

    def decrypt_json(self, token_enc: str) -> dict[str, object]:
        if not token_enc.startswith(_CIPHER_PREFIX):
            raise VaultConfigError("Malformed encrypted vault secret.")
        raw_key = self._raw_key()
        encoded = token_enc[len(_CIPHER_PREFIX) :]
        try:
            blob = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise VaultConfigError("Malformed base64 in vault secret.") from exc
        if len(blob) <= _NONCE_BYTES:
            raise VaultConfigError("Truncated encrypted vault secret.")
        try:
            plaintext = AESGCM(raw_key).decrypt(
                blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None
            )
            parsed = json.loads(plaintext.decode("utf-8"))
        except (InvalidTag, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VaultConfigError("Failed to decrypt vault secret.") from exc
        if not isinstance(parsed, dict):
            raise VaultConfigError("Decrypted vault secret must be a JSON object.")
        return parsed


class GoogleConnectorVault:
    """Stores, retrieves, and revokes encrypted Google OAuth tokens."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        cipher: VaultCipher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = VaultCipher() if cipher is None else cipher
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock

    def _now_iso(self) -> str:
        return format_utc_iso(self._clock())

    def store_tokens(
        self,
        *,
        owner_id: str,
        access_token: str,
        refresh_token: str | None = None,
        scopes: Sequence[str] = (),
        token_mode: ConnectorTokenMode = "linked",
        session_id: str | None = None,
    ) -> ConnectionSummary:
        """Encrypt and persist Google OAuth tokens for ``owner_id``."""
        if not access_token or not isinstance(access_token, str):
            raise VaultAuthError("access_token must be a non-empty string.")
        now_iso = self._now_iso()
        secret_enc = self._cipher.encrypt_json(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
            }
        )
        scopes_list = sorted({str(s) for s in scopes if str(s).strip()})
        scopes_json = canonical_json(scopes_list)

        with self._session_factory() as db:
            row = db.scalar(
                select(ConnectionModel).where(
                    ConnectionModel.owner_id == owner_id,
                    ConnectionModel.provider == "google",
                )
            )
            if row is not None:
                row.token_mode = token_mode
                row.scopes_json = scopes_json
                row.secret_enc = secret_enc
                row.status = "active"
                row.session_id = session_id if token_mode == "session" else None
                row.updated_at = now_iso
                row.revoked_at = None
                db.commit()
                cid = row.connection_id
            else:
                cid = f"conn_{secrets.token_hex(12)}"
                db.add(
                    ConnectionModel(
                        connection_id=cid,
                        owner_id=owner_id,
                        provider="google",
                        token_mode=token_mode,
                        scopes_json=scopes_json,
                        secret_enc=secret_enc,
                        status="active",
                        session_id=session_id if token_mode == "session" else None,
                        created_at=now_iso,
                        updated_at=now_iso,
                        revoked_at=None,
                    )
                )
                db.commit()

        return ConnectionSummary(
            connection_id=cid,
            provider="google",
            status="active",
            token_mode=token_mode,
            scopes=tuple(scopes_list),
            updated_at=now_iso,
        )

    def get_tokens(self, *, owner_id: str) -> GoogleTokens:
        """Decrypt active Google OAuth tokens for ``owner_id`` or fail closed."""
        with self._session_factory() as db:
            row = db.scalar(
                select(ConnectionModel).where(
                    ConnectionModel.owner_id == owner_id,
                    ConnectionModel.provider == "google",
                )
            )
            if (
                row is None
                or row.status != "active"
                or row.secret_enc == _REVOKED_MARKER
            ):
                raise VaultAuthError(
                    "Google connector is not connected or has been revoked."
                )
            secret_enc = row.secret_enc
            token_mode: ConnectorTokenMode = row.token_mode  # type: ignore[assignment]
            scopes = tuple(str(s) for s in json.loads(row.scopes_json))

        data = self._cipher.decrypt_json(secret_enc)
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise VaultAuthError("Google access token is missing or revoked.")
        return GoogleTokens(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            token_mode=token_mode,
            scopes=scopes,
        )

    def list_connections(self, *, owner_id: str) -> list[ConnectionSummary]:
        """List connection summaries for ``owner_id`` without exposing secrets."""
        with self._session_factory() as db:
            rows = db.scalars(
                select(ConnectionModel).where(ConnectionModel.owner_id == owner_id)
            ).all()
        return [
            ConnectionSummary(
                connection_id=row.connection_id,
                provider=row.provider,
                status=row.status,
                token_mode=row.token_mode,  # type: ignore[arg-type]
                scopes=tuple(str(s) for s in json.loads(row.scopes_json)),
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def disconnect(self, *, owner_id: str) -> str | None:
        """Revoke Google grant for ``owner_id`` and scrub encrypted secrets."""
        now_iso = self._now_iso()
        token_to_revoke: str | None = None
        with self._session_factory() as db:
            row = db.scalar(
                select(ConnectionModel).where(
                    ConnectionModel.owner_id == owner_id,
                    ConnectionModel.provider == "google",
                )
            )
            if row is None:
                return None
            if row.status == "active" and row.secret_enc != _REVOKED_MARKER:
                try:
                    data = self._cipher.decrypt_json(row.secret_enc)
                    raw_tok = data.get("refresh_token") or data.get("access_token")
                    if isinstance(raw_tok, str) and raw_tok:
                        token_to_revoke = raw_tok
                except VaultConfigError:
                    pass
            row.status = "revoked"
            row.secret_enc = _REVOKED_MARKER
            row.revoked_at = now_iso
            row.updated_at = now_iso
            db.commit()
        return token_to_revoke

    def teardown_session_tokens(self, *, owner_id: str, session_id: str) -> int:
        """Revoke session-scoped tokens on teardown; keep linked refresh tokens."""
        now_iso = self._now_iso()
        revoked_count = 0
        with self._session_factory() as db:
            rows = db.scalars(
                select(ConnectionModel).where(
                    ConnectionModel.owner_id == owner_id,
                    ConnectionModel.status == "active",
                )
            ).all()
            for row in rows:
                if row.token_mode == "session" and (
                    row.session_id is None or row.session_id == session_id
                ):
                    row.status = "revoked"
                    row.secret_enc = _REVOKED_MARKER
                    row.revoked_at = now_iso
                    row.updated_at = now_iso
                    revoked_count += 1
            if revoked_count:
                db.commit()
        return revoked_count

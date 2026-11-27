"""Per-session Squid egress allowlist policy generator (Phase 1, F-09).

This module provides validation and deterministic Squid ACL rule generation
for per-session container egress allowlists.

Security Invariants:
1. Domains are validated strictly as plain DNS hostnames (RFC 1035/1123).
   Schemes, ports, paths, wildcards, IP literals, whitespace, control characters,
   and Squid directive injection attempts are strictly rejected.
2. Source IPs are validated as valid host IP addresses (IPv4 or IPv6).
3. Session rules pair source IP ACLs with destination domain ACLs and end
   with an explicit deny for that source IP, ensuring one session cannot
   cross-grant access to another session's allowlist.
4. Empty allowlists fail closed (traffic from the session IP is denied).

Integration Boundary Note:
This component generates the Squid ACL rules. To achieve live enforcement:
- The session manager must wire the container management IP to this generator.
- The generated rules must be written to the egress proxy configuration
  (e.g., via a shared dynamic config directory or Squid reconfiguration signal).
- The container must have HTTP_PROXY and HTTPS_PROXY pointed to the egress proxy.
Until wired to the proxy reload mechanism, this component generates policy rules
without claiming active enforcement.
"""

from __future__ import annotations

import ipaddress
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# RFC 1123 DNS label regex: 1-63 chars, alphanumeric, hyphens allowed in middle
HOSTNAME_LABEL_REGEX = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# Session ID format: prefixed ULID ('s_...') per docs/CONTRACTS.md Section 1
SESSION_ID_PREFIX = "s_"
ULID_LENGTH = 26
# Crockford's Base32 alphabet: 0-9, A-Z excluding I, L, O, U (26 characters total)
# First character must be in range 0-7 to enforce 128-bit ULID limits
ULID_REGEX = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# Characters forbidden in session IDs to prevent traversal, injection,
# or formatting issues
FORBIDDEN_SESSION_ID_CHARS = {
    "\n",
    "\r",
    "\t",
    "\x00",
    " ",
    ";",
    ":",
    "/",
    "\\",
    "@",
    "!",
    "$",
    "%",
    "^",
    "&",
    "*",
    "(",
    ")",
    "=",
    "+",
    "[",
    "]",
    "{",
    "}",
    "|",
    "`",
    "~",
    "'",
    '"',
    "<",
    ">",
    "?",
    "#",
}

# Characters forbidden anywhere in domain input to prevent directive injection
FORBIDDEN_DOMAIN_CHARS = {
    "\n",
    "\r",
    "\t",
    "\x00",
    " ",
    "#",
    ";",
    ":",
    "/",
    "\\",
    "*",
    "?",
    "[",
    "]",
    "{",
    "}",
    "(",
    ")",
    '"',
    "'",
    "`",
    "<",
    ">",
    "|",
    "&",
    "$",
    "!",
    "%",
    "=",
}


class EgressPolicyError(Exception):
    """Base exception for egress policy generation failures."""


class InvalidDomainError(EgressPolicyError):
    """Raised when a domain violates DNS or security constraints."""


class InvalidIpError(EgressPolicyError):
    """Raised when a source IP address is invalid or not a host address."""


class DuplicateSourceIpError(EgressPolicyError):
    """Raised when a source IP is already registered to another active session."""


DuplicateIpError = DuplicateSourceIpError


class EgressPolicyPersistenceError(EgressPolicyError):
    """Raised when writing the rendered Squid ACL policy to disk fails."""


class InvalidSessionIdError(EgressPolicyError):
    """Raised when a session identifier format is invalid."""


class EmptyAllowlistError(EgressPolicyError):
    """Raised when an allowlist is empty and allow_empty is False."""


def validate_domain(domain: str) -> str:
    """Validate a domain name as a strict, plain DNS hostname.

    Rejects:
    - Schemes ('http://', 'https://', etc.)
    - Ports (':80', ':443')
    - Paths ('/path')
    - Wildcards ('*.example.com', '*')
    - Whitespace and control characters
    - IP literals ('192.168.1.1', '::1')
    - Squid configuration injection characters ('#', ';', newlines)
    - Single-label names ('localhost')
    - Labels with leading or trailing hyphens

    Returns:
        Canonical lowercase domain name.

    Raises:
        InvalidDomainError: If domain is invalid.
    """
    if not isinstance(domain, str):
        raise InvalidDomainError(
            f"Domain must be a string, got {type(domain).__name__}."
        )

    cleaned = domain.strip()
    if not cleaned or cleaned != domain:
        raise InvalidDomainError(
            f"Domain cannot be empty or contain whitespace: {domain!r}"
        )

    # Check for forbidden characters
    for char in FORBIDDEN_DOMAIN_CHARS:
        if char in cleaned:
            raise InvalidDomainError(
                f"Domain contains forbidden character {char!r}: {domain!r}"
            )

    if len(cleaned) > 253:
        raise InvalidDomainError(
            f"Domain exceeds maximum length of 253 characters: {domain!r}"
        )

    lower = cleaned.lower()

    # Reject IP literals (IPv4 and IPv6)
    try:
        ipaddress.ip_address(lower)
        raise InvalidDomainError(
            f"IP literals are not permitted as domain names: {domain!r}"
        )
    except ValueError:
        pass

    labels = lower.split(".")
    if len(labels) < 2:
        raise InvalidDomainError(
            f"Domain must contain at least two labels (e.g. 'example.com'): {domain!r}"
        )

    for label in labels:
        if not label:
            raise InvalidDomainError(
                f"Domain contains empty label (consecutive dots): {domain!r}"
            )
        if len(label) > 63:
            raise InvalidDomainError(f"Domain label exceeds 63 characters: {label!r}")
        if not HOSTNAME_LABEL_REGEX.match(label):
            raise InvalidDomainError(
                f"Domain label does not match RFC 1123 format: {label!r}"
            )

    # Top-level domain cannot be purely numeric (RFC 1123)
    if labels[-1].isdigit():
        raise InvalidDomainError(
            f"Top-level domain cannot be purely numeric: {labels[-1]!r}"
        )

    return lower


def validate_domains(domains: Iterable[str], allow_empty: bool = True) -> list[str]:
    """Validate and deduplicate a collection of domains.

    Returns:
        Sorted list of validated, canonical lowercase domains.

    Raises:
        EmptyAllowlistError: If domains collection is empty and allow_empty is False.
        InvalidDomainError: If any domain in the collection is invalid.
    """
    validated: set[str] = set()
    for item in domains:
        validated.add(validate_domain(item))

    if not validated and not allow_empty:
        raise EmptyAllowlistError(
            "Egress allowlist cannot be empty when allow_empty=False."
        )

    return sorted(validated)


def validate_source_ip(source_ip: str) -> str:
    """Validate that source_ip is a valid, single host IPv4 or IPv6 address.

    Returns:
        Canonical string representation of the IP address.

    Raises:
        InvalidIpError: If source_ip is not a valid host IP address.
    """
    if not isinstance(source_ip, str) or not source_ip.strip():
        raise InvalidIpError("Source IP must be a non-empty string.")

    cleaned = source_ip.strip()
    if cleaned != source_ip:
        raise InvalidIpError(
            f"Source IP cannot contain leading or trailing whitespace: {source_ip!r}"
        )

    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise InvalidIpError(f"Invalid IP address format: {source_ip!r}") from exc

    return str(addr)


def validate_session_id(session_id: str) -> str:
    """Validate session identifier as a prefixed ULID per docs/CONTRACTS.md.

    Guarantees:
    - Must be a non-empty string.
    - Must start with 's_' prefix per CONTRACTS.md Section 1 (session ID).
      Rejects other contract prefixes (p_ plan, a_ approval, u_ undo, e_ event)
      and unprefixed IDs.
    - The body after 's_' must be exactly 26 Crockford Base32 characters.
    - The ULID first character must be in range '0'-'7' (preventing 128-bit overflow).
    - Excludes Crockford Base32 invalid characters: 'I', 'L', 'O', 'U'.
    - Normalizes lowercase ULID characters to canonical uppercase form.
    - Rejects non-ULID identifiers (e.g. 's_test', 's_12345', 's_demo-session-123').

    Returns:
        Canonical session ID ('s_' followed by 26 uppercase Crockford characters).

    Raises:
        InvalidSessionIdError: If session_id format is invalid (fail closed).
    """
    if not isinstance(session_id, str):
        raise InvalidSessionIdError(
            f"Session ID must be a string, got {type(session_id).__name__}."
        )

    cleaned = session_id.strip()
    if not cleaned or cleaned != session_id:
        raise InvalidSessionIdError(
            f"Session ID cannot be empty or contain whitespace: {session_id!r}"
        )

    for char in FORBIDDEN_SESSION_ID_CHARS:
        if char in cleaned:
            raise InvalidSessionIdError(
                f"Session ID contains forbidden character {char!r}: {cleaned!r}"
            )

    if ".." in cleaned:
        raise InvalidSessionIdError(
            f"Path traversal ('..') detected in session ID: {cleaned!r}"
        )

    if not cleaned.startswith(SESSION_ID_PREFIX):
        raise InvalidSessionIdError(
            f"Session ID must start with {SESSION_ID_PREFIX!r} prefix "
            f"per CONTRACTS.md: {cleaned!r}"
        )

    ulid_part = cleaned[len(SESSION_ID_PREFIX) :]
    if len(ulid_part) != ULID_LENGTH:
        raise InvalidSessionIdError(
            f"Session ID ULID must be exactly {ULID_LENGTH} characters after "
            f"prefix, got {len(ulid_part)}: {cleaned!r}"
        )

    upper_ulid = ulid_part.upper()
    if not ULID_REGEX.match(upper_ulid):
        raise InvalidSessionIdError(
            f"Session ID ULID body violates Crockford Base32 specification "
            f"(must match ^[0-7][0-9A-HJKMNP-TV-Z]{{25}}$): {cleaned!r}"
        )

    return f"{SESSION_ID_PREFIX}{upper_ulid}"


@dataclass(frozen=True)
class SessionEgressRule:
    """Immutable representation of a validated per-session egress policy."""

    session_id: str
    source_ip: str
    allowed_domains: tuple[str, ...]

    def to_squid_acl(self) -> str:
        """Render deterministic Squid ACL configuration snippet."""
        return generate_session_squid_acls(
            session_id=self.session_id,
            source_ip=self.source_ip,
            allowed_domains=self.allowed_domains,
        )


def generate_session_squid_acls(
    session_id: str,
    source_ip: str,
    allowed_domains: Iterable[str],
) -> str:
    """Generate deterministic Squid ACL directives for a single session.

    Guarantees:
    - Pairs source IP ACL with destination domain ACL.
    - Ends with an explicit 'http_access deny <session_src>', preventing
      the session from accessing non-allowlisted destinations or matching
      other sessions' allowlists.
    - Fails closed if allowed_domains is empty: traffic from source_ip is denied.

    Returns:
        Formatted multi-line Squid configuration string.
    """
    valid_id = validate_session_id(session_id)
    valid_ip = validate_source_ip(source_ip)
    domains = validate_domains(allowed_domains, allow_empty=True)

    src_acl_name = f"sess_{valid_id}_src"
    dst_acl_name = f"sess_{valid_id}_dst"

    lines: list[str] = [
        f"# Session {valid_id} egress policy",
        f"acl {src_acl_name} src {valid_ip}",
    ]

    if domains:
        domains_str = " ".join(domains)
        lines.append(f"acl {dst_acl_name} dstdomain {domains_str}")
        lines.append(f"http_access allow {src_acl_name} {dst_acl_name}")
        lines.append(f"http_access deny {src_acl_name}")
    else:
        # Fail closed: no domains allowed; explicitly deny all from this session IP
        lines.append("# Fail closed: empty allowlist; no outbound access permitted")
        lines.append(f"http_access deny {src_acl_name}")

    return "\n".join(lines)


def generate_multi_session_squid_config(
    sessions: Iterable[SessionEgressRule],
) -> str:
    """Generate aggregate Squid configuration for multiple active sessions.

    Orders sessions deterministically by session_id.
    Fails closed if duplicate source IPs are detected across sessions.

    Raises:
        DuplicateSourceIpError: If multiple sessions share the same source IP.
    """
    seen_ips: dict[str, str] = {}
    for s in sessions:
        if s.source_ip in seen_ips and seen_ips[s.source_ip] != s.session_id:
            raise DuplicateSourceIpError(
                f"Duplicate source IP {s.source_ip!r} detected across active sessions "
                f"({seen_ips[s.source_ip]!r} and {s.session_id!r})."
            )
        seen_ips[s.source_ip] = s.session_id

    sorted_sessions = sorted(sessions, key=lambda s: s.session_id)
    snippets = [s.to_squid_acl() for s in sorted_sessions]
    return "\n\n".join(snippets)


class EgressPolicyManager:
    """In-memory and persistent manager for session egress policies.

    Exposes a clean interface for session creation and teardown hooks.
    Ensures that two active sessions cannot register the same source IP,
    preventing Squid source-IP ACL collision and cross-application of allowlists.

    When acl_path (or the OVERWATCH_SQUID_ACL_PATH environment variable) is
    configured, every session addition or removal atomically writes the full
    rendered Squid ACL configuration to that path. If disk persistence fails,
    in-memory state is restored and EgressPolicyPersistenceError is raised,
    maintaining a strict fail-closed security boundary.

    Integration Boundary Note:
    - This component generates, validates, and persists Squid ACL rules.
    - Squid reloads via file watching, SIGHUP, or 'squid -k reconfigure'.
    - For live traffic enforcement, the session manager must also attach the
      running proxy container to each session's internal network (sess_net_<id>).
    """

    ENV_SQUID_ACL_PATH: str = "OVERWATCH_SQUID_ACL_PATH"

    def __init__(self, acl_path: Path | str | None = None) -> None:
        self._rules: dict[str, SessionEgressRule] = {}
        self._ip_to_session: dict[str, str] = {}

        configured_path = acl_path
        if configured_path is None:
            env_val = os.environ.get(self.ENV_SQUID_ACL_PATH)
            if env_val and env_val.strip():
                configured_path = env_val.strip()

        self._acl_path: Path | None = (
            Path(configured_path).resolve() if configured_path else None
        )

        if self._acl_path is not None:
            self._write_policy_atomic()

    @property
    def acl_path(self) -> Path | None:
        """Configured path where Squid ACL configuration is persisted, if any."""
        return self._acl_path

    def _write_policy_atomic(self) -> None:
        """Atomically write the current rendered ACL policy to disk.

        Writes to a unique temporary file in the target directory and replaces
        the destination atomically with fsync to guarantee durability and
        prevent partial/corrupted configuration reads by Squid.
        """
        if self._acl_path is None:
            return

        content = self.render_active_config()
        if not content.strip():
            content = "# Overwatch Squid Dynamic Session ACLs - No active sessions\n"
        elif not content.endswith("\n"):
            content += "\n"

        target = self._acl_path
        target.parent.mkdir(parents=True, exist_ok=True)

        temp_name = f".{target.name}.tmp.{os.getpid()}_{uuid.uuid4().hex}"
        temp_file = target.with_name(temp_name)
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, target)
        except Exception as exc:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise EgressPolicyPersistenceError(
                f"Failed to atomically persist Squid ACL configuration to "
                f"'{target}': {exc}"
            ) from exc

    def add_session(
        self,
        session_id: str,
        source_ip: str,
        allowed_domains: Iterable[str],
    ) -> SessionEgressRule:
        """Register a session's egress allowlist and validate inputs.

        Raises:
            InvalidSessionIdError: If session_id format is invalid.
            InvalidIpError: If source_ip format is invalid.
            InvalidDomainError: If any domain is invalid.
            DuplicateSourceIpError: If source_ip is already registered to
                another active session.
            EgressPolicyPersistenceError: If writing to acl_path fails
                (in-memory state is rolled back).
        """
        valid_id = validate_session_id(session_id)
        valid_ip = validate_source_ip(source_ip)
        domains = validate_domains(allowed_domains, allow_empty=True)

        existing_session = self._ip_to_session.get(valid_ip)
        if existing_session is not None and existing_session != valid_id:
            raise DuplicateSourceIpError(
                f"Source IP {valid_ip!r} is already registered to active "
                f"session {existing_session!r}."
            )

        # Snapshot state for rollback on persistence failure
        prev_rule = self._rules.get(valid_id)
        prev_ip_to_session = dict(self._ip_to_session)

        # If re-registering an existing session with a new IP, clear previous IP mapping
        if prev_rule is not None and prev_rule.source_ip != valid_ip:
            self._ip_to_session.pop(prev_rule.source_ip, None)

        rule = SessionEgressRule(
            session_id=valid_id,
            source_ip=valid_ip,
            allowed_domains=tuple(domains),
        )
        self._rules[valid_id] = rule
        self._ip_to_session[valid_ip] = valid_id

        # Persist to disk if configured, rolling back in-memory state on error
        if self._acl_path is not None:
            try:
                self._write_policy_atomic()
            except Exception:
                if prev_rule is not None:
                    self._rules[valid_id] = prev_rule
                else:
                    self._rules.pop(valid_id, None)
                self._ip_to_session = prev_ip_to_session
                raise

        return rule

    def remove_session(self, session_id: str) -> bool:
        """Remove a session from active egress policies upon teardown.

        Raises:
            EgressPolicyPersistenceError: If writing to acl_path fails
                (in-memory state is rolled back).
        """
        valid_id = validate_session_id(session_id)
        prev_rule = self._rules.get(valid_id)
        if prev_rule is None:
            return False

        prev_ip_to_session = dict(self._ip_to_session)
        del self._rules[valid_id]
        self._ip_to_session.pop(prev_rule.source_ip, None)

        if self._acl_path is not None:
            try:
                self._write_policy_atomic()
            except Exception:
                self._rules[valid_id] = prev_rule
                self._ip_to_session = prev_ip_to_session
                raise

        return True

    def get_session(self, session_id: str) -> SessionEgressRule | None:
        """Retrieve registered policy rule for a session."""
        valid_id = validate_session_id(session_id)
        return self._rules.get(valid_id)

    def get_session_by_ip(self, source_ip: str) -> SessionEgressRule | None:
        """Retrieve registered policy rule for a source IP, if any."""
        valid_ip = validate_source_ip(source_ip)
        session_id = self._ip_to_session.get(valid_ip)
        if session_id is None:
            return None
        return self._rules.get(session_id)

    def render_active_config(self) -> str:
        """Render complete aggregate Squid configuration for all active sessions."""
        return generate_multi_session_squid_config(self._rules.values())

    @property
    def active_session_count(self) -> int:
        """Count of currently registered sessions."""
        return len(self._rules)

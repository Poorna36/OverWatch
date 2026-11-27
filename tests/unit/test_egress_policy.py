"""Unit tests for the per-session Squid egress allowlist policy generator (F-09)."""

from pathlib import Path

import pytest
import yaml
from control_plane.sessions.egress_policy import (
    DuplicateIpError,
    DuplicateSourceIpError,
    EgressPolicyError,
    EgressPolicyManager,
    EgressPolicyPersistenceError,
    EmptyAllowlistError,
    InvalidDomainError,
    InvalidIpError,
    InvalidSessionIdError,
    SessionEgressRule,
    generate_multi_session_squid_config,
    generate_session_squid_acls,
    validate_domain,
    validate_domains,
    validate_session_id,
    validate_source_ip,
)

# Standard valid test session IDs conforming to CONTRACTS.md Section 1
# (prefixed Crockford Base32 ULIDs, exactly 26 chars after 's_')
SESSION_1 = "s_01J80001000000000000000001"
SESSION_2 = "s_01J80002000000000000000002"
SESSION_A = "s_01J8AAAA00000000000000000A"
SESSION_B = "s_01J8BBBB00000000000000000B"
SESSION_M = "s_01J8MMMM00000000000000000M"
SESSION_Z = "s_01J8ZZZZ00000000000000000Z"
SESSION_EMPTY = "s_01J8EEEE000000000000000000"
SESSION_DET = "s_01J8DDDD000000000000000000"


class TestExceptionHierarchy:
    """Verify exception hierarchy for egress policy errors."""

    def test_inheritance(self):
        assert issubclass(InvalidDomainError, EgressPolicyError)
        assert issubclass(InvalidIpError, EgressPolicyError)
        assert issubclass(DuplicateSourceIpError, EgressPolicyError)
        assert issubclass(DuplicateIpError, EgressPolicyError)
        assert issubclass(InvalidSessionIdError, EgressPolicyError)
        assert issubclass(EmptyAllowlistError, EgressPolicyError)
        assert issubclass(EgressPolicyPersistenceError, EgressPolicyError)


class TestDomainValidation:
    """Tests for validate_domain() enforcing strict DNS hostname constraints."""

    @pytest.mark.parametrize(
        ("raw_domain", "expected"),
        [
            ("example.com", "example.com"),
            ("EXAMPLE.COM", "example.com"),
            ("pypi.org", "pypi.org"),
            ("files.pythonhosted.org", "files.pythonhosted.org"),
            ("registry.npmjs.org", "registry.npmjs.org"),
            ("github.com", "github.com"),
            (
                "api.v2.service-provider.internal.io",
                "api.v2.service-provider.internal.io",
            ),
            ("a-b-c.example.co.uk", "a-b-c.example.co.uk"),
            ("0-1.example.org", "0-1.example.org"),
        ],
    )
    def test_valid_domains_canonicalized(self, raw_domain: str, expected: str):
        assert validate_domain(raw_domain) == expected

    @pytest.mark.parametrize(
        "invalid_scheme",
        [
            "http://example.com",
            "https://example.com",
            "ftp://files.example.com",
            "ws://socket.example.com",
            "//example.com",
        ],
    )
    def test_reject_schemes(self, invalid_scheme: str):
        with pytest.raises(InvalidDomainError, match="forbidden character"):
            validate_domain(invalid_scheme)

    @pytest.mark.parametrize(
        "invalid_port",
        [
            "example.com:80",
            "example.com:443",
            "example.com:8080",
            ":80",
        ],
    )
    def test_reject_ports(self, invalid_port: str):
        with pytest.raises(InvalidDomainError, match="forbidden character"):
            validate_domain(invalid_port)

    @pytest.mark.parametrize(
        "invalid_path",
        [
            "example.com/",
            "example.com/api",
            "example.com/v1/resource",
            "example.com/index.html",
        ],
    )
    def test_reject_paths(self, invalid_path: str):
        with pytest.raises(InvalidDomainError, match="forbidden character"):
            validate_domain(invalid_path)

    @pytest.mark.parametrize(
        "wildcard_domain",
        [
            "*.example.com",
            "*",
            "example.*",
            "sub.*.example.com",
        ],
    )
    def test_reject_wildcards(self, wildcard_domain: str):
        with pytest.raises(InvalidDomainError, match="forbidden character"):
            validate_domain(wildcard_domain)

    @pytest.mark.parametrize(
        "ip_literal",
        [
            "127.0.0.1",
            "192.168.1.1",
            "10.0.0.1",
            "172.16.0.1",
            "8.8.8.8",
            "0.0.0.0",
            "::1",
            "2001:db8::1",
            "fe80::1",
        ],
    )
    def test_reject_ip_literals(self, ip_literal: str):
        with pytest.raises(InvalidDomainError):
            validate_domain(ip_literal)

    @pytest.mark.parametrize(
        "whitespace_domain",
        [
            " example.com",
            "example.com ",
            " example.com ",
            "ex ample.com",
            "example\t.com",
            "example.com\n",
            "example.com\r\n",
        ],
    )
    def test_reject_whitespace(self, whitespace_domain: str):
        with pytest.raises(InvalidDomainError):
            validate_domain(whitespace_domain)

    @pytest.mark.parametrize(
        "injection_payload",
        [
            "example.com\nacl evil src all",
            "example.com\rhttp_access allow all",
            "example.com; drop table",
            "example.com#comment",
            'example.com"evil',
            "example.com'evil",
            "example.com`whoami`",
            "example.com$(whoami)",
            "example.com|cat",
            "example.com&netstat",
            "example.com!danger",
            "example.com\x00evil",
        ],
    )
    def test_reject_squid_injection_characters(self, injection_payload: str):
        with pytest.raises(InvalidDomainError):
            validate_domain(injection_payload)

    @pytest.mark.parametrize(
        "single_label",
        [
            "localhost",
            "internal",
            "com",
            "org",
            "docker",
        ],
    )
    def test_reject_single_label_hostnames(self, single_label: str):
        with pytest.raises(InvalidDomainError, match="at least two labels"):
            validate_domain(single_label)

    @pytest.mark.parametrize(
        "malformed_dots",
        [
            ".example.com",
            "example.com.",
            "example..com",
            "sub..example.com",
            "...",
        ],
    )
    def test_reject_malformed_dots(self, malformed_dots: str):
        with pytest.raises(InvalidDomainError):
            validate_domain(malformed_dots)

    @pytest.mark.parametrize(
        "bad_hyphens",
        [
            "-example.com",
            "example-.com",
            "sub.-example.com",
            "sub.example-.com",
        ],
    )
    def test_reject_label_boundary_hyphens(self, bad_hyphens: str):
        with pytest.raises(InvalidDomainError, match="RFC 1123"):
            validate_domain(bad_hyphens)

    def test_reject_numeric_tld(self):
        with pytest.raises(InvalidDomainError, match="purely numeric"):
            validate_domain("example.123")

    def test_reject_oversized_label(self):
        long_label = "a" * 64
        with pytest.raises(InvalidDomainError, match="exceeds 63 characters"):
            validate_domain(f"{long_label}.com")

    def test_reject_oversized_domain(self):
        long_domain = ".".join(["label"] * 55) + ".com"  # > 253 chars
        assert len(long_domain) > 253
        with pytest.raises(InvalidDomainError, match="exceeds maximum length"):
            validate_domain(long_domain)

    @pytest.mark.parametrize("non_string", [None, 123, ["example.com"], {}])
    def test_reject_non_string_domain(self, non_string):
        with pytest.raises(InvalidDomainError, match="must be a string"):
            validate_domain(non_string)  # type: ignore[arg-type]


class TestSourceIpValidation:
    """Tests for validate_source_ip() enforcing single host IP constraints."""

    @pytest.mark.parametrize(
        "valid_ip",
        [
            "172.20.0.2",
            "10.0.0.5",
            "192.168.100.10",
            "2001:db8::1",
            "fd00::1234",
        ],
    )
    def test_valid_ips(self, valid_ip: str):
        assert validate_source_ip(valid_ip) == valid_ip

    @pytest.mark.parametrize(
        "invalid_ip",
        [
            "",
            "   ",
            "999.999.999.999",
            "172.20.0.256",
            "not-an-ip",
            "example.com",
            "172.20.0.2/24",  # Reject CIDR subnets; must be host IP
            "172.20.0.2:80",
            " 172.20.0.2",
            "172.20.0.2 ",
        ],
    )
    def test_reject_invalid_ips(self, invalid_ip: str):
        with pytest.raises(InvalidIpError):
            validate_source_ip(invalid_ip)

    @pytest.mark.parametrize("non_string", [None, 172, ["172.20.0.2"]])
    def test_reject_non_string_ip(self, non_string):
        with pytest.raises(InvalidIpError):
            validate_source_ip(non_string)  # type: ignore[arg-type]


class TestSessionIdValidation:
    """Tests for validate_session_id() enforcing Crockford ULID format."""

    @pytest.mark.parametrize(
        ("valid_id", "expected"),
        [
            (
                "s_01J8ABCDEFGH1234567890ABCD",
                "s_01J8ABCDEFGH1234567890ABCD",
            ),
            (
                "s_01ARZ3NDEKTSV4RRFFQ69G5FAV",  # Canonical spec example
                "s_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ),
            (
                "s_7ZZZZZZZZZZZZZZZZZZZZZZZZZ",  # Max 128-bit ULID (starts with 7)
                "s_7ZZZZZZZZZZZZZZZZZZZZZZZZZ",
            ),
            (
                "s_00000000000000000000000000",  # Min ULID value (starts with 0)
                "s_00000000000000000000000000",
            ),
            (
                "s_3K1B4N6M7P8Q9R0S2T3V4W5X6Y",
                "s_3K1B4N6M7P8Q9R0S2T3V4W5X6Y",
            ),
            # Lowercase Crockford Base32 normalized to uppercase canonical form
            (
                "s_01j8abcdefgh1234567890abcd",
                "s_01J8ABCDEFGH1234567890ABCD",
            ),
            (
                "s_01arz3ndektsv4rrffq69g5fav",
                "s_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ),
        ],
    )
    def test_valid_crockford_ulid_session_ids(self, valid_id: str, expected: str):
        assert validate_session_id(valid_id) == expected

    @pytest.mark.parametrize(
        ("invalid_id", "description"),
        [
            # Explicitly removed non-ULID test cases from previous iteration
            ("s_test", "not a 26-char ULID"),
            ("s_demo-session-123", "contains hyphens and wrong length"),
            ("s_SESSION_UNDER_SCORE", "contains underscores, invalid Crockford chars"),
            ("s_12345", "too short"),
            # ULID first-character range violation (must be [0-7])
            ("s_81J8ABCDEFGH1234567890ABCD", "first char 8 is out of range (>7)"),
            ("s_80000000000000000000000000", "first char 8 followed by 25 valid zeros"),
            ("s_91J8ABCDEFGH1234567890ABCD", "first char 9 is out of range (>7)"),
            ("s_A1J8ABCDEFGH1234567890ABCD", "first char A is out of range (>7)"),
            ("s_Z1J8ABCDEFGH1234567890ABCD", "first char Z is out of range (>7)"),
            # Forbidden Crockford Base32 characters: I, L, O, U (uppercase & lowercase)
            ("s_01J8ABCDEFGH1234567890ABCI", "contains forbidden Crockford letter I"),
            ("s_01J8ABCDEFGH1234567890ABCL", "contains forbidden Crockford letter L"),
            ("s_01J8ABCDEFGH1234567890ABCO", "contains forbidden Crockford letter O"),
            ("s_01J8ABCDEFGH1234567890ABCU", "contains forbidden Crockford letter U"),
            ("s_01j8abcdefgh1234567890abci", "contains forbidden Crockford letter i"),
            ("s_01j8abcdefgh1234567890abcl", "contains forbidden Crockford letter l"),
            ("s_01j8abcdefgh1234567890abco", "contains forbidden Crockford letter o"),
            ("s_01j8abcdefgh1234567890abcu", "contains forbidden Crockford letter u"),
            # Invalid lengths after 's_'
            ("s_01J8ABCDEFGH1234567890ABC", "25 chars (too short)"),
            ("s_01J8ABCDEFGH1234567890ABCDEF", "27 chars (too long)"),
            ("s_", "empty body after prefix"),
            # Missing s_ prefix
            ("01J8ABCDEFGH1234567890ABCD", "missing s_ prefix"),
            ("sess-1", "missing s_ prefix"),
            ("sess_1", "missing s_ prefix"),
            ("session-123", "missing s_ prefix"),
            # Other contract prefixes (p_ plan, a_ approval, u_ undo, e_ event)
            ("p_01J8ABCDEFGH1234567890ABCD", "plan contract prefix p_ instead of s_"),
            (
                "a_01J8ABCDEFGH1234567890ABCD",
                "approval contract prefix a_ instead of s_",
            ),
            ("u_01J8ABCDEFGH1234567890ABCD", "undo contract prefix u_ instead of s_"),
            ("e_01J8ABCDEFGH1234567890ABCD", "event contract prefix e_ instead of s_"),
            # Whitespace and formatting issues
            ("", "empty string"),
            ("   ", "whitespace only"),
            (" s_01J8ABCDEFGH1234567890ABCD", "leading whitespace"),
            ("s_01J8ABCDEFGH1234567890ABCD ", "trailing whitespace"),
            ("s_01J8 ABCDEFGH", "internal space"),
            # Traversal, injection, and metacharacters
            ("s_../escape", "path traversal .. in session ID"),
            ("s_path/with/slash", "forward slash in session ID"),
            ("s_path\\backslash", "backslash in session ID"),
            ("s_test;rm -rf /", "shell separator in session ID"),
            ("s_test\x00nullbyte", "null byte in session ID"),
            ("s_test@domain", "special char @ in session ID"),
            ("s_test:colon", "colon in session ID"),
            ("s_test!danger", "exclamation in session ID"),
            ("s_test$var", "dollar sign in session ID"),
            ("s_test|cat", "pipe in session ID"),
            ("s_test&net", "ampersand in session ID"),
            ("s_test`id`", "backtick in session ID"),
        ],
    )
    def test_invalid_contract_session_ids_rejected(
        self, invalid_id: str, description: str
    ):
        with pytest.raises(InvalidSessionIdError):
            validate_session_id(invalid_id)

    @pytest.mark.parametrize("non_string", [None, 12345, ["s_01J8"], {}])
    def test_reject_non_string_session_ids(self, non_string):
        with pytest.raises(InvalidSessionIdError):
            validate_session_id(non_string)  # type: ignore[arg-type]

    def test_egress_policy_manager_normalizes_lowercase_session_id(self):
        """EgressPolicyManager stores and indexes session ID in canonical uppercase."""
        epm = EgressPolicyManager()
        rule = epm.add_session(
            "s_01j8abcdefgh1234567890abcd", "172.30.1.2", ["example.com"]
        )
        assert rule.session_id == "s_01J8ABCDEFGH1234567890ABCD"
        assert epm.get_session("s_01J8ABCDEFGH1234567890ABCD") is not None
        assert epm.get_session("s_01j8abcdefgh1234567890abcd") is not None


class TestValidateDomainsCollection:
    """Tests for validate_domains() batch processing and deduplication."""

    def test_deduplicates_and_sorts_domains(self):
        raw = ["pypi.org", "github.com", "PYPI.ORG", "files.pythonhosted.org"]
        result = validate_domains(raw)
        assert result == ["files.pythonhosted.org", "github.com", "pypi.org"]

    def test_empty_domains_allowed_by_default(self):
        assert validate_domains([]) == []
        assert validate_domains([], allow_empty=True) == []

    def test_empty_domains_fails_when_allow_empty_false(self):
        with pytest.raises(EmptyAllowlistError, match="cannot be empty"):
            validate_domains([], allow_empty=False)

    def test_invalid_domain_in_list_aborts(self):
        with pytest.raises(InvalidDomainError):
            validate_domains(["pypi.org", "invalid..domain", "github.com"])


class TestSquidAclGeneration:
    """Tests for generate_session_squid_acls() and session isolation."""

    def test_single_session_rule_structure(self):
        acl = generate_session_squid_acls(
            session_id=SESSION_1,
            source_ip="172.20.0.5",
            allowed_domains=["pypi.org", "files.pythonhosted.org"],
        )

        lines = acl.strip().split("\n")
        assert lines[0] == f"# Session {SESSION_1} egress policy"
        assert lines[1] == f"acl sess_{SESSION_1}_src src 172.20.0.5"
        assert (
            lines[2]
            == f"acl sess_{SESSION_1}_dst dstdomain files.pythonhosted.org pypi.org"
        )
        assert (
            lines[3] == f"http_access allow sess_{SESSION_1}_src sess_{SESSION_1}_dst"
        )
        assert lines[4] == f"http_access deny sess_{SESSION_1}_src"

    def test_empty_allowlist_fails_closed(self):
        """When a session has an empty allowlist, traffic from its IP is denied."""
        acl = generate_session_squid_acls(
            session_id=SESSION_EMPTY,
            source_ip="172.20.0.9",
            allowed_domains=[],
        )

        assert "http_access allow" not in acl
        assert f"acl sess_{SESSION_EMPTY}_src src 172.20.0.9" in acl
        assert f"http_access deny sess_{SESSION_EMPTY}_src" in acl
        assert "Fail closed" in acl

    def test_session_isolation_guarantee(self):
        """Verify two distinct sessions cannot access each other's allowed domains.

        Each session's block ends with an explicit deny for that session's source IP,
        ensuring that traffic from Session 1 cannot fall through to evaluate Session 2's
        allow rule or any subsequent rules.
        """
        session_a_ip = "172.20.0.2"
        session_a_domains = ["pypi.org"]

        session_b_ip = "172.20.0.3"
        session_b_domains = ["registry.npmjs.org"]

        rule_a = SessionEgressRule(
            session_id=SESSION_A,
            source_ip=session_a_ip,
            allowed_domains=tuple(session_a_domains),
        )
        rule_b = SessionEgressRule(
            session_id=SESSION_B,
            source_ip=session_b_ip,
            allowed_domains=tuple(session_b_domains),
        )

        combined_config = generate_multi_session_squid_config([rule_a, rule_b])

        # Verify Session A block structure
        acl_a_src = f"sess_{SESSION_A}_src"
        acl_a_dst = f"sess_{SESSION_A}_dst"
        acl_b_src = f"sess_{SESSION_B}_src"
        acl_b_dst = f"sess_{SESSION_B}_dst"

        assert f"acl {acl_a_src} src {session_a_ip}" in combined_config
        assert f"acl {acl_a_dst} dstdomain pypi.org" in combined_config
        assert f"http_access allow {acl_a_src} {acl_a_dst}" in combined_config
        assert f"http_access deny {acl_a_src}" in combined_config

        # Verify Session B block structure
        assert f"acl {acl_b_src} src {session_b_ip}" in combined_config
        assert f"acl {acl_b_dst} dstdomain registry.npmjs.org" in combined_config
        assert f"http_access allow {acl_b_src} {acl_b_dst}" in combined_config
        assert f"http_access deny {acl_b_src}" in combined_config

        # Confirm order: Session A's deny comes before Session B's allow
        deny_a_pos = combined_config.index(f"http_access deny {acl_a_src}")
        allow_b_pos = combined_config.index(
            f"http_access allow {acl_b_src} {acl_b_dst}"
        )
        assert deny_a_pos < allow_b_pos, "Session A deny must precede Session B rules"

        # Check that Session A src IP is never referenced in Session B dst allow
        assert f"{acl_a_src} {acl_b_dst}" not in combined_config
        assert f"{acl_b_src} {acl_a_dst}" not in combined_config

    def test_determinism_across_orderings(self):
        """Different ordering of the same domain input produces identical output."""
        order1 = ["github.com", "pypi.org", "files.pythonhosted.org"]
        order2 = ["pypi.org", "files.pythonhosted.org", "github.com"]

        output1 = generate_session_squid_acls(SESSION_DET, "172.20.0.4", order1)
        output2 = generate_session_squid_acls(SESSION_DET, "172.20.0.4", order2)

        assert output1 == output2


class TestEgressPolicyManager:
    """Tests for EgressPolicyManager lifecycle and duplicate IP rejection."""

    def test_manager_lifecycle(self):
        mgr = EgressPolicyManager()
        assert mgr.active_session_count == 0

        # Add session
        rule = mgr.add_session(
            session_id=SESSION_1,
            source_ip="172.20.0.10",
            allowed_domains=["github.com", "pypi.org"],
        )
        assert mgr.active_session_count == 1
        assert rule.session_id == SESSION_1
        assert rule.source_ip == "172.20.0.10"
        assert rule.allowed_domains == ("github.com", "pypi.org")

        # Retrieve session
        retrieved = mgr.get_session(SESSION_1)
        assert retrieved == rule

        # Retrieve by IP
        by_ip = mgr.get_session_by_ip("172.20.0.10")
        assert by_ip == rule

        # Render active config
        config = mgr.render_active_config()
        assert f"acl sess_{SESSION_1}_src src 172.20.0.10" in config
        assert f"http_access allow sess_{SESSION_1}_src sess_{SESSION_1}_dst" in config

        # Remove session
        removed = mgr.remove_session(SESSION_1)
        assert removed is True
        assert mgr.active_session_count == 0
        assert mgr.get_session(SESSION_1) is None
        assert mgr.get_session_by_ip("172.20.0.10") is None

        # Remove non-existent
        assert mgr.remove_session(SESSION_1) is False

    def test_duplicate_source_ip_rejected(self):
        """Reject registering the same source IP for two distinct active sessions."""
        mgr = EgressPolicyManager()

        # Register Session 1 on 172.20.0.2
        mgr.add_session(SESSION_1, "172.20.0.2", ["pypi.org"])

        # Attempting to register Session 2 with the same source IP must fail
        with pytest.raises(
            DuplicateSourceIpError, match="already registered"
        ) as exc_info:
            mgr.add_session(SESSION_2, "172.20.0.2", ["registry.npmjs.org"])

        assert "172.20.0.2" in str(exc_info.value)
        assert SESSION_1 in str(exc_info.value)
        # DuplicateIpError alias also catches it
        assert isinstance(exc_info.value, DuplicateIpError)

        # Confirm only Session 1 remains active
        assert mgr.active_session_count == 1
        assert mgr.get_session(SESSION_1) is not None
        assert mgr.get_session(SESSION_2) is None

    def test_releasing_ip_on_session_removal(self):
        """Once a session is removed, its source IP can be reassigned."""
        mgr = EgressPolicyManager()

        mgr.add_session(SESSION_1, "172.20.0.2", ["pypi.org"])
        mgr.remove_session(SESSION_1)

        # Now Session 2 can take 172.20.0.2
        rule2 = mgr.add_session(SESSION_2, "172.20.0.2", ["registry.npmjs.org"])
        assert rule2.session_id == SESSION_2
        assert mgr.active_session_count == 1
        assert mgr.get_session_by_ip("172.20.0.2") == rule2

    def test_same_session_ip_update_allowed(self):
        """Updating allowlist for the same session ID with the same IP is permitted."""
        mgr = EgressPolicyManager()

        mgr.add_session(SESSION_1, "172.20.0.5", ["pypi.org"])
        updated = mgr.add_session(SESSION_1, "172.20.0.5", ["pypi.org", "github.com"])

        assert updated.allowed_domains == ("github.com", "pypi.org")
        assert mgr.active_session_count == 1

    def test_session_ip_change_frees_old_ip(self):
        """When an existing session changes its IP, its former IP is freed."""
        mgr = EgressPolicyManager()

        # Session 1 starts on .5
        mgr.add_session(SESSION_1, "172.20.0.5", ["pypi.org"])
        # Session 1 moves to .6
        mgr.add_session(SESSION_1, "172.20.0.6", ["pypi.org"])

        # Now Session 2 can use .5 without conflict
        rule2 = mgr.add_session(SESSION_2, "172.20.0.5", ["github.com"])
        assert rule2.session_id == SESSION_2
        assert mgr.active_session_count == 2

    def test_multi_session_config_generator_rejects_duplicate_ips(self):
        """Multi-session config generator fails closed if duplicate IPs are passed."""
        rule_a = SessionEgressRule(SESSION_A, "172.20.0.2", ("pypi.org",))
        rule_b = SessionEgressRule(SESSION_B, "172.20.0.2", ("registry.npmjs.org",))

        with pytest.raises(DuplicateSourceIpError, match="Duplicate source IP"):
            generate_multi_session_squid_config([rule_a, rule_b])

    def test_manager_deterministic_multi_session_rendering(self):
        mgr = EgressPolicyManager()
        # Add sessions out of order with distinct IPs
        mgr.add_session(SESSION_Z, "172.20.0.26", ["pypi.org"])
        mgr.add_session(SESSION_A, "172.20.0.2", ["github.com"])
        mgr.add_session(SESSION_M, "172.20.0.13", ["registry.npmjs.org"])

        rendered = mgr.render_active_config()
        # SESSION_A should appear before SESSION_M which should appear before SESSION_Z
        pos_a = rendered.index(f"Session {SESSION_A}")
        pos_m = rendered.index(f"Session {SESSION_M}")
        pos_z = rendered.index(f"Session {SESSION_Z}")
        assert pos_a < pos_m < pos_z


class TestEgressPolicyPersistence:
    """Tests for atomic persistence and rollback guarantees in EgressPolicyManager."""

    def test_persistence_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(EgressPolicyManager.ENV_SQUID_ACL_PATH, raising=False)
        mgr = EgressPolicyManager()
        assert mgr.acl_path is None

    def test_persistence_initializes_empty_file(self, tmp_path: Path):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)
        assert mgr.acl_path == acl_file.resolve()
        assert acl_file.is_file()
        content = acl_file.read_text(encoding="utf-8")
        assert "No active sessions" in content

    def test_persistence_env_var_configuration(self, tmp_path: Path, monkeypatch):
        acl_file = tmp_path / "env_sessions.conf"
        monkeypatch.setenv(EgressPolicyManager.ENV_SQUID_ACL_PATH, str(acl_file))
        mgr = EgressPolicyManager()
        assert mgr.acl_path == acl_file.resolve()
        assert acl_file.is_file()

    def test_atomic_persistence_on_add_and_remove(self, tmp_path: Path):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)

        # 1. Add Session 1
        mgr.add_session(SESSION_1, "172.20.0.10", ["pypi.org"])
        disk_content_1 = acl_file.read_text(encoding="utf-8")
        assert f"# Session {SESSION_1} egress policy" in disk_content_1
        assert "172.20.0.10" in disk_content_1
        assert "dstdomain pypi.org" in disk_content_1
        assert disk_content_1 == mgr.render_active_config() + "\n"

        # 2. Add Session 2
        mgr.add_session(SESSION_2, "172.20.0.11", ["registry.npmjs.org"])
        disk_content_2 = acl_file.read_text(encoding="utf-8")
        assert f"# Session {SESSION_1}" in disk_content_2
        assert f"# Session {SESSION_2}" in disk_content_2
        assert disk_content_2 == mgr.render_active_config() + "\n"

        # 3. Remove Session 1
        mgr.remove_session(SESSION_1)
        disk_content_3 = acl_file.read_text(encoding="utf-8")
        assert f"# Session {SESSION_1}" not in disk_content_3
        assert f"# Session {SESSION_2}" in disk_content_3

        # 4. Remove Session 2 (now empty)
        mgr.remove_session(SESSION_2)
        disk_content_4 = acl_file.read_text(encoding="utf-8")
        assert "No active sessions" in disk_content_4

    def test_rollback_on_add_failure_fails_closed(self, tmp_path: Path, monkeypatch):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)

        def mock_failing_write():
            raise OSError("Simulated disk I/O failure")

        monkeypatch.setattr(mgr, "_write_policy_atomic", mock_failing_write)

        with pytest.raises(OSError, match="Simulated disk I/O failure"):
            mgr.add_session(SESSION_1, "172.20.0.2", ["pypi.org"])

        # Verify in-memory state is completely rolled back
        assert mgr.active_session_count == 0
        assert mgr.get_session(SESSION_1) is None
        assert mgr.get_session_by_ip("172.20.0.2") is None

        # Verify disk file is unchanged (contains empty stub from init)
        content = acl_file.read_text(encoding="utf-8")
        assert "No active sessions" in content

    def test_rollback_on_update_failure_restores_previous_state(
        self, tmp_path: Path, monkeypatch
    ):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)

        # Initially add Session 1 cleanly
        mgr.add_session(SESSION_1, "172.20.0.2", ["pypi.org"])
        assert mgr.active_session_count == 1

        def mock_failing_write():
            raise OSError("Disk full")

        monkeypatch.setattr(mgr, "_write_policy_atomic", mock_failing_write)

        with pytest.raises(OSError, match="Disk full"):
            mgr.add_session(SESSION_1, "172.20.0.2", ["github.com"])

        # In-memory rule should have reverted to original domains
        restored_rule = mgr.get_session(SESSION_1)
        assert restored_rule is not None
        assert restored_rule.allowed_domains == ("pypi.org",)
        assert mgr.get_session_by_ip("172.20.0.2") == restored_rule

    def test_rollback_on_remove_failure_fails_closed(self, tmp_path: Path, monkeypatch):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)

        # Register Session 1 cleanly
        mgr.add_session(SESSION_1, "172.20.0.2", ["pypi.org"])

        def mock_failing_write():
            raise OSError("Permission denied")

        monkeypatch.setattr(mgr, "_write_policy_atomic", mock_failing_write)

        with pytest.raises(OSError, match="Permission denied"):
            mgr.remove_session(SESSION_1)

        # In-memory rule must be preserved (not removed)
        assert mgr.active_session_count == 1
        assert mgr.get_session(SESSION_1) is not None
        assert mgr.get_session_by_ip("172.20.0.2") is not None

    def test_no_leftover_temp_files_on_failure(self, tmp_path: Path, monkeypatch):
        acl_file = tmp_path / "sessions.conf"
        mgr = EgressPolicyManager(acl_path=acl_file)

        real_open = open

        def failing_open(file, *args, **kwargs):
            if ".tmp." in str(file):
                raise PermissionError("Write blocked")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)

        with pytest.raises(EgressPolicyPersistenceError):
            mgr.add_session(SESSION_1, "172.20.0.5", ["pypi.org"])

        # No temp files should remain in the directory
        temp_files = list(tmp_path.glob(".*tmp*"))
        assert temp_files == []


class TestShippedProfilesCompatibility:
    """Verify that all domains in repository profiles comply with egress policy.

    Ensures both coding.yaml and general.yaml define valid DNS hostnames.
    """

    def test_coding_profile_domains_validate(self):
        profile_path = Path("/home/zogratis/Work/OverWatch/profiles/coding.yaml")
        assert profile_path.exists()
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        domains = data.get("egress_allowlist", [])
        assert domains, "coding profile should define an egress allowlist"

        validated = validate_domains(domains)
        assert len(validated) == len(domains)
        for domain in domains:
            assert domain.lower() in validated

    def test_general_profile_domains_validate(self):
        profile_path = Path("/home/zogratis/Work/OverWatch/profiles/general.yaml")
        if not profile_path.exists():
            pytest.skip("general.yaml not present in this workspace")
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        domains = data.get("egress_allowlist", [])
        assert domains, "general profile should define an egress allowlist"

        validated = validate_domains(domains)
        assert len(validated) == len(domains)
        for domain in domains:
            assert domain.lower() in validated

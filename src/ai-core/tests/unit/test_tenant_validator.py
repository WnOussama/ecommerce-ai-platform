"""
Tests — TenantIDValidator

Covers:
- Valid tenant IDs accepted
- Invalid formats rejected
- Injection attack detection (SQL, path traversal, XSS, template)
- Edge cases (None, empty, whitespace, too long)
- sanitize_for_logging safety
- is_valid convenience method
"""

import pytest

from app.core.security.tenant_validator import (
    TenantIDValidator,
    TenantValidationError,
)


class TestTenantIDValidatorAccepts:
    """Valid tenant IDs must pass validation."""

    @pytest.mark.parametrize("tenant_id", [
        "tenant_abcdefgh",         # 8 chars
        "tenant_12345678",         # numeric
        "tenant_abc123def456",     # mixed
        "tenant_a" * 1 + "b" * 7, # exactly 8
        "tenant_" + "x" * 32,     # max 32 chars after prefix
    ], ids=["alpha_8", "numeric_8", "mixed", "exact_min", "exact_max"])
    def test_accepts_valid_tenant_ids(self, tenant_id):
        result = TenantIDValidator.validate(tenant_id)
        assert result == tenant_id


class TestTenantIDValidatorRejectsFormat:
    """Invalid formats must be rejected."""

    @pytest.mark.parametrize("tenant_id,reason", [
        ("", "empty"),
        ("   ", "whitespace_only"),
        ("abc12345678", "missing_prefix"),
        ("TENANT_ABCDEFGH", "uppercase"),
        ("tenant_abc", "too_short"),
        ("tenant_" + "a" * 33, "too_long_suffix"),
        ("tenant_abc-def-ghi", "has_dashes"),
        ("tenant_abc_def_ghi", "has_underscores_in_id"),
        ("tenant_ABC12345", "uppercase_in_id"),
    ])
    def test_rejects_invalid_format(self, tenant_id, reason):
        with pytest.raises(TenantValidationError):
            TenantIDValidator.validate(tenant_id)


class TestTenantIDValidatorRejectsNullish:
    """None and non-string inputs must be rejected."""

    def test_rejects_none(self):
        with pytest.raises(TenantValidationError, match="required"):
            TenantIDValidator.validate(None)

    def test_rejects_empty_string(self):
        with pytest.raises(TenantValidationError, match="empty"):
            TenantIDValidator.validate("")

    def test_rejects_whitespace(self):
        with pytest.raises(TenantValidationError, match="empty"):
            TenantIDValidator.validate("   ")

    def test_rejects_too_long(self):
        long_id = "a" * 51
        with pytest.raises(TenantValidationError, match="too long"):
            TenantIDValidator.validate(long_id)


class TestTenantIDValidatorDetectsInjections:
    """Injection attack patterns must be detected and blocked."""

    @pytest.mark.parametrize("malicious_input,attack_type", [
        ("tenant'; DROP TABLE--", "sql_injection"),
        ("tenant_abc/../../../etc/passwd", "path_traversal"),
        ("tenant_<script>alert(1)</script>", "xss"),
        ("tenant_${env.SECRET}", "template_injection"),
        ("tenant_{{config}}", "jinja_injection"),
        ("tenant_abc--comment", "sql_comment"),
        ("tenant_abc/**/or", "sql_block_comment"),
        ("tenant_abc%2F%2E%2E", "url_encoding"),
        ("tenant_abc\r\nX-Injected: true", "header_injection"),
        ("tenant_abc\x00null", "null_byte"),
    ], ids=lambda x: x if isinstance(x, str) and len(x) < 25 else "")
    def test_detects_injection_patterns(self, malicious_input, attack_type):
        with pytest.raises(TenantValidationError):
            TenantIDValidator.validate(malicious_input)


class TestTenantIDValidatorIsValid:
    """is_valid() convenience method."""

    def test_returns_true_for_valid(self):
        assert TenantIDValidator.is_valid("tenant_abcdefgh") is True

    def test_returns_false_for_none(self):
        assert TenantIDValidator.is_valid(None) is False

    def test_returns_false_for_invalid(self):
        assert TenantIDValidator.is_valid("bad-id") is False

    def test_returns_false_for_injection(self):
        assert TenantIDValidator.is_valid("tenant'; DROP--") is False


class TestTenantIDValidatorSanitizeForLogging:
    """sanitize_for_logging() must never leak dangerous content into logs."""

    def test_sanitize_none(self):
        assert TenantIDValidator.sanitize_for_logging(None) == "<none>"

    def test_sanitize_valid_id(self):
        result = TenantIDValidator.sanitize_for_logging("tenant_abcdefgh")
        assert "tenant_abcdefgh" in result

    def test_sanitize_replaces_special_chars(self):
        result = TenantIDValidator.sanitize_for_logging("tenant'; DROP TABLE")
        assert "'" not in result
        assert ";" not in result
        assert "?" in result  # replaced with ?

    def test_sanitize_truncates_long_input(self):
        long_input = "a" * 100
        result = TenantIDValidator.sanitize_for_logging(long_input)
        assert len(result) < 40  # truncated + "..."
        assert result.endswith("...")

    def test_sanitize_non_string(self):
        result = TenantIDValidator.sanitize_for_logging(12345)
        assert result == "<invalid_type>"


class TestTenantValidationErrorAttributes:
    """TenantValidationError stores context for debugging."""

    def test_error_has_message(self):
        try:
            TenantIDValidator.validate(None)
        except TenantValidationError as e:
            assert e.message
            assert "required" in e.message.lower()

    def test_error_has_tenant_id(self):
        try:
            TenantIDValidator.validate("bad_id")
        except TenantValidationError as e:
            assert e.tenant_id is not None


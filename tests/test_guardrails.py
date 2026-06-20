"""
tests/test_guardrails.py — Unit tests for BE-07 guardrail spine.

Tests:
  - check_gcloud_verb: rejects mutating verb → GuardrailReadonlyViolation + audit log
  - check_tool_allowed: denies off-allowlist tool → GuardrailAllowlistViolation + audit log
  - demo_blocked_write: always raises + logs exactly one trip
  - redact(): removes planted fake secret value but keeps name
"""

import pytest

from launchguard.guardrails.audit import get_audit_logger
from launchguard.guardrails.enforce import (
    GuardrailAllowlistViolation,
    GuardrailReadonlyViolation,
    check_gcloud_verb,
    check_tool_allowed,
    demo_blocked_write,
)
from launchguard.guardrails.redact import REDACTED_MARKER, redact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_audit_log():
    """Reset the audit logger before each test for isolation."""
    logger = get_audit_logger()
    logger.reset()
    yield
    logger.reset()


# ---------------------------------------------------------------------------
# check_gcloud_verb tests
# ---------------------------------------------------------------------------

class TestCheckGcloudVerb:
    def test_read_verb_passes(self):
        """describe/list/get-iam-policy should not raise."""
        for verb in ("describe", "list", "get-iam-policy"):
            check_gcloud_verb(verb)  # Must not raise

    def test_mutating_verb_raises(self):
        """A mutating verb must raise GuardrailReadonlyViolation."""
        with pytest.raises(GuardrailReadonlyViolation) as exc_info:
            check_gcloud_verb("delete")
        assert exc_info.value.code == "GUARDRAIL_READONLY_VIOLATION"
        assert "delete" in exc_info.value.message

    def test_mutating_verb_add_iam_policy_binding(self):
        """The hero mutating verb must be blocked."""
        with pytest.raises(GuardrailReadonlyViolation) as exc_info:
            check_gcloud_verb("add-iam-policy-binding")
        assert "add-iam-policy-binding" in exc_info.value.message

    def test_mutating_verb_logs_guardrail_trip(self):
        """A mutating verb must log exactly one guardrail trip."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailReadonlyViolation):
            check_gcloud_verb("update")

        trips = logger.get_guardrail_trips()
        assert len(trips) == 1
        assert trips[0].code == "GUARDRAIL_READONLY_VIOLATION"
        assert trips[0].blocked is True

    def test_mutating_verb_logs_before_exec(self):
        """The trip must be logged BEFORE any execution — no side effects after."""
        logger = get_audit_logger()
        # confirm trip logged even though we catch the exception immediately
        try:
            check_gcloud_verb("set-iam-policy")
        except GuardrailReadonlyViolation:
            pass
        trips = logger.get_guardrail_trips()
        assert len(trips) == 1

    def test_create_verb_raises(self):
        """gcloud create is mutating — must be rejected."""
        with pytest.raises(GuardrailReadonlyViolation):
            check_gcloud_verb("create")


# ---------------------------------------------------------------------------
# check_tool_allowed tests
# ---------------------------------------------------------------------------

class TestCheckToolAllowed:
    def test_allowed_tool_passes(self):
        """An allowed tool should not raise."""
        check_tool_allowed("RepoAuditor", "parse_dockerfile")
        check_tool_allowed("RepoAuditor", "read_file")
        check_tool_allowed("GcpStateInspector", "gcloud_read")
        check_tool_allowed("FixWriter", "propose_patch")

    def test_off_allowlist_tool_raises(self):
        """A tool not on the agent's list must raise GuardrailAllowlistViolation."""
        with pytest.raises(GuardrailAllowlistViolation) as exc_info:
            check_tool_allowed("RepoAuditor", "gcloud_read")
        assert exc_info.value.code == "GUARDRAIL_ALLOWLIST_VIOLATION"
        assert "RepoAuditor" in exc_info.value.message
        assert "gcloud_read" in exc_info.value.message

    def test_reconciler_has_no_tools(self):
        """Reconciler has an empty allow-list — any tool must be denied."""
        with pytest.raises(GuardrailAllowlistViolation):
            check_tool_allowed("Reconciler", "read_file")

    def test_orchestrator_has_no_tools(self):
        """Orchestrator has an empty allow-list — any tool must be denied."""
        with pytest.raises(GuardrailAllowlistViolation):
            check_tool_allowed("Orchestrator", "gcloud_read")

    def test_off_allowlist_logs_trip(self):
        """An off-allowlist call must log exactly one guardrail trip."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailAllowlistViolation):
            check_tool_allowed("RepoAuditor", "propose_patch")

        trips = logger.get_guardrail_trips()
        assert len(trips) == 1
        assert trips[0].code == "GUARDRAIL_ALLOWLIST_VIOLATION"
        assert trips[0].blocked is True

    def test_unknown_agent_raises(self):
        """An unknown agent name effectively has no allowed tools."""
        with pytest.raises(GuardrailAllowlistViolation):
            check_tool_allowed("UnknownAgent", "read_file")


# ---------------------------------------------------------------------------
# demo_blocked_write tests
# ---------------------------------------------------------------------------

class TestDemoBlockedWrite:
    def test_always_raises(self):
        """demo_blocked_write must always raise GuardrailReadonlyViolation."""
        with pytest.raises(GuardrailReadonlyViolation) as exc_info:
            demo_blocked_write()
        assert exc_info.value.code == "GUARDRAIL_READONLY_VIOLATION"

    def test_logs_exactly_one_trip(self):
        """demo_blocked_write must log exactly ONE trip."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailReadonlyViolation):
            demo_blocked_write()
        trips = logger.get_guardrail_trips()
        assert len(trips) == 1
        assert trips[0].code == "GUARDRAIL_READONLY_VIOLATION"

    def test_trip_detail_contains_mutating_verb(self):
        """The trip detail must name the mutating verb that was blocked."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailReadonlyViolation):
            demo_blocked_write()
        trip = logger.get_guardrail_trips()[0]
        assert "add-iam-policy-binding" in trip.detail


# ---------------------------------------------------------------------------
# redact() tests
# ---------------------------------------------------------------------------

class TestRedact:
    def test_removes_planted_fake_secret_value(self):
        """A long base64-like value must be redacted."""
        payload = {
            "name": "MY_API_KEY",
            "api_key": "AIzaSyD1234567890abcdefghijklmnopqrstuvwxyz",
        }
        result = redact(payload)
        assert result["name"] == "MY_API_KEY"  # Name preserved
        assert result["api_key"] == REDACTED_MARKER  # Value masked

    def test_keeps_secret_name(self):
        """Secret NAMES must be preserved — only values are masked."""
        payload = {"name": "JWT_SECRET_KEY", "accessor_members": []}
        result = redact(payload)
        assert result["name"] == "JWT_SECRET_KEY"
        assert result["accessor_members"] == []

    def test_masks_password_field(self):
        """A 'password' key must have its value masked."""
        payload = {"password": "super-secret-password-123"}
        result = redact(payload)
        assert result["password"] == REDACTED_MARKER

    def test_masks_connection_string(self):
        """postgres:// connection strings must be masked."""
        text = "postgresql://user:secret@host:5432/db"
        result = redact(text)
        assert "secret" not in result or result == REDACTED_MARKER

    def test_preserves_safe_fields(self):
        """Fields like source, locator, code, message must pass through."""
        payload = {
            "source": "intended",
            "locator": "Dockerfile:12",
            "code": "GUARDRAIL_READONLY_VIOLATION",
            "message": "Mutating verb rejected",
            "confidence": 0.95,
            "verdict": "BLOCKED",
        }
        result = redact(payload)
        assert result["source"] == "intended"
        assert result["locator"] == "Dockerfile:12"
        assert result["code"] == "GUARDRAIL_READONLY_VIOLATION"
        assert result["confidence"] == 0.95

    def test_redact_nested_dict(self):
        """Redaction must work recursively through nested dicts."""
        payload = {
            "outer": {
                "name": "SECRET_NAME",
                "token": "ghp_1234567890abcdefghij1234567890ab1234",
            }
        }
        result = redact(payload)
        assert result["outer"]["name"] == "SECRET_NAME"
        assert result["outer"]["token"] == REDACTED_MARKER

    def test_redact_list(self):
        """Redaction must work through lists."""
        payload = [
            {"name": "good_name"},
            {"password": "bad_value_should_be_masked"},
        ]
        result = redact(payload)
        assert result[0]["name"] == "good_name"
        assert result[1]["password"] == REDACTED_MARKER

    def test_scalars_unchanged(self):
        """Integers, booleans, None must pass through unchanged."""
        assert redact(42) == 42
        assert redact(True) is True
        assert redact(None) is None
        assert redact(3.14) == 3.14

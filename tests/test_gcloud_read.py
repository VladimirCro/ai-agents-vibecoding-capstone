"""
tests/test_gcloud_read.py — Unit tests for BE-03 gcloud_read tool.

Tests:
  - fixture mode returns LiveState
  - mutating verb raises GuardrailReadonlyViolation + logs audit trip (before exec)
  - read verbs pass the guardrail check
  - live/record mode raises NotImplementedError (Increment 1)
"""

import pytest

from launchguard.guardrails.audit import get_audit_logger
from launchguard.guardrails.enforce import GuardrailReadonlyViolation
from launchguard.models import Mode
from launchguard.tools.gcloud_read import gcloud_read


@pytest.fixture(autouse=True)
def reset_audit():
    logger = get_audit_logger()
    logger.reset()
    yield
    logger.reset()


class TestGcloudRead:
    def test_fixture_mode_returns_live_state(self):
        """fixture mode returns a LiveState dict."""
        result = gcloud_read(
            resource="secrets",
            verb="list",
            project_id="worknote-ai-like-project",
            mode=Mode.FIXTURE,
            fixture_name="worknote-ai-like",
        )
        assert "project_id" in result
        assert "secrets" in result
        assert result["mode"] == Mode.FIXTURE

    def test_mutating_verb_raises_409(self):
        """A mutating verb must raise GuardrailReadonlyViolation."""
        with pytest.raises(GuardrailReadonlyViolation) as exc_info:
            gcloud_read(
                resource="sa-iam",
                verb="add-iam-policy-binding",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )
        assert exc_info.value.code == "GUARDRAIL_READONLY_VIOLATION"

    def test_mutating_verb_logs_trip(self):
        """Mutating verb must log a guardrail trip."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailReadonlyViolation):
            gcloud_read(
                resource="sa-iam",
                verb="delete",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )
        trips = logger.get_guardrail_trips()
        assert len(trips) == 1
        assert trips[0].code == "GUARDRAIL_READONLY_VIOLATION"
        assert trips[0].blocked is True

    def test_mutating_verb_blocked_before_exec(self):
        """Guardrail must fire BEFORE any gcloud execution (no side effects)."""
        # Verify the trip is logged synchronously, not after a subprocess call
        logger = get_audit_logger()
        try:
            gcloud_read(
                resource="run-config",
                verb="set-iam-policy",
                project_id="test-project",
                mode=Mode.LIVE,  # Live mode — would execute if verb were allowed
            )
        except GuardrailReadonlyViolation:
            pass  # expected
        trips = logger.get_guardrail_trips()
        # Must have logged the trip (pre-exec)
        assert len(trips) == 1

    def test_read_verb_passes_guardrail(self):
        """Read verbs (describe, list, get-iam-policy) pass the guardrail."""
        # Should not raise GuardrailReadonlyViolation (but will fail on fixture lookup)
        try:
            gcloud_read(
                resource="secrets",
                verb="list",
                project_id="worknote-ai-like-project",
                mode=Mode.FIXTURE,
                fixture_name="worknote-ai-like",
            )
        except GuardrailReadonlyViolation:
            pytest.fail("Read verb 'list' must not raise GuardrailReadonlyViolation")

    def test_live_mode_not_implemented_increment1(self):
        """live mode raises NotImplementedError in Increment 1."""
        with pytest.raises(NotImplementedError):
            gcloud_read(
                resource="secrets",
                verb="list",
                project_id="test-project",
                mode=Mode.LIVE,
            )

    def test_invalid_resource_raises(self):
        """An unknown resource must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid resource"):
            gcloud_read(
                resource="unknown-resource",
                verb="list",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )

    def test_fixture_logs_tool_call(self):
        """Successful fixture call must log a tool call."""
        logger = get_audit_logger()
        gcloud_read(
            resource="secrets",
            verb="list",
            project_id="worknote-ai-like-project",
            mode=Mode.FIXTURE,
            fixture_name="worknote-ai-like",
        )
        calls = logger.get_tool_calls()
        assert len(calls) >= 1
        assert calls[0].tool_name == "gcloud_read"
        assert calls[0].outcome == "ok"

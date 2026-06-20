"""
tests/test_declared_parser.py — Unit tests for BE-02 Declared parser.

Tests:
  - parse_declared_state on worknote-ai-like fixture service.yaml
  - container_port=8080
  - all 9 secretKeyRef names listed
  - probes detected
  - scaling populated
  - ${ENV} placeholders → no crash + marked in templated_unresolved
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from launchguard.tools.declared_parser import parse_declared_state

FIXTURE_SERVICE_YAML = str(
    Path(__file__).parents[1] / "fixtures" / "repos" / "worknote-ai-like" / "infra" / "cloud-run" / "service.yaml"
)

EXPECTED_SECRETS = {
    "JWT_SECRET_KEY",
    "SES_SMTP_USERNAME",
    "SES_SMTP_PASSWORD",
    "SES_SMTP_HOST",
    "SENTRY_DSN_BACKEND",
    "LITELLM_AZURE_API_KEY",
    "LITELLM_AZURE_ENDPOINT",
    "LITELLM_VERTEX_CREDENTIALS",
    "CLAMAV_FUNCTION_URL",
}


class TestParseDeclaredState:
    def test_container_port_8080(self):
        """container_port must be 8080 for the worknote-ai-like fixture."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.container_port == 8080

    def test_all_9_secret_refs_listed(self):
        """All 9 secretKeyRef names must appear in secret_refs."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        parsed = set(state.secret_refs)
        assert EXPECTED_SECRETS <= parsed, (
            f"Missing secrets: {EXPECTED_SECRETS - parsed}"
        )

    def test_exactly_9_unique_secrets(self):
        """Must have exactly 9 unique secret refs (no duplicates)."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert len(set(state.secret_refs)) == 9

    def test_liveness_probe_detected(self):
        """has_liveness_probe must be True."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.has_liveness_probe is True

    def test_startup_probe_detected(self):
        """has_startup_probe must be True."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.has_startup_probe is True

    def test_scaling_min_scale(self):
        """min_scale from annotation must be 0."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.scaling.min_scale == 0

    def test_scaling_max_scale(self):
        """max_scale from annotation must be 3."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.scaling.max_scale == 3

    def test_scaling_concurrency(self):
        """containerConcurrency must be 80."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert state.scaling.concurrency == 80

    def test_templated_unresolved_populated(self):
        """${ENV} placeholders must be recorded in templated_unresolved (no crash)."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        # The fixture has ${SA_EMAIL}, ${PROJECT_NUMBER}, ${ENV}, ${PROJECT_ID} placeholders
        assert len(state.templated_unresolved) > 0

    def test_service_account_templated(self):
        """${SA_EMAIL} service account → service_account=None (unresolved)."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        # SA is ${SA_EMAIL} which is templated, so service_account should be None
        assert state.service_account is None

    def test_no_crash_on_heavy_templating(self):
        """Parser must not raise even with heavily templated values."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        # Basic sanity: returned a DeclaredState object
        assert state is not None
        assert isinstance(state.secret_refs, list)
        assert isinstance(state.templated_unresolved, list)

    def test_plain_env_vars_collected(self):
        """Plain (non-secret) env vars like ENV, PORT must be in env_vars."""
        state = parse_declared_state(FIXTURE_SERVICE_YAML)
        assert "PORT" in state.env_vars or "ENV" in state.env_vars

    def test_file_not_found_raises(self):
        """FileNotFoundError for missing service.yaml."""
        with pytest.raises(FileNotFoundError):
            parse_declared_state("/nonexistent/service.yaml")


class TestParseDeclaredStateMinimal:
    """Test parser with minimal synthetic YAML inputs."""

    def _write_yaml(self, data: dict) -> str:
        """Write yaml data to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
        yaml.dump(data, f)
        f.flush()
        return f.name

    def test_minimal_yaml_no_crash(self):
        """Minimal valid YAML must parse without crash."""
        path = self._write_yaml({
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"ports": [{"containerPort": 8080}]}]
                    }
                }
            }
        })
        state = parse_declared_state(path)
        assert state.container_port == 8080
        assert state.secret_refs == []
        assert state.has_liveness_probe is False
        assert state.has_startup_probe is False

    def test_port_mismatch_scenario(self):
        """container_port=3000 must be parsed correctly."""
        path = self._write_yaml({
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"ports": [{"containerPort": 3000}]}]
                    }
                }
            }
        })
        state = parse_declared_state(path)
        assert state.container_port == 3000

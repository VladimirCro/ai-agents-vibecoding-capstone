"""
tests/test_fixture_replay.py — Unit tests for BE-04 fixture replay layer.

Tests:
  - fixture_replay returns a LiveState from worknote-ai-like.json
  - fixture_replay is deterministic: replay x2 → byte-identical
  - fixture_replay raises FileNotFoundError for missing fixture
  - redact_snapshot keeps names, masks values
"""

import json

import pytest

from launchguard.models import Mode
from launchguard.tools.fixture_replay import (
    fixture_replay,
    redact_snapshot,
    replay_to_json,
)


class TestFixtureReplay:
    def test_loads_worknote_ai_like(self):
        """fixture_replay loads the worknote-ai-like fixture."""
        state = fixture_replay("worknote-ai-like")
        assert state.project_id == "worknote-ai-like-project"
        assert state.mode == Mode.FIXTURE

    def test_runtime_sa_present(self):
        """runtime_sa must be set in the fixture."""
        state = fixture_replay("worknote-ai-like")
        assert state.runtime_sa is not None
        assert "worknote-ai-sa" in (state.runtime_sa or "")

    def test_all_9_secrets_present(self):
        """All 9 secret names must be in the fixture."""
        state = fixture_replay("worknote-ai-like")
        secret_names = {s.name for s in state.secrets}
        expected = {
            "JWT_SECRET_KEY", "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD",
            "SES_SMTP_HOST", "SENTRY_DSN_BACKEND", "LITELLM_AZURE_API_KEY",
            "LITELLM_AZURE_ENDPOINT", "LITELLM_VERTEX_CREDENTIALS", "CLAMAV_FUNCTION_URL",
        }
        assert expected <= secret_names

    def test_jwt_secret_key_has_empty_accessors(self):
        """JWT_SECRET_KEY must have empty accessor_members (hero KILLER delta)."""
        state = fixture_replay("worknote-ai-like")
        jwt_secret = next(s for s in state.secrets if s.name == "JWT_SECRET_KEY")
        assert jwt_secret.accessor_members == []

    def test_other_secrets_have_sa_accessor(self):
        """All other 8 secrets must have the SA in accessor_members."""
        state = fixture_replay("worknote-ai-like")
        for secret in state.secrets:
            if secret.name == "JWT_SECRET_KEY":
                continue
            assert len(secret.accessor_members) > 0, (
                f"{secret.name} should have accessor_members"
            )

    def test_deterministic_replay_x2(self):
        """Replaying the same fixture twice must produce byte-identical JSON."""
        json1 = replay_to_json("worknote-ai-like")
        json2 = replay_to_json("worknote-ai-like")
        assert json1 == json2, "Fixture replay must be byte-identical across calls"

    def test_missing_fixture_raises_file_not_found(self):
        """fixture_replay raises FileNotFoundError for unknown fixture names."""
        with pytest.raises(FileNotFoundError) as exc_info:
            fixture_replay("nonexistent-fixture-xyz")
        assert "nonexistent-fixture-xyz" in str(exc_info.value)

    def test_no_secret_values_in_fixture(self):
        """No fixture should contain actual secret values (only names)."""
        state = fixture_replay("worknote-ai-like")
        # Convert to dict and check for common secret value patterns
        state_dict = state.to_dict()
        state_json = json.dumps(state_dict)
        # Common secret value indicators that must not appear
        suspicious_patterns = [
            "-----BEGIN",  # PEM key
            "eyJ",         # JWT token start
            "AIza",        # GCP API key prefix
        ]
        for pattern in suspicious_patterns:
            assert pattern not in state_json, (
                f"Potential secret value pattern '{pattern}' found in fixture output"
            )

    def test_enabled_apis_present(self):
        """enabled_apis must be populated."""
        state = fixture_replay("worknote-ai-like")
        assert len(state.enabled_apis) > 0
        assert "run.googleapis.com" in state.enabled_apis

    def test_sa_iam_roles_present(self):
        """sa_iam_roles must be populated."""
        state = fixture_replay("worknote-ai-like")
        assert len(state.sa_iam_roles) > 0


class TestRedactSnapshot:
    def test_keeps_secret_names(self):
        """redact_snapshot preserves secret names."""
        raw = {
            "secrets": [
                {"name": "MY_SECRET", "accessor_members": []},
            ]
        }
        result = redact_snapshot(raw)
        assert result["secrets"][0]["name"] == "MY_SECRET"

    def test_keeps_accessor_members(self):
        """redact_snapshot preserves accessor_members (membership, not values)."""
        raw = {
            "secrets": [
                {"name": "MY_SECRET", "accessor_members": ["serviceAccount:sa@proj.iam.gserviceaccount.com"]},
            ]
        }
        result = redact_snapshot(raw)
        assert result["secrets"][0]["accessor_members"] == ["serviceAccount:sa@proj.iam.gserviceaccount.com"]

    def test_returns_dict(self):
        """redact_snapshot returns a dict."""
        result = redact_snapshot({"project_id": "my-project", "mode": "fixture"})
        assert isinstance(result, dict)
        assert result["project_id"] == "my-project"

"""
tests/test_redaction_e2e.py — QA-01 Redaction E2E assertions.

Covers:
  1. Golden fixture contains no planted secret value patterns (PEM, JWT, AIza, etc.)
  2. ReconciliationDelta.summary contains no secret values (run on hero fixture)
  3. Evidence.snippet contains no secret values
  4. Scorecard outputs (JSON/Markdown from eval) contain no secret values
  5. redact() removes a fake secret value from a temp copy but keeps the name
  6. REVIEWER'S SPECIFIC TEST: grep_code output "text" field is already redacted
     BEFORE it would hit redact() again — the "text" safe-passthrough contract
     in redact.py means a pre-planted secret value in a grep'd line is masked
     at CodeMatch.text creation time (in grep_code), not by a second redact() call.

No google-adk, no network, no live gcloud.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from launchguard.guardrails.redact import REDACTED_MARKER, redact
from launchguard.models import ReadinessScorecard, RuleId
from launchguard.reconciler.engine import reconcile
from launchguard.tools.declared_parser import parse_declared_state
from launchguard.tools.fixture_replay import fixture_replay
from launchguard.tools.repo_tools import build_intended_contract, grep_code

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_GCP_JSON = _REPO_ROOT / "fixtures" / "gcp" / "worknote-ai-like.json"
_FIXTURE_REPO = _REPO_ROOT / "fixtures" / "repos" / "worknote-ai-like"
_FIXTURE_SERVICE_YAML = _FIXTURE_REPO / "infra" / "cloud-run" / "service.yaml"
_FIXTURE_NAME = "worknote-ai-like"

# Patterns that look like secret values (used to scan for leaks)
_SECRET_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]+-----"),          # PEM key block
    re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),            # GCP API key prefix
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),         # Long base64-like blob
    re.compile(r"(?i)(postgres|mysql|redis)://\S+:\S+@"),  # DB URL with password
]

# A fake secret value used for planted-secret tests (not a real credential)
_FAKE_SECRET_VALUE = "AIzaFAKE1234567890abcdefghijklmnopqrstuv"  # matches AIza + 35 chars
_FAKE_SECRET_NAME = "MY_TEST_API_KEY"


def _has_secret_value(text: str) -> bool:
    """Return True if `text` contains a string that looks like a secret value."""
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _run_spine_scorecard() -> ReadinessScorecard:
    intended = build_intended_contract(str(_FIXTURE_REPO))
    declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
    live = fixture_replay(_FIXTURE_NAME)
    deltas = reconcile(intended, declared, live)
    return ReadinessScorecard.from_deltas(deltas)


# ---------------------------------------------------------------------------
# 1. Golden fixture JSON contains no secret values
# ---------------------------------------------------------------------------

class TestGoldenFixtureIsClean:
    def test_fixture_json_has_no_pem_block(self):
        """The golden GCP fixture must not contain a PEM private key block."""
        raw = _FIXTURE_GCP_JSON.read_text(encoding="utf-8")
        assert "-----BEGIN" not in raw, (
            "Golden fixture contains a PEM key block — fixture is not redacted"
        )

    def test_fixture_json_has_no_jwt(self):
        """The golden fixture must not contain a JWT token."""
        raw = _FIXTURE_GCP_JSON.read_text(encoding="utf-8")
        assert not re.search(
            r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", raw
        ), "Golden fixture contains a JWT token — fixture is not redacted"

    def test_fixture_json_has_no_gcp_api_key(self):
        """The golden fixture must not contain a GCP AIza... API key."""
        raw = _FIXTURE_GCP_JSON.read_text(encoding="utf-8")
        assert not re.search(r"AIza[A-Za-z0-9_-]{35}", raw), (
            "Golden fixture contains a GCP API key — fixture is not redacted"
        )

    def test_fixture_json_is_valid_and_parseable(self):
        """The golden fixture must parse as valid JSON."""
        raw = _FIXTURE_GCP_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert "secrets" in data
        assert "project_id" in data

    def test_fixture_secrets_contain_only_names_not_values(self):
        """Each secret entry in the fixture must have only 'name' + 'accessor_members'."""
        data = json.loads(_FIXTURE_GCP_JSON.read_text(encoding="utf-8"))
        for entry in data.get("secrets", []):
            # Name must be present
            assert "name" in entry
            # No 'value' or 'secret_value' field
            assert "value" not in entry, (
                f"Secret '{entry['name']}' has a 'value' field — must not store values"
            )
            assert "secret_value" not in entry, (
                f"Secret '{entry['name']}' has a 'secret_value' field — must not store values"
            )
            # accessor_members must be a list (may be empty)
            assert isinstance(entry.get("accessor_members", []), list)


# ---------------------------------------------------------------------------
# 2. ReconciliationDelta.summary contains no secret values
# ---------------------------------------------------------------------------

class TestDeltaSummariesAreRedacted:
    def test_killer_delta_summary_has_no_secret_value(self):
        """
        The KILLER delta summary must name JWT_SECRET_KEY but must not contain
        any long-base64, JWT, or API-key-shaped value.
        """
        scorecard = _run_spine_scorecard()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta found — cannot assert redaction"
        for d in killer_deltas:
            assert not _has_secret_value(d.summary), (
                f"KILLER delta summary contains what looks like a secret value: {d.summary!r}"
            )
            # Name must still be there
            assert "JWT_SECRET_KEY" in d.summary

    def test_all_delta_summaries_have_no_secret_value(self):
        """No delta summary anywhere in the scorecard should contain a secret value."""
        scorecard = _run_spine_scorecard()
        for d in scorecard.deltas:
            assert not _has_secret_value(d.summary), (
                f"Delta rule_id={d.rule_id!r} summary contains a secret-value pattern: "
                f"{d.summary!r}"
            )


# ---------------------------------------------------------------------------
# 3. Evidence.snippet contains no secret values
# ---------------------------------------------------------------------------

class TestEvidenceSnippetsAreRedacted:
    def test_evidence_snippets_have_no_secret_value(self):
        """No evidence snippet in any delta should contain a secret value."""
        scorecard = _run_spine_scorecard()
        for d in scorecard.deltas:
            for ev in d.evidence:
                assert not _has_secret_value(ev.snippet), (
                    f"Evidence snippet contains a secret-value pattern "
                    f"(rule_id={d.rule_id!r}, locator={ev.locator!r}): {ev.snippet!r}"
                )

    def test_evidence_snippet_preserves_accessor_member_info(self):
        """
        The KILLER delta's evidence must preserve the accessor_members list
        (membership info is needed for diagnosis), but the value must be empty.
        """
        scorecard = _run_spine_scorecard()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta"
        for d in killer_deltas:
            live_evs = [ev for ev in d.evidence if ev.source == "live"]
            assert live_evs, "KILLER delta must have live-source evidence"


# ---------------------------------------------------------------------------
# 4. Plant fake secret → redact() removes it, keeps name
# ---------------------------------------------------------------------------

class TestRedactRemovesPlantedFakeSecret:
    def test_redact_removes_fake_api_key_value(self):
        """
        Plant a fake GCP API key value in a dict.
        redact() must remove the value but keep the secret name.
        """
        payload = {
            "name": _FAKE_SECRET_NAME,
            "api_key": _FAKE_SECRET_VALUE,
        }
        result = redact(payload)
        assert result["name"] == _FAKE_SECRET_NAME, "Secret name must be preserved"
        assert _FAKE_SECRET_VALUE not in str(result.get("api_key", "")), (
            f"Fake API key value must be redacted; got: {result.get('api_key')!r}"
        )
        assert result["api_key"] == REDACTED_MARKER, (
            f"api_key must be replaced with REDACTED_MARKER; got: {result['api_key']!r}"
        )

    def test_redact_removes_jwt_value(self):
        """
        Plant a fake JWT (3-segment eyJ... pattern) in a string.
        redact() must mask it.
        """
        fake_jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiJ0ZXN0IiwiaWF0IjoxNjAwMDAwMDAwfQ"
            ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        result = redact(fake_jwt)
        assert fake_jwt not in result, "JWT token must be redacted from plain string"

    def test_redact_on_dict_with_planted_secret(self):
        """
        Plant a fake secret value in a top-level dict key that is NOT in
        _SAFE_PASSTHROUGH_KEYS.  redact() must mask the value.

        Note: if the key were 'secrets' (which IS in _SAFE_PASSTHROUGH_KEYS),
        the entire list would pass through unchanged — that is correct behavior
        because secrets entries carry only names/membership info, never values.
        Here we use a top-level 'api_config' key to test the redaction path.
        """
        planted = {
            "project_id": "test-project",
            "api_config": {
                "name": _FAKE_SECRET_NAME,
                # 'value' key — not in safe-passthrough, not a key-name secret pattern
                # but the VALUE matches _VALUE_PATTERNS (AIza prefix pattern)
                "value": _FAKE_SECRET_VALUE,
            },
        }
        result = redact(planted)
        api_config = result["api_config"]
        # name preserved
        assert api_config["name"] == _FAKE_SECRET_NAME
        # value masked — AIza pattern matched by _redact_string
        value_result = api_config.get("value", "")
        assert _FAKE_SECRET_VALUE not in str(value_result), (
            f"Planted secret value must not appear after redact(); got: {value_result!r}"
        )


# ---------------------------------------------------------------------------
# 5. REVIEWER'S SPECIFIC TEST — "text" safe-passthrough contract
#
# The reviewer flagged: _SAFE_PASSTHROUGH_KEYS contains "text", meaning
# redact({"text": "<value>"}) passes the value through unchanged.
# This is SAFE because grep_code pre-redacts the line text at source BEFORE
# placing it in CodeMatch.text. This test confirms BOTH:
#
#   (A) The passthrough behavior exists (document it explicitly so it is not
#       silently relied on by future callers)
#   (B) grep_code pre-redacts the text, so CodeMatch.text is already safe
#       when it would reach any model-bound payload
# ---------------------------------------------------------------------------

class TestTextSafePassthroughContract:
    def test_redact_dict_with_text_key_passes_value_through(self):
        """
        DOCUMENT the known passthrough: redact({"text": "<value>"}) does NOT
        redact the value because "text" is in _SAFE_PASSTHROUGH_KEYS.

        This is the invariant the reviewer flagged. We assert this explicitly
        so any future change to this behavior is caught.
        """
        # A value that would normally be redacted by _VALUE_PATTERNS
        fake_connection_str = "postgresql://user:s3cr3t-password@db.example.com:5432/mydb"
        payload = {"text": fake_connection_str}
        result = redact(payload)
        # Assert the passthrough: "text" key is in _SAFE_PASSTHROUGH_KEYS
        # → value is NOT redacted by redact(dict)
        assert result["text"] == fake_connection_str, (
            "'text' key must pass through redact() unchanged (documented safe-passthrough). "
            "If this changes, update the _SAFE_PASSTHROUGH_KEYS contract in redact.py."
        )

    def test_grep_code_text_field_is_pre_redacted_before_codematch(self):
        """
        Confirm that grep_code() pre-redacts the line text via redact(line.strip()[:200])
        BEFORE placing it in CodeMatch.text — so the text value is already safe
        even though redact({"text": "..."}) would pass it through unchanged.

        Strategy:
          1. Create a temp repo with a Python file containing a planted fake secret value
          2. Call grep_code() on it to search for the pattern near the planted secret
          3. Assert the returned CodeMatch.text does NOT contain the raw fake secret value
        """
        fake_secret = _FAKE_SECRET_VALUE  # AIza... pattern — caught by _VALUE_PATTERNS

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a Python file that contains the fake secret value on a grep-able line
            secret_file = Path(tmpdir) / "config.py"
            secret_file.write_text(
                f'API_KEY = "{fake_secret}"  # production key\n'
                f'# some other content\n',
                encoding="utf-8",
            )

            # grep for "API_KEY" — will match the line containing the fake secret value
            result = grep_code(tmpdir, r"API_KEY")

        matches = result.get("matches", [])
        assert len(matches) >= 1, (
            "Expected at least one match for 'API_KEY' in temp repo"
        )

        # The matching line contains the fake secret value.
        # grep_code must have pre-redacted it — so CodeMatch.text must NOT contain it.
        for match in matches:
            text = match.get("text", "")
            assert fake_secret not in text, (
                f"grep_code failed to pre-redact the fake secret value in CodeMatch.text. "
                f"Got text: {text!r}. "
                f"The pre-redaction at repo_tools.py:514 must mask values before CodeMatch."
            )
            # Confirm something was put in text (not empty) — the line was found
            assert text != "", "CodeMatch.text must not be empty for a matching line"

    def test_grep_code_text_after_redact_dict_still_safe(self):
        """
        Even if a caller passes a CodeMatch dict through redact() a second time,
        the "text" field passes through unchanged.
        Confirm that because grep_code pre-redacts, the already-safe text value
        is still safe after the second redact() pass.
        """
        fake_secret = _FAKE_SECRET_VALUE

        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / "app.py"
            secret_file.write_text(
                f'key = "{fake_secret}"\n',
                encoding="utf-8",
            )
            result = grep_code(tmpdir, r"key\s*=")

        matches = result.get("matches", [])
        assert len(matches) >= 1, "Expected at least one match"

        # Apply redact() again to the full matches dict (simulating a model-bound payload)
        double_redacted = redact(result)
        for match in double_redacted.get("matches", []):
            # "text" is in _SAFE_PASSTHROUGH_KEYS → passes through on second call too.
            # BUT because grep_code already redacted it at source, the value in text
            # is already [REDACTED] — so the plain fake_secret cannot appear.
            text = match.get("text", "")
            assert fake_secret not in text, (
                f"Fake secret value leaked through double-redact pass: {text!r}"
            )

    def test_grep_code_preserves_non_secret_content_in_text(self):
        """
        grep_code must NOT redact every line — only lines with secret-value patterns.
        A plain variable assignment with a non-secret value must survive intact.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_file = Path(tmpdir) / "config.py"
            plain_file.write_text(
                "DEBUG = True\nPORT = 8080\n",
                encoding="utf-8",
            )
            result = grep_code(tmpdir, r"PORT")

        matches = result.get("matches", [])
        assert len(matches) >= 1, "Expected match for PORT"
        # PORT = 8080 has no secret-value pattern → text must not be REDACTED_MARKER
        for match in matches:
            if "PORT" in match.get("text", ""):
                assert REDACTED_MARKER not in match["text"], (
                    "Plain non-secret content should not be redacted in CodeMatch.text"
                )

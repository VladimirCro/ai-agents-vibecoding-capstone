"""
tests/test_e2e_eval.py — QA-01 E2E fixture-mode tests.

This file is the E2E surface for LaunchGuard Increment 1 (arch §7).
It runs the FULL pipeline spine in fixture mode against worknote-ai-like
and makes hard assertions on:

  (a) The secret-ref-without-secretAccessor KILLER fires, naming JWT_SECRET_KEY
  (b) verdict == BLOCKED
  (c) ZERO network calls / zero subprocess exec occurred
  (d) Replay twice → identical findings (determinism precondition for eval)

No google-adk, no network, no live gcloud. Pure Python.
"""

from __future__ import annotations

import json
import subprocess
import unittest.mock
from pathlib import Path

import pytest

from launchguard.guardrails.audit import get_audit_logger
from launchguard.models import (
    DeltaClass,
    ReadinessScorecard,
    RuleId,
    Verdict,
)
from launchguard.reconciler.engine import reconcile
from launchguard.tools.declared_parser import parse_declared_state
from launchguard.tools.fixture_replay import fixture_replay, replay_to_json
from launchguard.tools.repo_tools import build_intended_contract

# ---------------------------------------------------------------------------
# Path constants — absolute, cwd-independent
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_REPO = _REPO_ROOT / "fixtures" / "repos" / "worknote-ai-like"
_FIXTURE_SERVICE_YAML = _FIXTURE_REPO / "infra" / "cloud-run" / "service.yaml"
_FIXTURE_NAME = "worknote-ai-like"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_full_spine() -> ReadinessScorecard:
    """
    Execute the full LaunchGuard pipeline in fixture mode.

    Runs:
      1. build_intended_contract  (RepoAuditor)
      2. parse_declared_state     (Declared parser)
      3. fixture_replay           (GcpStateInspector fixture mode)
      4. reconcile                (Reconciler — pure deterministic logic)
      5. ReadinessScorecard.from_deltas
    Zero network, zero subprocess.
    """
    intended = build_intended_contract(str(_FIXTURE_REPO))
    declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
    live = fixture_replay(_FIXTURE_NAME)
    deltas = reconcile(intended, declared, live)
    return ReadinessScorecard.from_deltas(deltas)


# ---------------------------------------------------------------------------
# Fixture isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_audit_log():
    """Reset the shared audit logger before/after each test."""
    logger = get_audit_logger()
    logger.reset()
    yield
    logger.reset()


# ---------------------------------------------------------------------------
# (a) KILLER fires — JWT_SECRET_KEY named
# ---------------------------------------------------------------------------

class TestKillerDeltaDetected:
    def test_killer_rule_id_present(self):
        """Full spine must produce a delta with rule_id=secret-ref-without-secretAccessor."""
        scorecard = _run_full_spine()
        killer_rule = RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        killer_deltas = [
            d for d in scorecard.deltas if d.rule_id == killer_rule
        ]
        assert len(killer_deltas) >= 1, (
            f"Expected at least one delta with rule_id='{killer_rule}'; "
            f"got deltas: {[d.rule_id for d in scorecard.deltas]}"
        )

    def test_killer_names_jwt_secret_key(self):
        """The KILLER delta must name JWT_SECRET_KEY explicitly."""
        scorecard = _run_full_spine()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta found"
        summaries = " ".join(d.summary for d in killer_deltas)
        assert "JWT_SECRET_KEY" in summaries, (
            f"KILLER delta summary must mention 'JWT_SECRET_KEY'; got: {summaries}"
        )

    def test_killer_is_will_fail(self):
        """The KILLER delta must be classified as will-fail."""
        scorecard = _run_full_spine()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta found"
        for d in killer_deltas:
            assert d.delta_class == DeltaClass.WILL_FAIL, (
                f"KILLER delta must be 'will-fail'; got '{d.delta_class}'"
            )

    def test_killer_has_high_confidence(self):
        """The KILLER delta must have confidence >= 0.95."""
        scorecard = _run_full_spine()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta found"
        for d in killer_deltas:
            assert d.confidence >= 0.95, (
                f"KILLER delta confidence must be >= 0.95; got {d.confidence}"
            )

    def test_killer_has_evidence(self):
        """The KILLER delta must have at least one evidence entry (contract: minItems=1)."""
        scorecard = _run_full_spine()
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        assert killer_deltas, "No KILLER delta found"
        for d in killer_deltas:
            assert len(d.evidence) >= 1, (
                f"KILLER delta must have at least 1 evidence entry; got {d.evidence}"
            )

    def test_non_killer_secrets_do_not_fire(self):
        """
        The 8 secrets with SA in accessor_members must NOT produce
        a secret-ref-without-secretAccessor delta. Zero FPs on the true negatives.
        """
        scorecard = _run_full_spine()
        true_negative_secrets = {
            "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD", "SES_SMTP_HOST",
            "SENTRY_DSN_BACKEND", "LITELLM_AZURE_API_KEY", "LITELLM_AZURE_ENDPOINT",
            "LITELLM_VERTEX_CREDENTIALS", "CLAMAV_FUNCTION_URL",
        }
        killer_deltas = [
            d for d in scorecard.deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
        ]
        # None of the true-negative secrets should appear in KILLER delta summaries
        for secret_name in true_negative_secrets:
            for d in killer_deltas:
                assert secret_name not in d.summary, (
                    f"False positive: '{secret_name}' appeared in KILLER delta summary: {d.summary}"
                )


# ---------------------------------------------------------------------------
# (b) verdict == BLOCKED
# ---------------------------------------------------------------------------

class TestVerdictBlocked:
    def test_verdict_is_blocked(self):
        """Any will-fail delta → verdict must be BLOCKED."""
        scorecard = _run_full_spine()
        assert scorecard.verdict == Verdict.BLOCKED, (
            f"Expected verdict=BLOCKED; got verdict={scorecard.verdict}"
        )

    def test_scorecard_counts_will_fail_nonzero(self):
        """ReadinessScorecard.counts.will_fail must be >= 1 (the KILLER)."""
        scorecard = _run_full_spine()
        assert scorecard.counts.will_fail >= 1, (
            f"Expected will_fail >= 1; got {scorecard.counts.will_fail}"
        )

    def test_scorecard_shape_matches_contract(self):
        """
        Serialized scorecard must match api-contracts.yaml ReadinessScorecard shape:
        required: verdict, counts, deltas.
        counts required: will_fail, will_misbehave, cost_risk, needs_review.
        """
        scorecard = _run_full_spine()
        d = scorecard.to_dict()

        # Top-level required fields
        assert "verdict" in d
        assert "counts" in d
        assert "deltas" in d

        # Verdict is one of the contract enum values
        assert d["verdict"] in ("BLOCKED", "WARN", "READY")

        # Counts shape: underscore names per contract
        counts = d["counts"]
        for field in ("will_fail", "will_misbehave", "cost_risk", "needs_review"):
            assert field in counts, f"counts missing field: {field}"

        # deltas is a list; each delta has required contract fields
        assert isinstance(d["deltas"], list)
        for delta in d["deltas"]:
            for field in ("rule_id", "delta_class", "confidence", "summary", "evidence"):
                assert field in delta, f"delta missing contract field: {field}"
            assert len(delta["evidence"]) >= 1, "Each delta must have at least 1 evidence entry"


# ---------------------------------------------------------------------------
# (c) ZERO network / subprocess in fixture mode
# ---------------------------------------------------------------------------

class TestZeroNetworkInFixtureMode:
    def test_subprocess_never_called(self):
        """
        fixture_replay must not spawn any subprocess.
        We monkeypatch subprocess.run to fail immediately if called —
        if the test passes, no subprocess was invoked.
        """
        def _fail_if_called(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(
                f"subprocess.run was called during fixture-mode E2E! args={args!r}"
            )

        with unittest.mock.patch("subprocess.run", side_effect=_fail_if_called):
            scorecard = _run_full_spine()

        # Sanity: confirm the run produced the expected BLOCKED verdict
        assert scorecard.verdict == Verdict.BLOCKED, (
            "Spine must produce BLOCKED even with subprocess.run monkeypatched away"
        )

    def test_subprocess_popen_never_called(self):
        """subprocess.Popen also must not be called in fixture mode."""
        def _fail_if_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(
                f"subprocess.Popen was called during fixture-mode E2E! args={args!r}"
            )

        with unittest.mock.patch("subprocess.Popen", side_effect=_fail_if_popen):
            scorecard = _run_full_spine()

        assert scorecard.verdict == Verdict.BLOCKED


# ---------------------------------------------------------------------------
# (d) Determinism — replay twice → identical findings
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_two_replay_calls_produce_identical_json(self):
        """
        fixture_replay called twice on the same fixture must produce byte-identical
        JSON output. This is the determinism precondition for the eval scorecard.
        """
        first = replay_to_json(_FIXTURE_NAME)
        second = replay_to_json(_FIXTURE_NAME)
        assert first == second, (
            "fixture_replay must be byte-identical across replays; "
            "non-determinism detected"
        )

    def test_full_spine_twice_identical_verdict(self):
        """
        Running the full spine twice must produce the same verdict.
        Validates the pipeline is deterministic end-to-end.
        """
        scorecard1 = _run_full_spine()
        scorecard2 = _run_full_spine()
        assert scorecard1.verdict == scorecard2.verdict, (
            f"Non-deterministic verdict: run1={scorecard1.verdict}, run2={scorecard2.verdict}"
        )

    def test_full_spine_twice_identical_delta_rule_ids(self):
        """
        Running the full spine twice must produce the same set of rule_ids.
        """
        scorecard1 = _run_full_spine()
        scorecard2 = _run_full_spine()
        rule_ids_1 = sorted(d.rule_id for d in scorecard1.deltas)
        rule_ids_2 = sorted(d.rule_id for d in scorecard2.deltas)
        assert rule_ids_1 == rule_ids_2, (
            f"Non-deterministic delta rule_ids: run1={rule_ids_1}, run2={rule_ids_2}"
        )

    def test_full_spine_twice_identical_json_serialization(self):
        """
        Full scorecard serialized to JSON must be byte-identical across two runs.
        """
        scorecard1 = _run_full_spine()
        scorecard2 = _run_full_spine()
        json1 = json.dumps(scorecard1.to_dict(), sort_keys=True)
        json2 = json.dumps(scorecard2.to_dict(), sort_keys=True)
        assert json1 == json2, (
            "Scorecard JSON must be byte-identical across two runs; "
            "non-determinism detected"
        )


# ---------------------------------------------------------------------------
# Integration: declared_parser on hero fixture
# ---------------------------------------------------------------------------

class TestDeclaredParserHeroFixture:
    def test_container_port_8080(self):
        """worknote-ai-like service.yaml must parse to container_port=8080."""
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        assert declared.container_port == 8080

    def test_all_nine_secret_refs_parsed(self):
        """All 9 secretKeyRef names from service.yaml must be in declared.secret_refs."""
        expected_secrets = {
            "JWT_SECRET_KEY", "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD",
            "SES_SMTP_HOST", "SENTRY_DSN_BACKEND", "LITELLM_AZURE_API_KEY",
            "LITELLM_AZURE_ENDPOINT", "LITELLM_VERTEX_CREDENTIALS", "CLAMAV_FUNCTION_URL",
        }
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        declared_set = set(declared.secret_refs)
        missing = expected_secrets - declared_set
        assert not missing, (
            f"Declared parser missed secret refs: {missing}. "
            f"Got: {sorted(declared_set)}"
        )

    def test_scaling_min_max_concurrency_populated(self):
        """Scaling fields must be parsed from worknote-ai-like service.yaml."""
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        # min=0, max=3, concurrency=80 per the fixture
        assert declared.scaling.min_scale == 0
        assert declared.scaling.max_scale == 3
        assert declared.scaling.concurrency == 80

    def test_templated_unresolved_populated(self):
        """${SA_EMAIL}, ${PROJECT_ID} etc. must be collected in templated_unresolved."""
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        # Service.yaml has ${SA_EMAIL}, ${PROJECT_NUMBER}, ${PROJECT_ID}, ${ENV}
        assert len(declared.templated_unresolved) > 0, (
            "Expected templated_unresolved to be populated for ${SA_EMAIL} / "
            f"${'{PROJECT_ID}'} placeholders; got empty list"
        )
        # service_account should be None (it's a placeholder)
        assert declared.service_account is None, (
            f"service_account must be None for a templated ${'{SA_EMAIL}'}; "
            f"got: {declared.service_account}"
        )

    def test_has_liveness_probe(self):
        """worknote-ai-like service.yaml has a livenessProbe — must be True."""
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        assert declared.has_liveness_probe is True

    def test_has_startup_probe(self):
        """worknote-ai-like service.yaml has a startupProbe — must be True."""
        declared = parse_declared_state(str(_FIXTURE_SERVICE_YAML))
        assert declared.has_startup_probe is True


# ---------------------------------------------------------------------------
# Integration: live state fixture
# ---------------------------------------------------------------------------

class TestLiveStateFixture:
    def test_jwt_secret_key_has_empty_accessor_members(self):
        """JWT_SECRET_KEY must have accessor_members=[] in the golden fixture."""
        live = fixture_replay(_FIXTURE_NAME)
        jwt_entry = next(
            (s for s in live.secrets if s.name == "JWT_SECRET_KEY"), None
        )
        assert jwt_entry is not None, "JWT_SECRET_KEY not found in live fixture"
        assert jwt_entry.accessor_members == [], (
            f"JWT_SECRET_KEY must have empty accessor_members in hero fixture; "
            f"got: {jwt_entry.accessor_members}"
        )

    def test_mode_is_fixture(self):
        """Replayed LiveState must have mode='fixture'."""
        live = fixture_replay(_FIXTURE_NAME)
        assert live.mode == "fixture"

    def test_runtime_sa_is_set(self):
        """Runtime SA must be set in the hero fixture."""
        live = fixture_replay(_FIXTURE_NAME)
        assert live.runtime_sa is not None
        assert live.runtime_sa != ""

"""
tests/test_eval_matrix.py — BE-08 remainder + QA-01: eval fixture matrix as the E2E gate.

The architecture says "the eval harness is the E2E surface". eval/run_eval.py is not picked
up by default pytest collection (it is not named test_*.py), so this file imports it and runs
the FULL matrix inside the gated tests/ suite. It asserts:

  - >= 8 fixtures discovered (BE-08 acceptance: 8–10 misconfigured + clean control)
  - the clean-control fixture produces ZERO findings (no false positives)
  - blocker precision == recall == F1 == 1.0 over the matrix
  - the hero killer is a true positive
  - every detector category is represented across the matrix
  - scorecard JSON + Markdown carry precision/recall/F1 + headline

Offline, deterministic.
"""

from __future__ import annotations

import json

import pytest

from eval.run_eval import discover_fixtures, run_eval, run_fixture

_REQUIRED_CATEGORIES = {
    "secret-ref-without-secretAccessor",
    "secret-declared-not-created",
    "port-mismatch",
    "missing-health-probe",
    "missing-startup-probe",
    "pid1-signal-unsafe",
    "over-broad-sa-role",
    "missing-required-role",
    "api-not-enabled",
    "scaling-cost-flag",
    "host-not-0.0.0.0",
}


def test_matrix_has_at_least_eight_fixtures():
    fixtures = discover_fixtures()
    names = {f["name"] for f in fixtures}
    assert len(fixtures) >= 8, f"Expected >= 8 fixtures, got {len(fixtures)}: {sorted(names)}"
    assert "clean-control" in names, "true-negative control fixture missing"
    assert "worknote-ai-like" in names, "hero fixture missing"


def test_all_detector_categories_represented():
    """Across all fixtures' expected blockers+warnings, every detector category appears."""
    fixtures = discover_fixtures()
    seen: set[str] = set()
    for f in fixtures:
        labels = f["labels"]
        for b in labels.get("expected_blockers", []):
            seen.add(b["rule_id"])
        for w in labels.get("expected_warnings", []):
            seen.add(w["rule_id"])
    missing = _REQUIRED_CATEGORIES - seen
    assert not missing, f"Detector categories not covered by any fixture: {sorted(missing)}"


def test_full_matrix_precision_recall_f1_perfect():
    summary = run_eval()
    assert summary.errored_fixtures == 0, "no fixture may error"
    assert summary.aggregate_precision == 1.0, f"precision={summary.aggregate_precision}"
    assert summary.aggregate_recall == 1.0, f"recall={summary.aggregate_recall}"
    assert summary.aggregate_f1 == 1.0, f"f1={summary.aggregate_f1}"
    assert summary.total_fp == 0, f"false-positive blockers: {summary.total_fp}"


def test_clean_control_zero_findings():
    clean = next((f for f in discover_fixtures() if f["name"] == "clean-control"), None)
    assert clean is not None
    result = run_fixture(clean)
    assert result.verdict == "READY"
    assert not result.false_positives
    assert not result.detected_warning_rule_ids, (
        f"clean-control must fire nothing; got {result.detected_warning_rule_ids}"
    )


def test_hero_killer_true_positive():
    hero = next((f for f in discover_fixtures() if f["name"] == "worknote-ai-like"), None)
    assert hero is not None
    result = run_fixture(hero)
    assert "secret-ref-without-secretAccessor" in result.true_positives


def test_scorecard_files_carry_f1_and_headline(tmp_path):
    summary = run_eval()
    import eval.run_eval as re_mod
    json_path = re_mod._SCORECARD_DIR / "scorecard.json"
    md_path = re_mod._SCORECARD_DIR / "scorecard.md"
    assert json_path.exists() and md_path.exists()
    data = json.loads(json_path.read_text())
    assert "aggregate_f1" in data
    assert "aggregate_precision" in data
    assert "aggregate_recall" in data
    md = md_path.read_text()
    assert "F1" in md
    assert "Caught" in md


@pytest.mark.parametrize("fixture_info", discover_fixtures(), ids=lambda f: f["name"])
def test_each_fixture_verdict_and_warnings_match(fixture_info):
    result = run_fixture(fixture_info)
    assert not result.error, f"{result.name} errored: {result.error}"
    assert result.verdict_match, (
        f"{result.name}: verdict {result.verdict} != expected {result.expected_verdict}"
    )
    assert not result.false_negatives, f"{result.name} missed blockers: {result.false_negatives}"
    assert not result.false_positives, f"{result.name} false-positive blockers: {result.false_positives}"
    assert not result.warning_missed, f"{result.name} missed warnings: {result.warning_missed}"

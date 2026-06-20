"""
eval/run_eval.py — QA-01 Eval harness for LaunchGuard Increment 1.

Runnable two ways:
  1. pytest:             pytest eval/run_eval.py -v
  2. standalone script:  python eval/run_eval.py

Discovers fixture repos under fixtures/repos/*/ that have:
  - labels.json  (ground-truth: expected_blockers + expected_verdict)
  - A paired GCP snapshot at fixtures/gcp/<name>.json

For each fixture:
  - Builds IntendedContract  (build_intended_contract)
  - Parses DeclaredState     (parse_declared_state; looks for infra/cloud-run/service.yaml)
  - Replays LiveState        (fixture_replay)
  - Reconciles               (reconcile)
  - Builds ReadinessScorecard (ReadinessScorecard.from_deltas)
  - Compares detected blocker rule_ids vs labels.json expected_blockers
  - Accumulates TP/FP/FN counts for precision/recall

Emits:
  eval/scorecard/scorecard.json  — structured results
  eval/scorecard/scorecard.md    — human-readable headline metric

QA-01 acceptance: hero fixture JWT_SECRET_KEY must be a true positive.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: allow running as `python eval/run_eval.py` from repo root
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from launchguard.models import ReadinessScorecard  # noqa: E402 (after sys.path)
from launchguard.reconciler.engine import reconcile  # noqa: E402
from launchguard.tools.declared_parser import parse_declared_state  # noqa: E402
from launchguard.tools.fixture_replay import fixture_replay  # noqa: E402
from launchguard.tools.repo_tools import build_intended_contract  # noqa: E402

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------

_FIXTURES_REPOS_DIR = _REPO_ROOT / "fixtures" / "repos"
_FIXTURES_GCP_DIR = _REPO_ROOT / "fixtures" / "gcp"
_SCORECARD_DIR = _REPO_ROOT / "eval" / "scorecard"

# Service.yaml search candidates (relative to the fixture repo root)
_SERVICE_YAML_CANDIDATES = [
    "infra/cloud-run/service.yaml",
    "cloud-run/service.yaml",
    "service.yaml",
    "infra/service.yaml",
    "deploy/service.yaml",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FixtureResult:
    """Per-fixture eval result."""
    name: str
    verdict: str                              # scorecard verdict
    expected_verdict: str                     # from labels.json
    verdict_match: bool

    expected_blocker_rule_ids: list[str]      # from labels.json
    detected_blocker_rule_ids: list[str]      # will-fail rule_ids from scorecard

    true_positives: list[str]                 # expected & detected
    false_positives: list[str]                # detected but not expected
    false_negatives: list[str]                # expected but not detected

    precision: float
    recall: float

    error: str = ""                           # non-empty if the run crashed


@dataclass
class EvalSummary:
    """Aggregate eval summary across all fixtures."""
    total_fixtures: int
    passed_fixtures: int                      # fixtures where verdict matched
    failed_fixtures: int
    errored_fixtures: int

    total_expected_blockers: int
    total_tp: int
    total_fp: int
    total_fn: int

    aggregate_precision: float
    aggregate_recall: float

    fixture_results: list[FixtureResult] = field(default_factory=list)

    headline: str = ""                        # formatted headline metric


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

def discover_fixtures() -> list[dict]:  # type: ignore[type-arg]
    """
    Discover fixture repos that have both labels.json and a paired GCP snapshot.

    Returns a list of dicts with keys:
      - name (str)
      - repo_path (Path)
      - labels (dict)
      - service_yaml_path (Path | None)
      - gcp_fixture_path (Path)
    """
    if not _FIXTURES_REPOS_DIR.exists():
        return []

    fixtures = []
    for repo_dir in sorted(_FIXTURES_REPOS_DIR.iterdir()):
        if not repo_dir.is_dir():
            continue

        labels_path = repo_dir / "labels.json"
        if not labels_path.exists():
            continue

        name = repo_dir.name
        gcp_fixture_path = _FIXTURES_GCP_DIR / f"{name}.json"
        if not gcp_fixture_path.exists():
            continue

        labels = json.loads(labels_path.read_text(encoding="utf-8"))

        # Find service.yaml
        service_yaml_path = None
        for candidate in _SERVICE_YAML_CANDIDATES:
            candidate_path = repo_dir / candidate
            if candidate_path.exists():
                service_yaml_path = candidate_path
                break

        fixtures.append({
            "name": name,
            "repo_path": repo_dir,
            "labels": labels,
            "service_yaml_path": service_yaml_path,
            "gcp_fixture_path": gcp_fixture_path,
        })

    return fixtures


# ---------------------------------------------------------------------------
# Per-fixture runner
# ---------------------------------------------------------------------------

def run_fixture(fixture_info: dict) -> FixtureResult:  # type: ignore[type-arg]
    """Run the full LaunchGuard pipeline on one fixture and return FixtureResult."""
    name = fixture_info["name"]
    repo_path = fixture_info["repo_path"]
    labels = fixture_info["labels"]
    service_yaml_path = fixture_info.get("service_yaml_path")

    expected_verdict = labels.get("expected_verdict", "READY")
    expected_blockers = labels.get("expected_blockers", [])
    expected_blocker_rule_ids = [b["rule_id"] for b in expected_blockers]

    try:
        # Step 1: Build IntendedContract
        intended = build_intended_contract(str(repo_path))

        # Step 2: Parse DeclaredState (optional — not all fixtures have service.yaml)
        if service_yaml_path and service_yaml_path.exists():
            declared = parse_declared_state(str(service_yaml_path))
        else:
            # Minimal DeclaredState for fixtures without service.yaml
            from launchguard.models import DeclaredState, ScalingConfig
            declared = DeclaredState(
                container_port=None,
                secret_refs=[],
                env_vars=[],
                has_liveness_probe=False,
                has_startup_probe=False,
                scaling=ScalingConfig(),
            )

        # Step 3: Replay LiveState
        live = fixture_replay(name)

        # Step 4: Reconcile
        deltas = reconcile(intended, declared, live)

        # Step 5: Build Scorecard
        scorecard = ReadinessScorecard.from_deltas(deltas)

        # Step 6: Compare against ground truth
        detected_blocker_rule_ids = [
            d.rule_id for d in scorecard.deltas
            if d.delta_class == "will-fail"
        ]

        # For precision/recall, compare at rule_id level
        expected_set = set(expected_blocker_rule_ids)
        detected_set = set(detected_blocker_rule_ids)

        tp_ids = sorted(expected_set & detected_set)
        fp_ids = sorted(detected_set - expected_set)
        fn_ids = sorted(expected_set - detected_set)

        tp_count = len(tp_ids)
        fp_count = len(fp_ids)
        fn_count = len(fn_ids)

        precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 1.0
        recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 1.0

        verdict_match = scorecard.verdict == expected_verdict

        return FixtureResult(
            name=name,
            verdict=scorecard.verdict,
            expected_verdict=expected_verdict,
            verdict_match=verdict_match,
            expected_blocker_rule_ids=expected_blocker_rule_ids,
            detected_blocker_rule_ids=sorted(detected_blocker_rule_ids),
            true_positives=tp_ids,
            false_positives=fp_ids,
            false_negatives=fn_ids,
            precision=precision,
            recall=recall,
        )

    except Exception as exc:
        return FixtureResult(
            name=name,
            verdict="ERROR",
            expected_verdict=expected_verdict,
            verdict_match=False,
            expected_blocker_rule_ids=expected_blocker_rule_ids,
            detected_blocker_rule_ids=[],
            true_positives=[],
            false_positives=[],
            false_negatives=expected_blocker_rule_ids,
            precision=0.0,
            recall=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Aggregate computation
# ---------------------------------------------------------------------------

def compute_summary(results: list[FixtureResult]) -> EvalSummary:
    """Aggregate per-fixture results into a summary with headline metric."""
    total = len(results)
    passed = sum(1 for r in results if r.verdict_match and not r.error)
    failed = sum(1 for r in results if not r.verdict_match and not r.error)
    errored = sum(1 for r in results if r.error)

    total_expected = sum(len(r.expected_blocker_rule_ids) for r in results)
    total_tp = sum(len(r.true_positives) for r in results)
    total_fp = sum(len(r.false_positives) for r in results)
    total_fn = sum(len(r.false_negatives) for r in results)

    agg_precision = (
        total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    )
    agg_recall = (
        total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    )

    headline = (
        f"Caught {total_tp}/{total_tp + total_fn} blockers across {total} fixture(s); "
        f"precision={agg_precision:.2f} / recall={agg_recall:.2f}"
    )

    return EvalSummary(
        total_fixtures=total,
        passed_fixtures=passed,
        failed_fixtures=failed,
        errored_fixtures=errored,
        total_expected_blockers=total_expected,
        total_tp=total_tp,
        total_fp=total_fp,
        total_fn=total_fn,
        aggregate_precision=agg_precision,
        aggregate_recall=agg_recall,
        fixture_results=results,
        headline=headline,
    )


# ---------------------------------------------------------------------------
# Scorecard emitters
# ---------------------------------------------------------------------------

def emit_scorecard_json(summary: EvalSummary) -> Path:
    """Write eval/scorecard/scorecard.json and return the path."""
    _SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _SCORECARD_DIR / "scorecard.json"

    data = {
        "headline": summary.headline,
        "total_fixtures": summary.total_fixtures,
        "passed_fixtures": summary.passed_fixtures,
        "failed_fixtures": summary.failed_fixtures,
        "errored_fixtures": summary.errored_fixtures,
        "aggregate_precision": round(summary.aggregate_precision, 4),
        "aggregate_recall": round(summary.aggregate_recall, 4),
        "total_expected_blockers": summary.total_expected_blockers,
        "total_tp": summary.total_tp,
        "total_fp": summary.total_fp,
        "total_fn": summary.total_fn,
        "fixtures": [
            {
                "name": r.name,
                "verdict": r.verdict,
                "expected_verdict": r.expected_verdict,
                "verdict_match": r.verdict_match,
                "expected_blockers": r.expected_blocker_rule_ids,
                "detected_blockers": r.detected_blocker_rule_ids,
                "true_positives": r.true_positives,
                "false_positives": r.false_positives,
                "false_negatives": r.false_negatives,
                "precision": round(r.precision, 4),
                "recall": round(r.recall, 4),
                "error": r.error or None,
            }
            for r in summary.fixture_results
        ],
    }

    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def emit_scorecard_md(summary: EvalSummary) -> Path:
    """Write eval/scorecard/scorecard.md and return the path."""
    _SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _SCORECARD_DIR / "scorecard.md"

    lines = [
        "# LaunchGuard Eval Scorecard — Increment 1",
        "",
        "## Headline",
        "",
        f"> {summary.headline}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fixtures | {summary.total_fixtures} |",
        f"| Verdict match | {summary.passed_fixtures}/{summary.total_fixtures} |",
        f"| Aggregate precision | {summary.aggregate_precision:.2%} |",
        f"| Aggregate recall | {summary.aggregate_recall:.2%} |",
        f"| True positives | {summary.total_tp} |",
        f"| False positives | {summary.total_fp} |",
        f"| False negatives | {summary.total_fn} |",
        "",
        "## Per-Fixture Results",
        "",
    ]

    for r in summary.fixture_results:
        status = "PASS" if r.verdict_match and not r.error else ("ERROR" if r.error else "FAIL")
        lines.append(f"### `{r.name}` — {status}")
        lines.append("")
        if r.error:
            lines.append(f"**Error:** {r.error}")
            lines.append("")
        else:
            verdict_status = "match" if r.verdict_match else "MISMATCH"
            lines.append(f"- Verdict: `{r.verdict}` (expected `{r.expected_verdict}`) — {verdict_status}")
            lines.append(f"- Precision: {r.precision:.2%} | Recall: {r.recall:.2%}")
            lines.append(f"- Expected blockers: {r.expected_blocker_rule_ids or '(none)'}")
            lines.append(f"- Detected blockers: {r.detected_blocker_rule_ids or '(none)'}")
            if r.true_positives:
                lines.append(f"- True positives: {r.true_positives}")
            if r.false_positives:
                lines.append(f"- False positives (unexpected): {r.false_positives}")
            if r.false_negatives:
                lines.append(f"- False negatives (missed): {r.false_negatives}")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_eval() -> EvalSummary:
    """Run the full eval harness and return the summary."""
    fixtures = discover_fixtures()
    if not fixtures:
        print(
            "[eval] WARNING: No fixtures discovered under fixtures/repos/. "
            "Each fixture must have labels.json + a paired fixtures/gcp/<name>.json.",
            file=sys.stderr,
        )

    results = [run_fixture(f) for f in fixtures]
    summary = compute_summary(results)

    json_path = emit_scorecard_json(summary)
    md_path = emit_scorecard_md(summary)

    print(f"\n[eval] {summary.headline}")
    print("[eval] Scorecard written to:")
    print(f"       JSON: {json_path}")
    print(f"       MD:   {md_path}")

    return summary


# ---------------------------------------------------------------------------
# pytest integration — each discovered fixture becomes one test
# ---------------------------------------------------------------------------

import pytest  # noqa: E402 (late import keeps the module standalone-runnable)


def pytest_collect_file(parent, file_path):  # type: ignore[no-untyped-def]
    """pytest hook: no-op (we use generate_fixture_tests below instead)."""


def _get_fixture_infos() -> list[dict]:  # type: ignore[type-arg]
    """Return all discovered fixture infos for pytest parametrize."""
    return discover_fixtures()


@pytest.mark.parametrize(
    "fixture_info",
    _get_fixture_infos(),
    ids=[f["name"] for f in _get_fixture_infos()],
)
def test_fixture_verdict_matches_ground_truth(fixture_info: dict) -> None:  # type: ignore[type-arg]
    """
    Eval harness parametrized pytest test.

    For each discovered fixture:
      - Run the full spine
      - Assert verdict matches labels.json expected_verdict
      - Assert all expected_blockers are detected (true positives)
      - Assert no unexpected will-fail deltas (false positives)
    """
    result = run_fixture(fixture_info)

    if result.error:
        pytest.fail(
            f"Fixture '{result.name}' errored during eval: {result.error}"
        )

    assert result.verdict_match, (
        f"Fixture '{result.name}': expected verdict='{result.expected_verdict}', "
        f"got='{result.verdict}'"
    )

    assert not result.false_negatives, (
        f"Fixture '{result.name}': expected blockers not detected (false negatives): "
        f"{result.false_negatives}"
    )

    assert not result.false_positives, (
        f"Fixture '{result.name}': unexpected will-fail deltas detected (false positives): "
        f"{result.false_positives}"
    )


@pytest.mark.parametrize(
    "fixture_info",
    _get_fixture_infos(),
    ids=[f["name"] for f in _get_fixture_infos()],
)
def test_fixture_deterministic_across_two_runs(fixture_info: dict) -> None:  # type: ignore[type-arg]
    """
    Eval harness determinism test: running the spine twice on the same fixture
    must produce identical rule_ids and verdict.
    """
    result1 = run_fixture(fixture_info)
    result2 = run_fixture(fixture_info)

    if result1.error or result2.error:
        pytest.skip(f"Fixture '{fixture_info['name']}' errored — skipping determinism check")

    assert result1.verdict == result2.verdict, (
        f"Fixture '{fixture_info['name']}': non-deterministic verdict "
        f"(run1={result1.verdict}, run2={result2.verdict})"
    )

    assert sorted(result1.detected_blocker_rule_ids) == sorted(result2.detected_blocker_rule_ids), (
        f"Fixture '{fixture_info['name']}': non-deterministic detected blockers "
        f"(run1={result1.detected_blocker_rule_ids}, run2={result2.detected_blocker_rule_ids})"
    )


def test_hero_fixture_killer_is_true_positive() -> None:
    """
    The hero fixture (worknote-ai-like) must have secret-ref-without-secretAccessor
    as a true positive. This is the primary eval acceptance criterion for Increment 1.
    """
    fixtures = _get_fixture_infos()
    hero = next((f for f in fixtures if f["name"] == "worknote-ai-like"), None)
    if hero is None:
        pytest.fail(
            "Hero fixture 'worknote-ai-like' not found under fixtures/repos/. "
            "It is required for Increment 1 eval."
        )

    result = run_fixture(hero)

    if result.error:
        pytest.fail(f"Hero fixture errored: {result.error}")

    assert "secret-ref-without-secretAccessor" in result.true_positives, (
        f"Hero fixture: 'secret-ref-without-secretAccessor' must be a true positive. "
        f"TPs={result.true_positives}, FNs={result.false_negatives}"
    )


def test_eval_scorecard_files_are_written() -> None:
    """
    Running run_eval() must produce both scorecard.json and scorecard.md.
    """
    summary = run_eval()

    json_path = _SCORECARD_DIR / "scorecard.json"
    md_path = _SCORECARD_DIR / "scorecard.md"

    assert json_path.exists(), f"scorecard.json not created at {json_path}"
    assert md_path.exists(), f"scorecard.md not created at {md_path}"

    # Validate JSON is parseable
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "headline" in data
    assert "aggregate_precision" in data
    assert "aggregate_recall" in data
    assert "fixtures" in data

    # Validate MD contains headline
    md_content = md_path.read_text(encoding="utf-8")
    assert "Caught" in md_content or "fixture" in md_content.lower()

    print(f"\n[test] Scorecard headline: {summary.headline}")


# ---------------------------------------------------------------------------
# Standalone entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    summary = run_eval()

    # Print per-fixture breakdown
    print("\nPer-fixture results:")
    for r in summary.fixture_results:
        status = "OK" if r.verdict_match and not r.error else ("ERROR" if r.error else "FAIL")
        print(
            f"  [{status}] {r.name}: "
            f"verdict={r.verdict} (expected={r.expected_verdict}), "
            f"P={r.precision:.2f} R={r.recall:.2f}"
        )
        if r.true_positives:
            print(f"         TP: {r.true_positives}")
        if r.false_positives:
            print(f"         FP: {r.false_positives}")
        if r.false_negatives:
            print(f"         FN: {r.false_negatives}")
        if r.error:
            print(f"         ERROR: {r.error}")

    print(f"\nAggregate: {summary.headline}")
    sys.exit(0 if summary.failed_fixtures == 0 and summary.errored_fixtures == 0 else 1)

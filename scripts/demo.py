#!/usr/bin/env python3
"""
LaunchGuard hero demo — deterministic, no Gemini API key or network required.

Runs the three-source reconciliation twice and prints verdict + findings + proposed fix:

  1. The REAL worknote-ai service  → no false positives (READY/WARN on a real prod service)
  2. The worknote-ai-gap counterfactual (JWT accessor dropped) → BLOCKED (the killer fires)

This is the offline demo backbone for the submission video: it does NOT call Gemini, so it is
fully reproducible and never flakes on camera. (The `adk web` trace shows the same pipeline
driven by the live Gemini orchestrator — see NETWORK_PASS.md.)

Usage:
    python scripts/demo.py            # uses the real ~/repos/github/private/worknote-ai repo
    python scripts/demo.py --like     # uses the self-contained worknote-ai-like fixture (portable)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from launchguard.fix_writer_core import run_fix_writer  # noqa: E402
from launchguard.reconciler.engine import reconcile  # noqa: E402
from launchguard.tools.declared_parser import parse_declared_state  # noqa: E402
from launchguard.tools.fixture_replay import fixture_replay  # noqa: E402
from launchguard.tools.repo_tools import build_intended_contract  # noqa: E402

WORKNOTE_REPO = Path.home() / "repos" / "github" / "private" / "worknote-ai"

CONTEXT = {
    "project_id": "worknote-ai",
    "runtime_sa": "serviceAccount:worknote-staging-run@worknote-ai.iam.gserviceaccount.com",
    "service_name": "worknote-ai-staging",
}

_BAR = "─" * 78


def _print_scenario(title: str, repo_path: Path, service_yaml: Path, live_fixture: str) -> None:
    print(f"\n{_BAR}\n  {title}\n{_BAR}")
    print(f"  intended ← {repo_path}")
    print(f"  declared ← {service_yaml.relative_to(repo_path) if service_yaml.is_relative_to(repo_path) else service_yaml}")
    print(f"  live     ← fixtures/gcp/{live_fixture}.json (read-only GCP snapshot)\n")

    intended = build_intended_contract(str(repo_path))
    declared = parse_declared_state(str(service_yaml))
    live = fixture_replay(live_fixture)
    deltas = reconcile(intended, declared, live)
    result = run_fix_writer(deltas, context=CONTEXT)
    sc = result.scorecard
    c = sc.counts

    print(f"  VERDICT: {sc.verdict}   "
          f"(will-fail={c.will_fail}, will-misbehave={c.will_misbehave}, "
          f"cost-risk={c.cost_risk}, needs-review={c.needs_review})")

    if not deltas:
        print("  → No discrepancies across the three sources. Service is deploy-ready.")
        return

    for d in deltas:
        rule = getattr(d, "rule_id", "?")
        cls = getattr(d, "classification", getattr(d, "delta_class", "?"))
        conf = getattr(d, "confidence", "?")
        summary = getattr(d, "summary", getattr(d, "title", ""))
        print(f"\n  • [{cls}] {rule} (conf {conf})\n    {summary}")

    # Show the proposed fix — prefer the will-fail finding (the deploy-blocker)
    if result.patches:
        blocker_rules = {
            getattr(d, "rule_id", None)
            for d in deltas
            if "fail" in str(getattr(d, "classification", getattr(d, "delta_class", "")))
        }
        top = next((p for p in result.patches if p.rule_id in blocker_rules), result.patches[0])
        if getattr(top, "content", ""):
            print(f"\n  Proposed fix for '{top.rule_id}' "
                  f"(PR — applied={top.applied}, human-in-the-loop):")
            for line in str(top.content).strip().splitlines():
                print(f"    {line}")


def main() -> int:
    use_like = "--like" in sys.argv

    if use_like or not WORKNOTE_REPO.exists():
        if not use_like:
            print(f"[note] {WORKNOTE_REPO} not found — falling back to the portable "
                  f"worknote-ai-like fixture.")
        like_repo = REPO_ROOT / "fixtures" / "repos" / "worknote-ai-like"
        _print_scenario(
            "SCENARIO 1 — worknote-ai-like (correctly configured)",
            like_repo, like_repo / "infra" / "cloud-run" / "service.yaml", "worknote-ai-like",
        )
        # gap fixture for the like repo (killer demo)
        gap = REPO_ROOT / "fixtures" / "gcp" / "worknote-ai-like-gap.json"
        gap_name = "worknote-ai-like-gap" if gap.exists() else "worknote-ai-gap"
        _print_scenario(
            "SCENARIO 2 — same repo, one IAM grant missing (counterfactual)",
            like_repo, like_repo / "infra" / "cloud-run" / "service.yaml", gap_name,
        )
    else:
        svc = WORKNOTE_REPO / "infra" / "cloud-run" / "service.yaml"
        _print_scenario(
            "SCENARIO 1 — REAL worknote-ai-staging (live GCP state, redacted snapshot)",
            WORKNOTE_REPO, svc, "worknote-ai",
        )
        _print_scenario(
            "SCENARIO 2 — worknote-ai with JWT_SECRET_KEY accessor dropped (counterfactual)",
            WORKNOTE_REPO, svc, "worknote-ai-gap",
        )

    print(f"\n{_BAR}")
    print("  Three-source contract reconciliation: the value is the DELTA between")
    print("  what the code wants, what the deploy declares, and what GCP actually grants.")
    print(f"{_BAR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

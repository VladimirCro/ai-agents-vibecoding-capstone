#!/usr/bin/env python3
"""
scripts/record_fixture.py — Record a real, redacted LiveState fixture from GCP.

Orchestrates live gcloud_read calls for project worknote-ai / service worknote-ai-staging
and writes a redacted snapshot to fixtures/gcp/worknote-ai.json.

All calls are READ-ONLY (verb allow-list enforced by gcloud_read guardrail).
No secret VALUES are written — only names, accessor member principals, roles, API names,
and a minimal non-sensitive run_config (port, probe presence bools, scaling numbers,
secret ref NAMES).

Usage:
    venv/bin/python scripts/record_fixture.py [--reconcile]

Flags:
    --reconcile   After recording the fixture, also run the three-source reconciliation
                  against the recorded fixture and print the verdict + deltas.

AI Operating Principles §1 + §3:
  - All gcloud calls are read-only (guardrail check_gcloud_verb fires first).
  - record_live_state() runs redact_snapshot() at capture time before writing to disk.
  - This script prints only COUNTS, never VALUES.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the repo root importable when run from the repo root
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from launchguard.tools.gcloud_read import gcloud_read  # noqa: E402
from launchguard.tools.fixture_replay import record_live_state  # noqa: E402
from launchguard.models import Mode  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — real project facts (no secrets)
# ---------------------------------------------------------------------------

PROJECT_ID = "worknote-ai"
SERVICE_NAME = "worknote-ai-staging"
REGION = "europe-west1"
RUNTIME_SA = "worknote-staging-run@worknote-ai.iam.gserviceaccount.com"
FIXTURE_NAME = "worknote-ai"


def record_worknote_ai_fixture() -> dict:
    """
    Call gcloud_read for each resource, assemble a full LiveState dict, and write
    the redacted fixture to fixtures/gcp/worknote-ai.json.

    Returns the assembled LiveState dict (already redacted by record_live_state).
    """
    print(f"[record] project={PROJECT_ID}  service={SERVICE_NAME}  region={REGION}")
    print(f"[record] runtime_sa={RUNTIME_SA}")
    print()

    # ------------------------------------------------------------------
    # 1. SA IAM roles
    # ------------------------------------------------------------------
    print("[1/4] Reading SA IAM roles (gcloud projects get-iam-policy)...")
    sa_fragment = gcloud_read(
        resource="sa-iam",
        verb="get-iam-policy",
        project_id=PROJECT_ID,
        mode=Mode.LIVE,
        runtime_sa=RUNTIME_SA,
    )
    sa_iam_roles: list[str] = sa_fragment.get("sa_iam_roles", [])
    print(f"      → {len(sa_iam_roles)} role(s) bound to runtime SA")

    # ------------------------------------------------------------------
    # 2. Enabled APIs
    # ------------------------------------------------------------------
    print("[2/4] Reading enabled APIs (gcloud services list)...")
    apis_fragment = gcloud_read(
        resource="enabled-apis",
        verb="list",
        project_id=PROJECT_ID,
        mode=Mode.LIVE,
    )
    enabled_apis: list[str] = apis_fragment.get("enabled_apis", [])
    print(f"      → {len(enabled_apis)} API(s) enabled")

    # ------------------------------------------------------------------
    # 3. Secrets list (names only, accessor_members=[])
    # ------------------------------------------------------------------
    print("[3/4] Reading secret names (gcloud secrets list)...")
    secrets_fragment = gcloud_read(
        resource="secrets",
        verb="list",
        project_id=PROJECT_ID,
        mode=Mode.LIVE,
    )
    secrets_list: list[dict] = secrets_fragment.get("secrets", [])
    print(f"      → {len(secrets_list)} secret(s) found")

    # ------------------------------------------------------------------
    # 4. Secret accessor members (per secret)
    # ------------------------------------------------------------------
    print("[4/4] Reading secret accessor members (gcloud secrets get-iam-policy per secret)...")
    secrets_with_accessors: list[dict] = []
    total_accessor_grants = 0
    for secret_entry in secrets_list:
        secret_name = secret_entry["name"]
        accessor_fragment = gcloud_read(
            resource="secret-accessors",
            verb="get-iam-policy",
            project_id=PROJECT_ID,
            mode=Mode.LIVE,
            secret_name=secret_name,
        )
        accessor_members = accessor_fragment.get("accessor_members", [])
        total_accessor_grants += len(accessor_members)
        secrets_with_accessors.append({
            "name": secret_name,
            "accessor_members": accessor_members,
        })
    print(f"      → {total_accessor_grants} secretAccessor grant(s) across {len(secrets_list)} secret(s)")

    # ------------------------------------------------------------------
    # 4b. Run config (minimal — no value blobs)
    # ------------------------------------------------------------------
    print("[4b] Reading Cloud Run service config (gcloud run services describe)...")
    run_fragment = gcloud_read(
        resource="run-config",
        verb="describe",
        project_id=PROJECT_ID,
        mode=Mode.LIVE,
        service_name=SERVICE_NAME,
        region=REGION,
    )
    run_config = run_fragment.get("run_config")
    if run_config:
        print(f"      → container_port={run_config.get('container_port')}, "
              f"startup_probe={run_config.get('has_startup_probe')}, "
              f"liveness_probe={run_config.get('has_liveness_probe')}")
    else:
        print("      → run_config not retrieved")

    # ------------------------------------------------------------------
    # Assemble full LiveState dict
    # ------------------------------------------------------------------
    live_state_dict: dict = {
        "project_id": PROJECT_ID,
        "runtime_sa": f"serviceAccount:{RUNTIME_SA}",
        "sa_iam_roles": sa_iam_roles,
        "enabled_apis": enabled_apis,
        "secrets": secrets_with_accessors,
        "mode": Mode.FIXTURE,   # mode=fixture after recording (for replay)
        "run_config": run_config,
    }

    # ------------------------------------------------------------------
    # Write to fixtures/gcp/worknote-ai.json (redact_snapshot runs inside)
    # ------------------------------------------------------------------
    print()
    print(f"[write] Writing redacted fixture → fixtures/gcp/{FIXTURE_NAME}.json")
    record_live_state(FIXTURE_NAME, live_state_dict)

    fixture_path = _REPO_ROOT / "fixtures" / "gcp" / f"{FIXTURE_NAME}.json"
    file_size = fixture_path.stat().st_size
    print(f"[write] Done. File size: {file_size} bytes")

    return live_state_dict


def run_reconciliation(live_state_dict: dict) -> None:
    """
    Run the three-source reconciliation on real data and print the verdict.

    Uses:
      - IntendedContract: built from ~/repos/github/private/worknote-ai
      - DeclaredState:    parsed from infra/cloud-run/service.yaml
      - LiveState:        loaded from the recorded fixture (fixture mode)
    """
    print()
    print("=" * 60)
    print("THREE-SOURCE RECONCILIATION — worknote-ai-staging")
    print("=" * 60)

    repo_path = str(Path.home() / "repos" / "github" / "private" / "worknote-ai")
    service_yaml_path = str(
        Path.home() / "repos" / "github" / "private" / "worknote-ai"
        / "infra" / "cloud-run" / "service.yaml"
    )

    # IntendedContract
    print()
    print("[intended] Building IntendedContract from repo...")
    try:
        from launchguard.tools.repo_tools import build_intended_contract  # noqa: PLC0415
        intended = build_intended_contract(repo_path)
        print(f"  port={intended.port}, host_binding={intended.host_binding}, "
              f"pid1_safe={intended.pid1_signal_safe}, base_pinned={intended.base_image_pinned}")
        print(f"  secret_refs ({len(intended.secret_refs)}): {sorted(intended.secret_refs)}")
        print(f"  expects_health_probe={intended.expects_health_probe}, "
              f"expects_startup_probe={intended.expects_startup_probe}")
        print(f"  confidence={intended.confidence}")
    except Exception as e:
        print(f"  ERROR building IntendedContract: {e}")
        return

    # DeclaredState
    print()
    print("[declared] Parsing DeclaredState from service.yaml...")
    try:
        from launchguard.tools.declared_parser import parse_declared_state  # noqa: PLC0415
        declared = parse_declared_state(service_yaml_path)
        print(f"  container_port={declared.container_port}, "
              f"has_liveness_probe={declared.has_liveness_probe}, "
              f"has_startup_probe={declared.has_startup_probe}")
        print(f"  secret_refs ({len(declared.secret_refs)}): {sorted(declared.secret_refs)}")
        print(f"  scaling: {declared.scaling.to_dict()}")
    except Exception as e:
        print(f"  ERROR parsing DeclaredState: {e}")
        return

    # LiveState (from fixture)
    print()
    print("[live] Loading LiveState from recorded fixture...")
    try:
        from launchguard.tools.fixture_replay import fixture_replay  # noqa: PLC0415
        live = fixture_replay(FIXTURE_NAME)
        print(f"  project_id={live.project_id}, runtime_sa={live.runtime_sa}")
        print(f"  sa_iam_roles ({len(live.sa_iam_roles)}): {live.sa_iam_roles}")
        print(f"  enabled_apis ({len(live.enabled_apis)}): (truncated to first 5) "
              f"{live.enabled_apis[:5]}...")
        print(f"  secrets ({len(live.secrets)} total)")
    except Exception as e:
        print(f"  ERROR loading LiveState: {e}")
        return

    # Reconcile
    print()
    print("[reconcile] Running deterministic reconciler...")
    try:
        from launchguard.reconciler.engine import reconcile  # noqa: PLC0415
        from launchguard.models import ReadinessScorecard  # noqa: PLC0415
        deltas = reconcile(intended, declared, live)
        scorecard = ReadinessScorecard.from_deltas(deltas)

        print()
        print(f"VERDICT: {scorecard.verdict}")
        print(f"Counts:  will_fail={scorecard.counts.will_fail}, "
              f"will_misbehave={scorecard.counts.will_misbehave}, "
              f"cost_risk={scorecard.counts.cost_risk}, "
              f"needs_review={scorecard.counts.needs_review}")
        print(f"Total deltas: {len(deltas)}")

        if deltas:
            print()
            print("Deltas:")
            for i, delta in enumerate(deltas, 1):
                print(f"  [{i}] rule_id={delta.rule_id}")
                print(f"       class={delta.delta_class}, confidence={delta.confidence}")
                print(f"       summary={delta.summary[:200]}...")
        else:
            print()
            print("  (no deltas — service is READY)")

    except Exception as e:
        print(f"  ERROR during reconciliation: {e}")
        import traceback
        traceback.print_exc()


def run_gap_variant_demo(live_state_dict: dict) -> None:
    """
    Demonstrate the 'killer' finding by deriving a worknote-ai-gap variant:
    Drop accessor grants on JWT_SECRET_KEY (set accessor_members=[]) and re-run.

    This should fire secret-ref-without-secretAccessor → verdict BLOCKED.
    """
    print()
    print("=" * 60)
    print("GAP VARIANT DEMO — secret-ref-without-secretAccessor killer")
    print("=" * 60)

    # Find which secret from declared/intended to drop (pick JWT_SECRET_KEY if present)
    target_secret = "JWT_SECRET_KEY"

    # Load the real fixture and modify
    fixture_path = _REPO_ROOT / "fixtures" / "gcp" / f"{FIXTURE_NAME}.json"
    with open(fixture_path) as f:
        gap_dict = json.load(f)

    # Drop accessor grants on the target secret
    found = False
    for s in gap_dict.get("secrets", []):
        if s["name"] == target_secret:
            s["accessor_members"] = []
            found = True
            break

    if not found:
        print(f"  NOTE: {target_secret} not in fixture secrets list — demo skipped.")
        return

    print(f"  Dropping accessor_members for {target_secret} → []")

    # Write gap variant to a temp fixture name
    gap_fixture_name = "worknote-ai-gap"
    record_live_state(gap_fixture_name, gap_dict)
    print(f"  Written gap variant → fixtures/gcp/{gap_fixture_name}.json")

    # Reconcile with gap
    print()
    print("[reconcile-gap] Running reconciler on gap variant...")
    try:
        repo_path = str(Path.home() / "repos" / "github" / "private" / "worknote-ai")
        service_yaml_path = str(
            Path.home() / "repos" / "github" / "private" / "worknote-ai"
            / "infra" / "cloud-run" / "service.yaml"
        )
        from launchguard.tools.repo_tools import build_intended_contract  # noqa: PLC0415
        from launchguard.tools.declared_parser import parse_declared_state  # noqa: PLC0415
        from launchguard.tools.fixture_replay import fixture_replay  # noqa: PLC0415
        from launchguard.reconciler.engine import reconcile  # noqa: PLC0415
        from launchguard.models import ReadinessScorecard  # noqa: PLC0415

        intended = build_intended_contract(repo_path)
        declared = parse_declared_state(service_yaml_path)
        live_gap = fixture_replay(gap_fixture_name)

        deltas = reconcile(intended, declared, live_gap)
        scorecard = ReadinessScorecard.from_deltas(deltas)

        print(f"  VERDICT (gap): {scorecard.verdict}")
        print(f"  Counts: will_fail={scorecard.counts.will_fail}, "
              f"will_misbehave={scorecard.counts.will_misbehave}")

        gap_deltas = [d for d in deltas if d.rule_id == "secret-ref-without-secretAccessor"]
        if gap_deltas:
            print()
            print(f"  KILLER FINDING FIRED ({len(gap_deltas)} delta(s)):")
            for d in gap_deltas:
                print(f"    rule_id={d.rule_id}, class={d.delta_class}, "
                      f"confidence={d.confidence}")
                print(f"    summary={d.summary[:200]}")
        else:
            # JWT_SECRET_KEY might not be in intended.secret_refs; report honestly
            print(f"  NOTE: secret-ref-without-secretAccessor did NOT fire.")
            print(f"  (JWT_SECRET_KEY may not be in intended.secret_refs={intended.secret_refs})")
            all_rule_ids = [d.rule_id for d in deltas]
            print(f"  All rule_ids that fired: {all_rule_ids}")

    except Exception as e:
        print(f"  ERROR during gap reconciliation: {e}")
        import traceback
        traceback.print_exc()


def main() -> None:
    do_reconcile = "--reconcile" in sys.argv

    # Step 1: Record the real fixture
    live_state_dict = record_worknote_ai_fixture()

    if do_reconcile:
        # Step 2: Three-source reconciliation on real data
        run_reconciliation(live_state_dict)

        # Step 3: Gap variant demo
        run_gap_variant_demo(live_state_dict)

    print()
    print("[done] Fixture recorded. No git commit performed. No secrets written.")


if __name__ == "__main__":
    main()

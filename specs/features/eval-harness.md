# Feature: Eval harness + scorecard

## Goal
Run LaunchGuard against 8–10 intentionally-misconfigured repo fixtures with ground-truth blocker labels; compute precision/recall; emit a scorecard with a headline metric.

## User Persona
Capstone reviewer who wants numbers ("caught X/Y blockers, opened Z fix-PRs") and the builder who needs regression safety.

## Acceptance Criteria
- Given 8–10 fixture repos each with a ground-truth labels file, When `run_eval` runs, Then it reports per-fixture detected vs expected blockers + aggregate precision/recall.
- Given the hero fixture (secret/IAM gap), When the eval runs, Then the SECRET_FOO will-fail delta is detected (true positive).
- Given the eval completes, When the scorecard is written, Then a headline metric is present in both JSON and Markdown.
- Given a fixture run, When repeated, Then results are stable (relies on fixture-layer determinism).

## Fixture set (ground-truth blockers; hero = secret/IAM)
1. secret-ref-without-secretAccessor (HERO), 2. secret-declared-not-created,
3. port-mismatch, 4. missing-health-probe, 5. pid1-signal-unsafe,
6. over-broad-sa-role, 7. api-not-enabled / missing-required-role,
8. scaling-cost-flag, 9. host-not-0.0.0.0, 10. clean repo (true-negative control).

## Implementation Notes
- pytest-driven runner; each fixture pairs a misconfigured repo + a golden-JSON live snapshot + a labels file.
- Scorecard: JSON (machine) + Markdown (writeup). Precision/recall computed against labels.
- The clean-repo control guards against false positives (AI Operating Principles §8 cost asymmetry).

## Out of Scope
- Live-GCP eval; performance benchmarking.

## Dependencies
- Reconciler, fixture layer, misconfigured repo fixtures.

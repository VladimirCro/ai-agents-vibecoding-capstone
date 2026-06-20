# ADR-003: Golden-JSON fixture-replay strategy for offline reproducibility

Status: Proposed (pending Gate A)
Date: 2026-06-20

## Context

The headline feature — the three-source reconciliation — depends on reading **Live** GCP state. Live access is flaky for a solo, deadline-bound build (auth, quota, project access) and impossible for a capstone jury to reproduce. `PLAN.md` and `docs/TECH_STACK.md` mark a **golden-JSON fixture layer as NON-NEGOTIABLE (Day 3)**: without it the demo is flaky and the eval is not reproducible. AI Operating Principles §6 requires determinism (same input → same finding) as a precondition for the eval scorecard.

## Decision

Introduce a single **mode seam** on GcpStateInspector via `LAUNCHGUARD_MODE`:
- `live` — read-only gcloud calls, no persistence.
- `record` — read-only gcloud calls once, **redact at capture time**, persist a golden-JSON snapshot under `fixtures/gcp/`.
- `fixture` — **zero network**; load the snapshot from disk.

All three modes return the **identical `LiveState` shape**, so the Reconciler is mode-agnostic. Redaction (AI Operating Principles §3) happens at capture so committed fixtures contain secret *names/existence* but never *values*. The eval harness and the demo both run in `fixture` mode by default.

## Consequences

- **Positive:** reproducible offline demo + deterministic eval; jury can run it with no GCP access; tests can assert "zero network, zero mutation" in fixture mode.
- **Positive:** decouples build progress from live-GCP availability — the recording is a one-time step against worknote-ai.
- **Positive:** redaction-at-capture makes fixtures safely committable.
- **Negative:** fixtures can drift from real GCP over time (a recorded snapshot is a point-in-time truth); mitigated by re-recording before the final demo.
- **Negative:** the recording step must be done at least once with live access; if worknote-ai access is unavailable, hand-authored fixtures are the fallback (lower fidelity).

## Alternatives Considered

1. **Live-only:** highest fidelity, but flaky demo + non-reproducible eval + jury cannot run it. Rejected (violates the NON-NEGOTIABLE).
2. **Mock the gcloud client in code (no recorded snapshots):** reproducible but not grounded in real outputs, weakening the "real service" credibility and the killer-trace authenticity. Rejected as the primary; hand-authored fixtures are only the fallback.
3. **Record full gcloud stdout verbatim (no normalization):** brittle to gcloud version/format changes. Rejected — we normalize to the `LiveState` schema at capture.

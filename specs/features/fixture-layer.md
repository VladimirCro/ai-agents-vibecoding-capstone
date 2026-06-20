# Feature: Golden-JSON fixture layer (NON-NEGOTIABLE)

## Goal
Record real gcloud outputs once (redacted), replay offline — so the full pipeline + eval are reproducible without live GCP. This is the keystone that makes the headline feature demoable to the jury without flaky live access.

## User Persona
Capstone reviewer (reproducible offline demo) + deploy engineer (deterministic re-runs).

## Modes
- `live` — call gcloud read-only, no persistence.
- `record` — call gcloud read-only once, redact, persist a golden-JSON snapshot under `fixtures/gcp/`.
- `fixture` — zero network; load snapshot from disk.

## Acceptance Criteria
- Given record mode against worknote-ai, When fixtures are captured, Then a golden-JSON snapshot exists under `fixtures/gcp/` with all secret values redacted (names/existence only).
- Given fixture mode, When the full agent pipeline runs, Then it completes end-to-end with zero network calls to GCP.
- Given a fixture, When replayed N times, Then findings are identical (deterministic).
- Given any captured snapshot, When inspected, Then it contains no secret value, token, or connection-string value (redaction verified).

## Implementation Notes
- One mode seam (`LAUNCHGUARD_MODE`) gates GcpStateInspector. The same shape is returned in all modes so the Reconciler is mode-agnostic.
- Redaction at capture time (AI Operating Principles §3) — a fixture is committable safely.
- Determinism is a precondition for the eval scorecard (AI Operating Principles §6).

## Out of Scope
- Live mutation; capturing non-GCP state.

## Dependencies
- GcpStateInspector; guardrails (redaction).

# Feature: GcpStateInspector — Live GCP read + fixture replay

## Goal
Read **Live** GCP state read-only (runtime SA IAM bindings, enabled APIs, Secret Manager secrets + accessor grants, existing Cloud Run config), OR replay a golden-JSON fixture — so the Reconciler has the third source.

## User Persona
Deploy-day engineer. Wants the true current state of GCP without risk of mutation, and a jury/reviewer who can reproduce the run offline.

## Live state — fields (from real worknote-ai shapes)
- `runtime_sa` (the Cloud Run service account email)
- `sa_iam_roles` (e.g. `roles/secretmanager.secretAccessor`, `roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/logging.logWriter`)
- `enabled_apis`
- `secrets[]` with `name` + `accessor_members` (which SAs hold `secretAccessor` on each)
- `run_config` (existing service: port, scaling, probes, SA)

## Acceptance Criteria
- Given `LAUNCHGUARD_MODE=fixture` and a recorded snapshot, When GcpStateInspector runs, Then it returns the live-state shape from the fixture with zero network calls.
- Given `LAUNCHGUARD_MODE=live`, When GcpStateInspector runs, Then every gcloud invocation is a read/list/describe verb; any mutating verb is rejected before execution and logged (guardrail trip).
- Given live read in record mode, When GcpStateInspector runs, Then raw gcloud output is persisted as a redacted golden-JSON fixture.
- Given the same fixture replayed twice, When GcpStateInspector runs, Then the returned live-state is byte-identical.

## Implementation Notes
- `gcloud_read` tool: native shell wrapper + gcloud-mcp interop (Day-2 rubric). Allow-list restricted to read verbs.
- Mode switch (`live` / `record` / `fixture`) is the single seam that makes the whole pipeline offline-reproducible.
- Redaction happens at capture time so fixtures never contain secret values.

## Out of Scope
- Any mutating gcloud call (hard constraint). Cross-project aggregation.

## Dependencies
- ADK skeleton; fixture layer (for record/replay); guardrails (read-only enforcement + allow-list).

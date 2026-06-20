# Feature: Reconciler — three-source diff + classification (CORE IP)

## Goal
Diff Intended ⟷ Declared ⟷ Live, classify each delta as `will-fail` / `will-misbehave` / `cost-risk` with confidence + evidence. This is the differentiator — the delta no single existing tool computes.

## User Persona
Deploy-day engineer who needs a single, evidence-backed readiness verdict, not three disconnected reports.

## Detector rules (ground-truth from CLOUD_RUN_DEPLOYMENT_PLAYBOOK.md + worknote-ai)
| Rule | Class | Trigger |
|---|---|---|
| `secret-ref-without-secretAccessor` [KILLER] | will-fail | code/declared references secret X; runtime SA lacks `secretAccessor` on X in Live |
| `secret-declared-not-created` | will-fail | declared `secretKeyRef` for X; X absent from Live Secret Manager |
| `port-mismatch` | will-misbehave | intended.port ≠ declared.containerPort |
| `host-not-0.0.0.0` | will-misbehave | app binds localhost/127.0.0.1 |
| `missing-health-probe` / `missing-startup-probe` | will-misbehave | intended expects /health,/ready; declared has no probe |
| `pid1-signal-unsafe` | will-misbehave | shell-form CMD (SIGTERM lost) |
| `over-broad-sa-role` | will-misbehave (security) | runtime SA holds owner/editor |
| `missing-required-role` | will-fail | code needs an API (e.g. Vertex) but SA lacks the role (e.g. `roles/aiplatform.user`) |
| `api-not-enabled` | will-fail | code/declared needs an API not in Live `enabled_apis` |
| `scaling-cost-flag` | cost-risk | minScale high, or maxScale×concurrency unbounded, or cpu-throttling off |
| `unpinned-base-image` | will-misbehave (advisory) | base image `:latest` |
| `ambiguous` | needs-review | no deterministic rule fires + inputs ambiguous → Gemini classifies |

## Acceptance Criteria
- Given code requires SECRET_FOO, service.yaml declares it, but the runtime SA has no secretAccessor grant in Live, When the Reconciler runs, Then it emits a `will-fail` `secret-ref-without-secretAccessor` delta naming SECRET_FOO + the SA + the missing role, high confidence. [KILLER]
- Given intended.port=8080 but declared.containerPort=3000, When the Reconciler runs, Then `port-mismatch` (will-misbehave) with both values as evidence.
- Given service.yaml has no probes but intended expects /health, When the Reconciler runs, Then `missing-health-probe` (will-misbehave).
- Given the runtime SA holds roles/owner, When the Reconciler runs, Then `over-broad-sa-role` (will-misbehave/security) with a least-privilege recommendation.
- Given minScale=0 + slow cold start + high maxScale, When the Reconciler runs, Then a `cost-risk` advisory (non-blocking).
- Given no deterministic rule fires and inputs are ambiguous, When the Reconciler runs, Then Gemini classifies it and the delta is `needs-review` — never confidently `will-fail` (AI Operating Principles §8 fail-safe).

## Implementation Notes
- Deterministic detector rules first; Gemini 2.5 Pro only for ambiguity classification + human-readable explanation generation.
- Each delta carries: `rule_id`, `class`, `confidence`, `evidence` (source + locator), `recommendation`.
- Reconciler has NO external tools (AI Operating Principles §4) — pure logic + model for ambiguity; trusts only schema-validated inputs.

## Out of Scope
- Applying any fix; cost forecasting beyond simple flags.

## Dependencies
- RepoAuditor (Intended), Declared parser (Declared), GcpStateInspector (Live).

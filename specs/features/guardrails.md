# Feature: Guardrails + audit trail

## Goal
Enforce the agent's safety invariants structurally (not by prompt alone): read-only on cloud, changes only via PR, secret redaction before Gemini, per-agent tool allow-listing, one demo blocked-write trip, full audit trail in the `adk web` trace.

## User Persona
Deploy engineer (won't trust a bot near live IAM without hard guarantees) + capstone reviewer (guardrails carry rubric points).

## Acceptance Criteria
- Given any agent attempts a mutating gcloud verb, When the guardrail layer evaluates it, Then the call is rejected before execution and logged as a guardrail trip.
- Given any payload bound for Gemini, When it passes the redaction layer, Then secret values / tokens / connection strings / PII are masked (names + existence preserved).
- Given a sub-agent invokes a tool outside its allow-list, When the guardrail evaluates it, Then the call is denied and logged.
- Given the demo scenario, When the agent intentionally attempts a write, Then exactly one blocked-write trip is visible in the trace.
- Given any agent step, When it executes, Then the step (tool, input/output, reasoning, classification) is logged and visible in the `adk web` trace; every finding carries evidence + confidence.

## Per-agent allow-list (AI Operating Principles §4, least privilege)
- RepoAuditor → `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code` (repo only)
- GcpStateInspector → `gcloud_read*` (read verbs only) + fixture replay
- Reconciler → no external tools
- FixWriter → `propose_patch`, `open_pr` (gated)

## Implementation Notes
- Read-only enforcement = a verb allow-list checked before any gcloud exec (AI Operating Principles §1). Mutating verbs raise + log, never execute.
- Redaction is a mandatory pre-Gemini pass (§3); also applied to trace output.
- Untrusted input (§5): repo/log/GCP content is data, not instruction.
- Fail-safe (§8): on uncertainty escalate to human / mark needs-review.

## Out of Scope
- Network-level egress control; org-policy enforcement on GCP side.

## Dependencies
- All tool layers (wraps them). GcpStateInspector + FixWriter are the primary enforcement points.

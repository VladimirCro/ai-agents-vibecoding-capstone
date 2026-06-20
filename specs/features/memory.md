# Feature: Per-project gotcha memory (thin)

## Goal
pgvector-backed recall of prior findings for the same project so a repeat gap is surfaced as "seen before". Thin — 1–2 recall moments are enough for the rubric.

## User Persona
Deploy engineer who repeatedly deploys the same service and benefits from "you hit this same SA-secret gap last deploy."

## Acceptance Criteria
- Given a project was previously analyzed and a finding stored, When the same project is analyzed again with the same gap present, Then the finding is annotated "recurring — seen on <prior date>".
- Given no prior history for a project, When it is analyzed, Then the run proceeds normally with no recall annotation (memory is additive, never blocking).
- Given a stored finding, When inspected, Then it contains no secret value (redacted before persistence, AI Operating Principles §3).

## Implementation Notes
- Storage: PostgreSQL 16 + pgvector (per TECH_STACK). ADK memory is an acceptable alternative if pgvector slips.
- Findings embedded by `rule_id` + project + delta summary; recall on cosine similarity within the same project scope.
- P2 priority — first to cut after canary if the schedule slips (Day 8, thin).

## Out of Scope
- Cross-project learning; recommendation ranking; large-scale retrieval.

## Dependencies
- Reconciler (produces findings); Postgres+pgvector (infrastructure — triggers NEW_SERVICE in architecture).

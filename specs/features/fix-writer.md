# Feature: FixWriter — diffs, readiness scorecard, fix-PR

## Goal
Turn classified deltas into actionable, reviewable output: unified diffs (IAM grant commands, Dockerfile/service.yaml edits) + a readiness scorecard, opened as a human-in-the-loop fix-PR. NEVER mutate live GCP; never auto-commit to main.

## User Persona
Deploy-day engineer who trusts a reviewable PR over an opaque autofix and wants a one-line readiness verdict.

## Acceptance Criteria
- Given a will-fail secret-accessor delta, When FixWriter runs, Then it produces a proposed patch containing the exact `gcloud secrets add-iam-policy-binding ... --role=roles/secretmanager.secretAccessor` command (or service.yaml/Terraform edit) in the PR body — it does NOT execute it.
- Given a set of classified deltas, When FixWriter runs, Then it emits a readiness scorecard (JSON + Markdown) with counts per class + overall verdict (BLOCKED / WARN / READY).
- Given `open_pr` is invoked, When the PR is created, Then the agent surfaces the PR URL and stops — it never merges.
- Given LaunchGuard runs against a target, When FixWriter completes, Then no commit is pushed to the target's main branch and no live GCP resource is changed (assertable in fixture mode).

## Verdict logic
- any `will-fail` delta → **BLOCKED**
- else any `will-misbehave` → **WARN**
- else (only cost-risk / clean) → **READY**

## Implementation Notes
- Tools: `propose_patch`, `open_pr` via **GitHub MCP** (Day-2 MCP interop, human gate).
- Fix for secret/IAM deltas = a PR with the gcloud command + a service.yaml/Terraform snippet; the human applies it. This is the "changes only via PR" guardrail (AI Operating Principles §2) made concrete.
- Scorecard is the headline artifact for the writeup + eval.

## Out of Scope
- Auto-merge; applying any change to live GCP or main branch.

## Dependencies
- Reconciler (classified deltas); guardrails (PR gate + redaction); GitHub MCP.

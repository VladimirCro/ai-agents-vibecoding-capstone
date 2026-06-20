# PRD — LaunchGuard

> Product Requirements Document
> Status: Approved — Gate A (Continue, 2026-06-20)
> Author: product-manager (Phase 1) | Date: 2026-06-20
> Source of truth: `CLAUDE.md`, `docs/TECH_STACK.md`, `docs/AI_OPERATING_PRINCIPLES.md`, `PLAN.md`
> Framing (mandatory): **three-source contract reconciliation**. NEVER "AI Dockerfile/IAM linter".

---

## 1. Product Goal

LaunchGuard is an autonomous Google ADK + Gemini 2.5 agent that, **before** a Cloud Run deploy, reconciles three sources of truth — what the code requires (Intended), what the deploy config declares (Declared), and what GCP actually grants (Live, read-only) — and catches the "silent-misbehave" class of misconfiguration (deploy succeeds, app 500s on the first request), then opens a human-in-the-loop fix-PR. It is for backend/SRE engineers shipping FastAPI services to Cloud Run.

## 2. User Persona

**Primary: "Deploy-day Backend/SRE Engineer."** Ships FastAPI services to Cloud Run (e.g. the `worknote-ai` maintainer). Cares about: not getting paged at 02:00 because a green deploy 500s on the first request; catching the gap between "what my code needs" and "what the runtime service account actually has" before it reaches prod; an auditable, reviewable change rather than an opaque autofix. Trusts diffs and PRs, distrusts a bot that mutates live IAM. Time-poor on deploy day; wants a single readiness verdict with evidence, not a 40-page report.

**Secondary: capstone reviewer (Google × Kaggle, *Agents for Business* track).** Cares about: a reproducible demo (works offline, no live GCP needed), a clear multi-agent architecture, real guardrails, MCP interop, and an eval scorecard with numbers.

## 3. Problem Statement

A Cloud Run deploy engineer struggles with the **silent-misbehave class of deploy failure** — where `gcloud run deploy` reports success but the service 500s on the first real request — because **no existing tool computes the delta across all three sources of truth at once**: Checkov reads only the repo, GCP Security Review reads only live state, the deploy pipeline validates only the declaration. The value lives in the *delta between the three*. This results in failures that pass CI, pass deploy, and only surface in production (e.g. code references `SECRET_FOO`, the deploy declares the env var, but the runtime service account was never granted `roles/secretmanager.secretAccessor` on it → first request crashes). The engineer discovers this manually, under pressure, after the outage.

## 4. Scope

### In scope

- **RepoAuditor** — infer the "Intended contract" from a target repo (Dockerfile `PORT`/host binding, entrypoint/PID-1, declared env vars, secret refs, health/startup probe expectations, pinned base image).
- **GcpStateInspector** — read **Live** GCP state read-only (runtime SA IAM bindings, enabled APIs, Secret Manager secrets + accessor grants, existing Cloud Run config) via gcloud-mcp or shell, OR replay from golden-JSON fixtures.
- **Declared parser** — parse the deploy declaration (Cloud Run `service.yaml`, `cloudbuild.yaml`, deploy workflow) into a normalized shape.
- **Reconciler** (core IP) — diff Intended ⟷ Declared ⟷ Live, classify each delta as `will-fail` / `will-misbehave` / `cost-risk`, with confidence and evidence.
- **FixWriter** — generate unified diffs + a readiness scorecard and open a **fix-PR** (GitHub MCP, human gate). NEVER mutates live GCP, never auto-commits to main.
- **Golden-JSON fixture layer** (NON-NEGOTIABLE) — record real gcloud outputs once, replay offline → reproducible demo/eval without live GCP.
- **Eval harness** — run LaunchGuard against 8–10 intentionally-misconfigured repo fixtures; report precision/recall + a scorecard (JSON + Markdown).
- **Guardrails** — read-only on cloud (mutating gcloud calls rejected + logged), changes only via PR, secret redaction before Gemini, per-agent tool allow-listing, one demo blocked-write trip, full audit trail in the `adk web` trace.
- **Per-project gotcha memory** (thin) — pgvector-backed recall of past findings for the same project ("same SA-secret gap as last deploy"); 1–2 recall moments.
- **Demo surface** — `adk web` trace as primary demo; hero trace = SECRET_FOO killer + blocked-write guardrail + scorecard.

### Out of scope

- **Any live GCP mutation** (IAM grants applied, secrets written, services replaced) — hard constraint, never.
- **Auto-merge of fix-PRs** — human is always the merge gate.
- **Non-Cloud-Run targets** (GKE, App Engine, Lambda, raw VMs).
- **Non-FastAPI / non-Python deep app analysis** — Dockerfile/config-level checks are generic, but deep entrypoint inference is tuned for the FastAPI+uvicorn shape.
- **A full web dashboard / SaaS UI** — `adk web` trace is the demo surface; no bespoke frontend.
- **Canary verification (`gcloud run deploy --no-traffic`)** — stretch only (Day 9), first to cut.
- **Multi-cloud, cost forecasting beyond simple scaling/concurrency flags.**

## 5. Feature List

```
Feature: RepoAuditor — Intended contract inference
Priority: P0
Description: Parse a target repo into a normalized "intended contract" (PORT/host, entrypoint, env vars, secret refs, health/startup probe, pinned base image).
Acceptance Criteria:
  - Given a repo with a Dockerfile setting ENV PORT=8080 and EXPOSE 8080, When RepoAuditor runs, Then the intended contract records port=8080 and a port-source of "dockerfile".
  - Given a Dockerfile whose CMD is shell-form ("CMD npm run start"), When RepoAuditor runs, Then the contract flags pid1_signal_safe=false with evidence (the offending line).
  - Given app code / service.yaml referencing a secret name (e.g. SECRET_FOO via secretKeyRef or os.environ), When RepoAuditor runs, Then SECRET_FOO appears in intended.secret_refs with its source location.
  - Given an ambiguous entrypoint that deterministic parsing cannot resolve, When RepoAuditor runs, Then it escalates the single ambiguous field to Gemini and records confidence < 1.0 (never silently guesses).
```

```
Feature: GcpStateInspector — Live GCP read + fixture replay
Priority: P0
Description: Read live GCP state read-only (runtime SA IAM, enabled APIs, Secret Manager secrets + accessor grants, existing Run config) OR replay from a golden-JSON fixture.
Acceptance Criteria:
  - Given LAUNCHGUARD_MODE=fixture and a recorded snapshot for a project, When GcpStateInspector runs, Then it returns the live-state shape from the fixture with zero network calls.
  - Given LAUNCHGUARD_MODE=live, When GcpStateInspector runs, Then every gcloud invocation is a read/list/describe verb and any mutating verb is rejected before execution and logged.
  - Given a live read in record mode, When GcpStateInspector runs, Then the raw gcloud output is persisted as a golden-JSON fixture (redacted) for later offline replay.
  - Given the same fixture replayed twice, When GcpStateInspector runs, Then the returned live-state is byte-identical (determinism precondition for eval).
```

```
Feature: Declared parser — deploy-config normalization
Priority: P0
Description: Parse the deploy declaration (Cloud Run service.yaml, cloudbuild.yaml, deploy workflow) into a normalized declared-state shape.
Acceptance Criteria:
  - Given an infra/cloud-run/service.yaml with containerPort 8080 and N secretKeyRef entries, When the declared parser runs, Then declared.port=8080 and declared.secret_refs lists all N secret names.
  - Given a service.yaml with autoscaling minScale/maxScale and containerConcurrency annotations, When the declared parser runs, Then those values appear in declared.scaling.
  - Given a service.yaml using ${ENV} substitution placeholders, When the declared parser runs, Then it normalizes placeholders without crashing and marks unresolved values as templated.
```

```
Feature: Reconciler — three-source diff + classification (CORE)
Priority: P0
Description: Diff Intended ⟷ Declared ⟷ Live; classify each delta as will-fail / will-misbehave / cost-risk with confidence + evidence; deterministic detector rules, Gemini only for ambiguity/explanation.
Acceptance Criteria:
  - Given code requires SECRET_FOO and service.yaml declares it but the runtime SA has no secretAccessor grant on SECRET_FOO in live state, When the Reconciler runs, Then it emits a will-fail delta "secret-ref-without-secretAccessor" naming SECRET_FOO, the SA, and the missing role, with high confidence. [KILLER]
  - Given intended.port=8080 but declared.containerPort=3000, When the Reconciler runs, Then it emits a will-misbehave delta "port-mismatch" with both values as evidence.
  - Given service.yaml has no liveness/startup probe but the intended contract expects /health, When the Reconciler runs, Then it emits a will-misbehave delta "missing-health-probe".
  - Given the runtime SA holds roles/owner or roles/editor, When the Reconciler runs, Then it emits a will-misbehave (security) delta "over-broad-sa-role" recommending least-privilege roles.
  - Given minScale=0 with a slow cold start and high maxScale, When the Reconciler runs, Then it emits a cost-risk delta (advisory, not blocking).
  - Given a deterministic rule does not fire and inputs are ambiguous, When the Reconciler runs, Then Gemini classifies the ambiguity and the delta is labeled "needs-review" (never confidently "will-fail").
```

```
Feature: FixWriter — diffs, readiness scorecard, fix-PR
Priority: P0
Description: Generate unified diffs (IAM grant commands, Dockerfile/service.yaml edits) + a readiness scorecard, and open a fix-PR via GitHub MCP. Never mutates live GCP; never auto-commits to main.
Acceptance Criteria:
  - Given a will-fail secret-accessor delta, When FixWriter runs, Then it produces a proposed patch containing the exact `gcloud secrets add-iam-policy-binding` command (or service.yaml/Terraform edit) needed and includes it in the PR body — it does NOT execute it.
  - Given a set of classified deltas, When FixWriter runs, Then it emits a readiness scorecard (JSON + Markdown) with counts per class and an overall verdict (BLOCKED / WARN / READY).
  - Given open_pr is invoked, When the PR is created, Then the agent stops and surfaces the PR URL for human review — it never merges.
  - Given LaunchGuard runs against a target repo, When FixWriter completes, Then no commit is pushed to the target's main branch and no live GCP resource is changed (assertable in fixture mode).
```

```
Feature: Golden-JSON fixture layer (NON-NEGOTIABLE)
Priority: P0
Description: Record real gcloud outputs once (redacted), replay offline so the full pipeline + eval are reproducible without live GCP.
Acceptance Criteria:
  - Given record mode against worknote-ai, When fixtures are captured, Then a golden-JSON snapshot exists under fixtures/gcp/ with all secret values redacted (names/existence only).
  - Given fixture mode, When the full agent pipeline runs, Then it completes end-to-end with zero network calls to GCP.
  - Given a fixture, When replayed N times, Then findings are identical (deterministic).
```

```
Feature: Eval harness + scorecard
Priority: P0
Description: Run LaunchGuard against 8–10 intentionally-misconfigured repo fixtures with ground-truth blocker labels; compute precision/recall; emit a scorecard.
Acceptance Criteria:
  - Given 8–10 fixture repos each with a ground-truth labels file, When run_eval runs, Then it reports per-fixture detected vs expected blockers and aggregate precision/recall.
  - Given the hero fixture (secret/IAM gap), When the eval runs, Then the SECRET_FOO will-fail delta is detected (true positive).
  - Given the eval completes, When the scorecard is written, Then a headline metric ("caught X/Y blockers, opened Z fix-PRs") is present in JSON and Markdown.
```

```
Feature: Guardrails + audit trail
Priority: P0
Description: Read-only cloud enforcement, changes-only-via-PR, secret redaction before Gemini, per-agent tool allow-listing, one demo blocked-write trip, full audit trail in the adk web trace.
Acceptance Criteria:
  - Given any agent attempts a mutating gcloud verb, When the guardrail layer evaluates it, Then the call is rejected before execution and the attempt is logged as a guardrail trip.
  - Given any payload bound for Gemini, When it passes the redaction layer, Then secret values / tokens / connection strings / PII are masked (names and existence preserved).
  - Given a sub-agent invokes a tool outside its allow-list, When the guardrail evaluates it, Then the call is denied and logged.
  - Given the demo scenario, When the agent intentionally attempts a write, Then exactly one blocked-write trip is visible in the trace.
  - Given any agent step, When it executes, Then the step (tool, input/output, reasoning, classification) is logged and visible in the adk web trace; every finding carries evidence + confidence.
```

```
Feature: Per-project gotcha memory (thin)
Priority: P2
Description: pgvector-backed recall of prior findings for the same project so a repeat gap is surfaced as "seen before".
Acceptance Criteria:
  - Given a project was previously analyzed and a finding stored, When the same project is analyzed again with the same gap present, Then the finding is annotated "recurring — seen on <prior date>".
  - Given no prior history for a project, When it is analyzed, Then the run proceeds normally with no recall annotation (memory is additive, never blocking).
```

## 6. User Flow

1. Engineer points LaunchGuard at a target (repo path + GCP project/service, or a fixture name).
2. **Orchestrator** plans the run and delegates.
3. **RepoAuditor** infers the Intended contract from the repo (deterministic parse; Gemini only for ambiguity).
4. **GcpStateInspector** reads Live GCP state read-only (or replays a fixture).
5. The Declared parser normalizes the deploy config.
6. **Reconciler** diffs the three sources and classifies each delta (will-fail / will-misbehave / cost-risk) with confidence + evidence.
7. **FixWriter** generates diffs + a readiness scorecard and opens a fix-PR (human gate). Guardrails redact secrets, enforce read-only, and log every step.
8. Engineer reviews the `adk web` trace + scorecard, opens/reviews the PR, and merges if satisfied. LaunchGuard never applies anything itself.
9. (Optional) On a repeat run, memory surfaces "recurring gap" annotations.

## 7. Development Tasks (High-Level)

| Task | Description | Dependencies |
|---|---|---|
| ADK skeleton | Orchestrator + sub-agent scaffolding, session-state handoff | — |
| RepoAuditor | Intended-contract parsers + ambiguity escalation | ADK skeleton |
| Declared parser | service.yaml / cloudbuild / workflow normalization | ADK skeleton |
| GcpStateInspector | gcloud read tools + fixture replay/record | ADK skeleton |
| Fixture layer | Golden-JSON capture + redaction + replay | GcpStateInspector |
| Reconciler | Three-source diff + detector rules + classification | RepoAuditor, Declared parser, GcpStateInspector |
| FixWriter | Diff generation + scorecard + open_pr (GitHub MCP) | Reconciler |
| Guardrails | Read-only enforcement, redaction, allow-list, audit | all tool layers |
| Eval harness | Misconfigured fixtures + run_eval + scorecard | Reconciler, fixture layer |
| Memory | pgvector per-project recall | Reconciler |
| Demo polish | adk web hero trace + video | all |

> Agent assignments + ordering are owned by **system-architect** (`specs/tasks/implementation-tasks.json`).

## 8. Risks & Implementation Notes

- **Framing collapse risk:** if positioned as an "AI linter" the differentiation collapses onto Checkov + GCP Security Review. The three-source delta framing is mandatory in all copy and the writeup.
- **Fixture layer is the keystone:** if golden-JSON replay slips, the headline feature is not reproducible for the jury and the demo is flaky. It is NON-NEGOTIABLE (Day 3). Fallback project ("Pacijent") only if it truly slips.
- **False-positive cost asymmetry:** a wrong IAM/secret recommendation is expensive (an engineer applies it and breaks something). Per AI Operating Principles §8, prefer a false negative over a confident-but-wrong destructive recommendation; low confidence → "needs-review", never "will-fail".
- **Live-GCP access fragility:** auth, quota, and project access make live runs flaky for a solo build on a deadline; fixtures are the primary path, live is the recording source.
- **Scope discipline:** memory (P2) and canary (stretch) are the first to cut. Days 9–10 (video + writeup) are sacred (~half the score).
- **Secret redaction must precede Gemini unconditionally** — a leaked secret value in a model call or trace is a guardrail failure, not a bug.

# Architecture — LaunchGuard

> Master architecture document. Source of truth for project-wide concerns.
> Status: Approved — Gate A (Continue, 2026-06-20)
> Author: system-architect (Phase 1) | Date: 2026-06-20
> Stack: `docs/TECH_STACK.md` | Guardrails: `docs/AI_OPERATING_PRINCIPLES.md` | Handoff: `docs/HANDOFF_PROTOCOL.md`
> Framing (mandatory): **three-source contract reconciliation**. NEVER "AI Dockerfile/IAM linter".

---

## 1. System Overview

LaunchGuard is a **multi-agent ADK application** (no HTTP API surface in the deliverable — `adk web` is the demo surface). It ingests a target (repo path + GCP project/service, or a fixture name) and runs a fixed pipeline that produces three normalized state objects, diffs them, classifies deltas, and opens a fix-PR.

```
                         ┌──────────────────────────────┐
                         │  Orchestrator (root agent)    │
                         │  plans run, delegates, gates  │
                         └───────────────┬───────────────┘
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                 ▼                                 ▼
┌───────────────┐              ┌────────────────────┐            ┌──────────────────┐
│ RepoAuditor   │              │ GcpStateInspector  │            │ Declared parser  │
│ → Intended    │              │ → Live (read-only  │            │ → Declared       │
│   contract    │              │   or fixture)      │            │   (service.yaml) │
└───────┬───────┘              └─────────┬──────────┘            └────────┬─────────┘
        └──────────────────────┬─────────┴────────────────────────────────┘
                               ▼
                    ┌────────────────────────┐
                    │ Reconciler (CORE IP)    │  diff 3 sources → classified deltas
                    │ deterministic rules +   │  (will-fail/will-misbehave/cost-risk)
                    │ Gemini for ambiguity    │  + confidence + evidence
                    └───────────┬─────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ FixWriter               │  diffs + readiness scorecard
                    │ propose_patch, open_pr  │  → fix-PR (GitHub MCP, human gate)
                    └────────────────────────┘

  Cross-cutting: Guardrails (read-only enforce, redaction, allow-list, audit) wrap every tool.
                 Memory (pgvector) annotates findings with prior occurrences.
```

**Communication mechanism:** ADK **session state** (not files) for inter-agent handoff — adapts the dev-agents file-based handoff pattern to ADK. Each sub-agent reads its input slice from session state and writes its output slice back.

## 2. Architecture (components, boundaries, patterns)

| Component | Responsibility | Boundary |
|---|---|---|
| **Orchestrator** | sequence the pipeline, decide next step, present results | delegation only; no parsing/IO |
| **RepoAuditor** (sub-agent) | repo → `IntendedContract` | repo-read tools only |
| **GcpStateInspector** (sub-agent) | live/fixture → `LiveState` | `gcloud_read*` + fixture replay only |
| **Declared parser** (tool/module under Orchestrator) | deploy config → `DeclaredState` | file parse only |
| **Reconciler** (sub-agent) | 3 states → `ReconciliationDelta[]` | no external tools; logic + model |
| **FixWriter** (sub-agent) | deltas → diffs + scorecard + PR | `propose_patch`, `open_pr` only |
| **Guardrails** (module wrapping tools) | enforce read-only, redact, allow-list, audit | enforcement seam |
| **Memory** (module) | per-project recall | pgvector read/write |

**Patterns adopted from dev-agents framework:** Gate (human approval) → FixWriter PR gate; contract-first → tool I/O JSON schemas in `api-contracts.yaml`; file-based handoff → ADK session state.

**Key design principle — deterministic core, model at the edges:** detection is deterministic (parse + rules); Gemini 2.5 Pro is used only for (a) classifying genuine ambiguity and (b) generating human-readable explanations/diffs; Gemini 2.5 Flash for parsing the ambiguous residue. This is what makes findings reproducible (AI Operating Principles §6) and is a precondition for the eval scorecard.

## 3. Backend Services

There is no traditional web backend. The "services" are:
- **launchguard/ ADK package** — `agent.py` (Orchestrator), `sub_agents/`, `reconciler/`, `tools/`, `guardrails/`, `memory/`.
- **PostgreSQL 16 + pgvector** — per-project gotcha memory + eval substrate. **NEW_SERVICE** (see flag below): requires `docker-compose.dev.yml`.
- **gcloud-mcp** (read-only GCP) + **GitHub MCP** (PR) — external MCP servers, interop story for Day-2 rubric.

## 4. API Structure (summary — details in contract)

No HTTP endpoints. `specs/contracts/api-contracts.yaml` instead documents, as OpenAPI 3.1 component schemas + tool operation objects:
- **Tool contracts** (input/output JSON schema per tool): `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code`, `gcloud_read`, `fixture_replay`, `propose_patch`, `open_pr`.
- **Core data shapes:** `IntendedContract`, `DeclaredState`, `LiveState`, `ReconciliationDelta`, `ReadinessScorecard`.
- See ADR-002 for why the contract documents tools/shapes rather than HTTP paths.

## 5. Database Design

`launchguard_memory` (Postgres 16 + pgvector):

```
table project_finding (
  id            uuid pk,
  project_id    text not null,        -- GCP project or repo identity
  rule_id       text not null,        -- e.g. secret-ref-without-secretAccessor
  delta_class   text not null,        -- will-fail | will-misbehave | cost-risk
  summary       text not null,        -- REDACTED human summary (no secret values)
  embedding     vector(768),          -- gemini text-embedding; for recall
  first_seen_at timestamptz not null,
  last_seen_at  timestamptz not null
)
index on project_id;  ivfflat/hnsw index on embedding;
```
Recall = cosine similarity scoped to `project_id`. Summaries are redacted before persistence (AI Operating Principles §3). Memory is additive: a missing/empty table never blocks a run.

## 6. Security Considerations

- **Read-only on cloud (hard):** `gcloud_read` enforces a verb allow-list (`describe`/`list`/`get-iam-policy` etc.); mutating verbs are rejected pre-exec and logged (AI Operating Principles §1). Tested assertably in fixture mode.
- **Changes only via PR (§2):** FixWriter `open_pr` is the only write path, and it targets a branch/PR — never main, never live GCP.
- **Secret redaction before Gemini (§3):** a mandatory redaction pass on every model-bound payload AND every trace/log line. Fixtures are redacted at capture time.
- **Tool allow-listing per agent (§4):** least privilege, enforced by the guardrail module, not by prompt.
- **Untrusted input (§5):** repo/log/GCP content treated as data; Reconciler trusts only schema-validated inputs.
- **Fail-safe (§8):** low confidence → `needs-review`, never confident `will-fail`; prefer false negative over destructive false positive.

## 7. Handoff Notes

**Backend Engineer:**
- Owns: `tools/` (deterministic parsers `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code`; `gcloud_read` shell wrapper + gcloud-mcp interop; `fixture_replay`/record; `propose_patch`, `open_pr` GitHub MCP wrapper), the Declared parser, the `guardrails/` enforcement module, the `memory/` pgvector layer, and the Reconciler **rule engine** (deterministic detector rules + classification skeleton).
- Implement each tool's I/O exactly per `specs/contracts/api-contracts.yaml` schemas (`IntendedContract`, `DeclaredState`, `LiveState`, `ReconciliationDelta`, `ReadinessScorecard`). No tool may deviate from the contract without architect revision.
- `gcloud_read` MUST check the verb allow-list before exec — this is the read-only enforcement seam.
- pgvector connection config + `docker-compose.dev.yml` come from devops-local (NEW_SERVICE flagged).

**LLM Engineer (runs BEFORE backend in Pattern 2):**
- Owns: the ADK agent definitions (`agent.py` Orchestrator + `sub_agents/`), Gemini model wiring (2.5 Pro reasoning / 2.5 Flash parsing), prompt design for (a) ambiguity classification in RepoAuditor + Reconciler and (b) explanation/diff generation in FixWriter, and the eval prompts.
- **LLM gateway:** this project uses **native google-genai via ADK**, NOT LiteLLM (ADR-001). That is the canonical gateway here — use ADK's model abstraction; do not import provider SDKs ad hoc in business logic.
- Enforce AI Operating Principles: structured/JSON output for every model call; user/untrusted content in user-role only; redaction layer runs before any model call; confidence emitted on every model-classified delta.
- Do NOT write tools or DB models (backend owns those) or routes (none exist).

**Code Reviewer:**
- Validate: read-only enforcement actually rejects mutating verbs (not just documented); redaction runs before every Gemini call AND on trace output; per-agent allow-list enforced in code; Reconciler has no external tools; FixWriter never executes a gcloud command or pushes to main.
- Validate tool I/O against `api-contracts.yaml` schemas; deterministic rules produce evidence + confidence per delta.

**QA Testing Engineer:**
- Critical scenarios: the SECRET_FOO killer (will-fail, hero), each detector rule against its fixture, fixture-mode determinism (replay twice → identical), guardrail trips (mutating verb rejected; allow-list violation rejected; one demo blocked-write), redaction (no secret value in any fixture/trace/model payload), eval precision/recall over the 8–10 fixtures incl. the clean-repo true-negative control.
- This is a CLI/agent + eval project: the **eval harness is the E2E surface**. "E2E coverage" for a flow-changing feature = the eval run is green over its fixtures.

**DevOps (local):**
- NEW_SERVICE: postgres-pgvector → `docker-compose.dev.yml` + connection env. Bootstrap `scripts/local-ci.sh` + `Makefile` `verify` (lint + type + pytest) — none exist yet.
- Env/secrets: `GOOGLE_API_KEY` (Gemini), `GITHUB_TOKEN` (GitHub MCP / PR), GCP ADC for live mode, `DATABASE_URL` (pgvector). All in `venv/.env` (gitignored). `.env.example` updated, no values.

## 8. NEW_SERVICE flag

```
NEW_SERVICE: postgres-pgvector
Reason: per-project gotcha memory + eval substrate (TECH_STACK §Memorija, §Fixtures&Eval)
Impact: backend requires DATABASE_URL connection config + docker-compose.dev.yml entry (pgvector image)
```

## 9. Per-feature designs

None at Phase 1 (no feature exceeds the 500-line master-inflation threshold). If the Reconciler rule engine grows, a `specs/architecture/reconciler-design.md` may be added later (would emit `NEW_PATTERN`).

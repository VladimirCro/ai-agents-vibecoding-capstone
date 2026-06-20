# Deployment Requirements: LaunchGuard

> Status: Approved — Gate A (Continue, 2026-06-20)
> Presented at Gate A so the lead can decide build/demo scope before Phase 2.

## Current State

LaunchGuard is a Google ADK agent + eval harness, NOT a deployed web service. "Deployment" here means: **runnable for the demo + eval**, not a hosted service. The primary execution path is **offline via golden-JSON fixtures**; live GCP is the recording source only.

- **GCP access:** stubbed by default (fixture mode). Live mode is opt-in for recording fixtures.
- **GitHub PR:** GitHub MCP path; can be exercised against a throwaway repo or mocked in tests.
- **Memory (pgvector):** local docker-compose only; not required for the core demo.
- **Local-CI infra:** does NOT exist yet (`scripts/local-ci.sh`, `Makefile verify` missing) — devops-local bootstraps it (INFRA-01).

## Must Have (before any demo/eval run)

- [ ] **Local-CI infra** — missing → `scripts/local-ci.sh` + `Makefile verify` (ruff + type + pytest) [INFRA-01].
- [ ] **Golden-JSON fixtures** — none → recorded redacted snapshots under `fixtures/gcp/` (NON-NEGOTIABLE) [BE-04].
- [ ] **GOOGLE_API_KEY** (Gemini) — required for any model call; in `venv/.env` (gitignored).
- [ ] **pgvector docker-compose** — missing → `docker-compose.dev.yml` postgres:16+pgvector [INFRA-01] (needed for memory + eval substrate; core reconciliation runs without it).

## Should Have (before production / full rubric)

- [ ] **GITHUB_TOKEN** — for GitHub MCP `open_pr` against a real target repo (demo can use a throwaway repo).
- [ ] **GCP ADC (application-default credentials)** — only for `live`/`record` mode against worknote-ai to capture fixtures; never needed for the offline demo.
- [ ] **gcloud-mcp server** — for the MCP-interop story (Day-2 rubric); shell-wrapped `gcloud` is the fallback.
- [ ] **DATABASE_URL** — for the pgvector memory layer.

## Infrastructure Notes

| Item | Type | Where | Notes |
|---|---|---|---|
| `GOOGLE_API_KEY` | secret | `venv/.env` | Gemini 2.5 Pro/Flash via google-genai/ADK |
| `GITHUB_TOKEN` | secret | `venv/.env` | GitHub MCP / `open_pr`; least-privilege repo scope |
| GCP ADC | credential | local gcloud | live/record mode only; read-only usage |
| `DATABASE_URL` | config | `venv/.env` | `postgresql://...` to pgvector container |
| pgvector | service | `docker-compose.dev.yml` | postgres:16 + pgvector extension (NEW_SERVICE) |
| gcloud-mcp | external MCP | local | read-only GCP; fallback = shell gcloud |
| GitHub MCP | external MCP | local | PR creation; human merge gate |

**Hard guardrail (deployment-relevant):** the agent never mutates live GCP and never auto-commits to a default branch. In fixture mode this is assertable in tests (zero network, zero mutation). No secret values appear in committed fixtures, logs, traces, or model payloads (redaction at capture + pre-model).

**Capstone submission boundary:** the `dev-agents` framework (`.claude/`, most of `docs/`) is gitignored tooling and is NOT part of the submission. Deliverable = `launchguard/` + `fixtures/` + `eval/`.

# LaunchGuard — Cloud Run deploy-readiness agent

Autonoman agent (Google ADK + Gemini) koji **prije deploya na Cloud Run pomiruje tri izvora
istine** — (1) što kod traži, (2) što deploy deklarira, (3) što GCP stvarno daje — i hvata
"silent-misbehave" klasu (deploy prolazi, app puca na prvom requestu). Otvara fix-PR
(human-in-the-loop), nikad ne mijenja live. Za backend/SRE inženjere koji shippaju FastAPI
servise na Cloud Run. Capstone za Google × Kaggle "AI Agents: Intensive Vibe Coding"
(trač *Agents for Business*).

## Stack

**Google ADK + Gemini 2.5** (agent) · **gcloud (read-only) + GitHub** (toolovi) · **FastAPI/Postgres+pgvector** (memorija/eval substrate) · **pytest** (eval harness)
> Detalji: `docs/TECH_STACK.md`. ADK+Gemini native (LiteLLM nije mandatory — vidi override u TECH_STACK).

## Repo Layout

```
├── launchguard/      # ADK agent: agent.py, sub_agents/, tools/, reconciler/, memory/, guardrails/
├── fixtures/         # golden-JSON gcloud snapshots + namjerno misconfigured repo fixtures (eval substrate)
├── eval/             # eval harness: scenarios.yaml, run_eval.py, scorecard/
├── docs/             # TECH_STACK, handoff protocol, templates
├── specs/            # agent artefakti (PRD, architecture, contracts, tasks...)
├── .claude/          # dev-agents framework (agents, commands, rules, skills)
├── venv/             # izolirani env (gitignoran); .env s ključevima
├── PLAN.md           # plan + 10-dnevni raspored
└── CLAUDE.md         # ovaj fajl
```

## Conventions

- **Deliverable ≠ tool:** `dev-agents` framework (`.claude/`, većina `docs/`) je razvojni alat,
  gitignoran, NE ide u capstone submission. Deliverable je `launchguard/` + `fixtures/` + `eval/`.
- **Framing je obvezan:** "tri-source contract reconciliation". NIKAD "AI Dockerfile/IAM linter"
  (inače collapse na Checkov i diferencijacija pada).
- **Tajne:** API ključevi samo u `venv/.env` (gitignoran). Nikad u kod ni commit.
- **Guardraili su srž:** agent je READ-ONLY na cloudu; IAM/secret promjene SAMO kao PR; secret
  redakcija prije Gemini-ja; tool allow-list. Nikad ne mutira live GCP.
- **Hero target:** user-ov live Cloud Run servis `worknote-ai` (~/repos/github/private/worknote-ai)
  — demo na stvarnom servisu. Fixtures za offline reproducibilnost.
- **Reuse:** detektore posuditi iz `~/repos/github/private/cloud-run-prep-agents/playbook/CLOUD_RUN_DEPLOYMENT_PLAYBOOK.md`.
- **Pobjednički kod → CC-BY 4.0:** ako pobijedimo, repo se objavljuje. Ne uvlači privatni kod.
- **Language:** Croatian (komunikacija) / English (kod, tehnički termini, identifikatori).

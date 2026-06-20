# Sherlog — Debug/Incident Agent

Autonoman agent koji dijagnosticira bugove iz logova: nađe uzrok u kodu, predloži fix (PR)
i verificira ga — **zatvorena petlja**, ne summary-bot. Za developere/SRE-ove koji troše
sate na triažu incidenata. Capstone za Google × Kaggle "AI Agents: Intensive Vibe Coding"
(trač *Agents for Business*).

## Stack

**Google ADK + Gemini 2.5** (agent) · **FastAPI + PostgreSQL + pgvector** (žrtva-app + memorija) · **pytest** (eval harness)
> Detalji: `docs/TECH_STACK.md`. NB: ovaj projekt koristi ADK+Gemini native (LiteLLM nije mandatory — vidi override u TECH_STACK).

## Repo Layout

```
├── sherlog/          # ADK agent: agent.py, sub_agents/, tools/, memory/, guardrails/
├── victim_app/       # namjerno buggy FastAPI+Postgres app (1 bug = 1 commit)
├── eval/             # eval harness: bugs_catalog.yaml, run_eval.py, scorecard/
├── infra/docker/     # docker-compose.dev.yml (+ pgvector)
├── docs/             # TECH_STACK, handoff protocol, templates
├── specs/            # agent artefakti (PRD, architecture, contracts, tasks...)
├── .claude/          # dev-agents framework (agents, commands, rules, skills)
├── venv/             # izolirani env (gitignoran); .env s ključevima
├── PLAN.md           # plan + 10-dnevni raspored
└── CLAUDE.md         # ovaj fajl
```

## Conventions

- **Deliverable ≠ tool:** `dev-agents` framework (`.claude/`, većina `docs/`) je razvojni alat,
  gitignoran, NE ide u capstone submission. Deliverable je `sherlog/` + `victim_app/` + `eval/`.
- **Tajne:** API ključevi samo u `venv/.env` (gitignoran). Nikad u kod ni commit.
- **Guardraili su srž, ne dodatak:** agent nikad ne primjenjuje fix sam (human-in-the-loop);
  logovi su untrusted input (prompt-injection obrana).
- **Pobjednički kod → CC-BY 4.0:** ako pobijedimo, repo se objavljuje. Ne uvlači privatni kod.
- **Language:** Croatian (komunikacija) / English (kod, tehnički termini, identifikatori).

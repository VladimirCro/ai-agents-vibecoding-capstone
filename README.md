# LaunchGuard — Cloud Run deploy-readiness agent

> Autonoman AI agent (Google ADK + Gemini) koji **prije deploya na Cloud Run pomiruje tri izvora
> istine** i hvata misconfiguracije koje uzrokuju *"deploy uspije, ali aplikacija pukne na prvom
> requestu"*.

Capstone za **Google × Kaggle: AI Agents: Intensive Vibe Coding** · trač **Agents for Business**.

## Problem

Cloud Run deploy može proći zeleno, a aplikacija svejedno pukne na prvom requestu — jer
**nitko ne vidi sva tri izvora istine istovremeno**:

| Izvor | Što zna | Što NE zna |
|---|---|---|
| Linter (Checkov…) | repo | live GCP, deploy config |
| GCP Security Review | live GCP | repo namjeru |
| `gcloud deploy` | deklaraciju | repo namjeru, live grantove |

Klasičan primjer: kod traži `SECRET_FOO`, deploy ga deklarira (`secretKeyRef`), ali runtime
service account **nema `secretAccessor` grant** → deploy prolazi, prvi request je 500.

## Rješenje — tri-source contract reconciliation

```
TARGET (Cloud Run servis)
   │
   ├─ RepoAuditor        → INTENDED  (Dockerfile $PORT, env, secret refs, health probe)
   ├─ GcpStateInspector  → LIVE      (SA IAM, enabled APIs, Secret Manager grants, run config)  [read-only]
   ├─ Declared parser    → DECLARED  (service.yaml, cloudbuild.yaml, deploy workflow)
   │
   └─ Reconciler (core)  → DELTA klasificiran: will-fail / will-misbehave / cost-risk
         └─ FixWriter    → konkretan fix (diff / gcloud cmd) + readiness scorecard + PR  [human-gate]
```

**Diferencijator:** vrijednost je u **delti između tri izvora** — nešto što nijedan postojeći
alat ne računa.

## Agent design (gradivo tečaja)

- **Multi-agent (ADK):** Orchestrator + RepoAuditor + GcpStateInspector + Reconciler + FixWriter, handoff preko ADK session state
- **Model na rubovima:** deterministički parsing + detektorska pravila; Gemini samo za ambiguitet → reproducibilan eval
- **Memorija (pgvector):** per-project gotcha recall ("isti SA-secret gap kao prošli put")
- **MCP interop:** gcloud-mcp (GCP read) + GitHub MCP (PR)
- **Guardraili (srž):** read-only na cloudu · IAM/secret promjene samo kao PR · secret redakcija prije modela · per-agent tool allow-list · fail-safe (nesigurno → `needs-review`, nikad confident `will-fail`)
- **Observability:** `adk web` trace svakog koraka

## Rezultati (eval)

Eval nad **9 fixtura** (8 misconfigured kategorija + clean-control true-negative):

| Metrika | Vrijednost |
|---|---|
| Blocker precision / recall / F1 | **1.00 / 1.00 / 1.00** |
| False positives na clean-controlu | **0** |
| Warning coverage | 8/8 |
| Testovi (`make verify`) | **247 prošlo** (ruff + mypy + pytest) |

Killer detektor `secret-ref-without-secretAccessor` na hero fixtureu (modeliran po stvarnom
`worknote-ai` servisu): verdict **BLOCKED**, confidence 0.98.

## Struktura

```
launchguard/   # ADK agent: agent.py, sub_agents/, tools/, reconciler/, memory/, guardrails/
fixtures/      # golden-JSON GCP snapshots + 9 misconfigured repo fixtures (eval substrate)
eval/          # run_eval.py + scorecard/ (precision/recall/F1) + pr_preview/
tests/         # 247 testova
infra/docker/  # docker-compose.dev.yml (postgres:16 + pgvector)
specs/         # PRD, FRD-ovi, arhitektura, contracts, ADR-ovi (dev-agents Faza 1)
PLAN.md · docs/TECH_STACK.md · docs/AI_OPERATING_PRINCIPLES.md
```

## Quick start (offline, fixture mode — bez GCP-a)

```bash
source venv/bin/activate
make verify          # ruff + mypy + 247 testova
python eval/run_eval.py   # generira eval/scorecard/scorecard.md
```

## Live mode

Vidi **`NETWORK_PASS.md`** za korake na mašini s mrežom (ADK install, gcloud record, `adk web`).

---

### Capstone meta

Hackathon (nema dataseta), ocjenjuje Google po rubrici, 1 submission. Rok **2026-07-06 23:59 PT**.
Pobjednički kod → **CC-BY 4.0**. Tajne samo u `venv/.env` (gitignoran).
[Competition](https://www.kaggle.com/competitions/vibecoding-agents-capstone-project)

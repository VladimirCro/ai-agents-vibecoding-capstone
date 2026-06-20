# Tech Stack — LaunchGuard (Cloud Run deploy-readiness agent)

> **Jedini autoritativni izvor tech stacka za ovaj projekt.**
> Svi agenti referenciraju ovaj fajl. Ne dupliciraj sadržaj u agent promptovima.

Zadnje ažuriranje: 2026-06-20 | Verzija: capstone-2.0.0 (LaunchGuard)

**Projekt:** LaunchGuard — autonoman agent koji prije deploya na Cloud Run pomiruje TRI izvora
istine i hvata misconfiguracije koje uzrokuju "deploy uspije, app puca na prvom requestu".
**Kontekst:** Google × Kaggle "AI Agents: Intensive Vibe Coding" capstone, trač *Agents for Business*.

> **Override vs canonical:** ovaj projekt NE koristi LiteLLM-as-mandatory. Agent framework je
> **Google ADK** s **nativnim Gemini** modelom (capstone nagrađuje gradivo tečaja). ADK `LiteLlm`
> wrapper ostaje opcija, nije default.

> **FRAMING (obvezno):** "tri-source contract reconciliation". NIKAD "AI Dockerfile/IAM linter" —
> inače se diferencijacija sruši na Checkov + GCP Security Review.

---

## Srž: Tri-source reconciliation

| # | Izvor | Što čitamo | Odakle (primjer: worknote-ai) |
|---|---|---|---|
| 1 | **Intended** (što kod traži) | PORT/$PORT binding, entrypoint, env varovi, secret refs, health/startup probe | Dockerfile, app kod, `.env.example` |
| 2 | **Declared** (što deploy kaže) | Cloud Run service spec, scaling, ingress, IAM u deploy configu | `infra/cloud-run/service.yaml`, `cloudbuild.yaml`, `.github/workflows/deploy.yml` |
| 3 | **Live** (što GCP daje) | SA IAM bindings, enabled APIs, Secret Manager secrets + accessor grantovi, postojeći Run config | gcloud (read-only) / fixtures |

**Output:** delta klasificiran kao `will-fail` / `will-misbehave` / `cost-risk` + fix-PR.
**Killer detektor:** secret-ref-bez-secretAccessor, PORT mismatch, missing health/startup probe,
over-broad SA role, min/max-instances + concurrency cost flags.

---

## Agent Framework

| Layer | Technology | Notes |
|---|---|---|
| Agent framework | **Google ADK** (`google-adk`) | Multi-agent, sessions, tools, tracing, `adk web` dev UI |
| Model SDK | **google-genai** | ADK koristi interno |
| Model (reasoning) | **Gemini 2.5 Pro** | Reconciler klasifikacija, fix generacija, ambiguitet |
| Model (fast) | **Gemini 2.5 Flash** | Parsiranje, ekstrakcija contracta |

### Multi-agent arhitektura

| Agent | Uloga | Toolovi |
|---|---|---|
| **Orchestrator** (root) | Vodi tok, bira sljedeći korak | delegacija |
| **RepoAuditor** | Inferira "intended contract" iz repoa | `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code` |
| **GcpStateInspector** | Čita LIVE GCP (read-only) ili fixture | `gcloud_read` (SA IAM, APIs, secrets+grants, run config) preko gcloud-mcp ili shell; fixture-replay |
| **Reconciler** | Diff tri izvora → klasificirana delta | (interna logika + Gemini za ambiguitet) |
| **FixWriter** | Generira diffove + readiness scorecard, otvori PR | `propose_patch`, `open_pr` (GitHub MCP, human gate) |

**Komunikacija:** ADK session state. **Posuđeni patterni iz dev-agents:** Gate (human approval) →
FixWriter PR gate; contract-first → tool I/O schemas; file-based handoff → session state.

---

## Toolovi

| Tool | Tip | Notes |
|---|---|---|
| `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code` | native ADK | deterministički parsing; Gemini samo za ambiguitet |
| `gcloud_read` (SA IAM, enabled APIs, Secret Manager grants, run config) | native + **gcloud-mcp** | **READ-ONLY**; nikad mutacija |
| `open_pr`, `propose_patch` | **GitHub MCP** | human-in-the-loop gate |
| Fixture replay | native | golden-JSON snapshot → offline reproducibilnost |

> **MCP interop (Day 2 rubric):** gcloud-mcp za GCP state + GitHub MCP za PR → konkretna
> interoperabilnost priča, ne bolt-on.

---

## Fixtures & Eval (diferencijator — nosi bodove)

| Layer | Technology | Notes |
|---|---|---|
| **Golden-JSON fixtures** | snimljeni gcloud outputi | **NON-NEGOTIABLE (Dan 3)** — replay offline, reproducibilno za žiri bez GCP-a |
| Misconfigured repo fixtures | 8-10 namjerno pokvarenih repo-a | ground-truth blockeri; hero = secret/IAM gap |
| Eval runner | pytest | precision/recall: "uhvaćeno X/Y blockera, otvoreno Z fix-PR" |
| Scorecard | JSON + Markdown | headline metrika u writeup |

---

## Guardraili & Sigurnost (Day 4)

| Mehanizam | Implementacija |
|---|---|
| Read-only na cloudu | GcpStateInspector NIKAD ne mutira live IAM/services |
| IAM/secret promjene samo kao PR | FixWriter otvara PR, čovjek odobrava (human-in-the-loop) |
| Secret redakcija | sve prije slanja u Gemini |
| Tool allow-list | eksplicitno po agentu |
| Demo guardrail-trip | jedan namjeran blocked-write u traceu (za video) |
| Audit trail | svaka akcija logirana (observability) |

---

## Memorija (Day 3)

| Layer | Technology | Notes |
|---|---|---|
| Per-project gotcha memorija | pgvector (PostgreSQL) ili ADK memory | "isti SA-secret gap kao prošli deploy" — 1-2 recall momenta dovoljna za rubric |

---

## Demo UI / Observability (Day 5)

| Layer | Technology | Notes |
|---|---|---|
| Primarni demo | **`adk web`** | trace koraka agenta out-of-the-box |
| Hero trace | SECRET_FOO killer + blocked-write guardrail + scorecard | okosnica videa |

---

## Backend / pomoćna infra

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Runtime | Python | 3.12+ | |
| DB (memorija/eval) | PostgreSQL 16+ + pgvector | — | per-project memorija; nije nužan veliki app |
| GCP CLI | google-cloud-sdk (`gcloud`) | — | read-only pozivi; wrap preko gcloud-mcp |
| Testing/eval | pytest + pytest-asyncio | 8+ | eval harness |
| Env | python-dotenv | 1.0+ | `.env` u `venv/.env` (gitignoran) |

---

## Hero target & Reuse

- **Hero target (REAL):** `~/repos/github/private/worknote-ai` — live Cloud Run servis
  (FastAPI+alembic+pytest, `infra/cloud-run/service.yaml`, `infra/gcp/*.sh`, runbooks). Ima sva
  tri izvora → demo na stvarnom servisu + izvor prvog golden fixturea.
- **Reuse detektora:** `~/repos/github/private/cloud-run-prep-agents/playbook/CLOUD_RUN_DEPLOYMENT_PLAYBOOK.md`
  (Cloud Run readiness, IAM rola katalog, PORT/host MUST, secret handling). Pretvoriti u
  detektorska pravila Reconcilera.

---

## Repo Struktura

```
.
├── launchguard/              # ADK agent (deliverable srž)
│   ├── agent.py              # root Orchestrator
│   ├── sub_agents/           # repo_auditor, gcp_state_inspector, fix_writer
│   ├── reconciler/           # tri-source diff + klasifikacija + detektorska pravila
│   ├── tools/                # parse_*, gcloud_read, open_pr, fixture_replay
│   ├── memory/               # per-project gotcha memorija (pgvector)
│   └── guardrails/           # read-only enforcement, secret redakcija, allow-list, approval gate
├── fixtures/
│   ├── gcp/                  # golden-JSON gcloud snapshots
│   └── repos/                # 8-10 namjerno misconfigured repo fixtures
├── eval/                     # scenarios.yaml, run_eval.py, scorecard/
├── infra/docker/             # docker-compose.dev.yml (Postgres+pgvector)
├── venv/                     # izolirani env (gitignoran)
├── PLAN.md
└── README.md
```

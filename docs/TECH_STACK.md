# Tech Stack — Sherlog (Debug/Incident Agent)

> **Jedini autoritativni izvor tech stacka za ovaj projekt.**
> Svi agenti referenciraju ovaj fajl. Ne dupliciraj sadržaj u agent promptovima.

Zadnje ažuriranje: 2026-06-20 | Verzija: capstone-1.0.0

**Projekt:** Sherlog — autonoman agent koji dijagnosticira bugove iz logova, nađe uzrok u
kodu, predloži fix (PR) i verificira ga. Zatvorena petlja, ne summary-bot.
**Kontekst:** Google × Kaggle "AI Agents: Intensive Vibe Coding" capstone, trač *Agents for Business*.

> **Override vs canonical:** ovaj projekt NE koristi LiteLLM-as-mandatory. Agent framework je
> **Google ADK** s **nativnim Gemini** modelom (jer capstone nagrađuje gradivo tečaja).
> ADK `LiteLlm` wrapper ostaje dostupan kao opcija za provider-fleksibilnost, ali nije default.

---

## Agent Framework (srž projekta)

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Agent framework | **Google ADK** (`google-adk`) | latest | Multi-agent, sessions, tools, tracing, `adk web` dev UI |
| Model SDK | **google-genai** (`google-genai`) | latest | ADK koristi interno za Gemini |
| Model (reasoning) | **Gemini 2.5 Pro** | latest dostupna | Orchestrator, hypothesis/root-cause, fix-writing |
| Model (high-volume) | **Gemini 2.5 Flash** | latest dostupna | Log triage, klasifikacija, jeftini koraci |
| Provider flex (opc.) | ADK `LiteLlm` wrapper | — | Samo ako zatreba drugi provider; nije default |

### Model Tiers (kada koji)

| Tier | Model | Koraci |
|---|---|---|
| Heavy reasoning | `gemini-2.5-pro` | Orchestrator planiranje, hypothesis ranking, fix generacija |
| Fast / high-volume | `gemini-2.5-flash` | Log triage, ekstrakcija, klasifikacija anomalija |

---

## Multi-Agent Arhitektura

| Agent | Uloga | Toolovi |
|---|---|---|
| **Orchestrator** (root) | Vodi petlju, bira sljedeći korak, zna stati | delegacija na sub-agente |
| **LogInvestigator** | Povuče logove oko incidenta | `search_logs(query, time_window)` |
| **CodeInvestigator** | Nađe relevantni kod + zadnje promjene | `grep_code`, `read_file`, `git_log`, `git_diff` |
| **Reproducer** | Potvrdi hipotezu (read-only, sandbox) | `http_call`, `db_query` (RO), `run_tests` |
| **FixWriter** | Generira diff + objašnjenje, otvori PR | `propose_patch`, `open_pr` (human gate) |

**Komunikacija među agentima:** ADK **session state** (analog `specs/` file-based handoffa).

---

## Backend — Žrtva-app (namjerno buggy) + Agent runtime

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Framework | FastAPI | 0.115+ | Žrtva-app koju agent debugira; izloži i agent API |
| Runtime | Python | 3.12+ | Modern typing (`X \| Y`) |
| ASGI server | Uvicorn | 0.32+ | |
| Validation | Pydantic v2 | 2.10+ | |
| ORM | SQLAlchemy | 2.0+ async | `AsyncSession` |
| Migrations | Alembic | 1.14+ | Bad-migration bug je jedna eval kategorija |
| PG driver | asyncpg | 0.30+ | |
| HTTP client | httpx | 0.23+ | Reproducer `http_call` tool |
| Logging | structlog | 24+ | **Strukturirani JSON logovi** — hrana za agenta |
| Env | python-dotenv | 1.0+ | `.env` u `venv/.env` (gitignoran) |

---

## Database & Memorija

| Service | Technology | Notes |
|---|---|---|
| Database | PostgreSQL 16+ | Žrtva-app OLTP + audit/error log store |
| Vector / memorija | **pgvector** (ekstenzija) | Incident history embeddings — jedan servis manje od Qdranta |
| Embeddings | Gemini embeddings (`text-embedding-004` ili novija) | Za semantičku sličnost prošlih incidenata |

> **Odluka:** pgvector umjesto zasebnog vector store-a → manje infrastrukture za 10-dnevni sprint,
> a memorija (Day 3) i dalje potpuno demonstrirana.

---

## Eval Harness (diferencijator — nosi bodove)

| Layer | Technology | Notes |
|---|---|---|
| Runner | pytest + pytest-asyncio | Automatiziran eval nad katalogom bugova |
| Bug katalog | `eval/bugs_catalog.yaml` | 15–20 bugova: ground-truth uzrok + fix |
| Metrike | root-cause accuracy, fix correctness, # koraka, tool efficiency | → scorecard u writeup |
| Reporting | JSON + Markdown scorecard | `eval/scorecard/` |

---

## Guardraili & Sigurnost (Day 4 — dio priče, ne dodatak)

| Mehanizam | Implementacija |
|---|---|
| Human-in-the-loop | FixWriter NE primjenjuje fix; otvara PR, čovjek odobrava |
| Read-only repro | Reproducer `db_query` samo SELECT; sandbox za izvršavanje |
| PII redakcija | Logovi se čiste prije slanja u LLM |
| Prompt-injection obrana | Logovi = untrusted input; ne tretiraju se kao instrukcije |
| Tool allow-listing | Eksplicitna lista dozvoljenih toolova po agentu |
| Audit trail | Svaka agent akcija logirana (observability) |

---

## Frontend / Demo UI (minimalan)

| Layer | Technology | Notes |
|---|---|---|
| Primarni demo | **`adk web`** (ADK dev UI) | Prikazuje agent trace/korake out-of-the-box → štedi dane |
| Opcionalni timeline | Streamlit *ili* React+Vite | Samo ako ostane vremena u danu 9 |

> Bez punog React frontenda. Fokus je agent + eval + video, ne UI.

---

## Infrastructure

| Layer | Technology | Notes |
|---|---|---|
| Local dev | docker-compose.dev.yml | PostgreSQL (+ pgvector) za žrtva-app i memoriju |
| Containerization | Docker | Reproducibilan env za eval i demo |
| Deploy (opc.) | Cloud Run | Ako stignemo "production" graduaciju (Day 5); nije nužno za submission |
| Secrets | `venv/.env` lokalno (gitignoran); Secret Manager ako deployamo | `GOOGLE_API_KEY` / Gemini ključ + `KAGGLE_KEY` |

---

## Repo Struktura (Sherlog-specifična)

```
.
├── sherlog/                  # ADK agent (deliverable srž)
│   ├── agent.py              # root Orchestrator
│   ├── sub_agents/           # log_investigator, code_investigator, reproducer, fix_writer
│   ├── tools/                # search_logs, grep_code, git_*, http_call, db_query, run_tests, propose_patch
│   ├── memory/               # incident history (pgvector)
│   └── guardrails/           # PII redakcija, prompt-injection, approval gate
├── victim_app/               # namjerno buggy FastAPI + Postgres app
│   ├── app/
│   ├── bugs/                 # katalog bugova (1 bug = 1 branch/commit)
│   └── tests/
├── eval/                     # eval harness
│   ├── bugs_catalog.yaml
│   ├── run_eval.py
│   └── scorecard/
├── infra/docker/             # docker-compose.dev.yml (+ pgvector)
├── venv/                     # izolirani env (gitignoran); .env s ključevima
├── data/NOTE.md
├── PLAN.md
└── README.md
```

---

## Ključni Patterni (posuđeno iz dev-agents)

1. **Human approval gate** (dev-agents Gate A/B) → FixWriter human-in-the-loop guardrail.
2. **Contract-first** → tool input/output schemas definirani prije implementacije.
3. **File-based handoff** (`specs/`) → ADK session state između sub-agenata.
4. **Provider abstraction** → ADK model sloj; `LiteLlm` wrapper dostupan ako zatreba.

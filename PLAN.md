# PLAN — Debug/Incident Agent ("Sherlog")

Capstone: **Agents for Business** trač. Autonoman agent koji dijagnosticira bugove iz
logova, nađe uzrok u kodu, predloži fix i verificira ga — **zatvorena petlja**, ne summary-bot.

> Radni naziv: **Sherlog** (Sherlock + log). Pamtljiv za video; mijenjamo ako nađemo bolji.

## 1. Što agent radi (zatvorena petlja)

```
ALERT (anomalija u logovima)
   ↓
[1] TRIAGE        → klasificira, planira istragu
[2] LOG EVIDENCE  → povuče logove oko incidenta (tool: log search)
[3] CODE EVIDENCE → nađe relevantni kod + zadnje git diffove (tool: rg, git)
[4] HYPOTHESIS    → rangira uzroke po vjerojatnosti
[5] REPRODUCE     → pozove endpoint / provjeri DB / pokrene test (sandbox, read-only)
[6] FIX           → generira diff + objašnjenje, otvori PR (NE primjenjuje sam)
[7] VERIFY        → re-run testova; potvrdi da fix rješava
```

Korak [5] i [7] su ono što ga čine **agentom, a ne pipeline-om**: skuplja nove dokaze i
ispravlja se na temelju rezultata.

## 2. Arhitektura (multi-agent, Google ADK)

- **Orchestrator** (root agent) — vodi petlju, odlučuje sljedeći korak, zna stati.
- **LogInvestigator** (sub-agent) — alat: `search_logs(query, time_window)`.
- **CodeInvestigator** (sub-agent) — alati: `grep_code`, `read_file`, `git_log`, `git_diff`.
- **Reproducer** (sub-agent) — alati: `http_call`, `db_query` (read-only), `run_tests`.
- **FixWriter** (sub-agent) — alati: `propose_patch`, `open_pr` (human-in-the-loop gate).

> **Teaching:** ne dijeli agente bez razloga. Svaki sub-agent mora "zaraditi mjesto" =
> ima svoj skup toolova i svoju odgovornost. Previše agenata = overhead + gubitak konteksta.
> 4–5 ovdje je opravdano jer su faze istrage stvarno različite.

## 3. "Žrtva" — namjerno buggy demo-app

Mali **FastAPI + Postgres** servis (tvoj stack) s **strukturiranim audit/error logovima**.
Bugovi se ubacuju kontrolirano (jedan bug = jedan git branch/commit) → daje nam **ground
truth** za eval i čist demo.

Kategorije bugova (za raznolikost u eval setu):
- logička greška / off-by-one
- `None`/null dereference
- loša DB migracija / constraint
- N+1 / perf regresija
- race condition / concurrency
- auth/permission bug (npr. nedostaje RLS provjera)
- config/env greška
- bad input validation

## 4. Memorija (Day 3 — context engineering)

**Incident history**: agent pamti prošle incidente i njihova rješenja. Kod novog incidenta
prepozna obrazac: *"ovo liči na incident #4 od prošlog tjedna — isti root cause."*
→ Pohrana u Postgres (embeddings + metadata). Diferencijator i pokazuje curriculum.

## 5. Guardraili + sigurnost (Day 4 — najjači diferencijator)

- **Human-in-the-loop:** agent NIKAD ne primjenjuje fix sam → otvara PR, čovjek odobrava.
- **Read-only po defaultu:** Reproducer ne smije pisati u bazu; sandbox za izvršavanje.
- **PII redakcija:** logovi se čiste prije slanja u LLM.
- **Prompt-injection obrana:** logovi su attacker-influenced podaci → tretiraju se kao
  **untrusted input**. Agent ih ne smije poslušati kao instrukcije.
  > **Teaching:** "lethal trifecta" = untrusted input + pristup alatima + eksfiltracija.
  > Log-čitajući agent ima sve tri → guardrail nije dodatak, nego srž dizajna.
- **Tool allow-listing** + audit trail svake akcije agenta.

## 6. Eval harness (ovo nosi bodove — skoro nitko ne pokaže)

Set od **15–20 kontroliranih bugova** s ground-truth uzrokom i fixom. Mjerimo:
- **Root-cause accuracy** — je li pogodio točan file/line/uzrok
- **Fix correctness** — prolazi li predloženi fix testove
- **MTTR proxy** — broj koraka / tool-callova do rješenja
- **Tool efficiency** — suvišni pozivi

→ Automatiziran (pytest-based) → **scorecard** koji ide direktno u writeup.
> **Teaching:** ne možeš poboljšati što ne mjeriš. Kontrolirani bugovi = ground truth =
> objektivna metrika umjesto "izgleda da radi".

## 7. Observability + demo UI (Day 5)

- **Trace** svakog koraka (koji agent, koji tool, koje rezoniranje).
- Za demo koristimo **ADK dev UI (`adk web`)** koji već prikazuje agent traceove →
  štedi nam dane UI rada. Ako ostane vremena, mali React/Streamlit timeline.

## 8. Tech izbor

| Sloj | Izbor | Zašto |
|---|---|---|
| Agent framework | **Google ADK** | Tečajni alat → žiri to očekuje/nagrađuje; multi-agent + sessions + tracing + dev UI out-of-the-box |
| Model | **Gemini 2.x** | Pravila eksplicitno dopuštaju; native uz ADK |
| Žrtva-app | FastAPI + Postgres | Tvoj stack → brza, autentična izvedba |
| Toolovi | ripgrep, git, httpx, psycopg, pytest | Standard, bez egzotike |
| Eval | pytest + scorecard | Ground-truth metrike |
| Demo UI | `adk web` (+ opc. timeline) | Štedi vrijeme |

## 9. Raspored (~10 radnih dana, rok 2026-07-06 PT)

| Dan | Cilj |
|---|---|
| 1–2 | Scaffolding: žrtva-app + strukturirani logovi + bug-injection + 3–4 seed buga |
| 3–4 | Core agent loop (single agent + toolovi: log/code/git) → diagnose 1 bug end-to-end |
| 5 | Multi-agent split + hypothesis ranking + fix proposal (PR generacija) |
| 6 | Memorija (incident history) + reproduce/verify petlja |
| 7 | Guardraili + sigurnost (PII redakcija, human-in-loop, prompt-injection obrana) |
| 8 | Eval harness: 15+ bugova, scorecard, iteracija na točnosti |
| 9 | Observability/trace polish + **snimanje demo videa** |
| 10 | Writeup + rationale + submission. Buffer. |

> Video + writeup nose ~pola bodova → dani 9–10 su sveti, ne "ako ostane vremena".

## 10. Što presuđuje (checklist za pobjedu)

- [ ] Vidljiva autonomnost (zatvorena petlja, self-correct)
- [ ] Eval scorecard s brojkama
- [ ] Guardraili kao dio priče (ne dodatak)
- [ ] Oštar 60s hook u videu ("bug u 14:32 → PR u 14:33")
- [ ] Čist writeup: problem → rješenje → arhitektura → rezultati

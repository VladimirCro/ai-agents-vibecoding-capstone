# PLAN — LaunchGuard (Cloud Run deploy-readiness agent)

Capstone: **Agents for Business** trač. Autonoman ADK+Gemini agent koji prije deploya pomiruje
tri izvora istine (kod ⟷ deklaracija ⟷ live GCP) i hvata "silent-misbehave" misconfiguracije.

> Pivot iz "Sherlog" (debug agent, score 30/50, zasićena niša) → **LaunchGuard** (39/50,
> najdiferenciraniji, sjeda na user-ov GCP/spec2gcp ekspertizu). Detalji odluke: vidi memoriju.

## Što agent radi

```
TARGET (Cloud Run servis, npr. worknote-ai)
   ↓
[1] RepoAuditor        → inferira "intended contract" (PORT, env, secret refs, health probe)
[2] GcpStateInspector  → čita LIVE GCP read-only (SA IAM, APIs, Secret Manager grants, run config)
[3] Reconciler         → diff TRI izvora → delta: will-fail / will-misbehave / cost-risk
[4] FixWriter          → diffovi + readiness scorecard → otvori PR (human gate, nikad live mutacija)
```

**Killer trace (okosnica videa):**
> "Kod traži `SECRET_FOO`, runtime SA nema `secretAccessor` → deploy prolazi, prvi request 500
> → evo IAM diffa + PR."

## Diferencijator (čuvati framing!)

Tri-source delta koji **nijedan postojeći alat ne računa**: Checkov vidi samo repo, GCP Security
Review samo live state, deploy samo deklaraciju. Vrijednost je u **delti između tri izvora**.
NIKAD se ne smije prodavati kao "AI linter" — tada collapse na Checkov.

## Dvije non-negotiable prekretnice

1. **Golden-JSON fixture layer (Dan 3):** snimi prave gcloud outpute jednom → replay offline.
   Bez toga headline feature nije reproducibilan za žiri i demo je flaky. Ako slipa → fallback ideja "Pacijent".
2. **Framing = tri-source contract reconciliation** (gore).

## Hero target & Reuse

- **Hero target (REAL):** `worknote-ai` (live Cloud Run; ima sva tri izvora). Demo na stvarnom servisu.
- **Reuse:** `cloud-run-prep-agents/playbook/CLOUD_RUN_DEPLOYMENT_PLAYBOOK.md` → detektorska pravila.

## Stack

Google ADK + Gemini 2.5 · gcloud (read-only, gcloud-mcp) + GitHub MCP (PR) · PostgreSQL+pgvector
(memorija) · pytest (eval) · `adk web` (demo). Detalji: `docs/TECH_STACK.md`.

## Raspored (~10 radnih dana, rok 2026-07-06 PT)

| Dan | Cilj |
|---|---|
| 1–2 | ADK orchestrator + sub-agenti skeleton. **RepoAuditor**: parse Dockerfile/entrypoint/env/secret refs → "intended contract" JSON (deterministički + Gemini za ambiguitet) |
| 3 | **GcpStateInspector** read-only nad gcloud (SA IAM, APIs, secrets+grants, run config). **Golden-JSON fixture layer** (snimi worknote-ai outpute, replay offline) — NON-NEGOTIABLE |
| 4 | **Reconciler** (core IP): diff intended ⟷ declared ⟷ live; klasifikacija delte; hard-code high-value detektori (secret/secretAccessor, PORT, health probe, over-broad SA, scaling cost) |
| 5 | **FixWriter**: diffovi + readiness scorecard, open_pr (GitHub MCP). Guardrail spine: read-only enforcement, secret redakcija, allow-list, jedan demo blocked-write |
| 6–7 | 8–10 misconfigured repo fixtures (ground-truth, hero=secret/IAM). Eval run → precision/recall scorecard |
| 8 | Memorija (per-project gotcha, pgvector/ADK memory) — tanko, 1–2 recall momenta |
| 9 | Stretch (cuttable): `gcloud run deploy --no-traffic` canary za potvrdu blockera. Polish `adk web` traceova. **Snimanje videa** |
| 10 | Writeup + rationale + submission. Buffer. |

> Dani 9–10 (video + writeup) su sveti — ~pola bodova. Canary je prvi koji pada ako slipamo.

## Checklist za pobjedu

- [ ] Tri-source reconciliation radi end-to-end na worknote-ai
- [ ] Golden-JSON fixture layer (reproducibilno offline)
- [ ] SECRET_FOO killer trace u videu
- [ ] Eval scorecard s brojkama (precision/recall nad fixtureima)
- [ ] Guardrail-trip vidljiv u traceu (blocked write + redacted secret)
- [ ] MCP interop (gcloud-mcp + GitHub MCP) kao eksplicitna priča
- [ ] Framing "tri-source", ne "linter"

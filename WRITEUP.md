# LaunchGuard — Cloud Run deploy-readiness agent

> **DRAFT Kaggle writeup** — za finalizaciju nakon network-pass demoa. Trač: Agents for Business.

## The problem (and why it's silent)

A Cloud Run deploy can go green and the app still 500s on the very first request — because no
single tool ever sees all three sources of truth at once:

- a **linter** (Checkov, etc.) sees only the **repo**,
- **GCP Security Review** sees only the **live cloud state**,
- `gcloud deploy` sees only the **declared config**.

The canonical failure: the code reads `SECRET_FOO`, the deploy declares it as a `secretKeyRef`,
but the runtime service account was never granted `roles/secretmanager.secretAccessor`. Every
check passes in isolation. The deploy succeeds. The first request crashes on the secret mount.

I hit this class of bug on my own production Cloud Run services. So I built an agent that catches
it before deploy.

## The solution: three-source contract reconciliation

LaunchGuard is an autonomous Google ADK + Gemini agent that, for a target Cloud Run service,
reconciles three independently-derived contracts and surfaces the **delta**:

1. **Intended** — what the code requires (Dockerfile `$PORT`/host, entrypoint, env vars, secret refs, health/startup probe), inferred by **RepoAuditor**.
2. **Declared** — what the deploy says (`service.yaml`, `cloudbuild.yaml`, deploy workflow).
3. **Live** — what GCP actually grants (SA IAM bindings, enabled APIs, Secret Manager secrets + accessor grants, existing Run config), read **read-only** by **GcpStateInspector**.

The **Reconciler** diffs the three and classifies each delta as `will-fail`, `will-misbehave`, or
`cost-risk`. **FixWriter** turns each into a concrete fix — a unified diff or a `gcloud` command —
plus a readiness scorecard, and opens it as a **pull request** for human review. It never mutates
live infrastructure.

> The differentiator is structural: the value lives in the **delta between the three sources** —
> exactly what a single-source tool cannot compute.

## Agent design (course learnings, load-bearing not bolted-on)

- **Multi-agent (ADK):** Orchestrator delegating to RepoAuditor / GcpStateInspector / Reconciler / FixWriter, with handoff through ADK **session state**. Each sub-agent earns its place with a distinct tool set and responsibility.
- **Deterministic core, model at the edges:** parsing and the detector rules are deterministic; Gemini is used only to resolve ambiguity and write human-readable explanations. This is what makes the eval reproducible (same input → same finding).
- **Memory (pgvector):** per-project recall of past gotchas ("same SA-secret gap as your last run").
- **MCP interoperability:** `gcloud-mcp` for read-only GCP state, GitHub MCP for PR creation.
- **Guardrails as the spine:**
  - read-only on cloud — a mutating `gcloud` verb is rejected *before* execution and logged;
  - IAM/secret changes only ever ship as a PR (human-in-the-loop) — `applied` is always false;
  - secret redaction before anything reaches Gemini or a PR body;
  - per-agent tool allow-listing (the Reconciler holds zero tools and makes zero model calls);
  - fail-safe: low-confidence findings become `needs-review`, never a confident `will-fail` — because a wrong destructive suggestion is worse than a missed one.
- **Observability:** every step is traced in `adk web`.

> Security note (the "lethal trifecta"): an agent that reads untrusted input (repo/log/cloud
> output), has tool access, and can act is dangerous by construction. LaunchGuard treats all read
> content as data, not instructions, and routes every action through the read-only + PR-gate spine.

## Results

Evaluated on **9 fixtures** spanning the detector categories (secret-without-accessor, PORT
mismatch, missing health/startup probe, over-broad SA role, scaling cost-risk, API-not-enabled,
host/PID1, env-var mismatch) plus a **clean-control true-negative**:

| Metric | Value |
|---|---|
| Blocker precision / recall / F1 | **1.00 / 1.00 / 1.00** |
| False positives on clean-control | **0** |
| Warning coverage | 8/8 |
| Test suite (`make verify`: ruff + mypy + pytest) | **285 passing** |

The hero detector `secret-ref-without-secretAccessor`, run against a fixture modeled on a real
service, returns verdict **BLOCKED** at confidence 0.98 and emits the exact remediation:

```
gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
  --member='serviceAccount:...@....iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor'
```

(`applied=false` — proposed as a PR, never executed.)

### Validated on a real production service

LaunchGuard was run against my actual Cloud Run service `worknote-ai-staging` (read-only
`gcloud` describe/list/get-iam-policy — never a mutating call). It reconciled the real repo
(intended) against the real `service.yaml` (declared) against the real GCP state (15 secrets,
8 SA roles, 56 enabled APIs, recorded into a redacted golden fixture).

- **Verdict: WARN** — one genuine `will-misbehave` finding (`unpinned-base-image` in the real
  Dockerfile, confidence 0.90), **zero false positives**. Every secret the service references is
  in fact granted to the runtime SA — so the killer correctly did *not* fire on the real,
  correctly-configured service.
- The killer is demonstrated on a **minimal counterfactual** (`worknote-ai-gap`): the real fixture
  with exactly one field changed — `JWT_SECRET_KEY`'s accessor grant dropped — which makes
  `secret-ref-without-secretAccessor` fire (BLOCKED, confidence 0.98). Nothing manufactured; one
  honest field flip.

This is the credibility point: a real production service comes back clean (no false alarms), and
the failure mode is shown on a one-field delta from that same real state.

## Reproducibility

The deterministic core has zero runtime dependencies beyond stdlib + PyYAML, so the full eval runs
offline against golden-JSON fixtures (recorded GCP state, with secret values redacted at capture).
That's what makes the precision/recall numbers credible rather than a cherry-picked demo.

## Demo

Reproduce in ~30 seconds — deterministic, **no API key or network required**:

```bash
python scripts/demo.py
```

Animated terminal capture: [`assets/demo.svg`](assets/demo.svg) (open in a browser).

Actual output — LaunchGuard on the real service, then on a one-field counterfactual:

```text
SCENARIO 1 — REAL worknote-ai-staging (live GCP state, redacted snapshot)
  VERDICT: WARN   (will-fail=0, will-misbehave=1, cost-risk=0, needs-review=0)
  • [will-misbehave] unpinned-base-image (conf 0.9)
    The Dockerfile's base image is not pinned to a tag/SHA — builds not reproducible.

SCENARIO 2 — worknote-ai with JWT_SECRET_KEY accessor dropped (counterfactual)
  VERDICT: BLOCKED   (will-fail=1, will-misbehave=1, cost-risk=0, needs-review=0)
  • [will-fail] secret-ref-without-secretAccessor (conf 0.98)
    Secret 'JWT_SECRET_KEY' is referenced in code (Intended) and service.yaml (Declared)
    but the runtime SA lacks roles/secretmanager.secretAccessor → first request 500s.

  Proposed fix (PR — applied=False, human-in-the-loop):
    gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
      --project=worknote-ai \
      --member='serviceAccount:worknote-staging-run@worknote-ai.iam.gserviceaccount.com' \
      --role='roles/secretmanager.secretAccessor'
```

The real production service comes back clean of blockers (no false alarm); the deploy-breaking
failure mode is shown on a one-field delta from that same real state. Same pipeline can be driven
by the live Gemini orchestrator via `adk web` (see `NETWORK_PASS.md`).

## Limitations & honest notes

- Eval fixtures are synthetic-but-realistic; precision/recall is meaningful within that matrix.
- Live `gcloud` record mode and real Gemini calls run on a network-capable machine (the build
  environment was offline); every seam is wired and import-safe.
- Memory recall is intentionally thin (1–2 prior incidents); semantic collision on identical
  surrounding prose is possible and documented.

## Code

**https://github.com/VladimirCro/ai-agents-vibecoding-capstone** (public, CC-BY-4.0).
Built solo with a spec → build → review → QA workflow, reusing a hand-authored Cloud Run
deployment playbook as the detector rule source. `make verify`: 283 tests green.

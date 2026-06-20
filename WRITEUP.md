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
| Test suite (`make verify`: ruff + mypy + pytest) | **247 passing** |

The hero detector `secret-ref-without-secretAccessor`, run against a fixture modeled on a real
service, returns verdict **BLOCKED** at confidence 0.98 and emits the exact remediation:

```
gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
  --member='serviceAccount:...@....iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor'
```

(`applied=false` — proposed as a PR, never executed.)

## Reproducibility

The deterministic core has zero runtime dependencies beyond stdlib + PyYAML, so the full eval runs
offline against golden-JSON fixtures (recorded GCP state, with secret values redacted at capture).
That's what makes the precision/recall numbers credible rather than a cherry-picked demo.

## Demo

[VIDEO LINK] — on a real Cloud Run service: a missing IAM grant caught before deploy → fix PR →
guardrail blocked-write trip → eval scorecard.

## Limitations & honest notes

- Eval fixtures are synthetic-but-realistic; precision/recall is meaningful within that matrix.
- Live `gcloud` record mode and real Gemini calls run on a network-capable machine (the build
  environment was offline); every seam is wired and import-safe.
- Memory recall is intentionally thin (1–2 prior incidents); semantic collision on identical
  surrounding prose is possible and documented.

## Code

[REPO LINK] — Apache/CC-BY-4.0 on win. Built solo with the dev-agents workflow (spec → build →
review → QA), reusing a hand-authored Cloud Run deployment playbook as the detector rule source.

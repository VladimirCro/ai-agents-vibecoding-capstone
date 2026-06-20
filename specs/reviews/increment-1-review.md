# Code Review: LaunchGuard — Increment 1 (Spine through Reconciler)

**Reviewer**: Code Reviewer Agent
**Date**: 2026-06-21
**Status**: CONDITIONAL

---

## Summary

Increment 1 is a solid, well-structured deliverable. The deterministic core (models, tools, Reconciler rule engine, guardrails) is clean, correct, and fully verifiable. All 9 critical checks PASS. `make verify` passes all 129 tests (ruff + mypy + pytest). Two non-blocking findings are flagged and one nit; all are low risk. The CONDITIONAL status is driven by one evidence-discipline deferred verdict on the redact `"text"` passthrough key: it is safe by current design but relies on a per-call contract that QA should confirm via a targeted integration test.

---

## Critical Check Results

| # | Check | Result | Notes |
|---|---|---|---|
| 1 | Read-only enforcement rejects mutating verbs BEFORE exec | PASS | `check_gcloud_verb()` raises `GuardrailReadonlyViolation` before any execution path; AST-confirmed no actual `subprocess.run()` call in `gcloud_read.py`; log trip fires pre-exec |
| 2 | Redaction before model payload AND on trace | PASS | `redact()` correctly masks secret values while preserving names, accessor_members, and safe fields; `grep_code` pre-applies `redact()` at source; fixture is clean of secret values (PEM, JWT, AIza patterns absent) |
| 3 | Per-agent allow-list enforced in code (not prompt) | PASS | `TOOL_ALLOWLISTS` in `config.py`; `check_tool_allowed()` raises `GuardrailAllowlistViolation` + logs before any tool body executes; Reconciler and Orchestrator have `frozenset()` (zero tools) |
| 4 | Reconciler has NO external tools, NO model call | PASS | `engine.py` has no `from google`, no `import google`, no `genai`, no `from launchguard.tools`, no `subprocess`; confirmed by AST and grep |
| 5 | FixWriter never executes gcloud / never pushes to main | PASS | `fix_writer.py` is a clean stub; no `subprocess`, no gcloud calls; `propose_patch`/`open_pr` explicitly deferred to Increment 2 (BE-06); instruction explicitly forbids main/master targeting |
| 6 | Tool I/O matches api-contracts.yaml (field names/enums) | PASS | All models match contract exactly: `rule_id` enum strings, `delta_class` enum strings, `ReconciliationDelta` required fields, `ReadinessScorecard` verdict enum, `LiveState` required fields all match |
| 7 | Fail-safe (§8): ambiguous/low-confidence → needs-review, never will-fail | PASS | `host_binding=unknown` produces zero deltas (no confident will-fail); LLM-03 seam has explicit TODO deferral comment; Reconciler instruction enforces underclassification |
| 8 | Determinism: fixture_replay byte-identical across replays | PASS | `replay_to_json("worknote-ai-like")` called twice produces identical JSON (sort_keys=True, no randomness) |
| 9 | Killer detector correctness: fires on JWT_SECRET_KEY, NOT on other 8 secrets | PASS | Rule 1 fires for JWT_SECRET_KEY (accessor_members=[]); zero false positives on SES_SMTP_USERNAME, SES_SMTP_PASSWORD, SES_SMTP_HOST, SENTRY_DSN_BACKEND, LITELLM_AZURE_API_KEY, LITELLM_AZURE_ENDPOINT, LITELLM_VERTEX_CREDENTIALS, CLAMAV_FUNCTION_URL (all have SA in accessor_members) |

---

## Findings

### Blocking
_(none)_

### Non-blocking

- [ ] **Weak test assertion in `test_no_model_import_in_engine`** — `tests/test_reconciler.py:561-567` — The assertion uses a boolean OR with short-circuit logic that actually passes even though `"google"` IS present in `engine_source.lower()` (API hostname strings like `"aiplatform.googleapis.com"` contain "google"). The test reaches the correct conclusion but only because the second condition of the OR holds (no import lines start with "google"). This is fragile: a future `from google.something import X` inside a comment or long string could silently let the test pass. The stronger assertion (`assert "from google" not in engine_source` and `assert "import google" not in engine_source`) is already proven correct by separate grep; the test should be simplified to those two direct assertions. Severity: low (the underlying guarantee holds), but the test logic is misleading.

- [ ] **`_SAFE_PASSTHROUGH_KEYS` contains `"text"` — redaction relies on per-call pre-redaction contract** — `launchguard/guardrails/redact.py:93` — The `"text"` key is in `_SAFE_PASSTHROUGH_KEYS`, meaning if a dict `{"text": "<unredacted connection string>"}` were passed to `redact()`, the value would NOT be string-redacted. This is safe today because `grep_code` (`repo_tools.py:514`) pre-redacts the raw line text via `redact(line.strip()[:200])` before placing it in `CodeMatch.text`. However, any future caller that puts unredacted content under a `"text"` key and then calls `redact()` on the dict would silently leak it. The current design is correct but fragile; adding a note in `_SAFE_PASSTHROUGH_KEYS` explaining the invariant would prevent silent regressions.

### Nit

- [ ] **`ruff.toml` disables E501 (line-too-long) globally** — `ruff.toml:6-7` — The `ignore = ["E501"]` applies project-wide. The motivation (pre-existing long f-strings in the Reconciler's human-readable summary strings) is legitimate, and the 120-char `line-length` is a reasonable accommodation. However, disabling E501 globally means legitimately long lines elsewhere also go unchecked. Acceptable for Increment 1 given the constraint, but worth narrowing to per-file noqa annotations on the specific long lines in Increment 2.

---

## API Contract Compliance

This is a CLI/agent project. Tools are documented as contract operations in `api-contracts.yaml`. The following verifies tool I/O against the contract.

| Tool | Contract Operation | Path Match | Request Fields | Response Shape | Enum Compliance | Result |
|---|---|---|---|---|---|---|
| `parse_dockerfile` | `parseDockerfile` | n/a (in-process) | `repo_path`, `dockerfile_path` present | `DockerfileFacts` — all required fields match | `port_source` enum matches | PASS |
| `parse_app_entrypoint` | `parseAppEntrypoint` | n/a | `repo_path` present | `EntrypointFacts` — all required fields; confidence 0–1 | `host_binding` enum matches | PASS |
| `read_file` | `readFile` | n/a | `repo_path`, `file_path` present | `{"content": str}` | 403 mapped to `PathTraversalError` with `PATH_TRAVERSAL_VIOLATION` code | PASS |
| `grep_code` | `grepCode` | n/a | `repo_path`, `pattern` present | `{"matches": [CodeMatch]}` — file, line, text fields | n/a | PASS |
| `gcloud_read` | `gcloudRead` | n/a | resource/verb/project_id present; mode defaults to fixture | Returns `LiveState.to_dict()` in fixture mode | resource enum, verb enum, mode enum all match | PASS |
| `fixture_replay` | `fixtureReplay` | n/a | `fixture_name` present | `LiveState` parsed correctly | mode="fixture" set | PASS |
| `ReconciliationDelta` | data shape | n/a | all required fields: rule_id, delta_class, confidence, summary, evidence (minItems=1) | matches contract | rule_id + delta_class enums match exactly | PASS |
| `ReadinessScorecard` | data shape | n/a | verdict, counts, deltas required | matches contract | verdict enum (BLOCKED/WARN/READY) correct; counts use underscore names per contract | PASS |
| `LiveState` | data shape | n/a | project_id, runtime_sa, sa_iam_roles, enabled_apis, secrets, mode all present | matches contract | mode enum matches; secrets[].name + accessor_members correct | PASS |
| `IntendedContract` | data shape | n/a | all 9 required fields present | matches contract | host_binding enum matches | PASS |
| `DeclaredState` | data shape | n/a | all 6 required fields present | matches contract | scaling sub-object correct | PASS |

**Rule_id exactness check (KILLER):** `RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR = "secret-ref-without-secretAccessor"` — matches contract enum exactly. Confirmed firing in hero fixture test.

---

## QA Focus Areas

- **Hero fixture end-to-end**: run the full pipeline (build_intended_contract + parse_declared_state + fixture_replay + reconcile) against `worknote-ai-like`; assert exactly one `will-fail` delta with `rule_id="secret-ref-without-secretAccessor"` and "JWT_SECRET_KEY" in summary; assert zero deltas for the other 8 secrets. (Covered by `test_reconciler.py::test_hero_fixture_fires_on_jwt_secret_key` already — QA should confirm in integration context.)

- **Guardrail trip isolation**: each guardrail test should call `logger.reset()` before/after (already done via `autouse` fixture) — confirm the shared module-level `_default_logger` singleton doesn't leak state between test runs in parallel execution.

- **`read_file` path-traversal edge cases**: symlink-based traversal is handled by `Path.resolve()` → relative_to check; QA should test symlinks if the eval environment uses them.

- **`parse_declared_state` templated_unresolved**: the worknote-ai-like fixture has `${SA_EMAIL}` and `${PROJECT_ID}` placeholders; QA should confirm `service_account=None` and `templated_unresolved` is populated (currently confirmed by manual test: count=6).

- **`check_tool_allowed` unknown agent name**: `TOOL_ALLOWLISTS.get("UnknownAgent", frozenset())` returns empty frozenset → any tool call raises `GuardrailAllowlistViolation`; test `test_unknown_agent_raises` covers this.

---

## Deferred-to-QA Verdicts (Evidence Discipline)

- **Redact `"text"` safe-passthrough contract**: CONDITIONAL — RISK: any future caller that passes unredacted content as `{"text": "..."}` to `redact()` will receive an unmasked value because `"text"` is in `_SAFE_PASSTHROUGH_KEYS`. The current usage in `grep_code` is safe (pre-redacts the string before placing in `CodeMatch.text`), but this is a silent behavioral assumption. QA/integration must confirm: (1) no model-bound payload contains a raw `"text"` field with unredacted source-file content; (2) the `grep_code` output passed to any model call first goes through `redact(CodeMatch.to_dict())` and that the text value is already safe. This should be confirmed with a targeted test: pass a `CodeMatch`-shaped dict with a planted connection string under `"text"` through `redact()` and assert the connection string is NOT masked (to document the known passthrough behavior), then assert `grep_code` separately pre-masks it.

---

## Cross-Agent Touch Verification (Orchestrator-Noted Items)

1. **`launchguard/agent.py` — backend removed unused import**: Verified. The module-level `agent = None` pattern is intact; `_try_build_agent_for_adk_discovery()` is called at import time and silently skips when google-adk is absent. No behavioral change. The lazy-ADK import guard is structurally sound: `from google.adk.agents import Agent` only executes inside `build_root_agent()` and `_get_agent_class()`. Non-behavioral. PASS.

2. **`scripts/local-ci.sh` — mypy path-quoting fix for space in repo path**: Verified. The script uses `"${REPO_ROOT}/launchguard"` (double-quoted) in the mypy invocation, which correctly handles the space in `"AI Agents Intensive Vibe Coding"`. All three stages (ruff, mypy, pytest) execute and `make verify` produces 129 passed. PASS.

3. **`ruff.toml` with `ignore=["E501"]` + `line-length=120`**: Acceptable for Increment 1. The pre-existing long f-strings in `reconciler/engine.py` human-readable summaries are the primary driver. The `line-length=120` mitigates most real cases; the global E501 ignore is a pragmatic choice. Logged as a nit for Increment 2 cleanup. NOT a blocking issue.

---

## Task Status Changes

No task status changes (review artifact only — tasks remain at "implemented" pending this review's routing).

> Note to orchestrator: status for BE-01, BE-02, BE-03, BE-04, BE-05, BE-07, BE-08 (hero subset), LLM-01, INFRA-01 should be updated to `"reviewed"` per the CONDITIONAL verdict. The CONDITIONAL routes to qa-testing-engineer per handoff protocol.

---

**Next Agent (CONDITIONAL → QA):**

Review complete: LaunchGuard Increment 1 (spine through Reconciler). Status: CONDITIONAL. Review: `specs/reviews/increment-1-review.md`.

QA focus areas:
1. Hero fixture pipeline end-to-end: JWT_SECRET_KEY fires (TP), 8 others do not (no FP)
2. Guardrail trip isolation under parallel test execution (shared module-level logger singleton)
3. `grep_code` text-field redaction: confirm no unmasked content reaches any model-bound payload via `"text"` passthrough key
4. `parse_declared_state` templated_unresolved populated for `${SA_EMAIL}` / `${PROJECT_ID}` placeholders
5. `read_file` symlink traversal edge case if applicable in eval environment

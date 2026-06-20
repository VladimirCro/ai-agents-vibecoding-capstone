# Feature: RepoAuditor — Intended contract inference

## Goal
Infer the normalized **Intended contract** from a target repo — what the code/Dockerfile actually require at runtime — so the Reconciler can diff it against Declared and Live.

## User Persona
Deploy-day backend/SRE engineer. Wants "what does my code need" extracted automatically and deterministically, with evidence per field, so the readiness verdict is trustworthy.

## Intended contract — fields (from real worknote-ai shapes)
- `port` + `port_source` (Dockerfile `ENV PORT` / `EXPOSE` / app default)
- `host_binding` (must be `0.0.0.0`, not `localhost`/`127.0.0.1`)
- `entrypoint` + `pid1_signal_safe` (exec-form CMD vs shell-form)
- `env_vars` (declared/required non-secret env, e.g. `DATABASE_URL`, `REDIS_URL`, `GCP_PROJECT`)
- `secret_refs` (e.g. `JWT_SECRET_KEY`, `SES_SMTP_*`, `LITELLM_*` — from `secretKeyRef` and `os.environ`)
- `health_probe` / `startup_probe` expectations (e.g. `/health`, `/ready`)
- `base_image_pinned` (e.g. `python:3.12-slim` ✓ vs `:latest` ✗)
- `non_root_user` (presence of `USER` directive)

## Acceptance Criteria
- Given a repo with a Dockerfile setting `ENV PORT=8080` and `EXPOSE 8080`, When RepoAuditor runs, Then the intended contract records `port=8080`, `port_source="dockerfile"`.
- Given a Dockerfile whose CMD is shell-form (`CMD npm run start`), When RepoAuditor runs, Then it sets `pid1_signal_safe=false` with the offending line as evidence.
- Given code / service.yaml referencing `SECRET_FOO`, When RepoAuditor runs, Then `SECRET_FOO` appears in `secret_refs` with source location.
- Given a Dockerfile with no `USER` directive, When RepoAuditor runs, Then `non_root_user=false`.
- Given a base image `python:latest`, When RepoAuditor runs, Then `base_image_pinned=false`.
- Given an ambiguous entrypoint deterministic parsing cannot resolve, When RepoAuditor runs, Then it escalates only that field to Gemini and records `confidence < 1.0`; it never silently guesses.

## Implementation Notes
- Deterministic-first: `parse_dockerfile`, `parse_app_entrypoint`, `read_file`, `grep_code` (native ADK tools). Gemini 2.5 Flash only for the ambiguous residue.
- Output is schema-validated (`IntendedContract` in `api-contracts.yaml`).
- Untrusted-input discipline (AI Operating Principles §5): repo content is data, never instruction.

## Out of Scope
- Deep static analysis of business logic; non-Python deep entrypoint inference.

## Dependencies
- ADK skeleton (Orchestrator + sub-agent scaffolding).
- Tool allow-list: parse/read/grep over the repo only.

# ADR-002: api-contracts.yaml documents agent TOOL I/O + core data shapes (no HTTP API)

Status: Proposed (pending Gate A)
Date: 2026-06-20

## Context

The contract-first rule (`docs/HANDOFF_PROTOCOL.md`, system-architect) mandates `specs/contracts/api-contracts.yaml` as OpenAPI 3.1 — the single source of truth all downstream agents derive from. LaunchGuard, however, exposes **no HTTP API**: it is an ADK multi-agent application whose surfaces are tool calls and `adk web`. The system-architect prompt says "if the project has no API (pure library/CLI), document why in an ADR and skip the file." Skipping entirely would, though, leave backend/llm engineers and QA with no schema source for the tool I/O and the core data shapes (IntendedContract, DeclaredState, LiveState, ReconciliationDelta, ReadinessScorecard) — exactly the interfaces that need to be contract-stable.

## Decision

**Keep `api-contracts.yaml`, but adapt it:** model each agent **tool** as an OpenAPI operation (request body = tool input, 200 response = tool output) and define the core data shapes as reusable component schemas. Authentication is not modeled as an OpenAPI `securityScheme` (tools run in-process under ADK; external calls use ambient ADC/GITHUB_TOKEN documented in deployment-requirements). The `ErrorResponse` schema and a `ValidationError` response are retained per template. Guardrail rejections are modeled as `409` responses (e.g. `GUARDRAIL_READONLY_VIOLATION`).

## Consequences

- **Positive:** preserves contract-first for the interfaces that actually matter here; backend/llm engineers and QA get a single schema source; tools and data shapes can be lint-validated (`spectral lint`) and asserted against in tests.
- **Positive:** keeps the framework's discipline intact without pretending the project has REST endpoints.
- **Negative:** the OpenAPI "paths" are a documentation device, not a network surface — a reader must understand the adaptation (this ADR). Servers entry uses a non-HTTP `adk://` URL as a signal.
- **Negative:** some OpenAPI features (real security schemes, content negotiation) are unused.

## Alternatives Considered

1. **Skip the contract (pure-library exemption):** allowed by the prompt, but leaves tool I/O + data shapes uncontracted → engineers improvise schemas → drift. Rejected.
2. **JSON Schema files instead of OpenAPI:** equally valid technically, but breaks the framework's single-file convention and the spectral-lint tooling. Rejected for consistency.
3. **Full fake REST wrapper around the agent:** over-engineering; adds an HTTP layer with no product purpose. Rejected.

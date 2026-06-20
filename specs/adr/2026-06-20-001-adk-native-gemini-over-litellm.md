# ADR-001: Google ADK + native Gemini as the agent/model gateway (not LiteLLM-as-mandatory)

Status: Proposed (pending Gate A)
Date: 2026-06-20

## Context

The canonical dev-agents stack treats **LiteLLM** as the mandatory LLM gateway. LaunchGuard is a capstone for the Google × Kaggle "AI Agents: Intensive Vibe Coding" course (*Agents for Business* track), where the rubric explicitly rewards using the course material — **Google ADK** for multi-agent orchestration and **Gemini** as the model. `docs/TECH_STACK.md` already records this override. The LLM Engineer agent enforces a "never import provider SDKs directly; use the canonical gateway" rule and needs to know what the canonical gateway IS for this project.

## Decision

Use **Google ADK (`google-adk`) with native `google-genai`** as the agent framework and model gateway. Models: **Gemini 2.5 Pro** (reasoning: ambiguity classification, fix/PR-body generation) and **Gemini 2.5 Flash** (parsing/extraction). The ADK model abstraction IS the canonical gateway for this project — business logic uses ADK's model layer, not raw provider SDKs. ADK's `LiteLlm` wrapper remains an available option but is **not** the default.

## Consequences

- **Positive:** maximal rubric alignment (native course tooling); first-class multi-agent sessions, tracing, and `adk web` demo surface out of the box; one fewer abstraction layer for a solo 10-day build.
- **Positive:** the LLM-gateway rule still holds — "use the ADK model layer" replaces "use LiteLLM" as the canonical-gateway instruction in `architecture.md` handoff notes.
- **Negative:** loses LiteLLM's provider-agnostic routing/fallback/cost callbacks; cost tracking + fallback must be handled via ADK callbacks or accepted as thin for the capstone.
- **Negative:** couples the deliverable to Gemini; acceptable given the course context and that the differentiator is the reconciliation logic, not the model.

## Alternatives Considered

1. **LiteLLM (canonical default):** provider-agnostic, but adds a layer the rubric does not reward and contradicts the course's native-ADK intent.
2. **ADK + `LiteLlm` wrapper:** keeps LiteLLM routing under ADK; viable fallback if multi-provider is ever needed, but unnecessary complexity now.
3. **Raw google-genai without ADK:** loses multi-agent sessions, tracing, and the `adk web` demo — the very things the rubric rewards.

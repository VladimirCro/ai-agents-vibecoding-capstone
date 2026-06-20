"""
launchguard.config — Model-name constants, environment configuration, and per-agent
tool allow-list registry.

This module is stdlib-only and importable without google-adk installed.  It is the
SINGLE SOURCE OF TRUTH for:
  - Model identifiers (ADR-001: native Gemini via ADK, not LiteLLM)
  - Per-agent tool allow-lists (AI Operating Principles §4 / BE-07 guardrails seam)
  - Environment variable loading from venv/.env

Backend (BE-07) imports TOOL_ALLOWLISTS from here to build the enforcement seam.
Sub-agents import MODEL_PRO / MODEL_FLASH from here for their ADK model config.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------
# Load from venv/.env (gitignored).  We walk up from this file's location to
# find the repo root, then resolve venv/.env.  python-dotenv is a declared dep;
# if somehow missing we fail gracefully at startup (the key won't be set).

def _load_env() -> None:
    """Load environment from venv/.env.  Called once at module import time."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        # This file lives at <repo_root>/launchguard/config.py → parents[1] = repo root.
        venv_env_path = Path(__file__).resolve().parents[1] / "venv" / ".env"
        if venv_env_path.exists():
            load_dotenv(venv_env_path, override=False)
    except ImportError:
        # python-dotenv not installed — rely on shell-exported env vars.
        pass


_load_env()

# ---------------------------------------------------------------------------
# Model-name constants  (ADR-001: native google-genai via ADK)
# ---------------------------------------------------------------------------

#: Gemini 2.5 Pro — reasoning tasks: ambiguity classification, fix/PR-body generation,
#: Reconciler ambiguity layer (LLM-03).  High capability, higher latency/cost.
MODEL_PRO: str = "gemini-2.5-pro"

#: Gemini 2.5 Flash — parsing / extraction tasks: entrypoint inference, declared-parser
#: supplementary pass (LLM-02).  Lower latency / cost.
MODEL_FLASH: str = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Google API Key accessor (never logs or prints the value)
# ---------------------------------------------------------------------------

def get_google_api_key() -> str | None:
    """
    Return GOOGLE_API_KEY from the environment (loaded from venv/.env).

    Never logs, prints, or otherwise surfaces the key value.
    Returns None if the variable is absent (caller decides how to handle).
    """
    return os.environ.get("GOOGLE_API_KEY")

# ---------------------------------------------------------------------------
# Per-agent tool allow-list registry  (AI Operating Principles §4)
#
# This dict is the SINGLE SOURCE OF TRUTH consumed by BE-07 guardrails module.
# Keys are the canonical agent names used in ADK registration.
# Values are frozensets of allowed tool operation-IDs (matching api-contracts.yaml
# operationId fields and the tool function names registered with each agent).
#
# "no-tools" agents (Reconciler) have an empty frozenset — they run pure logic + model;
# any tool invocation attempted by those agents must be rejected and logged by BE-07.
# ---------------------------------------------------------------------------

TOOL_ALLOWLISTS: dict[str, frozenset[str]] = {
    "RepoAuditor": frozenset({
        "parse_dockerfile",       # api-contracts.yaml: parseDockerfile
        "parse_app_entrypoint",   # api-contracts.yaml: parseAppEntrypoint
        "read_file",              # api-contracts.yaml: readFile  (repo-scoped, traversal-guarded)
        "grep_code",              # api-contracts.yaml: grepCode
    }),
    "GcpStateInspector": frozenset({
        "gcloud_read",            # api-contracts.yaml: gcloudRead  (read-only verb allow-list)
        "fixture_replay",         # api-contracts.yaml: fixtureReplay  (offline golden-JSON)
    }),
    "Reconciler": frozenset(),    # no external tools — pure deterministic logic + model (BE-05 / LLM-03)
    "FixWriter": frozenset({
        "propose_patch",          # api-contracts.yaml: proposePatch  (applied=false always)
        "open_pr",                # api-contracts.yaml: openPr  (GitHub MCP, non-default branch only)
    }),
    "Orchestrator": frozenset(),  # orchestrates delegation only; no direct tool calls
}

# ---------------------------------------------------------------------------
# GCP read-only verb allow-list (also consumed by BE-03 gcloud_read implementation)
# ---------------------------------------------------------------------------

GCLOUD_READ_VERBS: frozenset[str] = frozenset({
    "describe",
    "list",
    "get-iam-policy",
})

# ---------------------------------------------------------------------------
# Session-state key constants (shared across Orchestrator + sub-agents)
# ---------------------------------------------------------------------------
# Each sub-agent reads its INPUT slice and writes its OUTPUT slice using these keys.
# Using constants avoids typo-driven silent misses in session state lookups.

class SessionKeys:
    """Canonical session-state keys for inter-agent handoff via ADK session state."""

    # Input to the pipeline (set by Orchestrator before delegating)
    REPO_PATH: str = "repo_path"
    GCP_PROJECT_ID: str = "gcp_project_id"
    GCP_SERVICE_NAME: str = "gcp_service_name"
    FIXTURE_NAME: str = "fixture_name"
    RUN_MODE: str = "run_mode"           # "live" | "record" | "fixture"

    # RepoAuditor output → Reconciler input
    INTENDED_CONTRACT: str = "intended_contract"

    # Declared parser output (runs under Orchestrator) → Reconciler input
    DECLARED_STATE: str = "declared_state"

    # GcpStateInspector output → Reconciler input
    LIVE_STATE: str = "live_state"

    # Reconciler output → FixWriter input
    RECONCILIATION_DELTAS: str = "reconciliation_deltas"

    # FixWriter output (final result surfaced to user)
    READINESS_SCORECARD: str = "readiness_scorecard"
    PROPOSED_PATCHES: str = "proposed_patches"
    PR_URL: str = "pr_url"

    # Memory annotation (BE-09, P2)
    MEMORY_ANNOTATIONS: str = "memory_annotations"

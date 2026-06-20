"""
launchguard.agent — Root Orchestrator Agent for the LaunchGuard pipeline.

This module defines build_root_agent(), the single factory function that constructs the
complete ADK agent graph.  It is the only module that imports google-adk (lazily, inside
the factory) — all other launchguard modules are importable without google-adk installed.

PIPELINE SEQUENCE
-----------------
The Orchestrator delegates to sub-agents in this fixed order:

  1. RepoAuditor        → reads repo → writes intended_contract to session state
  2. GcpStateInspector  → reads GCP/fixture → writes live_state to session state
  3. [Declared parser]  → parses service.yaml (tool call under Orchestrator) → writes
                          declared_state to session state  (BE-02 implements the tool)
  4. Reconciler         → diffs 3 states → writes reconciliation_deltas to session state
  5. FixWriter          → computes scorecard + patches + PR → writes final results

Steps 1 and 2 can run concurrently (no dependency between repo read and GCP read).
Steps 3 must complete before Reconciler (needs DeclaredState).
Step 4 needs all three states.
Step 5 needs Reconciler output.

INTER-AGENT HANDOFF MECHANISM
------------------------------
ADK session state (not files) carries data between sub-agents.  The Orchestrator sets
initial state keys (repo_path, gcp_project_id, etc.) and each sub-agent reads its input
slice and writes its output slice.  Session-state keys are defined in config.SessionKeys.

ADK AGENT GRAPH
---------------
ADK's SequentialAgent (or similar orchestration) manages the delegation.  The Orchestrator
is the root Agent; sub-agents are registered as sub-agents in the ADK graph.

MODEL ASSIGNMENT (ADR-001: native google-genai via ADK, not LiteLLM)
-----------------
  Orchestrator     — no model call itself (pure delegation + gating)
  RepoAuditor      — MODEL_FLASH  (parsing/extraction)
  GcpStateInspector — MODEL_FLASH (parsing/extraction)
  Reconciler       — MODEL_PRO   (reasoning: ambiguity classification + summaries)
  FixWriter        — MODEL_PRO   (reasoning: diff/PR-body generation)

The model constants are imported from launchguard.config (not hardcoded here).

DECLARED PARSER NOTE
--------------------
The architecture treats the Declared parser as a "tool/module under Orchestrator" rather
than a separate sub-agent (architecture.md §2 component table).  In practice, the
Orchestrator calls a parse_declared_state() tool (implemented in BE-02) directly in its
tool set.  This is simpler than a sub-agent because Declared parsing is purely
deterministic (no model needed).
"""

from __future__ import annotations


def build_root_agent():
    """
    Build and return the root Orchestrator ADK Agent with all sub-agents registered.

    Lazily imports google-adk; raises RuntimeError with a clear message if not installed.
    All sub-agent factories are also lazy — they import ADK only when this function runs.

    This is the entry-point for 'adk web' (ADK discovers the agent via this factory or
    via the 'agent' module attribute).  See launchguard/__init__.py for the public alias.

    Raises:
        RuntimeError: if google-adk is not installed.

    Returns:
        google.adk.agents.Agent: the root Orchestrator agent with sub-agents.

    TODO (LLM-02, Increment 2): Add ambiguity escalation hook in RepoAuditor delegation.
    TODO (LLM-03, Increment 2): Add Reconciler ambiguity layer after deterministic rules.
    TODO (LLM-04, Increment 2): Wire FixWriter with propose_patch + open_pr tools.
    """
    try:
        from google.adk.agents import Agent  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "google-adk is not installed — run 'make install' to install dependencies.\n"
            "Hint: pip install google-adk google-genai"
        ) from exc

    from launchguard.config import MODEL_PRO  # noqa: PLC0415
    from launchguard.sub_agents import (  # noqa: PLC0415
        build_fix_writer_agent,
        build_gcp_state_inspector_agent,
        build_reconciler_agent,
        build_repo_auditor_agent,
    )

    # Build sub-agents (each factory imports ADK lazily — already imported above)
    repo_auditor = build_repo_auditor_agent()
    gcp_state_inspector = build_gcp_state_inspector_agent()
    reconciler = build_reconciler_agent()
    fix_writer = build_fix_writer_agent()

    # Root Orchestrator — delegates pipeline steps, gates on results, presents output.
    # Model: MODEL_PRO for orchestration reasoning (deciding next step, gating).
    # Tools: parse_declared_state (BE-02) is registered here for the Declared parser step.
    orchestrator = Agent(
        name="Orchestrator",
        model=MODEL_PRO,
        description=(
            "Root orchestrator for LaunchGuard three-source reconciliation.  "
            "Sequences: RepoAuditor → GcpStateInspector → Declared parser → "
            "Reconciler → FixWriter.  Communicates via ADK session state."
        ),
        instruction=_ORCHESTRATOR_INSTRUCTION,
        sub_agents=[
            repo_auditor,
            gcp_state_inspector,
            reconciler,
            fix_writer,
        ],
    )

    return orchestrator


# ---------------------------------------------------------------------------
# ADK module-level agent discovery
# ---------------------------------------------------------------------------
# 'adk web' discovers the root agent via this module-level variable (or via
# a factory entrypoint configured in pyproject.toml / adk config).
# We define 'agent' lazily: if google-adk is installed, build_root_agent() works;
# if not, this module-level name stays None so that pure-Python imports are unaffected.

agent = None  # Populated only when google-adk is present (see adk_app below)


def _try_build_agent_for_adk_discovery():
    """
    Attempt to build the root agent for 'adk web' module discovery.
    Called at module import time — silently skips if google-adk is not installed.
    """
    global agent  # noqa: PLW0603
    try:
        agent = build_root_agent()
    except RuntimeError:
        # google-adk not installed — skip.  Pure-Python imports still work.
        pass


_try_build_agent_for_adk_discovery()


# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------

_ORCHESTRATOR_INSTRUCTION = """\
You are the Orchestrator of LaunchGuard, a three-source Cloud Run deploy-readiness
reconciliation agent.  Your job is to COORDINATE the pipeline — you do not parse files,
read GCP state, or generate fixes yourself.  You delegate to specialized sub-agents.

PIPELINE SEQUENCE (fixed order):
  Step 1: Delegate to RepoAuditor
          Input:  session state keys repo_path, gcp_service_name
          Output: session state key intended_contract
          Wait for completion before proceeding.

  Step 2: Delegate to GcpStateInspector  (can run concurrently with Step 1 if ADK supports)
          Input:  session state keys gcp_project_id, gcp_service_name, fixture_name, run_mode
          Output: session state key live_state
          Wait for completion before Step 3.

  Step 3: Call the parse_declared_state tool (declared parser — deterministic, no sub-agent)
          Input:  repo_path from session state
          Output: write result to session state key declared_state
          Wait for completion.

  Step 4: Delegate to Reconciler
          Input:  session state keys intended_contract, declared_state, live_state
          Output: session state key reconciliation_deltas
          Wait for completion.

  Step 5: Delegate to FixWriter
          Input:  session state key reconciliation_deltas
          Output: session state keys readiness_scorecard, proposed_patches, pr_url
          Wait for completion.

  Step 6: Present the ReadinessScorecard to the user.
          Summarize: verdict, counts by class, top findings, PR URL (if opened).

GATING:
  - If RepoAuditor fails (e.g., no Dockerfile found), surface the error and stop.
  - If GcpStateInspector fails in live mode, suggest switching to fixture mode and stop.
  - If Reconciler produces zero deltas, surface as READY with no further action.
  - If FixWriter opens a PR, surface the PR URL and stop — human review required.

SESSION STATE:
  Initial keys (set before you begin — provided by the caller):
    repo_path, gcp_project_id, gcp_service_name, fixture_name, run_mode
  You READ these and ensure they are present before delegating.
  If any required initial key is missing, ask the user before proceeding.

FRAMING:
  LaunchGuard is a THREE-SOURCE CONTRACT RECONCILIATION agent.  Present findings as:
  "Intended requires X, Declared says Y, Live grants Z → discrepancy class."
  NEVER frame findings as "AI linter found a problem in your Dockerfile."
  The insight is the three-way gap, not a single-source lint.

DO NOT:
  - Parse files or call repo tools directly (RepoAuditor does this).
  - Call gcloud or GCP tools directly (GcpStateInspector does this).
  - Diff the three states yourself (Reconciler does this).
  - Generate diffs or open PRs yourself (FixWriter does this).
  - Emit secret values in any output.
"""

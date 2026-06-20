"""
launchguard.sub_agents — ADK sub-agent factory modules for the LaunchGuard pipeline.

Each module exposes a build_*_agent() factory that constructs an ADK Agent with:
  - its canonical model assignment (MODEL_FLASH or MODEL_PRO)
  - its registered tools (from the TOOL_ALLOWLISTS registry)
  - its session-state input/output slice contract

All imports of google-adk are lazy (inside factory functions) so this package is
importable without google-adk installed.  The factory functions raise RuntimeError
clearly if called without google-adk.

Sub-agents:
  repo_auditor       — RepoAuditor  (MODEL_FLASH; reads repo → IntendedContract)
  gcp_state_inspector — GcpStateInspector  (MODEL_FLASH; reads GCP → LiveState)
  reconciler         — Reconciler  (MODEL_PRO; diffs 3 sources → ReconciliationDelta[])
  fix_writer         — FixWriter  (MODEL_PRO; deltas → scorecard + PR)  [Increment 1 stub]
"""

from launchguard.sub_agents.fix_writer import build_fix_writer_agent
from launchguard.sub_agents.gcp_state_inspector import build_gcp_state_inspector_agent
from launchguard.sub_agents.reconciler import build_reconciler_agent
from launchguard.sub_agents.repo_auditor import build_repo_auditor_agent

__all__ = [
    "build_repo_auditor_agent",
    "build_gcp_state_inspector_agent",
    "build_reconciler_agent",
    "build_fix_writer_agent",
]

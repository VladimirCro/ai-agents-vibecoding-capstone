"""
launchguard — ADK multi-agent package for three-source Cloud Run deploy-readiness reconciliation.

Imports cleanly with zero runtime dependencies (google-adk not required at import time).
The ADK agent graph is constructed lazily via build_root_agent() — only that call requires
google-adk to be installed.

Public surface:
    build_root_agent()     -> Orchestrator Agent (requires google-adk)
    models                 -> shared data shapes (stdlib-only, always importable)
    config                 -> model constants + TOOL_ALLOWLISTS registry (stdlib-only)
"""

__version__ = "0.1.0"
__all__ = ["build_root_agent"]


def build_root_agent():
    """
    Build and return the root Orchestrator ADK Agent.

    This is the single public entry-point that imports google-adk.  All other
    launchguard modules (models, config, sub_agents stubs) are importable without
    google-adk present.

    Raises:
        RuntimeError: if google-adk is not installed.
    """
    from launchguard.agent import build_root_agent as _build  # noqa: PLC0415 (lazy import)

    return _build()

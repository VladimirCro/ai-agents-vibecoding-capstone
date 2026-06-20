"""
test_adk_skeleton — Acceptance tests for LLM-01 (the ADK Orchestrator skeleton).

These pin the Increment-1 acceptance contract for launchguard/agent.py +
launchguard/sub_agents/* in the environment where google-adk is NOT installed:

  1. The package and every sub-agent module import cleanly with ZERO external deps
     (no google-adk required at import time) — the deterministic core depends on this.
  2. build_root_agent() and each build_*_agent() factory exists and is callable.
  3. With google-adk absent, build_root_agent() raises a clear RuntimeError at CALL
     time (not at import time) telling the operator to install it — never a bare
     ImportError leaking from deep inside, and never a silent failure.
  4. The module-level `agent` discovery shim degrades to None when ADK is absent
     (so `adk web` discovery is attempted but pure-Python imports are unaffected).
  5. The per-agent tool allow-list registry (config.TOOL_ALLOWLISTS) is wired and
     matches the architecture/guardrails contract (least privilege, §4).

This is the LLM-01 terminal-coverage test: the skeleton's full agent-graph behavior
cannot be exercised until google-adk is installed (DEMO-01, Increment 2), but its
Increment-1 contract — clean import + lazy build + graceful degradation + allow-list
wiring — is fully asserted here.
"""

from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# 1. Clean import with zero external deps
# ---------------------------------------------------------------------------

def test_package_imports_without_google_adk():
    import launchguard  # noqa: F401  (import is the assertion)


@pytest.mark.parametrize(
    "module_name",
    [
        "launchguard.agent",
        "launchguard.config",
        "launchguard.models",
        "launchguard.sub_agents",
        "launchguard.sub_agents.repo_auditor",
        "launchguard.sub_agents.gcp_state_inspector",
        "launchguard.sub_agents.reconciler",
        "launchguard.sub_agents.fix_writer",
    ],
)
def test_skeleton_modules_import_clean(module_name):
    importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# 2 + 3. Factories exist; build_root_agent raises a clear RuntimeError when ADK absent
# ---------------------------------------------------------------------------

def test_build_root_agent_exists_and_callable():
    from launchguard.agent import build_root_agent

    assert callable(build_root_agent)


def _google_adk_installed() -> bool:
    try:
        import google.adk.agents  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(_google_adk_installed(), reason="google-adk present; degradation path not exercised")
def test_build_root_agent_raises_clear_error_without_adk():
    from launchguard.agent import build_root_agent

    with pytest.raises(RuntimeError) as exc:
        build_root_agent()
    msg = str(exc.value).lower()
    # The message must guide the operator — not leak a bare ImportError.
    assert "google-adk" in msg
    assert "install" in msg


@pytest.mark.parametrize(
    "factory_name",
    [
        "build_repo_auditor_agent",
        "build_gcp_state_inspector_agent",
        "build_reconciler_agent",
        "build_fix_writer_agent",
    ],
)
def test_subagent_factories_exist_and_callable(factory_name):
    import launchguard.sub_agents as sa

    factory = getattr(sa, factory_name)
    assert callable(factory)


# ---------------------------------------------------------------------------
# 4. Discovery shim degrades to None when ADK absent
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_google_adk_installed(), reason="google-adk present; `agent` would be built")
def test_module_agent_is_none_without_adk():
    import launchguard.agent as agent_mod

    assert agent_mod.agent is None


# ---------------------------------------------------------------------------
# 5. Allow-list registry is wired per the guardrails/architecture contract (§4)
# ---------------------------------------------------------------------------

def test_tool_allowlists_match_least_privilege_contract():
    from launchguard.config import TOOL_ALLOWLISTS

    assert TOOL_ALLOWLISTS["RepoAuditor"] == frozenset(
        {"parse_dockerfile", "parse_app_entrypoint", "read_file", "grep_code"}
    )
    assert TOOL_ALLOWLISTS["GcpStateInspector"] == frozenset({"gcloud_read", "fixture_replay"})
    # Reconciler has NO external tools (pure logic + model) — architecture §2 / AI Principles §4.
    assert TOOL_ALLOWLISTS["Reconciler"] == frozenset()
    assert TOOL_ALLOWLISTS["FixWriter"] == frozenset({"propose_patch", "open_pr"})
    # Orchestrator delegates only.
    assert TOOL_ALLOWLISTS["Orchestrator"] == frozenset()


def test_model_constants_are_native_gemini_per_adr_001():
    from launchguard.config import MODEL_FLASH, MODEL_PRO

    # ADR-001: native Gemini via ADK (not LiteLLM). Pin the model family.
    assert MODEL_PRO.startswith("gemini-2.5-pro")
    assert MODEL_FLASH.startswith("gemini-2.5-flash")

"""
launchguard.tools — Deterministic tool implementations for LaunchGuard sub-agents.

Modules:
    repo_tools      — BE-01: parse_dockerfile, parse_app_entrypoint, read_file, grep_code,
                               build_intended_contract (RepoAuditor tools)
    declared_parser — BE-02: parse_declared_state (Declared parser tool)
    gcloud_read     — BE-03: gcloud_read (GcpStateInspector tool, read-only enforced)
    fixture_replay  — BE-04: fixture_replay, record_live_state, redact_snapshot

All tools are stdlib-only (+ pyyaml for declared_parser).  No google-adk import.
No LLM calls here — deterministic core only (AI Operating Principles §6).
"""

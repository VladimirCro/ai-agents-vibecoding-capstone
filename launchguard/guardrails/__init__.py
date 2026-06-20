"""
launchguard.guardrails — Enforcement seam for LaunchGuard AI Operating Principles.

Exports:
    audit     — AuditLogger: record every tool call + guardrail trip (in-memory)
    enforce   — check_tool_allowed(), check_gcloud_verb(), demo_blocked_write()
    redact    — redact(): mask secret values/tokens/PII while keeping names

These three are the enforcement seam between the ADK agent graph and external tools.
NOT prompt-based: all enforcement is code, not instruction.

AI Operating Principles:
    §1 Read-only on cloud  → enforce.check_gcloud_verb()
    §3 Redaction before model → redact.redact()
    §4 Tool allow-listing  → enforce.check_tool_allowed()
    §7 Audit trail         → audit.AuditLogger
"""

from launchguard.guardrails.audit import AuditLogger, get_audit_logger
from launchguard.guardrails.enforce import (
    GuardrailAllowlistViolation,
    GuardrailReadonlyViolation,
    check_gcloud_verb,
    check_tool_allowed,
    demo_blocked_write,
)
from launchguard.guardrails.redact import redact, redact_snapshot

__all__ = [
    "AuditLogger",
    "get_audit_logger",
    "GuardrailAllowlistViolation",
    "GuardrailReadonlyViolation",
    "check_tool_allowed",
    "check_gcloud_verb",
    "demo_blocked_write",
    "redact",
    "redact_snapshot",
]

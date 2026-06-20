"""
launchguard.guardrails.enforce — Tool allow-list and read-only verb enforcement.

AI Operating Principles:
    §1 Read-only on cloud    → check_gcloud_verb() rejects mutating verbs BEFORE any exec
    §4 Tool allow-listing    → check_tool_allowed() rejects off-list tools, denies + logs

These functions are called at tool-call seams BEFORE the tool body executes.
Violations raise typed exceptions that map to contract error codes (409).

Exports:
    GuardrailAllowlistViolation  — raised when a tool is off the agent's allow-list
    GuardrailReadonlyViolation   — raised when a mutating gcloud verb is attempted
    check_tool_allowed(agent_name, tool_name) → None (raises on violation)
    check_gcloud_verb(verb)                   → None (raises on violation)
    demo_blocked_write()                      → always raises GuardrailReadonlyViolation
"""

from __future__ import annotations

from launchguard.config import GCLOUD_READ_VERBS, TOOL_ALLOWLISTS
from launchguard.guardrails.audit import get_audit_logger

# ---------------------------------------------------------------------------
# Typed exception classes (map to contract 409 GUARDRAIL_* codes)
# ---------------------------------------------------------------------------

class GuardrailViolation(Exception):
    """Base class for all guardrail violations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """Serialize to ErrorResponse shape (api-contracts.yaml: ErrorResponse)."""
        return {"code": self.code, "message": self.message}


class GuardrailAllowlistViolation(GuardrailViolation):
    """
    Raised when an agent attempts to call a tool outside its allow-list.

    Contract: 409 GUARDRAIL_ALLOWLIST_VIOLATION
    AI Operating Principles §4.
    """

    def __init__(self, agent_name: str, tool_name: str) -> None:
        super().__init__(
            code="GUARDRAIL_ALLOWLIST_VIOLATION",
            message=(
                f"Agent '{agent_name}' attempted to call tool '{tool_name}', "
                f"which is not on its allow-list. LaunchGuard enforces least-privilege "
                f"tool access (AI Operating Principles §4)."
            ),
        )
        self.agent_name = agent_name
        self.tool_name = tool_name


class GuardrailReadonlyViolation(GuardrailViolation):
    """
    Raised when a mutating gcloud verb is attempted BEFORE execution.

    Contract: 409 GUARDRAIL_READONLY_VIOLATION
    AI Operating Principles §1.
    """

    def __init__(self, verb: str) -> None:
        super().__init__(
            code="GUARDRAIL_READONLY_VIOLATION",
            message=(
                f"Mutating gcloud verb '{verb}' rejected; LaunchGuard is read-only on cloud. "
                f"Only verbs in the read allow-list are permitted: "
                f"{sorted(GCLOUD_READ_VERBS)}. (AI Operating Principles §1)"
            ),
        )
        self.verb = verb


# ---------------------------------------------------------------------------
# Enforcement functions
# ---------------------------------------------------------------------------

def check_tool_allowed(agent_name: str, tool_name: str) -> None:
    """
    Assert that `agent_name` is permitted to call `tool_name`.

    If the tool is not on the agent's allow-list:
      - Logs a GUARDRAIL TRIP to the audit logger
      - Raises GuardrailAllowlistViolation (never continues)

    Called at every tool invocation seam BEFORE the tool body executes.

    Args:
        agent_name: Canonical agent name (matches TOOL_ALLOWLISTS key).
        tool_name:  Tool function name (matches TOOL_ALLOWLISTS value set).

    Raises:
        GuardrailAllowlistViolation: if the tool is off the allow-list.
    """
    allowed = TOOL_ALLOWLISTS.get(agent_name, frozenset())
    if tool_name not in allowed:
        logger = get_audit_logger()
        logger.log_guardrail_trip(
            code="GUARDRAIL_ALLOWLIST_VIOLATION",
            detail=f"agent='{agent_name}' attempted tool='{tool_name}'",
            agent_name=agent_name,
        )
        raise GuardrailAllowlistViolation(agent_name=agent_name, tool_name=tool_name)


def check_gcloud_verb(verb: str, agent_name: str = "GcpStateInspector") -> None:
    """
    Assert that `verb` is in the read-only allow-list BEFORE any gcloud exec.

    If the verb is mutating:
      - Logs a GUARDRAIL TRIP to the audit logger
      - Raises GuardrailReadonlyViolation (never continues to exec)

    This is the read-only enforcement seam for the GcpStateInspector (BE-03).

    Args:
        verb:       The gcloud verb attempted (e.g. "describe", "delete").
        agent_name: Agent context for audit logging (default "GcpStateInspector").

    Raises:
        GuardrailReadonlyViolation: if the verb is not in GCLOUD_READ_VERBS.
    """
    if verb not in GCLOUD_READ_VERBS:
        logger = get_audit_logger()
        logger.log_guardrail_trip(
            code="GUARDRAIL_READONLY_VIOLATION",
            detail=f"mutating verb='{verb}' rejected before exec",
            agent_name=agent_name,
        )
        raise GuardrailReadonlyViolation(verb=verb)


def demo_blocked_write() -> None:
    """
    Intentional demo path that provably raises GuardrailReadonlyViolation.

    This function exists to demonstrate and test the read-only enforcement seam.
    It attempts the mutating verb "add-iam-policy-binding" which is NOT in
    GCLOUD_READ_VERBS, so check_gcloud_verb() will always block it.

    Calling this:
      - Logs exactly ONE guardrail trip with code GUARDRAIL_READONLY_VIOLATION
      - Raises GuardrailReadonlyViolation immediately (never proceeds to exec)
      - Is visible in the hero trace as the "demo blocked-write" event

    Used in:
      - DEMO-01 hero trace: shows the enforcement seam working
      - QA-01 tests: asserts exactly one trip is logged

    Raises:
        GuardrailReadonlyViolation: always (by design).
    """
    # This is the mutating verb that LaunchGuard must NEVER execute.
    # It would grant IAM access if run — we block it before it reaches the shell.
    DEMO_MUTATING_VERB = "add-iam-policy-binding"
    check_gcloud_verb(DEMO_MUTATING_VERB, agent_name="GcpStateInspector")
    # Never reached — check_gcloud_verb always raises for this verb.
    raise AssertionError("demo_blocked_write: should have raised before this line")  # pragma: no cover

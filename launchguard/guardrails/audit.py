"""
launchguard.guardrails.audit — Audit logger for tool calls and guardrail trips.

AI Operating Principles §7 (Transparency / audit trail):
  Every tool invocation and every guardrail TRIP is recorded as a structured record.
  Records are in-memory and retrievable via get_records() for inspection in adk web trace.

Usage:
    from launchguard.guardrails.audit import get_audit_logger

    logger = get_audit_logger()
    logger.log_tool_call("RepoAuditor", "read_file", {"file_path": "Dockerfile"}, "ok")
    logger.log_guardrail_trip("GUARDRAIL_READONLY_VIOLATION", "delete", "GcpStateInspector")
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Audit record shapes
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """One recorded tool invocation."""
    timestamp: str
    agent_name: str
    tool_name: str
    input_summary: dict[str, Any]   # Summary of inputs (never secret values)
    outcome: str                    # "ok" | "denied" | "error"


@dataclass
class GuardrailTripRecord:
    """One recorded guardrail enforcement event."""
    timestamp: str
    code: str                   # e.g. "GUARDRAIL_READONLY_VIOLATION"
    detail: str                 # What was attempted
    agent_name: str             # Which agent triggered it
    blocked: bool = True        # Always True — guardrail always blocks on a trip


# ---------------------------------------------------------------------------
# AuditLogger — in-memory, append-only, retrievable for trace inspection
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    In-memory audit log for LaunchGuard tool calls and guardrail trips.

    Thread-safety: not required for Increment 1 (single-threaded ADK pipeline).
    In Increment 2, wrap with a threading.Lock if ADK runs tools concurrently.

    Records are visible in adk web trace by calling get_records() or get_trips().
    """

    def __init__(self) -> None:
        self._tool_calls: list[ToolCallRecord] = []
        self._guardrail_trips: list[GuardrailTripRecord] = []

    # ------------------------------------------------------------------ #
    # Public logging API
    # ------------------------------------------------------------------ #

    def log_tool_call(
        self,
        agent_name: str,
        tool_name: str,
        input_summary: dict[str, Any],
        outcome: str = "ok",
    ) -> None:
        """Record a tool invocation.  input_summary must NOT contain secret values."""
        record = ToolCallRecord(
            timestamp=_now(),
            agent_name=agent_name,
            tool_name=tool_name,
            input_summary=input_summary,
            outcome=outcome,
        )
        self._tool_calls.append(record)

    def log_guardrail_trip(
        self,
        code: str,
        detail: str,
        agent_name: str,
        blocked: bool = True,
    ) -> None:
        """
        Record a guardrail enforcement trip.

        This is the mandatory log line whenever a guardrail fires.
        Trips are visible in adk web trace and are the assertable evidence
        for QA guardrail-trip tests.

        Args:
            code:       Guardrail error code, e.g. "GUARDRAIL_READONLY_VIOLATION".
            detail:     What was attempted (verb, tool name, etc.) — no secret values.
            agent_name: Which agent triggered the trip.
            blocked:    Whether the action was blocked (always True for hard guardrails).
        """
        record = GuardrailTripRecord(
            timestamp=_now(),
            code=code,
            detail=detail,
            agent_name=agent_name,
            blocked=blocked,
        )
        self._guardrail_trips.append(record)
        # Emit to stdout so it shows in adk web console trace
        print(
            f"[GUARDRAIL TRIP] {record.timestamp} | {code} | {detail} | "
            f"agent={agent_name} | blocked={blocked}"
        )

    # ------------------------------------------------------------------ #
    # Retrieval API (for trace inspection + assertions in tests)
    # ------------------------------------------------------------------ #

    def get_tool_calls(self) -> list[ToolCallRecord]:
        """Return all recorded tool calls (read-only snapshot)."""
        return list(self._tool_calls)

    def get_guardrail_trips(self) -> list[GuardrailTripRecord]:
        """Return all recorded guardrail trips (read-only snapshot)."""
        return list(self._guardrail_trips)

    def get_records(self) -> dict[str, Any]:
        """
        Return both logs as a combined dict — useful for JSON serialization into trace.

        Returns:
            {"tool_calls": [...], "guardrail_trips": [...]}
        """
        return {
            "tool_calls": [
                {
                    "timestamp": r.timestamp,
                    "agent_name": r.agent_name,
                    "tool_name": r.tool_name,
                    "input_summary": r.input_summary,
                    "outcome": r.outcome,
                }
                for r in self._tool_calls
            ],
            "guardrail_trips": [
                {
                    "timestamp": r.timestamp,
                    "code": r.code,
                    "detail": r.detail,
                    "agent_name": r.agent_name,
                    "blocked": r.blocked,
                }
                for r in self._guardrail_trips
            ],
        }

    def reset(self) -> None:
        """Clear all records (for test isolation)."""
        self._tool_calls.clear()
        self._guardrail_trips.clear()


# ---------------------------------------------------------------------------
# Module-level singleton (default logger for the pipeline)
# ---------------------------------------------------------------------------

_default_logger: AuditLogger = AuditLogger()


def get_audit_logger() -> AuditLogger:
    """
    Return the module-level default AuditLogger instance.

    All tools and guardrail functions use this shared logger so that a single
    get_records() call returns the complete trace.  Tests can call reset() to
    isolate log state between test cases.
    """
    return _default_logger


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now() -> str:
    """Return UTC timestamp as ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

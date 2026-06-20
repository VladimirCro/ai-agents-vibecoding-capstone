"""
launchguard.memory.annotate — wire per-project memory into the delta flow (BE-09).

annotate_recurring():
  1. For each delta, recall prior findings for the SAME project (cosine on redacted summary).
  2. If a prior occurrence exists, prepend a "recurring — seen on <date>" note to the delta's
     recommendation (additive; never changes delta_class or confidence).
  3. Record the current delta as a finding for future runs.

Order matters: recall is checked BEFORE recording the current run, otherwise the current run
would always self-match. Memory is additive and NON-BLOCKING — any store failure leaves the
deltas untouched (PRD: memory never blocks a run).
"""

from __future__ import annotations

from launchguard.memory.store import MemoryStore, get_memory_store
from launchguard.models import ReconciliationDelta


def annotate_recurring(
    project_id: str,
    deltas: list[ReconciliationDelta],
    *,
    store: MemoryStore | None = None,
    record: bool = True,
) -> list[ReconciliationDelta]:
    """
    Annotate deltas that recur for this project, then record the current findings.

    Args:
        project_id: GCP project / repo identity scoping recall.
        deltas:     current run's classified deltas (mutated in place with annotations).
        store:      MemoryStore to use; defaults to get_memory_store() (pgvector or in-memory).
        record:     whether to persist the current deltas after recall (default True).

    Returns:
        The same deltas list (annotated). Never raises on store failure (additive).
    """
    try:
        mem = store or get_memory_store()
    except Exception:
        return deltas  # memory unavailable → run proceeds unannotated (non-blocking)

    for d in deltas:
        try:
            prior = mem.recall(project_id, d.rule_id, d.summary)
        except Exception:
            prior = []
        if prior:
            seen_on = prior[0].finding.first_seen_at or "a previous run"
            note = f"RECURRING — seen on {seen_on} for this project. " \
                   f"(Same {d.rule_id} gap as last run.)"
            d.recommendation = (
                f"{note}\n{d.recommendation}" if d.recommendation else note
            )

    if record:
        for d in deltas:
            try:
                mem.record_finding(project_id, d.rule_id, d.delta_class, d.summary)
            except Exception:
                pass  # additive — a failed write never breaks the run

    return deltas


__all__ = ["annotate_recurring"]

"""
launchguard.reconciler — Reconciler rule engine (CORE IP, BE-05).

The Reconciler has NO external tools and makes NO model calls.
It is pure deterministic logic over the three normalized state objects.

Exports:
    reconcile(intended, declared, live) -> list[ReconciliationDelta]

AI Operating Principles:
    §5 Untrusted input: Reconciler trusts only schema-validated model inputs
    §6 Determinism: same inputs → same deltas, always
    §8 Fail-safe: low confidence → needs-review, never confident will-fail
"""

from launchguard.reconciler.engine import reconcile

__all__ = ["reconcile"]

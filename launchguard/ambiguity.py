"""
launchguard.ambiguity — LLM-02 / LLM-03 ambiguity seam (Gemini-at-the-edges).

The LaunchGuard design is "deterministic core, model at the edges" (architecture.md §2):
deterministic parsing + detector rules do the heavy lifting; Gemini is used ONLY for
(a) resolving a single ambiguous field the deterministic parser could not (LLM-02) and
(b) classifying genuinely ambiguous reconciliation residue no rule fired on (LLM-03).

google-adk / google-genai are NOT installable in this environment. This module therefore
builds the SEAM with a hard, fail-safe DETERMINISTIC FALLBACK:

  - If a Gemini classifier is available (ADK present + GOOGLE_API_KEY set + a classifier
    injected), it is used.
  - Otherwise, ambiguity is resolved the fail-safe way (AI Operating Principles §8):
      * an ambiguous RepoAuditor field stays UNKNOWN with confidence < 1.0 (never guessed)
      * an ambiguous reconciliation delta is labeled `needs-review`, NEVER `will-fail`
        (prefer a false negative over a confident-but-wrong destructive recommendation).

This guarantees: tests pass with zero external deps; the model path is wired but optional;
and the fail-safe is the DEFAULT, not an error path. On a network machine, inject a real
Gemini classifier via set_gemini_classifier() and the same call sites use it.

Untrusted-input discipline (AI Operating Principles §5): repo / GCP content handed to a
classifier is DATA. The redaction pass (§3) runs on every model-bound payload before the
classifier ever sees it — enforced here, not left to the prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from launchguard.config import get_google_api_key
from launchguard.guardrails.redact import redact
from launchguard.models import (
    DeltaClass,
    EntrypointFacts,
    Evidence,
    EvidenceSource,
    HostBinding,
    ReconciliationDelta,
    RuleId,
)

# ---------------------------------------------------------------------------
# Confidence policy
# ---------------------------------------------------------------------------

#: A field/delta below this confidence is considered ambiguous (needs the edge path).
AMBIGUITY_THRESHOLD: float = 1.0

#: A model classifier may NEVER exceed this confidence for an ambiguity-classified delta
#: (it was ambiguous by definition — keep it reviewable). AI Operating Principles §8.
MAX_MODEL_CONFIDENCE: float = 0.85


# ---------------------------------------------------------------------------
# Pluggable Gemini classifier (injected on a network machine; absent here)
# ---------------------------------------------------------------------------
# A classifier is a callable: (redacted_payload: dict) -> dict with keys
#   {"label": str, "confidence": float, "explanation": str}
# It MUST receive an already-redacted payload. We never pass raw content.

_GEMINI_CLASSIFIER: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def set_gemini_classifier(
    classifier: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> None:
    """Inject (or clear) a Gemini-backed classifier. Default None → deterministic fallback."""
    global _GEMINI_CLASSIFIER  # noqa: PLW0603
    _GEMINI_CLASSIFIER = classifier


def gemini_available() -> bool:
    """
    True only if a classifier is injected AND an API key is present AND google-genai imports.

    Absent any of these, the fail-safe deterministic path is used. This is the DEFAULT in
    CI / this sandbox — not an error.
    """
    if _GEMINI_CLASSIFIER is None:
        return False
    if not get_google_api_key():
        return False
    try:
        import google.genai  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# LLM-02: RepoAuditor single-field ambiguity escalation
# ---------------------------------------------------------------------------

def resolve_entrypoint_ambiguity(facts: EntrypointFacts) -> EntrypointFacts:
    """
    Resolve an ambiguous entrypoint host-binding (LLM-02).

    If the deterministic parser already resolved it (confidence >= threshold), return as-is.
    Otherwise:
      - With a real Gemini classifier available → escalate ONLY this field (redacted), and
        record the model's confidence (capped < 1.0).
      - Without one (this environment) → leave host_binding UNKNOWN with confidence < 1.0.
        The field is NEVER silently guessed (PRD acceptance criterion).

    Returns a (possibly new) EntrypointFacts; never mutates the input.
    """
    if facts.confidence >= AMBIGUITY_THRESHOLD and facts.host_binding != HostBinding.UNKNOWN:
        return facts  # already deterministic

    if not gemini_available():
        # Fail-safe: keep it explicitly ambiguous, do not guess (AI Principles §8).
        note = Evidence(
            source=EvidenceSource.INTENDED,
            locator="entrypoint",
            snippet="(ambiguous host-binding; Gemini edge path unavailable — left UNKNOWN, not guessed)",
        )
        return EntrypointFacts(
            host_binding=HostBinding.UNKNOWN,
            port=facts.port,
            confidence=min(facts.confidence, 0.5),
            evidence=[*facts.evidence, note],
        )

    # Model path (network machine): classify the single field on a redacted payload.
    payload = redact({
        "task": "classify_host_binding",
        "evidence": [e.to_dict() for e in facts.evidence],
        "options": [HostBinding.ALL, HostBinding.LOCALHOST, HostBinding.LOOPBACK, HostBinding.UNKNOWN],
    })
    assert _GEMINI_CLASSIFIER is not None  # gemini_available() guaranteed it
    result = _GEMINI_CLASSIFIER(payload)
    label = result.get("label", HostBinding.UNKNOWN)
    conf = min(float(result.get("confidence", 0.0)), MAX_MODEL_CONFIDENCE)
    return EntrypointFacts(
        host_binding=label if label in vars(HostBinding).values() else HostBinding.UNKNOWN,
        port=facts.port,
        confidence=conf,
        evidence=[
            *facts.evidence,
            Evidence(
                source=EvidenceSource.INTENDED,
                locator="entrypoint",
                snippet=f"(Gemini-classified host_binding={label}, conf={conf:.2f}; {result.get('explanation','')})",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# LLM-03: Reconciler ambiguity classification
# ---------------------------------------------------------------------------

def classify_reconciliation_ambiguity(
    intended: Any,
    declared: Any,
    live: Any,
    existing_deltas: list[ReconciliationDelta],
) -> list[ReconciliationDelta]:
    """
    Produce `needs-review` deltas for genuine ambiguity the deterministic rules did NOT
    fire on (LLM-03). Returns ADDITIONAL deltas to append; never modifies existing ones.

    Deterministic-residue detection (works WITHOUT a model — this is the fail-safe core):
    we look for inputs that *look* like they might be a problem but no rule could confirm,
    e.g.:
      - intended.host_binding == "unknown" (parser couldn't resolve, no rule could fire)
      - a secret_ref whose accessor membership couldn't be determined (runtime_sa is None)
      - intended.confidence < 1.0 overall (some field was model/ambiguity-flagged)

    For each, we emit a `needs-review` delta with confidence < 1.0 — NEVER `will-fail`
    (AI Operating Principles §8). If a real Gemini classifier is available, it refines the
    explanation/confidence (still capped, still needs-review).

    This is the seam: the same residue list would be sent to Gemini on a network machine;
    here we classify it fail-safe.
    """
    residue: list[ReconciliationDelta] = []
    already = {d.rule_id for d in existing_deltas}

    # Residue 1: unresolved host binding (parser ambiguous, host-not-0.0.0.0 did NOT fire) #
    host_binding = getattr(intended, "host_binding", None)
    host_fired = RuleId.HOST_NOT_0_0_0_0 in already
    if host_binding == HostBinding.UNKNOWN and not host_fired:
        residue.append(_needs_review_delta(
            summary=(
                "The application's host binding could not be determined deterministically "
                "and no rule could confirm a misconfiguration. A human should verify the app "
                "binds 0.0.0.0 (not loopback) so Cloud Run can route traffic."
            ),
            locator="intended.host_binding",
            snippet="host_binding=unknown (deterministic parse inconclusive)",
            source=EvidenceSource.INTENDED,
        ))

    # Residue 2: secret refs whose accessor membership is indeterminate (no runtime SA) ---- #
    runtime_sa = getattr(live, "runtime_sa", None)
    secret_refs = list(getattr(intended, "secret_refs", []) or [])
    secret_fired = (
        RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR in already
        or RuleId.SECRET_DECLARED_NOT_CREATED in already
    )
    if secret_refs and runtime_sa is None and not secret_fired:
        residue.append(_needs_review_delta(
            summary=(
                "Code references secrets but the runtime service account could not be "
                "determined from Live state, so secret-accessor grants cannot be checked. "
                "A human should confirm the runtime SA has secretAccessor on these secrets."
            ),
            locator="live.runtime_sa",
            snippet="runtime_sa=None (accessor check indeterminate)",
            source=EvidenceSource.LIVE,
        ))

    # Residue 3: overall low-confidence contract (some field was model/ambiguity-flagged) -- #
    overall_conf = float(getattr(intended, "confidence", 1.0))
    if overall_conf < AMBIGUITY_THRESHOLD and not residue and not existing_deltas:
        residue.append(_needs_review_delta(
            summary=(
                f"The intended contract was inferred with reduced confidence "
                f"({overall_conf:.2f}); no deterministic rule fired but the inputs are not "
                f"fully resolved. Flagging for human review rather than asserting readiness."
            ),
            locator="intended.confidence",
            snippet=f"confidence={overall_conf:.2f} (< 1.0)",
            source=EvidenceSource.INTENDED,
        ))

    # Optional model refinement (network machine only) ------------------------------------ #
    if residue and gemini_available():
        payload = redact({
            "task": "explain_reconciliation_ambiguity",
            "residue": [d.to_dict() for d in residue],
        })
        assert _GEMINI_CLASSIFIER is not None
        try:
            result = _GEMINI_CLASSIFIER(payload)
            explanation = str(result.get("explanation", ""))
            if explanation:
                for d in residue:
                    d.recommendation = explanation
                    d.confidence = min(d.confidence, MAX_MODEL_CONFIDENCE)
        except Exception:
            # Model failure must NOT break the deterministic result — fail safe.
            pass

    return residue


def _needs_review_delta(
    *,
    summary: str,
    locator: str,
    snippet: str,
    source: str,
) -> ReconciliationDelta:
    """Build a fail-safe needs-review delta (confidence < 1.0, never will-fail)."""
    return ReconciliationDelta(
        rule_id=RuleId.AMBIGUOUS,
        delta_class=DeltaClass.NEEDS_REVIEW,
        confidence=0.5,  # ambiguous by definition — reviewable, never confident
        summary=str(redact(summary)),
        evidence=[Evidence(source=source, locator=locator, snippet=snippet)],
        recommendation=(
            "Escalated to human review (AI Operating Principles §8: prefer a false "
            "negative over a confident-but-wrong destructive recommendation)."
        ),
    )


__all__ = [
    "AMBIGUITY_THRESHOLD",
    "MAX_MODEL_CONFIDENCE",
    "classify_reconciliation_ambiguity",
    "gemini_available",
    "resolve_entrypoint_ambiguity",
    "set_gemini_classifier",
]

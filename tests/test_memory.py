"""
tests/test_memory.py — BE-09 + QA-02: per-project gotcha memory (graceful fallback).

Postgres is NOT reachable here and psycopg is NOT installed, so these tests exercise the
in-memory fallback (the architect-anticipated path) and assert:

  - get_memory_store() falls back to the in-memory backend (clear marker), never raises
  - recall annotates a recurring finding on a repeat run for the SAME project
  - no prior history → run proceeds with NO recall annotation (additive, non-blocking)
  - a different project does NOT recall another project's finding (scoped recall)
  - persisted summaries are redacted (no secret value stored, §3)
  - the hashing embedder is deterministic (same text → cosine 1.0) and pure-Python
  - annotate_recurring never raises even if the store misbehaves (additive guarantee)
"""

from __future__ import annotations

import pytest

from launchguard.memory import (
    InMemoryStore,
    annotate_recurring,
    get_memory_store,
    hash_embed,
    reset_shared_memory,
)
from launchguard.memory.embed import cosine_similarity
from launchguard.memory.store import RECALL_SIMILARITY_THRESHOLD
from launchguard.models import (
    DeltaClass,
    Evidence,
    EvidenceSource,
    ReconciliationDelta,
    RuleId,
)


@pytest.fixture(autouse=True)
def _fresh_memory():
    reset_shared_memory()
    yield
    reset_shared_memory()


def _delta(summary="Secret 'JWT_SECRET_KEY' missing accessor") -> ReconciliationDelta:
    return ReconciliationDelta(
        rule_id=RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR,
        delta_class=DeltaClass.WILL_FAIL,
        confidence=0.98,
        summary=summary,
        evidence=[Evidence(EvidenceSource.LIVE, "secretmanager/JWT_SECRET_KEY/iam-policy", "accessor_members=[]")],
    )


class TestBackendSelection:
    def test_no_dsn_falls_back_to_in_memory(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        store = get_memory_store()
        assert store.backend_name == "in-memory"
        assert store.available() is True

    def test_unreachable_db_falls_back_not_raises(self, monkeypatch):
        # psycopg is absent → even with a DSN we must fall back, never raise.
        monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost:5432/nope")
        store = get_memory_store()
        assert store.backend_name == "in-memory"


class TestRecallAnnotation:
    def test_recurring_annotation_on_repeat_run(self):
        d1 = _delta()
        annotate_recurring("proj-1", [d1])
        assert "RECURRING" not in d1.recommendation  # first time — no recall

        d2 = _delta()
        annotate_recurring("proj-1", [d2])
        assert "RECURRING" in d2.recommendation  # second time — recalled

    def test_no_history_no_annotation(self):
        d = _delta()
        annotate_recurring("brand-new-proj", [d])
        assert "RECURRING" not in d.recommendation

    def test_recall_is_project_scoped(self):
        annotate_recurring("proj-A", [_delta()])
        d_other = _delta()
        annotate_recurring("proj-B", [d_other])
        assert "RECURRING" not in d_other.recommendation

    def test_additive_never_blocks(self):
        # Even with a deliberately broken store, annotate_recurring must not raise and
        # must leave the delta unannotated (memory failure is non-blocking — PRD).
        class _Broken(InMemoryStore):
            def recall(self, *a, **k):
                raise RuntimeError("boom")

            def record_finding(self, *a, **k):
                raise RuntimeError("boom")

        d = _delta()
        out = annotate_recurring("proj-x", [d], store=_Broken())
        assert out == [d]
        assert "RECURRING" not in d.recommendation


class TestRedaction:
    def test_persisted_summary_redacted(self):
        store = get_memory_store()
        # connection-string form is caught by the redactor
        f = store.record_finding(
            "p", "r", "will-fail",
            "leak DATABASE_URL=postgres://u:supersecret@h:5432/db here",
        )
        assert "supersecret" not in f.summary
        assert "[REDACTED]" in f.summary

    def test_redaction_is_conservative_over_recall(self):
        """
        The shared redactor masks aggressively: the phrase "Secret 'NAME'" trips the
        keyword-value heuristic and masks the name too. This is the SAFE direction
        (never leak) and is intentional — memory recall keys on the redacted text, so a
        recurring gap still recalls (same redacted text → cosine 1.0). The trade-off (two
        different secrets under the same rule/prose could collide) is acceptable for the
        thin P2 memory feature. Asserting the masked-not-leaked invariant here.
        """
        store = get_memory_store()
        f = store.record_finding("p", "secret-ref-without-secretAccessor", "will-fail",
                                 "Secret 'JWT_SECRET_KEY' missing accessor")
        # Conservative redaction: no raw secret VALUE; the marker is present.
        assert "[REDACTED]" in f.summary
        # Recall still works on the redacted text (same gap → recalled).
        recalled = store.recall("p", "secret-ref-without-secretAccessor",
                                "Secret 'JWT_SECRET_KEY' missing accessor")
        assert recalled and recalled[0].similarity >= RECALL_SIMILARITY_THRESHOLD


class TestEmbedder:
    def test_hash_embed_deterministic(self):
        a = hash_embed("same text")
        b = hash_embed("same text")
        assert a == b
        assert abs(cosine_similarity(a, b) - 1.0) < 1e-9

    def test_hash_embed_dim_and_unit_norm(self):
        v = hash_embed("anything")
        assert len(v) == 768
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_different_text_below_recall_threshold(self):
        a = hash_embed("proj|secret-ref-without-secretAccessor|Secret 'JWT_SECRET_KEY' missing")
        b = hash_embed("proj|port-mismatch|completely different finding text")
        assert cosine_similarity(a, b) < RECALL_SIMILARITY_THRESHOLD

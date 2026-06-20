"""
launchguard.memory.store — MemoryStore interface + in-memory + pgvector backends.

ProjectFinding mirrors the architecture.md §5 `project_finding` table (redacted summary,
embedding, first/last seen). Memory is ADDITIVE and NON-BLOCKING: any backend failure or an
empty store degrades to "no recall", never an error that stops a run (PRD acceptance).

Backends:
  - InMemoryStore : pure stdlib, default in CI / this sandbox.
  - PgVectorStore : psycopg + pgvector, used only if psycopg imports AND DATABASE_URL is set.

get_memory_store() picks the best available backend and reports which one via .backend_name.
"""

from __future__ import annotations

import datetime
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from launchguard.guardrails.redact import redact
from launchguard.memory.embed import cosine_similarity, embed

#: Cosine threshold above which two findings are "the same gap" for recall.
RECALL_SIMILARITY_THRESHOLD: float = 0.9


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data shapes (mirror architecture.md §5 project_finding)
# ---------------------------------------------------------------------------

@dataclass
class ProjectFinding:
    """One stored finding for a project (REDACTED summary; no secret values)."""
    project_id: str
    rule_id: str
    delta_class: str
    summary: str                      # REDACTED before persistence (§3)
    embedding: list[float] = field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""

    def identity_text(self) -> str:
        """Stable text identity used for embedding/recall (rule + project + summary)."""
        return f"{self.project_id}|{self.rule_id}|{self.summary}"


@dataclass
class RecalledFinding:
    """A prior finding matched on recall, with its similarity score."""
    finding: ProjectFinding
    similarity: float


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class MemoryStore(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def record_finding(
        self, project_id: str, rule_id: str, delta_class: str, summary: str
    ) -> ProjectFinding:
        """Persist a REDACTED finding. Returns the stored record."""

    @abstractmethod
    def recall(
        self, project_id: str, rule_id: str, summary: str,
        *, threshold: float = RECALL_SIMILARITY_THRESHOLD,
    ) -> list[RecalledFinding]:
        """Return prior findings for the SAME project similar to the query (cosine)."""

    def available(self) -> bool:
        """Whether this backend is usable. InMemory is always True."""
        return True


# ---------------------------------------------------------------------------
# In-memory backend (default)
# ---------------------------------------------------------------------------

class InMemoryStore(MemoryStore):
    """
    Pure-stdlib store. Findings live in a per-process dict keyed by project_id.

    Used in CI / sandbox where Postgres is unreachable. Deterministic recall via the
    hashing embedder: the SAME gap recurring in the SAME project recalls at cosine 1.0.
    """

    backend_name = "in-memory"

    def __init__(self) -> None:
        self._by_project: dict[str, list[ProjectFinding]] = {}

    def record_finding(
        self, project_id: str, rule_id: str, delta_class: str, summary: str
    ) -> ProjectFinding:
        safe_summary = str(redact(summary))  # §3 — never store a secret value
        bucket = self._by_project.setdefault(project_id, [])

        # If the same (rule_id, summary) already exists, update last_seen_at (recurrence).
        for existing in bucket:
            if existing.rule_id == rule_id and existing.summary == safe_summary:
                existing.last_seen_at = _now()
                return existing

        finding = ProjectFinding(
            project_id=project_id,
            rule_id=rule_id,
            delta_class=delta_class,
            summary=safe_summary,
            first_seen_at=_now(),
            last_seen_at=_now(),
        )
        finding.embedding = embed(finding.identity_text())
        bucket.append(finding)
        return finding

    def recall(
        self, project_id: str, rule_id: str, summary: str,
        *, threshold: float = RECALL_SIMILARITY_THRESHOLD,
    ) -> list[RecalledFinding]:
        bucket = self._by_project.get(project_id, [])
        if not bucket:
            return []
        safe_summary = str(redact(summary))
        query = ProjectFinding(
            project_id=project_id, rule_id=rule_id, delta_class="", summary=safe_summary
        )
        q_vec = embed(query.identity_text())
        out: list[RecalledFinding] = []
        for f in bucket:
            sim = cosine_similarity(q_vec, f.embedding)
            if sim >= threshold:
                out.append(RecalledFinding(finding=f, similarity=sim))
        out.sort(key=lambda r: r.similarity, reverse=True)
        return out

    def clear(self) -> None:
        """Test helper: wipe all findings."""
        self._by_project.clear()


# ---------------------------------------------------------------------------
# pgvector backend (import-guarded; not used in this sandbox)
# ---------------------------------------------------------------------------

class PgVectorStore(MemoryStore):
    """
    PostgreSQL 16 + pgvector backend (architecture.md §5).

    Import-guarded: psycopg is imported lazily inside __init__ so that importing this module
    NEVER requires psycopg. If psycopg is missing or the DB is unreachable, callers fall back
    to InMemoryStore via get_memory_store(). DDL matches architecture.md §5.

    This class is fully written for the network machine; it is NOT exercised in the sandbox
    (no psycopg, no reachable Postgres). Its logic is covered by InMemoryStore behavior tests
    plus an availability test that asserts graceful fallback.
    """

    backend_name = "pgvector"

    _DDL = """
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS project_finding (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id    text NOT NULL,
        rule_id       text NOT NULL,
        delta_class   text NOT NULL,
        summary       text NOT NULL,
        embedding     vector(768),
        first_seen_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS project_finding_project_idx ON project_finding (project_id);
    """

    def __init__(self, dsn: str) -> None:
        import psycopg  # noqa: PLC0415  (guarded — only here, never at module import)

        self._psycopg = psycopg
        self._dsn = dsn
        self._conn = psycopg.connect(dsn)
        with self._conn.cursor() as cur:
            cur.execute(self._DDL)
        self._conn.commit()

    def record_finding(
        self, project_id: str, rule_id: str, delta_class: str, summary: str
    ) -> ProjectFinding:
        safe_summary = str(redact(summary))
        vec = embed(f"{project_id}|{rule_id}|{safe_summary}")
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE project_finding SET last_seen_at = now() "
                "WHERE project_id = %s AND rule_id = %s AND summary = %s",
                (project_id, rule_id, safe_summary),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO project_finding "
                    "(project_id, rule_id, delta_class, summary, embedding) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (project_id, rule_id, delta_class, safe_summary, vec_literal),
                )
        self._conn.commit()
        now = _now()
        return ProjectFinding(
            project_id=project_id, rule_id=rule_id, delta_class=delta_class,
            summary=safe_summary, embedding=vec, first_seen_at=now, last_seen_at=now,
        )

    def recall(
        self, project_id: str, rule_id: str, summary: str,
        *, threshold: float = RECALL_SIMILARITY_THRESHOLD,
    ) -> list[RecalledFinding]:
        safe_summary = str(redact(summary))
        vec = embed(f"{project_id}|{rule_id}|{safe_summary}")
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        with self._conn.cursor() as cur:
            # 1 - cosine_distance = cosine_similarity; pgvector <=> is cosine distance.
            cur.execute(
                "SELECT project_id, rule_id, delta_class, summary, "
                "first_seen_at, last_seen_at, 1 - (embedding <=> %s) AS sim "
                "FROM project_finding WHERE project_id = %s "
                "ORDER BY sim DESC",
                (vec_literal, project_id),
            )
            rows = cur.fetchall()
        out: list[RecalledFinding] = []
        for r in rows:
            sim = float(r[6])
            if sim >= threshold:
                out.append(RecalledFinding(
                    finding=ProjectFinding(
                        project_id=r[0], rule_id=r[1], delta_class=r[2], summary=r[3],
                        first_seen_at=str(r[4]), last_seen_at=str(r[5]),
                    ),
                    similarity=sim,
                ))
        return out


# ---------------------------------------------------------------------------
# Factory — pick the best available backend, fall back gracefully
# ---------------------------------------------------------------------------

# Process-level shared in-memory store, so recall works across runs in one process when
# no Postgres is configured (this sandbox / CI). A real pgvector backend persists across
# processes; the in-memory singleton is the closest faithful fallback within a process.
_SHARED_INMEMORY: InMemoryStore | None = None


def _shared_inmemory() -> InMemoryStore:
    global _SHARED_INMEMORY  # noqa: PLW0603
    if _SHARED_INMEMORY is None:
        _SHARED_INMEMORY = InMemoryStore()
    return _SHARED_INMEMORY


def reset_shared_memory() -> None:
    """Test helper: clear the process-level in-memory store between tests."""
    if _SHARED_INMEMORY is not None:
        _SHARED_INMEMORY.clear()


def get_memory_store(dsn: str | None = None) -> MemoryStore:
    """
    Return a MemoryStore. Prefers pgvector if psycopg imports AND a DSN is available AND the
    connection succeeds; otherwise returns the shared in-memory store (clear, non-fatal
    fallback that persists across runs within the process).

    Args:
        dsn: optional DATABASE_URL override; defaults to env DATABASE_URL.

    Returns:
        MemoryStore (check .backend_name to see which one).
    """
    resolved = dsn or os.environ.get("DATABASE_URL")
    if not resolved:
        return _shared_inmemory()
    try:
        import psycopg  # noqa: F401, PLC0415
    except ImportError:
        return _shared_inmemory()
    try:
        return PgVectorStore(resolved)
    except Exception:
        # DB unreachable / DDL failed — memory is additive, never blocking. Fall back.
        return _shared_inmemory()


def store_findings_and_annotate_inputs(*_: Any, **__: Any) -> None:  # pragma: no cover
    """Reserved hook for pipeline wiring; intentionally a no-op placeholder."""
    return None


__all__ = [
    "RECALL_SIMILARITY_THRESHOLD",
    "InMemoryStore",
    "MemoryStore",
    "PgVectorStore",
    "ProjectFinding",
    "RecalledFinding",
    "get_memory_store",
]

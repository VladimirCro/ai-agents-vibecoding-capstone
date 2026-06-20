"""
launchguard.memory — BE-09: per-project gotcha memory (thin, P2).

pgvector-backed recall of prior findings per project so a recurring gap is surfaced as
"seen before". Thin by design — 1–2 recall moments are enough (PLAN.md Day 8).

DESIGN FOR A DB-LESS SANDBOX
----------------------------
Postgres + pgvector (docker-compose.dev.yml) is the production substrate, but it is NOT
reachable in this sandbox and `psycopg` is NOT installed. So:

  - The pgvector backend (store_pgvector.PgVectorStore) is import-guarded: importing this
    package never imports psycopg. It is only loaded if psycopg is present AND a DATABASE_URL
    is configured.
  - The default backend is an in-memory store (store.InMemoryStore) — pure stdlib. Tests run
    against it with a clear marker; memory is ADDITIVE and NEVER blocks a run (PRD).
  - Embeddings use a deterministic, dependency-free hashing embedder (embed.hash_embed) so
    cosine recall works without numpy or a Gemini embeddings call. On a network machine,
    inject a real Gemini text-embedding via set_embedder().

PUBLIC API
----------
  get_memory_store()             → a MemoryStore (PgVector if available, else InMemory)
  MemoryStore.record_finding(...)→ persist a REDACTED finding (§3)
  MemoryStore.recall(...)        → list[RecalledFinding] for the same project (cosine)
  annotate_recurring(...)        → annotate deltas with "recurring — seen on <date>"
"""

from __future__ import annotations

from launchguard.memory.annotate import annotate_recurring
from launchguard.memory.embed import hash_embed, set_embedder
from launchguard.memory.store import (
    InMemoryStore,
    MemoryStore,
    ProjectFinding,
    RecalledFinding,
    get_memory_store,
    reset_shared_memory,
)

__all__ = [
    "InMemoryStore",
    "MemoryStore",
    "ProjectFinding",
    "RecalledFinding",
    "annotate_recurring",
    "get_memory_store",
    "hash_embed",
    "reset_shared_memory",
    "set_embedder",
]

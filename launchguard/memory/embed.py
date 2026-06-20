"""
launchguard.memory.embed — deterministic, dependency-free embedding + cosine similarity.

Why not numpy / Gemini embeddings here?
  - numpy is NOT installed in this sandbox.
  - A real Gemini text-embedding needs network + an API key (deferred to a network machine).

So the DEFAULT embedder is a deterministic hashing embedder: it maps a finding's textual
identity (rule_id + project + redacted summary) into a fixed-dimension float vector using a
stable hash. Same text → same vector (recall is reproducible — AI Operating Principles §6).
This is sufficient for the THIN memory feature: we only need to recall the SAME gap recurring
in the SAME project, which the hash embedder does exactly (identical text → cosine 1.0).

On a network machine, inject a Gemini embedder via set_embedder(); the store uses it
transparently. The 768-dim default matches the architecture's vector(768) column.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable

EMBED_DIM: int = 768

# Pluggable real embedder (e.g. Gemini text-embedding-004). None → hash_embed fallback.
_EMBEDDER: Callable[[str], list[float]] | None = None


def set_embedder(embedder: Callable[[str], list[float]] | None) -> None:
    """Inject (or clear) a real embedder. Default None → deterministic hash_embed."""
    global _EMBEDDER  # noqa: PLW0603
    _EMBEDDER = embedder


def embed(text: str) -> list[float]:
    """Return an embedding for text using the injected embedder, else hash_embed."""
    if _EMBEDDER is not None:
        vec = _EMBEDDER(text)
        return _normalize(vec)
    return hash_embed(text)


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """
    Deterministic hashing embedder: stable text → unit vector of length `dim`.

    Uses sha256 over the text, expanded with a counter to fill `dim` floats, then
    L2-normalized. Identical text yields an identical vector → cosine 1.0 (exact recall).
    No external deps; no randomness.
    """
    vec: list[float] = []
    counter = 0
    while len(vec) < dim:
        h = hashlib.sha256(f"{text}|{counter}".encode()).digest()
        for i in range(0, len(h), 4):
            if len(vec) >= dim:
                break
            chunk = int.from_bytes(h[i:i + 4], "big")
            # Map uint32 → [-1, 1]
            vec.append((chunk / 0xFFFFFFFF) * 2.0 - 1.0)
        counter += 1
    return _normalize(vec)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. Returns 0.0 for zero-length/empty vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


__all__ = ["EMBED_DIM", "cosine_similarity", "embed", "hash_embed", "set_embedder"]

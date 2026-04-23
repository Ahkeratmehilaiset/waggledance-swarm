"""B.7 — Embedding cache with NFC normalization + model-keyed invalidation.

Per v3 §1.15 + v3.1 tweak 3:
  - Cache key = sha256(embedding_model | NFC-normalized-input)
  - Normalization MUST match actual embedder input
  - Model change invalidates cache automatically (key includes model)
  - Routing decisions are NOT cached (would be invalidated by manifest swap)

Backend: SQLite (simple, persistent, thread-safe with WAL).

Invariant (critical per v3.1 tweak 3):
  canonicalize_for_embedding() here MUST equal the normalization used
  by the production embed call. If either changes, both must change,
  and `embedding_input_normalization_version` must be bumped.

Usage:
    cache = EmbeddingCache(path="data/embedding_cache.sqlite")
    vec = cache.get("nomic-embed-text:v1.5", query_text)
    if vec is None:
        vec = ollama_embed(query_text)
        cache.put("nomic-embed-text:v1.5", query_text, vec)
    use_vec(vec)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


NORMALIZATION_VERSION = "v1"  # bump if canonicalize_for_embedding changes


def canonicalize_for_embedding(text: str) -> str:
    """THE canonical function. Must match what actually goes to the embedder.

    NFC normalization + strip whitespace. No lowercasing, no stemming,
    no punctuation stripping — those would lose semantic content.
    """
    return unicodedata.normalize("NFC", text).strip()


def cache_key(embedding_model: str, text: str) -> str:
    """Compute sha256 cache key per v3.1 tweak 3."""
    normalized = canonicalize_for_embedding(text)
    normalized_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{embedding_model}|{normalized_hash}|{NORMALIZATION_VERSION}".encode("utf-8")
    ).hexdigest()


class EmbeddingCache:
    """SQLite-backed embedding cache, thread-safe via WAL mode."""

    def __init__(
        self,
        path: str | Path,
        ttl_days: int = 90,
        max_entries: int = 100_000,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days
        self.max_entries = max_entries
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    key TEXT PRIMARY KEY,
                    embedding_model TEXT NOT NULL,
                    normalization_version TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_hit_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_last_hit_at
                ON embeddings(last_hit_at)
            """)

    @contextmanager
    def _conn(self):
        # WAL for concurrent read+write safety on Windows
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get(self, embedding_model: str, text: str) -> Optional[list[float]]:
        """Return cached vector or None. Updates last_hit_at on hit."""
        key = cache_key(embedding_model, text)
        now = time.time()
        ttl_cutoff = now - (self.ttl_days * 86400)
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT vector_json, created_at, normalization_version "
                    "FROM embeddings WHERE key = ? AND embedding_model = ?",
                    (key, embedding_model),
                ).fetchone()
                if not row:
                    return None
                vector_json, created_at, norm_ver = row
                if created_at < ttl_cutoff:
                    # Expired
                    conn.execute("DELETE FROM embeddings WHERE key = ?", (key,))
                    return None
                if norm_ver != NORMALIZATION_VERSION:
                    # Stale normalization — re-embed needed
                    conn.execute("DELETE FROM embeddings WHERE key = ?", (key,))
                    return None
                conn.execute(
                    "UPDATE embeddings SET last_hit_at = ? WHERE key = ?",
                    (now, key),
                )
                return json.loads(vector_json)
        except sqlite3.Error:
            # Corrupted cache — graceful fall-through
            return None

    def put(self, embedding_model: str, text: str, vector: list[float]) -> None:
        key = cache_key(embedding_model, text)
        now = time.time()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings "
                    "(key, embedding_model, normalization_version, "
                    " vector_json, created_at, last_hit_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (key, embedding_model, NORMALIZATION_VERSION,
                     json.dumps(vector), now, now),
                )
                # Evict oldest if over max_entries
                count = conn.execute(
                    "SELECT COUNT(*) FROM embeddings"
                ).fetchone()[0]
                if count > self.max_entries:
                    to_evict = count - self.max_entries
                    conn.execute(
                        "DELETE FROM embeddings WHERE key IN ("
                        "  SELECT key FROM embeddings ORDER BY last_hit_at ASC LIMIT ?"
                        ")",
                        (to_evict,),
                    )
        except sqlite3.Error:
            # Cache failure should not break caller
            pass

    def stats(self) -> dict:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*), MIN(created_at), MAX(last_hit_at) FROM embeddings"
                ).fetchone()
                count, oldest, newest = row
                return {
                    "entries": count,
                    "oldest_age_days": (time.time() - oldest) / 86400 if oldest else None,
                    "newest_age_s": (time.time() - newest) if newest else None,
                    "normalization_version": NORMALIZATION_VERSION,
                }
        except sqlite3.Error as e:
            return {"error": str(e)}

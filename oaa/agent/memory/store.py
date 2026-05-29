"""Structured memory store — Chroma (vectors) + SQLite (metadata).

Implements the P1/P2 memory system: semantic search with importance-based
eviction, reference tracking, and graded context injection.
"""

import json
import os
import sqlite3
import time
import uuid
from typing import Optional

from .embedding import Embedder, EMBEDDING_DIM
from .models import (
    MAX_STORE, EVICT_DAYS, DIGEST_THRESHOLD,
    INJECT_TOP_K, INJECT_FULL,
    MemoryItem, SearchResult,
    STATUS_ACTIVE, STATUS_REFERENCED, STATUS_DIGESTED,
    importance_from_text,
)
from ...logging_config import get_logger

logger = get_logger("agent.memory.store")


class MemoryStore:
    """Memory store backed by Chroma (vector index) + SQLite (metadata).

    Usage::

        mem = MemoryStore(data_dir)
        mem.add("恒总是做联轴器出口贸易的", mem_type="fact")
        results = mem.search("用户做什么业务")
    """

    def __init__(self, data_dir: str, top_k: int = INJECT_TOP_K):
        self._data_dir = data_dir
        self._top_k = max(3, min(top_k, 10))

        # Embedder + auto-download model in background if missing
        self._embedder = Embedder(data_dir)
        if not self._embedder._session:
            try:
                from .embedding import download_model
                import threading
                threading.Thread(target=download_model, args=(data_dir,), daemon=True).start()
            except Exception:
                pass

        # SQLite
        self._db_path = os.path.join(data_dir, "memory", "memory_store.db")
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

        # Chroma
        self._chroma_dir = os.path.join(data_dir, "memory", "chroma")
        self._collection = None
        self._init_chroma()

    # ── initialisation ───────────────────────────────────────────────

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                summary TEXT DEFAULT '',
                mem_type TEXT DEFAULT 'fact',
                source TEXT DEFAULT 'agent',
                importance REAL DEFAULT 0.5,
                status TEXT DEFAULT 'active',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                last_accessed REAL DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                ref_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.commit()
        conn.close()

    def _init_chroma(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=self._chroma_dir)
            # Try getting existing collection
            self._collection = client.get_or_create_collection(
                name="memory_store",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.warning("Chroma init failed: %s — memory still works via SQLite", exc)
            self._collection = None

    # ── CRUD ─────────────────────────────────────────────────────────

    def add(self, text: str, mem_type: str = "fact",
            source: str = "agent", importance: float = 0.0,
            tags: list[str] | None = None,
            summary: str = "",
            metadata: dict | None = None) -> str:
        """Add a new memory item.  Returns the new item's ID."""
        mem_id = uuid.uuid4().hex[:12]
        now = time.time()
        importance = importance or importance_from_text(text)

        embedding = self._embedder.encode(text)

        # SQLite
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """INSERT INTO memories
               (id, text, summary, mem_type, source, importance,
                created_at, updated_at, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mem_id, text, summary, mem_type, source, importance,
             now, now, json.dumps(tags or []), json.dumps(metadata or {})),
        )
        conn.commit()
        conn.close()

        # Chroma
        if self._collection is not None:
            try:
                self._collection.add(
                    ids=[mem_id],
                    embeddings=[embedding],
                    metadatas=[{
                        "mem_type": mem_type, "source": source,
                        "importance": importance, "created_at": now,
                    }],
                    documents=[text],
                )
            except Exception as exc:
                logger.warning("Chroma add failed: %s", exc)

        # Evict if over capacity
        self._maybe_evict()

        logger.debug("Memory added: %s (%s, importance=%.2f)", mem_id, mem_type, importance)
        return mem_id

    def search(self, query: str, top_k: int = 0,
               mem_type: str = "") -> list[SearchResult]:
        """Semantic search across memory. Returns graded results.

        Args:
            query: Natural-language query text.
            top_k: Number of results (defaults to store top_k).
            mem_type: Optional filter by memory type.

        Returns:
            List of ``SearchResult`` objects, **highest score first**.
            First ``INJECT_FULL`` results get the full ``text``; the rest
            get only the ``summary``.
        """
        k = top_k or self._top_k
        query_embedding = self._embedder.encode(query)

        results: list[SearchResult] = []

        # Chroma vector search
        if self._collection is not None:
            try:
                where = {"mem_type": mem_type} if mem_type else None
                raw = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=k * 2,  # oversample for filtering
                    where=where,
                )
                if raw and raw["ids"]:
                    for i, mem_id in enumerate(raw["ids"][0]):
                        score = raw["distances"][0][i] if raw.get("distances") else 0
                        # Chroma returns cosine distance; convert to similarity
                        sim = max(0.0, min(1.0, 1.0 - score))
                        if sim < 0.15:
                            continue
                        item = self._get_by_id(mem_id)
                        if item:
                            results.append(SearchResult(item=item, score=sim))
            except Exception as exc:
                logger.warning("Chroma search failed: %s", exc)

        # Fallback: SQLite keyword search when Chroma is unavailable
        if not results:
            results = self._keyword_search(query, k, mem_type)

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)

        # Update access state for top results
        for r in results[:k]:
            self._mark_accessed(r.item.id)

        # Grade: first INJECT_FULL get full text, rest get summary
        for i, r in enumerate(results[:k]):
            if i < INJECT_FULL:
                r.full_text = r.item.text
            else:
                r.full_text = r.item.summary or _auto_summarise(r.item.text)

        return results[:k]

    def _get_by_id(self, mem_id: str) -> Optional[MemoryItem]:
        """Fetch a single memory item from SQLite by ID."""
        conn = sqlite3.connect(self._db_path)
        row = conn.execute("SELECT * FROM memories WHERE id=?", (mem_id,)).fetchone()
        conn.close()
        if row is None:
            return None
        return self._row_to_item(row)

    def _mark_accessed(self, mem_id: str):
        """Increment access count, update last_accessed and importance."""
        now = time.time()
        decay = 0.99
        conn = sqlite3.connect(self._db_path)
        row = conn.execute("SELECT access_count, importance FROM memories WHERE id=?",
                           (mem_id,)).fetchone()
        if row:
            new_importance = min(1.0, row[1] * decay + 0.02)
            conn.execute(
                "UPDATE memories SET access_count=access_count+1, last_accessed=?, "
                "importance=? WHERE id=?",
                (now, new_importance, mem_id),
            )
            conn.commit()
        conn.close()

    def mark_referenced(self, mem_id: str):
        """Mark a memory as ``referenced`` — the agent cited it."""
        conn = sqlite3.connect(self._db_path)
        row = conn.execute("SELECT ref_count FROM memories WHERE id=?",
                           (mem_id,)).fetchone()
        if row:
            new_count = row[0] + 1
            new_status = STATUS_DIGESTED if new_count >= DIGEST_THRESHOLD else STATUS_REFERENCED
            conn.execute(
                "UPDATE memories SET ref_count=?, status=?, updated_at=? WHERE id=?",
                (new_count, new_status, time.time(), mem_id),
            )
            conn.commit()
        conn.close()

    # ── injection ────────────────────────────────────────────────────

    def get_injection_text(self, query_hint: str = "") -> str:
        """Build a system-prompt injection block from top *top_k* memories.

        Args:
            query_hint: Optional context to guide which memories are
                        relevant (e.g., the user's current message).

        Returns:
            Markdown-formatted memory block, or empty string if empty.
        """
        query = query_hint or "current task context"
        results = self.search(query)

        if not results:
            return ""

        lines = ["# 🧠 记忆检索", ""]
        for i, r in enumerate(results):
            label = f"[{r.item.mem_type.upper()}]"
            if r.item.source == "user":
                label += " 👤"
            importance_bar = "★" * max(1, round(r.item.importance * 5))
            if i < INJECT_FULL:
                lines.append(f"{label} {importance_bar} 相似度 {r.score:.0%}")
                lines.append(f"{r.full_text[:500]}")
            else:
                lines.append(f"{label} {r.full_text[:200]}")
            lines.append("")

        return "\n".join(lines)

    # ── eviction ─────────────────────────────────────────────────────

    def count(self) -> int:
        conn = sqlite3.connect(self._db_path)
        cnt = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        return cnt

    def _maybe_evict(self):
        """Check capacity and evict lowest-importance + oldest items."""
        total = self.count()
        if total <= MAX_STORE:
            return

        conn = sqlite3.connect(self._db_path)
        now = time.time()
        cutoff = now - EVICT_DAYS * 86400

        # Find candidates: importance < 0.3 AND last_accessed > 30 days
        evict_ids = conn.execute(
            """SELECT id FROM memories
               WHERE importance < 0.3 AND last_accessed < ?
               ORDER BY importance ASC, last_accessed ASC
               LIMIT ?""",
            (cutoff, total - MAX_STORE + 100),
        ).fetchall()

        conn.close()

        evicted = 0
        for (mem_id,) in evict_ids:
            self.delete(mem_id)
            evicted += 1

        if evicted:
            logger.info("Evicted %d low-importance memories", evicted)

    def delete(self, mem_id: str):
        """Delete a memory item from both SQLite and Chroma."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        conn.commit()
        conn.close()

        if self._collection is not None:
            try:
                self._collection.delete(ids=[mem_id])
            except Exception:
                pass

    def delete_by_user(self, mem_id: str) -> bool:
        """User-facing delete — also logs it for audit."""
        item = self._get_by_id(mem_id)
        if item is None:
            return False
        self.delete(mem_id)
        logger.info("User deleted memory: %s (%s)", mem_id, item.text[:60])
        return True

    # ── query / list ─────────────────────────────────────────────────

    def list_by_status(self, status: str = "") -> list[MemoryItem]:
        """List memories, optionally filtered by status."""
        conn = sqlite3.connect(self._db_path)
        if status:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status=? ORDER BY importance DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY importance DESC"
            ).fetchall()
        conn.close()
        return [self._row_to_item(r) for r in rows]

    def get_stats(self) -> dict:
        """Aggregate statistics for the UI."""
        conn = sqlite3.connect(self._db_path)
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM memories GROUP BY status"
        ).fetchall())
        by_type = dict(conn.execute(
            "SELECT mem_type, COUNT(*) FROM memories GROUP BY mem_type"
        ).fetchall())
        conn.close()
        return {
            "total": total,
            "by_status": by_status,
            "by_type": by_type,
            "capacity": MAX_STORE,
        }

    # ── internals ────────────────────────────────────────────────────

    def _row_to_item(self, row: sqlite3.Row | tuple) -> MemoryItem:
        """Convert a SQLite row to a MemoryItem."""
        if hasattr(row, "keys"):
            return MemoryItem(
                id=row[0], text=row[1], summary=row[2], mem_type=row[3],
                source=row[4], importance=row[5], status=row[6],
                created_at=row[7], updated_at=row[8], last_accessed=row[9],
                access_count=row[10], ref_count=row[11],
                tags=json.loads(row[12] or "[]"),
                metadata=json.loads(row[13] or "{}"),
            )
        return MemoryItem(
            id=row[0], text=row[1], summary=row[2], mem_type=row[3],
            source=row[4], importance=row[5], status=row[6],
            created_at=row[7], updated_at=row[8], last_accessed=row[9],
            access_count=row[10], ref_count=row[11],
            tags=json.loads(row[12] or "[]"),
            metadata=json.loads(row[13] or "{}"),
        )

    def _keyword_search(self, query: str, limit: int,
                        mem_type: str = "") -> list[SearchResult]:
        """Fallback keyword search when Chroma is not available."""
        conn = sqlite3.connect(self._db_path)
        keywords = query.split()
        if mem_type:
            sql = "SELECT * FROM memories WHERE mem_type=? AND (%s) ORDER BY importance DESC LIMIT ?"
            params = [mem_type]
        else:
            sql = "SELECT * FROM memories WHERE %s ORDER BY importance DESC LIMIT ?"
            params = []
        like_clauses = []
        for kw in keywords:
            like_clauses.append("(text LIKE ? OR summary LIKE ? OR tags LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
        sql = sql % " OR ".join(like_clauses) if like_clauses else sql.replace(" AND %s", "")
        params.append(limit)
        rows = conn.execute(sql, params).fetchall() if like_clauses else []
        conn.close()
        return [SearchResult(item=self._row_to_item(r), score=0.5) for r in rows]


def _auto_summarise(text: str, max_len: int = 120) -> str:
    """Truncate and clean up text for summary use."""
    cleaned = text.replace("\n", " ").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rsplit("。", 1)[0] + "。"

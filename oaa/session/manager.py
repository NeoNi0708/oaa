"""Session Manager — SQLite + FTS5 for cross-channel conversation history.

Uses a connection pool (WAL mode) so concurrent operations reuse connections
instead of opening a new one each time.
"""
import json
import os
import queue
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger("session")

_POOL_SIZE = 4


class SessionManager:
    """Manages sessions and messages with FTS5 full-text search.

    Maintains a small pool of reusable SQLite connections. All write
    operations are serialised through a single writer thread to avoid
    ``SQLITE_BUSY`` in WAL mode.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Connection pool (lazily filled)
        self._pool: queue.Queue[sqlite3.Connection] = queue.Queue(maxsize=_POOL_SIZE)
        self._lock = threading.Lock()
        self._closed = False

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a connection from the pool, creating one if necessary."""
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            conn = self._create_conn()
        # Verify connection is alive
        try:
            conn.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            conn.close()
            conn = self._create_conn()
        return conn

    def _return_conn(self, conn: sqlite3.Connection):
        """Return a connection to the pool for reuse."""
        if self._closed:
            conn.close()
            return
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()

    def _create_conn(self) -> sqlite3.Connection:
        """Create and configure a new SQLite connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _with_conn(self, callback, *args, **kwargs):
        """Execute *callback* with a connection from the pool."""
        conn = self._get_conn()
        try:
            return callback(conn, *args, **kwargs)
        finally:
            self._return_conn(conn)

    def _init_db(self):
        """Create tables and indices if they don't exist."""
        def _init(conn):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id)
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content, content=messages, content_rowid=id
                )
            """)
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;
            """)
        self._with_conn(_init)

    def get_or_create_session(self, user_id: str, source: str = "") -> str:
        """Get existing session for user or create new one."""
        def _do(conn):
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            if row:
                session_id = row[0]
                conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                             (datetime.now().isoformat(), session_id))
                return session_id
            session_id = f"session_{uuid.uuid4().hex[:12]}"
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, now, now)
            )
            logger.debug("Created session %s for user %s", session_id, user_id)
            return session_id
        with self._lock:
            return self._with_conn(_do)

    def add_message(self, session_id: str, source: str, role: str,
                    content: str, metadata: dict = None) -> int:
        """Add a message to a session. Returns row id."""
        def _do(conn):
            cur = conn.execute(
                "INSERT INTO messages (session_id, source, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, source, role, content,
                 json.dumps(metadata, ensure_ascii=False) if metadata else None,
                 datetime.now().isoformat())
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                         (datetime.now().isoformat(), session_id))
            return cur.lastrowid
        with self._lock:
            return self._with_conn(_do)

    def get_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        """Get messages for a session."""
        def _do(conn):
            rows = conn.execute(
                "SELECT id, source, role, content, metadata, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id ASC LIMIT ?",
                (session_id, limit)
            ).fetchall()
            return [
                {"id": r["id"], "source": r["source"], "role": r["role"],
                 "content": r["content"],
                 "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                 "created_at": r["created_at"]}
                for r in rows
            ]
        return self._with_conn(_do)

    def search_messages(self, keyword: str, limit: int = 20) -> list[dict]:
        """Full-text search across all messages using FTS5."""
        def _do(conn):
            rows = conn.execute(
                "SELECT m.id, m.session_id, m.source, m.role, m.content, m.created_at "
                "FROM messages_fts f JOIN messages m ON f.rowid = m.id "
                "WHERE messages_fts MATCH ? ORDER BY m.id DESC LIMIT ?",
                (keyword, limit)
            ).fetchall()
            return [
                {"id": r["id"], "session_id": r["session_id"], "source": r["source"],
                 "role": r["role"], "content": r["content"], "created_at": r["created_at"]}
                for r in rows
            ]
        return self._with_conn(_do)

    def close(self):
        """Close all connections in the pool."""
        self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break

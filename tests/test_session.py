"""Tests for SessionManager — including FTS5 search."""
import os
import tempfile
from oaa.session.manager import SessionManager


def test_session_crud():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "test.db")
        sm = SessionManager(db)

        sid = sm.get_or_create_session("user1", "desktop")
        assert sid.startswith("session_")

        msg_id = sm.add_message(sid, "desktop", "user", "Hello world")
        assert msg_id > 0

        msgs = sm.get_messages(sid)
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"

        # Same user reuses session
        sid2 = sm.get_or_create_session("user1", "desktop")
        assert sid2 == sid


def test_fts5_search():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "test.db")
        sm = SessionManager(db)

        sid = sm.get_or_create_session("user1")
        sm.add_message(sid, "desktop", "user", "Searching for apples")
        sm.add_message(sid, "desktop", "assistant", "I found apples")
        sm.add_message(sid, "desktop", "user", "What about oranges?")

        results = sm.search_messages("apples")
        assert len(results) >= 1
        assert any("apples" in r.get("content", "") for r in results)

        results = sm.search_messages("oranges")
        assert len(results) >= 1


def test_empty_search():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "test.db")
        sm = SessionManager(db)

        sid = sm.get_or_create_session("user1")
        sm.add_message(sid, "desktop", "user", "hello")

        results = sm.search_messages("nonexistent_keyword_xyz")
        assert len(results) == 0


def test_sessions_separated():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "test.db")
        sm = SessionManager(db)

        sid_a = sm.get_or_create_session("alice", "desktop")
        sid_b = sm.get_or_create_session("bob", "desktop")
        assert sid_a != sid_b

        sm.add_message(sid_a, "desktop", "user", "Alice message")
        sm.add_message(sid_b, "desktop", "user", "Bob message")

        assert len(sm.get_messages(sid_a)) == 1
        assert len(sm.get_messages(sid_b)) == 1

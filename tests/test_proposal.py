"""Test proposal system — ProposalStore persistence + ProposalExecutor verify/rollback.

These tests cover the P0 items from the evolution factory test plan:
  1. ProposalStore CRUD + JSON persistence
  2. ProposalExecutor verify success → proposal done
  3. ProposalExecutor verify failure → rollback execution → proposal failed
"""
import json
import os
import tempfile
import time

import pytest

from oaa.agent.proposal import (
    Proposal,
    ProposalStore,
    ProposalExecutor,
    STATUS_PENDING,
    STATUS_DONE,
    STATUS_FAILED,
    TYPE_TOOL_FIX,
)
from oaa.agent.handler import BaseHandler


# ---------------------------------------------------------------------------
# Mock handler — lets us control what each tool returns
# ---------------------------------------------------------------------------

class MockHandler(BaseHandler):
    """A handler whose tools always succeed / fail on demand."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []  # (tool, args) per dispatch
        self._fail_on: set[str] = set()           # tool names that should raise

    def fail_tool(self, tool_name: str):
        """Make dispatch for *tool_name* raise RuntimeError."""
        self._fail_on.add(tool_name)

    async def dispatch(self, tool_name: str, args: dict):
        self.calls.append((tool_name, args))
        if tool_name in self._fail_on:
            raise RuntimeError(f"Mock failure for {tool_name}")
        return {"status": "ok", "content": f"{tool_name} executed"}


# ===================================================================
# ProposalStore tests
# ===================================================================

@pytest.mark.asyncio
async def test_create_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        prop = Proposal(type="tool_fix", title="test", problem="err", benefit="fix",
                        target="test_tool", actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}])
        pid = await store.add(prop)
        assert pid, "add() should return a non-empty ID"

        loaded = store.get(pid)
        assert loaded is not None
        assert loaded["type"] == "tool_fix"
        assert loaded["title"] == "test"
        assert loaded["target"] == "test_tool"
        assert loaded["status"] == STATUS_PENDING
        assert loaded["created_at"] > 0


@pytest.mark.asyncio
async def test_persistence_across_reload():
    """Data written by one instance is readable by a new instance (JSON file)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        prop = Proposal(type="tool_fix", title="persist-test", target="x")
        pid = await store.add(prop)

        # Create a new store pointing at the same directory
        store2 = ProposalStore(tmp)
        loaded = store2.get(pid)
        assert loaded is not None
        assert loaded["title"] == "persist-test"


@pytest.mark.asyncio
async def test_update_status():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        pid = await store.add(Proposal(type="tool_fix", title="status-test", target="x"))
        ok = await store.update_status(pid, STATUS_DONE)
        assert ok
        loaded = store.get(pid)
        assert loaded["status"] == STATUS_DONE


@pytest.mark.asyncio
async def test_list_pending():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        await store.add(Proposal(type="tool_fix", title="p1", target="a"))
        await store.add(Proposal(type="tool_fix", title="p2", target="b"))
        # Move one to done
        all_p = store.all_proposals()
        await store.update_status(all_p[0]["id"], STATUS_DONE)

        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0]["title"] == "p2"


@pytest.mark.asyncio
async def test_has_pending_for_target():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        assert not store.has_pending_for_target("my_tool")
        await store.add(Proposal(type="tool_fix", title="dup-test", target="my_tool"))
        assert store.has_pending_for_target("my_tool")
        assert store.has_pending_for_target("my_tool", "tool_fix")
        assert not store.has_pending_for_target("my_tool", "install_dep")


@pytest.mark.asyncio
async def test_count_pending():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        assert store.count_pending() == 0
        p1 = await store.add(Proposal(type="tool_fix", title="c1", target="a"))
        await store.add(Proposal(type="tool_fix", title="c2", target="b"))
        assert store.count_pending() == 2
        await store.update_status(p1, STATUS_DONE)
        assert store.count_pending() == 1


@pytest.mark.asyncio
async def test_all_proposals_returns_copy():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        await store.add(Proposal(type="tool_fix", title="a1", target="x"))
        await store.add(Proposal(type="tool_fix", title="a2", target="y"))
        assert len(store.all_proposals()) == 2


@pytest.mark.asyncio
async def test_get_pending_proposal_text():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        assert store.get_pending_proposal_text() == ""
        await store.add(Proposal(type="tool_fix", title="test-title", target="x",
                           problem="something broke", benefit="it works again",
                           actions=[{"tool": "shell_run", "args": {"command": "echo fix"}}]))
        text = store.get_pending_proposal_text()
        assert "test-title" in text
        assert "something broke" in text
        assert "shell_run" in text


# ===================================================================
# ProposalExecutor verify/rollback 测试
# ===================================================================

class TestProposalExecutor:

    @pytest.mark.asyncio
    async def test_basic_execution(self):
        """Basic execution: all actions succeed → STATUS_DONE."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-1",
            "actions": [
                {"tool": "read_own_source", "args": {"path": "test.py"}},
                {"tool": "shell_run", "args": {"command": "echo done"}},
            ],
            "status": STATUS_PENDING,
        }
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_DONE
        assert len(result.get("result", [])) > 0
        assert handler.calls[0][0] == "read_own_source"
        assert handler.calls[1][0] == "shell_run"

    @pytest.mark.asyncio
    async def test_verify_success(self):
        """Action with verify that passes → STATUS_DONE."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-2",
            "actions": [
                {
                    "tool": "file_patch",
                    "args": {"path": "test.py", "patch": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"},
                    "verify": {"tool": "shell_run", "args": {"command": "python -c \"import test\""}},
                }
            ],
            "status": STATUS_PENDING,
        }
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_DONE
        assert handler.calls[0][0] == "file_patch"
        assert handler.calls[1][0] == "shell_run"

    @pytest.mark.asyncio
    async def test_verify_fail_triggers_rollback(self):
        """Verify fails and rollback succeeds → STATUS_FAILED with step error."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-3",
            "actions": [
                {
                    "tool": "file_patch",
                    "args": {"path": "test.py", "patch": "..."},
                    "verify": {"tool": "shell_run", "args": {"command": "python -c \"import test\""}},
                    "rollback": {"tool": "shell_run", "args": {"command": "git checkout test.py"}},
                }
            ],
            "status": STATUS_PENDING,
        }
        handler.fail_tool("shell_run")  # make verify fail
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_FAILED
        # The rollback should have been called
        rollback_call = ("shell_run", {"command": "git checkout test.py"})
        assert rollback_call in handler.calls, f"Rollback not called. Calls: {handler.calls}"

    @pytest.mark.asyncio
    async def test_verify_fail_no_rollback(self):
        """Verify fails and no rollback → STATUS_FAILED gracefully."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-4",
            "actions": [
                {
                    "tool": "file_patch",
                    "args": {"path": "test.py", "patch": "..."},
                    "verify": {"tool": "shell_run", "args": {"command": "python -c \"import test\""}},
                }
            ],
            "status": STATUS_PENDING,
        }
        handler.fail_tool("shell_run")
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_FAILED
        assert "验证失败" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_action_failure_stops_execution(self):
        """An action that errors mid-way → STATUS_FAILED, no further actions."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-5",
            "actions": [
                {"tool": "read_own_source", "args": {"path": "test.py"}},
                {"tool": "file_patch", "args": {"path": "test.py", "patch": "..."}},
                {"tool": "shell_run", "args": {"command": "echo never"}},
            ],
            "status": STATUS_PENDING,
        }
        handler.fail_tool("file_patch")
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_FAILED
        # Last action should NOT have been called
        tool_names = [c[0] for c in handler.calls]
        assert "shell_run" not in tool_names, f"shell_run should not execute: {tool_names}"

    @pytest.mark.asyncio
    async def test_executed_at_set(self):
        """On success, executed_at should be set."""
        handler = MockHandler()
        executor = ProposalExecutor()
        proposal = {
            "id": "exec-test-6",
            "actions": [{"tool": "shell_run", "args": {"command": "echo ok"}}],
            "status": STATUS_PENDING,
        }
        result = await executor.execute(proposal, handler)
        assert result["status"] == STATUS_DONE
        assert result.get("executed_at") is not None

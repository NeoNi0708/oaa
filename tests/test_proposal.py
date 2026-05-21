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

    async def dispatch(self, tool_name: str, args: dict) -> dict:
        self.calls.append((tool_name, args))
        if tool_name in self._fail_on:
            raise RuntimeError(f"Simulated failure for {tool_name}")
        return {"status": "ok", "msg": f"{tool_name} executed"}


# ===================================================================
# ProposalStore 持久化测试
# ===================================================================

class TestProposalStore:

    def _make_store(self) -> tuple[ProposalStore, str]:
        tmp = tempfile.mkdtemp()
        store = ProposalStore(tmp)
        return store, tmp

    def test_create_and_get(self):
        store, tmp = self._make_store()
        prop = Proposal(type="tool_fix", title="test", problem="err", benefit="fix",
                        target="test_tool", actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}])
        pid = store.add(prop)
        assert pid, "add() should return a non-empty ID"

        loaded = store.get(pid)
        assert loaded is not None
        assert loaded["type"] == "tool_fix"
        assert loaded["title"] == "test"
        assert loaded["target"] == "test_tool"
        assert loaded["status"] == STATUS_PENDING
        assert loaded["created_at"] > 0

    def test_persistence_across_reload(self):
        """Data written by one instance is readable by a new instance (JSON file)."""
        store, tmp = self._make_store()
        prop = Proposal(type="tool_fix", title="persist-test", target="x")
        pid = store.add(prop)

        # Create a new store pointing at the same directory
        store2 = ProposalStore(tmp)
        loaded = store2.get(pid)
        assert loaded is not None
        assert loaded["title"] == "persist-test"

    def test_update_status(self):
        store, tmp = self._make_store()
        pid = store.add(Proposal(type="tool_fix", title="status-test", target="x"))
        ok = store.update_status(pid, STATUS_DONE)
        assert ok
        loaded = store.get(pid)
        assert loaded["status"] == STATUS_DONE

    def test_list_pending(self):
        store, tmp = self._make_store()
        store.add(Proposal(type="tool_fix", title="p1", target="a"))
        store.add(Proposal(type="tool_fix", title="p2", target="b"))
        # Move one to done
        all_p = store.all_proposals()
        store.update_status(all_p[0]["id"], STATUS_DONE)

        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0]["title"] == "p2"

    def test_has_pending_for_target(self):
        store, tmp = self._make_store()
        assert not store.has_pending_for_target("my_tool")
        store.add(Proposal(type="tool_fix", title="dup-test", target="my_tool"))
        assert store.has_pending_for_target("my_tool")
        assert store.has_pending_for_target("my_tool", "tool_fix")
        assert not store.has_pending_for_target("my_tool", "install_dep")

    def test_count_pending(self):
        store, tmp = self._make_store()
        assert store.count_pending() == 0
        p1 = store.add(Proposal(type="tool_fix", title="c1", target="a"))
        store.add(Proposal(type="tool_fix", title="c2", target="b"))
        assert store.count_pending() == 2
        store.update_status(p1, STATUS_DONE)
        assert store.count_pending() == 1

    def test_all_proposals_returns_copy(self):
        store, tmp = self._make_store()
        store.add(Proposal(type="tool_fix", title="a1", target="x"))
        store.add(Proposal(type="tool_fix", title="a2", target="y"))
        assert len(store.all_proposals()) == 2

    def test_get_pending_proposal_text(self):
        store, tmp = self._make_store()
        assert store.get_pending_proposal_text() == ""
        store.add(Proposal(type="tool_fix", title="test-title", target="x",
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
        """Actions run in sequence, proposal ends with status=done."""
        handler = MockHandler()
        executor = ProposalExecutor()
        prop = {
            "id": "test_1",
            "type": "tool_fix",
            "actions": [
                {"tool": "shell_run", "args": {"command": "echo 1"}},
                {"tool": "shell_run", "args": {"command": "echo 2"}},
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_DONE
        assert len(handler.calls) == 2

    @pytest.mark.asyncio
    async def test_verify_success(self):
        """Verify step passes → action marked verified, proposal done."""
        handler = MockHandler()
        executor = ProposalExecutor()
        prop = {
            "id": "test_2",
            "type": "tool_fix",
            "actions": [
                {
                    "tool": "shell_run",
                    "args": {"command": "echo fix"},
                    "verify": {"tool": "code_exec", "args": {"code": "print('ok')"}},
                },
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_DONE
        # 2 calls: main action + verify
        assert len(handler.calls) == 2
        assert handler.calls[1][0] == "code_exec"

    @pytest.mark.asyncio
    async def test_verify_fail_triggers_rollback(self):
        """Verify fails → rollback runs → proposal marked failed."""
        handler = MockHandler()
        # Make verify fail
        handler.fail_tool("code_exec")
        executor = ProposalExecutor()
        prop = {
            "id": "test_3",
            "type": "tool_fix",
            "actions": [
                {
                    "tool": "shell_run",
                    "args": {"command": "echo fix"},
                    "verify": {"tool": "code_exec", "args": {"code": "print('verify')"}},
                    "rollback": {"tool": "self_improve", "args": {"path": "x.py", "old_content": "a", "new_content": "b"}},
                },
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_FAILED
        assert "验证失败" in result.get("error", "")
        # 3 calls: main action + verify (fails) + rollback
        assert len(handler.calls) == 3
        assert handler.calls[2][0] == "self_improve"

    @pytest.mark.asyncio
    async def test_verify_fail_no_rollback(self):
        """Verify fails, no rollback defined → proposal marked failed, no rollback call."""
        handler = MockHandler()
        handler.fail_tool("code_exec")
        executor = ProposalExecutor()
        prop = {
            "id": "test_4",
            "type": "tool_fix",
            "actions": [
                {
                    "tool": "shell_run",
                    "args": {"command": "echo fix"},
                    "verify": {"tool": "code_exec", "args": {"code": "print('verify')"}},
                    # no rollback
                },
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_FAILED
        # 2 calls: main action + verify (no rollback)
        assert len(handler.calls) == 2

    @pytest.mark.asyncio
    async def test_action_failure_stops_execution(self):
        """Action itself fails → proposal marked failed, subsequent actions skipped."""
        handler = MockHandler()
        handler.fail_tool("shell_run")
        executor = ProposalExecutor()
        prop = {
            "id": "test_5",
            "type": "tool_fix",
            "actions": [
                {"tool": "shell_run", "args": {"command": "echo first"}},
                {"tool": "shell_run", "args": {"command": "echo second"}},
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_FAILED
        # Only first action attempted
        assert len(handler.calls) == 1

    @pytest.mark.asyncio
    async def test_executed_at_set(self):
        """Successful execution records executed_at timestamp."""
        handler = MockHandler()
        executor = ProposalExecutor()
        prop = {
            "id": "test_6",
            "type": "tool_fix",
            "actions": [
                {"tool": "shell_run", "args": {"command": "echo ok"}},
            ],
        }
        result = await executor.execute(prop, handler)
        assert result["status"] == STATUS_DONE
        assert result.get("executed_at") is not None
        assert isinstance(result["executed_at"], float)

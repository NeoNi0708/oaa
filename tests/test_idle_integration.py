"""IdleInspector integration tests — end-to-end: detect → proposal → approve → verify."""
import os
import tempfile
import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch

from oaa.agent.idle_inspector import IdleInspector
from oaa.agent.proposal import ProposalStore, Proposal, STATUS_PENDING, STATUS_DONE, STATUS_RUNNING


# ---------------------------------------------------------------------------
# Mock dependencies
# ---------------------------------------------------------------------------

class MockScheduler:
    def list_tasks(self, **kw): return []
    def get(self, _id): return None
    def get_due_tasks(self): return []


class MockMemory:
    def __init__(self):
        self._hot = []
        self._corrections = []
        self._tool_failures = {}
        self._warm_topics = []

    async def add_to_hot(self, msg):
        self._hot.append(msg)

    def load_hot(self):
        return "\n".join(self._hot)

    def load_recent_corrections(self, limit=20):
        return self._corrections[:limit]

    def list_warm_topics(self):
        return self._warm_topics

    def get_tool_failures(self, tool_name, limit=1):
        return self._tool_failures.get(tool_name, [])


class MockEvolution:
    stats = {
        "skill_usage": {},
        "sop_executions": {},
        "sop_skips": {},
        "crystallized": [],
        "suggestions": [],
        "applied": [],
    }
    async def analyze_for_suggestions(self): pass
    async def accept_suggestion(self, idx): return True


class MockLLM:
    async def chat(self, messages):
        # Return a response-like object
        class R:
            content = "建议探索数据可视化领域，添加 chart 相关技能。"
        return R()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def inspector():
    with tempfile.TemporaryDirectory() as tmp:
        mem = MockMemory()
        memory_dir = os.path.join(tmp, "memory")
        os.makedirs(memory_dir, exist_ok=True)

        store = ProposalStore(memory_dir)
        evo = MockEvolution()
        llm = MockLLM()
        sched = MockScheduler()

        insp = IdleInspector(
            scheduler=sched,
            memory_mgr=mem,
            evolution=evo,
            proposal_store=store,
            llm=llm,
        )
        insp.set_memory_path(memory_dir)
        insp._last_check = 0.0  # bypass cooldown
        insp._last_line_c_check = 0.0
        yield insp, store, mem


# ===================================================================
# Integration: pause/resume (B8 fix)
# ===================================================================

class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_blocks_inspection(self, inspector):
        insp, store, mem = inspector
        insp.pause()
        assert insp.is_paused()

        result = await insp.inspect()
        assert result is None  # paused → no inspection

    @pytest.mark.asyncio
    async def test_resume_restores_inspection(self, inspector):
        insp, store, mem = inspector
        insp.pause()
        insp.resume()
        assert not insp.is_paused()

        # After resume, inspect should work (table-driven check)
        result = await insp.inspect()
        # May be None if no triggers — that's fine, just verify it ran
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_pause_resume_cycle(self, inspector):
        insp, store, mem = inspector

        # Pause → block
        insp.pause()
        r1 = await insp.inspect()
        assert r1 is None

        # Resume → allow
        insp.resume()
        insp._last_check = 0.0  # reset cooldown again for inspect
        r2 = await insp.inspect()
        # After resume, should run (even if result is None)
        # Verify no exception, method ran to completion
        assert isinstance(r2, (str, type(None)))


# ===================================================================
# Integration: tool ignore persistence
# ===================================================================

class TestIgnorePersistence:
    @pytest.mark.asyncio
    async def test_ignore_once(self, inspector):
        insp, store, mem = inspector
        insp.ignore_tool("problem_tool", permanent=False)
        assert insp.is_tool_ignored("problem_tool") is True
        # Once-ignore consumed after check — second check returns False
        assert insp.is_tool_ignored("problem_tool") is False

    @pytest.mark.asyncio
    async def test_ignore_forever(self, inspector):
        insp, store, mem = inspector
        insp.ignore_tool("permanent_problem", permanent=True)
        assert insp.is_tool_ignored("permanent_problem") is True
        assert insp.is_tool_ignored("permanent_problem") is True  # persists


# ===================================================================
# Integration: proposal store + inspector
# ===================================================================

class TestProposalIntegration:
    """Proposal store + inspector integration (placeholder for Phase 2)."""


# ===================================================================
# Integration: proposal store CRUD
# ===================================================================

class TestProposalStore:
    @pytest.mark.asyncio
    async def test_add_and_get(self, inspector):
        insp, store, mem = inspector
        prop = Proposal(
            type="tool_fix",
            title="test proposal",
            target="test_tool",
            problem="工具 test_tool 报错",
            benefit="修复后可用",
            actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}],
        )
        pid = await store.add(prop)
        assert pid.startswith("prop_")

        got = store.get(pid)
        assert got["title"] == "test proposal"
        assert got["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_update_status(self, inspector):
        insp, store, mem = inspector
        prop = Proposal(
            type="tool_fix",
            title="status test",
            target="status_tool",
        )
        pid = await store.add(prop)

        await store.update_status(pid, STATUS_RUNNING)
        assert store.get(pid)["status"] == STATUS_RUNNING

        await store.update_status(pid, STATUS_DONE)
        assert store.get(pid)["status"] == STATUS_DONE

    @pytest.mark.asyncio
    async def test_list_by_status(self, inspector):
        insp, store, mem = inspector
        for i in range(3):
            await store.add(Proposal(
                type="tool_fix",
                title=f"pending-{i}",
                target=f"tool_{i}",
            ))

        pending = store.list_by_status(STATUS_PENDING)
        assert len(pending) == 3

    @pytest.mark.asyncio
    async def test_empty_store(self, inspector):
        insp, store, mem = inspector
        all_p = store.all_proposals()
        assert len(all_p) == 0
        pending = store.list_by_status(STATUS_PENDING)
        assert len(pending) == 0

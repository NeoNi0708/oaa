"""Tests for ReflectionScheduler — weekly learning cycle."""
import os
import tempfile
import time

import pytest

from oaa.agent.reflection_scheduler import ReflectionScheduler


class _MockLLM:
    async def chat(self, messages):
        class R:
            content = '{"lessons": ["检查路径参数避免 404"], "suggestion": ""}'
        return R()


class _MockMemory:
    def __init__(self):
        self._dir = ""
        self._hot = []
        self._corrections = []

    async def add_to_hot(self, entry):
        self._hot.append(entry)

    def load_recent_corrections(self, limit=10):
        return self._corrections[:limit]

    def load_tool_failures(self, limit=30):
        # Return failures without category (backward compat)
        return [
            {"tool": "file_write", "error": "Permission denied", "category": "unknown", "timestamp": "1"},
            {"tool": "file_write", "error": "Permission denied", "category": "unknown", "timestamp": "2"},
        ]


class _MockEvolution:
    stats = {
        "skill_usage": {"web_search": 8, "excel_xlsx": 3},
        "crystallized": [{"name": "web_search", "created": "2026-01-01"}],
    }


class _MockProposalStore:
    def __init__(self):
        self._proposals = []
        self._targets = set()

    def has_pending_for_target(self, target, ptype):
        return target in self._targets

    async def add(self, proposal):
        self._proposals.append(proposal)
        self._targets.add(proposal.target)


class TestReflectionScheduler:
    @pytest.mark.asyncio
    async def test_empty_data_no_error(self):
        """Reflection with no data should not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            rs = ReflectionScheduler(tmp, llm=None)
            # Should not raise
            await rs._run_reflection()

    @pytest.mark.asyncio
    async def test_llm_reflect_returns_lessons(self):
        """LLM analysis should parse the JSON output correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            mem = _MockMemory()
            rs = ReflectionScheduler(tmp, memory_mgr=mem, llm=_MockLLM())
            result = await rs._llm_reflect(
                failures=mem.load_tool_failures(),
                corrections=[],
                skill_usage={"web_search": 8},
                crystallized=[],
            )
            assert result is not None
            assert len(result.get("lessons", [])) >= 1
            assert "检查路径" in result["lessons"][0]

    @pytest.mark.asyncio
    async def test_full_cycle_with_mocks(self):
        """Full reflection cycle should write lessons to HOT memory."""
        with tempfile.TemporaryDirectory() as tmp:
            mem = _MockMemory()
            store = _MockProposalStore()
            evo = _MockEvolution()
            rs = ReflectionScheduler(tmp, memory_mgr=mem, evolution=evo,
                                     llm=_MockLLM(), proposal_store=store)
            await rs._run_reflection()

            # Lesson should be in HOT memory
            assert len(mem._hot) >= 1
            assert "[周学习]" in mem._hot[0]

    @pytest.mark.asyncio
    async def test_state_persistence(self):
        """Last reflection timestamp should survive restart."""
        with tempfile.TemporaryDirectory() as tmp:
            rs1 = ReflectionScheduler(tmp)
            rs1._last_reflection = 123456.0
            rs1._save_state()

            rs2 = ReflectionScheduler(tmp)
            assert rs2._last_reflection == 123456.0

    @pytest.mark.asyncio
    async def test_is_due(self):
        """is_due should be True when interval has elapsed."""
        with tempfile.TemporaryDirectory() as tmp:
            rs = ReflectionScheduler(tmp)
            rs._last_reflection = 0  # way in the past
            assert rs.is_due is True

    @pytest.mark.asyncio
    async def test_is_not_due(self):
        """is_due should be False when recently completed."""
        with tempfile.TemporaryDirectory() as tmp:
            rs = ReflectionScheduler(tmp)
            rs._last_reflection = time.time()  # just now
            assert rs.is_due is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """start/stop should not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            rs = ReflectionScheduler(tmp, memory_mgr=_MockMemory(), llm=_MockLLM())
            await rs.start(interval=86400)  # 1 day for test
            await rs.stop()

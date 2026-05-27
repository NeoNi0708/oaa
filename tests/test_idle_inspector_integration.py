"""IdleInspector integration tests — detection → proposal → visibility → execution."""
import os
import tempfile
import time
import pytest

from oaa.agent.idle_inspector import IdleInspector
from oaa.agent.proposal import ProposalStore, ProposalExecutor, Proposal
from oaa.agent.memory_manager import MemoryManager
from oaa.evolution.engine import EvolutionEngine


# ===================================================================
# Test: tool failure detection → structured proposal
# ===================================================================

class TestToolFailureIntegration:
    """IdleInspector detects tool failures → creates structured proposals."""

    @pytest.mark.asyncio
    async def test_tool_failure_creates_proposal(self):
        """When a tool fails ≥2 times, IdleInspector should create a Proposal."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)

            # Record 2 failures for the same tool
            mm.add_tool_failure("file_write", {"path": "/tmp/x"}, "Permission denied")
            mm.add_tool_failure("file_write", {"path": "/tmp/y"}, "Permission denied")

            # Inspect should detect and create a proposal
            result = await inspector._check_tool_failures()
            assert result is not None, "Expected a proposal for 2 tool failures"
            assert "file_write" in result
            assert "2 次" in result or "2次" in result

            # Proposal should be stored
            assert store.count_pending() >= 1
            proposal = store.list_pending()[0]
            assert proposal["type"] == "tool_fix"
            assert proposal["target"] == "file_write"
            # proposal uses problem_context instead of fixed actions
            assert proposal["problem_context"] is not None
            assert proposal["problem_context"]["type"] == "tool_failure"
            assert proposal["problem_context"]["tool_name"] == "file_write"

    @pytest.mark.asyncio
    async def test_single_failure_no_proposal(self):
        """One failure should NOT trigger a proposal."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=ProposalStore(tmp))

            mm.add_tool_failure("shell_run", {"command": "ls"}, "Timeout")

            result = await inspector._check_tool_failures()
            assert result is None, "Single failure should not trigger proposal"

    @pytest.mark.asyncio
    async def test_ignored_tool_skipped(self):
        """Tools in the ignore list should be excluded."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)
            inspector.set_memory_path(tmp)

            mm.add_tool_failure("wechat_contacts", {}, "not found")
            mm.add_tool_failure("wechat_contacts", {}, "not found")

            # Ignore the tool
            inspector.ignore_tool("wechat_contacts", permanent=True)

            result = await inspector._check_tool_failures()
            assert result is None, "Ignored tool should not produce proposal"

    @pytest.mark.asyncio
    async def test_tool_failure_dedup(self):
        """Same tool failure should not create duplicate pending proposals."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)

            mm.add_tool_failure("code_exec", {"code": "x"}, "NameError")
            mm.add_tool_failure("code_exec", {"code": "y"}, "NameError")

            # First check should create a proposal
            r1 = await inspector._check_tool_failures()
            assert r1 is not None

            # Second check should NOT create a duplicate (same target + type)
            r2 = await inspector._check_tool_failures()
            assert r2 is None

            # Only 1 pending proposal
            assert store.count_pending() == 1


# ===================================================================
# Test: memory health detection
# ===================================================================



# ===================================================================
# Test: full pipeline — detection → proposal → execution
# ===================================================================

class TestFullPipeline:
    """End-to-end: IdleInspector detects → ProposalStore stores → ProposalExecutor executes."""

    @pytest.mark.asyncio
    async def test_tool_fix_detect_store_and_execute(self):
        """Full cycle: tool failures → proposal creation → execute actions → proposal done."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)

            # Step 1: Record tool failures
            mm.add_tool_failure("shell_run", {"command": "bad_command"}, "Command not found")
            mm.add_tool_failure("shell_run", {"command": "another_bad"}, "Command not found")

            # Step 2: IdleInspector detects and creates a proposal
            result = await inspector._check_tool_failures()
            assert result is not None

            pending = store.list_pending()
            assert len(pending) >= 1
            proposal = pending[0]
            assert proposal["target"] == "shell_run"
            assert proposal["type"] == "tool_fix"
            assert proposal["problem_context"] is not None
            assert proposal["problem_context"]["type"] == "tool_failure"
            assert proposal["problem_context"]["tool_name"] == "shell_run"

            # Step 3: Execute the proposal via RepairLoop with a mock verifier
            from oaa.agent.repair_loop import RepairLoop, RepairPlan

            async def mock_verifier(ctx):
                return True, "模拟验证通过"

            plan = RepairPlan(
                proposal_id=proposal["id"],
                problem_context=proposal["problem_context"],
            )
            with tempfile.TemporaryDirectory() as tmp2:
                loop = RepairLoop(data_dir=tmp2)
                loop.register_verifier("tool_failure", mock_verifier)

                # Dummy agent that returns empty response
                class _DummyAgent:
                    async def process_message(self, prompt, history=None):
                        yield {"type": "done", "content": ""}

                result = await loop.run(plan, _DummyAgent())
                assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_proposal_persistence_across_inspector_restart(self):
        """Proposals survive IdleInspector restart (backed by ProposalStore JSON)."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)

            mm.add_tool_failure("code_exec", {"code": "x"}, "SyntaxError")
            mm.add_tool_failure("code_exec", {"code": "y"}, "SyntaxError")

            await inspector._check_tool_failures()
            assert store.count_pending() == 1

            # Create new store and inspector (simulating restart)
            store2 = ProposalStore(tmp)
            assert store2.count_pending() == 1

            inspector2 = IdleInspector(memory_mgr=mm, proposal_store=store2)
            r = await inspector2._check_tool_failures()
            assert r is None, "Should not re-create after restart (dedup by has_pending_for_target)"


# ===================================================================
# Test: inspect() and _inspect_all_phases don't crash
# ===================================================================

class TestInspectNoCrash:
    """inspect() / _inspect_all_phases() should never raise."""

    @pytest.mark.asyncio
    async def test_inspect_all_phases_no_crash(self):
        """_inspect_all_phases runs all phases without raising."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            store = ProposalStore(tmp)
            inspector = IdleInspector(memory_mgr=mm, proposal_store=store)

            # Should not raise despite having no dependencies configured
            try:
                await inspector._inspect_all_phases()
            except Exception as exc:
                pytest.fail(f"_inspect_all_phases raised: {exc}")

    @pytest.mark.asyncio
    async def test_inspect_no_crash(self):
        """inspect() returns None gracefully with no dependencies."""
        with tempfile.TemporaryDirectory() as tmp:
            inspector = IdleInspector(memory_mgr=MemoryManager(tmp))
            inspector._last_check = 0.0
            result = await inspector.inspect()
            # No scheduler / channel_adapters → no proposals expected
            assert result is None

    @pytest.mark.asyncio
    async def test_inspect_line_b_no_crash(self):
        """inspect_line_b() returns None gracefully with no dependencies."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            inspector = IdleInspector(memory_mgr=mm, evolution=EvolutionEngine(tmp))
            result = await inspector.inspect_line_b()
            assert result is None

    @pytest.mark.asyncio
    async def test_inspect_line_c_no_crash(self):
        """_inspect_line_c() returns None gracefully (stub in Phase 2)."""
        with tempfile.TemporaryDirectory() as tmp:
            mm = MemoryManager(tmp)
            inspector = IdleInspector(memory_mgr=mm, evolution=EvolutionEngine(tmp))
            result = await inspector._inspect_line_c()
            assert result is None


# ===================================================================
# Test: ignore list persistence
# ===================================================================

class TestIgnoreList:
    @pytest.mark.asyncio
    async def test_ignore_persists_across_restart(self):
        """Permanent ignore list survives IdleInspector restart."""
        with tempfile.TemporaryDirectory() as tmp:
            inspector1 = IdleInspector()
            inspector1.set_memory_path(tmp)
            inspector1.ignore_tool("some_tool", permanent=True)

            inspector2 = IdleInspector()
            inspector2.set_memory_path(tmp)
            assert inspector2.is_tool_ignored("some_tool") is True

    @pytest.mark.asyncio
    async def test_ignore_once_consumed(self):
        """Once-ignore is consumed after one check."""
        with tempfile.TemporaryDirectory() as tmp:
            inspector = IdleInspector()
            inspector.set_memory_path(tmp)
            inspector.ignore_tool("temp_tool", permanent=False)

            assert inspector.is_tool_ignored("temp_tool") is True
            assert inspector.is_tool_ignored("temp_tool") is False  # consumed

    @pytest.mark.asyncio
    async def test_ignore_forever_persists(self):
        """Forever-ignore persists across multiple checks."""
        with tempfile.TemporaryDirectory() as tmp:
            inspector = IdleInspector()
            inspector.set_memory_path(tmp)
            inspector.ignore_tool("perm_tool", permanent=True)

            assert inspector.is_tool_ignored("perm_tool") is True
            assert inspector.is_tool_ignored("perm_tool") is True  # still ignored

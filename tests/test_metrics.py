"""Test metrics collector — proactivity metrics + LLM call stats."""
import tempfile
import pytest
from oaa.agent.metrics import MetricsCollector


@pytest.fixture
def mc():
    with tempfile.TemporaryDirectory() as tmp:
        yield MetricsCollector(tmp)


class TestToolMetrics:
    def test_record_confirm_auto(self, mc: MetricsCollector):
        mc.record_confirm("file_read", "auto_level", True)
        s = mc.tool_stats["file_read"]
        assert s["auto"] == 1
        assert s["confirmed"] == 0
        assert s["denied"] == 0

    def test_record_confirm_confirmed(self, mc: MetricsCollector):
        mc.record_confirm("shell_run", "confirmed", True)
        s = mc.tool_stats["shell_run"]
        assert s["confirmed"] == 1
        assert s["auto"] == 0

    def test_record_confirm_denied(self, mc: MetricsCollector):
        mc.record_confirm("code_exec", "denied", False)
        s = mc.tool_stats["code_exec"]
        assert s["denied"] == 1

    def test_record_confirm_trusted(self, mc: MetricsCollector):
        mc.record_confirm("file_write", "trusted", True)
        s = mc.tool_stats["file_write"]
        assert s["auto"] == 1  # trusted counted as auto

    def test_tool_result_success(self, mc: MetricsCollector):
        mc.record_tool_result("file_read", success=True)
        assert mc.tool_stats["file_read"]["success"] == 1

    def test_tool_result_failure(self, mc: MetricsCollector):
        mc.record_tool_result("file_read", success=False)
        assert mc.tool_stats["file_read"]["failure"] == 1

    def test_proactivity_ratio(self, mc: MetricsCollector):
        mc.record_confirm("a", "auto_level", True)
        mc.record_confirm("a", "auto_level", True)
        mc.record_confirm("b", "confirmed", True)
        assert mc.get_proactivity_ratio() == 2 / 3

    def test_proactivity_ratio_all_auto(self, mc: MetricsCollector):
        mc.record_confirm("a", "auto_level", True)
        mc.record_confirm("b", "trusted", True)
        assert mc.get_proactivity_ratio() == 1.0

    def test_proactivity_ratio_no_data(self, mc: MetricsCollector):
        assert mc.get_proactivity_ratio() == 1.0

    def test_active_repairs_counted(self, mc: MetricsCollector):
        mc.record_confirm("self_improve", "auto_level", True)
        mc.record_confirm("reload_module", "trusted", True)
        mc.record_confirm("file_read", "auto_level", True)
        summary = mc.get_tool_summary()
        assert summary["active_repairs"] == 2


class TestLLMMetrics:
    def test_record_llm_call(self, mc: MetricsCollector):
        mc.record_llm_call("gpt-4", 1500.0, "stop", tool_call_count=3, content_length=200)
        assert len(mc.llm_calls) == 1
        assert mc.llm_calls[0]["model"] == "gpt-4"
        assert mc.llm_calls[0]["fr"] == "stop"

    def test_record_llm_error(self, mc: MetricsCollector):
        mc.record_llm_call("gpt-4", 500.0, "", error="BadRequestError")
        assert mc.llm_calls[0]["err"] == "BadRequestError"

    def test_llm_summary_aggregation(self, mc: MetricsCollector):
        mc.record_llm_call("gpt-4", 1000.0, "stop")
        mc.record_llm_call("gpt-4", 2000.0, "tool_calls")
        mc.record_llm_call("claude-3", 3000.0, "stop")
        summary = mc.get_llm_summary()
        assert summary["total_calls"] == 3
        assert summary["by_model"]["gpt-4"] == 2
        assert summary["by_model"]["claude-3"] == 1
        assert summary["by_finish_reason"]["stop"] == 2
        assert summary["by_finish_reason"]["tool_calls"] == 1

    def test_llm_summary_empty(self, mc: MetricsCollector):
        summary = mc.get_llm_summary()
        assert summary["total_calls"] == 0

    def test_llm_ring_buffer(self, mc: MetricsCollector):
        mc._max_llm_records = 5
        for i in range(10):
            mc.record_llm_call("model", 100.0, "stop")
        assert len(mc.llm_calls) == 5


class TestSystemPromptBlock:
    def test_empty_returns_empty_string(self, mc: MetricsCollector):
        assert mc.get_system_prompt_block() == ""

    def test_after_tool_call(self, mc: MetricsCollector):
        mc.record_confirm("file_read", "auto_level", True)
        block = mc.get_system_prompt_block()
        assert block.startswith("## 主动性指标")

    def test_after_llm_call(self, mc: MetricsCollector):
        mc.record_llm_call("gpt-4", 500.0, "stop")
        block = mc.get_system_prompt_block()
        assert "LLM 调用统计" in block


class TestPersistence:
    @pytest.mark.asyncio
    async def test_flush_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc1 = MetricsCollector(tmp)
            mc1.record_confirm("file_read", "auto_level", True)
            mc1.record_llm_call("gpt-4", 100.0, "stop")
            await mc1.flush_tool_stats()
            await mc1.flush_llm_stats()

            mc2 = MetricsCollector(tmp)
            assert mc2.tool_stats["file_read"]["auto"] == 1
            assert len(mc2.llm_calls) == 1

    @pytest.mark.asyncio
    async def test_persistence_empty_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc1 = MetricsCollector(tmp)
            await mc1.flush_tool_stats()
            await mc1.flush_llm_stats()

            mc2 = MetricsCollector(tmp)
            assert mc2.tool_stats == {}
            assert mc2.llm_calls == []

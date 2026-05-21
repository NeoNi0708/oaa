"""Metrics collector — proactivity metrics + LLM call statistics.

Tracks two categories of metrics:

1. **Proactivity metrics** (主动性度量指标)
   - Tool call counts broken down by decision type (auto/trusted/confirmed/denied)
   - Per-tool success/failure rates
   - Active repair counts (self_improve, reload_module, rollback_change)
   - Proactivity ratio = auto calls / total calls

2. **LLM call statistics**
   - Per-model call counts, duration, token estimates
   - Finish reason distribution
   - Error counts

All metrics are persisted to JSON for cross-session continuity.
"""
import json
import os
import time
from typing import Optional

from ..async_io import async_write_json
from ..logging_config import get_logger

logger = get_logger("metrics")

# Tool names considered "active repairs" (inherently proactive)
_ACTIVE_REPAIR_TOOLS = frozenset({
    "self_improve", "reload_module", "rollback_change",
    "code_exec", "shell_run",
})


class MetricsCollector:
    """Collect and report proactivity and LLM metrics."""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._metrics_path = os.path.join(data_dir, "memory", "metrics.json")
        self._llm_path = os.path.join(data_dir, "memory", "llm_stats.json")

        # Tool-level metrics
        # tool_name -> {auto: N, confirmed: N, denied: N, success: N, failure: N}
        self.tool_stats: dict[str, dict] = {}

        # LLM call history (bounded ring buffer)
        self.llm_calls: list[dict] = []
        self._max_llm_records = 1000

        # Derived accumulators
        self._active_repairs = 0
        self._session_start = time.time()

        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        try:
            if os.path.exists(self._metrics_path):
                with open(self._metrics_path, encoding="utf-8") as f:
                    data = json.load(f)
                self.tool_stats = data.get("tool_stats", {})
                self._active_repairs = data.get("active_repairs", 0)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load metrics: %s", exc)

        try:
            if os.path.exists(self._llm_path):
                with open(self._llm_path, encoding="utf-8") as f:
                    self.llm_calls = json.load(f)
                # Trim on load in case file grew externally
                if len(self.llm_calls) > self._max_llm_records:
                    self.llm_calls = self.llm_calls[-self._max_llm_records:]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load LLM stats: %s", exc)

    async def _save_tool_stats(self):
        try:
            await async_write_json(self._metrics_path, {
                "tool_stats": self.tool_stats,
                "active_repairs": self._active_repairs,
            }, indent=2)
        except Exception as exc:
            logger.warning("Failed to save metrics: %s", exc)

    async def _save_llm_stats(self):
        try:
            await async_write_json(self._llm_path, self.llm_calls, indent=2)
        except Exception as exc:
            logger.warning("Failed to save LLM stats: %s", exc)

    # ------------------------------------------------------------------
    # Tool metrics
    # ------------------------------------------------------------------

    def record_confirm(self, tool_name: str, decision: str, allowed: bool):
        """Record a permission decision for a tool call.

        Args:
            tool_name: The tool being called.
            decision: One of "auto_level" (level=auto),
                      "trusted" (trust count >= threshold),
                      "confirmed" (user approved),
                      "denied" (user rejected).
            allowed: Whether the call was ultimately allowed.
        """
        stats = self.tool_stats.setdefault(tool_name, {
            "auto": 0, "confirmed": 0, "denied": 0,
            "success": 0, "failure": 0,
        })

        if decision == "denied":
            stats["denied"] += 1
        elif decision in ("auto_level", "trusted"):
            stats["auto"] += 1
        elif decision == "confirmed":
            stats["confirmed"] += 1

        if tool_name in _ACTIVE_REPAIR_TOOLS:
            self._active_repairs += 1

    def record_tool_result(self, tool_name: str, success: bool):
        """Record whether a tool call succeeded or failed."""
        stats = self.tool_stats.setdefault(tool_name, {
            "auto": 0, "confirmed": 0, "denied": 0,
            "success": 0, "failure": 0,
        })
        if success:
            stats["success"] += 1
        else:
            stats["failure"] += 1

    async def flush_tool_stats(self):
        """Persist tool metrics to disk."""
        await self._save_tool_stats()

    # ------------------------------------------------------------------
    # LLM metrics
    # ------------------------------------------------------------------

    def record_llm_call(
        self,
        model: str,
        duration_ms: float,
        finish_reason: str,
        tool_call_count: int = 0,
        content_length: int = 0,
        error: Optional[str] = None,
    ):
        """Record a single LLM call.

        Args:
            model: Model ID used.
            duration_ms: Wall-clock duration in milliseconds.
            finish_reason: "stop", "length", "tool_calls", etc.
            tool_call_count: Number of tool calls in the response.
            content_length: Length of text content in response.
            error: Error type if the call failed, else None.
        """
        entry = {
            "t": time.time(),
            "model": model,
            "ms": round(duration_ms, 1),
            "fr": finish_reason or ("error" if error else "unknown"),
            "tc": tool_call_count,
            "cl": content_length,
        }
        if error:
            entry["err"] = error
        self.llm_calls.append(entry)

        # Ring buffer trim
        if len(self.llm_calls) > self._max_llm_records:
            self.llm_calls = self.llm_calls[-self._max_llm_records:]

    async def flush_llm_stats(self):
        """Persist LLM stats to disk."""
        await self._save_llm_stats()

    # ------------------------------------------------------------------
    # Report builders
    # ------------------------------------------------------------------

    def get_proactivity_ratio(self) -> float:
        """Auto / (auto + confirmed). 1.0 = fully proactive, 0.0 = fully passive."""
        auto = sum(s.get("auto", 0) for s in self.tool_stats.values())
        confirmed = sum(s.get("confirmed", 0) for s in self.tool_stats.values())
        total = auto + confirmed
        return auto / total if total > 0 else 1.0

    def get_tool_summary(self) -> dict:
        """Summary of tool-level metrics."""
        total_auto = 0
        total_confirmed = 0
        total_denied = 0
        total_success = 0
        total_failure = 0
        breakdown = {}

        for name, s in self.tool_stats.items():
            a = s.get("auto", 0)
            c = s.get("confirmed", 0)
            d = s.get("denied", 0)
            ok = s.get("success", 0)
            fail = s.get("failure", 0)
            total_auto += a
            total_confirmed += c
            total_denied += d
            total_success += ok
            total_failure += fail
            tt = a + c + d + ok + fail
            if tt > 0:
                breakdown[name] = {
                    "auto": a, "confirmed": c, "denied": d,
                    "success": ok, "failure": fail,
                }

        total_calls = total_auto + total_confirmed + total_denied
        proactivity = self.get_proactivity_ratio()
        success_rate = total_success / (total_success + total_failure) if (total_success + total_failure) > 0 else 1.0

        return {
            "total_tool_calls": total_calls,
            "auto": total_auto,
            "confirmed": total_confirmed,
            "denied": total_denied,
            "success": total_success,
            "failure": total_failure,
            "proactivity_ratio": round(proactivity, 3),
            "success_rate": round(success_rate, 3),
            "active_repairs": self._active_repairs,
            "breakdown": breakdown,
        }

    def get_llm_summary(self) -> dict:
        """Summary of LLM call statistics."""
        if not self.llm_calls:
            return {
                "total_calls": 0,
                "by_model": {},
                "by_finish_reason": {},
                "avg_duration_ms": 0,
                "error_count": 0,
                "session_duration_hours": 0,
            }

        total = len(self.llm_calls)
        by_model: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        total_ms = 0.0
        errors = 0

        for entry in self.llm_calls:
            model = entry.get("model", "unknown")
            by_model[model] = by_model.get(model, 0) + 1
            reason = entry.get("fr", "unknown")
            by_reason[reason] = by_reason.get(reason, 0) + 1
            total_ms += entry.get("ms", 0)
            if entry.get("err"):
                errors += 1

        session_hrs = (time.time() - self._session_start) / 3600

        return {
            "total_calls": total,
            "by_model": by_model,
            "by_finish_reason": by_reason,
            "total_duration_ms": round(total_ms, 1),
            "avg_duration_ms": round(total_ms / total, 1) if total > 0 else 0,
            "error_count": errors,
            "error_rate": round(errors / total, 3) if total > 0 else 0,
            "session_duration_hours": round(session_hrs, 1),
        }

    def get_system_prompt_block(self) -> str:
        """Return a concise metrics block for injection into system prompt.
        Returns empty string if there are no metrics yet.
        """
        tool_summary = self.get_tool_summary()
        llm_summary = self.get_llm_summary()

        tc = tool_summary["total_tool_calls"]
        lc = llm_summary["total_calls"]
        if tc == 0 and lc == 0:
            return ""

        lines = [
            "## 主动性指标",
        ]

        if tc > 0:
            ratio_pct = round(tool_summary["proactivity_ratio"] * 100, 1)
            lines.append(f"- 本轮会话工具调用: {tc} 次")
            lines.append(f"- 主动性比率: {ratio_pct}%（自动执行/总调用）")
            lines.append(f"  - 自动执行: {tool_summary['auto']} 次 | 用户确认: {tool_summary['confirmed']} 次 | 被拒绝: {tool_summary['denied']} 次")
            lines.append(f"  - 主动修复: {tool_summary['active_repairs']} 次")

        if lc > 0:
            lines.append(f"- LLM 调用统计: {lc} 次")
            model_str = ", ".join(f"{m}: {c}次" for m, c in llm_summary["by_model"].items())
            if model_str:
                lines.append(f"  - 模型: {model_str}")
            lines.append(f"  - 平均耗时: {llm_summary['avg_duration_ms']}ms")
            if llm_summary["error_count"] > 0:
                lines.append(f"  - 错误: {llm_summary['error_count']} 次")

        return "\n".join(lines)

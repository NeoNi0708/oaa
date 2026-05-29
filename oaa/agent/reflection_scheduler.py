"""Periodic reflection scheduler — weekly learning from recent task patterns.

Runs as a background asyncio task (default: every 7 days).  On each cycle it:

1. Gathers recent data: tool failures, corrections, skill usage
2. Calls the LLM to analyze patterns and extract lessons
3. Writes lessons to HOT memory for long-term retention
4. Creates Proposals for actionable improvements

Requires: memory_mgr (HOT + corrections), llm, evolution (stats), proposal_store.
"""

import asyncio
import json
import os
import time

from ..logging_config import get_logger

logger = get_logger("agent.reflection")

_REFLECTION_INTERVAL = 604800  # 7 days


class ReflectionScheduler:
    """Weekly self-reflection — analyze patterns, extract lessons, propose improvements.

    State is persisted in ``data_dir/reflection/state.json`` so the timer
    survives restarts without resetting.
    """

    def __init__(self, data_dir: str, memory_mgr=None, evolution=None,
                 llm=None, proposal_store=None):
        self._memory_mgr = memory_mgr
        self._evolution = evolution
        self._llm = llm
        self._proposal_store = proposal_store

        self._state_dir = os.path.join(data_dir, "reflection")
        self._state_path = os.path.join(self._state_dir, "state.json")
        self._interval = _REFLECTION_INTERVAL
        self._background_task: asyncio.Task | None = None
        self._last_reflection: float = 0.0

        os.makedirs(self._state_dir, exist_ok=True)
        self._load_state()

    def _load_state(self):
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, encoding="utf-8") as f:
                    self._last_reflection = json.load(f).get("last_reflection", 0.0)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self):
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump({"last_reflection": self._last_reflection}, f)
        except OSError as exc:
            logger.warning("Failed to save reflection state: %s", exc)

    @property
    def is_due(self) -> bool:
        """Check if reflection is due based on elapsed time."""
        return time.time() - self._last_reflection >= self._interval

    async def start(self, interval: int | None = None):
        """Start the background reflection loop.

        If a reflection is already due (missed while stopped), runs it
        immediately before entering the periodic loop.
        """
        if self._background_task is not None:
            logger.warning("ReflectionScheduler already running")
            return

        if self.is_due and self._llm:
            logger.info("Reflection is overdue — running catch-up cycle")
            try:
                await self._run_reflection()
            except Exception as exc:
                logger.warning("Catch-up reflection failed: %s", exc)

        if interval is not None:
            self._interval = interval
        self._background_task = asyncio.create_task(self._background_loop())
        logger.info("ReflectionScheduler started (interval=%ds)", self._interval)

    async def stop(self):
        if self._background_task is None:
            return
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:
            pass
        self._background_task = None
        logger.info("ReflectionScheduler stopped")

    async def _background_loop(self):
        while True:
            await asyncio.sleep(self._interval)
            try:
                if self._llm:
                    await self._run_reflection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Reflection cycle failed: %s", exc)

    async def _run_reflection(self):
        """Run one reflection cycle: gather data → LLM analysis → persist."""
        now = time.time()
        self._last_reflection = now
        self._save_state()

        # Gather recent data
        failures = []
        if self._memory_mgr:
            try:
                failures = self._memory_mgr.load_tool_failures(30)
            except Exception as exc:
                logger.warning("Reflection: failed to load failures: %s", exc)

        corrections = []
        if self._memory_mgr:
            try:
                corrections = self._memory_mgr.load_recent_corrections(15)
            except Exception as exc:
                logger.warning("Reflection: failed to load corrections: %s", exc)

        skill_usage = {}
        crystallized = []
        if self._evolution and hasattr(self._evolution, 'stats'):
            stats = self._evolution.stats
            skill_usage = stats.get("skill_usage", {})
            crystallized = [c.get("name", "") for c in stats.get("crystallized", [])]

        # Skip if nothing to analyze
        if not failures and not corrections and not skill_usage:
            logger.warning("Reflection: no data to analyze")
            return

        # LLM analysis
        analysis = await self._llm_reflect(failures, corrections, skill_usage, crystallized)
        if not analysis:
            return

        # Write lessons to HOT memory
        for lesson in analysis.get("lessons", []):
            if len(lesson) > 10:
                try:
                    await self._memory_mgr.add_to_hot(f"[周学习] {lesson}")
                except Exception as exc:
                    logger.warning("Reflection: failed to save lesson: %s", exc)

        # Create proposal for actionable suggestions
        suggestion = analysis.get("suggestion", "").strip()
        if suggestion and len(suggestion) > 20 and self._proposal_store:
            try:
                from .proposal import Proposal, TYPE_SOP_OPTIMIZE
                # Use a stable dedup key so the same suggestion isn't re-created weekly
                dedup_target = f"reflection:{hash(suggestion) % 10000}"
                if not self._proposal_store.has_pending_for_target(dedup_target, "reflection"):
                    await self._proposal_store.add(Proposal(
                        type="reflection",
                        title="周学习分析建议",
                        problem=suggestion,
                        benefit="采纳后可优化任务执行效率",
                        target=dedup_target,
                        actions=None,
                    ))
            except Exception as exc:
                logger.warning("Reflection: failed to create proposal: %s", exc)

        logger.info("Reflection complete: %d lessons, %s",
                    len(analysis.get("lessons", [])),
                    "has suggestion" if suggestion else "no suggestion")

    async def _llm_reflect(self, failures: list[dict], corrections: list[dict],
                           skill_usage: dict, crystallized: list[str]) -> dict | None:
        """Call LLM to analyze recent patterns and extract learning.

        Returns ``{"lessons": [...], "suggestion": "..."}`` or ``None``.
        """
        # Build compact summary
        parts = []

        if failures:
            by_tool: dict[str, int] = {}
            for f in failures:
                tool = f.get("tool", "?")
                by_tool[tool] = by_tool.get(tool, 0) + 1
            top_tools = sorted(by_tool.items(), key=lambda x: -x[1])[:5]
            fail_lines = [f"  - {t}: {c}次" for t, c in top_tools if c >= 2]
            if fail_lines:
                parts.append("反复失败的工具：\n" + "\n".join(fail_lines))

        if corrections:
            corr_lines = [f"  - {c.get('lesson', '')[:150]}" for c in corrections[-5:]]
            parts.append("最近修正记录：\n" + "\n".join(corr_lines))

        if skill_usage:
            heavy = sorted(skill_usage.items(), key=lambda x: -x[1])[:5]
            skill_lines = [f"  - {s}: {c}次" for s, c in heavy if c >= 3]
            if skill_lines:
                parts.append("高频技能：\n" + "\n".join(skill_lines))
            uncrystallized = [s for s, c in heavy if c >= 5 and s not in crystallized]
            if uncrystallized:
                parts.append(f"达到结晶阈值但未固化的技能: {', '.join(uncrystallized)}")

        if not parts:
            return None

        summary = "\n\n".join(parts)

        prompt = (
            "你是一个 AI 学习分析专家。以下是 OAA 过去一周的运行数据，请进行分析：\n\n"
            f"{summary}\n\n"
            "请输出：\n"
            "1. **lessons**: 从中可以总结出什么经验教训（数组，每条一句话，中文）\n"
            "2. **suggestion**: 有什么具体的改进行动建议（一句话，如无可留空）\n\n"
            "以 JSON 格式回答：\n"
            '{"lessons": ["经验1", "经验2"], '
            '"suggestion": "改进建议或空字符串"}'
        )

        try:
            response = await self._llm.chat([
                {"role": "system", "content": "你是一个严谨的 AI 学习分析专家。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ])
            raw = response.content.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()

            import json as _json
            result = _json.loads(raw)
            if not isinstance(result.get("lessons"), list):
                result["lessons"] = []
            if "suggestion" not in result:
                result["suggestion"] = ""
            logger.info("Reflection LLM result: %d lessons", len(result["lessons"]))
            return result
        except Exception as exc:
            logger.warning("Reflection LLM call failed: %s", exc)
            return None

"""Idle inspector — proactively checks for tasks and self-improvement opportunities."""
import time
from collections import Counter
from typing import TYPE_CHECKING, Optional

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..scheduler import TaskScheduler
    from .memory_manager import MemoryManager

logger = get_logger("agent.idle_inspector")

# Minimum seconds between idle inspections
_INSPECTION_COOLDOWN = 300  # 5 minutes


class IdleInspector:
    """Checks for real tasks and improvement areas during idle time.

    Flow:
    1. Check TaskScheduler for due tasks -> propose execution
    2. If no tasks, check memory/corrections -> propose self-improvement
    3. Always asks user before acting (via proposal text)
    """

    def __init__(self, scheduler: Optional["TaskScheduler"] = None,
                 memory_mgr: Optional["MemoryManager"] = None):
        self._scheduler = scheduler
        self._memory_mgr = memory_mgr
        self._last_check: float = 0.0

    def reset_cooldown(self):
        """Reset the cooldown timer so next check runs immediately."""
        self._last_check = 0.0

    def inspect(self) -> str | None:
        """Run idle inspection. Returns a proposal message or None."""
        now = time.time()
        if now - self._last_check < _INSPECTION_COOLDOWN:
            return None
        self._last_check = now

        # Phase 1: Check for due tasks (real work)
        proposal = self._check_due_tasks()
        if proposal:
            return proposal

        # Phase 2: Self-improvement opportunities
        proposal = self._check_self_improvement()
        if proposal:
            return proposal

        # Phase 3: Tool failure patterns
        proposal = self._check_tool_failures()
        if proposal:
            return proposal

        return None

    def _check_due_tasks(self) -> str | None:
        """Check TaskScheduler for due tasks. Returns proposal or None."""
        if not self._scheduler:
            return None

        try:
            due = self._scheduler.get_due_tasks()
        except Exception as exc:
            logger.warning("Idle check: scheduler error: %s", exc)
            return None

        if not due:
            return None

        lines = []
        for t in due:
            name = t.get("name", "?")
            desc = t.get("description", "")
            line = f"  - **{name}**"
            if desc:
                line += f": {desc[:100]}"
            lines.append(line)

        return (
            "🔍 空闲巡检：发现以下定时任务已到执行时间：\n\n"
            + "\n".join(lines)
            + "\n\n是否执行这些任务？请确认。"
        )

    def _check_self_improvement(self) -> str | None:
        """Check memory/corrections for improvement suggestions.

        Uses lightweight heuristics — no LLM calls — to find areas
        where communication or work quality could be improved.
        """
        if not self._memory_mgr:
            return None

        suggestions = []

        # 1. Detect correction patterns (same lesson repeated)
        try:
            corrections = self._memory_mgr.load_recent_corrections(20)
            if len(corrections) >= 3:
                lessons = [c["lesson"] for c in corrections]
                repeated = [l for l, c in Counter(lessons).items() if c >= 2]
                if repeated:
                    suggestions.append(
                        f"发现 {len(repeated)} 条反复出现的修正模式，"
                        f"建议回顾后优化响应方式，提高沟通效率。"
                    )
        except Exception:
            pass

        # 2. Check HOT memory density
        try:
            hot = self._memory_mgr.load_hot()
            if hot:
                line_count = len(hot.split("\n"))
                if line_count > 80:
                    suggestions.append(
                        f"持久记忆已存储 {line_count} 条记录，接近压缩上限。"
                        f"建议审查整理，保留最有价值的信息。"
                    )
                elif line_count > 50:
                    suggestions.append(
                        f"持久记忆已有 {line_count} 条记录，可适时回顾整理。"
                    )
        except Exception:
            pass

        # 3. Check archive topics for potential review
        try:
            warm_topics = self._memory_mgr.list_warm_topics()
            if warm_topics:
                suggestions.append(
                    f"存档区有 {len(warm_topics)} 个主题文件，"
                    f"可回顾是否有可重新纳入 HOT 记忆的内容。"
                )
        except Exception:
            pass

        if not suggestions:
            return None

        proposal = (
            "💡 空闲分析：发现以下可改进的方向：\n\n"
            + "\n".join(f"  - {s}" for s in suggestions)
            + "\n\n是否进行优化？请确认。"
        )
        return proposal

    def _check_tool_failures(self) -> str | None:
        """Phase 3: Check logged tool failures for patterns that need code fixes.

        When a tool fails ≥2 times, generates an actionable repair proposal
        that the agent can follow after user confirmation:
        ``read_own_source`` → ``file_patch`` → pycache clear → ``reload_module``.
        """
        if not self._memory_mgr:
            return None

        try:
            failures = self._memory_mgr.load_tool_failures(50)
        except Exception:
            return None

        if not failures:
            return None

        tool_counts = Counter(f["tool"] for f in failures)

        suggestions = []
        for tool, count in tool_counts.most_common(3):
            if count >= 2:
                latest = [f for f in failures if f["tool"] == tool][-1]
                error_snippet = latest.get("error", "")[:120]
                # Look for an example arg to hint at where the issue is
                args_hint = latest.get("args", "")
                if args_hint and len(args_hint) > 100:
                    args_hint = args_hint[:100] + "…"

                suggestions.append(
                    f"工具 **{tool}** 最近失败 {count} 次\n"
                    f"  - 最后错误: {error_snippet}\n"
                    f"  - 修复步骤: 查看源码(``read_own_source``) → 定位问题 → "
                    f"``file_patch`` 修复 → 清除 ``__pycache__`` → ``reload_module`` 生效"
                )

        if not suggestions:
            return None

        return (
            "🔧 空闲诊断：发现以下工具存在反复失败：\n\n"
            + "\n".join(f"  - {s}" for s in suggestions)
            + "\n\n是否检查并自动修复？请确认。"
        )

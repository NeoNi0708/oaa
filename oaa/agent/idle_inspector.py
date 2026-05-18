"""Idle inspector — proactively checks for tasks and self-improvement opportunities."""
import time
from collections import Counter
from typing import TYPE_CHECKING, Optional

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler
    from .memory_manager import MemoryManager

logger = get_logger("agent.idle_inspector")

# Minimum seconds between idle inspections
_INSPECTION_COOLDOWN = 300  # 5 minutes


class IdleInspector:
    """Checks for real tasks and improvement areas during idle time.

    Flow:
    1. Check TaskScheduler for due tasks -> propose execution
    2. Check EvolutionEngine for auto-refinements -> propose self_improve
    3. Check tool failures -> propose read_own_source + file_patch fix
    4. Check correction patterns -> propose modify_own_prompt
    5. Always asks user before acting (via proposal text)
    """

    def __init__(self, scheduler: Optional["TaskScheduler"] = None,
                 memory_mgr: Optional["MemoryManager"] = None,
                 evolution: Optional["EvolutionEngine"] = None):
        self._scheduler = scheduler
        self._memory_mgr = memory_mgr
        self._evolution = evolution
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

        # Phase 2: Evolution-driven refinements (SOP skips, usage milestones)
        proposal = self._check_evolution_refinements()
        if proposal:
            return proposal

        # Phase 3: Tool failure patterns → self_improve
        proposal = self._check_tool_failures()
        if proposal:
            return proposal

        # Phase 4: Correction patterns → modify_own_prompt
        proposal = self._check_correction_patterns()
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

    def _check_evolution_refinements(self) -> str | None:
        """Check EvolutionEngine for auto-refinements (SOP skips, usage milestones).

        Returns an actionable proposal the agent can execute with self_improve,
        modify_own_prompt, or code_exec.
        """
        if not self._evolution:
            return None

        try:
            refinements = self._evolution.get_auto_refinements()
        except Exception as exc:
            logger.warning("Evolution refinement check failed: %s", exc)
            return None

        if not refinements:
            return None

        lines = []
        for r in refinements:
            if r["type"] == "sop_skip":
                lines.append(
                    f"  - **SOP 优化**：{r['description']}\n"
                    f"    操作: 用 ``self_improve`` 从 ``{r['file_path']}`` 中移除该步骤\n"
                    f"    步骤名称: {r['step_name']}"
                )
            elif r["type"] == "skill_optimize":
                lines.append(
                    f"  - **技能优化**：{r['description']}\n"
                    f"    操作: 用 ``code_exec`` 分析使用数据，生成优化建议"
                )

        if not lines:
            return None

        return (
            "🔬 技能优化检测：发现以下可优化项：\n\n"
            + "\n\n".join(lines)
            + "\n\n是否执行这些优化？请确认。"
        )

    def _check_tool_failures(self) -> str | None:
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
        """Check logged tool failures and propose structured self_improve repairs.

        When a tool fails ≥2 times, generates a repair proposal with exact
        file paths and suggested fix approach so the agent can execute
        ``read_own_source`` → ``self_improve`` → clear pycache → ``reload_module``.
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
                args_hint = latest.get("args", "")
                if args_hint and len(args_hint) > 100:
                    args_hint = args_hint[:100] + "…"

                # Infer which source file likely contains the tool
                tool_to_file = {
                    # AtomicTools live in tools.py
                    "ask_user": "oaa/agent/tools.py",
                    "file_write": "oaa/agent/tools.py",
                    "file_patch": "oaa/agent/tools.py",
                    "shell_run": "oaa/agent/tools.py",
                    "code_run": "oaa/agent/tools.py",
                    "code_exec": "oaa/agent/tools.py",
                    "read_own_source": "oaa/agent/tools.py",
                    "list_own_structure": "oaa/agent/tools.py",
                    "reload_module": "oaa/agent/tools.py",
                    "rollback_change": "oaa/agent/tools.py",
                    "memory_recall": "oaa/agent/tools.py",
                    "correction_log": "oaa/agent/tools.py",
                    "self_reflect": "oaa/agent/tools.py",
                    "update_working_checkpoint": "oaa/agent/tools.py",
                    # ExtendedTools
                    "word_doc": "oaa/agent/extended_tools.py",
                    "excel_xlsx": "oaa/agent/extended_tools.py",
                    "email_send": "oaa/agent/extended_tools.py",
                    "skill_load": "oaa/agent/extended_tools.py",
                    "skill_create": "oaa/agent/extended_tools.py",
                    "web_search": "oaa/agent/extended_tools.py",
                    "web_scan": "oaa/agent/extended_tools.py",
                    "plan_create": "oaa/agent/extended_tools.py",
                    "plan_update": "oaa/agent/extended_tools.py",
                    "plan_list": "oaa/agent/extended_tools.py",
                }
                source_file = tool_to_file.get(tool, "oaa/agent/tools.py")

                suggestions.append(
                    f"**{tool}** 最近失败 {count} 次\n"
                    f"  - 最后错误: {error_snippet}\n"
                    f"  - 修复步骤:\n"
                    f"    1. ``read_own_source path={source_file}`` 查看工具实现\n"
                    f"    2. 分析错误原因，用 ``self_improve`` 修复\n"
                    f"    3. 清除 ``__pycache__`` 目录\n"
                    f"    4. ``reload_module module={source_file.replace('/', '.').replace('.py', '')}``"
                )

        if not suggestions:
            return None

        return (
            "🔧 空闲诊断：发现以下工具存在反复失败：\n\n"
            + "\n\n".join(suggestions)
            + "\n\n是否检查并自动修复？请确认。"
        )

    def _check_correction_patterns(self) -> str | None:
        """Check for repeated correction patterns and propose modify_own_prompt.

        When the same lesson appears 2+ times in recent corrections,
        suggests adding it to the system prompt so the agent stops
        repeating the mistake.
        """
        if not self._memory_mgr:
            return None

        try:
            corrections = self._memory_mgr.load_recent_corrections(20)
        except Exception:
            return None

        if len(corrections) < 2:
            return None

        lessons = [c["lesson"] for c in corrections if c.get("lesson")]
        repeated = [(l, c) for l, c in Counter(lessons).items() if c >= 2]

        if not repeated:
            return None

        lines = []
        for lesson, count in repeated[:3]:
            lines.append(
                f"  - **{lesson}**（重复 {count} 次）\n"
                f"    操作: 用 ``modify_own_prompt action=write section=agents`` "
                f"在 agents 段中加入该规则"
            )

        return (
            "📝 响应模式优化：发现以下反复修正：\n\n"
            + "\n\n".join(lines)
            + "\n\n是否更新提示词以记住这些规则？请确认。"
        )

"""Idle inspector — proactively checks for tasks and self-improvement opportunities.

``inspect()`` can be called synchronously (from end of ``process_message``)
or driven periodically by a background asyncio task managed via
:meth:`start_background` / :meth:`stop_background`.
"""
import asyncio
import json
import os
import time
from collections import Counter
from typing import TYPE_CHECKING, Callable, Coroutine, Optional

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler
    from .memory_manager import MemoryManager

logger = get_logger("agent.idle_inspector")

# Minimum seconds between idle inspections
_INSPECTION_COOLDOWN = 300  # 5 minutes

# Max times the same proposal is delivered before suppression
_MAX_PROPOSAL_REPEATS = 3


class IdleInspector:
    """Checks for real tasks and improvement areas during idle time.

    Flow:
    1. Check TaskScheduler for due tasks -> propose execution
    2. Check EvolutionEngine for auto-refinements -> propose self_improve
    3. Check memory health (density, archives)
    4. Check tool failures -> propose read_own_source + file_patch fix
    5. Check correction patterns -> propose modify_own_prompt
    6. Always asks user before acting (via proposal text)

    Supports both on-demand (``inspect()``) and periodic (``start_background()``)
    modes. The background task runs on ``_INSPECTION_COOLDOWN`` interval and
    pushes proposals to a registered notification callback.
    """

    def __init__(self, scheduler: Optional["TaskScheduler"] = None,
                 memory_mgr: Optional["MemoryManager"] = None,
                 evolution: Optional["EvolutionEngine"] = None):
        self._scheduler = scheduler
        self._memory_mgr = memory_mgr
        self._evolution = evolution
        self._last_check: float = 0.0
        # Background task support
        self._background_task: asyncio.Task | None = None
        self._notify_callback: Callable[[str], Coroutine] | None = None
        # Dedup tracking: proposal_hash → send_count
        self._proposal_tracker: dict[str, int] = {}
        self._dedup_path = ""  # set by set_memory_path or inferred from memory_mgr

    def reset_cooldown(self):
        """Reset the cooldown timer so next check runs immediately."""
        self._last_check = 0.0

    def set_notify_callback(self, callback: Callable[[str], Coroutine] | None):
        """Register an async callback that receives inspection proposals.

        The callback is called from the background loop with the proposal
        text whenever ``inspect()`` returns a non-None result.
        """
        self._notify_callback = callback

    async def start_background(self, interval: int = _INSPECTION_COOLDOWN):
        """Start the background inspection loop.

        Runs ``inspect()`` every ``interval`` seconds and pushes any
        proposal through the registered notification callback.
        """
        if self._background_task is not None:
            logger.warning("Background inspector already running")
            return
        self._background_task = asyncio.create_task(self._background_loop(interval))
        logger.info("IdleInspector background task started (interval=%ds)", interval)

    async def stop_background(self):
        """Stop the background inspection loop."""
        if self._background_task is None:
            return
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:
            pass
        self._background_task = None
        logger.info("IdleInspector background task stopped")

    async def _background_loop(self, interval: int):
        """Periodic inspection loop."""
        while True:
            await asyncio.sleep(interval)
            try:
                proposal = self.inspect()
                if proposal and self._notify_callback:
                    await self._notify_callback(proposal)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Background inspection failed: %s", exc)

    def inspect(self) -> str | None:
        """Run idle inspection. Returns a proposal message and stores it for the agent."""
        now = time.time()
        if now - self._last_check < _INSPECTION_COOLDOWN:
            return None
        self._last_check = now

        # Phase 1: Check for due tasks (real work)
        proposal = self._check_due_tasks()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 2: Evolution-driven refinements (SOP skips, usage milestones)
        proposal = self._check_evolution_refinements()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 2b: Task/tool/skill usage analysis & improvement suggestions
        proposal = self._check_usage_patterns()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 3: Memory health (density, archives)
        proposal = self._check_memory_health()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 4: Tool failure patterns → self_improve
        proposal = self._check_tool_failures()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 5: Correction patterns → modify_own_prompt
        proposal = self._check_correction_patterns()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        return None

    def _store_proposal(self, proposal: str):
        """Store a proposal, respecting dedup limit (max 3 same proposals)."""
        if not self._memory_mgr:
            return

        # Dedup: hash proposal content, track send count
        import hashlib
        phash = hashlib.md5(proposal.encode()).hexdigest()
        self._dedup_path = os.path.join(
            self._memory_mgr._dir, "proposal_dedup.json"
        ) if not self._dedup_path else self._dedup_path
        self._load_dedup_tracker()

        count = self._proposal_tracker.get(phash, 0) + 1
        if count > _MAX_PROPOSAL_REPEATS:
            logger.debug("Proposal %s suppressed (sent %d times)", phash, count - 1)
            return

        self._proposal_tracker[phash] = count
        self._save_dedup_tracker()

        try:
            self._memory_mgr.save_pending_proposal(proposal)
            logger.info("Stored proposal (send #%d/%d)", count, _MAX_PROPOSAL_REPEATS)
        except Exception as exc:
            logger.warning("Failed to store pending proposal: %s", exc)

    def _load_dedup_tracker(self):
        if self._dedup_path and os.path.exists(self._dedup_path):
            try:
                with open(self._dedup_path, encoding="utf-8") as f:
                    self._proposal_tracker = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._proposal_tracker = {}

    def _save_dedup_tracker(self):
        if self._dedup_path:
            try:
                os.makedirs(os.path.dirname(self._dedup_path), exist_ok=True)
                with open(self._dedup_path, "w", encoding="utf-8") as f:
                    json.dump(self._proposal_tracker, f)
            except OSError as exc:
                logger.warning("Failed to save dedup tracker: %s", exc)

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

    def _check_usage_patterns(self) -> str | None:
        """Analyze task/tool/skill usage patterns and propose improvements.

        Checks EvolutionEngine stats for:
        - Skills with heavy usage but no crystallization: suggest optimization
        - Skills with high skip rates: suggest SOP refinement
        - Tool failure clusters: suggest self_improve repairs
        - Recently crystallized skills: notify user
        """
        if not self._evolution:
            return None

        suggestions = []

        try:
            stats = self._evolution.stats
        except Exception:
            return None

        # 1. Skills with heavy usage (>=5) but no crystal yet
        skill_usage = stats.get("skill_usage", {})
        crystallized_names = {c["name"] for c in stats.get("crystallized", [])}
        for skill_name, count in sorted(skill_usage.items(), key=lambda x: -x[1]):
            if count >= 5 and skill_name not in crystallized_names:
                suggestions.append(
                    f"**{skill_name}** 已使用 {count} 次，达到结晶阈值但尚未生成固化技能。"
                    f"建议使用 ``self_improve`` 触发自动结晶。"
                )

        # 2. SOP steps with high skip counts → suggest removal
        sop_skips = stats.get("sop_skips", {})
        for skill_name, steps in sop_skips.items():
            if not isinstance(steps, dict):
                continue
            for step_name, skip_count in steps.items():
                if skip_count >= 5:
                    suggestions.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，"
                        f"强烈建议从 SOP 中移除。"
                    )
                elif skip_count >= 3:
                    suggestions.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，"
                        f"可考虑移除该步骤。"
                    )

        # 3. Recently crystallized skills — notify
        for c in stats.get("crystallized", []):
            name = c.get("name", "?")
            created = c.get("created", "")[:10]
            suggestions.append(
                f"**{name}** 已成功固化为技能（{created}），可查看 skill 目录确认。"
            )

        # 4. Check tool failure stats from memory
        if self._memory_mgr:
            try:
                failures = self._memory_mgr.count_tool_failures()
                if failures.get("total", 0) > 0:
                    by_tool = failures.get("by_tool", {})
                    for tool, count in sorted(by_tool.items(), key=lambda x: -x[1])[:3]:
                        if count >= 3:
                            suggestions.append(
                                f"**{tool}** 累计失败 {count} 次，需检查修复。"
                            )
            except Exception:
                pass

        if not suggestions:
            return None

        # Deduplicate (same text may appear from different sources)
        seen = set()
        unique_suggestions = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                unique_suggestions.append(s)

        proposal = (
            "📊 使用模式分析：发现以下可优化项：\n\n"
            + "\n".join(f"  - {s}" for s in unique_suggestions)
            + "\n\n是否进行优化？请确认。"
        )
        return proposal

    def _check_memory_health(self) -> str | None:
        """Check memory system health — density and archive status.

        Suggests cleanup when HOT memory approaches capacity or archived
        topics could be promoted back.
        """
        if not self._memory_mgr:
            return None

        suggestions = []

        # 1. Check HOT memory density
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

        # 2. Check archive topics for potential review
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

        # Skip known stub/limitation tools — their "failures" are by design
        _STUB_TOOLS = frozenset({
            "wechat_contacts", "wechat_history", "wechat_sessions", "wechat_search",
        })

        tool_counts = Counter(f["tool"] for f in failures if f["tool"] not in _STUB_TOOLS)

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

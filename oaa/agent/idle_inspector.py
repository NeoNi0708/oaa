"""Idle inspector — proactively checks for tasks and self-improvement opportunities.

``inspect()`` can be called synchronously (from end of ``process_message``)
or driven periodically by a background asyncio task managed via
:meth:`start_background` / :meth:`stop_background`.

Structured proposals are stored in ``ProposalStore`` (JSON) instead of
free-text ``pending_proposals.md``.  A persistent ignore list prevents
repeated proposals for tools the user has dismissed.
"""
import asyncio
import json
import os
import time
from collections import Counter
from typing import TYPE_CHECKING, Callable, Coroutine, Optional

from ..async_io import async_write_json
from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler
    from .memory_manager import MemoryManager
    from .proposal import ProposalStore

logger = get_logger("agent.idle_inspector")

# Minimum seconds between idle inspections
_INSPECTION_COOLDOWN = 1800  # 30 minutes (lineA — lightweight checks only)

# LineC — daily heavy checks (memory health, self-learning, etc.)
_LINE_C_COOLDOWN = 86400  # 24 hours

# Disk usage — weekly check (moved from lineA, now part of lineC)
_DISK_CHECK_COOLDOWN = 604800  # 7 days

# Max times the same proposal is delivered before suppression
_MAX_PROPOSAL_REPEATS = 3


class IdleInspector:
    """Multi-line idle inspection: LineA(background), LineB(task-triggered), LineC(daily), LineD(immediate).

    LineA — Background loop every ``_INSPECTION_COOLDOWN`` (30 min):
        channel_health, memory_usage (lightweight, no LLM)
    LineB — Task-triggered (对话完成 + idle ≥15 min):
        tool_failures (current task only), usage_patterns (current task only)
    LineC — Daily schedule (off-peak):
        memory_health, correction_patterns, self-learning (LLM), disk_usage (weekly)
    LineD — Immediate:
        due_tasks (auto-execute, no confirmation needed)
    """

    def __init__(self, scheduler: Optional["TaskScheduler"] = None,
                 memory_mgr: Optional["MemoryManager"] = None,
                 evolution: Optional["EvolutionEngine"] = None,
                 proposal_store: Optional["ProposalStore"] = None,
                 channel_adapters: Optional[dict] = None,
                 llm: Optional[object] = None):
        self._scheduler = scheduler
        self._memory_mgr = memory_mgr
        self._evolution = evolution
        self._proposal_store = proposal_store
        self._channel_adapters = channel_adapters or {}
        self._llm = llm
        self._last_check: float = time.time()
        self._last_line_c_check: float = 0.0
        self._last_disk_check: float = 0.0
        self._last_activity_time: float = time.time()
        self._last_task_tools: set = set()
        self._last_task_skills: set = set()
        # Background task support
        self._background_task: asyncio.Task | None = None
        self._notify_callback: Callable[[str], Coroutine] | None = None
        self._executor_callback: Callable[[dict], Coroutine] | None = None
        # Dedup tracking: proposal_hash → send_count
        self._proposal_tracker: dict[str, int] = {}
        self._dedup_path = ""  # set by set_memory_path or inferred from memory_mgr
        # Persistent ignore list (tool_name → "once" | "forever")
        self._ignore_list: dict[str, str] = {}
        self._ignore_path = ""
        self._load_ignore_list()
        # Pause flag — set by repair_loop to prevent nested inspection during self-healing
        self._paused: bool = False

    def reset_cooldown(self):
        """Reset the cooldown timer so next check runs immediately."""
        self._last_check = 0.0

    def set_last_activity_time(self):
        """Record current time as last user activity (for lineB idle detection)."""
        self._last_activity_time = time.time()

    def record_task_context(self, tools: set, skills: set):
        """Record tools/skills used in the most recent task (for lineB filtering)."""
        self._last_task_tools = tools
        self._last_task_skills = skills

    # ------------------------------------------------------------------
    # Ignore list — persistent tool/pattern suppression
    # ------------------------------------------------------------------

    def set_memory_path(self, memory_dir: str):
        """Set the memory directory for dedup and ignore list persistence."""
        self._dedup_path = os.path.join(memory_dir, "proposal_dedup.json")
        self._ignore_path = os.path.join(memory_dir, "proposal_ignore.json")
        self._load_ignore_list()

    def _load_ignore_list(self):
        if self._ignore_path and os.path.exists(self._ignore_path):
            try:
                with open(self._ignore_path, encoding="utf-8") as f:
                    self._ignore_list = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._ignore_list = {}

    def _save_ignore_list(self):
        if self._ignore_path:
            try:
                os.makedirs(os.path.dirname(self._ignore_path), exist_ok=True)
                with open(self._ignore_path, "w", encoding="utf-8") as f:
                    json.dump(self._ignore_list, f, indent=2, ensure_ascii=False)
            except OSError as exc:
                logger.warning("Failed to save ignore list: %s", exc)

    def is_tool_ignored(self, tool_name: str) -> bool:
        """Check if *tool_name* is in the ignore list."""
        mode = self._ignore_list.get(tool_name, "")
        if mode == "forever":
            return True
        if mode == "once":
            # Consume the once-ignore after this check
            del self._ignore_list[tool_name]
            self._save_ignore_list()
            return True
        return False

    def ignore_tool(self, tool_name: str, permanent: bool = False):
        """Add *tool_name* to the ignore list.

        Args:
            tool_name: Tool or pattern name to ignore.
            permanent: If True, ignored forever; if False, skipped once.
        """
        self._ignore_list[tool_name] = "forever" if permanent else "once"
        self._save_ignore_list()
        logger.info("Tool '%s' set to ignore (%s)", tool_name, "forever" if permanent else "once")

    def set_notify_callback(self, callback: Callable[[str], Coroutine] | None):
        """Register an async callback that receives inspection proposals.

        The callback is called from the background loop with the proposal
        text whenever ``inspect()`` returns a non-None result.
        """
        self._notify_callback = callback

    def set_executor_callback(self, callback: Callable[[dict], Coroutine] | None):
        """Register an async callback for auto-executing scheduled tasks.

        The callback receives the full task dict (including
        ``execution_prompt`` and ``delivery_channels``) and should run
        the agent with the prompt, then deliver results.
        """
        self._executor_callback = callback

    def pause(self):
        """Temporarily suppress all inspections (e.g. during repair_loop)."""
        self._paused = True
        logger.info("IdleInspector paused")

    def resume(self):
        """Resume normal inspection after a pause."""
        self._paused = False
        self._last_check = time.time()  # reset cooldown so next check is fresh
        logger.info("IdleInspector resumed")

    def is_paused(self) -> bool:
        return self._paused

    async def start_background(self, interval: int = _INSPECTION_COOLDOWN):
        """Start the background inspection loop.

        Runs an initial full-sweep inspection immediately, then continues
        with ``inspect()`` every ``interval`` seconds, pushing any proposal
        through the registered notification callback.
        """
        if self._background_task is not None:
            logger.warning("Background inspector already running")
            return

        # Initial full-sweep: run all phases once immediately so proposals
        # are available in EvolutionView without waiting for the first cycle.
        self.reset_cooldown()
        try:
            await self._inspect_all_phases()
        except Exception as exc:
            logger.warning("Initial full-sweep inspection failed: %s", exc)

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
        """Periodic inspection loop — lineA(30min) + lineB(task-triggered, 15min idle) + lineC(daily)."""
        while True:
            await asyncio.sleep(interval)
            if self._paused:
                continue
            try:
                # LineA + LineD: lightweight periodic checks
                proposal = await self.inspect()
                if proposal and self._notify_callback:
                    await self._notify_callback(proposal)

                # LineB: task-triggered checks (only if idle >= 15 min since last task)
                if self._last_task_tools and (time.time() - self._last_activity_time) >= 900:
                    b_proposal = await self.inspect_line_b()
                    if b_proposal and self._notify_callback:
                        await self._notify_callback(b_proposal)

                # LineC: daily heavy checks (memory health, self-learning, disk usage)
                if time.time() - self._last_line_c_check >= _LINE_C_COOLDOWN:
                    c_proposal = await self._inspect_line_c()
                    if c_proposal and self._notify_callback:
                        await self._notify_callback(c_proposal)
                    self._last_line_c_check = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Background inspection failed: %s", exc)

    async def _inspect_all_phases(self):
        """Run all inspection phases once, creating structured proposals.

        Does NOT send notifications through the callback — only populates
        the ProposalStore so EvolutionView has data to display immediately.
        """
        for phase_method in [
            self._check_due_tasks,
            self._check_evolution_refinements,
            self._check_usage_patterns,
            self._check_memory_health,
            self._check_tool_failures,
            self._check_correction_patterns,
            self._check_disk_usage,
            self._check_channel_health,
            self._check_memory_usage,
        ]:
            try:
                if asyncio.iscoroutinefunction(phase_method):
                    result = await phase_method()
                else:
                    result = phase_method()
                if result and isinstance(result, str):
                    await self._store_proposal(result)
            except Exception as exc:
                logger.debug("Startup phase %s: %s", phase_method.__name__, exc)

    async def inspect(self) -> str | None:
        """Run lineA + lineD inspections (background, lightweight, every 30 min).

        LineA: channel_health, memory_usage
        LineD: due_tasks (auto-execute, no confirmation)
        """
        if self._paused:
            return None
        now = time.time()
        if now - self._last_check < _INSPECTION_COOLDOWN:
            return None
        self._last_check = now

        # LineD: due tasks (immediate)
        proposal = self._check_due_tasks()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        # LineA: channel health
        proposal = self._check_channel_health()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        # LineA: memory usage
        proposal = self._check_memory_usage()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        return None

    async def inspect_line_b(self) -> str | None:
        """Run lineB inspections (task-triggered, filtered by last task context).

        Only checks tools/skills used in the most recent task.
        Requires 15+ min idle since last activity (checked by caller —
        :meth:`_background_loop` checks ``_last_activity_time``).
        """
        proposal = await self._check_tool_failures(tool_filter=self._last_task_tools)
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        proposal = await self._check_usage_patterns(skill_filter=self._last_task_skills)
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        return None

    async def _inspect_line_c(self) -> str | None:
        """Run lineC inspections (daily, off-peak, heavy checks involving LLM).

        Checks run in priority order — returns the first proposal found:
        1. Memory health (HOT density, archive status)
        2. Correction patterns (repeated lessons)
        3. Disk usage (weekly only, not every day)
        4. Self-learning (LLM-based skill gap analysis)
        """
        # 1. Memory health — no LLM needed
        proposal = self._check_memory_health()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        # 2. Correction patterns — reads correction history
        proposal = await self._check_correction_patterns()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        # 3. Disk usage — weekly granularity
        if time.time() - self._last_disk_check >= _DISK_CHECK_COOLDOWN:
            proposal = self._check_disk_usage()
            if proposal:
                if await self._store_proposal(proposal):
                    return proposal
            self._last_disk_check = time.time()

        # 4. Self-learning — LLM-based skill gap analysis (most expensive)
        proposal = await self._self_learn()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        return None

    async def _self_learn(self) -> str | None:
        """Self-learning: analyze skill usage patterns with LLM, propose exploration.

        Heavy operation — only runs in lineC (daily). Uses LLM to identify
        gaps in the current skill set and suggest ClawHub/GitHub searches.
        Does NOT execute searches itself — creates a proposal the agent can
        act on when approved.
        """
        if not self._evolution:
            return None

        try:
            stats = self._evolution.stats
        except Exception:
            return None

        skill_usage = stats.get("skill_usage", {})
        if not skill_usage:
            return None

        # Find top 3 most used skills/domains
        top_skills = sorted(skill_usage.items(), key=lambda x: -x[1])[:5]
        top_summary = "\n".join(f"  - {name}: {count}次" for name, count in top_skills)

        # Check already installed/crystallized skills
        crystallized_names = {c["name"] for c in stats.get("crystallized", [])}

        # Use LLM to analyze skill gaps (skipped if no LLM available)
        llm_analysis = ""
        if self._llm and top_skills:
            try:
                prompt = (
                    "根据以下当前技能使用数据，分析 agent 可能缺少什么有用的技能。\n"
                    "只输出 1-2 句分析结论，不要列清单。\n\n"
                    f"常用技能:\n{top_summary}\n"
                    f"已固化技能: {', '.join(crystallized_names) if crystallized_names else '无'}\n"
                )
                llm_response = await self._llm.chat([{"role": "user", "content": prompt}])
                llm_analysis = getattr(llm_response, 'content', '') or ''
            except Exception as exc:
                logger.debug("Self-learn LLM skipped: %s", exc)

        # Build a concise proposal
        lines = [
            "🌙 日调度分析：以下技能使用频繁，可考虑扩展能力：",
            "",
            top_summary,
        ]
        if crystallized_names:
            lines.append(f"\n已固化: {', '.join(crystallized_names)}")
        if llm_analysis:
            lines.append(f"\n{llm_analysis}")
        lines.append(
            "\n建议: 用 ``skill_search`` 探索 ClawHub/GitHub 上是否有更好的替代技能，"
            "或用 ``skill_create`` 将高频操作固化为新技能。"
        )
        lines.append("\n在进化工厂页面点击「批准执行」或「忽略」")

        return "\n".join(lines)

    async def _store_proposal(self, proposal_text: str, dedup_key: str = "") -> bool:
        """Store a proposal notification, respecting dedup limit.

        Args:
            proposal_text: The notification text to display.
            dedup_key: Stable identifier for dedup (e.g. tool name + type).
                       If empty, derived from the emoji + first bolded item.
        Returns:
            True if the notification should be sent (within repeat limit),
            False if suppressed (sent too many times already).
        """
        if not self._memory_mgr:
            return True  # allow through if we can't track
        import hashlib, re as _re
        if not dedup_key:
            m = _re.search(r'([\U0001F300-\U0001FAFF]).*?\*\*(.+?)\*\*', proposal_text)
            if m:
                dedup_key = f"{m.group(1)}:{m.group(2)}"
            else:
                dedup_key = proposal_text[:80]
        phash = hashlib.md5(dedup_key.encode()).hexdigest()
        if not self._dedup_path and self._memory_mgr:
            self.set_memory_path(str(self._memory_mgr._dir))
        self._load_dedup_tracker()

        count = self._proposal_tracker.get(phash, 0) + 1
        if count > _MAX_PROPOSAL_REPEATS:
            logger.info("Proposal %s suppressed (sent %d times)", dedup_key, count - 1)
            return False

        self._proposal_tracker[phash] = count
        await self._save_dedup_tracker()
        return True

    def _load_dedup_tracker(self):
        if self._dedup_path and os.path.exists(self._dedup_path):
            try:
                with open(self._dedup_path, encoding="utf-8") as f:
                    self._proposal_tracker = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._proposal_tracker = {}

    async def _save_dedup_tracker(self):
        if self._dedup_path:
            try:
                await async_write_json(self._dedup_path, self._proposal_tracker, indent=2)
            except OSError as exc:
                logger.warning("Failed to save dedup tracker: %s", exc)

    def _check_due_tasks(self) -> str | None:
        """Check TaskScheduler for due tasks.

        Two paths:
        - Tasks WITH ``execution_prompt`` → auto-execute via executor callback
        - Tasks WITHOUT ``execution_prompt`` → return proposal for user approval
          (backward-compatible: old-style simple reminders)
        """
        if not self._scheduler:
            return None

        try:
            due = self._scheduler.get_due_tasks()
        except Exception as exc:
            logger.warning("Idle check: scheduler error: %s", exc)
            return None

        if not due:
            return None

        # Split: auto-exec tasks vs manual-approval tasks
        auto_tasks = [t for t in due if t.get("execution_prompt", "").strip()]
        manual_tasks = [t for t in due if not t.get("execution_prompt", "").strip()]

        # Auto-execute tasks with execution_prompt
        if auto_tasks:
            for t in auto_tasks:
                logger.info("Auto-executing scheduled task: %s (%s)", t["name"], t["id"])
                if self._executor_callback:
                    asyncio.create_task(self._executor_callback(t))

        # Manual tasks still go through proposal flow
        if not manual_tasks:
            return None

        lines = []
        for t in manual_tasks:
            name = t.get("name", "?")
            desc = t.get("description", "")
            line = f"  - **{name}**"
            if desc:
                line += f": {desc[:100]}"
            lines.append(line)

        return (
            "🔍 空闲巡检：发现以下定时任务已到执行时间：\n\n"
            + "\n".join(lines)
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
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
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
        )

    async def _check_usage_patterns(self, skill_filter: set[str] | None = None,
                                    tool_filter: set[str] | None = None) -> str | None:
        """Analyze task/tool/skill usage patterns and propose improvements.

        Creates structured proposals for skill crystallization, SOP skips,
        and tool failure clusters.
        """
        if not self._evolution:
            return None

        suggestions_text = []
        try:
            stats = self._evolution.stats
        except Exception:
            return None

        # 1. Skills with heavy usage (>=5) but no crystal yet
        skill_usage = stats.get("skill_usage", {})
        crystallized_names = {c["name"] for c in stats.get("crystallized", [])}
        for skill_name, count in sorted(skill_usage.items(), key=lambda x: -x[1]):
            if count < 5 or skill_name in crystallized_names:
                continue
            if self.is_tool_ignored(f"crystal:{skill_name}"):
                continue
            if skill_filter is not None and skill_name not in skill_filter:
                continue
            if self._proposal_store and self._proposal_store.has_pending_for_target(f"crystal:{skill_name}", "skill_crystallize"):
                continue

            actions = [
                {"tool": "self_improve", "args": {"path": "", "old_content": "", "new_content": "",
                 "description": f"为 {skill_name} 生成固化技能"},
                 "description": f"触发 {skill_name} 的自动结晶"}
            ]
            if self._proposal_store:
                from .proposal import Proposal, TYPE_SKILL_CRYSTALLIZE
                await self._proposal_store.add(Proposal(
                    type=TYPE_SKILL_CRYSTALLIZE,
                    title=f"{skill_name} 可固化为技能",
                    problem=f"{skill_name} 已使用 {count} 次，达到结晶阈值但未生成固化技能",
                    benefit=f"固化后 agent 可直接调用该技能，无需重复加载",
                    target=f"crystal:{skill_name}",
                    actions=actions,
                ))
            suggestions_text.append(
                f"**{skill_name}** 已使用 {count} 次，达到结晶阈值但尚未生成固化技能。"
            )

        # 2. SOP steps with high skip counts → suggest removal
        sop_skips = stats.get("sop_skips", {})
        for skill_name, steps in sop_skips.items():
            if not isinstance(steps, dict):
                continue
            if skill_filter is not None and skill_name not in skill_filter:
                continue
            for step_name, skip_count in steps.items():
                if skip_count < 3:
                    continue
                target = f"sop:{skill_name}/{step_name}"
                if self.is_tool_ignored(target):
                    continue
                if self._proposal_store and self._proposal_store.has_pending_for_target(target, "sop_optimize"):
                    continue

                actions = [
                    {"tool": "read_own_source", "args": {"path": f"skills/{skill_name}/SOP.md"},
                     "description": f"查看 {skill_name} 的 SOP"},
                    {"tool": "self_improve", "args": {"path": f"skills/{skill_name}/SOP.md", "old_content": f"## {step_name}", "new_content": ""},
                     "description": f"从 SOP 中移除 {step_name} 步骤"},
                ]
                if self._proposal_store:
                    from .proposal import Proposal, TYPE_SOP_OPTIMIZE
                    await self._proposal_store.add(Proposal(
                        type=TYPE_SOP_OPTIMIZE,
                        title=f"{skill_name} SOP 步骤「{step_name}」可移除",
                        problem=f"该步骤已跳过 {skip_count} 次，说明不适用或多余",
                        benefit=f"移除后 agent 执行该技能时更简洁高效",
                        target=target,
                        actions=actions,
                    ))
                if skip_count >= 5:
                    suggestions_text.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，强烈建议移除。"
                    )
                else:
                    suggestions_text.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，可考虑移除。"
                    )

        # 3. Recently crystallized skills — notify (no structured proposal needed)
        for c in stats.get("crystallized", []):
            name = c.get("name", "?")
            created = c.get("created", "")[:10]
            suggestions_text.append(
                f"**{name}** 已成功固化为技能（{created}），可查看 skill 目录确认。"
            )

        # 4. Tool failure clusters from memory (with ignore check)
        if self._memory_mgr:
            try:
                failures = self._memory_mgr.count_tool_failures()
                if failures.get("total", 0) > 0:
                    by_tool = failures.get("by_tool", {})
                    for tool, count in sorted(by_tool.items(), key=lambda x: -x[1])[:3]:
                        if count < 3:
                            continue
                        if self.is_tool_ignored(tool):
                            continue
                        if tool_filter is not None and tool not in tool_filter:
                            continue
                        if self._proposal_store and self._proposal_store.has_pending_for_target(tool, "tool_fix"):
                            continue
                        suggestions_text.append(
                            f"**{tool}** 累计失败 {count} 次，需检查修复。"
                        )
            except Exception:
                pass

        if not suggestions_text:
            return None

        proposal = (
            "📊 使用模式分析：发现以下可优化项：\n\n"
            + "\n".join(f"  - {s}" for s in suggestions_text)
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
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
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
        )
        return proposal

    async def _check_tool_failures(self, tool_filter: set[str] | None = None) -> str | None:
        """Check logged tool failures and create structured proposals.

        When a tool fails ≥2 times, creates a ``Proposal`` with structured
        actions (``read_own_source`` → ``self_improve`` → pycache → reload)
        and stores it in ``ProposalStore``.  Returns notification text.
        """
        if not self._memory_mgr:
            return None

        try:
            failures = self._memory_mgr.load_tool_failures(50)
        except Exception:
            return None

        if not failures:
            return None

        # Skip known stub/limitation tools
        _STUB_TOOLS = frozenset()

        tool_counts = Counter(f["tool"] for f in failures if f["tool"] not in _STUB_TOOLS)

        for tool, count in tool_counts.most_common(3):
            if count < 2:
                continue
            # Skip ignored tools
            if self.is_tool_ignored(tool):
                continue
            # Skip if not in tool_filter (lineB filtering)
            if tool_filter is not None and tool not in tool_filter:
                continue
            # Skip if proposal already exists for this tool
            if self._proposal_store and self._proposal_store.has_pending_for_target(tool, "tool_fix"):
                continue

            latest = [f for f in failures if f["tool"] == tool][-1]
            error_snippet = latest.get("error", "")[:120]

            tool_to_file = {
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
                "word_doc": "oaa/agent/extended_tools.py",
                "excel_xlsx": "oaa/agent/extended_tools.py",
                "email_send": "oaa/agent/extended_tools.py",
                "skill_load": "oaa/agent/extended_tools.py",
                "skill_create": "oaa/agent/extended_tools.py",
                "ai_search": "oaa/agent/ai_search_tool.py",
                "web_scan": "oaa/agent/extended_tools.py",
                "plan_create": "oaa/agent/extended_tools.py",
                "plan_update": "oaa/agent/extended_tools.py",
                "plan_list": "oaa/agent/extended_tools.py",
            }
            source_file = tool_to_file.get(tool, "oaa/agent/tools.py")

            # Build problem_context instead of templated actions
            # The repair_loop will feed this to the agent and let it decide
            # the repair approach dynamically.
            problem_context = {
                "type": "tool_failure",
                "tool_name": tool,
                "failure_count": count,
                "last_error": error_snippet,
                "error_history": [f.get("error", "")[:200] for f in failures if f["tool"] == tool][-3:],
                "tool_source": source_file,
            }

            # Create structured proposal (no fixed actions — repair_loop handles it)
            if self._proposal_store:
                from .proposal import Proposal, TYPE_TOOL_FIX
                prop = Proposal(
                    type=TYPE_TOOL_FIX,
                    title=f"{tool} 累计失败 {count} 次需修复",
                    problem=f"工具 {tool} 累计失败 {count} 次。最后错误: {error_snippet}",
                    benefit=f"修复后 {tool} 可正常使用",
                    target=tool,
                    actions=None,  # repair_loop dynamically resolves these
                    problem_context=problem_context,
                )
                await self._proposal_store.add(prop)
                prop_id = prop.id
            else:
                prop_id = ""

            # Return notification text
            lines = [
                f"**{tool}** 最近失败 {count} 次",
                f"  - 最后错误: {error_snippet}",
                f"  - agent 将自动分析根因并选择修复方案",
            ]
            if prop_id:
                lines.append(f"  - 提案ID: {prop_id}（可用 proposal_approve 执行）")

            return (
                "🔧 空闲诊断：发现以下工具存在反复失败：\n\n"
                + "\n\n".join(lines)
                + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
            )

        return None

    async def _check_correction_patterns(self) -> str | None:
        """Check for repeated correction patterns and propose modify_own_prompt.

        When the same lesson appears 2+ times in recent corrections,
        creates a structured Proposal with ``modify_own_prompt`` action.
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

        for lesson, count in repeated[:3]:
            target = f"correction:{lesson[:40]}"
            if self.is_tool_ignored(target):
                continue
            if self._proposal_store and self._proposal_store.has_pending_for_target(target, "config_change"):
                continue

            actions = [
                {"tool": "modify_own_prompt", "args": {"action": "write", "section": "agents", "content": lesson},
                 "description": f"将规则「{lesson}」写入 agents 段"},
            ]
            if self._proposal_store:
                from .proposal import Proposal, TYPE_CONFIG_CHANGE
                await self._proposal_store.add(Proposal(
                    type=TYPE_CONFIG_CHANGE,
                    title=f"修正模式：{lesson[:40]}",
                    problem=f"该教训重复出现 {count} 次，说明 agent 未记住",
                    benefit=f"写入提示词后 agent 不再重复犯错",
                    target=target,
                    actions=actions,
                ))

            return (
                "📝 响应模式优化：发现以下反复修正：\n\n"
                + f"  - **{lesson}**（重复 {count} 次）\n"
                + f"    操作: 用 ``modify_own_prompt action=write section=agents`` "
                + "在 agents 段中加入该规则"
                + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
            )

        return None

    # ------------------------------------------------------------------
    # Phase 6: Disk usage check
    # ------------------------------------------------------------------

    def _check_disk_usage(self) -> str | None:
        """Check disk space usage of the data directory."""
        if not self._memory_mgr:
            return None
        try:
            import shutil
            target = getattr(self._memory_mgr, '_dir', None)
            if target is None:
                return None
            usage = shutil.disk_usage(str(target))
            pct = usage.used / usage.total * 100
            if pct > 90:
                free_gb = usage.free / (1024**3)
                return (
                    "磁盘空间告警：磁盘使用率 {:.1f}%，剩余空间 {:.1f} GB\n\n"
                    "建议清理：\n"
                    "  1. 删除旧的 __pycache__ 目录\n"
                    "  2. 清理 workspace 中不再需要的临时文件\n"
                    "  3. 检查 logs 目录是否有大文件\n\n"
                    "在进化工厂页面点击「批准执行」或「忽略」"
                ).format(pct, free_gb)
        except Exception as exc:
            logger.debug("Disk usage check failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Phase 7: Channel health check
    # ------------------------------------------------------------------

    def _check_channel_health(self) -> str | None:
        """Check each communication channel's connectivity and error state."""
        if not self._channel_adapters:
            return None
        issues = []
        for name, adapter in self._channel_adapters.items():
            try:
                online = (getattr(adapter, 'is_authenticated', False)
                          or getattr(adapter, '_running', False))
                if not online:
                    issues.append(f"  - **{name}**: 离线状态")
                err_count = getattr(adapter, 'error_count', None) or getattr(adapter, '_error_count', 0)
                if err_count and err_count > 0:
                    issues.append(f"  - **{name}**: 有 {err_count} 个待处理错误")
            except Exception as exc:
                issues.append(f"  - **{name}**: 健康检查异常 ({exc})")
        if not issues:
            return None
        return (
            "通道健康检查：发现以下通道问题：\n\n"
            + "\n".join(issues)
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
        )

    # ------------------------------------------------------------------
    # Phase 8: Process memory check
    # ------------------------------------------------------------------

    def _check_memory_usage(self) -> str | None:
        """Check the current process's RSS memory usage (>500 MB alerts)."""
        try:
            try:
                import psutil
                proc = psutil.Process()
                rss_mb = proc.memory_info().rss / (1024**2)
                cpu_pct = proc.cpu_percent(interval=0.1)
            except ImportError:
                rss_mb = 0
                cpu_pct = 0
            if rss_mb > 500:
                return (
                    "内存使用告警：当前进程占用 {:.0f} MB (CPU: {:.0f}%)\n\n"
                    "超过 500 MB 阈值，建议：\n"
                    "  - 检查 message history 是否过长\n"
                    "  - 确认是否有未关闭的文件句柄\n"
                    "  - 必要时重启进程释放内存\n\n"
                    "在进化工厂页面点击「批准执行」或「忽略」"
                ).format(rss_mb, cpu_pct)
        except Exception as exc:
            logger.debug("Memory check failed: %s", exc)
        return None

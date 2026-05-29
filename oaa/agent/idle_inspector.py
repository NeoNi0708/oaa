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
from collections import Counter, defaultdict
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

# Disk usage — weekly standalone check
_DISK_CHECK_COOLDOWN = 604800  # 7 days

# Evolution refinements — every 6 hours (stats change slowly)
_EVOLUTION_CHECK_COOLDOWN = 21600  # 6 hours

# Max times the same proposal is delivered before suppression
_MAX_PROPOSAL_REPEATS = 3


def _chain_display(chain_json: str) -> str:
    """Build a compact human-readable summary from a compressed execution chain JSON.

    Example output: ``shell_run×5(error:timeout) → memory_recall(error)``
    """
    try:
        chain = json.loads(chain_json)
    except (json.JSONDecodeError, TypeError):
        return chain_json[:200]
    parts = []
    for c in chain:
        count = c.get("count", 1)
        tool = c.get("tool", "?")
        status = c.get("status", "?")
        error = c.get("error", "")
        label = f"{tool}×{count}({status})" if count > 1 else f"{tool}({status})"
        if error and status == "error":
            label += f":{error[:60]}"
        parts.append(label)
    return " → ".join(parts)


def _extract_problem_context_tool_fix(tool: str, total: int, error_snippet: str,
                                      chain_summary: str, cats: Counter,
                                      analysis: str) -> dict:
    """Build a standardised ``problem_context`` dict for a tool-fix proposal."""
    return {
        "type": "tool_failure",
        "tool_name": tool,
        "failure_count": total,
        "last_error": error_snippet,
        "chain": chain_summary,
        "categories": dict(cats),
        "analysis": analysis,
    }


class IdleInspector:
    """Multi-line idle inspection: LineA(background), LineB(task-triggered), LineD(immediate).

    LineA — Background loop every ``_INSPECTION_COOLDOWN`` (30 min):
        channel_health, memory_usage (lightweight, no LLM)
    LineB — Task-triggered (对话完成 + idle ≥15 min):
        tool_failures (current task only), usage_patterns (current task only)
    LineD — Immediate:
        due_tasks (auto-execute, no confirmation needed)

    Weekly standalone — disk_usage check (every 7 days)

    Note: LineC (daily: memory_health, correction_patterns, self_learn) was
    removed in Phase 2 to free inspection capacity for task retrospection
    (see Phase 3 — reflection_scheduler).
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
        self._last_disk_check: float = 0.0
        self._last_evolution_check: float = 0.0  # runs on first background iteration
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

    def _should_skip_proposal(self, target: str, ptype: str = "") -> bool:
        """Return True if a proposal for this target should be skipped.

        Skips when: a pending proposal already exists, OR the same target
        was resolved (done/failed) within the last 24 hours.
        """
        if not self._proposal_store:
            return False
        if self._proposal_store.has_pending_for_target(target, ptype):
            return True
        if self._proposal_store.has_recent_for_target(target, ptype):
            return True
        return False

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
        """Periodic inspection loop — lineA(30min) + lineB(task-triggered, 15min idle) + weekly disk."""
        while True:
            await asyncio.sleep(interval)
            if self._paused:
                continue
            try:
                proposal = await self.inspect()
                if proposal and self._notify_callback:
                    await self._notify_callback(proposal)

                if self._last_task_tools and (time.time() - self._last_activity_time) >= 900:
                    b_proposal = await self.inspect_line_b()
                    if b_proposal and self._notify_callback:
                        await self._notify_callback(b_proposal)

                if time.time() - self._last_evolution_check >= _EVOLUTION_CHECK_COOLDOWN:
                    self._last_evolution_check = time.time()
                    e_proposal = await self._check_evolution_refinements()
                    if e_proposal and self._notify_callback:
                        await self._notify_callback(e_proposal)

                    u_proposal = await self._check_usage_patterns()
                    if u_proposal and self._notify_callback:
                        await self._notify_callback(u_proposal)

                if time.time() - self._last_disk_check >= _DISK_CHECK_COOLDOWN:
                    d_proposal = self._check_disk_usage()
                    if d_proposal and self._notify_callback:
                        await self._notify_callback(d_proposal)
                    self._last_disk_check = time.time()
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
            self._check_tool_failures,
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

        proposal = self._check_due_tasks()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        proposal = self._check_channel_health()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        proposal = self._check_memory_usage()
        if proposal:
            if await self._store_proposal(proposal):
                return proposal

        return None

    async def inspect_line_b(self) -> str | None:
        """Run lineB inspections (task-triggered, filtered by last task context)."""
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
        """Legacy — kept for backward compatibility.  Returns None."""
        return None

    async def _store_proposal(self, proposal_text: str, dedup_key: str = "",
                              max_repeats: int = 0) -> bool:
        """Store a proposal notification, respecting dedup limit.

        Dedup is keyed on *dedup_key* (a stable topic identifier), NOT on
        the full text.  Two proposals with the same key are treated as
        duplicates regardless of wording differences.

        Args:
            proposal_text: The notification text to display.
            dedup_key: Stable topic key (e.g. ``\"self_learn\"`` or
                       ``\"tool_fix:code_exec\"``). If empty, auto-derived
                       from the first emoji + bold text in *proposal_text*.
            max_repeats: Max deliveries before suppression (0 = use default).
        Returns:
            True if the notification should be sent, False if suppressed.
        """
        if not self._memory_mgr:
            return True
        if max_repeats <= 0:
            max_repeats = _MAX_PROPOSAL_REPEATS
        import hashlib
        if not dedup_key:
            import re as _re
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
        if count > max_repeats:
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

    # ------------------------------------------------------------------
    # LineD: Due tasks
    # ------------------------------------------------------------------

    def _check_due_tasks(self) -> str | None:
        """Check TaskScheduler for due tasks.

        Two paths:
        - Tasks WITH ``execution_prompt`` → auto-execute via executor callback
        - Tasks WITHOUT ``execution_prompt`` → return proposal for user approval
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

        auto_tasks = [t for t in due if t.get("execution_prompt", "").strip()]
        manual_tasks = [t for t in due if not t.get("execution_prompt", "").strip()]

        if auto_tasks:
            for t in auto_tasks:
                logger.info("Auto-executing scheduled task: %s (%s)", t["name"], t["id"])
                if self._executor_callback:
                    asyncio.create_task(self._executor_callback(t))

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

    # ------------------------------------------------------------------
    # Evolution refinements — SOP skips & skill usage milestones
    # ------------------------------------------------------------------

    async def _check_evolution_refinements(self) -> str | None:
        """Check EvolutionEngine for auto-refinements (SOP skips, usage milestones).

        Creates structured Proposal objects in the ProposalStore for each
        actionable refinement so EvolutionView can display them immediately.
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

        notification_lines = []
        for r in refinements:
            if r["type"] == "sop_skip":
                result = await self._handle_sop_skip_refinement(r)
                if result:
                    notification_lines.append(result)
            elif r["type"] == "skill_optimize":
                result = await self._handle_skill_optimize_refinement(r)
                if result:
                    notification_lines.append(result)

        if not notification_lines:
            return None

        return (
            "🔬 技能优化检测：发现以下可优化项：\n\n"
            + "\n\n".join(notification_lines)
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
        )

    async def _handle_sop_skip_refinement(self, r: dict) -> str | None:
        """Handle a single SOP-skip refinement: skip check + create proposal."""
        target = f"sop:{r.get('file_path', 'unknown')}/{r.get('step_name', 'unknown')}"
        if self._proposal_store and self._should_skip_proposal(target, "sop_optimize"):
            return None

        actions = [
            {"tool": "read_own_source", "args": {"path": r["file_path"]},
             "description": "查看对应的 SOP 文件"},
            {"tool": "self_improve", "args": {
                "path": r["file_path"],
                "old_content": f"## {r['step_name']}",
                "new_content": "",
             },
             "description": f"从 SOP 中移除 {r['step_name']} 步骤"},
        ]
        if self._proposal_store:
            from .proposal import Proposal, TYPE_SOP_OPTIMIZE
            await self._proposal_store.add(Proposal(
                type=TYPE_SOP_OPTIMIZE,
                title=f"SOP 步骤「{r['step_name']}」可移除",
                problem=r.get("description", f"步骤 {r['step_name']} 被频繁跳过"),
                benefit="移除后 agent 执行更简洁高效",
                target=target,
                actions=actions,
            ))
        return (
            f"  - **SOP 优化**：{r['description']}\n"
            f"    操作: 用 ``self_improve`` 从 ``{r['file_path']}`` 中移除该步骤\n"
            f"    步骤名称: {r['step_name']}"
        )

    async def _handle_skill_optimize_refinement(self, r: dict) -> str | None:
        """Handle a single skill-optimize refinement: skip check + create proposal."""
        target = f"optimize:{r.get('skill_name', 'unknown')}"
        if self._proposal_store and self._should_skip_proposal(target, "skill_optimize"):
            return None

        if self._proposal_store:
            from .proposal import Proposal, TYPE_CONFIG_CHANGE
            await self._proposal_store.add(Proposal(
                type=TYPE_CONFIG_CHANGE,
                title=f"技能「{r.get('skill_name', '未知')}」需优化",
                problem=r.get("description", "技能使用频繁建议优化"),
                benefit="优化后执行效率更高、质量更稳定",
                target=target,
                actions=[
                    {"tool": "code_exec", "args": {"description": r["description"]},
                     "description": "分析使用数据，生成优化建议"},
                ],
            ))
        return (
            f"  - **技能优化**：{r['description']}\n"
            f"    操作: 用 ``code_exec`` 分析使用数据，生成优化建议"
        )

    # ------------------------------------------------------------------
    # Usage pattern analysis — crystallization, SOP skips, tool clusters
    # ------------------------------------------------------------------

    async def _check_usage_patterns(self, skill_filter: set[str] | None = None,
                                    tool_filter: set[str] | None = None) -> str | None:
        """Analyze task/tool/skill usage patterns and propose improvements.

        Creates structured proposals for skill crystallization, SOP skips,
        and tool failure clusters.
        """
        if not self._evolution:
            return None

        suggestions_text: list[str] = []
        try:
            stats = self._evolution.stats
        except Exception:
            return None

        await self._detect_crystallization(suggestions_text, stats, skill_filter)
        await self._detect_sop_skips(suggestions_text, stats, skill_filter)
        self._detect_crystallized_notify(suggestions_text, stats)
        await self._detect_usage_tool_failures(suggestions_text, stats, tool_filter)

        if not suggestions_text:
            return None

        return (
            "📊 使用模式分析：发现以下可优化项：\n\n"
            + "\n".join(f"  - {s}" for s in suggestions_text)
            + "\n\n在进化工厂页面点击「批准执行」或「忽略」"
        )

    async def _detect_crystallization(self, suggestions: list[str], stats: dict,
                                      skill_filter: set[str] | None):
        """Detect skills with heavy usage (>=5) not yet crystallized."""
        skill_usage = stats.get("skill_usage", {})
        crystallized_names = {c["name"] for c in stats.get("crystallized", [])}
        for skill_name, count in sorted(skill_usage.items(), key=lambda x: -x[1]):
            if count < 5 or skill_name in crystallized_names:
                continue
            if self.is_tool_ignored(f"crystal:{skill_name}"):
                continue
            if skill_filter is not None and skill_name not in skill_filter:
                continue
            if self._proposal_store and self._should_skip_proposal(f"crystal:{skill_name}", "skill_crystallize"):
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
                    benefit="固化后 agent 可直接调用该技能，无需重复加载",
                    target=f"crystal:{skill_name}",
                    actions=actions,
                ))
            suggestions.append(
                f"**{skill_name}** 已使用 {count} 次，达到结晶阈值但尚未生成固化技能。"
            )

    async def _detect_sop_skips(self, suggestions: list[str], stats: dict,
                          skill_filter: set[str] | None):
        """Detect SOP steps with high skip counts (>=3)."""
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
                if self._proposal_store and self._should_skip_proposal(target, "sop_optimize"):
                    continue

                actions = [
                    {"tool": "read_own_source", "args": {"path": f"skills/{skill_name}/SOP.md"},
                     "description": f"查看 {skill_name} 的 SOP"},
                    {"tool": "self_improve", "args": {"path": f"skills/{skill_name}/SOP.md",
                     "old_content": f"## {step_name}", "new_content": ""},
                     "description": f"从 SOP 中移除 {step_name} 步骤"},
                ]
                if self._proposal_store:
                    from .proposal import Proposal, TYPE_SOP_OPTIMIZE
                    await self._proposal_store.add(Proposal(
                        type=TYPE_SOP_OPTIMIZE,
                        title=f"{skill_name} SOP 步骤「{step_name}」可移除",
                        problem=f"该步骤已跳过 {skip_count} 次，说明不适用或多余",
                        benefit="移除后 agent 执行该技能时更简洁高效",
                        target=target,
                        actions=actions,
                    ))
                if skip_count >= 5:
                    suggestions.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，强烈建议移除。"
                    )
                else:
                    suggestions.append(
                        f"**{skill_name}** 的 SOP 步骤「{step_name}」已跳过 {skip_count} 次，可考虑移除。"
                    )

    def _detect_crystallized_notify(self, suggestions: list[str], stats: dict):
        """Add notifications for recently crystallized skills."""
        for c in stats.get("crystallized", []):
            name = c.get("name", "?")
            created = c.get("created", "")[:10]
            suggestions.append(
                f"**{name}** 已成功固化为技能（{created}），可查看 skill 目录确认。"
            )

    async def _detect_usage_tool_failures(self, suggestions: list[str], stats: dict,
                                           tool_filter: set[str] | None):
        """Legacy — superseded by _check_tool_failures (execution-chain-aware).
        Kept as no-op for backward compat."""
        return

    # ------------------------------------------------------------------
    # Tool failure analysis — execution-chain-aware root cause
    # ------------------------------------------------------------------

    async def _check_tool_failures(self, tool_filter: set[str] | None = None) -> str | None:
        """Check logged tool failures with execution-chain-aware root cause analysis.

        Groups failures by task (not by tool), analyzes the full execution chain
        to distinguish strategy errors from tool bugs.

        Delegates to ``_analyze_task_failures`` (Tier 1) and
        ``_analyze_orphan_failures`` (Tier 2).
        """
        if not self._memory_mgr:
            return None

        try:
            failures = self._memory_mgr.load_tool_failures(50)
        except Exception:
            return None
        if not failures:
            return None

        _SKIP_ERRORS = ("未初始化", "not initialized", "未配置")
        valid_failures = [
            f for f in failures
            if f["tool"] not in frozenset()
            and not any(s in f.get("error", "") for s in _SKIP_ERRORS)
        ]
        if not valid_failures:
            return None

        try:
            successes = self._memory_mgr.load_tool_successes(100)
        except Exception:
            successes = []

        # Group by task for chain-aware analysis
        by_task: dict[str, list[dict]] = defaultdict(list)
        for f in valid_failures:
            task_key = f.get("task_id") or f.get("context", "")[:80]
            if task_key:
                by_task[task_key].append(f)

        # Orphan failures (no task context) — fall back to per-tool grouping
        orphan_failures = [f for f in valid_failures
                           if not (f.get("task_id") or f.get("context", ""))]
        by_tool_fallback: dict[str, list[dict]] = defaultdict(list)
        for f in orphan_failures:
            by_tool_fallback[f["tool"]].append(f)

        # Tier 1: task-level analysis
        for task_key, task_fails in by_task.items():
            if len(task_fails) < 2:
                continue
            result = await self._analyze_task_failures(task_key, task_fails, successes)
            if result:
                return result

        # Tier 2: orphan tool failures
        for tool, tool_fails in by_tool_fallback.items():
            if len(tool_fails) < 2:
                continue
            if tool_filter is not None and tool not in tool_filter:
                continue
            if self.is_tool_ignored(tool):
                continue
            if self._proposal_store and self._should_skip_proposal(tool, "tool_fix"):
                continue
            result = await self._analyze_orphan_failures(tool, tool_fails, successes)
            if result:
                return result

        return None

    async def _analyze_task_failures(self, task_key: str, task_fails: list[dict],
                                      successes: list[dict]) -> str | None:
        """Analyze a single task's failure chain and return notification or None.

        Checks:
        1. All tools resolved by subsequent successes → skip
        2. strategy_error → add correction, return notification
        3. tool_bug (>=2) → create tool-fix proposal, return notification
        4. llm_error + parameter_error (>=3) → add correction, continue
        5. unknown (>=2) → LLM chain analysis or conservative fallback
        """
        task_tools = set(f["tool"] for f in task_fails)
        all_resolved = True
        for tool in task_tools:
            tool_successes = [s for s in successes if s["tool"] == tool]
            if not tool_successes:
                all_resolved = False
                break
            last_fail_ts = max(f.get("timestamp", "") for f in task_fails)
            last_success_ts = max(s.get("timestamp", "") for s in tool_successes)
            if last_success_ts < last_fail_ts:
                all_resolved = False
                break
        if all_resolved:
            return None

        last_fail = task_fails[-1]
        chain_raw = last_fail.get("chain", "")
        chain_summary = _chain_display(chain_raw) if chain_raw else "（无链信息）"
        task_context = last_fail.get("context", task_key)[:150]
        error_snippet = last_fail.get("error", "")[:120]
        cats = Counter(f.get("category", "unknown") for f in task_fails)

        # --- strategy_error ---
        if cats.get("strategy_error", 0) >= 1:
            lesson = (
                f"任务「{task_context}」的执行链显示策略选择不当。"
                f"链: {chain_summary}"
            )
            logger.info("Task %s: strategy error detected — chain: %s", task_key, chain_summary)
            if self._memory_mgr:
                await self._memory_mgr.add_correction(
                    context=f"任务「{task_context}」的策略失误",
                    lesson=lesson,
                )
            return (
                "🔧 空闲诊断：发现任务执行策略问题\n\n"
                "  - 任务: {}\n"
                "  - 执行链: {}\n"
                "  - 类别: 策略失误 — agent 已记住教训\n\n"
                "无需操作，agent 下次会自行调整策略。"
            ).format(task_context, chain_summary)

        # --- tool_bug ---
        if cats.get("tool_bug", 0) >= 2:
            affected_tools = {f["tool"] for f in task_fails if f.get("category") == "tool_bug"}
            tool_list = "、".join(sorted(affected_tools)[:3])
            problem_context = {
                "type": "tool_failure",
                "task_context": task_context,
                "chain": chain_summary,
                "affected_tools": tool_list,
                "failure_count": len(task_fails),
                "last_error": error_snippet,
                "categories": dict(cats),
                "analysis": "执行链中检测到代码异常 — 需检查源码修复",
            }
            if self._proposal_store:
                from .proposal import Proposal, TYPE_TOOL_FIX
                prop = Proposal(
                    type=TYPE_TOOL_FIX,
                    title=f"任务链工具异常: {tool_list}",
                    problem=f"任务「{task_context}」执行链中 {tool_list} 失败 {cats.get('tool_bug', 0)} 次。链: {chain_summary}",
                    benefit="修复后相关工具可正常使用",
                    target=tool_list,
                    actions=None,
                    problem_context=problem_context,
                )
                await self._proposal_store.add(prop)
            return (
                "🔧 空闲诊断：发现任务执行链存在代码异常\n\n"
                "  - 任务: {}\n"
                "  - 受影响工具: {}\n"
                "  - 链: {}\n\n"
                "在进化工厂页面点击「批准执行」或「忽略」"
            ).format(task_context, tool_list, chain_summary)

        # --- llm_error + parameter_error ---
        llm_count = cats.get("llm_error", 0)
        param_count = cats.get("parameter_error", 0)
        if llm_count + param_count >= 3:
            if llm_count >= param_count:
                errors = [f.get("error", "")[:150] for f in task_fails
                          if f.get("category") == "llm_error"]
                top_err = Counter(e for e in errors if e).most_common(1)
                err_detail = top_err[0][0] if top_err else error_snippet
                lesson = (
                    f"任务「{task_context}」中多次因LLM参数选择不当失败。"
                    f"常见错误: {err_detail}。链: {chain_summary}"
                )
            else:
                errors = [f.get("error", "")[:150] for f in task_fails
                          if f.get("category") == "parameter_error"]
                top_err = Counter(e for e in errors if e).most_common(1)
                err_detail = top_err[0][0] if top_err else error_snippet
                lesson = (
                    f"任务「{task_context}」中参数错误。"
                    f"常见错误: {err_detail}。链: {chain_summary}"
                )

            logger.info("Task %s: creating correction entry (llm/param error), not tool-fix", task_key)
            if self._memory_mgr:
                await self._memory_mgr.add_correction(
                    context=f"任务「{task_context}」的 {len(task_fails)} 次失败",
                    lesson=lesson,
                )
            return None

        # --- unknown category ---
        return await self._analyze_unknown_task_failures(
            task_key, task_fails, task_context, chain_summary, error_snippet, cats,
        )

    async def _analyze_unknown_task_failures(self, task_key: str, task_fails: list[dict],
                                              task_context: str, chain_summary: str,
                                              error_snippet: str, cats: Counter) -> str | None:
        """Handle unknown-category task failures — LLM analysis or conservative fallback."""
        if cats.get("unknown", 0) < 2:
            return None

        if self._llm:
            analysis = await self._llm_analyze_task_chain(
                task_context, task_fails, chain_summary, cats,
            )
            if analysis:
                rec = analysis.get("recommendation", "")
                reasoning = analysis.get("reasoning", "")[:200]
                if rec == "tool_bug":
                    problem_context = {
                        "type": "tool_failure",
                        "task_context": task_context,
                        "chain": chain_summary,
                        "failure_count": len(task_fails),
                        "last_error": error_snippet,
                        "categories": dict(cats),
                        "analysis": f"LLM 链分析: {reasoning}",
                    }
                    if self._proposal_store:
                        from .proposal import Proposal, TYPE_TOOL_FIX
                        await self._proposal_store.add(Proposal(
                            type=TYPE_TOOL_FIX,
                            title=f"执行链异常需修复: {task_key[:40]}",
                            problem=f"任务「{task_context}」执行链异常。LLM 分析: {reasoning}",
                            benefit="修复后任务可正常执行",
                            target=task_context[:40],
                            actions=None,
                            problem_context=problem_context,
                        ))
                    return (
                        "🔧 空闲诊断：任务执行链检测到工具异常\n\n"
                        "  - 任务: {}\n"
                        "  - 链: {}\n"
                        "  - LLM 分析: {}\n\n"
                        "在进化工厂页面点击「批准执行」或「忽略」"
                    ).format(task_context, chain_summary, reasoning)
                elif rec == "strategy_error":
                    if self._memory_mgr:
                        await self._memory_mgr.add_correction(
                            context=f"任务「{task_context}」的策略失误（LLM 分析）",
                            lesson=f"LLM 分析认为该任务的策略选择不当: {reasoning}",
                        )
                    return (
                        "🔧 空闲诊断：任务执行策略需调整\n\n"
                        "  - 任务: {}\n"
                        "  - 链: {}\n"
                        "  - LLM 分析: {}\n\n"
                        "无需操作，agent 已记住教训。"
                    ).format(task_context, chain_summary, reasoning)
                elif rec in ("llm_error", "parameter_error"):
                    if self._memory_mgr:
                        await self._memory_mgr.add_correction(
                            context=f"任务「{task_context}」的 {len(task_fails)} 次失败（LLM 链分析）",
                            lesson=f"链分析认为属于 {rec}: {reasoning}",
                        )
                    return None
                # unknown recommendation — skip
                return None
            # LLM analysis failed — skip
            return None

        # No LLM — conservative fallback per tool within task
        task_tool_cats: dict[str, int] = defaultdict(int)
        for f in task_fails:
            if f.get("category", "unknown") == "unknown":
                task_tool_cats[f["tool"]] += 1
        worst_tool = max(task_tool_cats, key=task_tool_cats.get) if task_tool_cats else ""
        if worst_tool and task_tool_cats[worst_tool] >= 2:
            problem_context = {
                "type": "tool_failure",
                "task_context": task_context,
                "chain": chain_summary,
                "failure_count": len(task_fails),
                "last_error": error_snippet,
                "categories": dict(cats),
                "analysis": f"工具 {worst_tool} 潜在异常（无 LLM 分析）",
            }
            if self._proposal_store:
                from .proposal import Proposal, TYPE_TOOL_FIX
                await self._proposal_store.add(Proposal(
                    type=TYPE_TOOL_FIX,
                    title=f"{worst_tool} 可能有异常需检查",
                    problem=f"任务「{task_context}」中 {worst_tool} 多次失败，无法自动分析根因。链: {chain_summary}",
                    benefit=f"修复后 {worst_tool} 可正常使用",
                    target=worst_tool,
                    actions=None,
                    problem_context=problem_context,
                ))
            return (
                "🔧 空闲诊断：工具 **{}** 可能异常（无法自动分析根因）\n\n"
                "  - 任务: {}\n"
                "  - 链: {}\n"
                "  - 已生成提案，请在进化工厂查看\n\n"
                "在进化工厂页面点击「批准执行」或「忽略」"
            ).format(worst_tool, task_context, chain_summary)

        return None

    async def _analyze_orphan_failures(self, tool: str, tool_fails: list[dict],
                                        successes: list[dict]) -> str | None:
        """Analyze per-tool orphan failures (no task context) — Tier 2 fallback.

        Checks:
        1. Resolved by subsequent success → skip
        2. tool_bug (>=2) → create tool-fix proposal
        3. llm_error + parameter_error (>=3) → add correction
        4. unknown (>=2) → LLM analysis or conservative fallback
        """
        tool_successes = [s for s in successes if s["tool"] == tool]
        if tool_fails and tool_successes:
            last_fail_ts = tool_fails[-1].get("timestamp", "")
            last_success_ts = tool_successes[-1].get("timestamp", "")
            if last_success_ts >= last_fail_ts:
                return None

        cats = Counter(f.get("category", "unknown") for f in tool_fails)
        total = len(tool_fails)
        latest = tool_fails[-1]
        error_snippet = latest.get("error", "")[:120]
        chain_raw = latest.get("chain", "")
        chain_summary = _chain_display(chain_raw) if chain_raw else ""

        if cats.get("tool_bug", 0) >= 2:
            problem_context = _extract_problem_context_tool_fix(
                tool, total, error_snippet, chain_summary, cats,
                "代码异常 — 需检查源码修复",
            )
            if self._proposal_store:
                from .proposal import Proposal, TYPE_TOOL_FIX
                await self._proposal_store.add(Proposal(
                    type=TYPE_TOOL_FIX,
                    title=f"{tool} 失败 {total} 次需修复（无任务上下文）",
                    problem=f"工具 {tool} 累计失败 {total} 次。最后错误: {error_snippet}",
                    benefit=f"修复后 {tool} 可正常使用",
                    target=tool,
                    actions=None,
                    problem_context=problem_context,
                ))
            return (
                "🔧 空闲诊断：发现工具 **{}** 代码异常（失败 {} 次）\n\n"
                "  - 最后错误: {}\n"
                "  - 类别: 代码异常 — agent 将自动分析源码并修复\n\n"
                "在进化工厂页面点击「批准执行」或「忽略」"
            ).format(tool, total, error_snippet)

        llm_count = cats.get("llm_error", 0)
        param_count = cats.get("parameter_error", 0)
        if llm_count + param_count >= 3:
            lesson = f"工具 {tool} 的 {total} 次失败"
            if llm_count >= param_count:
                lesson += "源于LLM选择了错误的参数。"
            else:
                lesson += "源于参数内容错误。"
            if chain_summary:
                lesson += f" 链: {chain_summary}"
            if self._memory_mgr:
                await self._memory_mgr.add_correction(
                    context=f"工具 {tool} 的 {total} 次失败归类为 {'LLM 选择不当' if llm_count >= param_count else '参数错误'}",
                    lesson=lesson,
                )
            return None

        return await self._analyze_unknown_orphan_failures(
            tool, tool_fails, chain_summary, cats, total, error_snippet,
        )

    async def _analyze_unknown_orphan_failures(self, tool: str, tool_fails: list[dict],
                                                chain_summary: str, cats: Counter,
                                                total: int, error_snippet: str) -> str | None:
        """Handle unknown-category orphan failures — LLM analysis or conservative fallback."""
        if cats.get("unknown", 0) < 2:
            return None

        if self._llm:
            analysis = await self._llm_analyze_task_chain(tool, tool_fails, chain_summary, cats)
            if analysis:
                rec = analysis.get("recommendation", "")
                reasoning = analysis.get("reasoning", "")[:200]
                if rec in ("tool_bug",):
                    problem_context = _extract_problem_context_tool_fix(
                        tool, total, error_snippet, chain_summary, cats,
                        f"LLM 分析: {reasoning}",
                    )
                    if self._proposal_store:
                        from .proposal import Proposal, TYPE_TOOL_FIX
                        await self._proposal_store.add(Proposal(
                            type=TYPE_TOOL_FIX,
                            title=f"{tool} 失败 {total} 次需修复",
                            problem=f"工具 {tool} 失败 {total} 次。LLM 分析: {reasoning}",
                            benefit=f"修复后 {tool} 可正常使用",
                            target=tool,
                            actions=None,
                            problem_context=problem_context,
                        ))
                    return (
                        "🔧 空闲诊断：发现工具 **{}** 异常（失败 {} 次）\n\n"
                        "  - 最后错误: {}\n"
                        "  - LLM 分析: {}\n\n"
                        "在进化工厂页面点击「批准执行」或「忽略」"
                    ).format(tool, total, error_snippet, reasoning)
                elif rec in ("llm_error", "parameter_error", "strategy_error"):
                    if self._memory_mgr:
                        await self._memory_mgr.add_correction(
                            context=f"工具 {tool} 的 {total} 次失败（LLM 分析）",
                            lesson=f"LLM 分析认为属于 {rec}: {reasoning}",
                        )
                    return None
                # unknown recommendation — skip
                return None
            logger.debug("LLM analysis returned empty for %s failures, skipping", tool)
            return None

        # No LLM — conservative fallback
        problem_context = _extract_problem_context_tool_fix(
            tool, total, error_snippet, chain_summary, cats,
            "无法确定根因（无 LLM 分析）— 按潜在工具异常处理",
        )
        if self._proposal_store:
            from .proposal import Proposal, TYPE_TOOL_FIX
            await self._proposal_store.add(Proposal(
                type=TYPE_TOOL_FIX,
                title=f"{tool} 失败 {total} 次需检查",
                problem=f"工具 {tool} 累计失败 {total} 次，无法自动分析根因。最后错误: {error_snippet}",
                benefit=f"修复后 {tool} 可正常使用",
                target=tool,
                actions=None,
                problem_context=problem_context,
            ))
        return (
            "🔧 空闲诊断：工具 **{}** 失败 {} 次（无法自动分析根因）\n\n"
            "  - 最后错误: {}\n"
            "  - 链: {}\n"
            "  - 已生成提案，请在进化工厂查看\n\n"
            "在进化工厂页面点击「批准执行」或「忽略」"
        ).format(tool, total, error_snippet, chain_summary)

    async def _llm_analyze_task_chain(self, task_context: str, task_fails: list[dict],
                                       chain_summary: str, cats: Counter) -> dict | None:
        """Use LLM to analyze the full execution chain of a task and determine root cause.

        Returns a dict with ``recommendation`` (``tool_bug`` / ``llm_error`` /
        ``parameter_error`` / ``strategy_error``) and ``reasoning`` (summary text).
        Returns ``None`` if analysis fails or LLM is unavailable.
        """
        if not self._llm:
            return None

        fail_lines = []
        for f in task_fails[-5:]:
            ts = f.get("timestamp", "?")[:16]
            err = f.get("error", "")[:200]
            tool = f.get("tool", "?")
            fail_lines.append(f"  [{ts}] {tool}: {err}")

        summary = "\n".join(fail_lines)

        prompt = (
            "你是一个 AI 根因分析专家。分析以下任务执行过程中的失败记录，判断根本原因：\n\n"
            "1. **strategy_error**: agent 选错了实现策略（如在 Windows 上跑 Linux 命令、"
            "选择了不合适的工具链），执行链头部就能看出方向不对\n"
            "2. **llm_error**: 策略合理但 LLM 传了错误的工具参数或选了错误的具体命令\n"
            "3. **parameter_error**: 参数本身内容无效（路径不存在、URL 404、权限不足）\n"
            "4. **tool_bug**: 工具代码本身有 bug（KeyError, TypeError, 逻辑错误）\n"
            "5. **infra_error**: 基础设施问题（网络超时、服务不可用）\n\n"
            "判断方法：先看执行链头部（策略是否正确），再看尾部（具体执行中哪步出错）。"
            "不要只看最后一个错误。\n\n"
            f"任务目标: {task_context[:200]}\n"
            f"压缩执行链: {chain_summary[:300]}\n"
            f"失败详情:\n{summary}\n\n"
            "请以 JSON 格式回答，不要包含多余文字：\n"
            '{"recommendation": "tool_bug|llm_error|parameter_error|strategy_error", '
            '"reasoning": "用一句话解释分析结论（中文，说明是链的哪一环出了问题）"}'
        )

        try:
            response = await self._llm.chat([
                {"role": "system", "content": "你是一个严谨的 AI 根因分析专家。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ])
            raw = response.content.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            import json
            result = json.loads(raw)
            allowed = ("tool_bug", "llm_error", "parameter_error", "strategy_error")
            if result.get("recommendation") not in allowed:
                logger.debug("LLM analysis returned unexpected recommendation: %s", result.get("recommendation"))
                return None
            logger.info("LLM chain analysis for task %s: %s → %s",
                        task_context[:40], result["recommendation"], result.get("reasoning", "")[:100])
            return result
        except Exception as exc:
            logger.debug("LLM chain analysis failed: %s", exc)
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
                    "  3. 检查 logs 目录是否有大文件"
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
            + "\n\n请检查通道配置后重新连接。"
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
                    "  - 必要时重启进程释放内存"
                ).format(rss_mb, cpu_pct)
        except Exception as exc:
            logger.debug("Memory check failed: %s", exc)
        return None

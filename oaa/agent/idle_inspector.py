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

# Disk usage — weekly standalone check
_DISK_CHECK_COOLDOWN = 604800  # 7 days

# Max times the same proposal is delivered before suppression
_MAX_PROPOSAL_REPEATS = 3


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
        """Periodic inspection loop — lineA(30min) + lineB(task-triggered, 15min idle) + weekly disk."""
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

                # Weekly disk usage check (standalone, not part of lineC)
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
        """Legacy — kept for backward compatibility.  Disk check now runs
        directly in ``_background_loop``.  Returns None."""
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
                if self._proposal_store and self._should_skip_proposal(target, "sop_optimize"):
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
                        if self._proposal_store and self._should_skip_proposal(tool, "tool_fix"):
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

    async def _check_tool_failures(self, tool_filter: set[str] | None = None) -> str | None:
        """Check logged tool failures with root cause analysis.

        Failure categories and their handling:

        ==================== ========== ==============================================
        Category             Threshold  Action
        ==================== ========== ==============================================
        ``tool_bug``         ≥2         Create ``TYPE_TOOL_FIX`` proposal
        ``llm_error``        ≥3         Create correction entry (NOT tool-fix)
        ``parameter_error``  ≥3         Create correction entry (NOT tool-fix)
        ``unknown`` (+LLM)   ≥2         LLM analyzes execution chain, then acts
        ``unknown`` (no LLM) ≥2         Conservative fallback: treat as ``tool_bug``
        ``infra_error``      any        Skip (transient)
        ==================== ========== ==============================================
        """
        if not self._memory_mgr:
            return None

        try:
            failures = self._memory_mgr.load_tool_failures(50)
        except Exception:
            return None
        if not failures:
            return None

        # Skip stub / known-init errors
        _STUB_TOOLS = frozenset()
        _SKIP_ERRORS = ("未初始化", "not initialized", "未配置")
        valid_failures = [
            f for f in failures
            if f["tool"] not in _STUB_TOOLS
            and not any(s in f.get("error", "") for s in _SKIP_ERRORS)
        ]
        if not valid_failures:
            return None

        # Load successes for recovery check
        try:
            successes = self._memory_mgr.load_tool_successes(100)
        except Exception:
            successes = []

        # Tool → source file mapping (same as before)
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

        from collections import defaultdict
        by_tool: dict[str, list[dict]] = defaultdict(list)
        for f in valid_failures:
            by_tool[f["tool"]].append(f)

        for tool, tool_fails in by_tool.items():
            if len(tool_fails) < 2:
                continue
            if tool_filter is not None and tool not in tool_filter:
                continue
            if self.is_tool_ignored(tool):
                continue
            if self._proposal_store and self._should_skip_proposal(tool, "tool_fix"):
                continue

            # Check resolved by subsequent success
            tool_successes = [s for s in successes if s["tool"] == tool]
            if tool_fails and tool_successes:
                last_fail_ts = tool_fails[-1].get("timestamp", "")
                last_success_ts = tool_successes[-1].get("timestamp", "")
                if last_success_ts >= last_fail_ts:
                    continue

            # Analyze category distribution
            cats = Counter(f.get("category", "unknown") for f in tool_fails)
            total = len(tool_fails)
            latest = tool_fails[-1]
            error_snippet = latest.get("error", "")[:120]

            # ----------------------------------------------------------------
            # tool_bug (≥2) → create tool-fix proposal immediately
            # ----------------------------------------------------------------
            if cats.get("tool_bug", 0) >= 2:
                source_file = tool_to_file.get(tool, "oaa/agent/tools.py")
                problem_context = {
                    "type": "tool_failure",
                    "tool_name": tool,
                    "failure_count": total,
                    "last_error": error_snippet,
                    "error_history": [f.get("error", "")[:200] for f in tool_fails if f.get("error")][-3:],
                    "tool_source": source_file,
                    "categories": dict(cats),
                    "analysis": "代码异常 — 需检查源码修复",
                }
                if self._proposal_store:
                    from .proposal import Proposal, TYPE_TOOL_FIX
                    prop = Proposal(
                        type=TYPE_TOOL_FIX,
                        title=f"{tool} 累计失败 {total} 次需修复",
                        problem=f"工具 {tool} 累计失败 {total} 次（{cats.get('tool_bug', 0)} 次为代码异常）。最后错误: {error_snippet}",
                        benefit=f"修复后 {tool} 可正常使用",
                        target=tool,
                        actions=None,
                        problem_context=problem_context,
                    )
                    await self._proposal_store.add(prop)
                return (
                    "🔧 空闲诊断：发现工具 **{}** 存在代码异常（失败 {} 次）\n\n"
                    "  - 最后错误: {}\n"
                    "  - 类别: 代码异常 — agent 将自动分析源码并修复\n\n"
                    "在进化工厂页面点击「批准执行」或「忽略」"
                ).format(tool, total, error_snippet)

            # ----------------------------------------------------------------
            # llm_error + parameter_error (combined ≥3) → correction entry
            # ----------------------------------------------------------------
            llm_count = cats.get("llm_error", 0)
            param_count = cats.get("parameter_error", 0)
            if llm_count + param_count >= 3:
                if llm_count >= param_count:
                    # LLM chose wrong tool or wrong arguments
                    errors = [f.get("error", "")[:150] for f in tool_fails
                              if f.get("category") == "llm_error"]
                    contexts = [f.get("context", "") for f in tool_fails if f.get("context")]
                    top_err = Counter(e for e in errors if e).most_common(1)
                    err_detail = top_err[0][0] if top_err else error_snippet
                    ctx_detail = f"（任务: {contexts[-1]}）" if contexts else ""
                    lesson = (
                        f"工具 {tool} 的多次失败源于 LLM 选择了错误的参数或方法。"
                        f"常见错误: {err_detail}{ctx_detail}"
                    )
                else:
                    # Parameter content errors (bad path, URL, etc.)
                    errors = [f.get("error", "")[:150] for f in tool_fails
                              if f.get("category") == "parameter_error"]
                    top_err = Counter(e for e in errors if e).most_common(1)
                    err_detail = top_err[0][0] if top_err else error_snippet
                    lesson = (
                        f"工具 {tool} 的参数错误 — 检查输入的路径/URL/参数是否正确。"
                        f"常见错误: {err_detail}"
                    )

                logger.info("Tool %s: creating correction entry (%s), not tool-fix", tool,
                            "llm_error" if llm_count >= param_count else "parameter_error")
                if self._memory_mgr:
                    await self._memory_mgr.add_correction(
                        context=f"工具 {tool} 的 {total} 次失败归类为 {'LLM 选择不当' if llm_count >= param_count else '参数错误'}",
                        lesson=lesson,
                    )
                continue

            # ----------------------------------------------------------------
            # unknown (≥2) → LLM analysis if available, else conservative fallback
            # ----------------------------------------------------------------
            if cats.get("unknown", 0) >= 2:
                if self._llm:
                    analysis = await self._llm_analyze_failures(tool, tool_fails, cats)
                    if analysis:
                        rec = analysis.get("recommendation", "")
                        if rec == "tool_bug":
                            # LLM confirmed it's a tool bug — create fix proposal
                            source_file = tool_to_file.get(tool, "oaa/agent/tools.py")
                            problem_context = {
                                "type": "tool_failure",
                                "tool_name": tool,
                                "failure_count": total,
                                "last_error": error_snippet,
                                "error_history": [f.get("error", "")[:200] for f in tool_fails if f.get("error")][-3:],
                                "tool_source": source_file,
                                "categories": dict(cats),
                                "analysis": analysis.get("reasoning", "LLM 分析确认工具异常"),
                            }
                            if self._proposal_store:
                                from .proposal import Proposal, TYPE_TOOL_FIX
                                prop = Proposal(
                                    type=TYPE_TOOL_FIX,
                                    title=f"{tool} 累计失败 {total} 次需修复",
                                    problem=f"工具 {tool} 累计失败 {total} 次。LLM 分析: {analysis.get('reasoning', '')[:200]}",
                                    benefit=f"修复后 {tool} 可正常使用",
                                    target=tool,
                                    actions=None,
                                    problem_context=problem_context,
                                )
                                await self._proposal_store.add(prop)
                            return (
                                "🔧 空闲诊断：发现工具 **{}** 存在异常（失败 {} 次）\n\n"
                                "  - 最后错误: {}\n"
                                "  - LLM 分析: {}\n\n"
                                "在进化工厂页面点击「批准执行」或「忽略」"
                            ).format(tool, total, error_snippet, analysis.get("reasoning", "")[:200])
                        elif rec in ("llm_error", "parameter_error"):
                            # LLM says it's not a tool bug — create correction entry
                            await self._memory_mgr.add_correction(
                                context=f"工具 {tool} 的 {total} 次失败（LLM 分析）",
                                lesson=analysis.get("reasoning", f"LLM 分析认为属于 {rec}，而非工具异常"),
                            )
                            continue
                        # Unknown recommendation — skip
                        continue
                    # LLM analysis failed — skip (don't create false positives)
                    logger.debug("LLM analysis returned empty for %s failures, skipping", tool)
                    continue
                else:
                    # No LLM available — conservative fallback for backward compat
                    # Treat as potential tool bug (old records without category)
                    source_file = tool_to_file.get(tool, "oaa/agent/tools.py")
                    problem_context = {
                        "type": "tool_failure",
                        "tool_name": tool,
                        "failure_count": total,
                        "last_error": error_snippet,
                        "error_history": [f.get("error", "")[:200] for f in tool_fails if f.get("error")][-3:],
                        "tool_source": source_file,
                        "categories": dict(cats),
                        "analysis": "无法确定根因（无 LLM 分析）— 按潜在工具异常处理",
                    }
                    if self._proposal_store:
                        from .proposal import Proposal, TYPE_TOOL_FIX
                        prop = Proposal(
                            type=TYPE_TOOL_FIX,
                            title=f"{tool} 累计失败 {total} 次需检查",
                            problem=f"工具 {tool} 累计失败 {total} 次，无法自动分析根因。最后错误: {error_snippet}",
                            benefit=f"修复后 {tool} 可正常使用",
                            target=tool,
                            actions=None,
                            problem_context=problem_context,
                        )
                        await self._proposal_store.add(prop)
                    return (
                        "🔧 空闲诊断：工具 **{}** 失败 {} 次（无法自动分析根因）\n\n"
                        "  - 最后错误: {}\n"
                        "  - 已生成提案，请在进化工厂查看\n\n"
                        "在进化工厂页面点击「批准执行」或「忽略」"
                    ).format(tool, total, error_snippet)

            # infra_error: silently skip
            # Other low-count categories: skip

        return None

    async def _llm_analyze_failures(self, tool: str, tool_fails: list[dict],
                                     cats: Counter) -> dict | None:
        """Use LLM to analyze tool failure execution chains and determine root cause.

        Returns a dict with ``recommendation`` (``tool_bug`` / ``llm_error`` /
        ``parameter_error``) and ``reasoning`` (summary text).
        Returns ``None`` if analysis fails or LLM is unavailable.
        """
        if not self._llm:
            return None

        # Build a compact summary of the failures with their execution chains
        fail_lines = []
        for f in tool_fails[-5:]:  # last 5 failures
            ts = f.get("timestamp", "?")[:16]
            err = f.get("error", "")[:200]
            ctx = f.get("context", "")[:150]
            chain_raw = f.get("chain", "")
            chain_summary = ""
            if chain_raw:
                try:
                    import json
                    chain = json.loads(chain_raw)
                    chain_summary = " → ".join(
                        f"{c.get('tool', '?')}({c.get('status', '?')})"
                        for c in chain[-4:]
                    )
                except (json.JSONDecodeError, TypeError):
                    chain_summary = chain_raw[:200]
            fail_lines.append(
                f"  [{ts}] error: {err}\n"
                f"        context: {ctx}\n"
                f"        chain: {chain_summary}"
            )

        summary = "\n".join(fail_lines)

        prompt = (
            "你是一个 AI 根因分析专家。分析以下工具调用失败记录，判断根本原因属于哪一类：\n\n"
            "1. **tool_bug**: 工具代码本身有 bug（如 KeyError, TypeError, 逻辑错误）\n"
            "2. **llm_error**: LLM 选择了错误的工具、传了无效参数、或策略失误\n"
            "3. **parameter_error**: 参数格式正确但内容无效（路径不存在、URL 404、权限不足）\n"
            "4. **infra_error**: 基础设施问题（网络超时、服务不可用）\n\n"
            f"工具名称: {tool}\n"
            f"总失败次数: {len(tool_fails)}\n"
            f"当前分类统计: {dict(cats)}\n\n"
            f"失败记录（含执行链）：\n{summary}\n\n"
            "请以 JSON 格式回答，不要包含多余文字：\n"
            '{"recommendation": "tool_bug|llm_error|parameter_error", '
            '"reasoning": "用一句话解释分析结论（中文）"}'
        )

        try:
            response = await self._llm.chat([
                {"role": "system", "content": "你是一个严谨的 AI 根因分析专家。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ])
            raw = response.content.strip()
            # Extract JSON from potential markdown fences
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            import json
            result = json.loads(raw)
            if result.get("recommendation") not in ("tool_bug", "llm_error", "parameter_error"):
                logger.debug("LLM analysis returned unexpected recommendation: %s", result.get("recommendation"))
                return None
            logger.info("LLM root cause analysis for %s: %s → %s",
                        tool, result["recommendation"], result.get("reasoning", ""))
            return result
        except Exception as exc:
            logger.debug("LLM analysis failed for %s: %s", tool, exc)
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

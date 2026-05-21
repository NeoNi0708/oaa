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

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler
    from .memory_manager import MemoryManager
    from .proposal import ProposalStore

logger = get_logger("agent.idle_inspector")

# Minimum seconds between idle inspections
_INSPECTION_COOLDOWN = 600  # 10 minutes

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
    6. All proposals stored in ProposalStore; text returned for notifications

    Supports both on-demand (``inspect()``) and periodic (``start_background()``)
    modes. The background task runs on ``_INSPECTION_COOLDOWN`` interval and
    pushes proposals to a registered notification callback.
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
        # Background task support
        self._background_task: asyncio.Task | None = None
        self._notify_callback: Callable[[str], Coroutine] | None = None
        # Dedup tracking: proposal_hash → send_count
        self._proposal_tracker: dict[str, int] = {}
        self._dedup_path = ""  # set by set_memory_path or inferred from memory_mgr
        # Persistent ignore list (tool_name → "once" | "forever")
        self._ignore_list: dict[str, str] = {}
        self._ignore_path = ""
        self._load_ignore_list()

    def reset_cooldown(self):
        """Reset the cooldown timer so next check runs immediately."""
        self._last_check = 0.0

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

        # Phase 6: System health — disk usage
        proposal = self._check_disk_usage()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 7: Channel health
        proposal = self._check_channel_health()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        # Phase 8: Memory usage
        proposal = self._check_memory_usage()
        if proposal:
            self._store_proposal(proposal)
            return proposal

        return None

    def _store_proposal(self, proposal_text: str):
        """Store a proposal notification, respecting dedup limit."""
        if not self._memory_mgr:
            return
        import hashlib
        phash = hashlib.md5(proposal_text.encode()).hexdigest()
        if not self._dedup_path and self._memory_mgr:
            self.set_memory_path(str(self._memory_mgr._dir))
        self._load_dedup_tracker()

        count = self._proposal_tracker.get(phash, 0) + 1
        if count > _MAX_PROPOSAL_REPEATS:
            logger.debug("Proposal %s suppressed (sent %d times)", phash, count - 1)
            return

        self._proposal_tracker[phash] = count
        self._save_dedup_tracker()

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
            if self._proposal_store and self._proposal_store.has_pending_for_target(f"crystal:{skill_name}", "skill_crystallize"):
                continue

            actions = [
                {"tool": "self_improve", "args": {"path": "", "old_content": "", "new_content": "",
                 "description": f"为 {skill_name} 生成固化技能"},
                 "description": f"触发 {skill_name} 的自动结晶"}
            ]
            if self._proposal_store:
                from .proposal import Proposal, TYPE_SKILL_CRYSTALLIZE
                self._proposal_store.add(Proposal(
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
                    self._proposal_store.add(Proposal(
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
        _STUB_TOOLS = frozenset({
            "wechat_contacts", "wechat_history", "wechat_sessions", "wechat_search",
        })

        tool_counts = Counter(f["tool"] for f in failures if f["tool"] not in _STUB_TOOLS)

        for tool, count in tool_counts.most_common(3):
            if count < 2:
                continue
            # Skip ignored tools
            if self.is_tool_ignored(tool):
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
                "web_search": "oaa/agent/extended_tools.py",
                "web_scan": "oaa/agent/extended_tools.py",
                "plan_create": "oaa/agent/extended_tools.py",
                "plan_update": "oaa/agent/extended_tools.py",
                "plan_list": "oaa/agent/extended_tools.py",
            }
            source_file = tool_to_file.get(tool, "oaa/agent/tools.py")
            mod_for_reload = source_file.replace("/", ".").replace(".py", "")

            actions = [
                {"tool": "read_own_source", "args": {"path": source_file},
                 "description": f"查看 {tool} 的实现代码"},
                {"tool": "self_improve", "args": {"path": source_file, "old_content": "", "new_content": "",
                 "description": f"修复 {tool} 的 {error_snippet}"},
                 "description": f"用 self_improve 修复 {tool}"},
                {"tool": "reload_module", "args": {"module": mod_for_reload},
                 "description": "重载模块使修复生效",
                 "verify": {"tool": "code_exec", "args": {"code": f"import {mod_for_reload.split('.')[-1]}; print('reload ok')"},
                            "description": "验证模块重载成功"}},
            ]

            # Create structured proposal
            if self._proposal_store:
                from .proposal import Proposal, TYPE_TOOL_FIX
                prop = Proposal(
                    type=TYPE_TOOL_FIX,
                    title=f"{tool} 累计失败 {count} 次需修复",
                    problem=f"工具 {tool} 累计失败 {count} 次。最后错误: {error_snippet}",
                    benefit=f"修复后 {tool} 可正常使用",
                    target=tool,
                    actions=actions,
                )
                self._proposal_store.add(prop)
                prop_id = prop.id
            else:
                prop_id = ""

            # Return notification text
            lines = [
                f"**{tool}** 最近失败 {count} 次",
                f"  - 最后错误: {error_snippet}",
                f"  - 修复步骤:",
                f"    1. ``read_own_source path={source_file}`` 查看工具实现",
                f"    2. 分析错误原因，用 ``self_improve`` 修复",
                f"    3. 清除 ``__pycache__`` 目录",
                f"    4. ``reload_module module={mod_for_reload}``",
            ]
            if prop_id:
                lines.append(f"  - 提案ID: {prop_id}（可用 proposal_approve 执行）")

            return (
                "🔧 空闲诊断：发现以下工具存在反复失败：\n\n"
                + "\n\n".join(lines)
                + "\n\n是否检查并自动修复？请确认。"
            )

        return None

    def _check_correction_patterns(self) -> str | None:
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
                self._proposal_store.add(Proposal(
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
                + "\n\n是否更新提示词以记住这些规则？请确认。"
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
                    "是否执行磁盘清理？请确认。"
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
            + "\n\n是否检查并修复？请确认。"
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
                    "是否执行内存检查？请确认。"
                ).format(rss_mb, cpu_pct)
        except Exception as exc:
            logger.debug("Memory check failed: %s", exc)
        return None

"""Repair loop — feed+verify+retry wrapper for self-healing.

The repair loop takes a problem context, feeds it to the agent via its
existing process_message loop, then independently verifies the result.
On failure, it retries up to ``max_retries`` times with failure history
injected.  All failures trigger automatic rollback via the rollback manifest.
"""
import asyncio
import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger

logger = get_logger("agent.repair_loop")

# ContextVar for threading the active proposal ID through the tool layer
# into AtomicTools._record_rollback_entry().  Set by RepairLoop.run()
# and read by record_rollback_entry().
_active_proposal_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_proposal_id", default=""
)


def get_active_proposal_id() -> str:
    """Return the proposal ID for the currently executing repair loop.

    Returns the empty string when no repair loop is active (normal operation).
    """
    return _active_proposal_id.get()


VerifyFn = Any  # Callable[[dict], Awaitable[tuple[bool, str]]]


@dataclass
class RepairPlan:
    """Tracks a self-healing repair attempt across retries."""

    proposal_id: str
    problem_context: dict
    attempt: int = 1
    max_retries: int = 3
    failure_history: list = field(default_factory=list)
    rollback_manifest: dict = field(default_factory=dict)


class RepairLoop:
    """Self-healing execution loop — feed → execute → verify → retry/rollback.

    Usage::

        loop = RepairLoop(data_dir)
        loop.register_verifier("tool_failure", my_verifier_fn)
        plan = RepairPlan(proposal_id="prop_xxx", problem_context={...})
        result = await loop.run(plan, agent)
    """

    def __init__(self, data_dir: str, feed_timeout: float = 300.0):
        self.data_dir = data_dir
        self._feed_timeout = feed_timeout
        self._verifiers: dict[str, VerifyFn] = {}
        self._manifest_path = os.path.join(data_dir, "rollback_manifest.json")

    def register_verifier(self, problem_type: str, fn: VerifyFn):
        """Register an independent verifier for *problem_type*.

        The verifier receives the original ``problem_context`` and must
        return ``(passed: bool, message: str)``.
        """
        self._verifiers[problem_type] = fn

    async def run(self, plan: RepairPlan, agent, inspector=None) -> dict:
        """Execute the self-healing flow.  Returns ``{"status": ..., ...}``.

        When *inspector* (IdleInspector) is provided, inspection is paused
        for the duration of the repair to prevent nested healing loops.
        """
        token = _active_proposal_id.set(plan.proposal_id)
        if inspector:
            inspector.pause()
        try:
            return await self._run_impl(plan, agent)
        finally:
            _active_proposal_id.reset(token)
            if inspector:
                inspector.resume()

    async def _run_impl(self, plan: RepairPlan, agent) -> dict:
        """Internal implementation — called by ``run()`` with contextvar set."""
        while plan.attempt <= plan.max_retries:
            # Build context for this attempt (includes failure history on retry)
            prompt = self._build_feed_prompt(plan)

            # Feed → agent fixes the problem using its own capabilities
            await self._feed(agent, prompt)

            # Independent verification
            passed, message = await self._verify(plan)
            if passed:
                self._set_manifest_status(plan.proposal_id, "done")
                return {
                    "status": "done",
                    "message": message,
                    "attempts": plan.attempt,
                }

            # Analyse failure and prepare for retry
            failure_type = self._classify_failure(message)
            plan.failure_history.append({
                "attempt": plan.attempt,
                "failure_type": failure_type,
                "detail": message,
            })
            logger.info(
                "Repair attempt %d/%d failed (%s): %.80s",
                plan.attempt, plan.max_retries, failure_type, message,
            )
            plan.attempt += 1

        # All retries exhausted → rollback
        await self._rollback(plan)
        return {
            "status": "failed",
            "message": f"{plan.max_retries} 次重试全部失败，已回滚所有变更",
            "attempts": plan.attempt - 1,
            "failure_history": plan.failure_history,
        }

    # ------------------------------------------------------------------
    # Feed prompt construction
    # ------------------------------------------------------------------

    def _build_feed_prompt(self, plan: RepairPlan) -> str:
        """Build the self-healing prompt fed to the agent."""
        ctx = plan.problem_context
        ptype = ctx.get("type", "unknown")

        parts = [
            "【自愈任务】{}".format(ptype),
            "━" * 50,
            "agent 检测到以下问题需要处理：\n",
        ]

        if ptype == "tool_failure":
            parts.extend([
                "工具 **{}** 累计失败 **{}** 次".format(
                    ctx.get("tool_name", "?"), ctx.get("failure_count", 0),
                ),
                "最后错误：{}".format(ctx.get("last_error", "")),
            ])
        else:
            parts.append(json.dumps(ctx, ensure_ascii=False, indent=2))

        # Retry context — inject failure-type-specific guidance
        if plan.failure_history:
            last = plan.failure_history[-1]
            ftype = last.get("failure_type", "")
            parts.extend([
                "",
                "⚠️ 上次修复尝试失败（第 {} 次）：".format(last["attempt"]),
                "  失败原因：{}".format(last.get("detail", "")[:200]),
                "  失败类型：{}".format(ftype),
            ])

            if ftype == "dependency_missing":
                parts.extend([
                    "",
                    "上次你尝试安装依赖但未成功。**在重试之前，先搜索正确的方案：**",
                    "  1. 用 `ai_search` 搜索正确的包名/工具名（你上次可能装了错误的包）",
                    "  2. 确认平台兼容性（Windows 用 pip/Chocolatey，Mac 用 brew，Linux 用 apt）",
                    "  3. 用 `shell_run` 试运行验证后才算成功",
                    "可选方案：pip / npm / winget / GitHub Release 下载",
                ])
            elif ftype == "method_error":
                parts.append("上次的修复方案本身有问题，请换一种方法重试。")
            else:
                parts.append("请换一种方案重试，不要重复已经失败的做法。")

        parts.extend([
            "",
            "请完成以下步骤：",
            "1. 诊断根因 — 分析错误信息，判断是缺依赖、调用方式错误、权限不够还是服务不可达",
            "2. **搜索方案** — 当问题是「缺少工具/依赖/能力」时，必须先搜索再安装：",
            "   a. `ai_search` 在网上搜索（对比多个候选，选最合适的，不能抓到第一个就用）",
            "   b. `code_search` 在代码库搜索（有没有已存在的替代方案）",
            "   c. `skill_search` 在技能市场搜索（有没有可复用的技能）",
            "   d. `module_index` 查看已有工具（也许换个工具就能解决）",
            "3. 选择并安装 — 确保安装的平台兼容性（Windows/Mac/Linux），选错包名是常见错误",
            "4. **强制试运行** — 安装后用 `shell_run` 执行 `<工具名> --help` 或最小功能测试",
            "   ⚠️ 试运行失败必须回到步骤 2 重新搜索，禁止未验证就宣称「已安装/已修复」",
            "5. 验证修复 — 重新检查原始问题是否解决",
            "6. 输出结果 — 总结：搜索了什么、为什么选这个、试运行结果、问题是否解决",
            "",
            "约束：",
            "- 做修复时，不要只盯着故障工具本身，先看有没有其他方式可以解决问题",
            "- 能做+已有工具=优先，已有工具不够=先搜再装，自己能力内=必须自己做完",
            "- 搜索时对比多个候选方案，不要用第一个结果",
            "- 安装必须在试运行通过后才算完成，纸上谈兵不算修复",
            "- 试过所有自己能用的方法还不行，才需要请求用户协助",
            "- 请求用户协助时，必须说清楚：已尝试了什么、卡在哪、需要用户做什么",
            "- 每次修改会自动记录备份，无需担心改坏",
        ])
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Feed execution
    # ------------------------------------------------------------------

    async def _feed(self, agent, prompt: str) -> str:
        """Feed *prompt* into the agent and collect the full response."""
        parts: list[str] = []
        try:
            async def _collect():
                async for chunk in agent.process_message(prompt, history=[]):
                    if chunk["type"] == "llm_output":
                        parts.append(chunk["content"])
                    elif chunk["type"] == "done":
                        parts.append(chunk.get("content", ""))

            await asyncio.wait_for(_collect(), timeout=self._feed_timeout)
        except asyncio.TimeoutError:
            logger.error("Repair feed timed out after %ds", self._feed_timeout)
            parts.append("\n\n[修复超时 — 已中断]")
        except Exception as exc:
            logger.error("Repair feed failed: %s", exc)
            parts.append(f"\n\n[修复执行异常: {exc}]")
        return "".join(parts).strip()

    # ------------------------------------------------------------------
    # Independent verification
    # ------------------------------------------------------------------

    async def _verify(self, plan: RepairPlan) -> tuple[bool, str]:
        """Run the registered verifier for this problem type."""
        ctx = plan.problem_context
        ptype = ctx.get("type", "tool_failure")
        verifier = self._verifiers.get(ptype)
        if verifier is None:
            logger.error("No verifier registered for '%s' — verification failed", ptype)
            return False, "未注册验证器 — 无法确认修复结果"
        try:
            return await verifier(ctx)
        except Exception as exc:
            logger.error("Verifier for '%s' raised: %s", ptype, exc)
            return False, f"验证过程异常: {exc}"

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def _rollback(self, plan: RepairPlan):
        """Roll back every change recorded in the manifest."""
        manifest = plan.rollback_manifest or {}
        changes = manifest.get("changes", [])
        if not changes:
            logger.warning("No rollback manifest entries for %s", plan.proposal_id)
        else:
            # Restore file backups in reverse chronological order
            for change in reversed(changes):
                if change.get("type") == "file_edit" and change.get("backup"):
                    self._restore_backup(change["path"], change["backup"])

        self._set_manifest_status(plan.proposal_id, "rolled_back")
        logger.info("Rolled back proposal %s (%d changes)", plan.proposal_id, len(changes))

    @staticmethod
    def _restore_backup(path: str, backup_path: str):
        """Restore a single file from its backup."""
        if not os.path.exists(backup_path):
            logger.warning("Backup not found: %s", backup_path)
            return
        import shutil
        try:
            shutil.copy2(backup_path, path)
            logger.info("Restored %s from backup", path)
        except Exception as exc:
            logger.warning("Failed to restore %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Failure classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_failure(message: str) -> str:
        """Categorise a failure to guide the next retry strategy."""
        low = message.lower()
        if any(k in low for k in ("not found", "not installed", "no module",
                                  "no such", "command not found", "cannot find")):
            return "dependency_missing"
        if any(k in low for k in ("typeerror", "attributeerror", "nameerror",
                                  "syntaxerror", "valueerror")):
            return "method_error"
        if any(k in low for k in ("permission", "denied", "access")):
            return "permission"
        return "other"

    # ------------------------------------------------------------------
    # Manifest persistence
    # ------------------------------------------------------------------

    def _set_manifest_status(self, proposal_id: str, status: str):
        """Update the status of a proposal in the rollback manifest."""
        manifest = self._load_manifest()
        if proposal_id in manifest:
            manifest[proposal_id]["status"] = status
            self._save_manifest(manifest)

    def _load_manifest(self) -> dict:
        if not os.path.exists(self._manifest_path):
            return {}
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_manifest(self, manifest: dict):
        try:
            json_string = json.dumps(manifest, ensure_ascii=False, indent=2)
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                f.write(json_string)
        except OSError as exc:
            logger.warning("Failed to save rollback manifest: %s", exc)


def record_rollback_entry(data_dir: str, proposal_id: str, change: dict):
    """Record a single change in the rollback manifest (thread-safe write).

    Called by :meth:`AtomicTools._record_rollback_entry` after a
    self-modifying operation (self_improve, file_write, file_patch).

    When *proposal_id* is ``"_tool_level"`` (legacy sentinel), the active
    proposal ID from the :data:`_active_proposal_id` contextvar is used
    instead, so that changes are correctly attributed to the running repair
    loop.
    """
    effective_id = proposal_id
    if effective_id == "_tool_level" or not effective_id:
        ctx_id = _active_proposal_id.get()
        if ctx_id:
            effective_id = ctx_id

    manifest_path = os.path.join(data_dir, "rollback_manifest.json")
    manifest: dict = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError):
            manifest = {}

    if effective_id not in manifest:
        manifest[effective_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "attempts": [],
        }

    manifest[effective_id].setdefault("attempts", [])
    # Wrap the change in an attempt envelope
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **change,
    }
    manifest[effective_id]["attempts"].append(entry)

    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("Failed to write rollback manifest: %s", exc)

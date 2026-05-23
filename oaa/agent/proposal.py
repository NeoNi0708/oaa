"""Structured proposal system — replaces free-text pending_proposals.md.

Components:
- ``Proposal`` dataclass: id, type, problem, benefit, target, actions, status, ...
- ``ProposalStore``: JSON persistence with CRUD + dedup by target
- ``ProposalExecutor``: runs a proposal's action sequence via a handler
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Any

from ..async_io import async_write_json
from ..logging_config import get_logger

if TYPE_CHECKING:
    from .handler import BaseHandler

logger = get_logger("agent.proposal")

# ---------------------------------------------------------------------------
# Proposal statuses
# ---------------------------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_IGNORED_ONCE = "ignored_once"
STATUS_IGNORED_FOREVER = "ignored_forever"

# Proposal types
TYPE_TOOL_FIX = "tool_fix"
TYPE_INSTALL_DEP = "install_dep"
TYPE_SOP_OPTIMIZE = "sop_optimize"
TYPE_SKILL_CRYSTALLIZE = "skill_crystallize"
TYPE_CONFIG_CHANGE = "config_change"

# Sequence counter for unique IDs
_counter: int = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"prop_{int(time.time())}_{_counter}"


@dataclass
class Proposal:
    """A structured self-improvement proposal with executable actions.

    Either ``actions`` (traditional fixed-step proposals) or
    ``problem_context`` (self-healing feed+verify proposals) is set.
    """
    id: str = ""
    type: str = ""
    title: str = ""
    problem: str = ""
    benefit: str = ""
    target: str = ""
    actions: list[dict] | None = field(default_factory=list)
    problem_context: dict | None = None
    status: str = STATUS_PENDING
    created_at: float = 0.0
    executed_at: float | None = None
    result: str | None = None
    error: str | None = None

    def __post_init__(self):
        if self.actions and self.problem_context:
            raise ValueError(
                "Proposal cannot have both actions and problem_context set"
            )


class ProposalStore:
    """JSON-backed proposal store with dedup by target + type.

    Proposals are persisted to ``proposals.json`` under *store_dir*.
    """

    def __init__(self, store_dir: str):
        self._path = os.path.join(store_dir, "proposals.json")
        self._store: list[dict] = []
        self._lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._store = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load proposals: %s", exc)
                self._store = []

    async def _save(self):
        try:
            await async_write_json(self._path, self._store, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Failed to save proposals: %s", exc)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add(self, proposal: Proposal) -> str:
        """Add a new proposal. Returns its ID."""
        if not proposal.id:
            proposal.id = _next_id()
        if not proposal.created_at:
            proposal.created_at = time.time()
        d = asdict(proposal)
        async with self._lock:
            self._store.append(d)
            await self._save()
        logger.info("Proposal created: %s [%s] %s", proposal.id, proposal.type, proposal.title)
        return proposal.id

    def get(self, proposal_id: str) -> dict | None:
        for p in self._store:
            if p["id"] == proposal_id:
                return p
        return None

    async def update_status(self, proposal_id: str, status: str, **extra) -> bool:
        """Update proposal status and optional extra fields. Returns True if found."""
        async with self._lock:
            for p in self._store:
                if p["id"] == proposal_id:
                    p["status"] = status
                    p.update(extra)
                    await self._save()
                    return True
        return False

    def has_pending_for_target(self, target: str, ptype: str = "") -> bool:
        """Check if there's already a pending proposal for *target*."""
        for p in self._store:
            if p["target"] == target and p["status"] == STATUS_PENDING:
                if not ptype or p["type"] == ptype:
                    return True
        return False

    def has_recent_for_target(self, target: str, ptype: str = "",
                              window_hours: int = 24) -> bool:
        """Check if *target* had a proposal resolved within *window_hours*.

        Prevents the idle inspector from re-creating the same proposal
        immediately after it was completed or marked as failed.
        """
        cutoff = time.time() - (window_hours * 3600)
        for p in self._store:
            if p.get("target") != target:
                continue
            if ptype and p.get("type") != ptype:
                continue
            if p["status"] in (STATUS_DONE, STATUS_FAILED, "interrupted"):
                executed = p.get("executed_at", 0)
                updated = p.get("updated_at", 0)
                latest = max(executed if isinstance(executed, (int, float)) else 0,
                            updated if isinstance(updated, (int, float)) else 0,
                            p.get("created_at", 0))
                if latest > cutoff:
                    return True
        return False

    async def dedup_stale_pending(self):
        """Mark stale pending proposals as expired if there's a newer resolved
        proposal for the same target. Called at startup and periodically."""
        expired = 0
        now = time.time()
        for p in self._store:
            if p["status"] != STATUS_PENDING:
                continue
            # Check if there's a recently resolved proposal for same target
            target = p.get("target", "")
            if target and self.has_recent_for_target(target, p.get("type", "")):
                p["status"] = "expired"
                p["error"] = "已有更新的解决记录，自动失效"
                expired += 1
            # Also expire proposals older than 7 days
            elif (now - p.get("created_at", 0)) > 7 * 86400:
                p["status"] = "expired"
                p["error"] = "超过 7 天未处理，自动失效"
                expired += 1
        if expired:
            await self._save()
            logger.info("ProposalStore: expired %d stale pending proposals", expired)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_pending(self) -> list[dict]:
        return [p for p in self._store if p["status"] == STATUS_PENDING]

    def list_by_status(self, *statuses: str) -> list[dict]:
        return [p for p in self._store if p.get("status") in statuses]

    def count_pending(self) -> int:
        return sum(1 for p in self._store if p["status"] == STATUS_PENDING)

    async def fix_stale_running(self):
        """Mark all proposals left in 'running' state as 'interrupted'.

        Called at startup because a previous process may have been
        terminated while a proposal was executing.
        """
        async with self._lock:
            fixed = 0
            for p in self._store:
                if p.get("status") == "running":
                    p["status"] = "interrupted"
                    p["error"] = "应用退出，执行中断"
                    fixed += 1
            if fixed:
                await self._save()
                logger.info("ProposalStore: marked %d stale running proposals as interrupted", fixed)

    def has_pending(self) -> bool:
        return any(p["status"] == STATUS_PENDING for p in self._store)

    def all_proposals(self) -> list[dict]:
        return list(self._store)

    def get_pending_proposal_text(self) -> str:
        """Build a human-readable summary of pending proposals for system prompt injection."""
        pending = self.list_pending()
        if not pending:
            return ""
        lines = ["# ⏳ 待处理自愈提案", ""]
        for p in pending:
            lines.append(f"### {p['title']}")
            lines.append(f"- **ID**: {p['id']}")
            lines.append(f"- **类型**: {p['type']}")
            if p.get("problem"):
                lines.append(f"- **问题**: {p['problem']}")
            if p.get("benefit"):
                lines.append(f"- **修复后**: {p['benefit']}")
            actions = p.get("actions") or []
            if actions:
                lines.append(f"- **操作步骤**:")
                for action in actions:
                    lines.append(f"  1. `{action['tool']}` — {action.get('description', '')}")
            elif p.get("problem_context"):
                lines.append(f"- **修复方案**: agent 将自动分析根因并选择修复方案")
            lines.append("")
        lines.append("## 用户确认路由")
        lines.append("")
        lines.append("当**用户消息**包含「确认」「好」「执行」「yes」「可以」「是」等确认意图时：")
        lines.append("")
        for p in pending:
            lines.append(f"1. 调用 `proposal_approve(\"{p['id']}\")` 执行提案「{p['title']}」")
        lines.append("")
        lines.append("当用户说「忽略」「不用」「no」等否定意图时：")
        for p in pending:
            lines.append(f"1. 调用 `proposal_ignore(\"{p['id']}\")` 忽略本次提案")
        lines.append("")
        lines.append("执行完毕后用 1-2 句话向用户报告结果。")
        return "\n".join(lines)


class ProposalExecutor:
    """Executes a proposal's action sequence using a tool handler.

    Each action in the proposal must be ``{"tool": str, "args": dict, ...}``.
    Optionally an action may include:

    * ``verify`` — a dict ``{"tool": str, "args": dict, ...}`` that is run
      *after* the main action to confirm it succeeded.
    * ``rollback`` — a dict ``{"tool": str, "args": dict, ...}`` that is run
      when **verify** fails, to undo the action.

    The executor dispatches each action to ``handler.dispatch(tool, args)``
    and records success/failure/verification/rollback per step.
    """

    async def execute(self, proposal: dict, handler: "BaseHandler") -> dict:
        """Run all actions in *proposal* sequentially. Returns updated proposal dict.

        If an action defines ``verify``, the executor runs it after the action.
        If verification fails and ``rollback`` is defined, rollback is executed.
        """
        proposal["status"] = STATUS_RUNNING
        results = []

        for i, action in enumerate(proposal.get("actions") or []):
            tool = action.get("tool", "")
            args = action.get("args", {})
            desc = action.get("description", tool)
            verify_action = action.get("verify")
            rollback_action = action.get("rollback")

            logger.info("Proposal %s step %d: %s", proposal.get("id"), i + 1, desc)

            step = {
                "step": i + 1,
                "tool": tool,
                "status": "success",
            }

            try:
                result = await handler.dispatch(tool, args)
                step["result"] = _summarize(result, 500)

                # --- Verification ---
                if verify_action:
                    v_tool = verify_action.get("tool", "")
                    v_args = verify_action.get("args", {})
                    try:
                        v_result = await handler.dispatch(v_tool, v_args)
                        step["verified"] = True
                        step["verify_result"] = _summarize(v_result, 200)
                    except Exception as v_exc:
                        step["verified"] = False
                        step["verify_error"] = str(v_exc)
                        logger.warning(
                            "Proposal %s step %d verify failed: %s",
                            proposal.get("id"), i + 1, v_exc,
                        )

                        # --- Rollback ---
                        if rollback_action:
                            r_tool = rollback_action.get("tool", "")
                            r_args = rollback_action.get("args", {})
                            try:
                                r_result = await handler.dispatch(r_tool, r_args)
                                step["rollback"] = "success"
                            except Exception as r_exc:
                                step["rollback"] = f"failed: {r_exc}"
                                logger.warning(
                                    "Proposal %s step %d rollback also failed: %s",
                                    proposal.get("id"), i + 1, r_exc,
                                )

                        results.append(step)
                        proposal["status"] = STATUS_FAILED
                        proposal["error"] = (
                            f"Step {i + 1} ({tool}) 验证失败: {v_exc}"
                        )
                        proposal["result"] = json.dumps(
                            results, ensure_ascii=False, default=str,
                        )
                        return proposal

                results.append(step)

            except Exception as exc:
                logger.warning(
                    "Proposal %s step %d failed: %s",
                    proposal.get("id"), i + 1, exc,
                )
                step["status"] = "error"
                step["error"] = str(exc)
                results.append(step)
                proposal["status"] = STATUS_FAILED
                proposal["error"] = f"Step {i + 1} ({tool}) failed: {exc}"
                proposal["result"] = json.dumps(
                    results, ensure_ascii=False, default=str,
                )
                return proposal

        proposal["status"] = STATUS_DONE
        proposal["result"] = json.dumps(results, ensure_ascii=False, default=str)
        proposal["executed_at"] = time.time()
        logger.info("Proposal %s completed successfully", proposal.get("id"))
        return proposal


def _summarize(obj: Any, max_len: int = 500) -> str:
    """Convert tool result to a short string for logging."""
    if isinstance(obj, dict):
        text = obj.get("msg", obj.get("content", json.dumps(obj, ensure_ascii=False, default=str)))
    elif isinstance(obj, str):
        text = obj
    else:
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text

"""Cross-day planner — plan_create, plan_update, plan_list."""
import json
import os
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger

logger = get_logger("agent.planner")


class Planner:
    """Lightweight plan manager. Plans stored as JSON files in workspace/plans/.

    Enforces DAG dependencies: a step can only enter ``in_progress`` when all its
    ``blocked_by`` predecessors are ``done``. Detects cycles on creation.
    """

    def __init__(self, plans_dir: str):
        self.plans_dir = plans_dir
        Path(plans_dir).mkdir(parents=True, exist_ok=True)

    def _plan_path(self, plan_id: str) -> str:
        return os.path.join(self.plans_dir, f"{plan_id}.json")

    def _load_plan(self, plan_id: str) -> Optional[dict]:
        """Load a plan from disk, or None if not found."""
        path = self._plan_path(plan_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _save_plan(self, plan: dict):
        """Save a plan to disk."""
        with open(self._plan_path(plan["id"]), "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

    def _has_cycle(self, steps: list[dict]) -> bool:
        """Detect cycles in the step DAG using Kahn's algorithm."""
        step_ids = {s["id"] for s in steps}
        in_degree: dict[int, int] = {sid: 0 for sid in step_ids}
        adj: dict[int, list[int]] = {sid: [] for sid in step_ids}
        for s in steps:
            for dep in s.get("blocked_by", []):
                if dep in step_ids:
                    adj.setdefault(dep, []).append(s["id"])
                    in_degree[s["id"]] = in_degree.get(s["id"], 0) + 1
        queue = deque([n for n, d in in_degree.items() if d == 0])
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        return visited != len(step_ids)

    def _check_blocked_by(self, step: dict, steps: list[dict]) -> Optional[str]:
        """Return error message if step has unsatisfied blockers, else None."""
        for dep_id in step.get("blocked_by", []):
            for s in steps:
                if s["id"] == dep_id:
                    if s["status"] != "done":
                        blocker = next(
                            (x["task"] for x in steps if x["id"] == dep_id),
                            str(dep_id),
                        )
                        return f"Step '{step['id']}' blocked by step {dep_id} ('{blocker}') — not yet done"
                    break
        return None

    def _auto_advance(self, steps: list[dict]) -> list[str]:
        """After a step completes, mark unblocked steps as ``pending``.

        Returns a list of step IDs that were auto-advanced.
        """
        advanced = []
        for s in steps:
            if s["status"] == "in_progress":
                blocked = self._check_blocked_by(s, steps)
                if not blocked:
                    # Still in_progress but all blockers resolved — keep as in_progress
                    pass
            elif s["status"] == "pending":
                blocked = self._check_blocked_by(s, steps)
                if not blocked:
                    s["status"] = "ready"
                    advanced.append(str(s["id"]))
        return advanced

    def create(self, goal: str, steps: list[dict]) -> dict:
        """Create a new plan. steps: [{"id": 1, "task": "...", "status": "pending"}]

        Raises ``ValueError`` if the step DAG contains a cycle.
        """
        if self._has_cycle(steps):
            raise ValueError("Plan contains a cycle in step dependencies")
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        plan = {
            "id": plan_id,
            "goal": goal,
            "created": now,
            "updated": now,
            "status": "in_progress",
            "steps": steps,
        }
        # Auto-advance initial steps that have no blockers
        self._auto_advance(steps)
        self._save_plan(plan)
        return plan

    def update(self, plan_id: str, step_id: int, status: str, result: str = "") -> Optional[dict]:
        """Update a step's status in a plan. Returns updated plan or None if not found.

        Enforces ``blocked_by`` dependencies: a step cannot enter ``in_progress``
        or ``done`` until all its blockers are ``done``.
        """
        plan = self._load_plan(plan_id)
        if plan is None:
            return None
        for step in plan["steps"]:
            if step["id"] == step_id:
                if status in ("in_progress", "done"):
                    blocked = self._check_blocked_by(step, plan["steps"])
                    if blocked:
                        plan["error"] = blocked
                        return plan
                step["status"] = status
                if result:
                    step["result"] = result
                break
        plan["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Auto-advance unblocked steps
        advanced = self._auto_advance(plan["steps"])
        if advanced:
            logger.info("Auto-advanced steps after %s: %s", step_id, ", ".join(advanced))

        if all(s["status"] in ("done", "failed") for s in plan["steps"]):
            plan["status"] = "completed"
        self._save_plan(plan)
        return plan

    def list_plans(self, status: str = "") -> list[dict]:
        """List plans sorted by updated_at desc, optional status filter."""
        plans = []
        if not os.path.isdir(self.plans_dir):
            return plans
        for f_name in sorted(os.listdir(self.plans_dir), reverse=True):
            if not f_name.endswith(".json"):
                continue
            plan = self._load_plan(f_name.replace(".json", ""))
            if plan is None:
                continue
            if status and plan.get("status") != status:
                continue
            plans.append(plan)
        plans.sort(key=lambda p: p.get("updated", ""), reverse=True)
        return plans

    def get(self, plan_id: str) -> Optional[dict]:
        """Get a single plan by ID."""
        return self._load_plan(plan_id)

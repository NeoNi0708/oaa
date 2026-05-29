"""Planner mixin — plan creation, update, and listing tools."""
from ..tool_decorator import agent_tool


class PlannerMixin:
    """Plan management tools (delegates to self.planner)."""

    async def do_plan_create(self, args: dict) -> dict:
        """Create a new plan via Planner."""
        goal = args.get("goal", "")
        steps = args.get("steps", [])
        try:
            plan = self.planner.create(goal, steps)
        except ValueError as exc:
            return {"status": "error", "msg": str(exc)}
        return {"status": "success", "plan": plan}

    async def do_plan_update(self, args: dict) -> dict:
        """Update a plan step via Planner."""
        plan_id = args.get("plan_id", "")
        step_id = args.get("step_id", 0)
        status = args.get("status", "done")
        result = args.get("result", "")
        plan = self.planner.update(plan_id, step_id, status, result)
        if plan is None:
            return {"status": "error", "msg": f"Plan {plan_id} not found"}
        return {"status": "success", "plan": plan}

    async def do_plan_list(self, args: dict) -> dict:
        """List plans via Planner."""
        status = args.get("status", "")
        plans = self.planner.list_plans(status)
        return {"status": "success", "plans": plans, "count": len(plans)}

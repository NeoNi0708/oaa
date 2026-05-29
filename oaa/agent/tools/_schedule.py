"""Schedule and proposal mixin — schedule CRUD + proposal management."""

import asyncio

from ...logging_config import get_logger
from ..tool_decorator import agent_tool

logger = get_logger("agent.tools.schedule")


class ScheduleMixin:
    """Mixin for schedule and proposal tools."""

    # ------------------------------------------------------------------
    # Proposal management
    # ------------------------------------------------------------------

    @agent_tool(
        name="proposal_list",
        description="List pending self-improvement proposals. Each proposal has an ID, type (tool_fix/install_dep/sop_optimize/skill_crystallize), description, and executable actions. Call this to see what improvements are waiting for approval."
    )
    async def do_proposal_list(self, include_history: bool = False) -> dict:
        if not self._proposal_store:
            return {"status": "error", "msg": "提案系统未初始化"}
        proposals = self._proposal_store.all_proposals() if include_history else self._proposal_store.list_pending()
        if not proposals:
            return {"status": "success", "proposals": [], "msg": "暂无待处理提案"}
        return {"status": "success", "proposals": proposals, "count": len(proposals)}

    @agent_tool(
        name="proposal_approve",
        description="Approve and execute a self-improvement proposal by ID. Executes the proposal's action sequence (read_own_source → self_improve → reload_module, or shell_run install, etc.) and reports the result of each step. Example: proposal_approve(id='prop_1234567890_1')"
    )
    async def do_proposal_approve(self, id: str) -> dict:
        if not self._proposal_store:
            return {"status": "error", "msg": "提案系统未初始化"}
        proposal = self._proposal_store.get(id)
        if not proposal:
            return {"status": "error", "msg": f"未找到提案: {id}"}
        if proposal["status"] != "pending":
            return {"status": "error", "msg": f"提案 {id} 状态为 {proposal['status']}，不能执行"}
        from ..proposal import ProposalExecutor
        executor = ProposalExecutor()
        result = await executor.execute(proposal, self)
        await self._proposal_store.update_status(
            result["id"], result["status"],
            executed_at=result.get("executed_at"),
            result=result.get("result"),
            error=result.get("error"),
        )
        return {
            "status": "success" if result["status"] == "done" else "error",
            "proposal_id": id,
            "proposal_status": result["status"],
            "result": result.get("result", ""),
            "error": result.get("error", ""),
        }

    @agent_tool(
        name="proposal_ignore",
        description="Ignore a tool or pattern in future idle inspections. Use permanent=True to skip forever (e.g. a stub tool that will never work), or permanent=False to skip just the next inspection cycle. Example: proposal_ignore(target='wechat_contacts', permanent=True)"
    )
    async def do_proposal_ignore(self, target: str, permanent: bool = False) -> dict:
        if not self._idle_inspector:
            return {"status": "error", "msg": "巡检系统未初始化"}
        self._idle_inspector.ignore_tool(target, permanent=permanent)
        mode = "永久" if permanent else "本次"
        return {
            "status": "success",
            "msg": f"已忽略「{target}」（{mode}），下次巡检不再报告。",
            "target": target,
            "permanent": permanent,
        }

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------

    @agent_tool(
        name="schedule_create",
        description="Create a recurring scheduled task. The agent will auto-execute the task at the specified time and deliver results to the given channels. Use this when user says '每天/每周/每月 做X' or wants periodic reminders. Before calling, confirm: task content, time, cycle, delivery channels (default: chat+wechat)."
    )
    async def do_schedule_create(
        self,
        name: str,
        execution_prompt: str,
        cycle: str = "daily",
        start_hour: int = 9,
        start_minute: int = 0,
        description: str = "",
        delivery_channels: list = None,
        cycle_day: int = 0,
    ) -> dict:
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not name.strip():
            return {"status": "error", "msg": "必须提供任务名称"}
        if not execution_prompt.strip():
            return {"status": "error", "msg": "必须提供 execution_prompt（任务执行指令）"}
        if cycle not in ("daily", "weekly", "monthly"):
            return {"status": "error", "msg": "cycle 必须是 daily/weekly/monthly"}
        channels = delivery_channels or ["chat", "wechat"]
        for ch in channels:
            if ch not in ("chat", "wechat", "dingtalk", "feishu"):
                return {"status": "error", "msg": f"无效的交付渠道: {ch}"}
        conflicts = self._scheduler.find_conflicts(
            start_hour, start_minute, cycle=cycle, cycle_day=cycle_day,
        )
        conflict_info = None
        if conflicts:
            conflict_names = ", ".join(
                f"「{c['name']}」({c['start_hour']:02d}:{c['start_minute']:02d})"
                for c in conflicts
            )
            conflict_info = {
                "has_conflict": True,
                "conflict_count": len(conflicts),
                "conflict_tasks": conflict_names,
                "warning": (
                    f"⚠️ 同一时间段 ({start_hour:02d}:{start_minute:02d}) 已有 {len(conflicts)} 个任务：{conflict_names}。"
                    f"所有任务将在该时间同时执行。建议与用户确认：是否仍要创建，或将新任务调整到其他时间？"
                ),
            }
        task = self._scheduler.create({
            "type": "reminder",
            "name": name.strip(),
            "description": description or name.strip(),
            "cycle": cycle,
            "cycle_day": cycle_day,
            "start_hour": start_hour,
            "start_minute": start_minute,
            "channels": channels,
            "execution_prompt": execution_prompt.strip(),
            "delivery_channels": channels,
        })
        time_desc = f"{start_hour:02d}:{start_minute:02d}"
        cycle_desc = {"daily": "每天", "weekly": f"每周{['一','二','三','四','五','六','日'][cycle_day]}", "monthly": f"每月{cycle_day}号"}[cycle]
        result = {
            "status": "success",
            "msg": f"已创建定时任务「{name}」— {cycle_desc} {time_desc} 自动执行，交付渠道：{', '.join(channels)}",
            "task": task,
        }
        if conflict_info:
            result["conflict"] = conflict_info
            result["msg"] += "。\n\n" + conflict_info["warning"]
        return result

    @agent_tool(
        name="schedule_list",
        description="List all scheduled tasks. Each task shows its name, cycle, execution time, and delivery channels. Use to review what periodic tasks are configured."
    )
    async def do_schedule_list(self) -> dict:
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        tasks = self._scheduler.list_tasks()
        if not tasks:
            return {"status": "success", "tasks": [], "msg": "暂无定时任务"}
        return {"status": "success", "tasks": tasks, "count": len(tasks)}

    @agent_tool(
        name="schedule_update",
        description="Update an existing scheduled task. Only the fields you provide (non-empty/non-default) will be changed. "
                    "Updatable fields: name, execution_prompt, cycle (daily/weekly/monthly), cycle_day (1-31 for monthly, "
                    "1-7 for weekly where 1=Monday), start_hour (0-23), start_minute (0-59), description, "
                    "delivery_channels (list like [\"chat\",\"wechat\"]), enabled (true/false)."
    )
    async def do_schedule_update(self, id: str, name: str = "",
                                  execution_prompt: str = "", cycle: str = "",
                                  cycle_day: int = 0, start_hour: int = -1,
                                  start_minute: int = -1, description: str = "",
                                  delivery_channels: list = None,
                                  enabled: bool = None) -> dict:
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not id:
            return {"status": "error", "msg": "必须提供任务 ID"}
        # Build kwargs from only non-default values
        kwargs = {}
        if name:
            kwargs["name"] = name
        if execution_prompt:
            kwargs["execution_prompt"] = execution_prompt
        if cycle:
            kwargs["cycle"] = cycle
        if cycle_day > 0:
            kwargs["cycle_day"] = cycle_day
        if start_hour >= 0:
            kwargs["start_hour"] = start_hour
        if start_minute >= 0:
            kwargs["start_minute"] = start_minute
        if description:
            kwargs["description"] = description
        if delivery_channels is not None:
            kwargs["delivery_channels"] = delivery_channels
        if enabled is not None:
            kwargs["enabled"] = enabled
        updated = self._scheduler.update(id, kwargs)
        if updated is None:
            return {"status": "error", "msg": f"未找到任务: {id}"}
        return {"status": "success", "msg": f"已更新任务「{updated['name']}」", "task": updated}

    @agent_tool(
        name="schedule_delete",
        description="Delete a scheduled task by ID. This permanently removes the task — it will no longer execute. Use when user says '取消/删除 定时任务X'."
    )
    async def do_schedule_delete(self, id: str) -> dict:
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not id:
            return {"status": "error", "msg": "必须提供任务 ID"}
        deleted = self._scheduler.delete(id)
        if not deleted:
            return {"status": "error", "msg": f"未找到任务: {id}"}
        return {"status": "success", "msg": f"已删除定时任务"}

    @agent_tool(
        name="schedule_run",
        description="Manually trigger a scheduled task immediately (does not wait for its scheduled time). Use when user says '现在就执行' for a specific scheduled task."
    )
    async def do_schedule_run(self, id: str) -> dict:
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not id:
            return {"status": "error", "msg": "必须提供任务 ID"}
        task = self._scheduler.get(id)
        if task is None:
            return {"status": "error", "msg": f"未找到任务: {id}"}
        if not task.get("execution_prompt", "").strip():
            return {"status": "error", "msg": f"任务「{task['name']}」没有 execution_prompt，无法手动执行"}
        prompt = task["execution_prompt"]
        delivery = task.get("delivery_channels", ["chat", "wechat"])
        result = {
            "status": "success",
            "msg": f"已手动触发任务「{task['name']}」",
            "task_name": task["name"],
            "execution_prompt": prompt,
            "delivery_channels": delivery,
        }
        if self._idle_inspector and self._idle_inspector._executor_callback:
            asyncio.create_task(self._idle_inspector._executor_callback(task))
            result["msg"] += "，正在后台执行"
        else:
            result["msg"] += "，但执行器未连接——请等待定时触发"
        return result

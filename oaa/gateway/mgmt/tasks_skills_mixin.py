"""Tasks and Skills mixin — scheduled task CRUD and skill operations."""
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class TasksSkillsMixin:
    """Scheduled task management and skill operations."""

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def _handle_get_tasks(self, _payload: dict) -> dict:
        """Return all tasks from the scheduler."""
        tasks = self._scheduler.list_tasks(include_disabled=True)
        return {"ok": True, "tasks": tasks}

    def _handle_save_task(self, payload: dict) -> dict:
        """Create or update a task."""
        task_data = payload.get("task", {})
        if not task_data:
            return {"ok": False, "error": "No task data provided"}

        # Sync delivery_channels with channels — GUI only manages "channels"
        if "channels" in task_data:
            task_data["delivery_channels"] = list(task_data["channels"])

        # Auto-generate description if not provided
        if "description" not in task_data or not task_data.get("description"):
            task_data["description"] = self._render_task_description(task_data)

        task_id = task_data.get("id", "")
        if task_id and self._scheduler.get(task_id):
            # Update existing
            updated = self._scheduler.update(task_id, task_data)
            return {"ok": True, "task": updated}
        else:
            # Create new
            created = self._scheduler.create(task_data)
            return {"ok": True, "task": created}

    @staticmethod
    def _render_task_description(task: dict) -> str:
        """Generate a human-readable description from task fields."""
        cycle_map = {"daily": "每天", "weekly": "每周", "monthly": "每月"}
        cycle = cycle_map.get(task.get("cycle", "daily"), "每天")
        hour = task.get("start_hour", 9)
        minute = task.get("start_minute", 0)
        channels = task.get("channels", [])
        chan_map = {"chat": "聊天页面", "wechat": "微信", "dingtalk": "钉钉", "feishu": "飞书"}
        chan_labels = [chan_map.get(c, c) for c in channels]
        return f"{cycle}{hour:02d}:{minute:02d}执行，通过{'、'.join(chan_labels)}交付"

    def _handle_delete_task(self, payload: dict) -> dict:
        """Delete a task by id."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        deleted = self._scheduler.delete(task_id)
        return {"ok": deleted, "id": task_id}

    def _handle_toggle_task(self, payload: dict) -> dict:
        """Toggle task enabled/disabled."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        task = self._scheduler.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task not found: {task_id}"}
        updated = self._scheduler.update(task_id, {"enabled": not task.get("enabled", True)})
        return {"ok": True, "task": updated}

    def _handle_get_task_history(self, _payload: dict) -> dict:
        """Return recent task execution history."""
        history = self._scheduler.get_execution_history(limit=50)
        return {"ok": True, "history": history}

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def _handle_get_skills(self, _payload: dict) -> dict:
        """Return all active skills from SkillManager."""
        skills = []
        for info in self._skill_mgr.list_all():
            skills.append({
                "name": info.name,
                "display_name": info.display_name,
                "description": info.description,
                "category": info.category,
                "path": info.path,
                "loaded": bool(info.skill_md),
                "tools_count": len(info.tools) if info.tools else 0,
                "knowledge_count": len(info.knowledge) if info.knowledge else 0,
            })
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "skills": skills,
            "current": current.name if current else None,
            "total": len(skills),
        }

    def _handle_get_skill_detail(self, payload: dict) -> dict:
        """Return full detail for a single skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.get(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        info.load()  # ensure fresh
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "name": info.name,
            "category": info.category,
            "description": info.description,
            "skill_md": info.skill_md,
            "sop_md": info.sop_md,
            "tools": info.tools,
            "knowledge": info.knowledge,
            "is_current": current is not None and current.name == info.name,
        }

    def _handle_switch_skill(self, payload: dict) -> dict:
        """Switch the active skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.switch_to(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        return {"ok": True, "current": info.name}

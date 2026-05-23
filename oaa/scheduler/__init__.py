"""Task scheduler — timed task management for OAA.

Stores tasks as JSON files under ``<data_dir>/tasks/`` and runs a background
loop that checks for due tasks on the configured cycle.
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger

logger = get_logger("scheduler")


class TaskScheduler:
    """Lightweight task scheduler with JSON file storage.

    Each task has:
        - id, type (fixed|reminder), name, description
        - enabled, cycle (daily|weekly|monthly), cycle_day, start_hour, start_minute
        - channels, report, report_channels, confirm_receipt
        - execution_prompt (what the agent should DO when task fires)
        - delivery_channels (where to send results: "chat", "wechat", "dingtalk", "feishu")
        - created_at, updated_at, last_run
    """

    def __init__(self, tasks_dir: str):
        self.tasks_dir = Path(tasks_dir)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self.tasks_dir / "tasks.json"
        self._tasks: list[dict] = self._load()
        self._running = False

    def _load(self) -> list[dict]:
        if self._tasks_file.exists():
            try:
                return json.loads(self._tasks_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self):
        self._tasks_file.write_text(
            json.dumps(self._tasks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, task: dict) -> dict:
        """Create a new task. Returns the created task with generated id."""
        new = {
            "id": uuid.uuid4().hex[:12],
            "type": task.get("type", "reminder"),
            "name": task.get("name", ""),
            "description": task.get("description", ""),
            "enabled": task.get("enabled", True),
            "cycle": task.get("cycle", "daily"),
            "cycle_day": task.get("cycle_day", 0),
            "start_hour": task.get("start_hour", 9),
            "start_minute": task.get("start_minute", 0),
            "channels": task.get("channels", []),
            "report": task.get("report", False),
            "report_channels": task.get("report_channels", []),
            "confirm_receipt": task.get("confirm_receipt", False),
            "execution_prompt": task.get("execution_prompt", ""),
            "delivery_channels": task.get("delivery_channels", ["chat", "wechat"]),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_run": None,
        }
        self._tasks.append(new)
        self._save()
        return new

    def find_conflicts(self, start_hour: int, start_minute: int,
                       cycle: str = "daily", cycle_day: int = 0,
                       exclude_id: str = "") -> list[dict]:
        """Return enabled tasks that share the same time slot.

        Args:
            start_hour, start_minute: time slot to check
            cycle: task cycle to compare
            cycle_day: day specifier (0-6 for weekly, 1-31 for monthly)
            exclude_id: task ID to skip (for update, not self-conflict)
        """
        conflicts = []
        for t in self._tasks:
            if not t.get("enabled", True):
                continue
            if exclude_id and t["id"] == exclude_id:
                continue
            if (t.get("start_hour") == start_hour
                    and t.get("start_minute") == start_minute
                    and t.get("cycle") == cycle
                    and t.get("cycle_day", 0) == cycle_day):
                conflicts.append(t)
        return conflicts

    def list_tasks(self, include_disabled: bool = True) -> list[dict]:
        """Return all tasks, newest first."""
        if include_disabled:
            return list(reversed(self._tasks))
        return [t for t in reversed(self._tasks) if t.get("enabled", True)]

    def get(self, task_id: str) -> Optional[dict]:
        for t in self._tasks:
            if t["id"] == task_id:
                return t
        return None

    def update(self, task_id: str, updates: dict) -> Optional[dict]:
        """Update task fields. Returns updated task or None if not found."""
        for t in self._tasks:
            if t["id"] == task_id:
                safe_keys = {
                    "name", "description", "enabled", "type", "cycle",
                    "cycle_day", "start_hour", "start_minute", "channels",
                    "report", "report_channels", "confirm_receipt",
                    "execution_prompt", "delivery_channels",
                }
                for k, v in updates.items():
                    if k in safe_keys:
                        t[k] = v
                t["updated_at"] = datetime.now().isoformat()
                self._save()
                return t
        return None

    def delete(self, task_id: str) -> bool:
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if len(self._tasks) < before:
            self._save()
            return True
        return False

    def complete(self, task_id: str) -> Optional[dict]:
        """Mark a one-time task as completed (disabled)."""
        t = self.get(task_id)
        if t:
            t["enabled"] = False
            t["updated_at"] = datetime.now().isoformat()
            t["last_run"] = datetime.now().isoformat()
            self._save()
        return t

    # ------------------------------------------------------------------
    # Due-task checking
    # ------------------------------------------------------------------

    def _is_due(self, task: dict, now: datetime) -> bool:
        """Check if a task is due at *now* based on its cycle configuration."""
        if not task.get("enabled", True):
            return False
        hour = task.get("start_hour", 0)
        minute = task.get("start_minute", 0)
        if now.hour != hour or now.minute != minute:
            return False

        cycle = task.get("cycle", "daily")
        cycle_day = task.get("cycle_day", 0)

        if cycle == "daily":
            return True
        elif cycle == "weekly":
            return now.weekday() == cycle_day
        elif cycle == "monthly":
            return now.day == cycle_day
        return True

    def get_due_tasks(self) -> list[dict]:
        """Return tasks that are due right now, ordered by creation time."""
        now = datetime.now()
        now = now.replace(second=0, microsecond=0)
        due = []
        for t in self._tasks:
            if self._is_due(t, now):
                due.append(t)
        # Sort by creation time so older tasks execute first
        due.sort(key=lambda t: t.get("created_at", ""))
        return due

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def start_loop(self):
        """Background loop: checks for due tasks every 30 seconds."""
        self._running = True
        while self._running:
            try:
                due = self.get_due_tasks()
                for task in due:
                    logger.info("Task due: %s (%s)", task["name"], task["id"])
                    # Mark last_run
                    task["last_run"] = datetime.now().isoformat()
                if due:
                    self._save()
            except Exception as exc:
                logger.error("Scheduler check error: %s", exc)
            await asyncio.sleep(30)

    def stop_loop(self):
        self._running = False

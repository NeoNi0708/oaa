"""Task scheduler — timed task management for OAA.

Stores tasks as JSON files under ``<data_dir>/tasks/`` and runs a background
loop that checks for due tasks on the configured cycle.
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

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
        self._history_file = self.tasks_dir / "task_history.json"
        self._history: list[dict] = self._load_history()
        self._running = False
        self._due_callback = None  # async callable(task_dict) for auto-execution
        self._last_check_min: int = -1  # prevent double-fire within same minute
        self._notify_callback: Callable[[str, dict], None] | None = None  # push notification

    def _load_history(self) -> list[dict]:
        try:
            if self._history_file.exists():
                return json.loads(self._history_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load task history: %s", exc)
        return []

    def _save_history(self):
        try:
            self._history_file.write_text(
                json.dumps(self._history[-200:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save task history: %s", exc)

    def record_execution(self, task_id: str, task_name: str, status: str, summary: str = ""):
        """Record a task execution result (success or failure) to history."""
        self._history.append({
            "task_id": task_id,
            "task_name": task_name,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "summary": summary[:500],
        })
        self._save_history()

    def get_execution_history(self, limit: int = 50) -> list[dict]:
        """Return the most recent *limit* execution records, newest first."""
        return list(reversed(self._history[-limit:]))

    def set_notify_callback(self, callback: Callable[[str, dict], None]):
        """Register a callback for real-time UI push notifications."""
        self._notify_callback = callback

    def _notify(self, action: str, task: dict):
        """Push a task_updated notification to registered callback."""
        if self._notify_callback:
            try:
                self._notify_callback("task_updated", {
                    "action": action,
                    "task": task,
                })
            except Exception:
                pass

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
        self._notify("create", new)
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
                self._notify("update", t)
                return t
        return None

    def delete(self, task_id: str) -> bool:
        before = len(self._tasks)
        # Capture task data before removing for notification
        deleted_task = None
        for t in self._tasks:
            if t["id"] == task_id:
                deleted_task = dict(t)
                break
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if len(self._tasks) < before:
            self._save()
            if deleted_task:
                self._notify("delete", deleted_task)
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
            self._notify("complete", t)
        return t

    # ------------------------------------------------------------------
    # Due-task checking
    # ------------------------------------------------------------------

    def _is_due(self, task: dict, now: datetime) -> bool:
        """Check if a task is due at *now* based on its cycle configuration."""
        if not task.get("enabled", True):
            return False
        hour = int(task.get("start_hour", 0))
        minute = int(task.get("start_minute", 0))
        if now.hour != hour or now.minute != minute:
            return False

        # Skip if last_run is within the same minute (already fired)
        last_run = task.get("last_run")
        if last_run:
            try:
                lr = datetime.fromisoformat(last_run) if isinstance(last_run, str) else datetime.min
                if lr.hour == now.hour and lr.minute == now.minute and lr.day == now.day:
                    return False
            except (ValueError, TypeError):
                pass

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

    def set_due_callback(self, callback):
        """Register an async callback for tasks with execution_prompt."""
        self._due_callback = callback

    async def start_loop(self):
        """Background loop: checks for due tasks every 30 seconds.

        Tasks with ``execution_prompt`` are dispatched to the callback
        immediately.  The callback runs in its own task so a slow executor
        won't delay the next check cycle.
        """
        self._running = True
        while self._running:
            try:
                now = datetime.now()
                current_min = now.minute
                # Skip if we already checked this minute (prevents double-fire on 30s cycle)
                if current_min == self._last_check_min:
                    await asyncio.sleep(30)
                    continue
                self._last_check_min = current_min

                due = self.get_due_tasks()
                for task in due:
                    logger.info("Task due: %s (%s)", task["name"], task["id"])
                    task["last_run"] = datetime.now().isoformat()
                    # Auto-execute tasks that have an execution_prompt
                    if task.get("execution_prompt") and self._due_callback:
                        asyncio.create_task(self._due_callback(task))
                if due:
                    self._save()
            except Exception as exc:
                logger.error("Scheduler check error: %s", exc)
            await asyncio.sleep(30)

    def stop_loop(self):
        self._running = False

"""In-memory todo store — agent's external working memory.

Unlike the planner (which persists to disk and is out of the agent's
view), the todo list is injected into the system prompt every turn so
the agent always knows what it's doing and what comes next.
"""

import time
from typing import Optional


STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"


class TodoStore:
    """Lightweight in-memory task list for agent self-management.

    One item in_progress at a time. Injected into system prompt every turn.
    """

    def __init__(self):
        self._items: list[dict] = []
        self._updated: float = 0.0

    def set(self, items: list[dict]) -> list[dict]:
        """Replace the entire todo list.

        Each item: ``{"id": str, "content": str, "status": str,
                      "done_criteria": str}``.
        Status must be one of pending/in_progress/completed/cancelled.
        At most one item may be in_progress.
        ``done_criteria`` describes what counts as completion for this step
        (e.g. "文件已保存，数据验证通过").
        """
        valid_statuses = {STATUS_PENDING, STATUS_IN_PROGRESS,
                          STATUS_COMPLETED, STATUS_CANCELLED}
        cleaned = []
        has_in_progress = False
        for item in items:
            status = item.get("status", STATUS_PENDING)
            if status not in valid_statuses:
                status = STATUS_PENDING
            if status == STATUS_IN_PROGRESS:
                if has_in_progress:
                    status = STATUS_PENDING
                else:
                    has_in_progress = True
            cleaned.append({
                "id": item.get("id", str(len(cleaned) + 1)),
                "content": item.get("content", ""),
                "status": status,
                "done_criteria": item.get("done_criteria", ""),
            })
        self._items = cleaned
        self._updated = time.time()
        return self._items

    def get(self) -> list[dict]:
        """Return the current todo list."""
        return list(self._items)

    def update(self, patches: list[dict]) -> list[dict]:
        """Merge updates into the todo list by item id.

        Each patch: ``{"id": str, "content": str | None, "status": str | None}``.
        Items with new ids are appended. Omitting content/status leaves it unchanged.
        """
        by_id = {item["id"]: item for item in self._items}
        for patch in patches:
            pid = patch.get("id", "")
            if not pid:
                continue
            if pid in by_id:
                if "content" in patch and patch["content"] is not None:
                    by_id[pid]["content"] = patch["content"]
                if "status" in patch and patch["status"] is not None:
                    by_id[pid]["status"] = patch["status"]
            else:
                by_id[pid] = {
                    "id": pid,
                    "content": patch.get("content", ""),
                    "status": patch.get("status", STATUS_PENDING),
                }
        self._items = list(by_id.values())
        self._updated = time.time()
        return self._items

    def get_injection_text(self) -> str:
        """Build the system-prompt injection block for the current todo list."""
        if not self._items:
            return ""
        lines = ["# 📋 当前任务清单", ""]
        status_icons = {
            STATUS_PENDING: "⬜",
            STATUS_IN_PROGRESS: "🔄",
            STATUS_COMPLETED: "✅",
            STATUS_CANCELLED: "❌",
        }
        for item in self._items:
            icon = status_icons.get(item["status"], "⬜")
            lines.append(f"{icon} [{item['status']}] {item['content']}")
            if item.get("done_criteria"):
                lines.append(f"   ✅ 完成标准: {item['done_criteria']}")
        lines.append("")
        lines.append("每步完成后按完成标准自检。达标才标记完成。")
        return "\n".join(lines)

    def clear(self):
        """Reset the todo list."""
        self._items = []
        self._updated = time.time()

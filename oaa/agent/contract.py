"""Task Contract — auto-managed task workspace for P3 Product Contract.

On each ``process_message()`` call, a task directory is created under
``workspace/tasks/<task_id>_<summary>/`` with structured files tracking
the task from plan to completion.  No LLM or tool involvement needed —
all writes happen in the yield loop of ``process_message()``.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Criterion:
    """A single验收标准 — checked by Validator at task end."""

    type: str  # "file_exists" | "json_schema" | "contains_text"
    path: str  # file path relative to task dir (or absolute)
    schema: dict | None = None  # for json_schema
    text: str = ""  # for contains_text
    label: str = ""


@dataclass
class ValidationResult:
    """Outcome of a single criterion check."""

    passed: bool
    criterion: Criterion
    detail: str = ""


class TaskContract:
    """Creates and maintains a task workspace for a single user message.

    Usage::

        contract = TaskContract(data_dir)
        contract.start(user_input)

        # ... process_message yield loop ...

        contract.add_step("tool_name", {"arg": "val"})
        # ... later ...
        contract.complete_step("tool_name", {"status": "ok", ...}, 1.2)

        contract.finish(final_content)
    """

    def __init__(self, data_dir: str, max_tasks: int = 50):
        self._tasks_dir = Path(data_dir) / "workspace" / "tasks"
        self._max_tasks = max_tasks
        self._task_dir: Path | None = None
        self._step_count = 0
        self._criteria: list[Criterion] = []
        self._step_depends: dict[int, list[int]] = {}  # step_id -> [depends_on step_ids]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, user_input: str):
        """Create task directory and write initial ``_plan.md``."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary = _safe_dir_name(user_input, max_len=40)
        dir_name = f"{timestamp}_{summary}"
        self._task_dir = self._tasks_dir / dir_name
        self._task_dir.mkdir(parents=True, exist_ok=True)

        # Create artifacts subdirectory
        (self._task_dir / "artifacts").mkdir(exist_ok=True)

        # Write _plan.md
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plan = (
            f"# 任务\n\n{user_input.strip()}\n\n"
            f"## 开始时间\n\n{now_str}\n\n"
            f"## 步骤\n\n"
            f"| # | 工具 | 依赖 | 状态 | 耗时 |\n"
            f"|---|------|------|------|------|\n"
        )
        self._write("_plan.md", plan)

        self._step_count = 0
        self._init_progress(now_str, user_input)

        # Enforce max tasks limit (remove oldest)
        self._enforce_limit()

    def add_step(self, tool_name: str, args: dict, depends_on: list[int] | None = None):
        """Record a tool call being made. Optionally declare step dependencies."""
        self._step_count += 1
        if depends_on:
            self._step_depends[self._step_count] = depends_on
        args_preview = _preview_args(args)
        dep_str = ", ".join(str(d) for d in depends_on) if depends_on else "-"
        entry = (
            f"\n## 步骤 {self._step_count}: {tool_name}\n\n"
            f"- **参数**: {args_preview}\n"
            f"- **依赖**: {dep_str}\n"
            f"- **状态**: ⏳ 执行中\n"
        )
        self._append("_progress.md", entry)

    def complete_step(self, tool_name: str, result, duration: float):
        """Mark the most recent step as completed."""
        status, preview = _summarize_result(result)
        suffix = f" ✅ 完成 ({duration:.1f}s)" if status == "ok" else f" ❌ 失败 ({duration:.1f}s)"
        entry = f"- **结果**: {preview}{suffix}\n"
        self._append("_progress.md", entry)

        # Auto-detect file creation from tool result
        self._auto_detect_criteria(tool_name, result if isinstance(result, dict) else {})

        # Append a row to the plan's step table
        status_icon = "✅" if status == "ok" else "❌"
        dep_str = "-"
        if self._step_count in self._step_depends:
            dep_str = ",".join(str(d) for d in self._step_depends[self._step_count])
        table_row = f"| {self._step_count} | {tool_name} | {dep_str} | {status_icon} | {duration:.1f}s |\n"
        self._append("_plan.md", table_row)

    def update_status(self, status_text: str):
        """Write a status update to progress (e.g. \"LLM call failed, retrying...\")."""
        entry = f"\n> {status_text}\n"
        self._append("_progress.md", entry)

    # ------------------------------------------------------------------
    # Acceptance criteria
    # ------------------------------------------------------------------

    def add_criterion(self, criterion: Criterion):
        """Add a验收标准 to be validated at task end."""
        self._criteria.append(criterion)
        self._sync_criteria_to_plan()

    def add_criteria(self, criteria: list[Criterion]):
        """Add multiple验收标准."""
        self._criteria.extend(criteria)
        self._sync_criteria_to_plan()

    def validate(self) -> list[ValidationResult]:
        """Run all criteria checks and return results. Does NOT modify contract state."""
        results: list[ValidationResult] = []
        task_dir = self._task_dir
        if not task_dir:
            return results

        for c in self._criteria:
            resolved_path = c.path
            if not os.path.isabs(resolved_path):
                resolved_path = str(task_dir / resolved_path)

            if c.type == "file_exists":
                passed = os.path.exists(resolved_path)
                results.append(ValidationResult(
                    passed=passed,
                    criterion=c,
                    detail=f"{'已找到' if passed else '未找到'}: {c.path}",
                ))

            elif c.type == "json_schema":
                if not os.path.exists(resolved_path):
                    results.append(ValidationResult(
                        passed=False, criterion=c, detail=f"文件不存在: {c.path}",
                    ))
                else:
                    try:
                        with open(resolved_path, encoding="utf-8") as f:
                            data = json.load(f)
                        if c.schema:
                            # Lightweight field-presence check
                            missing = [k for k in c.schema.get("required", []) if k not in data]
                            passed = not missing
                            detail = f"缺少必填字段: {missing}" if missing else "JSON 格式验证通过"
                        else:
                            passed = True
                            detail = "JSON 格式有效"
                        results.append(ValidationResult(passed=passed, criterion=c, detail=detail))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        results.append(ValidationResult(
                            passed=False, criterion=c, detail=f"JSON 解析失败: {e}",
                        ))

            elif c.type == "contains_text":
                if not os.path.exists(resolved_path):
                    results.append(ValidationResult(
                        passed=False, criterion=c, detail=f"文件不存在: {c.path}",
                    ))
                else:
                    try:
                        with open(resolved_path, encoding="utf-8") as f:
                            content = f.read()
                        passed = c.text in content
                        results.append(ValidationResult(
                            passed=passed, criterion=c,
                            detail=f"{'已找到' if passed else '未找到'}关键词: {c.text}",
                        ))
                    except (OSError, UnicodeDecodeError) as e:
                        results.append(ValidationResult(
                            passed=False, criterion=c, detail=f"读取失败: {e}",
                        ))

            else:
                results.append(ValidationResult(
                    passed=False, criterion=c, detail=f"未知验收类型: {c.type}",
                ))

        return results

    def finish(self, final_content: str):
        """Finalize the task contract — run validation and write outcome."""
        if not self._task_dir or not self._task_dir.exists():
            return

        # Append summary to progress
        summary = final_content.strip()[:500] if final_content else "(无输出)"
        self._append("_progress.md", f"\n## 完成\n\n{summary}\n")

        # Run validation if criteria exist
        validation_results = self.validate() if self._criteria else []
        all_passed = all(r.passed for r in validation_results)

        # Write _done.md
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"# 完成\n\n{now_str}\n\n{summary}\n"]
        if validation_results:
            parts.append("\n## 验收结果\n\n")
            for r in validation_results:
                icon = "✅" if r.passed else "❌"
                parts.append(f"{icon} {r.detail}\n")
            if all_passed:
                parts.append("\n✅ **全部验收通过**\n")
            else:
                parts.append("\n❌ **存在未通过的验收项**\n")

        done_file = self._task_dir / "_done.md"
        done_file.write_text("".join(parts), encoding="utf-8")

    @property
    def task_dir(self) -> str | None:
        """Path to the current task directory, or ``None``."""
        return str(self._task_dir) if self._task_dir else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, name: str, content: str):
        if self._task_dir:
            (self._task_dir / name).write_text(content, encoding="utf-8")

    def _append(self, name: str, content: str):
        if self._task_dir:
            path = self._task_dir / name
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)

    def _init_progress(self, now_str: str, user_input: str):
        progress = (
            f"# 执行进度\n\n"
            f"**任务**: {user_input.strip()}\n"
            f"**开始**: {now_str}\n\n"
            f"---\n"
        )
        self._write("_progress.md", progress)

    def _enforce_limit(self):
        """Remove oldest task directories if count exceeds ``_max_tasks``."""
        if not self._tasks_dir.exists():
            return
        entries = sorted(
            [p for p in self._tasks_dir.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
        )
        while len(entries) > self._max_tasks:
            oldest = entries.pop(0)
            try:
                shutil.rmtree(str(oldest))
            except OSError:
                pass

    def _sync_criteria_to_plan(self):
        """Write criteria checklist into ``_plan.md`` (idempotent append)."""
        if not self._criteria:
            return
        # Check if criteria section already exists
        plan_path = self._task_dir / "_plan.md" if self._task_dir else None
        if not plan_path or not plan_path.exists():
            return

        plan = plan_path.read_text(encoding="utf-8")
        if "## 验收标准" in plan:
            return  # already written

        lines = ["\n## 验收标准\n\n"]
        for c in self._criteria:
            label = c.label or f"{c.type}: {c.path}"
            lines.append(f"- [ ] {label}\n")
        self._append("_plan.md", "".join(lines))

    def _auto_detect_criteria(self, tool_name: str, result: dict):
        """Automatically add file_exists criterion when a tool creates files."""
        task_dir = self._task_dir
        if not task_dir:
            return
        file_keys = ("path", "output", "file", "dst", "target")
        for key in file_keys:
            val = result.get(key)
            if val and isinstance(val, str) and "." in os.path.basename(val):
                # Make path relative to task dir for cleaner display
                try:
                    rel = os.path.relpath(val, str(task_dir))
                except ValueError:
                    rel = val
                # Skip paths that don't look like task artifacts
                if rel.startswith("..") and not os.path.exists(val):
                    continue
                self.add_criterion(Criterion(
                    type="file_exists",
                    path=rel,
                    label=f"产物: {os.path.basename(val)}",
                ))
                break


def _safe_dir_name(text: str, max_len: int = 40) -> str:
    """Convert user input to a safe directory name fragment."""
    clean = re.sub(r"[^\w\-一-鿿]+", "_", text.strip())[:max_len]
    return clean.strip("_") or "task"


def _preview_args(args: dict, max_len: int = 80) -> str:
    """Compact one-line arg preview for progress logging."""
    if not args:
        return "(无)"
    parts = []
    for k, v in list(args.items())[:3]:
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "..."
        parts.append(f"{k}={sv}")
    preview = " ".join(parts)
    if len(preview) > max_len:
        preview = preview[: max_len - 3] + "..."
    return preview


def _summarize_result(result) -> tuple[str, str]:
    """Return (status_label, short_preview) for any tool result."""
    if isinstance(result, dict):
        if result.get("status") == "error":
            return "error", str(result.get("msg", ""))[:80]
        for key in ("result", "content", "output", "data"):
            val = result.get(key)
            if val is not None:
                s = str(val)[:80]
                return "ok", s
        return "ok", f"({len(str(result))} bytes)"
    s = str(result)
    return "ok", s[:80]

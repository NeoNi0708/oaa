"""Atomic tools — ported from GenericAgent ga.py. All tools as async methods."""
import ast
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
from typing import TYPE_CHECKING, Any, Optional

from ..auth.permissions import PermissionsManager
from ..logging_config import get_logger
from .handler import BaseHandler
from .path_utils import resolve_workspace_path
from .tool_decorator import agent_tool

if TYPE_CHECKING:
    from .conversation_archiver import ConversationArchiver
    from .memory_manager import MemoryManager

logger = get_logger("agent.tools")

_SANDBOX_RUNNER = os.path.join(os.path.dirname(__file__), "_sandbox_runner.py")
_EXEC_RUNNER = os.path.join(os.path.dirname(__file__), "_exec_runner.py")

# OAA project root for self-modification tools
OAA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_OAA_SOURCE_DIRS = {os.path.normpath(os.path.join(OAA_ROOT, d)) for d in ("oaa", "skills", "dynamic_tools")}
_OAA_BACKUP_DIR = "backups"  # relative to data_dir

# ---------------------------------------------------------------------------
# code_exec auto-correction helpers
# ---------------------------------------------------------------------------

_COMMON_MODULES = frozenset({
    "json", "os", "re", "sys", "math", "random", "datetime",
    "collections", "itertools", "functools", "typing", "pathlib",
    "shutil", "glob", "csv", "io", "string", "copy", "decimal",
    "hashlib", "base64", "uuid", "pprint", " fractions",
})


def _fix_syntax_errors(code: str) -> tuple[str, str]:
    """Pre-execution syntax fix — handle indentation issues common in LLM output.

    Returns (fixed_code, description).
    Returns the original code unchanged if no fix is needed.
    """
    # Fix 1: normalize tabs to spaces
    lines = code.split("\n")
    fixed = False
    normalized = []
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            leading = line[:len(line) - len(stripped)]
            norm = leading.replace("\t", "    ")
            if norm != leading:
                fixed = True
            normalized.append(norm + stripped)
        else:
            normalized.append(line)

    if fixed:
        try:
            ast.parse("\n".join(normalized))
            return "\n".join(normalized), "统一缩进为空格"
        except SyntaxError:
            pass  # other errors remain, fall through

    # Fix 2: remove accidental module-level indentation
    try:
        dedented = textwrap.dedent(code)
        if dedented != code:
            ast.parse(dedented)
            return dedented, "移除多余缩进"
    except SyntaxError:
        pass

    return code, ""


def _fix_name_error(code: str, stderr: str) -> tuple[str, str]:
    """Post-execution NameError fix — add missing import statements.

    Returns (fixed_code, description).
    Returns the original code unchanged if no fix applies.
    """
    m = re.search(r"NameError.*?name '(\w+)' is not defined", stderr)
    if not m:
        return code, ""

    name = m.group(1)
    if name not in _COMMON_MODULES:
        return code, ""

    # Don't re-insert if already imported
    for line in code.split("\n"):
        if re.match(rf"^\s*import\s+{name}(\s|$)", line) or re.match(rf"^\s*from\s+{name}\s+import", line):
            return code, ""

    fixed = f"import {name}\n{code}"
    return fixed, f"自动补充 import {name}"


class AtomicTools(BaseHandler):
    """9 atomic tools from GenericAgent, adapted to async."""

    def __init__(self, data_dir: str, permissions: Optional[PermissionsManager] = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self.working_memory = {}
        self._memory_mgr: Optional["MemoryManager"] = None
        self._archiver: Optional["ConversationArchiver"] = None
        self._proposal_store = None
        self._idle_inspector = None
        self._wechat_cli_path: str = ""

    def _resolve_path(self, path: str) -> str:
        """Resolve path relative to workspace, checking permissions if configured."""
        return resolve_workspace_path(path, self.data_dir, self.permissions)

    def set_memory_manager(self, mgr: "MemoryManager"):
        """Inject the tiered memory manager."""
        self._memory_mgr = mgr

    def set_archiver(self, archiver: "ConversationArchiver"):
        """Inject the conversation archiver for history search."""
        self._archiver = archiver

    def set_proposal_store(self, store):
        """Inject the ProposalStore for structured proposal management."""
        self._proposal_store = store

    def set_idle_inspector(self, inspector):
        """Inject the IdleInspector for registering tool ignores."""
        self._idle_inspector = inspector

    def set_wechat_cli_path(self, path: str):
        """Set the path to wechat-cli binary for WeChat data tools."""
        self._wechat_cli_path = path

    async def dispatch(self, tool_name: str, args: dict) -> Any:
        """Dispatch tool call and record successful completions for trust tracking."""
        result = await super().dispatch(tool_name, args)
        if self.permissions and isinstance(result, dict) and result.get("status") in ("success", "ok"):
            await self.permissions.record_tool_success(tool_name)
        return result

    async def _confirm(self, operation: str, details: str = "") -> bool:
        """Check permission for an operation. Returns True if allowed."""
        if self.permissions:
            return await self.permissions.confirm_operation(operation, details)
        return True

    # ------------------------------------------------------------------
    # Self-modification helpers (backup / changelog / pycache)
    # ------------------------------------------------------------------

    def _is_oaa_path(self, path: str) -> bool:
        """Check if *path* is within an OAA source directory (oaa/, skills/, dynamic_tools/)."""
        norm = os.path.normpath(path)
        return any(norm.startswith(d + os.sep) or norm == d for d in _OAA_SOURCE_DIRS)

    def _backup_file(self, filepath: str) -> str:
        """Create a timestamped backup of *filepath* in ``data_dir/backups/``.

        Returns the backup path, or empty string on failure.
        """
        backup_dir = os.path.join(self.data_dir, _OAA_BACKUP_DIR)
        rel = os.path.relpath(filepath, OAA_ROOT)
        backup_path = os.path.join(backup_dir, f"{rel}.{int(time.time())}.bak")
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        try:
            import shutil
            shutil.copy2(filepath, backup_path)
            logger.info("Backup created: %s -> %s", filepath, backup_path)
            return backup_path
        except Exception as exc:
            logger.warning("Backup failed for %s: %s", filepath, exc)
            return ""

    def _record_change(self, filepath: str, description: str, backup_path: str = ""):
        """Append a self-modification record to ``data_dir/backups/changelog.md``."""
        from datetime import datetime
        changelog_dir = os.path.join(self.data_dir, _OAA_BACKUP_DIR)
        os.makedirs(changelog_dir, exist_ok=True)
        changelog_path = os.path.join(changelog_dir, "changelog.md")
        rel = os.path.relpath(filepath, OAA_ROOT)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n## {timestamp}\n"
            f"- **file**: {rel}\n"
            f"- **change**: {description}\n"
            f"- **backup**: {os.path.relpath(backup_path, changelog_dir) if backup_path else 'none'}\n"
            f"- **status**: active\n"
        )
        try:
            with open(changelog_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as exc:
            logger.warning("Failed to record change: %s", exc)

    def _record_rollback_entry(self, filepath: str, description: str, backup_path: str = ""):
        """Record a change in the rollback manifest for self-healing tracking.

        This complements ``_record_change`` (changelog.md) with a structured
        JSON manifest that the :class:`~oaa.agent.repair_loop.RepairLoop`
        reads to roll back changes on failure.
        """
        if not self._is_oaa_path(filepath):
            return
        try:
            from .repair_loop import record_rollback_entry as _record

            # proposal_id isn't known at the tool level — use a sentinel.
            # The RepairLoop will link the manifest entry to the proposal
            # when it reads the manifest during rollback.
            rel = os.path.relpath(filepath, OAA_ROOT)
            change = {
                "type": "file_edit",
                "path": rel,
                "description": description[:200],
            }
            if backup_path:
                change["backup"] = backup_path
            _record(self.data_dir, "_tool_level", change)
        except Exception as exc:
            logger.debug("Failed to record rollback entry: %s", exc)

    def _clear_pycache(self, filepath: str):
        """Remove __pycache__ entries for the module containing *filepath*."""
        pycache_dir = os.path.join(os.path.dirname(filepath), "__pycache__")
        if not os.path.isdir(pycache_dir):
            return
        module_name = os.path.splitext(os.path.basename(filepath))[0]
        try:
            for fname in os.listdir(pycache_dir):
                if fname.startswith(module_name + ".") and fname.endswith(".pyc"):
                    os.remove(os.path.join(pycache_dir, fname))
                    logger.debug("Cleared pycache: %s", fname)
        except Exception as exc:
            logger.warning("Failed to clear pycache for %s: %s", module_name, exc)

    @agent_tool(
        name="code_run",
        description="Execute Python/PowerShell code within workspace restrictions."
    )
    async def do_code_run(self, code: str, type: str = "python", timeout: int = 15, cwd: str = "") -> dict:
        """Execute Python/PowerShell code within workspace restrictions."""
        code_type = type
        timeout = min(timeout, 60)
        cwd = self._resolve_path(cwd) if cwd else "."

        if code_type in ("python", "py"):
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8", dir=self.data_dir
            ) as f:
                f.write(code)
                tmp_path = f.name
            cmd = [sys.executable, "-I", "-X", "utf8", "-u", _SANDBOX_RUNNER, tmp_path]
        elif code_type in ("powershell", "ps1"):
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", code]
        else:
            return {"status": "error", "msg": f"Unsupported type: {code_type}"}

        try:
            logger.info("code_run: type=%s cwd=%s timeout=%s", code_type, cwd, timeout)
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("code_run timeout after %ss", timeout)
                return {"status": "error", "msg": f"Timeout after {timeout}s"}
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            logger.info("code_run exit_code=%s", proc.returncode)
            return {"status": "success" if proc.returncode == 0 else "error",
                    "stdout": output[:50000], "exit_code": proc.returncode}
        except Exception as exc:
            logger.error("code_run failed: %s", exc)
            return {"status": "error", "msg": str(exc)}
        finally:
            if code_type in ("python", "py") and 'tmp_path' in locals():
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @agent_tool(
        name="code_exec",
        description="Execute Python code in-process for agent self-extension. Allows most imports but blocks shell execution (os.system, subprocess.Popen, shutil.rmtree, etc.). Includes auto-correction layer (SyntaxError fix, NameError import injection)."
    )
    async def do_code_exec(self, code: str, timeout: int = 15) -> dict:
        """Execute Python code in-process for agent self-extension.

        Allows most imports but blocks shell execution (os.system,
        subprocess.Popen, shutil.rmtree, etc.).  Uses the ``result``
        variable convention for return values.

        Includes auto-correction layer:
        - Pre-execution: fixes indentation issues (SyntaxError)
        - Post-execution: adds missing import statements (NameError)
        - Returns ``fix_applied`` / ``original_code`` / ``fixed_code`` so the
          LLM can learn from the correction.
        """
        if not await self._confirm("code_exec", code[:120]):
            return {"status": "error", "msg": "Code execution not permitted"}
        timeout = min(timeout, 60)

        if not code.strip():
            return {"status": "error", "msg": "No code provided"}

        # Pre-execution syntax fix
        original_code = code
        code, fix_desc = _fix_syntax_errors(code)
        all_fixes = [fix_desc] if fix_desc else []

        # Try execution, with one re-try after NameError fix
        for attempt in range(2):
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8", dir=self.data_dir
            ) as f:
                f.write(code)
                tmp_path = f.name

            result_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=self.data_dir)
            result_path = result_file.name
            result_file.close()

            cmd = [sys.executable, "-I", "-X", "utf8", "-u", _EXEC_RUNNER, "--timeout", str(timeout), tmp_path, result_path]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    return {"status": "error", "msg": f"Timeout after {timeout}s"}

                stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
                stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

                result_data = {}
                try:
                    with open(result_path, "r", encoding="utf-8") as f:
                        result_data = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass

                base = {
                    "result": result_data.get("result"),
                    "stdout": stdout_str[:50000] if stdout_str else "",
                    "stderr": stderr_str[:50000] if stderr_str else "",
                    "exit_code": proc.returncode,
                }

                if proc.returncode == 0:
                    base["status"] = "success"
                    if all_fixes:
                        base["fix_applied"] = "; ".join(all_fixes)
                        base["original_code"] = original_code
                        base["fixed_code"] = code
                    return base

                # Attempt NameError fix on first failure (skip for syntax-only fixes)
                if attempt == 0:
                    code, fix2 = _fix_name_error(code, stderr_str)
                    if fix2:
                        all_fixes.append(fix2)
                        continue  # retry with fixed code

                # Unfixable error — return full traceback for LLM self-repair
                base["status"] = "error"
                if all_fixes:
                    base["fix_applied"] = "; ".join(all_fixes)
                    base["original_code"] = original_code
                    base["fixed_code"] = code
                return base

            except Exception as exc:
                logger.error("code_exec failed: %s", exc)
                return {"status": "error", "msg": str(exc)}
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                try:
                    os.unlink(result_path)
                except OSError:
                    pass

    @agent_tool(description="Read file content")
    async def do_file_read(self, path: str, start: int = 1, count: int = 200, keyword: str = "") -> dict:
        """Read file content. Returns dict with status/content."""
        path = self._resolve_path(path)
        if not os.path.exists(path):
            return {"status": "error", "msg": "File not found"}
        if not os.path.isfile(path):
            return {"status": "error", "msg": "Not a file"}

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as exc:
            logger.error("file_read failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

        if keyword:
            matches = [(i, ln) for i, ln in enumerate(lines, 1) if keyword.lower() in ln.lower()]
            if not matches:
                return {"status": "success", "content": f"Keyword '{keyword}' not found", "line_count": 0}
            start_line = max(0, matches[0][0] - 1 - count // 2)
            lines = lines[start_line:start_line + count]
        else:
            lines = lines[start - 1:start - 1 + count]

        content = "\n".join(ln.rstrip("\n\r") for ln in lines)
        logger.info("file_read: path=%s lines=%d", os.path.basename(path), len(lines))
        return {"status": "success", "content": content, "line_count": len(lines)}

    @agent_tool(
        name="file_write",
        description="Create or overwrite a file. Auto-backs up when editing OAA source. Supports overwrite, append, and prepend modes."
    )
    async def do_file_write(self, path: str, content: str, mode: str = "overwrite") -> dict:
        """Create or modify a file. Auto-backs up when editing OAA source."""
        path = self._resolve_path(path)
        if not await self._confirm("file_write", path):
            return {"status": "error", "msg": "File write not permitted"}
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
            is_oaa = self._is_oaa_path(path) and os.path.exists(path)
            backup_path = ""
            if is_oaa:
                backup_path = self._backup_file(path)

            if mode == "append":
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            elif mode == "prepend":
                old = open(path, "r", encoding="utf-8").read() if os.path.exists(path) else ""
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content + old)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            if is_oaa:
                self._record_change(path, f"file_write ({mode})", backup_path)
                self._record_rollback_entry(path, f"file_write ({mode})", backup_path)
                self._clear_pycache(path)

            logger.info("file_write: path=%s mode=%s bytes=%d", os.path.basename(path), mode, len(content))
            return {"status": "success", "bytes": len(content)}
        except Exception as exc:
            logger.error("file_write failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="file_patch",
        description="Replace unique text in a file. Auto-backs up when editing OAA source. The old_content must appear exactly once in the file."
    )
    async def do_file_patch(self, path: str, old_content: str, new_content: str) -> dict:
        """Replace unique text in a file. Auto-backs up when editing OAA source."""
        path = self._resolve_path(path)
        if not await self._confirm("file_patch", path):
            return {"status": "error", "msg": "File patch not permitted"}
        if not os.path.exists(path):
            return {"status": "error", "msg": "File not found"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            count = text.count(old_content)
            if count == 0:
                return {"status": "error", "msg": "old_content not found"}
            if count > 1:
                return {"status": "error", "msg": f"Found {count} matches — must be unique"}

            is_oaa = self._is_oaa_path(path)
            backup_path = ""
            if is_oaa:
                backup_path = self._backup_file(path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old_content, new_content))

            if is_oaa:
                self._record_change(path, "file_patch: replaced unique text", backup_path)
                self._record_rollback_entry(path, "file_patch", backup_path)
                self._clear_pycache(path)

            logger.info("file_patch: %s", os.path.basename(path))
            return {"status": "success"}
        except Exception as exc:
            logger.error("file_patch failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="ask_user",
        description="Interrupt for user input or decision"
    )
    async def do_ask_user(self, question: str, candidates: list = []) -> dict:
        """Interrupt for user input."""
        return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
                "data": {"question": question, "candidates": candidates or []}}

    # ------------------------------------------------------------------
    # B3: modify own prompt
    # ------------------------------------------------------------------

    _PROMPT_SECTIONS = frozenset({"identity", "soul", "user", "agents", "bootstrap"})

    @agent_tool(
        name="modify_own_prompt",
        description="Read or modify your own system prompt sections. Sections: identity (自我介绍/人格设定), "
                    "soul (工作哲学/原则), user (用户信息), agents (工作边界/规则), bootstrap (启动自我介绍). "
                    "Use 'list' action to see available sections, 'read' to view a section, "
                    "'write' to replace a section's content."
    )
    async def do_modify_own_prompt(self, args: dict) -> dict:
        """Read or modify your own system prompt sections.

        Sections:
          identity  — 自我介绍 / 人格设定
          soul      — 工作哲学 / 原则
          user      — 用户信息
          agents    — 工作边界 / 规则
          bootstrap — 启动自我介绍

        Actions:
          list              — show available sections and their line counts
          read <section>    — show full content of a section
          write <section>   — replace a section's content
        """
        action = args.get("action", "list")
        section = args.get("section", "")

        memory_dir = os.path.join(self.data_dir, "memory")

        def _read_file(p: str) -> str:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()

        if action == "list":
            result = {}
            for sec in sorted(self._PROMPT_SECTIONS):
                path = os.path.join(memory_dir, f"{sec.upper()}.md")
                if os.path.exists(path):
                    text = _read_file(path)
                    lines = len(text.strip().split("\n"))
                    preview = text.strip().split("\n")[0] if text.strip() else "(empty)"
                    result[sec] = {"lines": lines, "preview": preview[:80]}
                else:
                    result[sec] = {"lines": 0, "preview": "(not found)"}
            return {"status": "success", "sections": result}

        if action == "read":
            if section not in self._PROMPT_SECTIONS:
                return {"status": "error", "msg": f"Unknown section '{section}'. Available: {', '.join(sorted(self._PROMPT_SECTIONS))}"}
            path = os.path.join(memory_dir, f"{section.upper()}.md")
            if not os.path.exists(path):
                return {"status": "error", "msg": f"Section file not found: {path}"}
            content = _read_file(path)
            return {"status": "success", "section": section, "content": content, "lines": len(content.strip().split("\n"))}

        if action == "write":
            if section not in self._PROMPT_SECTIONS:
                return {"status": "error", "msg": f"Unknown section '{section}'. Available: {', '.join(sorted(self._PROMPT_SECTIONS))}"}
            content = args.get("content", "")
            if not content.strip():
                return {"status": "error", "msg": "Content is required"}
            path = os.path.join(memory_dir, f"{section.upper()}.md")
            # Backup current
            if os.path.exists(path):
                backup_dir = os.path.join(self.data_dir, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(backup_dir, f"{section.upper()}.md.bak")
                import shutil
                shutil.copy2(path, backup_path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content.strip() + "\n")
            logger.info("modify_own_prompt: section=%s lines=%d", section, len(content.strip().split("\n")))
            return {"status": "success", "section": section, "lines": len(content.strip().split("\n"))}

        return {"status": "error", "msg": f"Unknown action '{action}'. Use list, read, or write."}

    # ------------------------------------------------------------------
    # B4: self_improve — atomic self-modification with verify & rollback
    # ------------------------------------------------------------------

    async def _do_reload(self, rel_path: str) -> str:
        """Reload a Python module by its relative path (e.g. 'oaa/agent/tools.py')."""
        mod_name = rel_path.replace("/", ".").replace("\\", ".").replace(".py", "")
        try:
            import importlib
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                return f"重载 {mod_name} 成功"
            return f"{mod_name} 未加载，无需重载"
        except Exception as exc:
            return f"重载失败: {exc}"

    async def do_self_improve(self, args: dict) -> dict:
        """Apply a self-modification with verification and automatic rollback.

        Safely patches your own source code: backs up the target file, applies
        the change, runs an optional verification command, and either commits
        (clear pycache + reload + changelog) or rolls back on failure.

        Args:
            path: File path relative to OAA root, e.g. 'oaa/agent/tools.py'
            old_content: Exact unique text to replace
            new_content: Replacement text
            verify: Optional shell command to verify the change (e.g. 'python -m pytest tests/test_tools.py -x')
            description: Summary of the change for the changelog
        """
        path = args.get("path", "")
        old = args.get("old_content", "")
        new = args.get("new_content", "")
        verify_cmd = args.get("verify", "")
        description = args.get("description", "")

        if not path or not old or new is None:
            return {"status": "error", "msg": "path, old_content, and new_content are required"}

        full_path = os.path.normpath(os.path.join(OAA_ROOT, path))
        if not full_path.startswith(OAA_ROOT):
            return {"status": "error", "msg": "Path must be within OAA project root"}

        if not os.path.exists(full_path):
            return {"status": "error", "msg": f"File not found: {path}"}

        # Permission check
        if not await self._confirm("self_improve", f"{path}: replace unique text"):
            return {"status": "error", "msg": "Self-improvement not permitted"}

        # Backup
        backup_path = self._backup_file(full_path)
        if not backup_path:
            return {"status": "error", "msg": "Backup failed — aborting"}

        # Read & validate uniqueness
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as exc:
            return {"status": "error", "msg": f"Failed to read {path}: {exc}"}

        count = text.count(old)
        if count == 0:
            return {"status": "error", "msg": "old_content not found in file"}
        if count > 1:
            return {"status": "error", "msg": f"Found {count} matches — old_content must be unique"}

        # Apply
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))
        except Exception as exc:
            self._restore_backup(full_path, backup_path)
            return {"status": "error", "msg": f"Write failed: {exc}"}

        # Verification (if command provided)
        if verify_cmd:
            if not await self._confirm("shell_run", f"self_improve verify: {verify_cmd[:200]}"):
                self._restore_backup(full_path, backup_path)
                return {"status": "error", "msg": "Verify command not permitted"}
            import subprocess
            try:
                proc = await asyncio.create_subprocess_shell(
                    verify_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode != 0:
                    # Rollback
                    self._restore_backup(full_path, backup_path)
                    out = stdout.decode("utf-8", errors="replace")[:2000]
                    err = stderr.decode("utf-8", errors="replace")[:2000]
                    return {
                        "status": "error",
                        "msg": f"Verification failed (exit {proc.returncode}) — rolled back",
                        "verify_stdout": out,
                        "verify_stderr": err,
                    }
            except asyncio.TimeoutError:
                self._restore_backup(full_path, backup_path)
                return {"status": "error", "msg": "Verification timed out (120s) — rolled back"}
            except Exception as exc:
                self._restore_backup(full_path, backup_path)
                return {"status": "error", "msg": f"Verification error: {exc}"}

        # Auto-verify Python files: syntax check if no explicit verify was given
        if not verify_cmd and full_path.endswith(".py"):
            try:
                with open(full_path, "r", encoding="utf-8") as _f:
                    ast.parse(_f.read())
            except SyntaxError as _syn:
                self._restore_backup(full_path, backup_path)
                return {
                    "status": "error",
                    "msg": f"Python 语法错误 (line {_syn.lineno}): {_syn.msg} — 已回滚",
                    "syntax_error": _syn.msg,
                    "line": _syn.lineno,
                }

        # Success: clear pycache, reload, record
        self._clear_pycache(full_path)
        rel_path = os.path.relpath(full_path, OAA_ROOT)
        reload_msg = await self._do_reload(rel_path)
        desc = description or f"self_improve: replaced unique text in {path}"
        self._record_change(full_path, desc, backup_path)
        self._record_rollback_entry(full_path, desc, backup_path)
        return {
            "status": "success",
            "msg": f"Applied to {path} and verified successfully. {reload_msg}",
            "backup": backup_path,
        }

    def _restore_backup(self, filepath: str, backup_path: str):
        """Restore a file from backup."""
        try:
            import shutil
            shutil.copy2(backup_path, filepath)
            logger.info("Rolled back %s from %s", filepath, backup_path)
        except Exception as exc:
            logger.error("Rollback failed for %s: %s", filepath, exc)

    @agent_tool(
        name="update_working_checkpoint",
        description="Save a user preference, rule, or key fact to persistent HOT memory. Automatically compacted when full. Survives across restarts and sessions."
    )
    async def do_update_working_checkpoint(self, key_info: str) -> dict:
        """Save key info to working memory (survives across restarts). Uses
        tiered HOT memory — entries are automatically compacted when HOT
        exceeds 100 lines.  Call this when you learn something about the
        user's preferences, rules, or workflow patterns."""
        if not key_info:
            return {"status": "error", "msg": "key_info is required"}

        if self._memory_mgr:
            return await self._memory_mgr.add_to_hot(key_info)

        # Fallback: direct file write
        self.working_memory["key_info"] = key_info
        try:
            memory_dir = os.path.join(self.data_dir, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            path = os.path.join(memory_dir, "WORKING_CHECKPOINT.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(key_info)
        except Exception:
            pass
        return {"status": "ok"}

    @agent_tool(
        name="correction_log",
        description="Log a user correction so the model remembers next time. Call when user says '不对', '不是', '你错了', '我告诉过你', or otherwise corrects you."
    )
    async def do_correction_log(self, context: str, lesson: str) -> dict:
        """Log a user correction so the agent remembers next time.

        Call this when the user explicitly corrects you:
        - "不对，应该是..."
        - "你错了，..."
        - "不是这样，..."
        - "我告诉过你..."

        Also call this after self-reflection when you realize your own
        output could have been better.
        """
        if not context or not lesson:
            return {"status": "error", "msg": "context and lesson are required"}
        if self._memory_mgr:
            return self._memory_mgr.add_correction(context, lesson)
        return {"status": "error", "msg": "Memory manager not available"}

    @agent_tool(
        name="memory_recall",
        description="Search across all memory tiers (HOT + corrections + warm) for a keyword. Use when user asks '还记得吗', '我之前说过', or you need to find past learnings."
    )
    async def do_memory_recall(self, query: str) -> dict:
        """Search across all memory tiers (HOT, corrections, warm) for a keyword.

        Use when you need to find something you learned earlier,
        or when the user asks "还记得...", "我之前说过...".
        """
        if not query:
            return {"status": "error", "msg": "query is required"}
        if self._memory_mgr:
            return self._memory_mgr.search(query)
        return {"status": "error", "msg": "Memory manager not available"}

    @agent_tool(
        name="chat_history_search",
        description="Search past conversation summaries by keyword. Use when user asks about previous discussions ('我们之前聊过什么', '上次那个客户', '我记得说过...'). Returns structured summaries of past sessions sorted by relevance."
    )
    async def do_chat_history_search(self, query: str, limit: int = 10) -> dict:
        """Search archived conversation summaries for a keyword.

        Searches structured summaries (user goals, key info, completed items, open issues)
        stored across past sessions. Results are ordered by relevance score.

        Args:
            query: Keyword or phrase to search for.
            limit: Maximum number of results (default 10, max 30).
        """
        if not query:
            return {"status": "error", "msg": "query is required"}
        if not self._archiver:
            return {"status": "error", "msg": "对话归档模块未就绪"}
        limit = min(limit, 30)
        matches = self._archiver.search(query, limit=limit)
        return {"status": "success", "query": query, "matches": matches, "total": len(matches)}

    @agent_tool(
        name="self_reflect",
        description="After completing significant work, reflect on what went well and what could be improved. Call at the end of multi-step tasks to log lessons learned."
    )
    async def do_self_reflect(self, context: str, reflection: str, lesson: str = "") -> dict:
        """After completing significant work, reflect and learn.

        Call this at the end of a multi-step task to log what went well
        and what could be improved next time.
        """
        if not context or not reflection:
            return {"status": "error", "msg": "context and reflection are required"}
        msg = f"[Self-reflection] {context}: {reflection}"
        if lesson:
            msg += f" → Lesson: {lesson}"
        if self._memory_mgr:
            await self._memory_mgr.add_to_hot(msg)
        return {"status": "success", "msg": "Reflection saved"}

    # --- WeChat CLI tools (proxy to gateway.adapters.wechat_cli.WeChatCLI) ---

    async def _wechat_cli_call(self, method: str, **kwargs) -> dict:
        """Call a WeChatCLI method, returning a standard result dict."""
        try:
            from ..gateway.adapters.wechat_cli import WeChatCLI
            cli = WeChatCLI(cli_path=self._wechat_cli_path)
            fn = getattr(cli, method, None)
            if fn is None:
                return {"status": "error", "msg": f"WeChatCLI 没有方法: {method}"}
            result = await fn(**kwargs)
            if isinstance(result, str) and result.startswith("Error:"):
                return {"status": "error", "msg": result[6:].strip()}
            return {"status": "success", "data": result}
        except FileNotFoundError:
            return {"status": "error", "msg": "wechat-cli 未安装。请先安装 wechat-cli 或设置正确的路径。"}
        except ImportError:
            return {"status": "error", "msg": "WeChatCLI 模块不可用"}
        except Exception as e:
            return {"status": "error", "msg": f"wechat-cli 调用失败: {e}"}

    async def do_wechat_sessions(self, args: dict) -> dict:
        return await self._wechat_cli_call("sessions", limit=args.get("limit", 20))

    async def do_wechat_history(self, args: dict) -> dict:
        return await self._wechat_cli_call("history", chat_name=args.get("name", ""), limit=args.get("limit", 20))

    async def do_wechat_search(self, args: dict) -> dict:
        return await self._wechat_cli_call("search", keyword=args.get("keyword", ""), chat=args.get("chat", ""), limit=args.get("limit", 20))

    async def do_wechat_contacts(self, args: dict) -> dict:
        return await self._wechat_cli_call("contacts", query=args.get("query", ""))

    async def do_wechat_unread(self, args: dict) -> dict:
        return await self._wechat_cli_call("unread", limit=args.get("limit", 20))

    @agent_tool(
        name="shell_run",
        description="Execute an arbitrary shell command. Use this when you need to run CLI tools, scripts, or any system command. For Python/PowerShell code use 'code_run' instead."
    )
    async def do_shell_run(self, command: str, timeout: int = 60, cwd: str = "") -> dict:
        """Execute an arbitrary shell command."""
        if not command:
            return {"status": "error", "msg": "No command provided"}
        if not await self._confirm("shell_run", command[:200]):
            return {"status": "error", "msg": "Shell execution not permitted"}
        timeout = min(timeout, 300)
        cwd_resolved = self._resolve_path(cwd) if cwd else None

        logger.info("shell_run: command=%.200s timeout=%s cwd=%s", command, timeout, cwd_resolved)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd_resolved,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("shell_run timeout after %ss", timeout)
                return {"status": "error", "msg": f"Timeout after {timeout}s"}
            out = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""
            logger.info("shell_run exit_code=%s stdout_len=%s stderr_len=%s",
                        proc.returncode, len(out), len(err))
            return {
                "status": "success" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": out[:50000],
                "stderr": err[:10000],
            }
        except Exception as exc:
            logger.error("shell_run failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="read_own_source",
        description="Read OAA source code files. Use when you need to understand or debug your own implementation. Provide a file path or a glob pattern."
    )
    async def do_read_own_source(self, path: str = "", pattern: str = "", start_line: int = 1, line_count: int = 200) -> dict:
        """Read OAA source code files, restricted to the project root."""

        if not path and not pattern:
            return {"status": "error", "msg": "path or pattern required"}

        if pattern:
            import glob
            full_pattern = os.path.normpath(os.path.join(OAA_ROOT, pattern))
            if not full_pattern.startswith(os.path.normpath(OAA_ROOT)):
                return {"status": "error", "msg": "Pattern outside OAA project root"}
            matches = [m for m in glob.glob(full_pattern, recursive=True) if os.path.isfile(m)]
            if not matches:
                return {"status": "error", "msg": f"No files matching '{pattern}'"}
            matches = matches[:10]
            results = {}
            for fp in matches:
                rel = os.path.relpath(fp, OAA_ROOT)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        results[rel] = f.read(50000)
                except Exception as e:
                    results[rel] = f"[error: {e}]"
            return {"status": "success", "files": results}

        full_path = os.path.normpath(os.path.join(OAA_ROOT, path))
        if not full_path.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}
        if not os.path.exists(full_path):
            return {"status": "error", "msg": f"Path not found: {path}"}
        if os.path.isdir(full_path):
            import glob as _glob
            rel_path = os.path.relpath(full_path, OAA_ROOT)
            items = []
            for entry in sorted(os.listdir(full_path)):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    items.append(f"[dir]  {entry}/")
                else:
                    size = os.path.getsize(entry_path)
                    items.append(f"[file] {entry}  ({size} bytes)")
            listing = "\n".join(items)
            try:
                from .memory_manager import MemoryManager
                mm = MemoryManager(os.path.join(os.path.dirname(OAA_ROOT), "memory"))
                mm.add_correction(
                    context="read_own_source called with a directory path",
                    lesson="read_own_source 只能读取文件。要浏览目录结构请使用 list_own_structure 工具。"
                )
            except Exception:
                pass
            return {
                "status": "success",
                "path": rel_path,
                "is_directory": True,
                "content": f"📁 {rel_path}/\n\n{listing}",
                "hint": "这是目录，不是文件。浏览目录结构请用 list_own_structure，读取文件内容才用 read_own_source。",
            }

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start = max(0, start_line - 1)
        selected = lines[start:start + line_count]
        content = "".join(selected)

        return {
            "status": "success",
            "path": path,
            "content": content,
            "total_lines": total_lines,
            "start_line": start_line,
            "line_count": len(selected),
        }

    @agent_tool(
        name="list_own_structure",
        description="List OAA project directory structure. Use to discover where tools, skills, and config files are located."
    )
    async def do_list_own_structure(self, path: str = "", depth: int = 2) -> dict:
        """List OAA project directory structure."""
        subpath = path
        depth = min(depth, 4)

        target_dir = os.path.normpath(os.path.join(OAA_ROOT, subpath))
        if not target_dir.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}
        if not os.path.isdir(target_dir):
            return {"status": "error", "msg": f"Directory not found: {subpath}"}

        tree_lines = []
        rel_root = os.path.relpath(target_dir, OAA_ROOT)
        if rel_root == ".":
            tree_lines.append("oaa/")
        else:
            tree_lines.append(f"oaa/{rel_root}/")

        for root, dirs, files in os.walk(target_dir):
            rel = os.path.relpath(root, OAA_ROOT)
            current_depth = rel.count(os.sep)
            if rel_root != ".":
                current_depth -= rel_root.count(os.sep)
            if current_depth >= depth:
                dirs[:] = []
                continue
            indent = "  " * (current_depth + 1)
            for d in sorted(dirs):
                tree_lines.append(f"{indent}{d}/")
            for f in sorted(files):
                tree_lines.append(f"{indent}{f}")

        return {
            "status": "success",
            "path": subpath or ".",
            "tree": "\n".join(tree_lines),
        }

    @agent_tool(
        name="reload_module",
        description="Reload a Python module after source changes. Clears __pycache__ and re-imports. Only works for non-core modules (tools, extended_tools, adapter files). Core module changes (loop, handler, oaa_agent) require a restart."
    )
    async def do_reload_module(self, module: str) -> dict:
        """Reload a non-core Python module after source changes.

        Clears ``__pycache__`` then attempts ``importlib.reload()``.
        Core modules (loop, handler, oaa_agent) require a full restart.
        """
        module_path = module
        if not module_path:
            return {"status": "error", "msg": "module is required"}

        full_path = os.path.normpath(os.path.join(OAA_ROOT, module_path))
        if not full_path.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Module path outside OAA project root"}

        # Normalise to dotted module name
        rel = os.path.splitext(os.path.relpath(full_path, OAA_ROOT))[0]
        mod_name = rel.replace(os.sep, ".")

        # Block core module reloads
        core_modules = {"oaa.agent.loop", "oaa.agent.handler", "oaa.agent.oaa_agent",
                        "oaa.agent.tool_schema", "oaa.app"}
        if mod_name in core_modules:
            return {"status": "error", "msg": f"核心模块 {mod_name} 修改后需要重启进程才能生效。请重启 OAA。"}

        # Clear pycache
        pycache_dir = os.path.join(os.path.dirname(full_path), "__pycache__")
        base = os.path.splitext(os.path.basename(full_path))[0]
        if os.path.isdir(pycache_dir):
            for fname in os.listdir(pycache_dir):
                if fname.startswith(base + ".") and fname.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(pycache_dir, fname))
                    except OSError:
                        pass

        # Try reload
        try:
            import importlib
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                # If the module has a handler class, also refresh tool schemas
                logger.info("Module reloaded: %s", mod_name)
                return {"status": "success", "msg": f"{mod_name} 已重载"}
            else:
                importlib.import_module(mod_name)
                logger.info("Module loaded: %s", mod_name)
                return {"status": "success", "msg": f"{mod_name} 已加载"}
        except Exception as exc:
            logger.error("Failed to reload %s: %s", mod_name, exc)
            return {"status": "error", "msg": f"重载 {mod_name} 失败: {exc}"}

    @agent_tool(
        name="rollback_change",
        description="List or apply a rollback of a previous self-modification. Call with no arguments to list recent changes with indexes. Call with an index to roll back a specific change."
    )
    async def do_rollback_change(self, index: int = -1) -> dict:
        """List recent self-modifications or roll back a specific change."""
        idx = index if index != -1 else None

        changelog_path = os.path.join(self.data_dir, _OAA_BACKUP_DIR, "changelog.md")
        backup_dir = os.path.join(self.data_dir, _OAA_BACKUP_DIR)

        if not os.path.exists(changelog_path):
            return {"status": "error", "msg": "暂无修改记录"}

        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as exc:
            return {"status": "error", "msg": f"读取修改记录失败: {exc}"}

        # Parse changelog entries
        entries = []
        for block in text.split("\n## "):
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            entry = {"timestamp": lines[0].strip(), "file": "", "change": "", "backup": "", "status": ""}
            for l in lines:
                if l.startswith("- **file**"):
                    entry["file"] = l.split(":", 1)[-1].strip()
                elif l.startswith("- **change**"):
                    entry["change"] = l.split(":", 1)[-1].strip()
                elif l.startswith("- **backup**"):
                    entry["backup"] = l.split(":", 1)[-1].strip()
                elif l.startswith("- **status**"):
                    entry["status"] = l.split(":", 1)[-1].strip()
            entries.append(entry)

        # List mode
        if idx is None:
            if not entries:
                return {"status": "success", "msg": "暂无修改记录"}
            lines = ["# 自修改记录\n"]
            for i, e in enumerate(reversed(entries[-20:])):
                status_tag = " ✅" if e["status"] == "active" else " ↩️"
                lines.append(f"{len(entries) - i}. [{e['timestamp']}]{status_tag} {e['file']} — {e['change']}")
            return {"status": "success", "content": "\n".join(lines)}

        # Rollback mode
        if idx < 1 or idx > len(entries):
            return {"status": "error", "msg": f"无效索引 {idx}，有效范围 1-{len(entries)}"}

        target = entries[idx - 1]
        if target["status"] != "active":
            return {"status": "error", "msg": f"变更 #{idx} 已被回滚或状态异常"}

        backup_rel = target["backup"]
        if backup_rel == "none" or not backup_rel:
            return {"status": "error", "msg": f"变更 #{idx} 没有备份文件，无法回滚"}

        backup_path = os.path.normpath(os.path.join(backup_dir, backup_rel))
        if not os.path.exists(backup_path):
            return {"status": "error", "msg": f"备份文件不存在: {backup_path}"}

        # Restore backup
        src_path = os.path.normpath(os.path.join(OAA_ROOT, target["file"]))
        try:
            import shutil
            shutil.copy2(backup_path, src_path)
            self._clear_pycache(src_path)
            # Reload module so rollback takes effect immediately
            rel = os.path.relpath(src_path, OAA_ROOT)
            reload_msg = await self._do_reload(rel)
            # Mark as rolled back in changelog
            self._record_change(src_path, f"回滚变更 #{idx}: {target['change']}", "")

            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            # Rebuild changelog with updated status
            target["status"] = f"rolled-back at {now}"
            rebuild = []
            for e in entries:
                rebuild.append(
                    f"## {e['timestamp']}\n"
                    f"- **file**: {e['file']}\n"
                    f"- **change**: {e['change']}\n"
                    f"- **backup**: {e['backup']}\n"
                    f"- **status**: {e['status']}\n"
                )
            with open(changelog_path, "w", encoding="utf-8") as f:
                f.write("".join(rebuild))

            return {"status": "success", "msg": f"已回滚变更 #{idx} ({target['file']})"}
        except Exception as exc:
            logger.error("Rollback failed: %s", exc)
            return {"status": "error", "msg": f"回滚失败: {exc}"}

    # ------------------------------------------------------------------
    # Code review & project navigation tools
    # ------------------------------------------------------------------

    @agent_tool(
        name="code_search",
        description="Search across all project files for a text pattern or regex. Results include file paths and matching lines with line numbers. Use for finding where functions are defined, tracing references, or auditing code patterns."
    )
    async def do_code_search(self, pattern: str, path: str = "", include: str = "", max_results: int = 50) -> dict:
        """Search across OAA project files for a text pattern or regex."""
        search_root = os.path.join(OAA_ROOT, path) if path else OAA_ROOT
        if not search_root.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}

        import subprocess as _sp
        cmd = ["grep", "-rn", "--color=never", "-I"]
        if include:
            for g in include.split(","):
                cmd.extend(["--include", g.strip()])
        cmd.extend([pattern, search_root])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace") if stdout else ""

            lines = [l for l in out.split("\n") if l.strip()]
            total = len(lines)

            # Win32: use findstr if grep unavailable
            if total == 0 and proc.returncode != 0:
                cmd2 = ["findstr", "/S", "/N", "/I"]
                if include:
                    for g in include.split(","):
                        cmd2.extend(["/D:" + g.strip().replace("*", "")])
                cmd2.extend([pattern, search_root + "\\*" if path else OAA_ROOT + "\\*"])
                proc2 = await asyncio.create_subprocess_exec(
                    *cmd2,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)
                out2 = stdout2.decode("utf-8", errors="replace") if stdout2 else ""
                lines = [l for l in out2.split("\n") if l.strip()][:max_results]
                total = len(lines)
                return {"status": "success" if total > 0 else "empty",
                        "results": lines, "total": total, "pattern": pattern, "path": path or "."}

            lines = lines[:max_results]
            return {"status": "success" if total > 0 else "empty",
                    "results": lines, "total": min(total, max_results),
                    "truncated": total > max_results, "pattern": pattern, "path": path or "."}
        except asyncio.TimeoutError:
            return {"status": "error", "msg": "Search timed out after 30s"}
        except Exception as exc:
            logger.error("code_search failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="file_glob",
        description="List files matching a glob pattern. Use for discovering project structure, finding files by extension or name pattern. Supports recursive patterns like '**/*.py'."
    )
    async def do_file_glob(self, pattern: str, path: str = "") -> dict:
        """List files matching a glob pattern within the OAA project."""
        import glob as _glob
        search_root = os.path.join(OAA_ROOT, path) if path else OAA_ROOT
        if not search_root.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}

        full_pattern = os.path.normpath(os.path.join(search_root, pattern))
        matches = [m for m in _glob.glob(full_pattern, recursive=True) if os.path.isfile(m)]
        matches = matches[:200]  # cap at 200 files

        rel_matches = [os.path.relpath(m, OAA_ROOT) for m in matches]
        return {
            "status": "success" if rel_matches else "empty",
            "files": rel_matches,
            "total": len(rel_matches),
            "truncated": len(matches) >= 200,
        }

    @agent_tool(
        name="git_status",
        description="Show git working tree status: modified, staged, untracked files. Use to understand what's changed before code review."
    )
    async def do_git_status(self) -> dict:
        """Show git working tree status."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--short",
                cwd=OAA_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""
            if proc.returncode != 0:
                return {"status": "error", "msg": f"git status failed: {err[:1000]}"}
            lines = [l for l in out.split("\n") if l.strip()]
            return {"status": "success", "changes": lines, "total": len(lines)}
        except asyncio.TimeoutError:
            return {"status": "error", "msg": "git status timed out"}
        except FileNotFoundError:
            return {"status": "error", "msg": "git not found in PATH"}
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="git_diff",
        description="Show git diff of unstaged changes. Use with staged=true to see staged diff, or provide two refs to compare branches/commits."
    )
    async def do_git_diff(self, staged: bool = False, ref1: str = "", ref2: str = "") -> dict:
        """Show git diff — unstaged, staged, or between two refs."""
        try:
            args = ["git", "diff", "--no-color"]
            if ref1 and ref2:
                args.append(f"{ref1}...{ref2}")
            elif ref1:
                args.append(ref1)
            elif staged:
                args.append("--staged")

            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=OAA_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""
            if proc.returncode != 0:
                return {"status": "error", "msg": f"git diff failed: {err[:1000]}"}

            diff_sections = []
            current = []
            for line in out.split("\n"):
                if line.startswith("diff --git"):
                    if current:
                        diff_sections.append("\n".join(current))
                    current = [line]
                else:
                    current.append(line)
            if current:
                diff_sections.append("\n".join(current))

            # Truncate to avoid blowing the context
            max_sections = 15
            total_files = len(diff_sections)
            truncated = diff_sections[:max_sections]
            total_chars = sum(len(s) for s in truncated)

            return {
                "status": "success",
                "files_changed": total_files,
                "diffs": truncated,
                "total_chars": total_chars,
                "truncated": total_files > max_sections,
            }
        except asyncio.TimeoutError:
            return {"status": "error", "msg": "git diff timed out"}
        except FileNotFoundError:
            return {"status": "error", "msg": "git not found in PATH"}
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="git_log",
        description="Show recent git commit history. Use to understand what changes have been made and by whom. Supports limiting count and showing a specific file's history."
    )
    async def do_git_log(self, count: int = 10, file_path: str = "", branch: str = "") -> dict:
        """Show recent git commit history."""
        try:
            args = ["git", "log", f"--max-count={min(count, 50)}", "--format=format:%h %ai %an%n%s%n"]
            if branch:
                args.append(branch)
            if file_path:
                args.append("--", file_path)

            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=OAA_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""
            if proc.returncode != 0:
                return {"status": "error", "msg": f"git log failed: {err[:1000]}"}

            entries = [e.strip() for e in out.split("\n\n") if e.strip()]
            return {"status": "success", "entries": entries, "total": len(entries)}
        except asyncio.TimeoutError:
            return {"status": "error", "msg": "git log timed out"}
        except FileNotFoundError:
            return {"status": "error", "msg": "git not found in PATH"}
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    @agent_tool(
        name="health_diagnose",
        description="Comprehensive health diagnosis: checks WebSocket port 9765, process status, memory, CPU, recent tool failures, and data directory state. Run this when user reports app issues like '页面不见了', '技能加载不出来', or general instability."
    )
    async def do_health_diagnose(self) -> dict:
        """Run comprehensive health checks — process, port, errors, disk."""
        import datetime as _dt
        pid = os.getpid()
        checks = {}

        # 1. Process health
        checks["pid"] = pid
        try:
            import psutil
            proc = psutil.Process(pid)
            create_time = proc.create_time()
            checks["uptime_sec"] = int(_dt.datetime.now().timestamp() - create_time)
            checks["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
            checks["cpu_percent"] = proc.cpu_percent(interval=0.1)
            checks["num_threads"] = proc.num_threads()
            checks["open_files"] = len(proc.open_files())
            checks["connections"] = len(proc.connections())
            checks["process_status"] = "running"
        except ImportError:
            checks["process_status"] = "running (psutil unavailable)"
        except Exception as exc:
            checks["process_status"] = f"error: {exc}"

        # 2. WebSocket port 9765 check
        try:
            import subprocess as _sp
            if os.name == "nt":
                r = await asyncio.create_subprocess_exec(
                    "netstat", "-ano",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await asyncio.wait_for(r.communicate(), timeout=10)
                text = out.decode("utf-8", errors="replace") if out else ""
                ws_listen = [l for l in text.split("\n") if "9765" in l and "LISTENING" in l]
                ws_estab = [l for l in text.split("\n") if "9765" in l and "ESTABLISHED" in l]
                checks["ws_port_9765"] = {
                    "listening": len(ws_listen) > 0,
                    "connections": len(ws_estab),
                }
            else:
                r = await asyncio.create_subprocess_exec(
                    "ss", "-tlnp", "sport", "=:9765",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await asyncio.wait_for(r.communicate(), timeout=10)
                text = out.decode("utf-8", errors="replace") if out else ""
                checks["ws_port_9765"] = {"listening": "LISTEN" in text}
        except Exception as exc:
            checks["ws_port_9765"] = {"error": str(exc)}

        # 3. Tool failure count (recent)
        try:
            failures = self._memory_mgr.count_tool_failures() if self._memory_mgr else {"total": 0}
            checks["recent_tool_failures"] = failures.get("total", 0)
            checks["failures_by_tool"] = failures.get("by_tool", {})
        except Exception:
            checks["recent_tool_failures"] = -1

        # 4. Data directory
        try:
            data_dir = self.data_dir
            config_path = os.path.join(data_dir, "config.json")
            skills_dir = os.path.join(data_dir, "skills")
            checks["data_dir"] = {
                "path": data_dir,
                "exists": os.path.isdir(data_dir),
                "config_exists": os.path.isfile(config_path),
                "skills_count": len(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else 0,
                "disk_free_gb": round(shutil.disk_usage(data_dir).free / (1024**3), 1) if hasattr(shutil, 'disk_usage') else "unknown",
            }
        except Exception as exc:
            checks["data_dir"] = {"error": str(exc)}

        return {"status": "success", **checks}

    @agent_tool(
        name="check_self_process",
        description="Check if the OAA application process is currently running. Returns PID, status, and uptime. Use this when user says the app is not running to verify before responding."
    )
    async def do_check_self_process(self) -> dict:
        """Check if OAA process itself is alive and return runtime info."""
        import datetime as _dt
        pid = os.getpid()
        try:
            import psutil
            proc = psutil.Process(pid)
            create_time = proc.create_time()
            uptime_sec = int(_dt.datetime.now().timestamp() - create_time)
            mem_mb = proc.memory_info().rss / 1024 / 1024
            cpu_pct = proc.cpu_percent(interval=0.1)
            return {
                "status": "success",
                "alive": True,
                "pid": pid,
                "uptime_sec": uptime_sec,
                "memory_mb": round(mem_mb, 1),
                "cpu_percent": cpu_pct,
                "cmdline": " ".join(proc.cmdline())[:200],
            }
        except ImportError:
            # fallback without psutil
            return {
                "status": "success",
                "alive": True,
                "pid": pid,
                "note": "psutil not available, limited info",
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc), "pid": pid, "alive": True}

    # ------------------------------------------------------------------
    # Proposal management tools (self-healing closed loop)
    # ------------------------------------------------------------------

    @agent_tool(
        name="proposal_list",
        description="List pending self-improvement proposals. Each proposal has an ID, type (tool_fix/install_dep/sop_optimize/skill_crystallize), description, and executable actions. Call this to see what improvements are waiting for approval."
    )
    async def do_proposal_list(self, include_history: bool = False) -> dict:
        """List pending or all proposals from the store."""
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
        """Approve a proposal and execute its action sequence."""
        if not self._proposal_store:
            return {"status": "error", "msg": "提案系统未初始化"}
        proposal = self._proposal_store.get(id)
        if not proposal:
            return {"status": "error", "msg": f"未找到提案: {id}"}
        if proposal["status"] != "pending":
            return {"status": "error", "msg": f"提案 {id} 状态为 {proposal['status']}，不能执行"}

        from .proposal import ProposalExecutor
        executor = ProposalExecutor()
        # The handler is self (AtomicTools is a BaseHandler with dispatch)
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
        """Add *target* to the persistent ignore list for idle inspections."""
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

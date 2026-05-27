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

    # Patterns that indicate destructive or dangerous shell commands
    _DANGEROUS_SHELL_PATTERNS = [
        r"\brm\s+(-[rRf]+\s+)*[/~]",    # rm -rf / or rm -rf ~
        r"\bdd\s+if=",                    # dd disk overwrite
        r">\s*/dev/sd",                   # overwrite block device
        r"mkfs\.",                        # format filesystem
        r":\(\)\s*\{\s*:\|:&\s*\}\s*;",  # fork bomb
        r"\bchmod\s+(-R\s+)?777\s+/",    # chmod 777 on root
        r"\bcurl.*\|\s*(ba)?sh",         # curl|bash
        r"\bwget.*\|\s*(ba)?sh",         # wget|bash
        r"\beval\s",                      # eval injection
    ]

    _MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
    _BLOCKED_CONTENT_TYPES = {
        "application/x-msdownload", "application/x-msdos-program",
        "application/x-executable",
    }

    def __init__(self, data_dir: str, permissions: Optional[PermissionsManager] = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self.working_memory = {}
        self._memory_mgr: Optional["MemoryManager"] = None
        self._archiver: Optional["ConversationArchiver"] = None
        self._proposal_store = None
        self._idle_inspector = None
        self._scheduler = None
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

    def set_scheduler(self, scheduler):
        """Inject the TaskScheduler for schedule_create/list/update/delete."""
        self._scheduler = scheduler

    def set_tool_group_manager(self, agent):
        """Inject the OAAAgent for tool group load/unload."""
        self._agent_ref = agent

    def set_wechat_cli_path(self, path: str):
        """Set the path to wechat-cli binary for WeChat data tools."""
        self._wechat_cli_path = path

    def set_clone_manager(self, mgr):
        """Inject the CloneManager for safe self-modification."""
        self._clone_mgr = mgr

    def set_preferences_store(self, store):
        """Inject the PreferencesStore for structured user preferences."""
        self._prefs_store = store

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

    @staticmethod
    def _resolve_source_path(path: str) -> str | None:
        """Resolve a source path to an absolute filesystem path.

        Accepts:
        - File paths relative to OAA_ROOT (``oaa/app.py``)
        - Python module paths (``oaa.app`` -> ``oaa/app.py``)
        - Directories (``oaa/`` -> directory listing)
        - Duplicate-prefix paths (``oaa/oaa/app.py`` -> ``oaa/app.py``)

        Returns the absolute path, or ``None`` if nothing was found.
        """
        candidates = [os.path.normpath(os.path.join(OAA_ROOT, path))]
        orig_stripped = path.lstrip(".")

        # Python module path: oaa.app -> oaa/app.py
        if "." in orig_stripped and not orig_stripped.endswith(".py"):
            mod_path = orig_stripped.replace(".", "/") + ".py"
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, mod_path)))
            pkg_path = orig_stripped.replace(".", "/") + "/__init__.py"
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, pkg_path)))

        # Duplicate prefix: oaa/oaa/app.py -> oaa/app.py
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[0] == parts[1]:
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, "/".join(parts[1:]))))

        for c in candidates:
            if c.startswith(os.path.normpath(OAA_ROOT)) and os.path.exists(c):
                return c
        return None

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

    # code_run merged into code_exec — keep implementation for internal use
    # but remove from LLM tool list to eliminate the code_run vs code_exec choice.
    async def do_code_run(self, args_or_code, type: str = "python",
                           timeout: int = 15, cwd: str = "") -> dict:
        """Execute Python/PowerShell code within workspace restrictions.
        Accepts either a dict (legacy calling convention) or individual params."""
        if isinstance(args_or_code, dict):
            code = args_or_code.get("code", "")
            timeout = args_or_code.get("timeout", timeout)
            type = args_or_code.get("type", type)
            cwd = args_or_code.get("cwd", cwd)
        else:
            code = args_or_code
        return await self._do_code_run_subprocess(code, timeout, type, cwd)

    async def _do_code_run_subprocess(self, code: str, timeout: int = 15,
                                       type: str = "python", cwd: str = "") -> dict:
        """Run code in a sandboxed subprocess (legacy code_run path)."""
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
        description="Execute Python code for data processing, analysis, computation, or script automation. Use mode='sandbox' for untrusted code or when you only need stdout/stderr output. Use mode='exec' (default) when you need the Python 'result' variable back. Use async_mode=True for long-running background execution (returns task_id for status tracking). Blocks dangerous system operations (os.system, subprocess, etc.) — for system commands use 'shell_run' instead."
    )
    async def do_code_exec(self, code: str, timeout: int = 15, mode: str = "exec", async_mode: bool = False) -> dict:
        """Execute Python code in-process for agent self-extension.

        Allows most imports but blocks shell execution (os.system,
        subprocess.Popen, shutil.rmtree, etc.).  Uses the ``result``
        variable convention for return values.

        When *mode* is ``\"sandbox\"``, runs in a restricted subprocess
        (equivalent to the legacy ``code_run`` tool) — useful when the
        code is untrusted or only stdout/stderr output is needed.

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

        # Sandbox mode (legacy code_run): simple subprocess, no result variable
        if mode == "sandbox":
            return await self._do_code_run_subprocess(code, timeout)

        # Async mode: fire-and-forget background execution
        if async_mode:
            return await self._do_code_exec_async(code, timeout)

        # Exec mode (default): auto-correction + result variable
        original_code = code
        code, fix_desc = _fix_syntax_errors(code)
        all_fixes = [fix_desc] if fix_desc else []

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
        """Call a WeChatCLI method, returning a standard result dict.

        Rediscovers the binary on every call so that if the agent installed
        wechat-cli in the current session, the new binary is found without
        a config reload.  When a new path is discovered that differs from
        the configured path, the config is updated automatically.
        """
        try:
            from ..gateway.adapters.wechat_cli import WeChatCLI

            # Re-discover every call — catches newly-installed binaries
            cli = WeChatCLI(cli_path=self._wechat_cli_path)
            fn = getattr(cli, method, None)
            if fn is None:
                return {"status": "error", "msg": f"WeChatCLI 没有方法: {method}"}
            result = await fn(**kwargs)
            if isinstance(result, str) and result.startswith("Error:"):
                return {"status": "error", "msg": result[6:].strip()}

            # Auto-update config path when a new binary is discovered
            if cli._binary and cli._binary != self._wechat_cli_path:
                self._wechat_cli_path = cli._binary
                if hasattr(self, '_config') and self._config:
                    self._config.wechat.wechat_cli_path = cli._binary
                    await self._config.save()

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
        description="Execute an arbitrary shell command. Use this when you need to run CLI tools, scripts, or any system command. For Python/PowerShell code use 'code_exec' instead."
    )
    async def do_shell_run(self, command: str, timeout: int = 60, cwd: str = "") -> dict:
        """Execute an arbitrary shell command."""
        if not command:
            return {"status": "error", "msg": "No command provided"}
        import re
        for pat in self._DANGEROUS_SHELL_PATTERNS:
            if re.search(pat, command):
                return {"status": "error", "msg": f"拒绝执行危险命令（匹配模式: {pat}）"}
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
        description="Read OAA source code files. Accepts file paths (oaa/app.py), Python module paths (oaa.app), or glob patterns. Use when you need to understand or debug your own implementation."
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

        full_path = self._resolve_source_path(path)
        if full_path is None:
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
                asyncio.create_task(mm.add_correction(
                    context="read_own_source called with a directory path",
                    lesson="read_own_source 只能读取文件。要浏览目录结构请使用 list_own_structure 工具。"
                ))
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
            # Try fuzzy resolution (module path, duplicate prefix)
            resolved = self._resolve_source_path(subpath)
            if resolved and os.path.isdir(resolved):
                target_dir = resolved
            else:
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

    # ------------------------------------------------------------------
    # N4: async code_exec background execution
    # ------------------------------------------------------------------

    def _do_code_exec_async(self, code: str, timeout: int) -> dict:
        """Fire-and-forget code execution in a background asyncio task.

        Returns immediately with a task_id.  The agent can later check
        the result by reading the output file or via task status query.
        """
        task_id = f"async_{int(time.time() * 1000)}"
        output_dir = os.path.join(self.data_dir, "async_results")
        os.makedirs(output_dir, exist_ok=True)
        status_path = os.path.join(output_dir, f"{task_id}.json")

        async def _run_async():
            result = {"status": "running", "started": time.time(), "task_id": task_id}
            try:
                with open(status_path, "w", encoding="utf-8") as f:
                    json.dump(result, f)
                # Run in thread pool to avoid blocking event loop
                loop = asyncio.get_event_loop()
                # Use sandbox mode for background execution
                exec_result = await loop.run_in_executor(
                    None, self._run_code_exec_sync, code, timeout
                )
                result.update(exec_result)
                result["status"] = "done"
                result["finished"] = time.time()
            except Exception as exc:
                result["status"] = "error"
                result["error"] = str(exc)
            finally:
                with open(status_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

        asyncio.create_task(_run_async())
        return {
            "status": "success",
            "msg": f"代码已在后台开始执行（task_id: {task_id}）",
            "task_id": task_id,
            "result_path": status_path,
        }

    def _run_code_exec_sync(self, code: str, timeout: int) -> dict:
        """Synchronous wrapper for code_exec (used by async mode thread)."""
        import subprocess as _sp
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8", dir=self.data_dir
            ) as f:
                f.write(code)
                tmp_path = f.name
            result_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=self.data_dir)
            result_path = result_file.name
            result_file.close()
            cmd = [sys.executable, "-I", "-X", "utf8", "-u", _EXEC_RUNNER,
                   "--timeout", str(timeout), tmp_path, result_path]
            proc = _sp.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    return json.load(rf)
            return {"status": "success", "result": None, "stdout": proc.stdout, "stderr": proc.stderr}
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    # ------------------------------------------------------------------
    # N3: aifix — standalone auto-fix tool
    # ------------------------------------------------------------------

    @agent_tool(
        name="aifix",
        description="Auto-detect and fix common Python code errors. Analyzes the given code for syntax errors, missing imports, and other common AI-generated code issues. Returns the fixed code with a diff-like explanation. Use this when code_exec returns errors or before writing code to disk with self_improve."
    )
    async def do_aifix(self, code: str) -> dict:
        """Analyze and auto-fix common Python code errors."""
        if not code.strip():
            return {"status": "error", "msg": "No code provided"}

        original = code
        fixed = code
        fixes: list[str] = []

        # Stage 1: Syntax check + repair
        fixed, syn_fix = _fix_syntax_errors(fixed)
        if syn_fix:
            fixes.append(f"语法修复: {syn_fix}")

        # Stage 2: Compile check (catches NameError / missing imports)
        try:
            compile(fixed, "<aifix>", "exec")
        except SyntaxError as e:
            return {
                "status": "error",
                "msg": f"代码仍有语法错误无法自动修复: {e}",
                "original_code": original,
                "error_line": e.lineno,
                "error_text": e.msg,
            }
        except Exception as e:
            # Not a syntax error — try NameError fix
            fixed2, name_fix = _fix_name_error(fixed, str(e))
            if name_fix:
                fixed = fixed2
                fixes.append(f"导入补全: {name_fix}")

        # Stage 3: Re-validate after all fixes
        try:
            compile(fixed, "<aifix>", "exec")
        except Exception as e:
            if fixes:
                return {
                    "status": "partial",
                    "msg": f"部分修复后仍有编译错误: {e}",
                    "fixed_code": fixed,
                    "original_code": original,
                    "fixes_applied": fixes,
                }
            return {"status": "error", "msg": f"无法自动修复: {e}", "original_code": original}

        if not fixes:
            return {"status": "success", "msg": "代码检查通过，无需修复", "code": original}

        return {
            "status": "success",
            "msg": f"已自动修复 {len(fixes)} 处问题",
            "original_code": original,
            "fixed_code": fixed,
            "fixes_applied": fixes,
        }

    # ------------------------------------------------------------------
    # N5: file_download + github tools
    # ------------------------------------------------------------------

    @agent_tool(
        name="download_file",
        description="Download a file from a URL and save it locally. Returns the saved path and file size. Use for fetching assets, datasets, or any remote file that should be stored locally."
    )
    async def do_download_file(self, url: str, save_path: str = "") -> dict:
        """Download a file from *url*, optionally saving to *save_path*."""
        if not url:
            return {"status": "error", "msg": "No URL provided"}
        # Block non-HTTP schemes
        if not url.startswith(("http://", "https://")):
            return {"status": "error", "msg": f"不支持协议: {url.split('://')[0]}"}
        try:
            import requests as _req
            resp = _req.get(url, stream=True, timeout=60,
                          headers={"User-Agent": "OAA/1.0 (Windows; agent)"})
            resp.raise_for_status()

            # Size guard from Content-Length
            cl = resp.headers.get("Content-Length", "")
            if cl and cl.isdigit() and int(cl) > self._MAX_DOWNLOAD_SIZE:
                return {"status": "error", "msg": f"文件过大 ({int(cl)} 字节)，上限 {self._MAX_DOWNLOAD_SIZE} 字节"}

            # Content-type guard
            ct = resp.headers.get("Content-Type", "")
            if ct in self._BLOCKED_CONTENT_TYPES:
                return {"status": "error", "msg": f"拒绝下载可执行文件 ({ct})"}

            if not save_path:
                cd = resp.headers.get("Content-Disposition", "")
                fname = ""
                if "filename=" in cd:
                    import re as _re
                    m = _re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]*)', cd)
                    if m:
                        fname = m.group(1)
                if not fname:
                    fname = url.rsplit("/", 1)[-1].split("?")[0] or "download"
                save_path = os.path.join(self.data_dir, "workspace", fname)

            os.makedirs(os.path.dirname(save_path) or self.data_dir, exist_ok=True)
            total = 0
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    total += len(chunk)
                    if total > self._MAX_DOWNLOAD_SIZE:
                        return {"status": "error", "msg": f"下载超出大小上限 {self._MAX_DOWNLOAD_SIZE} 字节"}

            return {
                "status": "success",
                "msg": f"已下载 {total} 字节到 {save_path}",
                "path": save_path,
                "size": total,
                "content_type": ct or "unknown",
            }
        except Exception as exc:
            logger.error("download_file failed: %s", exc)
            return {"status": "error", "msg": f"下载失败: {exc}"}

    @agent_tool(
        name="github_repo",
        description="Look up a GitHub repository by owner/repo or URL. Returns repo metadata: description, stars, forks, language, topics, and clone URL. Use to evaluate open-source tools, libraries, or skills before installing them."
    )
    async def do_github_repo(self, query: str) -> dict:
        """Query GitHub API for repository information.

        *query* can be: ``owner/repo`` (e.g. ``codejunkie99/ztk``), a full
        GitHub URL, or a search keyword (returns first match).
        """
        if not query:
            return {"status": "error", "msg": "No query provided"}
        try:
            # Normalize query
            repo_path = query.strip()
            # Strip URL prefixes
            for prefix in ("https://github.com/", "github.com/"):
                if repo_path.startswith(prefix):
                    repo_path = repo_path[len(prefix):]
            repo_path = repo_path.rstrip("/")

            import requests as _req
            headers = {
                "User-Agent": "OAA/1.0",
                "Accept": "application/vnd.github+json",
            }

            if "/" in repo_path and repo_path.count("/") == 1:
                # Direct repo lookup
                url = f"https://api.github.com/repos/{repo_path}"
                resp = _req.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "status": "success",
                    "full_name": data.get("full_name"),
                    "description": data.get("description", ""),
                    "stars": data.get("stargazers_count", 0),
                    "forks": data.get("forks_count", 0),
                    "language": data.get("language", ""),
                    "topics": data.get("topics", []),
                    "clone_url": data.get("clone_url", ""),
                    "html_url": data.get("html_url", ""),
                    "open_issues": data.get("open_issues_count", 0),
                }
            else:
                # Search mode
                url = f"https://api.github.com/search/repositories?q={repo_path}&per_page=1"
                resp = _req.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                search_data = resp.json()
                items = search_data.get("items", [])
                if not items:
                    return {"status": "error", "msg": f"未找到匹配 '{query}' 的仓库"}
                data = items[0]
                return {
                    "status": "success",
                    "full_name": data.get("full_name"),
                    "description": data.get("description", ""),
                    "stars": data.get("stargazers_count", 0),
                    "forks": data.get("forks_count", 0),
                    "language": data.get("language", ""),
                    "topics": data.get("topics", []),
                    "clone_url": data.get("clone_url", ""),
                    "html_url": data.get("html_url", ""),
                }
        except Exception as exc:
            logger.error("github_repo failed: %s", exc)
            return {"status": "error", "msg": f"GitHub 查询失败: {exc}"}

    @agent_tool(
        name="github_content",
        description="Fetch a single file's content from a GitHub repository. Provide a GitHub file URL or 'owner/repo/path/to/file' format. Returns the file content (UTF-8 text). Use to read README, source code, or configuration files from GitHub repos without cloning."
    )
    async def do_github_content(self, query: str) -> dict:
        """Fetch file content from GitHub.

        *query* can be a full URL like
        ``https://github.com/codejunkie99/ztk/blob/main/README.md`` or
        ``owner/repo/blob/branch/path``.
        """
        if not query:
            return {"status": "error", "msg": "No query provided"}
        try:
            # Parse query into owner/repo/ref/path
            q = query.strip()
            for prefix in ("https://github.com/", "github.com/"):
                if q.startswith(prefix):
                    q = q[len(prefix):]
            parts = q.split("/")
            if len(parts) < 4 or "blob" not in parts:
                return {"status": "error", "msg": "格式错误。请用 'owner/repo/blob/branch/filepath' 或完整 URL"}
            blob_idx = parts.index("blob")
            owner = parts[blob_idx - 1] if blob_idx > 0 else ""
            repo = parts[0] if blob_idx == 1 else parts[blob_idx - 1] if blob_idx > 1 else ""
            if "/" in owner:
                owner, repo = owner.split("/", 1)
            ref = parts[blob_idx + 1] if blob_idx + 1 < len(parts) else "main"
            filepath = "/".join(parts[blob_idx + 2:])

            import requests as _req
            headers = {
                "User-Agent": "OAA/1.0",
                "Accept": "application/vnd.github.raw+json",
            }
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filepath}?ref={ref}"
            resp = _req.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            import base64 as _b64
            content = _b64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            return {
                "status": "success",
                "path": data.get("path", ""),
                "size": data.get("size", 0),
                "content": content,
                "html_url": data.get("html_url", ""),
            }
        except Exception as exc:
            logger.error("github_content failed: %s", exc)
            return {"status": "error", "msg": f"GitHub 文件读取失败: {exc}"}

    # ------------------------------------------------------------------
    # N6: module_index — structured self-introspection
    # ------------------------------------------------------------------

    @agent_tool(
        name="module_index",
        description="Query the internal module structure index. Use to find which modules expose which tools, config keys, or data formats. Modes: 'list_modules' (all importable modules), 'list_tools' (all registered tools with owning module), 'list_config' (config key paths), or 'lookup' (search for a specific name/tool/config). The index is generated on first call and cached."
    )
    async def do_module_index(self, query: str = "", mode: str = "lookup") -> dict:
        """Structured self-introspection index for OAA codebase.

        Modes:
        - ``list_modules`` — all importable OAA modules with brief descriptions
        - ``list_tools`` — all registered tools grouped by module
        - ``list_config`` — config key paths with types
        - ``lookup`` — search for *query* across modules, tools, and config keys
        """
        mode = mode.strip() or "lookup"

        if mode == "list_modules":
            modules = self._build_module_list()
            return {"status": "success", "modules": modules, "count": len(modules)}

        if mode == "list_tools":
            tools = self._build_tool_list()
            return {"status": "success", "tools": tools, "count": len(tools)}

        if mode == "list_config":
            config_keys = self._build_config_index()
            return {"status": "success", "config_keys": config_keys, "count": len(config_keys)}

        if mode == "lookup":
            if not query.strip():
                return {"status": "error", "msg": "lookup 模式需要 query 参数（搜索关键字）"}
            results = self._lookup_in_index(query.strip())
            return {"status": "success", "query": query, "results": results}

        return {"status": "error", "msg": f"未知 mode: {mode}。请用 list_modules/list_tools/list_config/lookup"}

    def _build_module_list(self) -> list[dict]:
        """Discover all OAA Python modules."""
        import pkgutil as _pu
        import importlib as _il
        modules = []
        try:
            oaa_path = os.path.dirname(os.path.dirname(__file__))
            for finder, name, ispkg in _pu.iter_modules([oaa_path]):
                if not name.startswith("_"):
                    try:
                        mod = _il.import_module(f"oaa.{name}")
                        doc = (getattr(mod, "__doc__", "") or "").strip()
                        modules.append({
                            "name": f"oaa.{name}",
                            "package": ispkg,
                            "description": doc[:120].split("\n")[0] if doc else "",
                        })
                    except Exception:
                        modules.append({"name": f"oaa.{name}", "package": ispkg, "description": ""})
        except Exception as exc:
            logger.warning("_build_module_list failed: %s", exc)
        return modules

    def _build_tool_list(self) -> list[dict]:
        """List all registered tools with schema info."""
        tools = []
        try:
            from .tool_schema import ATOMIC_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA
            for cat, schemas in [("atomic", ATOMIC_TOOLS_SCHEMA), ("extended", EXTENDED_TOOLS_SCHEMA)]:
                for s in schemas:
                    fn = s.get("function", {})
                    tools.append({
                        "name": fn.get("name", ""),
                        "category": cat,
                        "description": fn.get("description", "")[:150],
                    })
        except Exception:
            pass
        return tools

    def _build_config_index(self) -> list[dict]:
        """Extract config key structure from AppConfig."""
        keys = []
        try:
            from ..config import AppConfig
            import dataclasses as _dc
            for field in _dc.fields(AppConfig):
                keys.append({
                    "key": field.name,
                    "type": str(field.type) if field.type else "any",
                    "default": str(field.default) if field.default is not None else "",
                })
        except Exception:
            pass
        return keys

    def _lookup_in_index(self, keyword: str) -> list[dict]:
        """Search modules + tools + config for keyword."""
        results = []
        q = keyword.lower()
        for m in self._build_module_list():
            if q in m.get("name", "").lower() or q in m.get("description", "").lower():
                results.append({"type": "module", **m})
        for t in self._build_tool_list():
            if q in t.get("name", "").lower() or q in t.get("description", "").lower():
                results.append({"type": "tool", **t})
        for c in self._build_config_index():
            if q in c.get("key", "").lower() or q in c.get("type", "").lower():
                results.append({"type": "config", **c})
        return results[:20]  # cap to prevent context blowout

    # ------------------------------------------------------------------
    # Tool-group management (dynamic tool loading)
    # ------------------------------------------------------------------

    @agent_tool(
        name="tool_group_load",
        description="Load a tool group to access domain-specific tools. Available groups: wechat(8), feishu(18), dingtalk(28), schedule(3), skills(4), self_modify(7), office(2), plans(3), proposals(3), mcp(3), browser(1), github(2), diagnostics(2), chat_history(1), reflection(2). Core tools include: file ops, code exec, shell, search, memory, git, health, download, module_index. Use this when core tools aren't enough. Groups stay loaded for the session."
    )
    async def do_tool_group_load(self, group: str) -> dict:
        """Load a tool group by name, making its schemas visible to the LLM."""
        if not hasattr(self, '_agent_ref') or self._agent_ref is None:
            return {"status": "error", "msg": "工具组管理器未初始化"}
        group = group.lower().strip()
        valid = {"wechat", "feishu", "dingtalk", "schedule", "skills",
                 "self_modify", "office", "plans", "proposals", "mcp",
                 "browser", "github", "diagnostics", "chat_history",
                 "reflection", "email"}
        if group not in valid:
            return {"status": "error", "msg": f"未知的工具组: {group}。可用组: {', '.join(sorted(valid))}"}
        count = self._agent_ref.load_tool_group(group)
        if count > 0:
            loaded = self._agent_ref.get_loaded_groups()
            return {"status": "success", "msg": f"已加载 **{group}** 组 ({count} 个工具)。当前已加载: {', '.join(loaded)}"}
        elif group in self._agent_ref._loaded_groups:
            return {"status": "success", "msg": f"**{group}** 组已经加载，无需重复操作"}
        return {"status": "error", "msg": f"加载 {group} 失败（组不存在或为空）"}

    @agent_tool(
        name="tool_group_unload",
        description="Unload a tool group to free context space. Use for groups no longer needed in the current conversation."
    )
    async def do_tool_group_unload(self, group: str) -> dict:
        """Unload a tool group."""
        if not hasattr(self, '_agent_ref') or self._agent_ref is None:
            return {"status": "error", "msg": "工具组管理器未初始化"}
        ok = self._agent_ref.unload_tool_group(group.lower().strip())
        if ok:
            loaded = self._agent_ref.get_loaded_groups()
            remaining = ', '.join(loaded) if loaded else '(仅核心工具)'
            return {"status": "success", "msg": f"已卸载 **{group}** 组。当前已加载: {remaining}"}
        return {"status": "error", "msg": f"**{group}** 组未加载，无需卸载"}

    @agent_tool(
        name="tool_group_list",
        description="List all tool groups with their tool counts and load status."
    )
    async def do_tool_group_list(self) -> dict:
        """List all tool groups and their status."""
        from . import tool_groups as tg
        loaded = getattr(self._agent_ref, '_loaded_groups', set()) if hasattr(self, '_agent_ref') and self._agent_ref else set()
        lines = ["## 工具组列表\n"]
        lines.append("| 组名 | 工具数 | 状态 |")
        lines.append("|------|--------|------|")
        for g, count in sorted(tg.GROUP_INDEX.items()):
            status = "✅ 已加载" if g in loaded else "📦"
            lines.append(f"| {g} | {count} | {status} |")
        core_count = len(self._build_tool_list()) - sum(tg.GROUP_INDEX.values())
        lines.append(f"\n核心工具（始终可见）: ~{max(0, core_count)} 个")
        return {"status": "success", "msg": "\n".join(lines)}

    # ------------------------------------------------------------------
    # Scheduled task tools (schedule_create / list / update / delete)
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
        """Create a scheduled task.

        Args:
            name: Task name (e.g. '每日科技新闻摘要')
            execution_prompt: What the agent should DO when the task fires.
                Write this as a self-contained instruction that an agent
                can follow without additional context.  Include specifics
                like word count, data source, format requirements.
            cycle: 'daily', 'weekly', or 'monthly'
            start_hour: Hour (0-23) to execute
            start_minute: Minute (0-59) to execute
            description: Human-readable description shown in the task list
            delivery_channels: Where to send results. Default ['chat', 'wechat'].
                Valid: 'chat', 'wechat', 'dingtalk', 'feishu'
            cycle_day: For weekly: 0=Mon..6=Sun. For monthly: 1-31
        """
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

        # Check for time conflicts with existing tasks
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
        """List all scheduled tasks."""
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        tasks = self._scheduler.list_tasks()
        if not tasks:
            return {"status": "success", "tasks": [], "msg": "暂无定时任务"}
        return {"status": "success", "tasks": tasks, "count": len(tasks)}

    @agent_tool(
        name="schedule_update",
        description="Update an existing scheduled task. Only the fields you provide will be changed. You can update: name, execution_prompt, cycle, start_hour, start_minute, description, delivery_channels, enabled."
    )
    async def do_schedule_update(self, id: str, **kwargs) -> dict:
        """Update a scheduled task by ID."""
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not id:
            return {"status": "error", "msg": "必须提供任务 ID"}
        updated = self._scheduler.update(id, kwargs)
        if updated is None:
            return {"status": "error", "msg": f"未找到任务: {id}"}
        return {"status": "success", "msg": f"已更新任务「{updated['name']}」", "task": updated}

    @agent_tool(
        name="schedule_delete",
        description="Delete a scheduled task by ID. This permanently removes the task — it will no longer execute. Use when user says '取消/删除 定时任务X'."
    )
    async def do_schedule_delete(self, id: str) -> dict:
        """Delete a scheduled task."""
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
        """Trigger a scheduled task immediately."""
        if not self._scheduler:
            return {"status": "error", "msg": "任务调度器未初始化"}
        if not id:
            return {"status": "error", "msg": "必须提供任务 ID"}
        task = self._scheduler.get(id)
        if task is None:
            return {"status": "error", "msg": f"未找到任务: {id}"}
        if not task.get("execution_prompt", "").strip():
            return {"status": "error", "msg": f"任务「{task['name']}」没有 execution_prompt，无法手动执行"}

        # Execute the task prompt now
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

    # ------------------------------------------------------------------
    # Clone tools — safe self-modification via isolated code copy
    # ------------------------------------------------------------------

    @agent_tool(
        name="clone_create",
        description="Create an isolated copy of the OAA source tree. The clone lives in the data directory and excludes runtime data (workspace, memory, db), build artifacts (node_modules, dist), and git history. Use this when you need to make complex or risky self-modifications — edit the clone first, test, then sync back."
    )
    async def do_clone_create(self) -> dict:
        """Create a source code clone for safe self-modification."""
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.create)

    @agent_tool(
        name="clone_edit",
        description="Apply a text edit to a file in the clone (not the live file!). Similar to self_improve but targets the cloned copy. Use clone_sync to push changes to live after testing. Parameters: path (relative to OAA root, e.g. oaa/agent/tools.py), old_content (exact text to replace), new_content (replacement text), description (summary of the change)."
    )
    async def do_clone_edit(self, path: str, old_content: str,
                            new_content: str, description: str = "") -> dict:
        """Edit a file inside the clone."""
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.apply_edit, path, old_content, new_content)

    @agent_tool(
        name="clone_sync",
        description="Sync all pending clone modifications to the live source tree. Each modified file is backed up before overwrite. After sync, call reload_module for the changes to take effect."
    )
    async def do_clone_sync(self) -> dict:
        """Sync clone modifications to live system."""
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        from .repair_loop import get_active_proposal_id
        prop_id = get_active_proposal_id()
        return await asyncio.to_thread(mgr.sync, prop_id)

    @agent_tool(
        name="clone_discard",
        description="Delete the clone directory. Any unsynced modifications will be lost. Idempotent — safe to call even if no clone exists."
    )
    async def do_clone_discard(self) -> dict:
        """Delete the clone directory."""
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.discard)

    @agent_tool(
        name="clone_status",
        description="Show the clone status: whether it exists, when it was created, how many files have been modified, and which files. Use before sync to review pending changes."
    )
    async def do_clone_status(self) -> dict:
        """Show clone status and pending modifications."""
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.status)

    # ------------------------------------------------------------------
    # Preference tools — structured user preference management
    # ------------------------------------------------------------------

    @agent_tool(
        name="preference_get",
        description="Get a user preference by key. Use when you need to know a specific user preference (e.g., report style, notification channel). Returns the value and metadata."
    )
    async def do_preference_get(self, key: str) -> dict:
        """Get a single user preference by key."""
        store = getattr(self, "_prefs_store", None)
        if store is None:
            return {"status": "error", "msg": "PreferencesStore 未初始化"}
        pref = store.get(key)
        if pref is None:
            return {"status": "error", "msg": f"偏好不存在: {key}"}
        return {"status": "success", "preference": pref}

    @agent_tool(
        name="preference_search",
        description="Search user preferences by keyword. Matches against keys and descriptions. Returns all matches (enabled first). Use when you want to find relevant preferences for the current task."
    )
    async def do_preference_search(self, query: str = "") -> dict:
        """Search preferences by keyword."""
        store = getattr(self, "_prefs_store", None)
        if store is None:
            return {"status": "error", "msg": "PreferencesStore 未初始化"}
        results = store.search(query)
        return {"status": "success", "preferences": results, "count": len(results)}

    @agent_tool(
        name="preference_set",
        description="Set a user preference. Use when the user explicitly expresses a preference about how you should behave (e.g. 'report briefly', 'always confirm before deleting'). The preference will be remembered across sessions and shown in your system prompt. Parameters: key (short identifier), value (the preference value), description (human-readable explanation of what this means)."
    )
    async def do_preference_set(self, key: str, value: str,
                                description: str = "") -> dict:
        """Set a user preference (agent-sourced)."""
        store = getattr(self, "_prefs_store", None)
        if store is None:
            return {"status": "error", "msg": "PreferencesStore 未初始化"}
        result = store.set(key, value, description=description, source="agent")
        return {"status": "success", "preference": result}

    # ------------------------------------------------------------------
    # Self code review — Phase 6
    # ------------------------------------------------------------------

    @agent_tool(
        name="self_code_review",
        description="Review your own source code for bugs, anti-patterns, and improvements. Reads the specified source file and analyzes it for quality issues, security concerns, and optimization opportunities. Use before making changes or when troubleshooting. Returns structured findings with line references."
    )
    async def do_self_code_review(self, path: str) -> dict:
        """Review own source code for quality issues.

        Args:
            path: File path relative to OAA root, e.g. 'oaa/agent/tools.py'
        """
        # Resolve the source file path
        full_path = self._resolve_source_path(path)
        if not full_path:
            return {"status": "error", "msg": f"File not found: {path}"}

        if not os.path.isfile(full_path):
            return {"status": "error", "msg": f"Not a file: {path}"}

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError as exc:
            return {"status": "error", "msg": f"Cannot read file: {exc}"}

        if not source.strip():
            return {"status": "error", "msg": "File is empty"}

        # Basic static checks (lint-like, no LLM needed)
        lines = source.split("\n")
        findings = []
        _warned_lines = set()

        # Check 1: overly long lines
        for i, line in enumerate(lines, 1):
            if len(line) > 200 and i not in _warned_lines:
                findings.append({
                    "type": "style",
                    "line": i,
                    "severity": "warning",
                    "message": f"Line too long ({len(line)} chars, max 200 recommended)",
                })
                _warned_lines.add(i)

        # Check 2: bare except clauses
        for i, line in enumerate(lines, 1):
            if re.match(r"^\s*except\s*:", line) and i not in _warned_lines:
                findings.append({
                    "type": "bug_risk",
                    "line": i,
                    "severity": "warning",
                    "message": "Bare except: catches all exceptions, may hide bugs",
                })
                _warned_lines.add(i)

        # Check 3: TODO/FIXME markers
        for i, line in enumerate(lines, 1):
            if re.search(r"#\s*(TODO|FIXME|HACK|XXX)", line, re.IGNORECASE) and i not in _warned_lines:
                findings.append({
                    "type": "incomplete",
                    "line": i,
                    "severity": "info",
                    "message": f"Unresolved marker: {re.search(r'#\s*(TODO|FIXME|HACK|XXX)\b.*', line).group(0).strip()}",
                })
                _warned_lines.add(i)

        # Check 4: print statements (should use logger)
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.match(r"^\s*print\s*\(", stripped) and "logger" not in line and i not in _warned_lines:
                # Exclude common test/demo scenarios
                if "tests/" not in path and "demo" not in path.lower():
                    findings.append({
                        "type": "style",
                        "line": i,
                        "severity": "info",
                        "message": "print() used instead of logger",
                    })
                    _warned_lines.add(i)

        # Check 5: large functions (simple heuristic — count lines between def and next def/class)
        _in_def = False
        _def_start = 0
        _def_name = ""
        _def_indent = 0
        for i, line in enumerate(lines, 1):
            if re.match(r"^\s*def\s+\w+\s*\(", line):
                if _in_def and i - _def_start > 80:
                    findings.append({
                        "type": "complexity",
                        "line": _def_start,
                        "severity": "warning",
                        "message": f"Function '{_def_name}' is {i - _def_start} lines long, consider refactoring",
                    })
                _in_def = True
                _def_start = i
                _def_name = re.match(r"^\s*def\s+(\w+)", line).group(1)
                _def_indent = len(line) - len(line.lstrip())
            elif _in_def and re.match(r"^\S", line) and _def_indent > 0 and len(line) - len(line.lstrip()) <= _def_indent:
                if i - _def_start > 80:
                    findings.append({
                        "type": "complexity",
                        "line": _def_start,
                        "severity": "warning",
                        "message": f"Function '{_def_name}' is {i - _def_start} lines long, consider refactoring",
                    })
                _in_def = False

        return {
            "status": "success",
            "file": path,
            "line_count": len(lines),
            "char_count": len(source),
            "findings": findings,
            "finding_count": len(findings),
            "summary": {
                "errors": len([f for f in findings if f["severity"] == "error"]),
                "warnings": len([f for f in findings if f["severity"] == "warning"]),
                "info": len([f for f in findings if f["severity"] == "info"]),
            },
        }

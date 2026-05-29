"""Atomic tools core — AtomicTools class, constants, file/shell/self-modify/introspection tools."""

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

from ...auth.permissions import PermissionsManager
from ...logging_config import get_logger
from ..handler import BaseHandler
from ..path_utils import resolve_workspace_path
from ..tool_decorator import agent_tool

if TYPE_CHECKING:
    from ..conversation_archiver import ConversationArchiver
    from ..memory_manager import MemoryManager

logger = get_logger("agent.tools")

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # oaa/agent/
_SANDBOX_RUNNER = os.path.join(_AGENT_DIR, "_sandbox_runner.py")
_EXEC_RUNNER = os.path.join(_AGENT_DIR, "_exec_runner.py")

OAA_ROOT = os.path.normpath(os.path.join(_AGENT_DIR, "..", ".."))
_OAA_SOURCE_DIRS = {os.path.normpath(os.path.join(OAA_ROOT, d)) for d in ("oaa", "skills", "dynamic_tools")}
_OAA_BACKUP_DIR = "backups"

from ._code import CodeMixin
from ._git import GitMixin
from ._memory import MemoryMixin
from ._schedule import ScheduleMixin
from ._misc import MiscMixin


class AtomicTools(BaseHandler, CodeMixin, GitMixin, MemoryMixin, ScheduleMixin, MiscMixin):
    """9 atomic tools from GenericAgent, adapted to async."""

    _DANGEROUS_SHELL_PATTERNS = [
        r"\brm\s+(-[rRf]+\s+)*[/~]",
        r"\bdd\s+if=",
        r">\s*/dev/sd",
        r"mkfs\.",
        r":\(\)\s*\{\s*:\|:&\s*\}\s*;",
        r"\bchmod\s+(-R\s+)?777\s+/",
        r"\bcurl.*\|\s*(ba)?sh",
        r"\bwget.*\|\s*(ba)?sh",
        r"\beval\s",
    ]

    _MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024
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
        return resolve_workspace_path(path, self.data_dir, self.permissions)

    def set_memory_manager(self, mgr: "MemoryManager"):
        self._memory_mgr = mgr

    def set_archiver(self, archiver: "ConversationArchiver"):
        self._archiver = archiver

    def set_proposal_store(self, store):
        self._proposal_store = store

    def set_idle_inspector(self, inspector):
        self._idle_inspector = inspector

    def set_scheduler(self, scheduler):
        self._scheduler = scheduler

    def set_tool_group_manager(self, agent):
        self._agent_ref = agent

    def set_wechat_cli_path(self, path: str):
        self._wechat_cli_path = path

    def set_clone_manager(self, mgr):
        self._clone_mgr = mgr

    def set_preferences_store(self, store):
        self._prefs_store = store

    def set_todo_store(self, store):
        self._todo_store = store

    def set_memory_store(self, store):
        self._memory_store = store

    async def dispatch(self, tool_name: str, args: dict) -> Any:
        result = await super().dispatch(tool_name, args)
        if self.permissions and isinstance(result, dict) and result.get("status") in ("success", "ok"):
            await self.permissions.record_tool_success(tool_name)
        return result

    async def _confirm(self, operation: str, details: str = "") -> bool:
        if self.permissions:
            return await self.permissions.confirm_operation(operation, details)
        return True

    # ------------------------------------------------------------------
    # Self-modification helpers (backup / changelog / pycache)
    # ------------------------------------------------------------------

    def _is_oaa_path(self, path: str) -> bool:
        norm = os.path.normpath(path)
        return any(norm.startswith(d + os.sep) or norm == d for d in _OAA_SOURCE_DIRS)

    @staticmethod
    def _resolve_source_path(path: str) -> str | None:
        candidates = [os.path.normpath(os.path.join(OAA_ROOT, path))]
        orig_stripped = path.lstrip(".")
        if "." in orig_stripped and not orig_stripped.endswith(".py"):
            mod_path = orig_stripped.replace(".", "/") + ".py"
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, mod_path)))
            pkg_path = orig_stripped.replace(".", "/") + "/__init__.py"
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, pkg_path)))
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[0] == parts[1]:
            candidates.append(os.path.normpath(os.path.join(OAA_ROOT, "/".join(parts[1:]))))
        for c in candidates:
            if c.startswith(os.path.normpath(OAA_ROOT)) and os.path.exists(c):
                return c
        return None

    def _backup_file(self, filepath: str) -> str:
        backup_dir = os.path.join(self.data_dir, _OAA_BACKUP_DIR)
        rel = os.path.relpath(filepath, OAA_ROOT)
        backup_path = os.path.join(backup_dir, f"{rel}.{int(time.time())}.bak")
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        try:
            shutil.copy2(filepath, backup_path)
            logger.info("Backup created: %s -> %s", filepath, backup_path)
            return backup_path
        except Exception as exc:
            logger.warning("Backup failed for %s: %s", filepath, exc)
            return ""

    def _record_change(self, filepath: str, description: str, backup_path: str = ""):
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
        if not self._is_oaa_path(filepath):
            return
        try:
            from ..repair_loop import record_rollback_entry as _record
            rel = os.path.relpath(filepath, OAA_ROOT)
            change = {"type": "file_edit", "path": rel, "description": description[:200]}
            if backup_path:
                change["backup"] = backup_path
            _record(self.data_dir, "_tool_level", change)
        except Exception as exc:
            logger.debug("Failed to record rollback entry: %s", exc)

    def _clear_pycache(self, filepath: str):
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

    # ------------------------------------------------------------------
    # code_run (internal, not exposed to LLM)
    # ------------------------------------------------------------------

    async def do_code_run(self, args_or_code, type: str = "python",
                           timeout: int = 15, cwd: str = "") -> dict:
        if isinstance(args_or_code, dict):
            code = args_or_code.get("code", "")
            timeout = args_or_code.get("timeout", timeout)
            type = args_or_code.get("type", type)
            cwd = args_or_code.get("cwd", cwd)
        else:
            code = args_or_code
        return await self._do_code_run_subprocess(code, timeout, type, cwd)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    @agent_tool(description="Read file content")
    async def do_file_read(self, path: str, start: int = 1, count: int = 200, keyword: str = "") -> dict:
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

    # ------------------------------------------------------------------
    # ask_user
    # ------------------------------------------------------------------

    @agent_tool(
        name="ask_user",
        description="Interrupt for user input or decision"
    )
    async def do_ask_user(self, question: str, candidates: list = []) -> dict:
        return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
                "data": {"question": question, "candidates": candidates or []}}

    # ------------------------------------------------------------------
    # modify_own_prompt
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
            if os.path.exists(path):
                backup_dir = os.path.join(self.data_dir, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(backup_dir, f"{section.upper()}.md.bak")
                shutil.copy2(path, backup_path)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content.strip() + "\n")
            logger.info("modify_own_prompt: section=%s lines=%d", section, len(content.strip().split("\n")))
            return {"status": "success", "section": section, "lines": len(content.strip().split("\n"))}
        return {"status": "error", "msg": f"Unknown action '{action}'. Use list, read, or write."}

    # ------------------------------------------------------------------
    # self_improve — atomic self-modification with verify & rollback
    # ------------------------------------------------------------------

    async def _do_reload(self, rel_path: str) -> str:
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
        if not await self._confirm("self_improve", f"{path}: replace unique text"):
            return {"status": "error", "msg": "Self-improvement not permitted"}
        backup_path = self._backup_file(full_path)
        if not backup_path:
            return {"status": "error", "msg": "Backup failed — aborting"}
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
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))
        except Exception as exc:
            self._restore_backup(full_path, backup_path)
            return {"status": "error", "msg": f"Write failed: {exc}"}
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
        try:
            shutil.copy2(backup_path, filepath)
            logger.info("Rolled back %s from %s", filepath, backup_path)
        except Exception as exc:
            logger.error("Rollback failed for %s: %s", filepath, exc)

    # ------------------------------------------------------------------
    # shell_run
    # ------------------------------------------------------------------

    @agent_tool(
        name="shell_run",
        description="Execute an arbitrary shell command. Use this when you need to run CLI tools, scripts, or any system command. For Python/PowerShell code use 'code_exec' instead."
    )
    async def do_shell_run(self, command: str, timeout: int = 60, cwd: str = "") -> dict:
        if not command:
            return {"status": "error", "msg": "No command provided"}
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

    # ------------------------------------------------------------------
    # WeChat CLI proxy tools
    # ------------------------------------------------------------------

    async def _wechat_cli_call(self, method: str, **kwargs) -> dict:
        try:
            from ...gateway.adapters.wechat_cli import WeChatCLI
            cli = WeChatCLI(cli_path=self._wechat_cli_path)
            fn = getattr(cli, method, None)
            if fn is None:
                return {"status": "error", "msg": f"WeChatCLI 没有方法: {method}"}
            result = await fn(**kwargs)
            if isinstance(result, str) and result.startswith("Error:"):
                return {"status": "error", "msg": result[6:].strip()}
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

    # ------------------------------------------------------------------
    # read_own_source / list_own_structure
    # ------------------------------------------------------------------

    @agent_tool(
        name="read_own_source",
        description="Read OAA source code files. Accepts file paths (oaa/app.py), Python module paths (oaa.app), or glob patterns. Use when you need to understand or debug your own implementation."
    )
    async def do_read_own_source(self, path: str = "", pattern: str = "", start_line: int = 1, line_count: int = 200) -> dict:
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
                from ..memory_manager import MemoryManager
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
        subpath = path
        depth = min(depth, 4)
        target_dir = os.path.normpath(os.path.join(OAA_ROOT, subpath))
        if not target_dir.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}
        if not os.path.isdir(target_dir):
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
        return {"status": "success", "path": subpath or ".", "tree": "\n".join(tree_lines)}

    # ------------------------------------------------------------------
    # reload_module / rollback_change
    # ------------------------------------------------------------------

    @agent_tool(
        name="reload_module",
        description="Reload a Python module after source changes. Clears __pycache__ and re-imports. Only works for non-core modules (tools, extended_tools, adapter files). Core module changes (loop, handler, oaa_agent) require a restart."
    )
    async def do_reload_module(self, module: str) -> dict:
        module_path = module
        if not module_path:
            return {"status": "error", "msg": "module is required"}
        full_path = os.path.normpath(os.path.join(OAA_ROOT, module_path))
        if not full_path.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Module path outside OAA project root"}
        rel = os.path.splitext(os.path.relpath(full_path, OAA_ROOT))[0]
        mod_name = rel.replace(os.sep, ".")
        core_modules = {"oaa.agent.loop", "oaa.agent.handler", "oaa.agent.oaa_agent",
                        "oaa.agent.tool_schema", "oaa.app"}
        if mod_name in core_modules:
            return {"status": "error", "msg": f"核心模块 {mod_name} 修改后需要重启进程才能生效。请重启 OAA。"}
        pycache_dir = os.path.join(os.path.dirname(full_path), "__pycache__")
        base = os.path.splitext(os.path.basename(full_path))[0]
        if os.path.isdir(pycache_dir):
            for fname in os.listdir(pycache_dir):
                if fname.startswith(base + ".") and fname.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(pycache_dir, fname))
                    except OSError:
                        pass
        try:
            import importlib
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                logger.info("Module reloaded: %s", mod_name)
                return {"status": "success", "msg": f"{mod_name} 已重载"}
            else:
                if OAA_ROOT not in sys.path:
                    sys.path.insert(0, OAA_ROOT)
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
        if idx is None:
            if not entries:
                return {"status": "success", "msg": "暂无修改记录"}
            lines = ["# 自修改记录\n"]
            for i, e in enumerate(reversed(entries[-20:])):
                status_tag = " ✅" if e["status"] == "active" else " ↩️"
                lines.append(f"{len(entries) - i}. [{e['timestamp']}]{status_tag} {e['file']} — {e['change']}")
            return {"status": "success", "content": "\n".join(lines)}
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
        src_path = os.path.normpath(os.path.join(OAA_ROOT, target["file"]))
        try:
            shutil.copy2(backup_path, src_path)
            self._clear_pycache(src_path)
            rel = os.path.relpath(src_path, OAA_ROOT)
            reload_msg = await self._do_reload(rel)
            self._record_change(src_path, f"回滚变更 #{idx}: {target['change']}", "")
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
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
    # module_index — structured self-introspection
    # ------------------------------------------------------------------

    @agent_tool(
        name="module_index",
        description="Query the internal module structure index. Use to find which modules expose which tools, config keys, or data formats. Modes: 'list_modules' (all importable modules), 'list_tools' (all registered tools with owning module), 'list_config' (config key paths), or 'lookup' (search for a specific name/tool/config). The index is generated on first call and cached."
    )
    async def do_module_index(self, query: str = "", mode: str = "lookup") -> dict:
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
        import pkgutil as _pu
        import importlib as _il
        modules = []
        try:
            oaa_path = os.path.join(OAA_ROOT, "oaa")
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
        tools = []
        try:
            from ..tool_schema import ATOMIC_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA
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
        keys = []
        try:
            from ...config import AppConfig
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
        return results[:20]

    # ------------------------------------------------------------------
    # Tool-group management (dynamic tool loading)
    # ------------------------------------------------------------------

    @agent_tool(
        name="tool_group_load",
        description="Load a tool group to access domain-specific tools. Available groups: wechat(8), feishu(18), dingtalk(28), schedule(3), skills(4), self_modify(7), office(2), plans(3), proposals(3), mcp(3), browser(1), github(2), diagnostics(2), chat_history(1), reflection(2). Core tools include: file ops, code exec, shell, search, memory, git, health, download, module_index. Use this when core tools aren't enough. Groups stay loaded for the session."
    )
    async def do_tool_group_load(self, group: str) -> dict:
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
        from .. import tool_groups as tg
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

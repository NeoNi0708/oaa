"""Atomic tools — ported from GenericAgent ga.py. All tools as async methods."""
import asyncio
import json
import os
import sys
import tempfile
from typing import TYPE_CHECKING, Optional

from ..auth.permissions import PermissionsManager
from ..logging_config import get_logger
from .handler import BaseHandler
from .path_utils import resolve_workspace_path
from .tool_decorator import agent_tool

if TYPE_CHECKING:
    from .memory_manager import MemoryManager

logger = get_logger("agent.tools")

_SANDBOX_RUNNER = os.path.join(os.path.dirname(__file__), "_sandbox_runner.py")
_EXEC_RUNNER = os.path.join(os.path.dirname(__file__), "_exec_runner.py")

# OAA project root for self-modification tools
OAA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_OAA_SOURCE_DIRS = {os.path.normpath(os.path.join(OAA_ROOT, d)) for d in ("oaa", "skills", "dynamic_tools")}
_OAA_BACKUP_DIR = "backups"  # relative to data_dir


class AtomicTools(BaseHandler):
    """9 atomic tools from GenericAgent, adapted to async."""

    def __init__(self, data_dir: str, permissions: Optional[PermissionsManager] = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self.working_memory = {}
        self._memory_mgr: Optional["MemoryManager"] = None

    def _resolve_path(self, path: str) -> str:
        """Resolve path relative to workspace, checking permissions if configured."""
        return resolve_workspace_path(path, self.data_dir, self.permissions)

    def set_memory_manager(self, mgr: "MemoryManager"):
        """Inject the tiered memory manager."""
        self._memory_mgr = mgr

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

    async def do_code_run(self, args: dict) -> dict:
        """Execute Python/PowerShell code within workspace restrictions."""
        code = args.get("code") or args.get("script", "")
        code_type = args.get("type", "python")
        timeout = min(args.get("timeout", 15), 60)
        cwd = self._resolve_path(args.get("cwd", "."))

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

    async def do_code_exec(self, args: dict) -> dict:
        """Execute Python code in-process for agent self-extension.

        Allows most imports but blocks shell execution (os.system,
        subprocess.Popen, shutil.rmtree, etc.).  Uses the ``result``
        variable convention for return values.
        """
        code = args.get("code", "")
        timeout = min(args.get("timeout", 15), 60)

        if not code.strip():
            return {"status": "error", "msg": "No code provided"}

        with tempfile.NamedTemporaryFile(
            suffix=".py", delete=False, mode="w", encoding="utf-8", dir=self.data_dir
        ) as f:
            f.write(code)
            tmp_path = f.name

        result_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=self.data_dir)
        result_path = result_file.name
        result_file.close()

        cmd = [sys.executable, "-I", "-X", "utf8", "-u", _EXEC_RUNNER, tmp_path, result_path]

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

            return {
                "status": "success" if proc.returncode == 0 else "error",
                "result": result_data.get("result"),
                "stdout": stdout_str[:50000] if stdout_str else "",
                "stderr": stderr_str[:50000] if stderr_str else "",
                "exit_code": proc.returncode,
            }
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

    async def do_file_write(self, args: dict) -> dict:
        """Create or modify a file. Auto-backs up when editing OAA source."""
        path = self._resolve_path(args.get("path", ""))
        content = args.get("content", "")
        mode = args.get("mode", "overwrite")
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
                self._clear_pycache(path)

            logger.info("file_write: path=%s mode=%s bytes=%d", os.path.basename(path), mode, len(content))
            return {"status": "success", "bytes": len(content)}
        except Exception as exc:
            logger.error("file_write failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    async def do_file_patch(self, args: dict) -> dict:
        """Replace unique text in a file. Auto-backs up when editing OAA source."""
        path = self._resolve_path(args.get("path", ""))
        old = args.get("old_content", "")
        new = args.get("new_content", "")

        if not os.path.exists(path):
            return {"status": "error", "msg": "File not found"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            count = text.count(old)
            if count == 0:
                return {"status": "error", "msg": "old_content not found"}
            if count > 1:
                return {"status": "error", "msg": f"Found {count} matches — must be unique"}

            is_oaa = self._is_oaa_path(path)
            backup_path = ""
            if is_oaa:
                backup_path = self._backup_file(path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))

            if is_oaa:
                self._record_change(path, "file_patch: replaced unique text", backup_path)
                self._clear_pycache(path)

            logger.info("file_patch: %s", os.path.basename(path))
            return {"status": "success"}
        except Exception as exc:
            logger.error("file_patch failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    async def do_ask_user(self, args: dict) -> dict:
        """Interrupt for user input."""
        return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
                "data": {"question": args.get("question", ""), "candidates": args.get("candidates", [])}}

    async def do_update_working_checkpoint(self, args: dict) -> dict:
        """Save key info to working memory (survives across restarts). Uses
        tiered HOT memory — entries are automatically compacted when HOT
        exceeds 100 lines.  Call this when you learn something about the
        user's preferences, rules, or workflow patterns."""
        key_info = args.get("key_info", "")
        if not key_info:
            return {"status": "error", "msg": "key_info is required"}

        if self._memory_mgr:
            return self._memory_mgr.add_to_hot(key_info)

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

    async def do_correction_log(self, args: dict) -> dict:
        """Log a user correction so the agent remembers next time.

        Call this when the user explicitly corrects you:
        - "不对，应该是..."
        - "你错了，..."
        - "不是这样，..."
        - "我告诉过你..."

        Also call this after self-reflection when you realize your own
        output could have been better.
        """
        context = args.get("context", "")
        lesson = args.get("lesson", "")
        if not context or not lesson:
            return {"status": "error", "msg": "context and lesson are required"}
        if self._memory_mgr:
            return self._memory_mgr.add_correction(context, lesson)
        return {"status": "error", "msg": "Memory manager not available"}

    async def do_memory_recall(self, args: dict) -> dict:
        """Search across all memory tiers (HOT, corrections, warm) for a keyword.

        Use when you need to find something you learned earlier,
        or when the user asks "还记得...", "我之前说过...".
        """
        query = args.get("query", "")
        if not query:
            return {"status": "error", "msg": "query is required"}
        if self._memory_mgr:
            return self._memory_mgr.search(query)
        return {"status": "error", "msg": "Memory manager not available"}

    async def do_self_reflect(self, args: dict) -> dict:
        """After completing significant work, reflect and learn.

        Call this at the end of a multi-step task to log what went well
        and what could be improved next time.
        """
        context = args.get("context", "")
        reflection = args.get("reflection", "")
        lesson = args.get("lesson", "")
        if not context or not reflection:
            return {"status": "error", "msg": "context and reflection are required"}
        msg = f"[Self-reflection] {context}: {reflection}"
        if lesson:
            msg += f" → Lesson: {lesson}"
        if self._memory_mgr:
            self._memory_mgr.add_to_hot(msg)
        return {"status": "success", "msg": "Reflection saved"}

    # --- WeChat CLI tool stubs (wechat-cli not bundled yet) ---

    async def do_wechat_sessions(self, args: dict) -> dict:
        return {"status": "error", "msg": "wechat-cli 未配置。请在设置中配置微信数据目录后重试。"}

    async def do_wechat_history(self, args: dict) -> dict:
        return {"status": "error", "msg": "wechat-cli 未配置。请在设置中配置微信数据目录后重试。"}

    async def do_wechat_search(self, args: dict) -> dict:
        return {"status": "error", "msg": "wechat-cli 未配置。请在设置中配置微信数据目录后重试。"}

    async def do_wechat_contacts(self, args: dict) -> dict:
        return {"status": "error", "msg": "wechat-cli 未配置。请在设置中配置微信数据目录后重试。"}

    async def do_wechat_unread(self, args: dict) -> dict:
        return {"status": "error", "msg": "wechat-cli 未配置。请在设置中配置微信数据目录后重试。"}

    async def do_shell_run(self, args: dict) -> dict:
        """Execute an arbitrary shell command."""
        command = args.get("command", "")
        if not command:
            return {"status": "error", "msg": "No command provided"}
        timeout = min(args.get("timeout", 60), 300)
        cwd = self._resolve_path(args.get("cwd", ".")) if args.get("cwd") else None

        logger.info("shell_run: command=%.200s timeout=%s cwd=%s", command, timeout, cwd)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
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

    async def do_read_own_source(self, args: dict) -> dict:
        """Read OAA source code files, restricted to the project root."""
        path = args.get("path", "")
        pattern = args.get("pattern", "")
        start_line = args.get("start_line", 1)
        line_count = args.get("line_count", 200)

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
        if not os.path.isfile(full_path):
            return {"status": "error", "msg": f"File not found: {path}"}

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

    async def do_list_own_structure(self, args: dict) -> dict:
        """List OAA project directory structure."""
        subpath = args.get("path", "")
        depth = min(args.get("depth", 2), 4)

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

    async def do_reload_module(self, args: dict) -> dict:
        """Reload a non-core Python module after source changes.

        Clears ``__pycache__`` then attempts ``importlib.reload()``.
        Core modules (loop, handler, oaa_agent) require a full restart.
        """
        module_path = args.get("module", "")
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

    async def do_rollback_change(self, args: dict) -> dict:
        """List recent self-modifications or roll back a specific change."""
        index = args.get("index")

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
        if index is None:
            if not entries:
                return {"status": "success", "msg": "暂无修改记录"}
            lines = ["# 自修改记录\n"]
            for i, e in enumerate(reversed(entries[-20:])):
                status_tag = " ✅" if e["status"] == "active" else " ↩️"
                lines.append(f"{len(entries) - i}. [{e['timestamp']}]{status_tag} {e['file']} — {e['change']}")
            return {"status": "success", "content": "\n".join(lines)}

        # Rollback mode
        if index < 1 or index > len(entries):
            return {"status": "error", "msg": f"无效索引 {index}，有效范围 1-{len(entries)}"}

        target = entries[index - 1]
        if target["status"] != "active":
            return {"status": "error", "msg": f"变更 #{index} 已被回滚或状态异常"}

        backup_rel = target["backup"]
        if backup_rel == "none" or not backup_rel:
            return {"status": "error", "msg": f"变更 #{index} 没有备份文件，无法回滚"}

        backup_path = os.path.normpath(os.path.join(backup_dir, backup_rel))
        if not os.path.exists(backup_path):
            return {"status": "error", "msg": f"备份文件不存在: {backup_path}"}

        # Restore backup
        src_path = os.path.normpath(os.path.join(OAA_ROOT, target["file"]))
        try:
            import shutil
            shutil.copy2(backup_path, src_path)
            self._clear_pycache(src_path)
            # Mark as rolled back in changelog
            self._record_change(src_path, f"回滚变更 #{index}: {target['change']}", "")

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

            return {"status": "success", "msg": f"已回滚变更 #{index} ({target['file']})"}
        except Exception as exc:
            logger.error("Rollback failed: %s", exc)
            return {"status": "error", "msg": f"回滚失败: {exc}"}

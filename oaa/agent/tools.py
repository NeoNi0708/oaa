"""Atomic tools — ported from GenericAgent ga.py. All tools as async methods."""
import asyncio
import os
import sys
import tempfile
from typing import Optional

from ..auth.permissions import PermissionsManager
from ..logging_config import get_logger
from .handler import BaseHandler
from .path_utils import resolve_workspace_path

logger = get_logger("agent.tools")

_SANDBOX_RUNNER = os.path.join(os.path.dirname(__file__), "_sandbox_runner.py")


class AtomicTools(BaseHandler):
    """9 atomic tools from GenericAgent, adapted to async."""

    def __init__(self, data_dir: str, permissions: Optional[PermissionsManager] = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self.working_memory = {}

    def _resolve_path(self, path: str) -> str:
        """Resolve path relative to workspace, checking permissions if configured."""
        return resolve_workspace_path(path, self.data_dir, self.permissions)

    async def do_code_run(self, args: dict) -> dict:
        """Execute Python/PowerShell code within workspace restrictions."""
        code = args.get("code") or args.get("script", "")
        code_type = args.get("type", "python")
        timeout = min(args.get("timeout", 60), 120)
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

    async def do_file_read(self, args: dict) -> dict:
        """Read file content. Returns dict with status/content."""
        path = self._resolve_path(args.get("path", ""))
        start = args.get("start", 1)
        count = args.get("count", 200)
        keyword = args.get("keyword")

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
        """Create or modify a file."""
        path = self._resolve_path(args.get("path", ""))
        content = args.get("content", "")
        mode = args.get("mode", "overwrite")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
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
            logger.info("file_write: path=%s mode=%s bytes=%d", os.path.basename(path), mode, len(content))
            return {"status": "success", "bytes": len(content)}
        except Exception as exc:
            logger.error("file_write failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

    async def do_file_patch(self, args: dict) -> dict:
        """Replace unique text in a file."""
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
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))
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
        """Save key info to working memory (survives across turns)."""
        key_info = args.get("key_info", "")
        if key_info:
            self.working_memory["key_info"] = key_info
            logger.debug("checkpoint updated with %d bytes", len(key_info))
        return {"status": "ok"}

    async def do_start_long_term_update(self, args: dict) -> dict:
        """Trigger async long-term memory consolidation."""
        logger.info("long_term_update triggered")
        return {"status": "ok", "msg": "Long-term memory update triggered"}

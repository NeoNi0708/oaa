"""Git operations mixin — git_status, git_diff, git_log."""

import asyncio
import os

from ...logging_config import get_logger
from ._core import OAA_ROOT
from ..tool_decorator import agent_tool

logger = get_logger("agent.tools.git")


class GitMixin:
    """Mixin for git operation tools."""

    @agent_tool(
        name="git_status",
        description="Show git working tree status: modified, staged, untracked files. Use to understand what's changed before code review."
    )
    async def do_git_status(self) -> dict:
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

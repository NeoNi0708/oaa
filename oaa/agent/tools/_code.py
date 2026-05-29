"""Code execution and search mixin — code_exec, aifix, code_search, glob, self_code_review."""

import ast
import asyncio
import json
import os
import re
import sys
import tempfile
import textwrap
import time

from ...logging_config import get_logger
from ._core import OAA_ROOT
from ..tool_decorator import agent_tool

logger = get_logger("agent.tools.code")

_SANDBOX_RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_sandbox_runner.py")
_EXEC_RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_exec_runner.py")

_COMMON_MODULES = frozenset({
    "json", "os", "re", "sys", "math", "random", "datetime",
    "collections", "itertools", "functools", "typing", "pathlib",
    "shutil", "glob", "csv", "io", "string", "copy", "decimal",
    "hashlib", "base64", "uuid", "pprint", " fractions",
})


def _fix_syntax_errors(code: str) -> tuple[str, str]:
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
            pass
    try:
        dedented = textwrap.dedent(code)
        if dedented != code:
            ast.parse(dedented)
            return dedented, "移除多余缩进"
    except SyntaxError:
        pass
    return code, ""


def _fix_name_error(code: str, stderr: str) -> tuple[str, str]:
    m = re.search(r"NameError.*?name '(\w+)' is not defined", stderr)
    if not m:
        return code, ""
    name = m.group(1)
    if name not in _COMMON_MODULES:
        return code, ""
    for line in code.split("\n"):
        if re.match(rf"^\s*import\s+{name}(\s|$)", line) or re.match(rf"^\s*from\s+{name}\s+import", line):
            return code, ""
    fixed = f"import {name}\n{code}"
    return fixed, f"自动补充 import {name}"


class CodeMixin:
    """Mixin for code execution, search, and review tools."""

    async def _do_code_run_subprocess(self, code: str, timeout: int = 15,
                                       type: str = "python", cwd: str = "") -> dict:
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
        if not await self._confirm("code_exec", code[:120]):
            return {"status": "error", "msg": "Code execution not permitted"}
        timeout = min(timeout, 60)
        if not code.strip():
            return {"status": "error", "msg": "No code provided"}
        if mode == "sandbox":
            return await self._do_code_run_subprocess(code, timeout)
        if async_mode:
            return await self._do_code_exec_async(code, timeout)
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
                if attempt == 0:
                    code, fix2 = _fix_name_error(code, stderr_str)
                    if fix2:
                        all_fixes.append(fix2)
                        continue
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

    def _do_code_exec_async(self, code: str, timeout: int) -> dict:
        task_id = f"async_{int(time.time() * 1000)}"
        output_dir = os.path.join(self.data_dir, "async_results")
        os.makedirs(output_dir, exist_ok=True)
        status_path = os.path.join(output_dir, f"{task_id}.json")

        async def _run_async():
            result = {"status": "running", "started": time.time(), "task_id": task_id}
            try:
                with open(status_path, "w", encoding="utf-8") as f:
                    json.dump(result, f)
                loop = asyncio.get_event_loop()
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

    @agent_tool(
        name="aifix",
        description="Auto-detect and fix common Python code errors. Analyzes the given code for syntax errors, missing imports, and other common AI-generated code issues. Returns the fixed code with a diff-like explanation. Use this when code_exec returns errors or before writing code to disk with self_improve."
    )
    async def do_aifix(self, code: str) -> dict:
        if not code.strip():
            return {"status": "error", "msg": "No code provided"}
        original = code
        fixed = code
        fixes: list[str] = []
        fixed, syn_fix = _fix_syntax_errors(fixed)
        if syn_fix:
            fixes.append(f"语法修复: {syn_fix}")
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
            fixed2, name_fix = _fix_name_error(fixed, str(e))
            if name_fix:
                fixed = fixed2
                fixes.append(f"导入补全: {name_fix}")
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

    @agent_tool(
        name="code_search",
        description="Search across all project files for a text pattern or regex. Results include file paths and matching lines with line numbers. Use for finding where functions are defined, tracing references, or auditing code patterns."
    )
    async def do_code_search(self, pattern: str, path: str = "", include: str = "", max_results: int = 50) -> dict:
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
        import glob as _glob
        search_root = os.path.join(OAA_ROOT, path) if path else OAA_ROOT
        if not search_root.startswith(os.path.normpath(OAA_ROOT)):
            return {"status": "error", "msg": "Path outside OAA project root"}
        full_pattern = os.path.normpath(os.path.join(search_root, pattern))
        matches = [m for m in _glob.glob(full_pattern, recursive=True) if os.path.isfile(m)]
        matches = matches[:200]
        rel_matches = [os.path.relpath(m, OAA_ROOT) for m in matches]
        return {
            "status": "success" if rel_matches else "empty",
            "files": rel_matches,
            "total": len(rel_matches),
            "truncated": len(matches) >= 200,
        }

    @agent_tool(
        name="self_code_review",
        description="Review your own source code for bugs, anti-patterns, and improvements. Reads the specified source file and analyzes it for quality issues, security concerns, and optimization opportunities. Use before making changes or when troubleshooting. Returns structured findings with line references."
    )
    async def do_self_code_review(self, path: str) -> dict:
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
        lines = source.split("\n")
        findings = []
        _warned_lines = set()
        for i, line in enumerate(lines, 1):
            if len(line) > 200 and i not in _warned_lines:
                findings.append({
                    "type": "style", "line": i, "severity": "warning",
                    "message": f"Line too long ({len(line)} chars, max 200 recommended)",
                })
                _warned_lines.add(i)
        for i, line in enumerate(lines, 1):
            if re.match(r"^\s*except\s*:", line) and i not in _warned_lines:
                findings.append({
                    "type": "bug_risk", "line": i, "severity": "warning",
                    "message": "Bare except: catches all exceptions, may hide bugs",
                })
                _warned_lines.add(i)
        for i, line in enumerate(lines, 1):
            if re.search(r"#\s*(TODO|FIXME|HACK|XXX)", line, re.IGNORECASE) and i not in _warned_lines:
                findings.append({
                    "type": "incomplete", "line": i, "severity": "info",
                    "message": f"Unresolved marker: {re.search(r'#\s*(TODO|FIXME|HACK|XXX)\b.*', line).group(0).strip()}",
                })
                _warned_lines.add(i)
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.match(r"^\s*print\s*\(", stripped) and "logger" not in line and i not in _warned_lines:
                if "tests/" not in path and "demo" not in path.lower():
                    findings.append({
                        "type": "style", "line": i, "severity": "info",
                        "message": "print() used instead of logger",
                    })
                    _warned_lines.add(i)
        _in_def = False
        _def_start = 0
        _def_name = ""
        _def_indent = 0
        for i, line in enumerate(lines, 1):
            if re.match(r"^\s*def\s+\w+\s*\(", line):
                if _in_def and i - _def_start > 80:
                    findings.append({
                        "type": "complexity", "line": _def_start, "severity": "warning",
                        "message": f"Function '{_def_name}' is {i - _def_start} lines long, consider refactoring",
                    })
                _in_def = True
                _def_start = i
                _def_name = re.match(r"^\s*def\s+(\w+)", line).group(1)
                _def_indent = len(line) - len(line.lstrip())
            elif _in_def and re.match(r"^\S", line) and _def_indent > 0 and len(line) - len(line.lstrip()) <= _def_indent:
                if i - _def_start > 80:
                    findings.append({
                        "type": "complexity", "line": _def_start, "severity": "warning",
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

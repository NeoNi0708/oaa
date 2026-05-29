"""Miscellaneous tools mixin — health, download, github, clone, preferences."""

import asyncio
import os

from ...logging_config import get_logger
from ._core import OAA_ROOT
from ..tool_decorator import agent_tool

logger = get_logger("agent.tools.misc")


class MiscMixin:
    """Mixin for miscellaneous tools (health, download, github, clone, preference)."""

    # ------------------------------------------------------------------
    # Health diagnose
    # ------------------------------------------------------------------

    @agent_tool(
        name="health_diagnose",
        description="Comprehensive health diagnosis: checks WebSocket port 9765, process status, memory, CPU, recent tool failures, and data directory state. Run this when user reports app issues like '页面不见了', '技能加载不出来', or general instability."
    )
    async def do_health_diagnose(self) -> dict:
        import datetime as _dt
        import shutil
        pid = os.getpid()
        checks = {}
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
                checks["ws_port_9765"] = {"listening": len(ws_listen) > 0, "connections": len(ws_estab)}
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
        try:
            failures = self._memory_mgr.count_tool_failures() if self._memory_mgr else {"total": 0}
            checks["recent_tool_failures"] = failures.get("total", 0)
            checks["failures_by_tool"] = failures.get("by_tool", {})
        except Exception:
            checks["recent_tool_failures"] = -1
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
                "status": "success", "alive": True, "pid": pid,
                "uptime_sec": uptime_sec, "memory_mb": round(mem_mb, 1),
                "cpu_percent": cpu_pct, "cmdline": " ".join(proc.cmdline())[:200],
            }
        except ImportError:
            return {"status": "success", "alive": True, "pid": pid, "note": "psutil not available, limited info"}
        except Exception as exc:
            return {"status": "error", "msg": str(exc), "pid": pid, "alive": True}

    # ------------------------------------------------------------------
    # Download file
    # ------------------------------------------------------------------

    @agent_tool(
        name="download_file",
        description="Download a file from a URL and save it locally. Returns the saved path and file size. Use for fetching assets, datasets, or any remote file that should be stored locally."
    )
    async def do_download_file(self, url: str, save_path: str = "") -> dict:
        if not url:
            return {"status": "error", "msg": "No URL provided"}
        if not url.startswith(("http://", "https://")):
            return {"status": "error", "msg": f"不支持协议: {url.split('://')[0]}"}
        try:
            import requests as _req
            resp = _req.get(url, stream=True, timeout=60,
                          headers={"User-Agent": "OAA/1.0 (Windows; agent)"})
            resp.raise_for_status()
            cl = resp.headers.get("Content-Length", "")
            if cl and cl.isdigit() and int(cl) > self._MAX_DOWNLOAD_SIZE:
                return {"status": "error", "msg": f"文件过大 ({int(cl)} 字节)，上限 {self._MAX_DOWNLOAD_SIZE} 字节"}
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
                "status": "success", "msg": f"已下载 {total} 字节到 {save_path}",
                "path": save_path, "size": total, "content_type": ct or "unknown",
            }
        except Exception as exc:
            logger.error("download_file failed: %s", exc)
            return {"status": "error", "msg": f"下载失败: {exc}"}

    # ------------------------------------------------------------------
    # GitHub tools
    # ------------------------------------------------------------------

    @agent_tool(
        name="github_repo",
        description="Look up a GitHub repository by owner/repo or URL. Returns repo metadata: description, stars, forks, language, topics, and clone URL. Use to evaluate open-source tools, libraries, or skills before installing them."
    )
    async def do_github_repo(self, query: str) -> dict:
        if not query:
            return {"status": "error", "msg": "No query provided"}
        try:
            repo_path = query.strip()
            for prefix in ("https://github.com/", "github.com/"):
                if repo_path.startswith(prefix):
                    repo_path = repo_path[len(prefix):]
            repo_path = repo_path.rstrip("/")
            import requests as _req
            headers = {"User-Agent": "OAA/1.0", "Accept": "application/vnd.github+json"}
            if "/" in repo_path and repo_path.count("/") == 1:
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
        if not query:
            return {"status": "error", "msg": "No query provided"}
        try:
            q = query.strip()
            for prefix in ("https://github.com/", "github.com/"):
                if q.startswith(prefix):
                    q = q[len(prefix):]
            parts = q.split("/")
            if len(parts) < 4 or "blob" not in parts:
                return {"status": "error", "msg": "格式错误。请用 'owner/repo/blob/branch/filepath' 或完整 URL"}
            blob_idx = parts.index("blob")
            owner = parts[0]
            repo = parts[1]
            ref = parts[blob_idx + 1] if blob_idx + 1 < len(parts) else "main"
            filepath = "/".join(parts[blob_idx + 2:])
            import requests as _req
            headers = {"User-Agent": "OAA/1.0", "Accept": "application/vnd.github.raw+json"}
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
    # Clone tools
    # ------------------------------------------------------------------

    @agent_tool(
        name="clone_create",
        description="Create an isolated copy of the OAA source tree. The clone lives in the data directory and excludes runtime data (workspace, memory, db), build artifacts (node_modules, dist), and git history. Use this when you need to make complex or risky self-modifications — edit the clone first, test, then sync back."
    )
    async def do_clone_create(self) -> dict:
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
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.apply_edit, path, old_content, new_content)

    @agent_tool(
        name="clone_sync",
        description="Sync all pending clone modifications to the live source tree. Each modified file is backed up before overwrite. After sync, call reload_module for the changes to take effect."
    )
    async def do_clone_sync(self) -> dict:
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        from ..repair_loop import get_active_proposal_id
        prop_id = get_active_proposal_id()
        return await asyncio.to_thread(mgr.sync, prop_id)

    @agent_tool(
        name="clone_discard",
        description="Delete the clone directory. Any unsynced modifications will be lost. Idempotent — safe to call even if no clone exists."
    )
    async def do_clone_discard(self) -> dict:
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.discard)

    @agent_tool(
        name="clone_status",
        description="Show the clone status: whether it exists, when it was created, how many files have been modified, and which files. Use before sync to review pending changes."
    )
    async def do_clone_status(self) -> dict:
        mgr = getattr(self, "_clone_mgr", None)
        if mgr is None:
            return {"status": "error", "msg": "CloneManager 未初始化"}
        return await asyncio.to_thread(mgr.status)

    # ------------------------------------------------------------------
    # Preference tools
    # ------------------------------------------------------------------

    @agent_tool(
        name="preference_get",
        description="Get a user preference by key. Use when you need to know a specific user preference (e.g., report style, notification channel). Returns the value and metadata."
    )
    async def do_preference_get(self, key: str) -> dict:
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
        store = getattr(self, "_prefs_store", None)
        if store is None:
            return {"status": "error", "msg": "PreferencesStore 未初始化"}
        results = store.search(query)
        return {"status": "success", "preferences": results, "count": len(results)}

    @agent_tool(
        name="preference_set",
        description="Set a user preference. Use when the user explicitly expresses a preference about how you should behave (e.g. 'report briefly', 'always confirm before deleting'). The preference will be remembered across sessions and shown in your system prompt. Parameters: key (short identifier), value (the preference value), description (human-readable explanation of what this means)."
    )
    async def do_preference_set(self, key: str, value: str, description: str = "") -> dict:
        store = getattr(self, "_prefs_store", None)
        if store is None:
            return {"status": "error", "msg": "PreferencesStore 未初始化"}
        result = store.set(key, value, description=description, source="agent")
        return {"status": "success", "preference": result}

    # ------------------------------------------------------------------
    # Code audit
    # ------------------------------------------------------------------

    @agent_tool(
        name="code_audit",
        description="Perform a symbol-level audit of Python source code using the ast module. "
                    "Does NOT require loading source files into the LLM context — the audit runs "
                    "directly on disk. Use this when you need to find bugs, broken call chains, "
                    "or structural issues across many files. "
                    "Parameters: module_path (dotted module name like 'oaa.agent.loop' or file path "
                    "relative to project root), resolve_calls (whether to check if called functions "
                    "actually exist, default true). "
                    "Returns: classes, functions, call graph, unresolved calls (potential bugs), "
                    "imports, and a summary."
    )
    async def do_code_audit(self, module_path: str, resolve_calls: bool = True) -> dict:
        """Run a code audit against a module or package."""
        try:
            from ..code_audit import audit_module
            result = audit_module(str(OAA_ROOT), module_path, resolve_calls=resolve_calls)
            return {"status": "success", **result}
        except Exception as exc:
            return {"status": "error", "msg": f"Code audit failed: {exc}"}

    # ------------------------------------------------------------------
    # Todo — in-memory working memory
    # ------------------------------------------------------------------

    @agent_tool(
        name="todo",
        description="Manage your in-memory task list. Use for complex multi-step tasks (3+ steps). "
                    "This is your external working memory — you see it every turn in the system prompt. "
                    "Three operations: "
                    "action='set' with items=[{id,content,status}] to replace the entire list; "
                    "action='update' with items=[{id,content?,status?}] to merge changes (new ids appended); "
                    "action='get' to read the current list. "
                    "Statuses: pending/in_progress/completed/cancelled. Only ONE in_progress at a time. "
                    "Start multi-step tasks by calling todo with action='set' to build your task list, "
                    "then update as you progress."
    )
    async def do_todo(self, action: str = "get", items: list = None) -> dict:
        """Manage the in-memory todo list."""
        store = getattr(self, "_todo_store", None)
        if store is None:
            return {"status": "error", "msg": "TodoStore 未初始化"}

        if action == "set":
            if not items:
                return {"status": "error", "msg": "todo set 需要 items 参数"}
            result = store.set(items)
            return {"status": "success", "items": result, "count": len(result)}

        elif action == "update":
            if not items:
                return {"status": "error", "msg": "todo update 需要 items 参数"}
            result = store.update(items)
            return {"status": "success", "items": result, "count": len(result)}

        elif action == "get":
            result = store.get()
            return {"status": "success", "items": result, "count": len(result)}

        else:
            return {"status": "error", "msg": f"未知 action: {action}，支持 set/update/get"}

    # ------------------------------------------------------------------
    # CodeGraph — semantic code search
    # ------------------------------------------------------------------

    @agent_tool(
        name="codegraph_query",
        description="Semantic code search using CodeGraph. "
                    "Describe what code you need to find (e.g. 'how does the memory system work', "
                    "'find where handle_chat_action is defined'). "
                    "Much faster and cheaper than reading files one by one. "
                    "Returns entry points, related symbols, and code snippets."
    )
    async def do_codegraph_query(self, task: str) -> dict:
        """Query the codebase via CodeGraph (pre-indexed symbol graph)."""
        if not task:
            return {"status": "error", "msg": "task is required"}
        import subprocess, json, os as _os
        # Find CodeGraph binary: bundled CLI first, then system PATH
        _cg_bin = "codegraph.cmd" if _os.name == "nt" else "codegraph"
        _bundled_dir = _os.path.join(str(OAA_ROOT), "cli", "node_modules", ".bin")
        _bundled = _os.path.join(_bundled_dir, _cg_bin)
        if not _os.path.isfile(_bundled):
            _bundled = _os.path.join(_bundled_dir, "codegraph.cmd")
        if _os.path.isfile(_bundled):
            _cg_bin = _bundled
        try:
            result = subprocess.run(
                [_cg_bin, "context", task, "--path", str(OAA_ROOT),
                 "--format", "json", "--max-nodes", "30", "--max-code", "8"],
                capture_output=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                return {"status": "error", "msg": f"CodeGraph error: {result.stderr[:200]}"}

            data = json.loads(result.stdout)
            return {"status": "success", **data}
        except FileNotFoundError:
            return {"status": "error", "msg": "CodeGraph not installed. Run: npm install -g @colbymchenry/codegraph"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "msg": "CodeGraph query timed out"}
        except json.JSONDecodeError as exc:
            return {"status": "error", "msg": f"CodeGraph output parse error: {exc}"}
        except Exception as exc:
            return {"status": "error", "msg": f"CodeGraph query failed: {exc}"}

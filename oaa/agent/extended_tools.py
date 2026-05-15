"""Extended tools: email, word, excel, planner, skill tools, wechat, mcp, dynamic."""
import asyncio
import importlib.util
import json
import os
from typing import TYPE_CHECKING, Any, Optional

from .path_utils import resolve_workspace_path
from .planner import Planner

if TYPE_CHECKING:
    from ..auth.permissions import PermissionsManager
    from .skill_manager import SkillManager

DYNAMIC_TOOLS_DIR = "dynamic_tools"


class ExtendedTools:
    """Extended tools: email, word, excel, skill tools, planner."""

    def __init__(self, data_dir: str, permissions: Optional["PermissionsManager"] = None,
                 wechat_adapter: Any = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self._wechat_adapter = wechat_adapter
        self._skill_mgr = None
        self.planner = Planner(os.path.join(data_dir, "workspace", "plans"))
        from ..gateway.adapters.wechat_cli import WeChatCLI
        self.wechat = WeChatCLI()
        from ..gateway.adapters.feishu_cli import FeishuCLI
        self.feishu = FeishuCLI()
        from ..gateway.adapters.dingtalk_cli import DingTalkCLI
        self.dingtalk = DingTalkCLI()

    def set_wechat_adapter(self, adapter: Any):
        """Inject the active WeChat iLink adapter for proactive sending."""
        self._wechat_adapter = adapter

    def set_skill_manager(self, mgr: "SkillManager"):
        """Inject SkillManager reference for skill_load tool."""
        self._skill_mgr = mgr

    # ------------------------------------------------------------------
    # Dynamic tools (runtime-created by agent)
    # ------------------------------------------------------------------

    def _dynamic_tools_dir(self) -> str:
        path = os.path.join(self.data_dir, DYNAMIC_TOOLS_DIR)
        os.makedirs(path, exist_ok=True)
        return path

    def _dynamic_manifest_path(self) -> str:
        return os.path.join(self._dynamic_tools_dir(), "manifest.json")

    def _load_dynamic_manifest(self) -> dict:
        path = self._dynamic_manifest_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_dynamic_manifest(self, manifest: dict):
        path = self._dynamic_manifest_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    async def _run_dynamic_tool(self, tool_name: str, args: dict) -> dict:
        """Execute a dynamic tool by loading and calling its execute() function."""
        manifest = self._load_dynamic_manifest()
        entry = manifest.get(tool_name)
        if not entry:
            return {"status": "error", "msg": f"Dynamic tool '{tool_name}' not found in manifest"}

        filepath = entry.get("path")
        if not filepath or not os.path.exists(filepath):
            return {"status": "error", "msg": f"Dynamic tool '{tool_name}' file not found: {filepath}"}

        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()
            ns: dict[str, Any] = {}
            exec(source, ns)
            if "execute" not in ns:
                return {"status": "error", "msg": f"Dynamic tool '{tool_name}' has no execute() function"}
            fn = ns["execute"]
            if asyncio.iscoroutinefunction(fn):
                return await fn(args)
            return fn(args)
        except Exception as e:
            return {"status": "error", "msg": f"Dynamic tool '{tool_name}' error: {e}"}

    async def do_tool_create(self, args: dict) -> dict:
        """Create a new tool at runtime by providing Python code and a schema.

        The code must define an ``async def execute(args: dict) -> dict`` function.
        After creation, the agent can call this tool by name like any built-in tool.
        """
        name = args.get("name", "")
        code = args.get("code", "")
        description = args.get("description", "")
        parameters = args.get("parameters", {})

        if not name or not code:
            return {"status": "error", "msg": "name and code are required"}

        if not await self._confirm("tool_create", f"Create tool: {name}"):
            return {"status": "error", "msg": "Tool creation not permitted"}

        # Validate: code must define execute()
        ns: dict[str, Any] = {}
        try:
            exec(code, ns)
        except Exception as e:
            return {"status": "error", "msg": f"Code validation failed: {e}"}
        if "execute" not in ns:
            return {"status": "error", "msg": "Code must define an execute() function"}

        # Save to file
        tools_dir = self._dynamic_tools_dir()
        filepath = os.path.join(tools_dir, f"{name}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)

        # Register in manifest
        manifest = self._load_dynamic_manifest()
        manifest[name] = {
            "path": filepath,
            "description": description,
            "parameters": parameters,
        }
        self._save_dynamic_manifest(manifest)

        return {
            "status": "success",
            "msg": f"Tool '{name}' created and registered",
            "tool_name": name,
        }

    async def do_tool_delete(self, args: dict) -> dict:
        """Delete a dynamically created tool."""
        name = args.get("name", "")
        if not name:
            return {"status": "error", "msg": "name is required"}

        manifest = self._load_dynamic_manifest()
        if name not in manifest:
            return {"status": "error", "msg": f"Tool '{name}' not found"}

        # Remove file
        filepath = manifest[name].get("path", "")
        if filepath and os.path.exists(filepath):
            os.remove(filepath)

        # Update manifest
        del manifest[name]
        self._save_dynamic_manifest(manifest)

        return {"status": "success", "msg": f"Tool '{name}' deleted"}

    async def do_tool_list(self, args: dict) -> dict:
        """List all dynamically created tools."""
        manifest = self._load_dynamic_manifest()
        if not manifest:
            return {"status": "success", "tools": {}, "count": 0, "msg": "No dynamic tools created"}
        result = {}
        for name, entry in manifest.items():
            result[name] = {
                "description": entry.get("description", ""),
                "has_params": bool(entry.get("parameters", {})),
            }
        return {"status": "success", "tools": result, "count": len(result)}

    async def _confirm(self, operation: str, details: str = "") -> bool:
        """Check permission for an operation. Returns True if allowed."""
        if self.permissions:
            return await self.permissions.confirm_operation(operation, details)
        return True

    async def do_email_send(self, args: dict) -> dict:
        """Send email via SMTP (himayala or smtplib)."""
        to = args.get("to", "")
        subject = args.get("subject", "")
        if not await self._confirm("email_send", f"To: {to}, Subject: {subject}"):
            return {"status": "error", "msg": "Email sending not permitted"}
        # Stub — actual SMTP sending depends on user config
        return {"status": "success", "msg": f"Email to {to}: '{subject}' queued"}
        # TODO: implement actual SMTP when user provides email config

    async def do_word_doc(self, args: dict) -> dict:
        """Generate Word document."""
        try:
            from docx import Document
        except ImportError:
            return {"status": "error", "msg": "python-docx not installed. Run: pip install python-docx"}
        try:
            path = resolve_workspace_path(args.get("path", "document.docx"), self.data_dir, self.permissions)
        except PermissionError as exc:
            return {"status": "error", "msg": str(exc)}
        title = args.get("title", "Document")
        content = args.get("content", "")
        doc = Document()
        doc.add_heading(title, 0)
        for para in content.split("\n"):
            if para.strip():
                doc.add_paragraph(para)
        doc.save(path)
        return {"status": "success", "path": path}

    async def do_excel_xlsx(self, args: dict) -> dict:
        """Generate Excel spreadsheet."""
        try:
            from openpyxl import Workbook
        except ImportError:
            return {"status": "error", "msg": "openpyxl not installed. Run: pip install openpyxl"}
        try:
            path = resolve_workspace_path(args.get("path", "spreadsheet.xlsx"), self.data_dir, self.permissions)
        except PermissionError as exc:
            return {"status": "error", "msg": str(exc)}
        rows = args.get("rows", [])
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except (json.JSONDecodeError, TypeError):
                rows = [[cell] for cell in rows.split("\n") if cell.strip()]
        wb = Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        wb.save(path)
        return {"status": "success", "path": path}

    async def do_plan_create(self, args: dict) -> dict:
        """Create a new plan via Planner."""
        goal = args.get("goal", "")
        steps = args.get("steps", [])
        try:
            plan = self.planner.create(goal, steps)
        except ValueError as exc:
            return {"status": "error", "msg": str(exc)}
        return {"status": "success", "plan": plan}

    async def do_plan_update(self, args: dict) -> dict:
        """Update a plan step via Planner."""
        plan_id = args.get("plan_id", "")
        step_id = args.get("step_id", 0)
        status = args.get("status", "done")
        result = args.get("result", "")
        plan = self.planner.update(plan_id, step_id, status, result)
        if plan is None:
            return {"status": "error", "msg": f"Plan {plan_id} not found"}
        return {"status": "success", "plan": plan}

    async def do_plan_list(self, args: dict) -> dict:
        """List plans via Planner."""
        status = args.get("status", "")
        plans = self.planner.list_plans(status)
        return {"status": "success", "plans": plans, "count": len(plans)}

    async def do_skill_search(self, args: dict) -> dict:
        """Search ClawHub skill market (stub)."""
        return {"status": "success", "results": [], "msg": "Skill market search stub"}

    async def do_skill_install(self, args: dict) -> dict:
        """Install a skill from ClawHub (stub)."""
        slug = args.get("slug", "")
        if not await self._confirm("skill_install", f"Install skill: {slug}"):
            return {"status": "error", "msg": "Skill install not permitted"}
        return {"status": "success", "msg": f"Skill '{slug}' install stub"}

    # ------------------------------------------------------------------
    # MCP (Model Context Protocol) management
    # ------------------------------------------------------------------

    async def do_mcp_install(self, args: dict) -> dict:
        """Install an MCP server (npm package) and register it for use.

        Installs the package via npm (optionally pinned to a version),
        then registers it in the MCP config file so tools can discover it.
        """
        package = args.get("package", "")
        name = args.get("name", "") or package
        version = args.get("version", "latest")
        command = args.get("command", "npx")
        args_list = args.get("args", [])
        env_vars = args.get("env", {})

        if not package:
            return {"status": "error", "msg": "package name is required"}

        if not await self._confirm("mcp_install", f"Install MCP server: {package}@{version}"):
            return {"status": "error", "msg": "MCP install not permitted"}

        # npm install the package globally
        full_pkg = f"{package}@{version}" if version != "latest" else package
        npm_proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", full_pkg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await npm_proc.communicate()
        if npm_proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return {"status": "error", "msg": f"npm install failed: {err}"}

        # Register in MCP config
        mcp_config = self._load_mcp_config()
        mcp_config["servers"][name] = {
            "command": command,
            "args": args_list,
            "env": env_vars,
        }
        self._save_mcp_config(mcp_config)

        return {
            "status": "success",
            "msg": f"MCP server '{name}' installed and registered",
            "package": full_pkg,
        }

    async def do_mcp_list(self, args: dict) -> dict:
        """List installed/configured MCP servers."""
        mcp_config = self._load_mcp_config()
        servers = mcp_config.get("servers", {})
        if not servers:
            return {"status": "success", "servers": {}, "count": 0, "msg": "No MCP servers configured"}
        result = {}
        for name, cfg in servers.items():
            result[name] = {
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "env_count": len(cfg.get("env", {})),
            }
        return {"status": "success", "servers": result, "count": len(result)}

    async def do_mcp_remove(self, args: dict) -> dict:
        """Remove an MCP server configuration."""
        name = args.get("name", "")
        if not name:
            return {"status": "error", "msg": "name is required"}

        mcp_config = self._load_mcp_config()
        if name not in mcp_config.get("servers", {}):
            return {"status": "error", "msg": f"MCP server '{name}' not found"}

        del mcp_config["servers"][name]
        self._save_mcp_config(mcp_config)

        return {"status": "success", "msg": f"MCP server '{name}' removed"}

    def _load_mcp_config(self) -> dict:
        """Load the MCP config file (creates default if missing)."""
        path = os.path.join(self.data_dir, "mcp_servers.json")
        if not os.path.exists(path):
            return {"servers": {}}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"servers": {}}

    def _save_mcp_config(self, config: dict):
        """Persist MCP config to disk."""
        path = os.path.join(self.data_dir, "mcp_servers.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Skill loading (model-activated per-task)
    # ------------------------------------------------------------------

    async def do_skill_load(self, args: dict) -> dict:
        """Load a skill's SKILL.md, SOP.md, and knowledge content by name.

        The model calls this when it determines a skill matches the current task.
        Returns the full content of the skill for the model to follow.
        """
        name = args.get("name", "")
        if not name or not self._skill_mgr:
            return {"status": "error", "msg": "Skill name required or skill manager not available"}

        skill = self._skill_mgr.get(name)
        if not skill:
            return {"status": "error", "msg": f"Skill '{name}' not found. Available: {', '.join(s.name for s in self._skill_mgr.list_all())}"}

        skill.load()
        result: dict[str, Any] = {
            "status": "success",
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
        }
        if skill.skill_md:
            result["skill_md"] = skill.skill_md
        if skill.sop_md:
            result["sop_md"] = skill.sop_md
        if skill.knowledge:
            result["knowledge"] = skill.knowledge
        return result

    async def do_wechat_sessions(self, args: dict) -> dict:
        """Get recent WeChat session list."""
        limit = args.get("limit", 20)
        result = await self.wechat.sessions(limit)
        return {"status": "success", "data": result}

    async def do_wechat_history(self, args: dict) -> dict:
        """Get WeChat chat history with a contact."""
        name = args.get("name", "")
        limit = args.get("limit", 20)
        result = await self.wechat.history(name, limit)
        return {"status": "success", "data": result}

    async def do_wechat_search(self, args: dict) -> dict:
        """Search WeChat messages globally or in a specific chat."""
        keyword = args.get("keyword", "")
        chat = args.get("chat", "")
        limit = args.get("limit", 20)
        result = await self.wechat.search(keyword, chat, limit)
        return {"status": "success", "data": result}

    async def do_wechat_contacts(self, args: dict) -> dict:
        """Search WeChat contacts."""
        query = args.get("query", "")
        result = await self.wechat.contacts(query)
        return {"status": "success", "data": result}

    async def do_wechat_unread(self, args: dict) -> dict:
        """Get unread WeChat sessions."""
        limit = args.get("limit", 20)
        result = await self.wechat.unread(limit)
        return {"status": "success", "data": result}

    async def do_wechat_send_text(self, args: dict) -> dict:
        """Proactively send a WeChat text message to a contact."""
        to = args.get("to", "")
        text = args.get("text", "")
        if not to or not text:
            return {"status": "error", "msg": "to and text are required"}
        if not self._wechat_adapter:
            return {"status": "error", "msg": "微信适配器未连接，请先扫码登录"}
        if not await self._confirm("wechat_send_text", f"To: {to}"):
            return {"status": "error", "msg": "WeChat send not permitted"}
        try:
            result = await self._wechat_adapter.send_message(to, text)
            return {"status": "success" if result.get("status") == "success" else "error", "data": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    async def do_wechat_send_typing(self, args: dict) -> dict:
        """Send typing indicator ('对方正在输入...'). status=1 show, 0 hide."""
        to = args.get("to", "")
        status = args.get("status", 1)
        if not to:
            return {"status": "error", "msg": "to is required"}
        if not self._wechat_adapter:
            return {"status": "error", "msg": "微信适配器未连接，请先扫码登录"}
        try:
            result = await self._wechat_adapter.send_typing(to, status)
            return {"status": "success" if result.get("status") == "success" else "error", "data": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ------------------------------------------------------------------
    # Feishu tools
    # ------------------------------------------------------------------

    async def do_feishu_send_message(self, args: dict) -> dict:
        """Send a Feishu IM message."""
        text = args.get("text", "")
        to = args.get("to", "")
        user = args.get("user", "")
        if not text:
            return {"status": "error", "msg": "No text provided"}
        if to:
            result = await self.feishu.send_message(to, text)
        elif user:
            result = await self.feishu.send_user_message(user, text)
        else:
            return {"status": "error", "msg": "Provide 'to' (chat_id) or 'user' (open_id)"}
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_feishu_search_user(self, args: dict) -> dict:
        """Search Feishu users."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.feishu.search_user(query, limit)
        return {"status": "success", "data": result}

    async def do_feishu_get_user(self, args: dict) -> dict:
        """Get Feishu user info."""
        user_id = args.get("user_id", "")
        result = await self.feishu.get_user(user_id)
        return {"status": "success", "data": result}

    async def do_feishu_calendar_agenda(self, args: dict) -> dict:
        """View calendar agenda."""
        start = args.get("start", "")
        end = args.get("end", "")
        result = await self.feishu.calendar_agenda(start, end)
        return {"status": "success", "data": result}

    async def do_feishu_calendar_create(self, args: dict) -> dict:
        """Create a calendar event."""
        summary = args.get("summary", "")
        start = args.get("start", "")
        end = args.get("end", "")
        description = args.get("description", "")
        attendees = args.get("attendees", [])
        result = await self.feishu.calendar_create_event(summary, start, end, description, attendees)
        return {"status": "success", "data": result}

    async def do_feishu_drive_search(self, args: dict) -> dict:
        """Search Drive files."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.feishu.drive_search(query, limit)
        return {"status": "success", "data": result}

    async def do_feishu_doc_fetch(self, args: dict) -> dict:
        """Fetch document content."""
        token = args.get("token", "")
        result = await self.feishu.doc_fetch(token)
        return {"status": "success", "data": result}

    async def do_feishu_doc_create(self, args: dict) -> dict:
        """Create a document."""
        title = args.get("title", "")
        content = args.get("content", "")
        result = await self.feishu.doc_create(title, content)
        return {"status": "success", "data": result}

    async def do_feishu_doc_search(self, args: dict) -> dict:
        """Search documents."""
        query = args.get("query", "")
        result = await self.feishu.doc_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_sheets_read(self, args: dict) -> dict:
        """Read spreadsheet values."""
        token = args.get("spreadsheet_token", "")
        range_str = args.get("range", "")
        result = await self.feishu.sheets_read(token, range_str)
        return {"status": "success", "data": result}

    async def do_feishu_sheets_create(self, args: dict) -> dict:
        """Create a spreadsheet."""
        title = args.get("title", "")
        result = await self.feishu.sheets_create(title)
        return {"status": "success", "data": result}

    async def do_feishu_base_records(self, args: dict) -> dict:
        """List bitable records."""
        base_token = args.get("base_token", "")
        table_id = args.get("table_id", "")
        limit = args.get("limit", 100)
        result = await self.feishu.base_records(base_token, table_id, limit)
        return {"status": "success", "data": result}

    async def do_feishu_task_list(self, args: dict) -> dict:
        """List tasks."""
        limit = args.get("limit", 50)
        result = await self.feishu.task_list(limit)
        return {"status": "success", "data": result}

    async def do_feishu_wiki_search(self, args: dict) -> dict:
        """Search wiki (uses drive search)."""
        query = args.get("query", "")
        result = await self.feishu.drive_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_mail_list(self, args: dict) -> dict:
        """List mail (uses mail +messages — requires message IDs)."""
        limit = args.get("limit", 20)
        return {"status": "error", "msg": "Mail listing requires specific message IDs; use feishu_drive_search instead"}

    async def do_feishu_chat_search(self, args: dict) -> dict:
        """Search group chats."""
        query = args.get("query", "")
        result = await self.feishu.chat_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_chat_messages(self, args: dict) -> dict:
        """List chat messages."""
        chat_id = args.get("chat_id", "")
        limit = args.get("limit", 20)
        result = await self.feishu.chat_messages(chat_id, limit)
        return {"status": "success", "data": result}

    async def do_feishu_drive_upload(self, args: dict) -> dict:
        """Upload file to Drive."""
        local_path = args.get("local_path", "")
        folder_token = args.get("folder_token", "")
        result = await self.feishu.drive_upload(local_path, folder_token)
        return {"status": "success", "data": result}

    # ------------------------------------------------------------------
    # DingTalk tools
    # ------------------------------------------------------------------

    async def do_dingtalk_send_message(self, args: dict) -> dict:
        """Send a DingTalk message to a user."""
        user_id = args.get("user_id", "")
        text = args.get("text", "")
        title = args.get("title", "")
        if not user_id or not text:
            return {"status": "error", "msg": "user_id and text are required"}
        result = await self.dingtalk.send_message(user_id, text, title)
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_dingtalk_send_group_message(self, args: dict) -> dict:
        """Send a DingTalk message to a group."""
        group_id = args.get("group_id", "")
        text = args.get("text", "")
        title = args.get("title", "")
        if not group_id or not text:
            return {"status": "error", "msg": "group_id and text are required"}
        result = await self.dingtalk.send_group_message(group_id, text, title)
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_dingtalk_search_user(self, args: dict) -> dict:
        """Search DingTalk users."""
        query = args.get("query", "")
        result = await self.dingtalk.search_user(query)
        return {"status": "success", "data": result}

    async def do_dingtalk_user_info(self, args: dict) -> dict:
        """Get DingTalk user info."""
        user_id = args.get("user_id", "")
        result = await self.dingtalk.get_user(user_id)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_search(self, args: dict) -> dict:
        """Search group conversations."""
        query = args.get("query", "")
        result = await self.dingtalk.chat_search(query)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_list(self, args: dict) -> dict:
        """List top conversations."""
        limit = args.get("limit", 20)
        result = await self.dingtalk.chat_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_history(self, args: dict) -> dict:
        """List recent messages in a group."""
        group_id = args.get("group_id", "")
        limit = args.get("limit", 20)
        if not group_id:
            return {"status": "error", "msg": "group_id is required"}
        result = await self.dingtalk.chat_history(group_id, limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_unread(self, args: dict) -> dict:
        """List unread conversations."""
        limit = args.get("limit", 20)
        result = await self.dingtalk.chat_unread(limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_calendar_list(self, args: dict) -> dict:
        """List calendar events."""
        limit = args.get("limit", 50)
        result = await self.dingtalk.calendar_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_calendar_create(self, args: dict) -> dict:
        """Create a calendar event."""
        summary = args.get("summary", "")
        start_time = args.get("start_time", "")
        end_time = args.get("end_time", "")
        description = args.get("description", "")
        attendees = args.get("attendees", [])
        result = await self.dingtalk.calendar_create(
            summary, start_time, end_time, description, attendees,
        )
        return {"status": "success", "data": result}

    async def do_dingtalk_todo_list(self, args: dict) -> dict:
        """List todo tasks."""
        limit = args.get("limit", 50)
        result = await self.dingtalk.todo_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_todo_create(self, args: dict) -> dict:
        """Create a todo task."""
        subject = args.get("subject", "")
        description = args.get("description", "")
        due_time = args.get("due_time", "")
        executor_ids = args.get("executor_ids", [])
        result = await self.dingtalk.todo_create(subject, description, due_time, executor_ids)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_search(self, args: dict) -> dict:
        """Search documents."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.dingtalk.doc_search(query, limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_read(self, args: dict) -> dict:
        """Read a document."""
        doc_id = args.get("doc_id", "")
        if not doc_id:
            return {"status": "error", "msg": "doc_id is required"}
        result = await self.dingtalk.doc_read(doc_id)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_create(self, args: dict) -> dict:
        """Create a document."""
        title = args.get("title", "")
        content = args.get("content", "")
        if not title:
            return {"status": "error", "msg": "title is required"}
        result = await self.dingtalk.doc_create(title, content)
        return {"status": "success", "data": result}

    async def do_dingtalk_drive_list(self, args: dict) -> dict:
        """List Drive files."""
        parent_id = args.get("parent_id", "")
        limit = args.get("limit", 50)
        result = await self.dingtalk.drive_list(parent_id, limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_wiki_search(self, args: dict) -> dict:
        """Search wiki."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.dingtalk.wiki_search(query, limit=limit)
        return {"status": "success", "data": result}

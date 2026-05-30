"""Core mixin — __init__, setters, _resolve_recipient, dynamic tools, _confirm."""
import asyncio
import json
import os
from typing import TYPE_CHECKING, Any, Optional

from ..path_utils import resolve_workspace_path
from ..planner import Planner
from ..tool_decorator import agent_tool

if TYPE_CHECKING:
    from ...auth.permissions import PermissionsManager
    from ..skill_manager import SkillManager

DYNAMIC_TOOLS_DIR = "dynamic_tools"


class CoreMixin:
    """Core extended tools: init, setters, dynamic tools, confirm."""

    def __init__(self, data_dir: str, permissions: Optional["PermissionsManager"] = None,
                 wechat_adapter: Any = None,
                 dingtalk_client_id: str = "", dingtalk_client_secret: str = "",
                 image_gen_config: Any = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self._wechat_adapter = wechat_adapter
        self._skill_mgr = None
        self.planner = Planner(os.path.join(data_dir, "workspace", "plans"))
        from ...gateway.adapters.wechat_cli import WeChatCLI
        self.wechat = WeChatCLI()
        from ...gateway.adapters.feishu_cli import FeishuCLI
        self.feishu = FeishuCLI()
        from ...gateway.adapters.dingtalk_cli import DingTalkCLI
        self.dingtalk = DingTalkCLI(
            client_id=dingtalk_client_id,
            client_secret=dingtalk_client_secret,
        )
        # Store image gen API key (from config or passed directly)
        self._image_gen_key = ""
        if image_gen_config:
            if hasattr(image_gen_config, "api_key"):
                self._image_gen_key = image_gen_config.api_key or ""
            elif isinstance(image_gen_config, dict):
                self._image_gen_key = image_gen_config.get("api_key", "")

    @agent_tool(
        name="create_survey",
        description="Create a simple single-page survey/form for the user. "
                    "Use ONLY for small, single-page forms (1-5 questions). "
                    "The survey appears as a form in the chat. The user fills it out and submits "
                    "all answers at once. "
                    "Parameters: title (survey title), description (optional instruction text), "
                    "questions (array of {id, type, label, options}). "
                    "Question types: 'single' (radio buttons), 'multiple' (checkboxes), "
                    "'text' (free text input). "
                    "Example: create_survey(title=\"Report Settings\", "
                    "questions=[{id:\"time\",type:\"single\",label:\"Time range?\","
                    "options:[\"1 month\",\"3 months\",\"1 year\"]}, "
                    "{id:\"format\",type:\"single\",label:\"Format?\",options:[\"Word\",\"PDF\"]}])"
                    "IMPORTANT: For questionnaires with 6+ questions, multiple pages, "
                    "conditional logic, or any survey spanning multiple topics, "
                    "use 'create_questionnaire' instead."
    )
    async def do_create_survey(self, title: str, questions: list,
                                description: str = "") -> dict:
        """Create an interactive survey for the user."""
        if not title or not questions:
            return {"status": "error", "msg": "title and questions are required"}
        import time, uuid
        survey_id = uuid.uuid4().hex[:12]
        survey = {
            "survey_id": survey_id,
            "title": title,
            "description": description,
            "questions": questions,
            "created_at": time.time(),
        }
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_survey = survey
        return {
            "status": "success",
            "survey_id": survey_id,
            "question_count": len(questions),
            "_survey_data": survey,
        }

    @agent_tool(
        name="create_questionnaire",
        description=(
            "Create a multi-page interactive questionnaire with conditional logic. "
            "Use this when you need to collect information across multiple form pages "
            "with branching/filtering logic.\n\n"

            "Parameters:\n"
            "- title (string, required) — questionnaire title\n"
            "- description (string, optional) — overall instruction text\n"
            "- sections (array, required) — array of page definitions, each:\n"
            "    - id (string, required) — unique section identifier\n"
            "    - title (string, required) — page heading\n"
            "    - description (string, optional) — page-level instruction\n"
            "    - condition (object, optional) — condition expression (see below)\n"
            "    - questions (array, required) — questions on this page. Each question:\n"
            "        - id (string, required)\n"
            "        - type: 'single', 'multiple', or 'text'\n"
            "        - label (string)\n"
            "        - options (array, required for single/multiple)\n"
            "        - condition (object, optional) — question-level condition\n\n"

            "Condition expression (recursive):\n"
            '- Simple: {"depends_on": "q_id", "equals": "value"} or {"depends_on": "q_id", "in": ["A","B"]}\n'
            '- Compound: {"and": [cond1, cond2]} or {"or": [cond1, cond2]}\n\n'

            "Constraints:\n"
            "- All question_id referenced in conditions must appear in an earlier section "
            "or an earlier question in the same section (no forward references)\n"
            "- LLM must ensure all ids are unique within the questionnaire\n\n"

            "Example:\n"
            'create_questionnaire(\n'
            '  title="需求调研",\n'
            '  description="请根据您的实际情况完成以下问卷",\n'
            '  sections=[\n'
            '    {\n'
            '      "id": "sec_1",\n'
            '      "title": "基本信息",\n'
            '      "questions": [\n'
            '        {"id": "q_role", "type": "single", '
            '"label": "您的身份？", "options": ["选项A", "选项B", "选项C"]}\n'
            '      ]\n'
            '    },\n'
            '    {\n'
            '      "id": "sec_2",\n'
            '      "title": "详细信息",\n'
            '      "condition": {"depends_on": "q_role", "equals": "选项A"},\n'
            '      "questions": [\n'
            '        {"id": "q_detail", "type": "multiple", '
            '"label": "感兴趣的方向", "options": ["方向X", "方向Y", "方向Z"]}\n'
            '      ]\n'
            '    },\n'
            '    {\n'
            '      "id": "sec_3",\n'
            '      "title": "其他需求",\n'
            '      "condition": {"depends_on": "q_role", "in": ["选项B", "选项C"]},\n'
            '      "questions": [\n'
            '        {"id": "q_other", "type": "text", '
            '"label": "请描述您的需求"},\n'
            '        {"id": "q_budget", "type": "single", '
            '"label": "预算范围", "options": ["<1万", "1-5万", "5万+"]}\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            ')'
        )
    )
    async def do_create_questionnaire(self, title: str, sections: list,
                                        description: str = "") -> dict:
        """Create a multi-page interactive questionnaire."""
        if not title or not sections:
            return {"status": "error", "msg": "title and sections are required"}
        import time, uuid
        qnr_id = uuid.uuid4().hex[:12]
        qnr = {
            "id": qnr_id,
            "title": title,
            "description": description,
            "sections": sections,
            "created_at": time.time(),
        }
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_questionnaire = qnr
        return {
            "status": "success",
            "questionnaire_id": qnr_id,
            "section_count": len(sections),
            "_questionnaire_data": qnr,
        }

    @agent_tool(
        name="update_taskboard",
        description="Display a visual task board in the chat showing current task progress. "
                    "Call this after creating or updating your todo list. "
                    "Parameters: items (array of {id, content, status, done_criteria}). "
                    "Statuses: pending, in_progress, completed, cancelled."
    )
    async def do_update_taskboard(self, items: list) -> dict:
        """Display a task board in the chat."""
        if not items:
            return {"status": "error", "msg": "items are required"}
        board_data = {"items": items}
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_taskboard = board_data
        total = len(items)
        done = sum(1 for it in items if it.get("status") == "completed")
        return {"status": "success", "total": total, "completed": done}

    @agent_tool(
        name="show_file",
        description="Display a file preview card in the chat. Call this after generating "
                    "a document, image, spreadsheet, or any file the user might want to download. "
                    "Parameters: path (local file path), file_type (docx/xlsx/png/pdf etc), "
                    "title (optional display name)."
    )
    async def do_show_file(self, path: str, file_type: str = "", title: str = "") -> dict:
        """Show a file preview card in the chat."""
        if not path:
            return {"status": "error", "msg": "path is required"}
        import os
        if not os.path.isfile(path):
            return {"status": "error", "msg": f"File not found: {path}"}
        size = os.path.getsize(path)
        ftype = file_type or path.split(".")[-1] if "." in path else ""
        fname = title or os.path.basename(path)
        file_data = {"path": path, "file_type": ftype, "title": fname, "size": size}
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_file = file_data
        return {"status": "success", "path": path, "file_type": ftype, "size": size}

    @agent_tool(
        name="send_choices",
        description="Present the user with options to choose from in a single interaction. "
                    "Use for simple confirmations or single-selection scenarios "
                    "(e.g. 'confirm execution?', 'which option do you prefer?'). "
                    "The user clicks one button and the selection is sent back immediately. "
                    "Parameters: question (string), options (array of {label, value}). "
                    "For multi-question scenarios use create_survey instead."
    )
    async def do_send_choices(self, question: str, options: list) -> dict:
        """Present a simple choice to the user."""
        if not question or not options:
            return {"status": "error", "msg": "question and options are required"}
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_choices = {
                "question": question,
                "options": [{"label": o.get("label", str(o)) if isinstance(o, dict) else str(o),
                             "value": o.get("value", str(o)) if isinstance(o, dict) else str(o)}
                            for o in options],
            }
        return {"status": "success", "question": question, "option_count": len(options)}

    @agent_tool(
        name="notify_user",
        description="Send a system notification card to the user. Use for non-critical "
                    "information that doesn't need a response: task completion, system status, "
                    "scheduled task results, etc. Parameters: title, message, msg_type "
                    "(info/success/warning/error)."
    )
    async def do_notify_user(self, title: str, message: str,
                              msg_type: str = "info") -> dict:
        """Send a notification card to the chat."""
        if not title or not message:
            return {"status": "error", "msg": "title and message are required"}
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_notify = {"title": title, "message": message, "type": msg_type}
        return {"status": "success", "notified": True}

    @agent_tool(
        name="display_chart",
        description="Display a chart in the chat. Call this when you generate a chart or graph "
                    "so the user can see it directly in the conversation. "
                    "Pass the ECharts-compatible option dict. The chart will render in the chat bubble. "
                    "Example: display_chart(option={\"xAxis\":{\"type\":\"category\",\"data\":[\"A\",\"B\"]},"
                    "\"yAxis\":{\"type\":\"value\"},\"series\":[{\"type\":\"bar\",\"data\":[10,20]}]})"
    )
    async def do_display_chart(self, option: dict, title: str = "") -> dict:
        """Display a chart in the chat via ECharts."""
        if not option:
            return {"status": "error", "msg": "option is required"}
        # Store for loop.py to yield as a chart chunk
        chart_data = {"option": option, "title": title}
        agent = getattr(self, '_oaa_agent', None)
        if agent:
            agent._pending_chart = chart_data
        return {"status": "success", "msg": "图表已展示", "chart": True}

    @agent_tool(
        name="tool_reload",
        description="Hot-reload all tool schemas and rebuild the handler. "
                    "Call this after creating or modifying a source-level tool "
                    "(via file_write+file_patch) so it becomes available "
                    "without restarting OAA. Returns the number of tools loaded."
    )
    async def do_tool_reload(self) -> dict:
        """Hot-reload all tool schemas."""
        agent = getattr(self, '_oaa_agent', None)
        if agent is None or not hasattr(agent, 'reload_tools'):
            return {"status": "error", "msg": "Agent reference not available, cannot reload tools"}
        try:
            agent.reload_tools()
            count = len(agent._tools_schema)
            return {"status": "success", "msg": f"工具已热加载，当前 {count} 个工具可用", "count": count}
        except Exception as exc:
            return {"status": "error", "msg": f"热加载失败: {exc}"}

    def _resolve_recipient(self, to: str) -> str:
        """Resolve WeChat recipient.

        1. Use explicit ``to`` if provided.
        2. If processing an incoming WeChat message, use the sender's wxid.
        3. Fall back to the bot owner's wxid (iLink is one-to-one).
        """
        if not to:
            agent = getattr(self, '_oaa_agent', None)
            if agent and getattr(agent, '_channel_source', '') == 'wechat':
                return getattr(agent, '_channel_user_id', '')
            # iLink is one-to-one — the only recipient is the QR-scan user
            adapter = getattr(self, '_wechat_adapter', None)
            if adapter and getattr(adapter, '_bot_user_id', None):
                return adapter._bot_user_id
        return to

    def set_wechat_adapter(self, adapter: Any):
        """Inject the active WeChat iLink adapter for proactive sending."""
        self._wechat_adapter = adapter

    def set_dingtalk_adapter(self, adapter: Any):
        """Share the DingTalk adapter's authenticated DingTalkCLI instance.

        The adapter's ``_dws_cli`` may hold device-auth state from the QR
        login flow.  Sharing the instance lets ExtendedTools domain tools
        (chat, doc, calendar, …) reuse the same auth session instead of
        creating an unauthenticated clone.
        """
        if adapter and hasattr(adapter, '_dws_cli'):
            self.dingtalk = adapter._dws_cli

    def set_feishu_adapter(self, adapter: Any):
        """Share the Feishu adapter's configured FeishuCLI instance.

        The adapter's ``_lark_cli`` holds the app credentials (App ID/Secret)
        configured during the QR login flow.  Sharing the instance ensures
        ExtendedTools domain tools use the same credentials instead of an
        unconfigured clone.
        """
        if adapter and hasattr(adapter, '_lark_cli'):
            self.feishu = adapter._lark_cli

    def set_skill_manager(self, mgr: "SkillManager"):
        """Inject SkillManager reference for skill_load tool."""
        self._skill_mgr = mgr

    def set_oaa_agent(self, agent):
        """Inject OAAAgent reference so tools can set pending state for the loop."""
        self._oaa_agent = agent

    def set_patch_manager(self, mgr):
        """Inject PatchManager for do_apply_patch/do_remove_patch tools."""
        self._patch_mgr = mgr

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

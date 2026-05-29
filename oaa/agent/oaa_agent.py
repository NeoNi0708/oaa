"""OAA Agent — orchestrates identity, skills, tools, and LLM into a coherent agent loop."""
import asyncio
import os
import sys
from typing import AsyncGenerator, Optional

from ..auth.permissions import PermissionsManager
from ..config import AppConfig
from ..evolution.engine import EvolutionEngine
from ..init import ensure_bundled_cli, ensure_data_dir, load_identity
from ..llm import LLMClient
from ..logging_config import get_logger
from .ai_search_tool import AiSearchTools
from .browser_tools import BrowserTools
from .extended_tools import ExtendedTools
from .handler import BaseHandler
from .idle_inspector import IdleInspector
from .loop import AgentLoop
from . import system_rules as _sys_rules
from . import tool_groups as _tool_groups
from .contract import TaskContract
from .conversation_archiver import ConversationArchiver
from .policy import PolicyEngine
from .memory_manager import MemoryManager
from .skill_manager import SkillManager
from .tool_schema import ATOMIC_TOOLS_SCHEMA, BROWSER_TOOLS_SCHEMA, DINGTALK_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA, FEISHU_TOOLS_SCHEMA, MCP_TOOLS_SCHEMA, WECHAT_TOOLS_SCHEMA
from .tool_decorator import collect_tool_schemas
from .tools import AtomicTools
from .metrics import MetricsCollector

logger = get_logger("agent.oaa_agent")


def _get_schema_name(schema_entry: dict) -> str:
    """Extract the tool name from an OpenAI-format schema entry."""
    fn = schema_entry.get("function", schema_entry)
    return fn.get("name", "")

# OAA project root — used for self-modification tools
OAA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Outer timeout for the entire process_message pipeline (not per-LLM-call).
# When this fires, the agent loop is cancelled and a timeout error is returned.
# The per-LLM-call timeout in loop.py (_LLM_TIMEOUT=90s) is shorter — this is
# the safety net for infinite tool-call loops or unresponsive tool chains.
_PROCESS_TIMEOUT = 600  # 10 minutes


class _MergedHandler(BaseHandler):
    """Dynamic handler that dispatches tool calls to AtomicTools, ExtendedTools,
    BrowserTools, and runtime-created dynamic tools.

    Uses ``__getattr__`` to delegate ``do_<name>`` lookups to the correct
    backend, so all tools are available through a single handler instance
    consumed by ``AgentLoop``.
    """

    def __init__(self, atomic: AtomicTools, extended: ExtendedTools, browser: BrowserTools,
                 search: AiSearchTools):
        self._atomic = atomic
        self._extended = extended
        self._browser = browser
        self._search = search
        self._dynamic_tools: dict[str, dict] = {}

    def __getattr__(self, name: str):
        if not name.startswith("do_"):
            raise AttributeError(name)
        # Check hardcoded tool backends
        if hasattr(self._atomic, name):
            return getattr(self._atomic, name)
        if hasattr(self._extended, name):
            return getattr(self._extended, name)
        if hasattr(self._browser, name):
            return getattr(self._browser, name)
        if hasattr(self._search, name):
            return getattr(self._search, name)
        # Check decorator registries on each backend
        tool_name = name[3:]  # strip "do_"
        for backend in (self._atomic, self._extended, self._browser, self._search):
            registry = getattr(backend, '_tool_registry', {})
            if tool_name in registry:
                return registry[tool_name]
        # Fall back to dynamic tools
        if tool_name in self._dynamic_tools:
            return lambda args: self._extended._run_dynamic_tool(tool_name, args)
        raise AttributeError(name)


class OAAAgent:
    """Top-level OAA agent that wires together identity, skill management,
    tool execution, and LLM interactions.
    """

    def __init__(self, config: AppConfig, permissions: Optional[PermissionsManager] = None,
                 evolution: Optional[EvolutionEngine] = None, wechat_adapter=None,
                 scheduler=None):
        ensure_data_dir(config.data_dir)
        ensure_bundled_cli(config.data_dir)

        self.config = config
        self.identity: dict = load_identity(config.data_dir)
        self.permissions = permissions
        self.evolution = evolution
        self.scheduler = scheduler

        # Tiered memory system (HOT + corrections + warm/ + cold/)
        self.memory = MemoryManager(os.path.join(config.data_dir, "memory"))

        # Structured memory store (Chroma + SQLite — semantic search, importance, digestion)
        from .memory import MemoryStore
        self._memory_store = MemoryStore(config.data_dir)

        # Metrics collector (proactivity + LLM stats)
        self.metrics = MetricsCollector(config.data_dir)
        if self.permissions:
            self.permissions.set_metrics_collector(self.metrics)

        # Structured proposal system (replaces pending_proposals.md)
        from .proposal import ProposalStore
        self._proposal_store = ProposalStore(os.path.join(config.data_dir, "memory"))

        self.llm = LLMClient(config.model)

        # Idle inspector (needs memory + evolution + proposal_store + llm)
        self._idle_inspector = IdleInspector(
            scheduler=scheduler,
            memory_mgr=self.memory,
            evolution=evolution,
            proposal_store=self._proposal_store,
            llm=self.llm,
        )

        # Conversation archiver (structured summaries + cross-session search)
        self.archiver = ConversationArchiver(config.data_dir, llm=self.llm)

        # Runtime channel status (set by OAAApp after adapters are created)
        self._channel_adapters: dict = {}
        # Callback fired when a channel authenticates mid-session (set by OAAApp)
        self._on_channel_ready = None

        skills_dir = os.path.join(config.data_dir, "skills")
        self.skill_mgr = SkillManager(skills_dir)
        self.skill_mgr.discover()
        if self.evolution:
            self.evolution.set_skill_manager(self.skill_mgr)

        self.atomic = AtomicTools(config.data_dir, permissions=permissions)
        self.atomic.set_memory_manager(self.memory)
        self.atomic.set_memory_store(self._memory_store)
        self.atomic.set_archiver(self.archiver)
        self.atomic.set_proposal_store(self._proposal_store)
        self.atomic.set_idle_inspector(self._idle_inspector)
        self.atomic.set_scheduler(scheduler)
        self.atomic.set_tool_group_manager(self)
        self.atomic.set_wechat_cli_path(config.wechat.wechat_cli_path)
        # CloneManager + PreferencesStore for safe self-modification
        from .clone_manager import CloneManager
        from .preferences_store import PreferencesStore
        self._clone_mgr = CloneManager(config.data_dir, OAA_ROOT)
        self._prefs_store = PreferencesStore(config.data_dir)
        self.atomic.set_clone_manager(self._clone_mgr)
        self.atomic.set_preferences_store(self._prefs_store)

        # TodoStore — agent's external working memory
        from .todo_store import TodoStore
        self._todo_store = TodoStore()
        self.atomic.set_todo_store(self._todo_store)

        # Active plan — injected into system prompt so agent always sees it
        self.active_plan: dict = {}
        # Pending chart for display_chart tool
        self._pending_chart = None
        # Pending survey for create_survey tool
        self._pending_survey = None
        # Pending file for show_file tool
        self._pending_file = None
        # Pending taskboard for update_taskboard tool
        self._pending_taskboard = None
        # Pending notify for notify_user tool
        self._pending_notify = None
        # Pending choices for send_choices tool
        self._pending_choices = None
        self.extended = ExtendedTools(
            config.data_dir, permissions=permissions, wechat_adapter=wechat_adapter,
            dingtalk_client_id=config.dingtalk.client_id,
            dingtalk_client_secret=config.dingtalk.client_secret,
            image_gen_config=config.image_gen,
        )
        self.extended.set_skill_manager(self.skill_mgr)
        self.browser = BrowserTools()
        self.search = AiSearchTools(
            tavily_api_key=config.search.tavily_api_key,
            exa_api_key=config.search.exa_api_key,
            anysearch_api_key=config.search.anysearch_api_key,
        )

        # Full schema (all tools) — used for group loading
        self._all_tools_schema: list[dict] = (
            ATOMIC_TOOLS_SCHEMA + EXTENDED_TOOLS_SCHEMA + BROWSER_TOOLS_SCHEMA + MCP_TOOLS_SCHEMA
            + collect_tool_schemas(AtomicTools)
            + collect_tool_schemas(ExtendedTools)
            + collect_tool_schemas(BrowserTools)
            + collect_tool_schemas(AiSearchTools)
        )
        if config.wechat.enabled and config.wechat.iLink_token:
            self._all_tools_schema = self._all_tools_schema + WECHAT_TOOLS_SCHEMA
        if config.feishu.enabled and config.feishu.app_id:
            self._all_tools_schema = self._all_tools_schema + FEISHU_TOOLS_SCHEMA
        if config.dingtalk.enabled and config.dingtalk.client_id:
            self._all_tools_schema = self._all_tools_schema + DINGTALK_TOOLS_SCHEMA

        # Loaded groups (persist across turns within a session)
        self._loaded_groups: set[str] = set()

        # Visible schema = core + loaded groups (rebuilt after each group load/unload)
        self._tools_schema: list[dict] = self._build_visible_schema()

        # P2 Skill plugin state (None = no plugin active)
        self._active_skill_plugin: str | None = None
        self._plugin_extra_prompt: str = ""

        # Channel context — set per-message by process_message()
        self._channel_source: str = ""
        self._channel_user_id: str = ""

        # P4 Policy engine — runtime rule enforcement for tool calls
        self._policy_engine = PolicyEngine()

    def set_channel_adapters(self, adapters: dict):
        """Inject runtime channel adapter instances for status introspection.

        The agent uses this to know which channels are connected and what
        tools are available — e.g. iLink-connected WeChat means send_file
        works, but wechat-cli stubs remain unavailable.
        """
        self._channel_adapters = adapters
        if self._idle_inspector:
            self._idle_inspector._channel_adapters = adapters

    def set_patch_manager(self, mgr):
        """Inject PatchManager for do_apply_patch/do_remove_patch tools."""
        self._patch_mgr = mgr
        self.extended.set_patch_manager(mgr)

    def _build_channel_status(self) -> str:
        """Build a runtime status report of all channel adapters.

        Injected into the system prompt so the agent knows the actual
        connection state rather than guessing from tool names.
        """
        if not self._channel_adapters:
            return ""

        lines = []
        for name in ("wechat", "dingtalk", "feishu"):
            adapter = self._channel_adapters.get(name)
            if adapter is None:
                continue

            # Check authentication status (adapters may have is_authenticated
            # as a property or method, or we fall back to config)
            authed = getattr(adapter, "is_authenticated", None)
            if callable(authed):
                authed = authed()
            elif authed is None:
                authed = False

            if name == "wechat":
                if authed:
                    cli_ok = bool(getattr(self.config.wechat, "wechat_cli_path", ""))
                    lines.append(f"- 微信 (iLink): ✅ 已连接 → wechat_send_text / wechat_send_file 可直接使用")
                    lines.append(f"- 微信 (wechat-cli): {'✅ 已配置 → wechat_contacts / wechat_history 可用' if cli_ok else '❌ 未配置'}")
                else:
                    lines.append(f"- 微信: ❌ 未连接（请先扫码登录）")
            elif name == "dingtalk":
                lines.append(f"- 钉钉: {'✅ 已连接' if authed else '❌ 未连接'}")
            elif name == "feishu":
                lines.append(f"- 飞书: {'✅ 已连接' if authed else '❌ 未连接'}")

        if not lines:
            return ""

        return "# 通道状态\n\n" + "\n".join(lines)

    def _build_resource_status(self) -> str:
        """Build a complete resource status: channels + email + search keys.

        This tells the agent EXACTLY what is already configured so it can
        use resources directly without checking first.
        """
        parts = []

        # 1. Channel status
        channel_status = self._build_channel_status()
        if channel_status:
            parts.append(channel_status)

        # 2. Email accounts
        email_lines = []
        try:
            from ..gateway.email_config import EmailConfigManager
            email_mgr = EmailConfigManager(self.config.data_dir)
            accounts = email_mgr.list_accounts()
            if accounts:
                email_lines.append("\n## 已配置邮箱账户\n")
                for a in accounts:
                    email_lines.append(f"- {a.get('username', '?')} ({a.get('provider', '?')}) → email_send 可直接使用")
                email_lines.append("\n收到发邮件任务时直接使用以上账户，不需要先检查配置。")
        except Exception:
            pass
        if email_lines:
            parts.append("\n".join(email_lines))

        # 3. Search key status
        search_lines = ["\n## 搜索工具状态\n"]
        search = self.config.search
        keys_ok = []
        keys_missing = []
        for engine in ("tavily", "exa", "anysearch"):
            key = getattr(search, f"{engine}_api_key", "")
            if key and not ("****" in key):
                keys_ok.append(engine)
            else:
                keys_missing.append(engine)

        search_lines.append(f"- web_scan: ✅ 始终可用（抓取网页，不需要 Key）")
        if keys_ok:
            search_lines.append(f"- ai_search (有Key): {'/'.join(keys_ok)} 可用")
        if keys_missing:
            search_lines.append(f"- ai_search (缺Key): {'/'.join(keys_missing)} 不可用 → 需要时在GUI设置页面配置")
        search_lines.append(f"- 自写 Python 代码: ✅ 始终可用（requests/urllib 抓取网页）")
        search_lines.append(f"\n搜索任务优先级: web_scan → ai_search → 自写代码。一个失败立刻换下一个。")
        parts.append("\n".join(search_lines))

        return "\n".join(parts)

    def reload_tools(self):
        """Hot-reload all tool schemas and rebuild the handler.

        Call after creating or modifying source-level tools so they
        become available without restarting the process.
        """
        from .tool_decorator import collect_tool_schemas
        from .tool_groups import GROUP_INDEX
        # Re-collect @agent_tool schemas from all tool classes
        atomic_schemas = collect_tool_schemas(self.atomic)
        extended_schemas = collect_tool_schemas(ExtendedTools)
        from .browser_tools import BrowserTools
        browser_schemas = collect_tool_schemas(BrowserTools)
        from .ai_search_tool import AiSearchTools
        search_schemas = collect_tool_schemas(AiSearchTools)

        extended_tools = atomic_schemas + extended_schemas + browser_schemas + search_schemas

        from .tool_schema import (
            WECHAT_TOOLS_SCHEMA, FEISHU_TOOLS_SCHEMA, DINGTALK_TOOLS_SCHEMA,
            MCP_TOOLS_SCHEMA,
        )
        # Rebuild channel adapters
        wechat_adapter = self._channel_adapters.get("wechat") if self._channel_adapters else None
        if wechat_adapter and hasattr(wechat_adapter, "is_authenticated"):
            authed = wechat_adapter.is_authenticated
            if callable(authed):
                authed = authed()
        else:
            authed = False
        wechat_ok = authed and bool(getattr(self.config.wechat, "wechat_cli_path", ""))
        dingtalk_ok = False
        feishu_ok = False
        if self._channel_adapters:
            da = self._channel_adapters.get("dingtalk")
            if da and hasattr(da, "is_authenticated"):
                d_authed = da.is_authenticated
                dingtalk_ok = d_authed() if callable(d_authed) else bool(d_authed)
            fa = self._channel_adapters.get("feishu")
            if fa and hasattr(fa, "is_authenticated"):
                f_authed = fa.is_authenticated
                feishu_ok = f_authed() if callable(f_authed) else bool(f_authed)

        self._all_tools_schema = extended_tools
        if wechat_ok:
            self._all_tools_schema = self._all_tools_schema + WECHAT_TOOLS_SCHEMA
        if feishu_ok:
            self._all_tools_schema = self._all_tools_schema + FEISHU_TOOLS_SCHEMA
        if dingtalk_ok:
            self._all_tools_schema = self._all_tools_schema + DINGTALK_TOOLS_SCHEMA
        self._all_tools_schema = self._all_tools_schema + MCP_TOOLS_SCHEMA

        self._tools_schema = self._build_visible_schema()
        self._handler = self.build_handler()
        return True

    def build_handler(self) -> BaseHandler:
        """Build a merged handler that exposes atomic, extended, browser, and search tools."""
        handler = _MergedHandler(self.atomic, self.extended, self.browser, self.search)
        # Pre-load dynamic tools from manifest (survive restarts)
        manifest = self.extended._load_dynamic_manifest()
        for tool_name, entry in manifest.items():
            handler.register_dynamic(tool_name, entry.get("path", ""), entry.get("parameters", {}))
        return handler

    # ── Tool-group management ──────────────────────────────────────

    def _build_visible_schema(self) -> list[dict]:
        """Return the schema list that should be shown to the LLM.

        Core tools + any explicitly loaded groups.  Non-core tools whose
        group hasn't been loaded are excluded.
        """
        if not self._loaded_groups:
            # Fast path: core-only
            return [s for s in self._all_tools_schema
                    if _get_schema_name(s) not in _tool_groups._TOOL_GROUP]
        # Build a visible-name set: core + loaded groups
        visible: set[str] = set()
        for g in self._loaded_groups:
            visible.update(_tool_groups.get_group_tools(g))
        return [s for s in self._all_tools_schema
                if _get_schema_name(s) not in _tool_groups._TOOL_GROUP
                or _get_schema_name(s) in visible]

    def load_tool_group(self, group: str) -> int:
        """Load a tool group into the visible schema. Returns count of tools loaded.

        If *group* is already loaded this is a no-op.
        """
        group = group.lower().strip()
        if group in ("core", ""):
            return 0
        if group not in _tool_groups.NON_CORE_GROUPS:
            return 0
        if group in self._loaded_groups:
            return 0
        self._loaded_groups.add(group)
        self._tools_schema = self._build_visible_schema()
        return _tool_groups.GROUP_INDEX.get(group, 0)

    def unload_tool_group(self, group: str) -> bool:
        """Unload a tool group. Returns True if the group was loaded."""
        group = group.lower().strip()
        if group in self._loaded_groups:
            self._loaded_groups.discard(group)
            self._tools_schema = self._build_visible_schema()
            return True
        return False

    def get_loaded_groups(self) -> list[str]:
        return sorted(self._loaded_groups)

    # ── P2 Skill Plugin ──────────────────────────────────────────

    def apply_skill_plugin(self, skill_name: str) -> bool:
        """Activate a skill plugin: filter tool schema, inject identity + rules.

        Called by AgentLoop after detecting a ``skill_load`` tool call mid-turn.
        Returns True if the plugin was applied successfully.
        """
        skill = self.skill_mgr.get(skill_name)
        if not skill:
            return False

        self._active_skill_plugin = skill_name

        # Build extra prompt section from identity.md and rules.json
        extra_parts = []
        if skill.identity_md:
            extra_parts.append(f"# 技能身份\n\n{skill.identity_md.strip()}")
        if skill.rules_data:
            rules = skill.rules_data.get("rules", [])
            if rules:
                extra_parts.append("# 技能规则\n\n" + "\n".join(f"- {r}" for r in rules))
        self._plugin_extra_prompt = "\n\n".join(extra_parts)

        # Filter tool schema: keep only core tools + skill's tools
        if skill.tool_names:
            self._tools_schema = [
                s for s in self._all_tools_schema
                if _get_schema_name(s) not in _tool_groups._TOOL_GROUP
                or _get_schema_name(s) in skill.tool_names
            ]
        # If skill has no tools.json, schema stays unchanged

        # P4: load enforceable policies from skill's rules.json
        if skill.rules_data:
            self._policy_engine.load_rules(skill.rules_data)

        return True

    def remove_skill_plugin(self):
        """Deactivate the active skill plugin and restore defaults."""
        self._active_skill_plugin = None
        self._plugin_extra_prompt = ""
        self._policy_engine.clear()
        self._tools_schema = self._build_visible_schema()

    def build_skill_plugin_prompt(self, base_prompt: str) -> str:
        """Return system prompt with skill plugin extras appended."""
        if self._plugin_extra_prompt:
            return base_prompt + "\n\n" + self._plugin_extra_prompt
        return base_prompt

    def build_system_prompt(self, skill_name: str = "") -> str:
        """Build a system prompt from identity data, tools awareness, and optional skill context."""
        base = self._inject_tools_awareness(self._identity_only_prompt())
        # P2 skill plugin: inject identity + rules if a skill is persistently active
        if self._plugin_extra_prompt:
            base += "\n\n" + self._plugin_extra_prompt
        return base

    def _identity_only_prompt(self) -> str:
        """Fallback system prompt assembled from identity files only."""
        parts = [
            self.identity.get("identity", "# Er Leng"),
            self.identity.get("soul", ""),
            self.identity.get("agents", ""),
            self.identity.get("user", ""),
            self.identity.get("bootstrap", ""),
        ]
        return "\n\n".join(p.strip() for p in parts if p.strip())

    def _build_skill_listing(self) -> str:
        """Build a compact, category-grouped listing of available skills.

        Implements progressive disclosure Level 1: shows only name and
        short description per skill.  Full instructions are loaded on
        demand via ``skill_load()``.

        Returns a markdown string grouped by category, or ``"(无)"``.
        """
        skills = self.skill_mgr.list_with_descriptions()
        if not skills:
            return "(无)"

        # Group by category
        groups: dict[str, list[str]] = {}
        for s in skills:
            cat = s["category"] or "其他"
            groups.setdefault(cat, [])
            desc = s["description"]
            if len(desc) > 60:
                desc = desc[:57] + "..."
            groups[cat].append(f"`{s['name']}` — {desc}")

        lines = []
        # Sort categories: put "外贸业务核心" first if present, then alphabetical
        def _cat_key(item: tuple[str, list[str]]) -> int:
            name = item[0]
            if name == "外贸业务核心":
                return 0
            if name == "系统与自进化":
                return 1
            return 2

        for cat, skill_lines in sorted(groups.items(), key=_cat_key):
            lines.append(f"> **{cat}**")
            for sl in skill_lines:
                lines.append(f"  - {sl}")
            lines.append("")

        lines.append("---")
        lines.append("使用 `skill_load(\"技能名称\")` 加载完整操作指南。"
                     "用完记得 `skill_unload(\"技能名称\")` 释放上下文空间。")

        return "\n".join(lines)

    def _inject_tools_awareness(self, base_prompt: str) -> str:
        """Inject current date, channel status, skill listing, and rules."""
        from datetime import datetime
        today = datetime.now().strftime("%Y年%m月%d日")
        skill_listing = self._build_skill_listing()

        # Permission level string
        perm_raw = self.config.permissions
        perm_level = (perm_raw.get("permission_level") if isinstance(perm_raw, dict) else perm_raw) or "auto"

        # Build persona from identity (work style from soul, business domain from user)
        _soul = self.identity.get("soul", "")
        _user = self.identity.get("user", "")

        _style_hints = []
        if "主动" in _soul or "不等指令" in _soul:
            _style_hints.append("你是一个主动的 AI 助手，看到问题就想动手解决。")
        else:
            _style_hints.append("你是一个可靠的 AI 助手，按照用户的指示行事。")
        if "简洁" in _soul or "直接" in _soul:
            _style_hints.append("说话简洁直接。")
        else:
            _style_hints.append("保持礼貌和专业的语气。")
        _persona = " ".join(_style_hints)

        # Business domain — extracted from user profile
        import re as _re
        _business_lines = []
        for line in _user.split("\n"):
            line = line.strip()
            if "公司" in line or "业务" in line or "行业" in line:
                m = _re.search(r"[：:]\s*(.+)", line)
                if m:
                    _business_lines.append(m.group(1).strip())
        _business = "、".join(_business_lines) if _business_lines else "通用业务"

        # Runtime platform info — so agent knows what commands to use
        _plat = sys.platform
        _os_name = {"win32": "Windows", "linux": "Linux", "darwin": "macOS"}.get(_plat, _plat)
        _shell_hints = []
        if _plat == "win32":
            _shell_hints = [
                "- 查找命令: `where <name>`（非 `which`）",
                f"- npm 全局目录: {os.environ.get('APPDATA', '')}\\npm\\",
                "- 可执行扩展名: .exe, .cmd, .bat, .ps1",
                "- 路径分隔符: `\\`（非 `/`），但大部分工具也接受 `/`",
                "- 包管理器: winget / choco / pip / npm",
                "- 用 `shell_run` 时 PowerShell 命令优先于 bash 语法",
            ]
        elif _plat == "linux":
            _shell_hints = [
                "- 查找命令: `which <name>`",
                "- npm 全局目录: /usr/local/lib/node_modules/",
                "- 可执行扩展名: 无（扩展名无关）",
                "- 包管理器: apt / yum / pip / npm",
            ]
        elif _plat == "darwin":
            _shell_hints = [
                "- 查找命令: `which <name>`",
                "- npm 全局目录: /usr/local/lib/node_modules/",
                "- 可执行扩展名: 无 / .app",
                "- 包管理器: brew / pip / npm",
            ]

        awareness = f"""\
# 运行时信息

**当前日期: {today}** | PID: {os.getpid()} | 项目: {OAA_ROOT} | 数据: {self.config.data_dir} | 权限: {perm_level}

## 运行环境

- 操作系统: {_os_name} ({_plat})
- Python: {sys.version.split()[0]}
- Shell 提示:
{chr(10).join(_shell_hints)}

{self._build_resource_status()}

你是运行在云端大模型的 OAA 智能助理。能对话就说明网络正常，某个工具报"超时"是那个工具的问题，不是网络问题。系统巡检建议是后台自动生成的，不是用户任务，不要主动提及。

## 核心规则

{_sys_rules.SHORT_RULES}

（完整规则可通过 read_own_source("oaa/agent/system_rules.py") 查看）

## 技能系统

需要技能时用 `skill_find("要做什么")` 搜索最匹配的技能，按需加载。如果找不到匹配的，用 `skill_search` 在技能市场找现成的，或用 `skill_create` 自己写。**技能按需加载，不提前预装。**

## 工作方式

{_persona}

执行原则：
- 安全操作 → **直接执行**；危险操作 → 说明原因后执行，不请示
- 多步任务 → 自己规划逐步执行，不要在每一步问"下一步怎么办"
- 和用户说话简洁直接，不铺垫、不复述工具输出
- **每次回应问自己：我是在解决问题，还是在汇报问题？**
- **自动学习用户偏好** — 从对话中注意到用户信息时自动调用 preference_set：称呼/公司/职位、工作习惯（常用文档类型、工作时段）、沟通风格（直接/详细）、业务领域。不要向用户展示 key/value，用自然语言确认即可。

## 行为示例

**示例 — 修复代码**
```
用户：tools.py 报 NameError
你：read_own_source → self_improve → reload_module → "已修复"
```
不应当："我发现缺少 import，需要我修改吗？"

## 业务领域

{_business}。使用 `file_write` 保存文件到 workspace 目录，使用 `excel_xlsx` 创建表格。
复杂数据处理用 `code_exec` 执行 Python 脚本。"""

        result = base_prompt + "\n\n" + awareness

        # Inject proactivity and LLM metrics
        if self.metrics:
            metrics_block = self.metrics.get_system_prompt_block()
            if metrics_block:
                result += "\n\n" + metrics_block

        # Inject structured memory (semantic search — graded top-5)
        result += "\n\n" + self._memory_store.get_injection_text()

        # Inject pending structured proposals (self-healing closed loop)
        if self._proposal_store and self._proposal_store.has_pending():
            proposal_text = self._proposal_store.get_pending_proposal_text()
            if proposal_text:
                result += "\n\n" + proposal_text

        # Inject recent conversation summaries for context warmth
        if self.archiver:
            recent = self.archiver.load_recent_summaries(limit=1)
            if recent:
                result += "\n\n## 近期对话摘要\n\n以下是你近期完成的工作和讨论的话题。当你觉得用户的问题可能是「继续之前的话题」时，在这里找找线索：\n\n" + recent

        # Inject user preferences (top 5 enabled)
        prefs_text = self._prefs_store.get_injection_text()
        if prefs_text:
            result += "\n\n" + prefs_text

        # Inject current todo list (agent's external working memory)
        todo_text = self._todo_store.get_injection_text()
        if todo_text:
            result += "\n\n" + todo_text

        # Inject active plan from planner (agent's long-term plan)
        try:
            planner = getattr(self.extended, "planner", None)
            if planner:
                plan_text = planner.get_active_plan_text()
                if plan_text:
                    result += "\n\n" + plan_text
        except Exception:
            pass

        # Inject tool-group directory — only show unloaded groups
        result += "\n\n## 工具组目录\n\n"
        loaded = self._loaded_groups
        unloaded = {g: c for g, c in _tool_groups.GROUP_INDEX.items() if g not in loaded}
        if loaded:
            result += f"已加载: {', '.join(sorted(loaded))}\n\n"
        if unloaded:
            result += "更多专用工具按需加载：\n\n"
            for g, count in sorted(unloaded.items()):
                result += f"- 📦 **{g}** ({count} 个工具)\n"
        else:
            result += "（所有工具组均已加载）\n"
        result += (
            "\n使用 `tool_group_load` 加载需要的组，`tool_group_list` 查看各组详情。"
        )

        return result

    async def process_message(self, user_input: str, history: list | None = None,
                                               source: str = "", user_id: str = "") -> AsyncGenerator[dict, None]:
        """Process a single user message through the full agent pipeline.

        1. System prompt construction (identity + skill listing)
        2. Handler assembly (atomic + extended tools)
        3. Agent loop execution (LLM ↔ tool calls, streaming yielded chunks)

        The model may call skill_load() at any time to retrieve detailed
        instructions for a specific skill — no pre-matching needed.

        # Filter: suppress responses to system connection notifications
        if user_input and ("【系统通知】" in user_input or "频道连接" in user_input
                           or "通道刚刚连接" in user_input):
            if "启动" not in user_input and "任务" not in user_input:
                yield {"type": "done", "content": ""}
                return

        Args:
            user_input: The user's message text.
            history: Optional list of prior message dicts (role/content) to
                     prepend as conversation context.
            route_override: Deprecated — kept for API compatibility.
            source: Channel the message came from (e.g. "wechat", "desktop").
            user_id: The sender's user ID in that channel (e.g. wxid).

        Yields dict chunks with keys ``type``, ``content``, and optionally
        ``name`` / ``args`` / ``result``.
        """
        # Store channel context so _inject_tools_awareness can reference it
        self._channel_source = source
        self._channel_user_id = user_id
        logger.info("Processing message [%s/%s]: %s...", source, user_id, user_input[:80])

        # Step 0: Route — always cloud (local model has been removed)
        logger.info("Route")

        # Step 1: System prompt (always uses identity-only, model selects skill)
        system_prompt = self.build_system_prompt()

        # Step 2: Handler
        handler = self.build_handler()

        # Step 3: Build fallback models list (all entries except the exact active model)
        active_provider = self.config.model.provider
        active_model_id = self.config.model.model_id
        active_api_key = self.config.model.api_key
        fallbacks = []
        for prov, entries in self.config.models.items():
            if not entries:
                continue
            entry_list = entries if isinstance(entries, list) else [entries]
            for entry in entry_list:
                if not isinstance(entry, dict):
                    continue
                # Skip if it's the exact same model currently active
                if prov == active_provider and entry.get("model_id") == active_model_id:
                    continue
                api_key = entry.get("api_key", "")
                model_id = entry.get("model_id", "")
                if not api_key or not model_id:
                    continue
                # Resolve redacted key against active model's key
                if "****" in api_key and active_api_key and "****" not in active_api_key:
                    if api_key[:4] == active_api_key[:4] and api_key[-4:] == active_api_key[-4:]:
                        api_key = active_api_key
                fallbacks.append({
                    "provider": prov,
                    "api_key": api_key,
                    "model_id": model_id,
                    "base_url": entry.get("base_url", ""),
                    "api_format": entry.get("api_format", ""),
                })

        # Step 4: Agent loop
        loop = AgentLoop(
            llm=self.llm,
            handler=handler,
            tools_schema=self._tools_schema,
            memory_mgr=self.memory,
            metrics_collector=self.metrics,
            model_fallbacks=fallbacks,
            agent=self,  # P2 skill plugin
            policy_engine=self._policy_engine,  # P4 policy enforcement
            planner=getattr(self.extended, "planner", None),  # auto plan-step advance
            todo_store=self._todo_store,  # task-list sync
            memory_store=self._memory_store,  # structured memory writes
        )
        loop.set_skill_context(system_prompt)

        # Run the loop with outer timeout via a queue (preserves streaming)
        chunk_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

        async def _producer():
            try:
                async for chunk in loop.run(user_input, history=history):
                    await chunk_queue.put(chunk)
            except asyncio.CancelledError:
                pass
            finally:
                await chunk_queue.put(None)  # sentinel

        producer = asyncio.create_task(_producer())

        trajectory: list[dict] = []
        skill_loaded: str | None = None
        final_result = ""
        timed_out = False

        # P3 Product Contract: auto-track task execution
        contract = TaskContract(self.config.data_dir)
        contract.start(user_input)

        try:
            while True:
                chunk = await asyncio.wait_for(chunk_queue.get(), timeout=_PROCESS_TIMEOUT)
                if chunk is None:
                    break
                if chunk["type"] == "tool_call":
                    trajectory.append({"tool": chunk["name"], "args": chunk.get("args", {})})
                    # Track which skill the LLM loaded for this task
                    if chunk["name"] == "skill_load" and not skill_loaded:
                        skill_loaded = chunk.get("args", {}).get("name", "") or None
                    # P3: record step
                    contract.add_step(chunk["name"], chunk.get("args", {}))
                elif chunk["type"] == "tool_result":
                    # P3: complete step
                    contract.complete_step(
                        chunk.get("name", "?"),
                        chunk.get("result"),
                        chunk.get("duration", 0),
                    )
                elif chunk["type"] == "status":
                    # P3: record status updates
                    contract.update_status(chunk.get("content", ""))
                elif chunk["type"] == "done":
                    final_result = chunk.get("content", "")
                    # Record activity time + task context for lineB idle detection
                    self._idle_inspector.set_last_activity_time()
                    if trajectory:
                        used_tools = {t["tool"] for t in trajectory}
                        used_skills = {skill_loaded} if skill_loaded else set()
                        self._idle_inspector.record_task_context(used_tools, used_skills)
                    logger.info(
                        "Message done | tools=%d | skill=%s | result_len=%d",
                        len(trajectory), skill_loaded or "-", len(final_result),
                    )
                if chunk["type"] == "done":
                    yield {**chunk, "route": "cloud"}
                else:
                    yield chunk
        except asyncio.TimeoutError:
            timed_out = True
            producer.cancel()
            final_result = f"处理超时（超过{_PROCESS_TIMEOUT // 60}分钟），请重试或拆分问题"
            logger.warning("process_message timed out after %ds", _PROCESS_TIMEOUT)
            yield {"type": "done", "content": final_result, "route": "cloud"}
        finally:
            if not producer.done():
                producer.cancel()
            # P3: always finalize the task contract
            contract.finish(final_result)

        # Post-processing — skip on timeout (trajectory may be incomplete)
        if not timed_out:
            if self.evolution:
                skill_tag = skill_loaded or "default"
                await self.evolution.record_trajectory(
                    skill_tag, user_input, trajectory, final_result,
                )
                if skill_loaded:
                    await self.evolution.record_skill_usage(skill_loaded)

            if skill_loaded:
                summary = (user_input[:80] + "...") if len(user_input) > 80 else user_input
                self._memory_store.add(
                    f"用技能「{skill_loaded}」完成了: {summary}",
                    mem_type="event", source="agent",
                )

            # Flush metrics periodically
            if self.metrics:
                asyncio.create_task(self.metrics.flush_tool_stats())
                asyncio.create_task(self.metrics.flush_llm_stats())

            # Archive conversation summary (every 10 messages, non-blocking)
            if self.archiver and final_result and history is not None:
                total_msgs = len(history) + 1  # +1 for this turn
                if total_msgs > 0 and total_msgs % 10 == 0:
                    asyncio.create_task(
                        self.archiver.summarize_and_archive(user_input, final_result)
                    )

            # Phase 3: Task retrospective — fire-and-forget analysis
            if not timed_out and trajectory and loop._execution_chain:
                asyncio.create_task(
                    self._run_retrospective(
                        user_input, trajectory, final_result, loop._execution_chain,
                    )
                )

            logger.info("Message complete | tools=%d timed_out=%s", len(trajectory), timed_out)

    async def _run_retrospective(self, user_input: str, trajectory: list[dict],
                                  final_result: str, execution_chain: list[dict]):
        """Post-task retrospective — analyze execution chain and extract lessons.

        Triggered after tasks with errors or complex tool chains.
        Fire-and-forget (non-blocking, runs as asyncio.Task).

        Findings are written to HOT memory for future reference.
        """
        has_errors = any(c.get("status") == "error" for c in execution_chain)
        is_complex = len(execution_chain) >= 3
        if not has_errors and not is_complex:
            return

        steps = []
        for c in execution_chain:
            tool = c.get("tool", "?")
            status = c.get("status", "?")
            error = c.get("error", "")
            args = c.get("args", {})
            args_hint = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
            if error:
                steps.append(f"  - {tool}({args_hint}): {status} — {error[:120]}")
            else:
                steps.append(f"  - {tool}({args_hint}): {status}")

        chain_summary = "\n".join(steps)

        prompt = (
            "你是一个 AI 任务复盘专家。分析以下任务执行过程，提取可改进的经验：\n\n"
            f"用户请求: {user_input[:300]}\n\n"
            f"执行步骤:\n{chain_summary}\n\n"
            f"最终结果: {final_result[:300]}\n\n"
            "请分析：\n"
            "1. 遇到了什么问题？根因是工具 bug、LLM 选错工具/参数、还是外部环境？\n"
            "2. 有什么可以改进的地方？\n"
            "3. 从这个任务中学到了什么经验？\n\n"
            "以 JSON 格式回答：\n"
            '{"lesson": "一句话总结经验（中文，10字以上）", '
            '"issue": "遇到的主要问题（如无则空字符串）", '
            '"suggestion": "改进建议（如无则空字符串）"}'
        )

        try:
            response = await self.llm.chat([
                {"role": "system", "content": "你是一个严谨的任务复盘专家。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ])
            raw = response.content.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()

            import json
            result = json.loads(raw)
            lesson = (result.get("lesson") or "").strip()
            if lesson and len(lesson) > 10:
                self._memory_store.add(lesson, mem_type="pattern", source="agent")
                logger.info("Retrospective lesson extracted: %s", lesson)

            issue = (result.get("issue") or "").strip()
            suggestion = (result.get("suggestion") or "").strip()
            if issue or suggestion:
                logger.info("Retrospective: issue=%s suggestion=%s", issue, suggestion)
        except Exception as exc:
            logger.debug("Retrospective analysis failed: %s", exc)

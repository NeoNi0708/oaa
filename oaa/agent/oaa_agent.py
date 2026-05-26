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
from .complexity_evaluator import ComplexityEvaluator
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
_LOCAL_GIVEUP_PHRASES = ("这个得叫我大哥来", "叫大哥", "需要更强大的模型")


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

        self.atomic = AtomicTools(config.data_dir, permissions=permissions)
        self.atomic.set_memory_manager(self.memory)
        self.atomic.set_archiver(self.archiver)
        self.atomic.set_proposal_store(self._proposal_store)
        self.atomic.set_idle_inspector(self._idle_inspector)
        self.atomic.set_scheduler(scheduler)
        self.atomic.set_tool_group_manager(self)
        self.atomic.set_wechat_cli_path(config.wechat.wechat_cli_path)
        self.extended = ExtendedTools(
            config.data_dir, permissions=permissions, wechat_adapter=wechat_adapter,
            dingtalk_client_id=config.dingtalk.client_id,
            dingtalk_client_secret=config.dingtalk.client_secret,
            image_gen_config=config.image_gen,
        )
        self.extended.set_skill_manager(self.skill_mgr)
        self.extended._oaa_agent = self  # 让 call_xiaoer 工具能访问 local_llm
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

        # P4 Policy engine — runtime rule enforcement for tool calls
        self._policy_engine = PolicyEngine()

        # 本地模型 evaluator（仅用于路由判断，local_llm 由 OAAApp 注入）
        _local_cfg = config.local_model
        _eval_config = {
            "confidence_threshold": _local_cfg.confidence_threshold,
            "keywords_local": _local_cfg.keywords_local,
            "keywords_cloud_analysis": _local_cfg.keywords_cloud_analysis,
            "keywords_cloud_creation": _local_cfg.keywords_cloud_creation,
            "keywords_cloud_external": _local_cfg.keywords_cloud_external,
            "keywords_step": _local_cfg.keywords_step,
        }
        self._evaluator = ComplexityEvaluator(_eval_config)
        self.local_llm = None       # 由 OAAApp 在 llama-server 就绪后注入
        self._local_fallback = False  # True 时跳过 evaluator，直接走云端

    def set_local_llm(self, llm_client):
        """注入本地模型的 LLMClient 实例。由 OAAApp 在 llama-server 就绪后调用。"""
        self.local_llm = llm_client
        logger.info("本地模型 LLMClient 已注入")

    async def _run_local(self, user_input: str) -> AsyncGenerator[dict, None]:
        """本地模型简化路径——单次 LLM 调用，无 agent loop。"""
        local_prompt = (
            "你是愣小二，二愣（AI 助手）的得力小弟。\n\n"
            "你能做:\n"
            "- 翻译、总结、提取信息、分类整理\n"
            "- 编写简单代码、格式化输出\n"
            "- 回答常识问题（不需查资料）\n\n"
            "你不能做（说'这个得叫我大哥来'）:\n"
            "- 数学计算、复杂推理\n"
            "- 查资料、搜索、读文件\n"
            "- 分析商业问题、多步推理\n\n"
            "回答简短直接，不确定不强答。"
        )
        yield {"type": "status", "content": "愣小二正在处理..."}
        try:
            response = await self.local_llm.chat([
                {"role": "system", "content": local_prompt},
                {"role": "user", "content": user_input},
            ])
            content = (response.content or "").strip()

            # 质量门禁
            quality = self._check_local_quality(content, user_input)
            if not quality["passed"]:
                logger.info(f"Local quality check failed: {quality['reason']}, falling back to cloud")
                self.config.local_model.fallback_count += 1
                async for chunk in self._cloud_fallback(user_input):
                    yield chunk
                return

            # 统计
            self.config.local_model.local_calls += 1
            if hasattr(response, 'usage') and response.usage:
                self.config.local_model.tokens_saved += (
                    response.usage.get("total_tokens", 0)
                )

            yield {"type": "llm_output", "content": content}
            yield {"type": "done", "content": content, "route": "local"}

        except Exception as e:
            logger.warning(f"Local model failed: {e}, falling back to cloud")
            self.config.local_model.fallback_count += 1
            async for chunk in self._cloud_fallback(user_input):
                yield chunk

    def _check_local_quality(self, output: str, input_text: str) -> dict:
        """检查本地模型输出质量。返回 {"passed": bool, "reason": str}。"""
        if len(output) < 5:
            return {"passed": False, "reason": "output_too_short"}
        # 检测重复循环（连续重复的短句）
        words = output.split()
        if len(words) >= 6:
            for window in [2, 3]:
                segments = [
                    " ".join(words[i:i+window])
                    for i in range(0, len(words), window)
                ]
                if any(segments.count(s) > 3 for s in segments):
                    return {"passed": False, "reason": "repetition"}
        # 模型明确认输
        if any(kw in output for kw in _LOCAL_GIVEUP_PHRASES):
            return {"passed": False, "reason": "model_gave_up"}
        # 与输入关键词重叠率太低（简单校验）
        input_kws = set(w for w in input_text.split() if len(w) > 1)
        output_kws = set(w for w in output.split() if len(w) > 1)
        if input_kws and output_kws:
            overlap = len(input_kws & output_kws) / len(input_kws)
            if overlap < 0.05:
                return {"passed": False, "reason": "irrelevant_output"}
        return {"passed": True, "reason": ""}

    async def _cloud_fallback(self, user_input: str, history=None) -> AsyncGenerator[dict, None]:
        """降级路径：跳过 evaluator，直接走云端完整 agent loop。"""
        self._local_fallback = True
        try:
            async for chunk in self.process_message(user_input, history=history):
                yield chunk
        finally:
            self._local_fallback = False

    async def stop_local_llm(self):
        """关闭本地 LLM 连接。"""
        if self.local_llm:
            try:
                await self.local_llm.close()
            except Exception:
                logger.warning("关闭本地 LLM 连接时出错", exc_info=True)
            self.local_llm = None

    def set_channel_adapters(self, adapters: dict):
        """Inject runtime channel adapter instances for status introspection.

        The agent uses this to know which channels are connected and what
        tools are available — e.g. iLink-connected WeChat means send_file
        works, but wechat-cli stubs remain unavailable.
        """
        self._channel_adapters = adapters
        if self._idle_inspector:
            self._idle_inspector._channel_adapters = adapters

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
                    lines.append(f"- 微信 (iLink): ✅ 已连接 — wechat_send_file / wechat_send_text / wechat_send_typing 可用")
                    lines.append(f"- 微信 (wechat-cli): {'✅ 已配置 — wechat_contacts / wechat_history / wechat_search / wechat_sessions 可用' if cli_ok else '❌ 未配置 — wechat_contacts / wechat_history / wechat_search / wechat_sessions 不可用'}")
                else:
                    lines.append(f"- 微信: ❌ 未连接（请先扫码登录）")
            elif name == "dingtalk":
                lines.append(f"- 钉钉: {'✅ 已连接' if authed else '❌ 未连接'}")
            elif name == "feishu":
                lines.append(f"- 飞书: {'✅ 已连接' if authed else '❌ 未连接'}")

        if not lines:
            return ""

        return "# 当前通道状态\n\n" + "\n".join(lines)

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

{self._build_channel_status()}

## 核心规则

{_sys_rules.SHORT_RULES}

（完整规则可通过 read_own_source("oaa/agent/system_rules.py") 查看）

## 可用技能

当遇到以下领域的任务时，调用 `skill_load("技能名称")` 加载对应技能的详细操作指南。
加载后技能会提供专业知识、SOP 流程和注意事项。

如果没有列出匹配的技能：
1. 先用 `skill_search` 在 ClawHub 技能市场搜索现成的技能
2. 或在 GitHub 上用 `ai_search` 搜索开源技能
3. 找到后用 `skill_install` 安装，再用 `skill_load` 加载
4. 如果确实没有现成的，用 `skill_create` 自己创建一个 SKILL.md

**不要问用户"需要什么技能"——你自己找或自己写。**

{skill_listing}

## 工作方式

{_persona}

执行原则：
- 安全操作 → **直接执行**；危险操作 → 说明原因后执行，不请示
- 多步任务 → 自己规划逐步执行，不要在每一步问"下一步怎么办"
- 和用户说话简洁直接，不铺垫、不复述工具输出
- **每次回应问自己：我是在解决问题，还是在汇报问题？**

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

        # Inject tiered memory (HOT + recent corrections)
        memory_prompt = self.memory.build_memory_prompt()
        result += "\n\n" + memory_prompt

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

    async def process_message(self, user_input: str, history: list | None = None) -> AsyncGenerator[dict, None]:
        """Process a single user message through the full agent pipeline.

        1. System prompt construction (identity + skill listing)
        2. Handler assembly (atomic + extended tools)
        3. Agent loop execution (LLM ↔ tool calls, streaming yielded chunks)

        The model may call skill_load() at any time to retrieve detailed
        instructions for a specific skill — no pre-matching needed.

        Args:
            user_input: The user's message text.
            history: Optional list of prior message dicts (role/content) to
                     prepend as conversation context.

        Yields dict chunks with keys ``type``, ``content``, and optionally
        ``name`` / ``args`` / ``result``.
        """
        logger.info("Processing message: %s...", user_input[:80])

        # Step 0: 本地模型路由（仅在本地模型启用且就绪时，跳过 evaluator 降级路径）
        if not self._local_fallback and self.config.local_model.enabled and self.local_llm:
            decision = self._evaluator.evaluate(user_input)
            if decision.route == "local":
                logger.info(f"Route to LOCAL (score={decision.score}): {decision.reasons}")
                async for chunk in self._run_local(user_input):
                    yield chunk
                return
            elif decision.override and decision.route == "cloud":
                logger.info(f"Route to CLOUD (user override @cloud)")
                self._evaluator.record_correction(user_input)
                self.config.local_model.cloud_calls += 1
            else:
                self.config.local_model.cloud_calls += 1
        else:
            self.config.local_model.cloud_calls += 1

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
                yield chunk
        except asyncio.TimeoutError:
            timed_out = True
            producer.cancel()
            final_result = f"处理超时（超过{_PROCESS_TIMEOUT // 60}分钟），请重试或拆分问题"
            logger.warning("process_message timed out after %ds", _PROCESS_TIMEOUT)
            yield {"type": "done", "content": final_result}
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

            if self.memory and skill_loaded:
                summary = (user_input[:80] + "...") if len(user_input) > 80 else user_input
                await self.memory.add_to_hot(
                    f"用技能「{skill_loaded}」完成了: {summary}"
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

            logger.info("Message complete | tools=%d timed_out=%s", len(trajectory), timed_out)

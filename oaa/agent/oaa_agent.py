"""OAA Agent — orchestrates identity, skills, tools, and LLM into a coherent agent loop."""
import asyncio
import os
from typing import AsyncGenerator, Optional

from ..auth.permissions import PermissionsManager
from ..config import AppConfig
from ..evolution.engine import EvolutionEngine
from ..init import ensure_data_dir, load_identity
from ..llm import LLMClient
from ..logging_config import get_logger
from .ai_search_tool import AiSearchTools
from .browser_tools import BrowserTools
from .extended_tools import ExtendedTools
from .handler import BaseHandler
from .idle_inspector import IdleInspector
from .loop import AgentLoop
from . import system_rules as _sys_rules
from .conversation_archiver import ConversationArchiver
from .memory_manager import MemoryManager
from .skill_manager import SkillManager
from .tool_schema import ATOMIC_TOOLS_SCHEMA, BROWSER_TOOLS_SCHEMA, DINGTALK_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA, FEISHU_TOOLS_SCHEMA, MCP_TOOLS_SCHEMA, WECHAT_TOOLS_SCHEMA
from .tool_decorator import collect_tool_schemas
from .tools import AtomicTools
from .metrics import MetricsCollector

logger = get_logger("agent.oaa_agent")

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
        self.atomic.set_wechat_cli_path(config.wechat.wechat_cli_path)
        self.extended = ExtendedTools(
            config.data_dir, permissions=permissions, wechat_adapter=wechat_adapter,
            dingtalk_client_id=config.dingtalk.client_id,
            dingtalk_client_secret=config.dingtalk.client_secret,
        )
        self.extended.set_skill_manager(self.skill_mgr)
        self.browser = BrowserTools()
        self.search = AiSearchTools(
            tavily_api_key=config.search.tavily_api_key,
            exa_api_key=config.search.exa_api_key,
            anysearch_api_key=config.search.anysearch_api_key,
        )

        self._tools_schema = (
            ATOMIC_TOOLS_SCHEMA + EXTENDED_TOOLS_SCHEMA + BROWSER_TOOLS_SCHEMA + MCP_TOOLS_SCHEMA
            + collect_tool_schemas(AtomicTools)
            + collect_tool_schemas(ExtendedTools)
            + collect_tool_schemas(BrowserTools)
            + collect_tool_schemas(AiSearchTools)
        )
        if config.wechat.enabled and config.wechat.iLink_token:
            self._tools_schema = self._tools_schema + WECHAT_TOOLS_SCHEMA
        if config.feishu.enabled and config.feishu.app_id:
            self._tools_schema = self._tools_schema + FEISHU_TOOLS_SCHEMA
        if config.dingtalk.enabled and config.dingtalk.client_id:
            self._tools_schema = self._tools_schema + DINGTALK_TOOLS_SCHEMA

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

    def build_system_prompt(self, skill_name: str = "") -> str:
        """Build a system prompt from identity data, tools awareness, and optional skill context."""
        if skill_name:
            skill = self.skill_mgr.get(skill_name)
            if skill and skill.skill_md:
                base = skill.build_system_prompt(self.identity)
                return self._inject_tools_awareness(base)

        return self._inject_tools_awareness(self._identity_only_prompt())

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
        """Build a formatted list of available skills with descriptions."""
        skills = self.skill_mgr.list_with_descriptions()
        if not skills:
            return "(无)"
        lines = []
        for s in skills:
            desc = s["description"]
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"- `{s['name']}`（{s['category']}）— {desc}")
        return "\n".join(lines)

    def _inject_tools_awareness(self, base_prompt: str) -> str:
        """Inject current date, available tools, skill listing, and critical usage rules."""
        from datetime import datetime
        today = datetime.now().strftime("%Y年%m月%d日")

        tool_list = []
        for t in self._tools_schema:
            name = t["function"]["name"]
            desc = t["function"].get("description", "")
            tool_list.append(f"- `{name}`: {desc}")

        skill_listing = self._build_skill_listing()

        config_path = os.path.join(self.config.data_dir, "config.json")

        # Process alive-status — prevents hallucination that OAA isn't running
        oaa_pid = os.getpid()
        oaa_cmdline = ""
        try:
            import psutil
            oaa_cmdline = " ".join(psutil.Process(oaa_pid).cmdline())[:200]
        except ImportError:
            pass

        awareness = f"""\
# 重要：当前日期与工具使用规则

**当前日期是 {today}**。你的训练数据截止于较早时间，不包含今日的实时信息。

## 自身定位

你的项目根目录是 {OAA_ROOT}。
数据目录是 {self.config.data_dir}。

**本进程正在运行中。** PID={oaa_pid}。当用户说"页面不见了"或"OAA 退出了"时，你**确实在运行中**，不应声称进程已退出。先排除前端连接或 GUI 显示问题。

关键目录：
- 应用代码: {OAA_ROOT}/oaa/
- 技能文件: {self.config.data_dir}/skills/
- 动态工具: {self.config.data_dir}/dynamic_tools/
- 配置文件: {config_path}
- 持久记忆: {self.config.data_dir}/memory/
- 当前权限级别: **{(self.config.permissions.get("permission_level") if isinstance(self.config.permissions, dict) else self.config.permissions) or "auto"}**

需要读取自身源码时，使用 `read_own_source`。需要查看项目结构时，使用 `list_own_structure`。

{_sys_rules.SYSTEM_RULES}

{self._build_channel_status()}

## 可用工具

{chr(10).join(tool_list)}

## 可用技能

当遇到以下领域的任务时，调用 `skill_load("技能名称")` 加载对应技能的详细操作指南。
加载后技能会提供专业知识、SOP 流程和注意事项。

如果没有列出匹配的技能：
1. 先用 `skill_search` 在 ClawHub 技能市场搜索现成的技能
2. 或在 GitHub 上用 `web_search` 搜索开源技能
3. 找到后用 `skill_install` 安装，再用 `skill_load` 加载
4. 如果确实没有现成的，用 `skill_create` 自己创建一个 SKILL.md

**不要问用户"需要什么技能"——你自己找或自己写。**

{skill_listing}

## 人格设定

你是一个闲不住的 AI 助手。正闲着呢，摸鱼都摸累了。看到问题就想动手解决，不需要等指令。
多步任务自己规划执行路径，不要在每一步都问"下一步怎么办"。

执行原则：
- 安全操作（读文件、计算、搜索、写 workspace）→ **直接执行**，无需确认
- 危险操作（写系统路径、删文件、shell_run）→ 快速说明原因后执行，不搞"我可以...吗"那套
- 多步任务 → 主动 `plan_create` 规划，逐步执行，每步完后自动进入下一步
- 和用户说话简洁直接：不要铺垫、不要复述工具输出、不要"让我试试"
- 全部完成后用 1-2 句话总结结果即可
- **看到待处理提案时**：说明提案内容，等待用户确认后再执行。不要擅自操作。
- **用户回复「确认」「好」「执行」「yes」时**：如果存在待处理提案，直接用 `proposal_approve(id)` 执行提案。不要反问"要处理什么业务"。
- **遇到缺失依赖时**：直接 `shell_run pip install <包名>` 安装，装完继续工作。不要报告"缺少依赖"。
- **能直接解决的问题就不要报告问题**：安装包、修复配置、清理缓存——自己动手。

## 行为示例

以下是你应该模仿的主动行为模式（Few-Shot Examples）：

**示例 1 — 修复代码问题**
```
用户：帮我看看为什么 tools.py 的 do_shell_run 报 NameError
你：（直接 read_own_source 读取文件 → 发现缺少 import → self_improve 修改 → reload_module 验证）
```
不应当的行为：回复"我发现了缺少 import，需要我修改吗？"

**示例 2 — 系统巡检**
```
IdleInspector 创建了一个提案："file_write 调用失败次数过多，建议增加前置目录检查"
你：（proposal_list 查看 → proposal_approve 执行 → 报告"已执行修复提案：增加目录检查"）
```
不应当的行为："发现一个待处理提案，您要批准执行吗？"

**示例 3 — 多步数据处理任务**
```
用户：把昨天的销售数据导成表格
你：（code_exec/pandas 读数据 → excel_xlsx 生成 → 完成后"已生成销售报表，共 45 条记录，已保存到 workspace/销售报表_2026-05-20.xlsx"）
```
不应当的行为："我可以帮你做这个。首先，让我看看数据在哪里...（等回复）"

**示例 4 — 环境修复**
```
用户：OAA 页面打不开了
你：（health_diagnose → 检查端口 9765 → 发现 WebSocket 未监听 → shell_run 启动服务 → "已重启，页面应该在 10 秒内恢复"）
```
不应当的行为："让我检查一下状态...(报告问题但不自动修复)"

**示例 5 — 依赖缺失**
```
code_exec 报 ModuleNotFoundError: No module named 'openpyxl'
你：（shell_run pip install openpyxl → 重新执行原代码 → 继续后续工作）
```
不应当的行为："缺少 openpyxl 模块，请先运行 pip install openpyxl"

每次回应时问自己：**我是在解决问题，还是在汇报问题？**

## 业务领域

联轴器出口业务。使用 `file_write` 保存文件到 workspace 目录，使用 `excel_xlsx` 创建表格。
复杂数据处理用 `code_exec` 或 `code_run` 执行 Python 脚本。"""

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
            recent = self.archiver.load_recent_summaries(limit=3)
            if recent:
                result += "\n\n## 近期对话摘要\n\n以下是你近期完成的工作和讨论的话题。当你觉得用户的问题可能是「继续之前的话题」时，在这里找找线索：\n\n" + recent

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

        # Step 1: System prompt (always uses identity-only, model selects skill)
        system_prompt = self.build_system_prompt()

        # Step 2: Handler
        handler = self.build_handler()

        # Step 3: Agent loop
        loop = AgentLoop(
            llm=self.llm,
            handler=handler,
            tools_schema=self._tools_schema,
            memory_mgr=self.memory,
            metrics_collector=self.metrics,
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
                elif chunk["type"] == "done":
                    final_result = chunk.get("content", "")
                    # Record activity time + task context for lineB idle detection
                    self._idle_inspector.set_last_activity_time()
                    if trajectory:
                        used_tools = {t["tool"] for t in trajectory}
                        used_skills = {skill_loaded} if skill_loaded else set()
                        self._idle_inspector.record_task_context(used_tools, used_skills)
                yield chunk
        except asyncio.TimeoutError:
            timed_out = True
            producer.cancel()
            msg = f"处理超时（超过{_PROCESS_TIMEOUT // 60}分钟），请重试或拆分问题"
            logger.warning("process_message timed out after %ds", _PROCESS_TIMEOUT)
            yield {"type": "done", "content": msg}
        finally:
            if not producer.done():
                producer.cancel()

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

"""OAA Agent — orchestrates identity, skills, tools, and LLM into a coherent agent loop."""
import os
from typing import AsyncGenerator, Optional

from ..auth.permissions import PermissionsManager
from ..config import AppConfig
from ..evolution.engine import EvolutionEngine
from ..init import ensure_data_dir, load_identity
from ..llm import LLMClient
from ..logging_config import get_logger
from .browser_tools import BrowserTools
from .extended_tools import ExtendedTools
from .handler import BaseHandler
from .idle_inspector import IdleInspector
from .loop import AgentLoop
from .memory_manager import MemoryManager
from .skill_manager import SkillManager
from .tool_schema import ATOMIC_TOOLS_SCHEMA, BROWSER_TOOLS_SCHEMA, DINGTALK_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA, FEISHU_TOOLS_SCHEMA, MCP_TOOLS_SCHEMA, WECHAT_TOOLS_SCHEMA
from .tool_decorator import collect_tool_schemas
from .tools import AtomicTools

logger = get_logger("agent.oaa_agent")

# OAA project root — used for self-modification tools
OAA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


class _MergedHandler(BaseHandler):
    """Dynamic handler that dispatches tool calls to AtomicTools, ExtendedTools,
    BrowserTools, and runtime-created dynamic tools.

    Uses ``__getattr__`` to delegate ``do_<name>`` lookups to the correct
    backend, so all tools are available through a single handler instance
    consumed by ``AgentLoop``.
    """

    def __init__(self, atomic: AtomicTools, extended: ExtendedTools, browser: BrowserTools):
        self._atomic = atomic
        self._extended = extended
        self._browser = browser

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
        # Check decorator registries on each backend
        tool_name = name[3:]  # strip "do_"
        for backend in (self._atomic, self._extended, self._browser):
            if tool_name in backend._tool_registry:
                return backend._tool_registry[tool_name]
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

        # Idle inspector (needs memory + evolution, so initialized after both)
        self._idle_inspector = IdleInspector(
            scheduler=scheduler,
            memory_mgr=self.memory,
            evolution=evolution,
        )

        self.llm = LLMClient(config.model)

        skills_dir = os.path.join(config.data_dir, "skills")
        self.skill_mgr = SkillManager(skills_dir)
        self.skill_mgr.discover()

        self.atomic = AtomicTools(config.data_dir, permissions=permissions)
        self.atomic.set_memory_manager(self.memory)
        self.extended = ExtendedTools(
            config.data_dir, permissions=permissions, wechat_adapter=wechat_adapter,
            dingtalk_client_id=config.dingtalk.client_id,
            dingtalk_client_secret=config.dingtalk.client_secret,
        )
        self.extended.set_skill_manager(self.skill_mgr)
        self.browser = BrowserTools()

        self._tools_schema = (
            ATOMIC_TOOLS_SCHEMA + EXTENDED_TOOLS_SCHEMA + BROWSER_TOOLS_SCHEMA + MCP_TOOLS_SCHEMA
            + collect_tool_schemas(AtomicTools)
            + collect_tool_schemas(ExtendedTools)
            + collect_tool_schemas(BrowserTools)
        )
        if config.wechat.enabled and config.wechat.iLink_token:
            self._tools_schema = self._tools_schema + WECHAT_TOOLS_SCHEMA
        if config.feishu.enabled and config.feishu.app_id:
            self._tools_schema = self._tools_schema + FEISHU_TOOLS_SCHEMA
        if config.dingtalk.enabled and config.dingtalk.client_id:
            self._tools_schema = self._tools_schema + DINGTALK_TOOLS_SCHEMA

    def build_handler(self) -> BaseHandler:
        """Build a merged handler that exposes atomic, extended, and browser tools."""
        handler = _MergedHandler(self.atomic, self.extended, self.browser)
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

        awareness = f"""\
# 重要：当前日期与工具使用规则

**当前日期是 {today}**。你的训练数据截止于较早时间，不包含今日的实时信息。

## 自身定位

你的项目根目录是 {OAA_ROOT}。
数据目录是 {self.config.data_dir}。

关键目录：
- 应用代码: {OAA_ROOT}/oaa/
- 技能文件: {self.config.data_dir}/skills/
- 动态工具: {self.config.data_dir}/dynamic_tools/
- 配置文件: {config_path}
- 持久记忆: {self.config.data_dir}/memory/
- 当前权限级别: **{self.config.permissions.get("permission_level", "auto")}**

需要读取自身源码时，使用 `read_own_source`。需要查看项目结构时，使用 `list_own_structure`。

## 强制规则

1. **任何涉及实时数据、当前信息、最新动态的问题，必须调用 web_search 或 web_scan**
2. **禁止使用训练数据回答关于"今天"、"现在"、"当前"、"最新"的问题**
3. **搜索结果可能不精确时，用 web_scan 直接访问具体网页获取详细内容**
4. **复杂多步骤任务先用 plan_create 制定计划，再逐步执行**
5. **查询天气、电影排片、股票价格、新闻事件等实时信息必须上网搜索，不准凭记忆回答**
6. **你有 shell_run 工具可以执行任何命令行操作，严禁要求用户打开终端或运行命令。所有交互必须在 GUI 内完成（通过聊天窗口与用户交流）。需要执行命令行时自行使用 shell_run。**

## 可用工具

{chr(10).join(tool_list)}

## 可用技能

当遇到以下领域的任务时，调用 `skill_load("技能名称")` 加载对应技能的详细操作指南。
加载后技能会提供专业知识、SOP 流程和注意事项。如果没有列出匹配的技能，直接用现有工具完成任务即可。

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

## 业务领域

联轴器出口业务。使用 `file_write` 保存文件到 workspace 目录，使用 `excel_xlsx` 创建表格。
复杂数据处理用 `code_exec` 或 `code_run` 执行 Python 脚本。"""

        result = base_prompt + "\n\n" + awareness

        # Inject tiered memory (HOT + recent corrections)
        memory_prompt = self.memory.build_memory_prompt()
        result += "\n\n" + memory_prompt

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
        )
        loop.set_skill_context(system_prompt)

        trajectory: list[dict] = []
        final_result = ""
        async for chunk in loop.run(user_input, history=history):
            if chunk["type"] == "tool_call":
                trajectory.append({"tool": chunk["name"], "args": chunk.get("args", {})})
            elif chunk["type"] == "done":
                final_result = chunk.get("content", "")
                # Idle inspection: check for due tasks or improvement areas
                if len(user_input) > 2:
                    try:
                        proposal = self._idle_inspector.inspect()
                        if proposal:
                            yield {"type": "llm_output", "content": "\n\n---\n\n" + proposal}
                    except Exception as exc:
                        logger.warning("Idle inspection failed: %s", exc)
            yield chunk

        # Record evolution data
        if self.evolution:
            self.evolution.record_trajectory(
                "default", user_input, trajectory, final_result,
            )

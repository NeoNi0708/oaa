"""OAA Agent — orchestrates identity, skills, tools, and LLM into a coherent agent loop."""
import os
from typing import AsyncGenerator, Optional

from ..auth.permissions import PermissionsManager
from ..config import AppConfig
from ..evolution.engine import EvolutionEngine
from ..init import ensure_data_dir, load_identity
from ..llm import LLMClient
from ..logging_config import get_logger
from .extended_tools import ExtendedTools
from .handler import BaseHandler
from .loop import AgentLoop
from .skill_manager import SkillInfo, SkillManager
from .tool_schema import ATOMIC_TOOLS_SCHEMA, EXTENDED_TOOLS_SCHEMA, WECHAT_TOOLS_SCHEMA
from .tools import AtomicTools

logger = get_logger("agent.oaa_agent")


class _MergedHandler(BaseHandler):
    """Dynamic handler that dispatches tool calls to both AtomicTools and ExtendedTools.

    Uses ``__getattr__`` to delegate ``do_<name>`` lookups to the correct
    backend, so both sets of tools are available through a single handler
    instance consumed by ``AgentLoop``.
    """

    def __init__(self, atomic: AtomicTools, extended: ExtendedTools):
        self._atomic = atomic
        self._extended = extended

    def __getattr__(self, name: str):
        if not name.startswith("do_"):
            raise AttributeError(name)
        if hasattr(self._atomic, name):
            return getattr(self._atomic, name)
        if hasattr(self._extended, name):
            return getattr(self._extended, name)
        raise AttributeError(name)


class OAAAgent:
    """Top-level OAA agent that wires together identity, skill management,
    tool execution, and LLM interactions.
    """

    def __init__(self, config: AppConfig, permissions: Optional[PermissionsManager] = None,
                 evolution: Optional[EvolutionEngine] = None):
        ensure_data_dir(config.data_dir)

        self.config = config
        self.identity: dict = load_identity(config.data_dir)
        self.permissions = permissions
        self.evolution = evolution

        self.llm = LLMClient(config.model)

        skills_dir = os.path.join(config.data_dir, "skills")
        self.skill_mgr = SkillManager(skills_dir)
        self.skill_mgr.discover()

        self.atomic = AtomicTools(config.data_dir, permissions=permissions)
        self.extended = ExtendedTools(config.data_dir, permissions=permissions)

        self._tools_schema = ATOMIC_TOOLS_SCHEMA + EXTENDED_TOOLS_SCHEMA + WECHAT_TOOLS_SCHEMA

    def build_handler(self) -> BaseHandler:
        """Build a merged handler that exposes both atomic and extended tools."""
        return _MergedHandler(self.atomic, self.extended)

    def build_system_prompt(self, skill_name: str = "") -> str:
        """Build a system prompt from identity data and optional skill context."""
        if skill_name:
            skill = self.skill_mgr.get(skill_name)
            if skill and skill.skill_md:
                return skill.build_system_prompt(self.identity)

        return self._identity_only_prompt()

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

    async def process_message(self, user_input: str, history: list | None = None) -> AsyncGenerator[dict, None]:
        """Process a single user message through the full agent pipeline.

        1. Intent matching → skill switching
        2. System prompt construction (identity + skill context)
        3. Handler assembly (atomic + extended tools)
        4. Agent loop execution (LLM ↔ tool calls, streaming yielded chunks)

        Args:
            user_input: The user's message text.
            history: Optional list of prior message dicts (role/content) to
                     prepend as conversation context.

        Yields dict chunks with keys ``type``, ``content``, and optionally
        ``name`` / ``args`` / ``result``.
        """
        logger.info("Processing message: %s...", user_input[:80])

        # Step 1: Intent matching & skill switching
        matched_skill: Optional[SkillInfo] = self.skill_mgr.match_intent(user_input)
        if not matched_skill:
            # LLM fallback when keyword matching fails
            matched_skill = await self.skill_mgr._llm_match_intent(user_input, self.llm)
        skill_name = ""
        extra_tools: Optional[list] = None

        if matched_skill:
            loaded = self.skill_mgr.switch_to(matched_skill.name)
            if loaded:
                skill_name = loaded.name
                logger.info("Skill activated: %s", skill_name)
                if loaded.tools:
                    extra_tools = loaded.tools

        # Step 2: System prompt
        system_prompt = self.build_system_prompt(skill_name)

        # Step 3: Handler
        handler = self.build_handler()

        # Step 4: Agent loop
        loop = AgentLoop(
            llm=self.llm,
            handler=handler,
            tools_schema=self._tools_schema,
        )
        loop.set_skill_context(system_prompt, extra_tools)

        trajectory: list[dict] = []
        final_result = ""
        async for chunk in loop.run(user_input, history=history):
            if chunk["type"] == "tool_call":
                trajectory.append({"tool": chunk["name"], "args": chunk.get("args", {})})
            elif chunk["type"] == "done":
                final_result = chunk.get("content", "")
            yield chunk

        # Record evolution data
        if self.evolution:
            if skill_name:
                self.evolution.record_skill_usage(skill_name)
                self.evolution.record_trajectory(
                    skill_name, user_input, trajectory, final_result,
                )

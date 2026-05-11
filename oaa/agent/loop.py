"""Agent core loop — adapted from GenericAgent's agent_loop.py"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Optional, TYPE_CHECKING

from ..llm import LLMClient, LLMResponse
from ..logging_config import get_logger

if TYPE_CHECKING:
    from .handler import BaseHandler

logger = get_logger("agent.loop")

_MAX_RETRIES = 3
_BASE_DELAY = 1.0


class StepOutcome:
    """Outcome of a single step — used by BaseHandler step methods."""

    def __init__(self, data: Any, next_prompt: Optional[str] = None, should_exit: bool = False):
        self.data = data
        self.next_prompt = next_prompt
        self.should_exit = should_exit


class AgentLoop:
    """Minimal agent loop — same structure as GenericAgent.

    Yields dict chunks with keys: type, content, and optionally name/args/result.
    """

    def __init__(
        self,
        llm: LLMClient,
        handler: "BaseHandler",
        tools_schema: list,
        max_turns: int = 70,
    ):
        self.llm = llm
        self.handler = handler
        self.tools_schema = tools_schema
        self.max_turns = max_turns
        self._system_prompt = "You are OAA Agent."

    def set_skill_context(self, system_prompt: str, extra_tools: Optional[list] = None):
        """Set system prompt and optionally add skill-specific tools."""
        self._system_prompt = system_prompt
        combined = list(self.tools_schema)
        if extra_tools:
            combined = combined + extra_tools
        self.llm.set_tools(combined)

    async def run(self, user_input: str, history: list | None = None) -> AsyncGenerator[dict, None]:
        """Run agent loop. Yields intermediate result dicts, returns final response.

        Each yielded dict has the structure:
            {"type": "status", "content": ...}
            {"type": "llm_output", "content": ...}
            {"type": "tool_call", "name": ..., "args": ...}
            {"type": "tool_result", "name": ..., "result": ...}
            {"type": "done", "content": ...}
        """
        if not self.llm:
            yield {"type": "done", "content": "No LLM client configured."}
            return

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        turn = 0
        while turn < self.max_turns:
            turn += 1
            yield {"type": "status", "content": f"Thinking (Turn {turn})..."}

            # Call LLM with retry + exponential backoff
            response: LLMResponse | None = None
            last_error: Exception | None = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = await self.llm.chat(messages)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    err_type = type(exc).__name__
                    if attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            "LLM call failed [%s] attempt %d/%d, retrying in %.1fs: %s",
                            err_type, attempt, _MAX_RETRIES, delay, exc,
                        )
                        yield {"type": "status", "content": f"LLM {err_type}, 重试 ({attempt}/{_MAX_RETRIES})..."}
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "LLM call failed [%s] after %d attempts: %s",
                            err_type, _MAX_RETRIES, exc,
                        )
                        yield {"type": "done", "content": f"LLM error: {err_type}"}
                        return

            content = (response.content or "") if response else ""
            tool_calls = response.tool_calls

            if content:
                yield {"type": "llm_output", "content": content}

            if not tool_calls:
                yield {"type": "done", "content": content}
                return

            # Execute tools and yield results
            tool_result_entries = []
            for tc in tool_calls:
                tool_name = tc.function.name
                raw_args = tc.function.arguments
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}

                yield {"type": "tool_call", "name": tool_name, "args": args}
                try:
                    result = await self.handler.dispatch(tool_name, args)
                except Exception as exc:
                    logger.error("Tool %s failed: %s", tool_name, exc)
                    result = {"status": "error", "msg": str(exc)}
                yield {"type": "tool_result", "name": tool_name, "result": result}

                tool_result_entries.append({
                    "tool_use_id": tc.id,
                    "content": str(result),
                })

            # Append assistant + tool-result messages
            messages = self._build_turn_messages(messages, content, tool_calls, tool_result_entries)

        yield {"type": "done", "content": "Max turns exceeded."}

    def _build_turn_messages(self, messages: list, content: str,
                              tool_calls: list, tool_result_entries: list) -> list:
        """Build messages for this turn: assistant msg with tool_calls + tool results."""
        assistant_msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": (
                            tc.function.arguments
                            if isinstance(tc.function.arguments, str)
                            else json.dumps(tc.function.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_msg)
        for entry in tool_result_entries:
            messages.append({
                "role": "tool",
                "content": entry["content"],
                "tool_call_id": entry["tool_use_id"],
            })
        return messages

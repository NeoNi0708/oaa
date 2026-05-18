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
_LLM_TIMEOUT = 90.0  # hard cap per LLM call, outer safety net beyond SDK timeout
_MAX_CONTINUATIONS = 5  # max auto-continuation turns for truncated responses


def _friendly_error(exc: Exception) -> str:
    """Return a user-facing message for common LLM errors."""
    name = type(exc).__name__
    msg = str(exc)
    if "429" in msg or "RateLimit" in name:
        return "模型服务繁忙，请稍后重试（可尝试切换到其他模型厂商）"
    if "401" in msg or "Authentication" in name or "Unauthorized" in msg:
        return "API Key 无效或已过期，请在设置页面更新 Key"
    if "404" in msg or "Not Found" in msg:
        return "模型 ID 不存在，请在设置页面检查模型配置"
    if "timeout" in msg.lower() or "Timeout" in name or "TimedOut" in name:
        return "模型响应超时，请检查网络或切换模型厂商"
    if "Connection" in name:
        return f"无法连接到模型服务，请检查网络（{name}）"
    if "APIError" in msg or "EngineInternalError" in msg or "InvalidParamError" in msg:
        return "模型服务内部错误，请重试或切换模型"
    return f"模型调用失败: {name}"


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
        max_messages: int = 60,
        memory_mgr=None,
    ):
        self.llm = llm
        self.handler = handler
        self.tools_schema = tools_schema
        self.max_turns = max_turns
        self._max_messages = max_messages
        self._memory_mgr = memory_mgr
        self._system_prompt = "You are OAA Agent."
        self._last_llm_content = ""
        self._continuation_count = 0

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
            # Only retry on timeout/connection errors — API/server errors fail immediately
            response: LLMResponse | None = None
            last_error: Exception | None = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = await asyncio.wait_for(
                        self.llm.chat(messages),
                        timeout=_LLM_TIMEOUT,
                    )
                    last_error = None
                    break
                except asyncio.TimeoutError:
                    last_error = TimeoutError("LLM call timed out")
                    err_type = "TimeoutError"
                except Exception as exc:
                    last_error = exc
                    err_type = type(exc).__name__
                    # API/server errors are not transient — fail immediately
                    if "APIError" in str(exc) or "EngineInternalError" in str(exc) or "InvalidParamError" in str(exc):
                        logger.error("LLM server error [%s], not retrying: %s", err_type, exc)
                        yield {"type": "done", "content": _friendly_error(exc)}
                        return
                if last_error:
                    if attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            "LLM call failed [%s] attempt %d/%d, retrying in %.1fs: %s",
                            err_type, attempt, _MAX_RETRIES, delay, last_error,
                        )
                        yield {"type": "status", "content": f"LLM {err_type}, 重试 ({attempt}/{_MAX_RETRIES})..."}
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "LLM call failed [%s] after %d attempts: %s",
                            err_type, _MAX_RETRIES, last_error,
                        )
                        yield {"type": "done", "content": _friendly_error(last_error)}
                        return

            content = (response.content or "") if response else ""
            tool_calls = response.tool_calls

            if content:
                yield {"type": "llm_output", "content": content}
                self._last_llm_content = content

            # Auto-continuation: if response was truncated (max_tokens hit) with no tool calls,
            # re-prompt the LLM to continue from where it left off
            if not tool_calls and response.finish_reason in ("length", "max_tokens"):
                if self._continuation_count < _MAX_CONTINUATIONS:
                    self._continuation_count += 1
                    logger.info("Response truncated (turn %d), auto-continuing (%d/%d)",
                                turn, self._continuation_count, _MAX_CONTINUATIONS)
                    messages.append({
                        "role": "user",
                        "content": "【系统提示：你的回复在输出时被截断，请从上次中断处继续完成，不要重复已经输出的内容】"
                    })
                    yield {"type": "status", "content": f"输出被截断，正在继续({self._continuation_count}/{_MAX_CONTINUATIONS})..."}
                    continue
                else:
                    logger.warning("Response truncated, max continuations reached")
                    content += "\n\n[已达到连续续写上限，回复可能不完整]"

            if not tool_calls:
                final_content = content or self._last_llm_content or ""
                yield {"type": "done", "content": final_content}
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
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Tool %s failed: %s", tool_name, exc)
                    result = {"status": "error", "msg": str(exc)}
                yield {"type": "tool_result", "name": tool_name, "result": result}

                # Record tool failures for self-diagnosis
                if isinstance(result, dict) and result.get("status") == "error" and self._memory_mgr:
                    try:
                        self._memory_mgr.add_tool_failure(tool_name, args, str(result.get("msg", "")))
                    except Exception as rec_err:
                        logger.warning("Failed to record tool failure: %s", rec_err)

                result_str = str(result)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "...[truncated]"
                tool_result_entries.append({
                    "tool_use_id": tc.id,
                    "content": result_str,
                })

            # Append assistant + tool-result messages
            messages = self._build_turn_messages(messages, content, tool_calls, tool_result_entries)

            # Compact messages if over limit (always keep recent context)
            messages = await self._compact_messages(messages)

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

    async def _summarize_with_llm(self, messages: list) -> str | None:
        """Generate a concise summary of compacted messages using the LLM."""
        try:
            text_parts = []
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                if not content or not content.strip():
                    continue
                if isinstance(content, str) and len(content) > 500:
                    content = content[:500] + "..."
                label = {"user": "用户", "assistant": "助手", "tool": "工具结果", "system": "系统"}.get(role, role)
                text_parts.append(f"[{label}]: {content}")

            text = "\n".join(text_parts)
            if len(text) > 6000:
                text = text[:6000] + "\n...（余下内容截断）"

            response = await asyncio.wait_for(
                self.llm.chat([
                    {"role": "system", "content": "你是对话摘要助手。请用中文简要总结以下对话的核心内容（用户目标、已完成步骤、关键决策、已知信息），保留对后续对话有用的细节。控制在300字以内。"},
                    {"role": "user", "content": text},
                ]),
                timeout=15.0,
            )
            summary = (response.content or "").strip()
            return summary if summary else None
        except Exception as exc:
            logger.warning("LLM compaction summary failed: %s", exc)
            return None

    async def _compact_messages(self, messages: list) -> list:
        """Trim oldest non-system messages when over ``_max_messages``.

        Keeps the system prompt and the most recent messages. On first
        compaction, uses the LLM to generate a summary of compacted
        messages and injects it into the context so the conversation
        history is preserved. Falls back to HOT memory recording.
        """
        if len(messages) <= self._max_messages:
            return messages

        # Identify messages to compact
        to_compact = messages[1:-(self._max_messages - 1)]
        removed_count = len(to_compact)

        summary = None
        # On first compaction, try LLM summary
        if self.llm and not getattr(self, '_compaction_recorded', False):
            self._compaction_recorded = True
            summary = await self._summarize_with_llm(to_compact)

        trimmed = messages[:1]  # system prompt
        if summary:
            trimmed.append({"role": "system", "content": f"[对话摘要] {summary}"})
            trimmed.extend(messages[-(self._max_messages - 2):])
        else:
            trimmed.extend(messages[-(self._max_messages - 1):])

        logger.info("Compacted %d old messages, kept %d (llm_summary=%s)",
                     removed_count, len(trimmed), bool(summary))

        # Record compaction in HOT memory once per run (backup for non-LLM-summary case)
        if self._memory_mgr and not getattr(self, '_hot_recorded', False):
            self._hot_recorded = True
            if not summary:
                original_request = ""
                for msg in messages[1:]:
                    if msg.get("role") == "user" and not msg.get("content", "").startswith("【系统提示"):
                        original_request = msg["content"][:200]
                        break
                hot_msg = (
                    f"[消息压缩] 已压缩 {removed_count} 条较早的对话消息，"
                    f"保留最近 {self._max_messages} 条上下文。"
                )
                if original_request:
                    hot_msg += f" 原始请求: \"{original_request}\""
                try:
                    self._memory_mgr.add_to_hot(hot_msg)
                except Exception as exc:
                    logger.warning("Failed to store compaction summary: %s", exc)

        return trimmed


_StepOutcome = StepOutcome

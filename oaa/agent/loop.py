"""Agent core loop — adapted from GenericAgent's agent_loop.py"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Optional, TYPE_CHECKING

from ..llm import LLMClient, LLMResponse
from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..config import ModelConfig

if TYPE_CHECKING:
    from .handler import BaseHandler

logger = get_logger("agent.loop")

_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_LLM_TIMEOUT = 90.0  # hard cap per LLM call, outer safety net beyond SDK timeout
_MAX_CONTINUATIONS = 5  # max auto-continuation turns for truncated responses
_MAX_TOOL_RETRIES = 2  # max auto-retries for transient tool errors
_TRANSIENT_ERRORS = {"rate", "429", "timeout", "timed out", "too many", "busy", "503", "502"}


def _classify_error(exc: Exception) -> str:
    """Classify a tool error as 'transient', 'auth', or 'permanent'."""
    msg = str(exc).lower()
    if any(t in msg for t in _TRANSIENT_ERRORS):
        return "transient"
    if "401" in msg or "unauthorized" in msg or "auth" in msg or "403" in msg:
        return "auth"
    return "permanent"


# Map loop-level error classification → failure root cause categories
_LOOP_CAT_TO_FAILURE = {
    "transient": "infra_error",
    "auth": "parameter_error",
    "permanent": "unknown",
}


def _recovery_hint(tool_name: str, error_msg: str, err_type: str) -> str:
    """Return a recovery hint appended to the tool result for the LLM."""
    if err_type == "auth":
        return (
            f"\n\n[恢复提示] 工具 {tool_name} 因权限/认证问题失败。"
            f"请检查配置中相关 API Key 是否有效，或通过 ai_search 查找该服务的认证方式。"
            f"不要建议用户手动完成。"
        )
    return (
        f"\n\n[恢复提示] 工具 {tool_name} 调用失败: {error_msg[:120]}。"
        f"请尝试：1) 使用其他工具或方法完成相同目标；"
        f"2) 如果没有合适的工具，用 read_own_source + self_improve 修改代码添加功能；"
        f"3) 用 ai_search 查找现成的解决方案或替代工具。"
        f"完成任务是第一优先级。不要建议用户手动操作。"
    )


def _friendly_error(exc: Exception) -> str:
    """Return a user-facing message for common LLM errors."""
    name = type(exc).__name__
    msg = str(exc)
    if "429" in msg or "RateLimit" in name:
        return "模型服务繁忙，请稍后重试（可尝试切换到其他模型厂商）"
    if "401" in msg or "Authentication" in name or "Unauthorized" in msg:
        return "API Key 无效或已过期（如果 Key 有效也可能是该模型不支持聊天功能），请在设置页面更新 Key 或切换模型"
    if "404" in msg or "Not Found" in msg:
        return "模型 ID 不存在，请在设置页面检查模型配置"
    if "timeout" in msg.lower() or "Timeout" in name or "TimedOut" in name:
        return "模型响应超时，请检查网络或切换模型厂商"
    if "Connection" in name:
        return f"无法连接到模型服务，请检查网络（{name}）"
    if "APIError" in msg or "EngineInternalError" in msg or "InvalidParamError" in msg:
        return "模型服务内部错误，请重试或切换模型"
    return f"模型调用失败: {name}"


def _summarize_args(args: dict, max_len: int = 120) -> str:
    """Summarize tool args for logging — key=value pairs, truncated."""
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f"{k}={sv}")
    joined = " ".join(parts)
    if len(joined) > max_len:
        joined = joined[:max_len - 3] + "..."
    return joined


def _result_status(result: Any) -> str:
    """Return a short status string for any tool result."""
    if isinstance(result, dict):
        if result.get("status") == "error":
            return f"error: {str(result.get('msg', ''))[:80]}"
        return f"ok ({len(str(result))} bytes)"
    return f"ok ({len(str(result))} bytes)"


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
        metrics_collector=None,
        model_fallbacks: list | None = None,
        agent=None,  # OAAAgent reference for P2 skill plugin
        policy_engine=None,  # P4 PolicyEngine reference
    ):
        self.llm = llm
        self.handler = handler
        self.tools_schema = tools_schema
        self.max_turns = max_turns
        self._max_messages = max_messages
        self._memory_mgr = memory_mgr
        self._metrics = metrics_collector
        self._agent = agent
        self._policy_engine = policy_engine
        self._system_prompt = "You are OAA Agent."
        self._system_prompt_base = self._system_prompt  # saved for skill unload restore
        self._last_llm_content = ""
        self._continuation_count = 0
        self._model_fallbacks = model_fallbacks or []
        self._active_fallback_idx = -1  # -1 = primary model, 0+ = fallback index
        self._combined_tools = list(self.tools_schema)
        # Execution chain for the current task — tracks each tool call for root cause analysis
        self._execution_chain: list[dict] = []

    def _error_with_context(self, error_msg: str) -> str:
        """Append error to last yielded content so it doesn't overwrite the conversation."""
        if self._last_llm_content:
            return self._last_llm_content + "\n\n[系统错误] " + error_msg
        return error_msg

    def _try_next_fallback(self) -> bool:
        """Advance to next fallback model. Returns True if one is available."""
        # Reset continuation counter on fallback so the new model isn't
        # immediately constrained by the old model's truncation limit.
        self._continuation_count = 0
        next_idx = self._active_fallback_idx + 1
        if next_idx < len(self._model_fallbacks):
            self._active_fallback_idx = next_idx
            return True
        return False

    def _apply_fallback_config(self, fb: dict):
        """Reconfigure the LLM client with a fallback model dict."""
        from ..config import ModelConfig
        # Auto-detect api_format from provider name
        provider = fb.get("provider", "")
        api_format = "anthropic" if provider in ("anthropic", "custom-anthropic") else "openai"
        cfg = ModelConfig(
            provider=provider,
            api_format=fb.get("api_format", api_format),
            base_url=fb.get("base_url", ""),
            api_key=fb.get("api_key", ""),
            model_id=fb.get("model_id", ""),
        )
        self.llm.reconfigure(cfg)
        # reconfigure creates a new client instance — reapply tools so the
        # fallback model can still call tools (otherwise it would only produce
        # plain-text replies and the agent would terminate early).
        self.llm.set_tools(self._combined_tools)

    def set_skill_context(self, system_prompt: str, extra_tools: Optional[list] = None):
        """Set system prompt and optionally add skill-specific tools."""
        self._system_prompt = system_prompt
        self._system_prompt_base = system_prompt
        self._combined_tools = list(self.tools_schema)
        if extra_tools:
            self._combined_tools = self._combined_tools + extra_tools
        self.llm.set_tools(self._combined_tools)

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

        # Reset execution chain for this task
        self._execution_chain = []

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
            _llm_start = 0.0
            attempt = 0
            while attempt < _MAX_RETRIES:
                attempt += 1
                try:
                    _llm_start = time.time()
                    response = await asyncio.wait_for(
                        self.llm.chat(messages),
                        timeout=_LLM_TIMEOUT,
                    )
                    last_error = None
                    break
                except asyncio.TimeoutError:
                    _record_llm_metrics(self._metrics, self.llm, time.time() - _llm_start if _llm_start else 0, error="TimeoutError")
                    last_error = TimeoutError("LLM call timed out")
                    err_type = "TimeoutError"
                except Exception as exc:
                    _record_llm_metrics(self._metrics, self.llm, time.time() - _llm_start if _llm_start else 0, error=type(exc).__name__)
                    last_error = exc
                    err_type = type(exc).__name__
                    # API/server errors are not transient — fail immediately.
                    # BadRequestError (OpenAI SDK), ContextLengthExceeded, etc.
                    # should not be retried — they will fail the same way every time.
                    err_msg = str(exc)
                    if ("APIError" in err_msg or "EngineInternalError" in err_msg
                        or "InvalidParamError" in err_msg
                        or err_type in ("BadRequestError", "ContextLengthExceeded", "NotFoundError",
                                        "AuthenticationError", "PermissionDeniedError",
                                        "UnprocessableEntityError", "ContentTooLongError")):
                        # Try fallback models before giving up
                        if self._try_next_fallback():
                            fb_model = self._model_fallbacks[self._active_fallback_idx]
                            logger.warning(
                                "Falling back to model [%s/%s]: %s (%s)",
                                self._active_fallback_idx + 1, len(self._model_fallbacks),
                                fb_model.get("model_id", "?"), fb_model.get("provider", "?"),
                            )
                            yield {"type": "status",
                                   "content": f"{getattr(getattr(self.llm, '_config', None), 'model_id', '?')} 不可用{'（可能是 Key 无效或该模型不支持聊天）' if err_type == 'AuthenticationError' else f'({err_type})'}，切换到 {fb_model.get('model_id','?')}..."}
                            # Reconfigure LLM client with fallback model
                            self._apply_fallback_config(fb_model)
                            # Reset attempt counter so the fallback model gets
                            # a full set of retries (the while loop re-increments).
                            attempt = 0
                            continue  # retry current turn with new model

                        logger.error("LLM non-retryable error [%s] (no fallbacks left): %s", err_type, exc)
                        # If there was any previous assistant output, append the error
                        # so it doesn't overwrite the conversation in the frontend.
                        if self._last_llm_content:
                            yield {"type": "llm_output", "content": "\n\n[系统错误] " + _friendly_error(exc)}
                            yield {"type": "done", "content": ""}
                        else:
                            yield {"type": "done", "content": self._error_with_context(_friendly_error(exc))}
                        return
                if last_error:
                    if attempt < _MAX_RETRIES:
                        # Timeout errors: longer delay to let server recover
                        # Transient errors: exponential backoff starting small
                        if err_type == "TimeoutError":
                            delay = 30.0
                        else:
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
                        if self._last_llm_content:
                            yield {"type": "llm_output", "content": "\n\n[系统错误] " + _friendly_error(last_error)}
                            yield {"type": "done", "content": ""}
                        else:
                            yield {"type": "done", "content": self._error_with_context(_friendly_error(last_error))}
                        return

            # Record successful LLM call metrics
            _record_llm_metrics(self._metrics, self.llm, time.time() - _llm_start if _llm_start else 0, response=response)

            content = (response.content or "") if response else ""
            tool_calls = response.tool_calls if response else []
            thinking = response.thinking if response else ""

            logger.debug(
                "LLM turn=%d | content_len=%d | tool_calls=%d | finish=%s | thinking=%s",
                turn, len(content), len(tool_calls),
                response.finish_reason if response else "none",
                "yes" if thinking else "no",
            )

            if content:
                yield {"type": "llm_output", "content": content}
                self._last_llm_content = content

            # Auto-continuation: if response was truncated (max_tokens hit) with no tool calls,
            # re-prompt the LLM to continue from where it left off
            if not tool_calls and response and response.finish_reason in ("length", "max_tokens"):
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

            # Execute tools ONE AT A TIME, each as an independent step.
            # After each tool completes, feed the result back to the LLM
            # so it can adapt subsequent calls based on intermediate results.
            pending = list(tool_calls)
            step_id = 0
            while pending:
                tc = pending.pop(0)
                step_id += 1

                tool_name = tc.function.name
                raw_args = tc.function.arguments
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}

                logger.debug("Step %d: Tool call: %s %s", step_id, tool_name, _summarize_args(args))
                yield {"type": "tool_call", "name": tool_name, "args": args,
                       "step_id": step_id, "phase": "plan"}

                # Add assistant message with this single tool call
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
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
                    }],
                })

                # P4 Policy check — enforce rules before dispatching
                policy_blocked = False
                if self._policy_engine:
                    policy_result = self._policy_engine.check(tool_name, args)
                    if policy_result.action == "block":
                        logger.info("Policy blocked: %s — %s", tool_name, policy_result.reason)
                        yield {"type": "status", "content": f"⛔ {policy_result.reason}"}
                        result = {"status": "error", "msg": policy_result.reason, "policy_blocked": True}
                        policy_blocked = True
                    elif policy_result.action == "modify":
                        args.update(policy_result.modifications)
                        logger.info("Policy modified args for %s: %s", tool_name, policy_result.modifications)
                        yield {"type": "status", "content": f"策略: {policy_result.reason}"}

                # Execute tool with auto-retry for transient errors
                result = None if not policy_blocked else result
                last_tool_error: Exception | None = None
                t_start = time.time()
                if not policy_blocked:
                    for tool_attempt in range(1, _MAX_TOOL_RETRIES + 1):
                        try:
                            result = await self.handler.dispatch(tool_name, args)
                            last_tool_error = None
                            break
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            last_tool_error = exc
                            err_cat = _classify_error(exc)
                            if err_cat == "transient" and tool_attempt < _MAX_TOOL_RETRIES:
                                t_delay = _BASE_DELAY * (2 ** (tool_attempt - 1))
                                logger.warning("Tool %s transient error [%s] attempt %d/%d, retry in %.1fs",
                                               tool_name, err_cat, tool_attempt, _MAX_TOOL_RETRIES, t_delay)
                                yield {"type": "status", "content": f"工具 {tool_name} 临时错误，重试 ({tool_attempt}/{_MAX_TOOL_RETRIES})...",
                                       "step_id": step_id}
                                await asyncio.sleep(t_delay)
                            else:
                                break

                    if last_tool_error is not None:
                        exc = last_tool_error
                        logger.error("Tool %s failed: %s", tool_name, exc)
                        err_cat = _classify_error(exc)
                        result = {
                            "status": "error",
                            "msg": str(exc),
                            "_recovery_hint": _recovery_hint(tool_name, str(exc), err_cat),
                        }
                t_dur = time.time() - t_start
                yield {"type": "tool_result", "name": tool_name, "result": result,
                       "step_id": step_id, "phase": "result", "duration": round(t_dur, 2)}
                logger.debug("Tool result: %s | status=%s", tool_name, _result_status(result))

                # Record in execution chain for root cause analysis
                is_error = isinstance(result, dict) and result.get("status") == "error"
                self._execution_chain.append({
                    "tool": tool_name,
                    "args": {k: v for k, v in args.items() if k != "code"},  # omit large code blobs
                    "status": "error" if is_error else "ok",
                    "error": result.get("msg", "")[:200] if is_error else "",
                })

                # Record tool success/failure metrics
                if self._metrics:
                    try:
                        self._metrics.record_tool_result(tool_name, success=not is_error)
                    except Exception as exc:
                        logger.warning("Failed to record tool metrics: %s", exc)

                # Record tool results for self-diagnosis
                if self._memory_mgr:
                    try:
                        if is_error:
                            err_cat_loop = _classify_error(Exception(result.get("msg", ""))) if result.get("msg") else "permanent"
                            failure_cat = _LOOP_CAT_TO_FAILURE.get(err_cat_loop, "unknown")
                            self._memory_mgr.add_tool_failure(
                                tool_name, args,
                                str(result.get("msg", "")),
                                category=failure_cat,
                                task_context=user_input[:300] if user_input else "",
                                execution_chain=list(self._execution_chain),
                            )
                        else:
                            self._memory_mgr.add_tool_success(tool_name, args)
                    except Exception as rec_err:
                        logger.warning("Failed to record tool result: %s", rec_err)

                # Auto-detect missing modules for code tools → suggest pip install
                if tool_name in ("code_run", "code_exec") and isinstance(result, dict) and result.get("status") == "error":
                    err_text = " ".join(filter(None, [
                        str(result.get("msg", "")),
                        str(result.get("stdout", "")),
                        str(result.get("stderr", "")),
                    ]))
                    mod_match = re.search(r"(?:ModuleNotFoundError|ImportError):\s*No module named ['\"]?(.+?)['\"]?(?:\s|$)", err_text)
                    if mod_match:
                        mod_name = mod_match.group(1)
                        result["_recovery_hint"] = (
                            f"\n\n[恢复提示] 缺少 Python 模块 '{mod_name}'。"
                            f"请用 shell_run 命令安装：`pip install {mod_name}`，然后重试。"
                            f"完成任务是第一优先级。不要建议用户手动操作。"
                        )

                # Format error results with clear recovery hints for the LLM
                if isinstance(result, dict) and result.get("status") == "error":
                    parts = [f"status: error", f"msg: {result.get('msg', '')}"]
                    # Include stdout/stderr for code tools (the error detail is there, not in msg)
                    if tool_name in ("code_run", "code_exec"):
                        for key in ("stdout", "stderr"):
                            val = result.get(key, "")
                            if val:
                                lines = val.strip().split("\n")
                                snippet = "\n".join(lines[-8:])
                                parts.append(f"{key} (tail):\n{snippet}")
                    hint = result.get("_recovery_hint")
                    if hint:
                        parts.append("_" * 30)
                        parts.append(hint)
                    else:
                        # Every tool error gets a standardized recovery instruction
                        # (code-level — prevents LLM from ever suggesting manual action)
                        parts.append("_" * 30)
                        parts.append(
                            "\n[恢复提示] 工具调用失败。请用其他工具重试或换一种方案。"
                            "不能建议用户手动操作——你有 shell_run、code_exec、self_improve、"
                            "ai_search 等工具自行解决问题。"
                        )
                        parts.append("_" * 30)
                    result_str = "\n".join(parts)
                else:
                    result_str = str(result)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "...[truncated]"
                # Append tool result to conversation history
                messages.append({
                    "role": "tool",
                    "content": result_str,
                    "tool_call_id": tc.id,
                })

                # P2 Skill plugin: detect skill_load/unload and update schema + prompt
                # for subsequent LLM calls in this turn.
                if self._agent:
                    if tool_name == "skill_load":
                        skill_name = args.get("name", "")
                        if skill_name and self._agent.apply_skill_plugin(skill_name):
                            self._combined_tools = list(self._agent._tools_schema)
                            self._system_prompt = self._agent.build_skill_plugin_prompt(self._system_prompt_base)
                            messages[0] = {"role": "system", "content": self._system_prompt}
                            self.llm.set_tools(self._combined_tools)
                            logger.info("Skill plugin activated: %s (%d tools visible)",
                                        skill_name, len(self._combined_tools))
                    elif tool_name == "skill_unload":
                        skill_name = args.get("name", "")
                        if skill_name and self._agent._active_skill_plugin:
                            self._agent.remove_skill_plugin()
                            self._combined_tools = list(self._agent._tools_schema)
                            self._system_prompt = self._system_prompt_base
                            messages[0] = {"role": "system", "content": self._system_prompt}
                            self.llm.set_tools(self._combined_tools)
                            logger.info("Skill plugin deactivated: %s", skill_name)

                # If more tools remain, feed result back so LLM can adjust
                if pending:
                    yield {"type": "status", "content": f"Step {step_id} 完成，继续下一步...",
                           "step_id": step_id}
                    try:
                        inner_resp = await asyncio.wait_for(
                            self.llm.chat(messages),
                            timeout=_LLM_TIMEOUT,
                        )
                        if inner_resp.tool_calls:
                            # LLM may adjust the plan based on this step's result
                            pending = list(inner_resp.tool_calls)
                    except Exception:
                        logger.warning("LLM call failed during step continuation, continuing with remaining tools")

            # Compact messages if over limit (always keep recent context)
            messages = await self._compact_messages(messages)

        yield {"type": "done", "content": "Max turns exceeded."}

    def _build_turn_messages(self, messages: list, content: str,
                              tool_calls: list, tool_result_entries: list,
                              thinking: str = "") -> list:
        """Build messages for this turn: assistant msg with tool_calls + tool results."""
        assistant_msg: dict = {"role": "assistant", "content": content}
        if thinking:
            assistant_msg["reasoning_content"] = thinking
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
                    await self._memory_mgr.add_to_hot(hot_msg)
                except Exception as exc:
                    logger.warning("Failed to store compaction summary: %s", exc)

        return trimmed


def _record_llm_metrics(metrics_collector, llm, duration_ms: float, response: LLMResponse | None = None, error: str = ""):
    """Record LLM call metrics if a collector is available."""
    if not metrics_collector:
        return
    try:
        model = getattr(getattr(llm, '_config', None), 'model_id', 'unknown')
        if response:
            metrics_collector.record_llm_call(
                model=model,
                duration_ms=duration_ms * 1000,
                finish_reason=response.finish_reason,
                tool_call_count=len(response.tool_calls),
                content_length=len(response.content or ""),
            )
        else:
            metrics_collector.record_llm_call(
                model=model,
                duration_ms=duration_ms * 1000,
                finish_reason="",
                error=error or "unknown",
            )
    except Exception as exc:
        logger.warning("Failed to record LLM metrics: %s", exc)


_StepOutcome = StepOutcome

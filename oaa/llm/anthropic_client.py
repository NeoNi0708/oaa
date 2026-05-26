"""Anthropic Messages API client — streaming with tool use support."""
import json
from dataclasses import dataclass, field

from ..config import ModelConfig
from ..logging_config import get_logger

logger = get_logger("llm.anthropic")


@dataclass
class AnthropicResponse:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    thinking: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


def _openai_tool_to_anthropic(tool: dict) -> dict:
    """Convert OpenAI-style tool definition to Anthropic format."""
    fn = tool["function"]
    params = fn.get("parameters", {"type": "object", "properties": {}})
    required = params.get("required")
    input_schema = {"type": "object", "properties": params.get("properties", {})}
    if required:
        input_schema["required"] = required
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": input_schema,
    }


def _convert_messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert internal message format to Anthropic API format.

    Internal format uses OpenAI-standard ``tool_calls`` (assistant) and
    ``role: "tool"`` with ``tool_call_id`` (tool results).
    Anthropic uses content blocks with ``tool_use`` and ``tool_result`` types.

    Returns (system_prompt, anthropic_messages).
    """
    system_prompt = ""
    result = []

    for m in messages:
        role = m["role"]
        if role == "system":
            system_prompt = m.get("content", "")
            continue

        if role == "assistant" and m.get("tool_calls"):
            # Convert assistant message with tool_calls to Anthropic content blocks
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                args_str = tc["function"]["arguments"]
                inp = json.loads(args_str) if isinstance(args_str, str) else args_str
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": inp,
                })
            result.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            # Convert OpenAI standard tool result to Anthropic tool_result block
            result.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": str(m.get("content", "")),
            }]})
        else:
            # Plain text message
            result.append({"role": role, "content": m.get("content", "")})

    return system_prompt, result


class AnthropicClient:
    """Async Anthropic Messages API client with streaming and tool use."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self._tools: list[dict] = []
        self._anthropic_tools: list[dict] = []
        import httpx
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=10),
        )

    def set_tools(self, tools: list):
        self._tools = tools
        self._anthropic_tools = [_openai_tool_to_anthropic(t) for t in tools]

    async def chat(self, messages: list[dict]) -> AnthropicResponse:
        """Call Anthropic Messages API with streaming. Returns AnthropicResponse."""
        import anthropic

        client = anthropic.AsyncAnthropic(
            base_url=self.config.base_url.rstrip("/"),
            api_key=self.config.api_key,
            http_client=self._http_client,
        )

        system_prompt, api_messages = _convert_messages_to_anthropic(messages)

        kwargs: dict = {
            "model": self.config.model_id,
            "max_tokens": self.config.max_tokens,
            "messages": api_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if self._anthropic_tools:
            kwargs["tools"] = self._anthropic_tools

        logger.debug("anthropic chat: model=%s msg_count=%d tool_count=%d",
                       self.config.model_id, len(api_messages), len(self._anthropic_tools))

        text_content = ""
        tool_use_blocks: dict[int, dict] = {}
        thinking_content = ""
        stop_reason = ""

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        text_content += event.delta.text
                    elif event.delta.type == "thinking_delta":
                        thinking_content += event.delta.thinking
                    elif event.delta.type == "input_json_delta":
                        idx = event.index
                        if idx not in tool_use_blocks:
                            tool_use_blocks[idx] = {"id": "", "name": "", "arguments": ""}
                        tool_use_blocks[idx]["arguments"] += event.delta.partial_json
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        idx = event.index
                        tool_use_blocks[idx] = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "arguments": "",
                        }
                elif event.type == "message_delta":
                    if event.delta.stop_reason:
                        stop_reason = event.delta.stop_reason
                    if hasattr(event, 'usage') and event.usage:
                        usage = {
                            "input_tokens": getattr(event.usage, 'input_tokens', None),
                            "output_tokens": getattr(event.usage, 'output_tokens', None),
                        }

        from .client import ToolCall, ToolCallFunction
        result_tool_calls = []
        for idx in sorted(tool_use_blocks):
            tb = tool_use_blocks[idx]
            result_tool_calls.append(ToolCall(
                id=tb["id"],
                function=ToolCallFunction(name=tb["name"], arguments=tb["arguments"]),
            ))

        logger.debug("anthropic chat done: text_len=%d tool_calls=%d stop_reason=%s usage=%s",
                       len(text_content), len(result_tool_calls), stop_reason, usage)
        return AnthropicResponse(
            content=text_content,
            tool_calls=result_tool_calls,
            thinking=thinking_content,
            finish_reason=stop_reason,
            usage=usage,
        )

    async def close(self):
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

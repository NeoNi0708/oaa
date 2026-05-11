"""LLM client — unified factory for OpenAI-compatible and Anthropic APIs."""
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from ..config import ModelConfig
from ..logging_config import get_logger

logger = get_logger("llm")


@dataclass
class ToolCallFunction:
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCall:
    id: str = ""
    function: ToolCallFunction = field(default_factory=ToolCallFunction)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    thinking: str = ""


class _OpenAIClient:
    """OpenAI-compatible streaming client."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self._client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        self._tools: list = []

    def set_tools(self, tools: list):
        self._tools = tools

    async def chat(self, messages: list) -> LLMResponse:
        kwargs: dict = {"model": self.config.model_id, "messages": messages, "stream": True}
        if self._tools:
            kwargs["tools"] = self._tools

        logger.debug("openai chat: model=%s msg_count=%d tool_count=%d",
                       self.config.model_id, len(messages), len(self._tools))

        stream = await self._client.chat.completions.create(**kwargs)
        content = ""
        tool_calls: dict[int, dict] = {}
        thinking = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            if delta.content:
                content += delta.content
            if getattr(delta, "reasoning_content", None):
                thinking += delta.reasoning_content
            for tc in (delta.tool_calls or []):
                idx = tc.index
                if tc.id:
                    tool_calls.setdefault(idx, {"id": tc.id, "function": {"name": "", "arguments": ""}})
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                if tc.function:
                    if tc.function.name:
                        tool_calls[idx]["function"]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        result_tool_calls = []
        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            result_tool_calls.append(ToolCall(
                id=tc["id"],
                function=ToolCallFunction(name=tc["function"]["name"], arguments=tc["function"]["arguments"]),
            ))

        logger.debug("openai chat done: text_len=%d tool_calls=%d", len(content), len(result_tool_calls))
        return LLMResponse(content=content, tool_calls=result_tool_calls, thinking=thinking)


class LLMClient:
    """Unified LLM client — delegates to OpenAI or Anthropic backend based on config.api_format."""

    def __init__(self, config: ModelConfig):
        self._config = config
        self._backend = self._build_backend()

    def _build_backend(self):
        fmt = self._config.api_format or "openai"
        if fmt == "anthropic":
            from .anthropic_client import AnthropicClient
            return AnthropicClient(self._config)
        return _OpenAIClient(self._config)

    def reconfigure(self, config: ModelConfig):
        """Hot-reload the backend after config changes (API key, base URL, model, etc.)."""
        self._config = config
        self._backend = self._build_backend()

    def set_tools(self, tools: list):
        self._backend.set_tools(tools)

    async def chat(self, messages: list) -> LLMResponse:
        response = await self._backend.chat(messages)
        # Normalise to LLMResponse
        if isinstance(response, LLMResponse):
            return response
        return LLMResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            thinking=response.thinking,
        )

"""LLM client — unified factory for OpenAI-compatible and Anthropic APIs."""
from dataclasses import dataclass, field

import openai
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
    finish_reason: str = ""  # "stop" | "length" | "tool_calls" | "content_filter" | "end_turn" | "max_tokens"


class _OpenAIClient:
    """OpenAI-compatible streaming client."""

    def __init__(self, config: ModelConfig):
        self.config = config
        base_url = config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            base_url = base_url[:-len("/chat/completions")]
        import httpx
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=10),
        )
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=config.api_key,
            http_client=http_client,
            max_retries=0,
        )
        self._tools: list = []

    def set_tools(self, tools: list):
        self._tools = tools

    async def chat(self, messages: list) -> LLMResponse:
        import asyncio as _asyncio
        import random as _random
        kwargs: dict = {"model": self.config.model_id, "messages": messages, "stream": True}
        if self._tools:
            kwargs["tools"] = self._tools
        if self.config.max_tokens:
            kwargs["max_tokens"] = self.config.max_tokens

        logger.debug("openai chat: model=%s msg_count=%d tool_count=%d max_tokens=%d url=%s",
                       self.config.model_id, len(messages), len(self._tools),
                       self.config.max_tokens, self.config.base_url)

        last_exc = None
        for attempt in range(4):  # 1 initial + 3 retries
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                break
            except openai.RateLimitError as exc:
                last_exc = exc
                if attempt < 3:
                    wait = (2 ** attempt) + _random.random()
                    logger.warning("Rate limit (attempt %d/3), retrying in %.1fs", attempt + 1, wait)
                    await _asyncio.sleep(wait)
                    continue
                raise
            except openai.APIStatusError as exc:
                if exc.status_code in (502, 503) and attempt < 3:
                    wait = (2 ** attempt) + _random.random()
                    logger.warning("API %d (attempt %d/3), retrying in %.1fs", exc.status_code, attempt + 1, wait)
                    await _asyncio.sleep(wait)
                    continue
                raise
        else:
            # All retries exhausted
            if last_exc:
                raise last_exc
        content = ""
        tool_calls: dict[int, dict] = {}
        thinking = ""
        finish_reason = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                # Capture finish_reason from the last choice (non-delta chunk)
                if chunk.choices and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
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
            # Capture finish_reason if present on this chunk
            if chunk.choices and chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        result_tool_calls = []
        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            result_tool_calls.append(ToolCall(
                id=tc["id"],
                function=ToolCallFunction(name=tc["function"]["name"], arguments=tc["function"]["arguments"]),
            ))

        logger.debug("openai chat done: text_len=%d tool_calls=%d finish_reason=%s",
                       len(content), len(result_tool_calls), finish_reason)
        return LLMResponse(content=content, tool_calls=result_tool_calls, thinking=thinking, finish_reason=finish_reason)


class LLMClient:
    """Unified LLM client — delegates to OpenAI or Anthropic backend.

    Protocol auto-detection::
        1. ``api_format`` config field (if set) takes precedence.
        2. Otherwise, ``base_url`` is inspected for known providers.
        3. Falls back to OpenAI-compatible for everything else.
    """

    _ANTHROPIC_DOMAINS = frozenset({"anthropic.com"})

    def __init__(self, config: ModelConfig):
        self._config = config
        self._backend = self._build_backend()

    @classmethod
    def _detect_api_format(cls, config: ModelConfig) -> str:
        """Detect API format from config, falling back to URL-based heuristic."""
        if config.api_format:
            return config.api_format
        if not config.base_url:
            return "openai"
        url = config.base_url.lower()
        for domain in cls._ANTHROPIC_DOMAINS:
            if domain in url:
                return "anthropic"
        return "openai"

    def _build_backend(self):
        fmt = self._detect_api_format(self._config)
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
            finish_reason=response.finish_reason if hasattr(response, "finish_reason") else "",
        )

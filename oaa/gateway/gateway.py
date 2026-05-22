"""Gateway — routes messages between channels and agent core."""
import asyncio

from ..agent.oaa_agent import OAAAgent
from ..logging_config import get_logger
from ..session.manager import SessionManager

logger = get_logger("gateway")


class Message:
    """Inbound message from any channel."""

    def __init__(self, source: str, user_id: str, content: str,
                 metadata: dict = None, session_id: str = "",
                 images: list[str] | None = None):
        self.source = source
        self.user_id = user_id
        self.content = content
        self.metadata = metadata or {}
        self.session_id = session_id
        self.images = images or []  # base64 data URIs for multimodal LLM


class Gateway:
    """Unified gateway — manages adapters, sessions, and agent communication."""

    def __init__(self, agent: OAAAgent, session_mgr: SessionManager):
        self.agent = agent
        self.session_mgr = session_mgr
        self._adapters: dict = {}

    def register_adapter(self, name: str, adapter):
        """Register a channel adapter. Adapter instance receives a gateway reference
        for sending responses back through the channel."""
        self._adapters[name] = adapter
        if hasattr(adapter, 'gateway'):
            adapter.gateway = self

    async def incoming_message(self, msg: Message):
        """Process incoming message from any channel.

        Yields dict chunks from the agent pipeline.
        """
        # Get or create session
        session_id = msg.session_id or self.session_mgr.get_or_create_session(
            msg.user_id, msg.source)
        msg.session_id = session_id

        # Load conversation history from session (excluding current message)
        raw_history = self.session_mgr.get_messages(session_id, limit=50)
        history = [
            {"role": r["role"], "content": r["content"]}
            for r in raw_history
        ]

        # Save user message
        self.session_mgr.add_message(session_id, msg.source, "user", msg.content, msg.metadata)
        logger.info("Message from %s/%s: %s...", msg.source, msg.user_id, msg.content[:60])

        # Send to agent
        response = ""
        try:
            async for chunk in self.agent.process_message(msg.content, history=history):
                if chunk["type"] == "done":
                    response = chunk.get("content", "")
                yield chunk
        except asyncio.CancelledError:
            yield {"type": "done", "content": ""}
            return
        except Exception as exc:
            logger.error("Agent processing failed: %s", exc)
            yield {"type": "done", "content": f"处理消息时出错: {exc}"}
            return

        # Save assistant response (skip system errors to avoid context pollution)
        if response and not response.startswith("[系统错误]"):
            self.session_mgr.add_message(session_id, msg.source, "assistant", response)

    async def send_to_channel(self, source: str, user_id: str, content: str,
                              session_id: str = ""):
        """Route response back to the originating channel."""
        adapter = self._adapters.get(source)
        if adapter:
            await adapter.send_message(user_id, content, session_id)
        else:
            logger.warning("No adapter for source: %s", source)

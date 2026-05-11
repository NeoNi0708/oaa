"""Desktop WebSocket adapter — communicates with Vue3+Electron GUI.

Supports three message categories:

1. **Chat messages** (``type: "message"``) — forwarded to the agent pipeline
   via :meth:`Gateway.incoming_message`.  Chat processing runs in a background
   ``asyncio.Task`` so that the WebSocket loop stays responsive for management
   requests while the LLM is thinking.

2. **Management requests** (``type: "get_config"``, ``"save_config"``, etc.) —
   handled synchronously by :class:`ManagementHandler` and answered
   immediately with a response that echoes the ``request_id`` from the
   original request.

3. **User confirmation** — when the agent needs human approval (e.g. before
   sending email), a ``confirm_request`` is pushed to the GUI and the chat
   task waits for a ``confirm_response`` before resuming.
"""
import asyncio
import contextvars
import json
import uuid
from typing import TYPE_CHECKING, Callable, Coroutine

if TYPE_CHECKING:
    from ..management import ManagementHandler

try:
    from websockets.server import WebSocketServerProtocol
except ImportError:
    WebSocketServerProtocol = None

from ...logging_config import get_logger

logger = get_logger("gateway.desktop")

# Context variable that holds the current WebSocket connection for the
# active chat task.  Used by confirm_callback to know which GUI client
# to send the confirmation dialog to.
_current_ws: contextvars.ContextVar = contextvars.ContextVar("desktop_current_ws")

# Management message types that skip the agent pipeline
_MANAGEMENT_TYPES = {
    "get_config", "save_config",
    "get_tasks", "save_task", "delete_task", "toggle_task",
    "get_skills", "get_evolution",
    "qr_login", "poll_qr",
    "get_status",
    "switch_model", "get_models",
    "stop_chat",
    "apply_evolution",
}


class DesktopAdapter:
    """WebSocket adapter for Vue3+Electron GUI communication."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9765):
        self.host = host
        self.port = port
        self.gateway = None
        self._management = None          # set by app during bootstrap
        self._clients = set()
        self._server = None
        # Pending user confirmations: request_id → Future[bool]
        self._pending_confirms: dict[str, asyncio.Future] = {}

    def set_management_handler(self, handler: "ManagementHandler"):
        """Register the management handler for non-chat requests."""
        self._management = handler

    def create_confirm_callback(self) -> Callable[[str, str], Coroutine]:
        """Return an async callback suitable for ``PermissionsManager``.

        The callback sends a ``confirm_request`` to the GUI (via the
        WebSocket of the currently-active chat task), then waits for a
        ``confirm_response`` before returning ``True`` or ``False``.
        """

        async def _confirm(operation: str, details: str) -> bool:
            ws = _current_ws.get(None)
            if ws is None:
                logger.warning("confirm_callback called outside a chat task")
                return False

            request_id = uuid.uuid4().hex[:12]
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending_confirms[request_id] = fut

            try:
                await ws.send(json.dumps({
                    "type": "confirm_request",
                    "request_id": request_id,
                    "payload": {
                        "operation": operation,
                        "details": details,
                    },
                }, ensure_ascii=False))
            except Exception as exc:
                logger.debug("Failed to send confirm_request: %s", exc)
                self._pending_confirms.pop(request_id, None)
                return False

            try:
                # Wait up to 60 seconds for the user to respond
                result = await asyncio.wait_for(fut, timeout=60.0)
                return result
            except asyncio.TimeoutError:
                logger.info("User confirmation timed out for %s", operation)
                return False
            finally:
                self._pending_confirms.pop(request_id, None)

        return _confirm

    async def start(self):
        import websockets
        self._server = await websockets.serve(self._handler, self.host, self.port)
        logger.info("WebSocket server started on ws://%s:%s", self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # Reject any pending confirmations so tasks don't hang
        for rid, fut in self._pending_confirms.items():
            if not fut.done():
                fut.set_result(False)
        self._pending_confirms.clear()
        logger.info("WebSocket server stopped")

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _handler(self, websocket):
        self._clients.add(websocket)
        remote = websocket.remote_address if hasattr(websocket, 'remote_address') else "?"
        logger.debug("Client connected: %s", remote)
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("Invalid JSON from client %s: %s", remote, exc)
                    continue

                msg_type = data.get("type", "message")

                # --- User confirmation response ---
                if msg_type == "confirm_response":
                    self._resolve_confirm(data)
                    continue

                # --- Management requests: handle inline in the event loop ---
                if msg_type in _MANAGEMENT_TYPES:
                    await self._handle_management(websocket, data)
                    continue

                # --- Chat message: run in background task ---
                if msg_type == "message":
                    asyncio.create_task(self._process_chat(websocket, data))
                    continue

        except Exception as exc:
            logger.warning("Connection closed: %s — %s", remote, exc)
        finally:
            self._clients.discard(websocket)

    # ------------------------------------------------------------------
    # User confirmation
    # ------------------------------------------------------------------

    def _resolve_confirm(self, data: dict):
        """Resolve a pending confirmation Future from a GUI response."""
        request_id = data.get("request_id", "")
        confirmed = data.get("payload", {}).get("confirmed", False)
        fut = self._pending_confirms.get(request_id)
        if fut and not fut.done():
            fut.set_result(confirmed)

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    async def _handle_management(self, websocket, data: dict):
        """Handle a management request and send the response back inline."""
        request_id = data.get("request_id", "")
        msg_type = data.get("type", "")
        payload = data.get("payload", {})

        if self._management is None:
            await self._send_response(websocket, msg_type, request_id,
                                      {"ok": False, "error": "No management handler registered"})
            return

        result = self._management.handle(msg_type, payload)
        await self._send_response(websocket, msg_type, request_id, result)

    @staticmethod
    async def _send_response(websocket, msg_type: str, request_id: str, result: dict):
        response = {
            "type": msg_type + "_resp",
            "request_id": request_id,
            "payload": result,
        }
        try:
            await websocket.send(json.dumps(response, ensure_ascii=False))
        except Exception as exc:
            logger.debug("Failed to send management response: %s", exc)

    # ------------------------------------------------------------------
    # Chat processing (background task)
    # ------------------------------------------------------------------

    async def _process_chat(self, websocket, data: dict):
        """Process a chat message in a background asyncio task.

        This keeps the WebSocket main loop responsive so that management
        requests (config, tasks, …) are not blocked while the LLM runs.
        """
        content = data.get("payload", {}).get("content", "")
        if not content.strip():
            return

        # Set the context variable so confirm_callback knows which
        # WebSocket to address.
        token = _current_ws.set(websocket)

        from ..gateway import Message

        msg = Message("desktop", "local_user", content)
        if self._management:
            self._management.set_agent_state("thinking")

        try:
            async for chunk in self.gateway.incoming_message(msg):
                chunk_type = chunk.get("type", "")
                if chunk_type == "tool_call" and self._management:
                    self._management.set_agent_state("executing")
                elif chunk_type == "llm_output" and self._management:
                    self._management.set_agent_state("responding")

                await self._send_chunk(websocket, chunk)
        except Exception as exc:
            logger.exception("Chat processing failed: %s", exc)
            if self._management:
                self._management.set_agent_state("idle")
            try:
                await websocket.send(json.dumps({
                    "type": "done",
                    "payload": {"content": f"处理消息时出错: {exc}"},
                }, ensure_ascii=False))
            except Exception:
                pass
        finally:
            _current_ws.reset(token)
            if self._management:
                self._management.set_agent_state("idle")

    # ------------------------------------------------------------------
    # Chunk forwarding
    # ------------------------------------------------------------------

    async def _send_chunk(self, websocket, chunk: dict):
        """Send a single agent chunk to the WebSocket client."""
        msg_type = chunk["type"]
        if msg_type == "done":
            payload = {"type": "done", "payload": {"content": chunk["content"]}}
        elif msg_type == "tool_call":
            payload = {"type": "tool_call", "payload": {"name": chunk["name"], "args": chunk["args"]}}
        elif msg_type == "status":
            payload = {"type": "status", "payload": {"content": chunk["content"]}}
        elif msg_type == "llm_output":
            payload = {"type": "llm_output", "payload": {"content": chunk["content"]}}
        elif msg_type == "tool_result":
            payload = {"type": "tool_result", "payload": {"name": chunk.get("name", ""), "result": chunk.get("result", "")}}
        elif msg_type == "qr_code":
            payload = {"type": "qr_code", "payload": {"url": chunk.get("url", ""), "channel": chunk.get("channel", ""), "state": chunk.get("state", "")}}
        else:
            return
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.debug("Send failed, removing client: %s", exc)
            self._clients.discard(websocket)

    async def send_message(self, user_id: str, content: str, session_id: str = ""):
        """Send message to desktop client."""
        msg = json.dumps({
            "type": "message",
            "payload": {"content": content, "session_id": session_id},
        }, ensure_ascii=False)
        for ws in self._clients.copy():
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

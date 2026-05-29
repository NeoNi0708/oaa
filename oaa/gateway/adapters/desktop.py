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
    "get_tasks", "save_task", "delete_task", "toggle_task", "get_task_history",
    "get_skills", "get_skill_detail", "switch_skill", "get_evolution",
    "qr_login", "poll_qr", "reconnect_channel",
    "get_status",
    "switch_model", "get_models",
    "stop_chat",
    "apply_evolution",
    "list_proposals",
    "proposal_approve",
    "proposal_ignore",
    "get_evolution_stats",
    "get_metrics",
    "list_emails",
    "save_email",
    "delete_email",
    "test_email",
    # Chat bubble rich content
    "chat_action",
    "get_action_status",
    # User preferences
    "list_preferences",
    "update_preference",
    "delete_preference",
    # Runtime patches
    "list_patches",
    "remove_patch",
    # Memory store
    "list_memories",
    "delete_memory",
    "get_memory_stats",
    "submit_survey",
    "submit_choice",
}


class DesktopAdapter:
    """WebSocket adapter for Vue3+Electron GUI communication."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9765):
        self.host = host
        self.port = port
        self.gateway = None
        self._management = None          # set by app during bootstrap
        self._worker = None              # set by app for background tasks
        self._clients = set()
        self._server = None
        # Optional callback fired when first client connects (set by app)
        self._on_first_client = None
        # Pending user confirmations: request_id → Future[bool]
        self._pending_confirms: dict[str, asyncio.Future] = {}
        # Active chat tasks per websocket — cancelled when new message arrives
        self._chat_tasks: dict[int, asyncio.Task] = {}

    def set_management_handler(self, handler: "ManagementHandler"):
        """Register the management handler for non-chat requests."""
        self._management = handler
        # Register for push notifications from background tasks
        handler.on_push_notification(self._broadcast_push)

    def _broadcast_push(self, msg_type: str, payload: dict):
        """Broadcast a push notification to all connected WebSocket clients."""
        msg = json.dumps({
            "type": msg_type,
            "payload": payload,
        }, ensure_ascii=False)
        dead = set()
        for ws in tuple(self._clients):
            try:
                asyncio.create_task(ws.send(msg))
            except Exception:
                dead.add(ws)
        self._clients -= dead

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
        # Fire first-client hook (startup check, etc.)
        if len(self._clients) == 1 and self._on_first_client:
            asyncio.create_task(self._on_first_client())
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

                # --- Chat message: cancel old task, run in background ---
                if msg_type == "message":
                    ws_id = id(websocket)
                    old = self._chat_tasks.pop(ws_id, None)
                    if old and not old.done():
                        old.cancel()
                        logger.debug("Cancelled previous chat task for client %s", remote)
                    task = asyncio.create_task(self._process_chat(websocket, data))
                    self._chat_tasks[ws_id] = task
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

        # Intercept stop_chat — cancel the running chat task for this client,
        # then return immediately (management handler has nothing more to do).
        if msg_type == "stop_chat":
            ws_id = id(websocket)
            task = self._chat_tasks.get(ws_id)
            if task and not task.done():
                task.cancel()
                logger.debug("Cancelled chat task for stop_chat (client %s)", websocket.remote_address)
            return

        if self._management is None:
            await self._send_response(websocket, msg_type, request_id,
                                      {"ok": False, "error": "No management handler registered"})
            return

        result = self._management.handle(msg_type, payload)
        if asyncio.iscoroutine(result):
            result = await result

        # Broadcast config_updated to other connected clients after a successful save
        if msg_type == "save_config" and isinstance(result, dict) and result.get("ok"):
            await self._broadcast_config_updated(websocket)

        await self._send_response(websocket, msg_type, request_id, result)

        # If a management action was forwarded to the agent, process it as a chat message
        if (msg_type in ("chat_action", "submit_survey", "submit_choice")
                and isinstance(result, dict)
                and result.get("status") == "forwarded_to_agent"
                and result.get("user_message")):
            asyncio.create_task(
                self._process_chat(websocket, {
                    "type": "message",
                    "payload": {"content": result["user_message"]},
                })
            )

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
        payload = data.get("payload", {})
        content = payload.get("content", "")
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
        except asyncio.CancelledError:
            # If this task was replaced by a new message, silently exit —
            # the new task manages its own state.  Only send done for
            # explicit stop_chat (where no replacement task exists).
            ws_id = id(websocket)
            if self._chat_tasks.get(ws_id) is not asyncio.current_task():
                return  # replaced by newer message task
            try:
                await websocket.send(json.dumps({
                    "type": "done",
                    "payload": {"content": ""},
                }, ensure_ascii=False))
            except Exception:
                pass
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
            route = chunk.get("route")
            payload_data = {"content": chunk["content"]}
            if route:
                payload_data["route"] = route
            payload = {"type": "done", "payload": payload_data}
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
        elif msg_type == "survey":
            payload = {"type": "survey", "payload": {k: v for k, v in chunk.items() if k != "type"}}
        else:
            return
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.debug("Send failed, removing client: %s", exc)
            self._clients.discard(websocket)

    async def _broadcast_config_updated(self, sender_ws):
        """Notify all connected clients (except the sender) that config changed."""
        msg = json.dumps({
            "type": "config_updated",
            "payload": {"msg": "配置已更新"},
        }, ensure_ascii=False)
        for ws in self._clients.copy():
            if ws != sender_ws:
                try:
                    await ws.send(msg)
                except Exception:
                    self._clients.discard(ws)

    async def notify_all(self, content: str, msg_type: str = "llm_output"):
        """Broadcast a notification to all connected GUI clients.

        Used by IdleInspector background task to push proposals.
        Defaults to ``llm_output`` type so the GUI renders it as assistant output.
        """
        msg = json.dumps({
            "type": msg_type,
            "payload": {"content": content},
        }, ensure_ascii=False)
        for ws in self._clients.copy():
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

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

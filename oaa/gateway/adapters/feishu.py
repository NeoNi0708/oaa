"""Feishu (Lark) adapter — message receive/send via lark-oapi SDK.

Uses ``lark.ws.Client`` for receiving messages via WebSocket event subscription,
and ``lark.Client`` (REST) for sending messages.

QR login uses the `lark-cli` device auth flow (auto-installed via npm) for a
seamless scan-and-authorize experience.  Falls back to OAuth redirect QR when
the CLI is unavailable.

All HTTP calls are offloaded to a thread pool via ``asyncio.to_thread`` so the
event loop remains responsive.  The synchronous lark-oapi WebSocket callback
bridges to the async gateway pipeline via ``asyncio.run_coroutine_threadsafe``.

Credentials (App ID / App Secret) come from creating a self-built app
at https://open.feishu.cn.
"""
import asyncio
import json
import threading
import time
from typing import Any

import lark_oapi as lark
import requests
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from ...logging_config import get_logger
from ..gateway import Message
from .feishu_cli import FeishuCLI

logger = get_logger("gateway.feishu")


class FeishuAdapter:
    """Feishu (Lark) channel adapter — WebSocket event subscription for receiving,
    REST API for sending.

    Usage:
        1. Create a self-built app on open.feishu.cn
        2. Enable permissions: ``im:message``, ``contact:user.base:readonly``, etc.
        3. Subscribe to ``im.message.receive_v1`` event (WebSocket mode)
        4. Pass App ID / App Secret to this adapter
        5. Call :meth:`get_qrcode` for OAuth login QR, then
           :meth:`handle_oauth_callback` to exchange the code for user info
        6. Call :meth:`start` to begin receiving messages via WebSocket
    """

    FEISHU_OPEN_BASE = "https://open.feishu.cn"
    OAUTH_AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/index"

    def __init__(self, app_id: str = "", app_secret: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.gateway = None
        self._ws_client: Any = None
        self._rest_client: Any = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._tenant_token = ""
        self._token_expires_at = 0.0
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._lark_cli = FeishuCLI()
        self._using_lark_cli = False
        self._device_code = ""
        self._poll_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # QR-code OAuth login
    # ------------------------------------------------------------------

    async def get_qrcode(self) -> dict:
        """Generate a Feishu login QR code via lark-cli device auth.

        Uses lark-cli ``auth login --recommend --no-wait --json``.
        Auto-installs lark-cli via npm if needed, and configures App ID/Secret.

        Returns:
            dict with ``qrcode_url`` (base64 PNG data URI) and
            ``qrcode_id`` (device_code for polling), or ``error`` on failure.
        """
        # Cancel any previous poll task
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        self._using_lark_cli = False
        self._device_code = ""

        installed = await self._lark_cli.ensure_installed()
        if not installed:
            return {"error": "lark-cli 未安装且自动安装失败。请手动执行: npm install -g @larksuite/cli"}

        configured = await self._lark_cli.ensure_configured(
            self.app_id, self.app_secret
        )
        if not configured:
            detail = self._lark_cli.last_error
            msg = f"lark-cli 配置失败: {detail}" if detail else "lark-cli 配置失败"
            msg += "。请检查 App ID 和 App Secret 是否正确，并确保飞书应用已启用机器人和设备授权能力。"
            return {"error": msg}

        result = await self._lark_cli.get_qrcode()
        if "error" in result:
            detail = result["error"]
            if isinstance(detail, dict):
                detail = detail.get("message", str(detail))
            return {"error": f"lark-cli 设备授权失败: {detail}。请确认飞书应用已开启 '设备授权' 能力。"}

        self._using_lark_cli = True
        self._device_code = result.get("qrcode_id", "")

        # Start background task to wait for device auth completion.
        # lark-cli auth login --device-code blocks until user scans or code expires,
        # so it must run in a background task, not in the poll handler.
        self._poll_task = asyncio.create_task(
            self._lark_cli._run(
                ["auth", "login", "--device-code", self._device_code, "--json"]
            )
        )

        return {
            "qrcode_url": result["qrcode_url"],
            "qrcode_id": result["qrcode_id"],
        }

    async def poll_qrcode_status(self, qrcode_id: str) -> dict:
        """Poll QR code scan status.

        Checks the background device-auth task started by :meth:`get_qrcode`.
        The lark-cli ``--device-code`` command blocks until the user scans or
        the device code expires, so it runs in a background ``asyncio.Task``.

        Args:
            qrcode_id: The ``device_code`` from :meth:`get_qrcode`.

        Returns:
            dict with ``status`` ``"waiting"``, ``"confirmed"``, ``"expired"``,
            or ``"error"``.
        """
        if not self._poll_task:
            return {"status": "waiting"}
        if not self._poll_task.done():
            return {"status": "waiting"}

        # Background task finished — check result
        try:
            result = self._poll_task.result()
        except asyncio.CancelledError:
            return {"status": "waiting"}
        except Exception as e:
            logger.error("[Feishu] Auth poll task error: %s", e)
            return {"status": "error", "msg": str(e)}

        if result.get("ok", False):
            return {"status": "confirmed", **result}

        error = result.get("error", {})
        if isinstance(error, dict):
            err_msg = (error.get("message", "") or str(error)).lower()
            if "expired" in err_msg:
                return {"status": "expired"}
            return {"status": "error", "msg": error.get("message", "")}

        err_str = str(error).lower()
        if "expired" in err_str:
            return {"status": "expired"}
        return {"status": "error", "msg": str(error)}

    async def handle_oauth_callback(self, code: str) -> dict:
        """Step 2: Exchange an OAuth authorization code for the user's identity.

        Calls the Feishu OAuth token endpoint, then fetches the user profile.

        Returns:
            dict with ``status`` ``"ok"`` + user info (``user_id``, ``open_id``,
            ``union_id``, ``name``, ``avatar``) on success, or ``status``
            ``"error"`` on failure.
        """
        try:
            # Exchange code for access token
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.FEISHU_OPEN_BASE}/open-apis/authen/v1/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                },
                auth=(self.app_id, self.app_secret),
                timeout=10,
            )
            resp.raise_for_status()
            token_data = resp.json()

            if token_data.get("code") != 0:
                return {
                    "status": "error",
                    "msg": token_data.get("msg", "Token exchange failed"),
                }

            access_token = token_data.get("data", {}).get("access_token", "")
            if not access_token:
                return {"status": "error", "msg": "No access_token in response"}

            # Fetch user info with the access token
            user_resp = await asyncio.to_thread(
                requests.get,
                f"{self.FEISHU_OPEN_BASE}/open-apis/authen/v1/user_info",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

            if user_data.get("code") != 0:
                return {
                    "status": "error",
                    "msg": user_data.get("msg", "Failed to get user info"),
                }

            user_info = user_data.get("data", {})
            return {
                "status": "ok",
                "user_id": user_info.get("user_id", ""),
                "open_id": user_info.get("open_id", ""),
                "union_id": user_info.get("union_id", ""),
                "name": user_info.get("name", ""),
                "avatar": user_info.get("avatar_url", ""),
            }
        except Exception as e:
            logger.error("[Feishu] OAuth callback error: %s", e)
            return {"status": "error", "msg": str(e)}

    # ------------------------------------------------------------------
    # Access token management (for tenant-level API calls)
    # ------------------------------------------------------------------

    async def _get_tenant_token(self) -> str:
        """Obtain or refresh a Feishu ``tenant_access_token``."""
        if self._tenant_token and time.time() < self._token_expires_at:
            return self._tenant_token

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.FEISHU_OPEN_BASE}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._tenant_token = data.get("tenant_access_token", "")
            expires_in = data.get("expire", 7200)
            self._token_expires_at = time.time() + expires_in - 120
            return self._tenant_token
        except Exception as e:
            logger.error("[Feishu] Failed to get tenant token: %s", e)
            return ""

    # ------------------------------------------------------------------
    # WebSocket event client (message receiving)
    # ------------------------------------------------------------------

    async def _process_incoming(self, msg: Message):
        """Process an incoming Feishu message through the gateway pipeline.

        Runs on the main event loop via ``run_coroutine_threadsafe``.
        """
        try:
            response = ""
            async for chunk in self.gateway.incoming_message(msg):
                if chunk["type"] == "done":
                    response = chunk.get("content", "")
            if response:
                await self.send_message(
                    msg.user_id,
                    response,
                    receive_id_type="open_id",
                )
        except Exception as exc:
            logger.error("[Feishu] Process error: %s", exc)

    async def start(self):
        """Start the Feishu WebSocket event client for receiving messages.

        Captures the current event loop for bridging sync→async.  Runs
        ``lark.ws.Client.start()`` in a daemon thread (the SDK's main
        loop is synchronous).
        """
        if not self.is_authenticated:
            logger.warning("[Feishu] Not configured — skipping start")
            return

        # Stop existing client before re-starting
        if self._ws_client is not None:
            self.stop()

        self._main_loop = asyncio.get_running_loop()

        def _on_message(data: P2ImMessageReceiveV1):
            """Handle an incoming ``im.message.receive_v1`` event."""
            if data.event is None or data.event.message is None:
                return

            message = data.event.message
            sender_id_obj = data.event.sender.sender_id if data.event.sender else None
            sender_id = sender_id_obj.open_id if sender_id_obj else ""

            msg_type = message.message_type or "text"
            content_raw = message.content or "{}"

            # Parse content JSON — for text it is {"text": "..."}
            try:
                content_data = json.loads(content_raw)
            except json.JSONDecodeError:
                content_data = {}

            if msg_type == "text":
                content = content_data.get("text", "")
            else:
                content = str(content_data)

            chat_type = message.chat_type or "p2p"

            if not content or not sender_id:
                return

            logger.info(
                "[Feishu] Message from %s (%s): %.60s",
                sender_id, msg_type, content,
            )

            msg = Message(
                source="feishu",
                user_id=sender_id,
                content=content,
                metadata={
                    "message_id": message.message_id,
                    "chat_id": message.chat_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                    "sender_id": sender_id,
                    "create_time": message.create_time,
                },
            )

            # Bridge from sync lark-oapi callback to the async gateway pipeline.
            # This is safe because run_coroutine_threadsafe schedules on the
            # main event loop that was captured during start().
            if self._main_loop is not None and not self._main_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._process_incoming(msg), self._main_loop,
                )

        try:
            # Build event handler
            event_handler = (
                EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(_on_message)
                .build()
            )

            # Build REST client for sending
            self._rest_client = (
                lark.Client.builder()
                .app_id(self.app_id)
                .app_secret(self.app_secret)
                .build()
            )

            # Build and start WebSocket client
            self._ws_client = lark.ws.Client(
                self.app_id,
                self.app_secret,
                event_handler=event_handler,
                auto_reconnect=True,
            )

            self._running = True
            self._connected = True
            self._thread = threading.Thread(
                target=self._ws_client.start,
                daemon=True,
                name="feishu-ws",
            )
            self._thread.start()
            logger.info("[Feishu] WebSocket event client started")
        except Exception as e:
            logger.error("[Feishu] Failed to start: %s", e)

    def stop(self):
        """Stop the Feishu WebSocket event client."""
        self._running = False
        self._connected = False
        # Cancel background QR poll task
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        if self._ws_client is not None:
            stop = getattr(self._ws_client, "stop", None)
            if stop is not None:
                try:
                    stop()
                except Exception as e:
                    logger.debug("[Feishu] Stop error: %s", e)
        logger.info("[Feishu] WS client stopped")

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(
        self,
        user_id: str,
        content: str,
        session_id: str = "",
        receive_id_type: str = "open_id",
    ) -> dict:
        """Send a Feishu message to *user_id*.

        Uses the ``lark-oapi`` REST client to call
        ``im.v1.message.create``.

        Args:
            user_id: The recipient's Open ID / User ID.
            content: Message text content.
            session_id: Optional session ID.
            receive_id_type: ``"open_id"`` (default), ``"union_id"``,
                ``"user_id"``, or ``"chat_id"``.

        Returns:
            dict with ``status`` ``"ok"`` on success or ``status``
            ``"error"`` on failure.
        """
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not configured"}

        # If REST client is not initialized, use direct HTTP
        if self._rest_client is None:
            return await self._send_message_http(user_id, content, receive_id_type)

        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(user_id)
                    .msg_type("text")
                    .content(json.dumps({"text": content}))
                    .build()
                )
                .build()
            )

            response = await asyncio.to_thread(self._rest_client.im.v1.message.create, request)
            if response.success():
                return {"status": "ok"}
            return {
                "status": "error",
                "msg": f"API error: code={response.code}, msg={response.msg}",
            }
        except Exception as e:
            logger.error("[Feishu] Send failed via SDK: %s", e)
            return await self._send_message_http(user_id, content, receive_id_type)

    async def _send_message_http(
        self,
        user_id: str,
        content: str,
        receive_id_type: str = "open_id",
    ) -> dict:
        """Fallback: send a Feishu message via direct REST API call."""
        token = await self._get_tenant_token()
        if not token:
            return {"status": "error", "msg": "No tenant token"}

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.FEISHU_OPEN_BASE}/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "receive_id": user_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": content}),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return {"status": "ok"}
            return {"status": "error", "msg": data.get("msg", "Unknown error")}
        except Exception as e:
            logger.error("[Feishu] Send failed via HTTP: %s", e)
            return {"status": "error", "msg": str(e)}

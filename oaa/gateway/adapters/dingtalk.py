"""DingTalk adapter — Stream mode message receive/send via dingtalk-stream SDK.

Uses dingtalk-stream SDK for receiving messages via WebSocket Stream,
and DingTalk Open REST API for sending messages.

All HTTP calls are offloaded to a thread pool via ``asyncio.to_thread`` so the
event loop remains responsive during authentication and message delivery.

Credentials (AppKey / AppSecret) come from creating an enterprise internal app
at https://open.dingtalk.com.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import threading
import time

import requests

from ...logging_config import get_logger
from ..gateway import Message
from .dingtalk_cli import DingTalkCLI

logger = get_logger("gateway.dingtalk")


class DingTalkAdapter:
    """DingTalk channel adapter — Stream connection for receiving, REST API for sending.

    Usage:
        1. Create an enterprise internal app on open.dingtalk.com
        2. Enable Robot capability & register Stream URL
        3. Pass AppKey / AppSecret to this adapter
        4. Call :meth:`get_qrcode` for QR login, then
           :meth:`poll_qrcode_status` to wait for user authorisation
        5. Call :meth:`start` to begin receiving messages via Stream
    """

    OPEN_API_BASE = "https://api.dingtalk.com"
    OAUTH_AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.gateway = None
        self._client = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._access_token = ""
        self._token_expires_at = 0.0
        self._webhooks: dict[str, str] = {}
        self._dws_cli = DingTalkCLI(client_id=client_id, client_secret=client_secret)
        self._using_dws = False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # QR-code login
    # ------------------------------------------------------------------

    async def get_qrcode(self) -> dict:
        """Generate a DingTalk login QR code.

        Primary path: use dws (dingtalk-workspace-cli) device auth
        (auto-installs CLI via npm if needed).  Falls back to OAuth
        redirect QR when dws cannot be installed.

        Returns:
            dict with ``qrcode_url`` (base64 PNG data URI) and
            ``qrcode_id`` (device code for polling).
        """
        self._using_dws = False

        # --- Try dws device auth path ---
        try:
            installed = await self._dws_cli.ensure_installed()
            if installed:
                result = await self._dws_cli.get_qrcode()
                if "error" not in result:
                    self._using_dws = True
                    return {
                        "qrcode_url": result["qrcode_url"],
                        "qrcode_id": result.get("qrcode_id", ""),
                        "user_code": result.get("user_code", ""),
                    }
                logger.warning(
                    "[DingTalk] dws device auth failed (%s), falling back to OAuth",
                    result.get("error", "unknown"),
                )
        except Exception as exc:
            logger.warning("[DingTalk] dws error (%s), falling back to OAuth QR", exc)

        # --- Fallback: OAuth redirect QR ---
        redirect_uri = "oaa://dingtalk/callback"
        state = str(int(time.time()))
        auth_url = (
            f"{self.OAUTH_AUTHORIZE_URL}"
            f"?appId={self.client_id}"
            f"&response_type=code"
            f"&scope=openid"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )
        try:
            import qrcode
            import io
            import base64
            img = qrcode.make(auth_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            return {"qrcode_url": data_uri, "qrcode_id": state, "state": state}
        except ImportError:
            return {"qrcode_url": auth_url, "state": state}

    async def poll_qrcode_status(self, qrcode_id: str) -> dict:
        """Poll QR code scan status.

        When dws device auth is active, polls the device authorization
        endpoint.  Otherwise returns ``"waiting"`` (the OAuth fallback has
        no polling mechanism).

        Args:
            qrcode_id: The ``device_code`` from :meth:`get_qrcode`.

        Returns:
            dict with ``status`` ``"waiting"``, ``"confirmed"``,
            ``"expired"``, or ``"error"``.
        """
        if self._using_dws:
            return await self._dws_cli.poll_qrcode_status(qrcode_id)

        # OAuth fallback: no polling
        return {"status": "waiting"}

    async def handle_oauth_callback(self, code: str) -> dict:
        """Exchange an OAuth authorization code for the user's identity.

        Uses HMAC-SHA256 signed request to ``/sns/getuserinfo_bycode``.
        Only used in the OAuth fallback path.

        Returns:
            dict with ``status`` ``"ok"`` + user info (``user_id``, ``name``,
            ``nick``, ``openid``) on success, or ``status`` ``"error"`` on
            failure.
        """
        timestamp = str(int(round(time.time() * 1000)))
        sign_string = timestamp + "\n" + self.client_secret
        signature = base64.b64encode(
            hmac.new(
                self.client_secret.encode("utf-8"),
                sign_string.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.OPEN_API_BASE}/sns/getuserinfo_bycode",
                json={"tmp_auth_code": code},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                params={
                    "accessKey": self.client_id,
                    "timestamp": timestamp,
                    "signature": signature,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") == 0:
                user_info = data.get("user_info", {})
                return {
                    "status": "ok",
                    "user_id": user_info.get("userid", ""),
                    "name": user_info.get("name", ""),
                    "nick": user_info.get("nick", ""),
                    "openid": user_info.get("openid", ""),
                }
            return {"status": "error", "msg": data.get("errmsg", "Login failed")}
        except Exception as e:
            logger.error("[DingTalk] OAuth callback error: %s", e)
            return {"status": "error", "msg": str(e)}

    # ------------------------------------------------------------------
    # Access-token management (for REST API calls)
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        """Obtain or refresh a DingTalk Open API ``access_token``."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.OPEN_API_BASE}/v1.0/oauth2/accessToken",
                json={"appKey": self.client_id, "appSecret": self.client_secret},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data.get("accessToken", "")
            expires_in = data.get("expireIn", 7200)
            self._token_expires_at = time.time() + expires_in - 120
            return self._access_token
        except Exception as e:
            logger.error("[DingTalk] Failed to refresh access token: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Stream client (message receiving)
    # ------------------------------------------------------------------

    async def start(self):
        """Start the dingtalk-stream client for receiving messages.

        Runs ``DingTalkStreamClient.start_forever()`` in a daemon thread
        (the SDK's main loop is synchronous).
        """
        if not self.is_authenticated:
            logger.warning("[DingTalk] Not configured — skipping start")
            return

        import dingtalk_stream
        from dingtalk_stream import AckMessage, ChatbotHandler, ChatbotMessage

        class _Handler(ChatbotHandler):
            def __init__(self, adapter: "DingTalkAdapter"):
                super().__init__()
                self._adapter = adapter

            async def process(self, callback_message):
                incoming = ChatbotMessage.from_dict(callback_message.headers)

                msg_type = incoming.message_type or "text"
                content = ""
                if msg_type == "text":
                    texts = incoming.get_text_list()
                    content = texts[0] if texts else ""
                elif msg_type == "richText":
                    content = str(incoming.text or "")

                sender_id = incoming.sender_id or ""
                sender_nick = incoming.sender_nick or ""

                if not content or not sender_id:
                    return AckMessage.STATUS_SUCCESS, "ok"

                logger.info(
                    "[DingTalk] Message from %s (%s): %.60s",
                    sender_nick, sender_id, content,
                )

                webhook = incoming.session_webhook or ""
                if webhook:
                    self._adapter._webhooks[sender_id] = webhook

                msg = Message(
                    source="dingtalk",
                    user_id=sender_id,
                    content=content,
                    metadata={
                        "sender_nick": sender_nick,
                        "conversation_type": incoming.conversation_type,
                        "conversation_id": incoming.conversation_id,
                        "message_id": incoming.message_id,
                        "session_webhook": webhook,
                    },
                )

                try:
                    async for chunk in self._adapter.gateway.incoming_message(msg):
                        if chunk["type"] == "done":
                            reply = chunk.get("content", "")
                            if reply:
                                await self._adapter.send_message(
                                    sender_id, reply, webhook=webhook,
                                )
                except Exception as exc:
                    logger.error("[DingTalk] Process error: %s", exc)

                return AckMessage.STATUS_SUCCESS, "ok"

        try:
            credential = dingtalk_stream.Credential(
                self.client_id, self.client_secret,
            )
            self._client = dingtalk_stream.DingTalkStreamClient(credential)
            self._client.register_callback_handler(
                ChatbotMessage.TOPIC, _Handler(self),
            )
            self._client.register_callback_handler(
                ChatbotMessage.DELEGATE_TOPIC, _Handler(self),
            )

            self._running = True
            self._connected = True
            self._thread = threading.Thread(
                target=self._client.start_forever,
                daemon=True,
                name="dingtalk-stream",
            )
            self._thread.start()
            logger.info("[DingTalk] Stream client started")
        except Exception as e:
            logger.error("[DingTalk] Failed to start stream: %s", e)

    def stop(self):
        """Stop the DingTalk stream client."""
        self._running = False
        self._connected = False
        if self._client is not None:
            stop = getattr(self._client, "stop", None)
            if stop is not None:
                try:
                    stop()
                except Exception as e:
                    logger.debug("[DingTalk] Stop error: %s", e)
        logger.info("[DingTalk] Stream client stopped")

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(
        self,
        user_id: str,
        content: str,
        session_id: str = "",
        webhook: str = "",
    ) -> dict:
        """Send a DingTalk message to *user_id*.

        Tries the cached ``session_webhook`` first (contextual reply), then
        falls back to the REST ``robot/oToMessages/batchSend`` endpoint.

        Returns:
            API response dict, or ``{"status": "error", "msg": ...}`` on failure.
        """
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not configured"}

        hook = webhook or self._webhooks.get(user_id, "")
        if hook:
            try:
                resp = await asyncio.to_thread(
                    requests.post,
                    hook,
                    json={
                        "msgKey": "sampleText",
                        "msgParam": json.dumps({"content": content}),
                    },
                    timeout=10,
                )
                if resp.ok:
                    return {"status": "ok"}
            except Exception as e:
                logger.debug("[DingTalk] Webhook failed: %s", e)

        token = await self._get_access_token()
        if not token:
            return {"status": "error", "msg": "No access token"}

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.OPEN_API_BASE}/v1.0/robot/oToMessages/batchSend",
                headers={
                    "x-acs-dingtalk-access-token": token,
                    "Content-Type": "application/json",
                },
                json={
                    "robotCode": self.client_id,
                    "userIds": [user_id],
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": content}),
                },
                timeout=10,
            )
            resp.raise_for_status()
            return {"status": "ok"}
        except Exception as e:
            logger.error("[DingTalk] Send failed: %s", e)
            return {"status": "error", "msg": str(e)}

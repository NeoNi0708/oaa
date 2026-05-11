"""WeChat iLink ClawBot adapter — real-time message send/receive via HTTP API.

Provides QR-code based login, long-poll message retrieval, and message sending
through the iLink ClawBot HTTP API (https://api.ilinkapi.com).

All HTTP calls are offloaded to a thread pool via ``asyncio.to_thread`` so the
event loop remains responsive during long-poll and send operations.
"""
import asyncio
import json
from typing import Any

import requests

from ..gateway import Message


class WeChatILinkAdapter:
    """iLink ClawBot adapter — QR code login, message polling, sending.

    API Base: https://api.ilinkapi.com (or custom, returned after scan)
    """

    API_BASE = "https://api.ilinkapi.com"

    def __init__(self, token: str = "", bot_id: str = ""):
        self.token = token
        self.bot_id = bot_id
        self.base_url = ""
        self.gateway = None
        self._running = False

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        """Return True when both a session token and API base URL are set."""
        return bool(self.token and self.base_url)

    # ------------------------------------------------------------------
    # QR-code login flow
    # ------------------------------------------------------------------

    def get_qrcode(self) -> dict:
        """Step 1: Get QR code for WeChat scan login (synchronous).

        Returns:
            dict with keys ``qrcode_url`` and ``qrcode_id`` on success,
            or ``error`` on failure.
        """
        try:
            resp = requests.get(
                f"{self.API_BASE}/ilink/bot/get_bot_qrcode",
                params={"bot_type": 3},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return {"error": f"HTTP request failed: {e}"}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response: {e}"}

        if data.get("status") == "ok" or data.get("code") == 0:
            d = data.get("data", {})
            return {
                "qrcode_url": d.get("qrcode_url", ""),
                "qrcode_id": d.get("qrcode_id", ""),
            }
        return {"error": data.get("msg", "Failed to get QR code")}

    def poll_qrcode_status(self, qrcode_id: str, timeout: int = 35) -> dict:
        """Step 2: Poll QR code scan status (synchronous, long poll).

        On successful scan the adapter stores the returned *bot_token*,
        *base_url* and *bot_id* internally so that :meth:`is_authenticated`
        becomes ``True``.

        Returns:
            dict with ``status`` ``"scanned"`` on success, or
            ``"waiting"`` / ``"error"`` on failure.
        """
        try:
            resp = requests.get(
                f"{self.API_BASE}/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode_id},
                timeout=timeout + 5,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return {"status": "error", "msg": f"HTTP request failed: {e}"}
        except json.JSONDecodeError as e:
            return {"status": "error", "msg": f"Invalid JSON response: {e}"}

        if data.get("status") == "ok" or data.get("code") == 0:
            d = data.get("data", {})
            self.token = d.get("bot_token", self.token)
            self.base_url = d.get("baseurl", "")
            self.bot_id = d.get("bot_id", self.bot_id)
            return {
                "status": "scanned",
                "bot_token": self.token,
                "base_url": self.base_url,
            }
        return {"status": "waiting"}

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    async def send_message(
        self, to_wxid: str, content: str, session_id: str = ""
    ) -> dict:
        """Send a WeChat message via the iLink API.

        Args:
            to_wxid: Recipient WeChat ID.
            content: Message text to send.
            session_id: Optional session ID (unused by this adapter).

        Returns:
            API response dict, or ``{"status": "error", "msg": ...}`` on
            failure.
        """
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not authenticated"}

        payload: dict[str, Any] = {
            "to_wxid": to_wxid,
            "content": content,
        }

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.base_url}/ilink/bot/sendmessage",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            return {"status": "error", "msg": f"Send message failed: {e}"}
        except json.JSONDecodeError as e:
            return {"status": "error", "msg": f"Invalid JSON response: {e}"}

    async def send_typing(self, to_wxid: str) -> dict:
        """Send a 'typing' indicator to the given user."""
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not authenticated"}

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.base_url}/ilink/bot/sendtyping",
                json={"to_wxid": to_wxid},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            return {"status": "error", "msg": f"Send typing failed: {e}"}
        except json.JSONDecodeError as e:
            return {"status": "error", "msg": f"Invalid JSON response: {e}"}

    # ------------------------------------------------------------------
    # Long-poll message retrieval
    # ------------------------------------------------------------------

    async def get_updates(self, timeout: int = 35) -> list[dict]:
        """Long-poll for new incoming messages (async).

        Blocks for up to *timeout* seconds waiting for messages.  Returns
        an empty list on timeout or error.

        Returns:
            List of message dicts, each containing at minimum ``content``,
            ``from_wxid`` and ``context_token`` keys.
        """
        if not self.is_authenticated:
            return []

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self.base_url}/ilink/bot/getupdates",
                json={"timeout": timeout},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timeout + 10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"[WeChat] get_updates error: {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"[WeChat] get_updates invalid JSON: {e}")
            return []

        if data.get("status") == "ok" or data.get("code") == 0:
            return data.get("data", {}).get("messages", [])
        return []

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def start_polling(self):
        """Start the long-polling loop for incoming WeChat messages.

        Each received message is wrapped in a :class:`Message` and
        forwarded to :meth:`gateway.incoming_message`.  When the gateway
        yields a ``done`` chunk the reply content is sent back to the
        original user via :meth:`send_message`.

        This method runs until :meth:`stop_polling` is called.
        """
        self._running = True
        while self._running:
            messages = await self.get_updates()
            for msg_data in messages:
                content = msg_data.get("content", "")
                from_wxid = msg_data.get("from_wxid", "")
                if not content or not from_wxid:
                    continue

                msg = Message(
                    source="wechat",
                    user_id=from_wxid,
                    content=content,
                    metadata={
                        "msg_data": msg_data,
                        "context_token": msg_data.get("context_token", ""),
                    },
                )

                try:
                    async for chunk in self.gateway.incoming_message(msg):
                        if chunk["type"] == "done":
                            reply = chunk.get("content", "")
                            if reply:
                                await self.send_message(
                                    from_wxid,
                                    reply,
                                    msg_data.get("context_token", ""),
                                )
                except Exception as e:
                    print(f"[WeChat] Error processing message from {from_wxid}: {e}")

            await asyncio.sleep(1)

    def stop_polling(self):
        """Signal the polling loop to exit at the next iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # start/stop — consistent interface with other adapters
    # ------------------------------------------------------------------

    async def start(self):
        """Start receiving messages (alias for :meth:`start_polling`)."""
        await self.start_polling()

    async def stop(self):
        """Stop receiving messages (alias for :meth:`stop_polling`)."""
        self.stop_polling()

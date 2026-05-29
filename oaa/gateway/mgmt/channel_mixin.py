"""Channel mixin — QR code login/poll and channel reconnect."""
import asyncio
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class ChannelMixin:
    """Channel authentication: QR login, polling, reconnect."""

    async def _handle_qr_login(self, payload: dict) -> dict:
        """Initiate QR code login for a channel (wechat/dingtalk/feishu)."""
        channel = payload.get("channel", "")
        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "get_qrcode"):
            return {"ok": False, "error": f"Channel {channel} does not support QR login"}

        if channel == "dingtalk":
            cid = payload.get("client_id", "")
            sec = payload.get("client_secret", "")
            if cid:
                adapter.client_id = cid
            if sec:
                adapter.client_secret = sec
        elif channel == "feishu":
            aid = payload.get("app_id")
            asec = payload.get("app_secret")
            if aid is not None:
                adapter.app_id = aid
            if asec is not None:
                adapter.app_secret = asec
            logger.info("[FeishuQR] app_id=%s app_secret=%s", "SET" if aid else "EMPTY", "SET" if asec else "EMPTY")

        result = adapter.get_qrcode()
        if asyncio.iscoroutine(result):
            result = await result
        if "error" in result:
            return {"ok": False, "error": result["error"]}

        return {
            "ok": True,
            "qr_code_url": result.get("qrcode_url", ""),
            "qr_code_id": result.get("qrcode_id", ""),
            "user_code": result.get("user_code", ""),
            "channel": channel,
        }

    async def _handle_poll_qr(self, payload: dict) -> dict:
        """Poll QR code scan status."""
        channel = payload.get("channel", "")
        qrcode_id = payload.get("qrcode_id", "")

        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "poll_qrcode_status"):
            return {"ok": False, "error": f"Channel {channel} does not support QR polling"}

        result = adapter.poll_qrcode_status(qrcode_id)
        if asyncio.iscoroutine(result):
            result = await result
        logger.info("poll_qr channel=%s qrcode_id=%s result=%s", channel, qrcode_id[:16], result)
        if result.get("status") == "confirmed":
            if channel == "wechat":
                token = result.get("bot_token", "")
                if token:
                    self._config.wechat.iLink_token = token
                    self._config.wechat.iLink_bot_id = result.get("ilink_bot_id", self._config.wechat.iLink_bot_id)
                    self._config.wechat.ilink_user_id = result.get("ilink_user_id", self._config.wechat.ilink_user_id)
                    self._config.wechat.base_url = result.get("base_url", self._config.wechat.base_url)
                    self._config.wechat.enabled = True
                    await self._config.save()
                    # Update adapter instance so it can actually send/receive
                    adapter.token = token
                    adapter.bot_id = self._config.wechat.iLink_bot_id
                    adapter.base_url = self._config.wechat.base_url
                    adapter._bot._base_url = self._config.wechat.base_url
                    # Reset upload health flag — new session may restore permission
                    adapter._upload_available = True
                    # Restart polling with new credentials
                    if hasattr(adapter, "stop_polling"):
                        adapter.stop_polling()
                    if hasattr(adapter, "start_polling"):
                        asyncio.create_task(adapter.start_polling())
            elif channel == "dingtalk":
                self._config.dingtalk.client_id = getattr(adapter, "client_id", "")
                self._config.dingtalk.client_secret = getattr(adapter, "client_secret", "")
                self._config.dingtalk.enabled = True
                await self._config.save()
                # Start the Stream client
                if hasattr(adapter, "start") and callable(adapter.start):
                    result_or_coro = adapter.start()
                    if asyncio.iscoroutine(result_or_coro):
                        asyncio.create_task(result_or_coro)
            elif channel == "feishu":
                self._config.feishu.app_id = getattr(adapter, "app_id", "")
                self._config.feishu.app_secret = getattr(adapter, "app_secret", "")
                self._config.feishu.enabled = True
                await self._config.save()
                # Start the WebSocket event client
                if hasattr(adapter, "start") and callable(adapter.start):
                    result_or_coro = adapter.start()
                    if asyncio.iscoroutine(result_or_coro):
                        asyncio.create_task(result_or_coro)

            # Notify the newly-connected channel with a welcome message
            if self._agent and self._agent._on_channel_ready:
                asyncio.create_task(self._agent._on_channel_ready(channel))

        return {
            "ok": True,
            "status": result.get("status", "waiting"),
            "msg": result.get("msg", ""),
        }

    def _handle_reconnect_channel(self, payload: dict) -> dict:
        """Reconnect a channel using saved credentials (no QR scan)."""
        channel = payload.get("channel", "")
        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if channel == "wechat":
            token = self._config.wechat.iLink_token
            base_url = self._config.wechat.base_url
            if not token or not base_url:
                return {"ok": False, "error": "微信未认证，请先扫码登录"}
            adapter.token = token
            adapter.base_url = base_url
            adapter.bot_id = self._config.wechat.iLink_bot_id
            adapter._bot._base_url = base_url
            # Restart polling with updated credentials
            if hasattr(adapter, "stop_polling"):
                adapter.stop_polling()
            if hasattr(adapter, "start_polling"):
                asyncio.create_task(adapter.start_polling())
            if self._agent and self._agent._on_channel_ready:
                asyncio.create_task(self._agent._on_channel_ready(channel))
            return {"ok": True, "online": True, "msg": "微信已重连"}
        elif channel == "dingtalk":
            cid = self._config.dingtalk.client_id
            sec = self._config.dingtalk.client_secret
            if not cid or not sec:
                return {"ok": False, "error": "钉钉未配置凭证"}
            if hasattr(adapter, "client_id"):
                adapter.client_id = cid
            if hasattr(adapter, "client_secret"):
                adapter.client_secret = sec
            # Start the Stream client
            if hasattr(adapter, "start") and callable(adapter.start):
                result_or_coro = adapter.start()
                if asyncio.iscoroutine(result_or_coro):
                    asyncio.create_task(result_or_coro)
            return {"ok": True, "online": True, "msg": "钉钉已重连"}
        elif channel == "feishu":
            aid = self._config.feishu.app_id
            asec = self._config.feishu.app_secret
            if not aid or not asec:
                return {"ok": False, "error": "飞书未配置凭证"}
            if hasattr(adapter, "app_id"):
                adapter.app_id = aid
            if hasattr(adapter, "app_secret"):
                adapter.app_secret = asec
            # Start the WebSocket event client
            if hasattr(adapter, "start") and callable(adapter.start):
                result_or_coro = adapter.start()
                if asyncio.iscoroutine(result_or_coro):
                    asyncio.create_task(result_or_coro)
            return {"ok": True, "online": True, "msg": "飞书已重连"}
        else:
            return {"ok": False, "error": f"Channel {channel} reconnect not implemented"}

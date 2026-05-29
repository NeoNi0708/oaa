"""Healthcheck mixin — periodic channel health probe and disconnect notification."""
import asyncio
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class HealthcheckMixin:
    """Periodic healthcheck for channel adapters."""

    def start_healthcheck(self):
        """Start the background healthcheck coroutine."""
        if not hasattr(self, '_healthcheck_task') or self._healthcheck_task is None:
            self._disconnect_notified: dict[str, bool] = {}
            self._healthcheck_task = asyncio.create_task(self._healthcheck_loop())

    async def _healthcheck_loop(self):
        """Periodically verify each channel adapter is still connected.

        On disconnect (online->offline transition): push to GUI and send
        one WeChat notification.  On reconnect (offline->online): reset
        the notification flag so the next disconnect fires again.
        """
        while True:
            await asyncio.sleep(30)
            for name, adapter in self._channels.items():
                was_connected = getattr(adapter, 'is_connected', False)

                # Perform a lightweight health probe
                now_connected = await self._probe_adapter(name, adapter)

                # Sync the _connected flag so _handle_get_status stays correct
                if hasattr(adapter, '_connected'):
                    adapter._connected = now_connected

                # Online -> offline: notify once
                if was_connected and not now_connected:
                    if not self._disconnect_notified.get(name):
                        self._disconnect_notified[name] = True
                        await self._notify_disconnect(name)
                # Offline -> online: reset notification flag for next time
                elif not was_connected and now_connected:
                    self._disconnect_notified[name] = False

    @staticmethod
    async def _probe_adapter(name: str, adapter) -> bool:
        """Lightweight check if a channel adapter is responsive.

        Returns ``True`` if the adapter appears to be running, ``False``
        if it has stopped or crashed.
        """
        # Not started or explicitly stopped
        if not getattr(adapter, '_running', False):
            return False

        # Thread-based adapters (DingTalk, Feishu): check thread is alive
        thread = getattr(adapter, '_thread', None)
        if thread is not None and not thread.is_alive():
            logger.warning("[Health] %s thread died", name)
            return False

        return True

    async def _notify_disconnect(self, channel_name: str):
        """Push a disconnect alert to all GUI clients and send one WeChat
        notification (only for non-WeChat channels)."""
        logger.warning("[Health] %s disconnected", channel_name)

        # Push to all GUI clients
        self._push_notification("channel_disconnected", {
            "channel": channel_name,
            "msg": f"{channel_name} 通道已断开连接",
        })

        # Send WeChat notification (only once, so the user isn't spammed)
        if channel_name != "wechat":
            wechat = self._channels.get("wechat")
            if wechat and getattr(wechat, 'is_connected', False) and getattr(wechat, '_bot_user_id', None):
                try:
                    await wechat.send_message(
                        wechat._bot_user_id,
                        f"⚠️ {channel_name} 通道已断开连接",
                    )
                except Exception:
                    pass

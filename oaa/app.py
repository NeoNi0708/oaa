"""OAA main application — ties everything together."""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Prepend bundled Node.js portable to PATH so CLI tools use it automatically
_BUNDLED_NODE = Path(__file__).resolve().parent.parent / "cli" / "node"
if _BUNDLED_NODE.is_dir():
    _node_path = str(_BUNDLED_NODE)
    if _node_path not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _node_path + os.pathsep + os.environ.get("PATH", "")

from .agent.oaa_agent import OAAAgent
from .agent.worker import WorkerAgent
from .auth.permissions import PermissionsManager
from .config import AppConfig
from .evolution.engine import EvolutionEngine
from .gateway.adapters.desktop import DesktopAdapter
from .gateway.adapters.dingtalk import DingTalkAdapter
from .gateway.adapters.feishu import FeishuAdapter
from .gateway.adapters.wechat_ilink import WeChatILinkAdapter
from .gateway.gateway import Gateway
from .gateway.management import ManagementHandler
from .logging_config import get_logger, setup_logging
from .scheduler import TaskScheduler
from .session.manager import SessionManager
from .wizard import SetupWizard

logger = get_logger("app")


class OAAApp:
    def __init__(self, config_path: str = ""):
        config_path = config_path or AppConfig.DEFAULT_CONFIG_PATH

        # First-run wizard if no config file exists
        if not os.path.exists(config_path):
            wizard = SetupWizard()
            self.config = wizard.run_text()
            config_path = AppConfig.DEFAULT_CONFIG_PATH
        else:
            self.config = AppConfig.load(config_path)

        # Configure logging — OAA_LOG_LEVEL=DEBUG for verbose console output
        log_file = os.path.join(self.config.data_dir, "oaa.log")
        log_level = os.environ.get("OAA_LOG_LEVEL", "INFO").upper()
        level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
        setup_logging(level=level_map.get(log_level, logging.INFO), log_file=log_file)
        logger.info("OAA starting: data_dir=%s", self.config.data_dir)

        # Core services
        self.permissions = PermissionsManager(self.config)
        self.session_mgr = SessionManager(os.path.join(self.config.data_dir, "db", "oaa.db"))
        self.evolution = EvolutionEngine(self.config.data_dir, llm=None)
        self.scheduler = TaskScheduler(os.path.join(self.config.data_dir, "tasks"))
        self.agent = OAAAgent(self.config, permissions=self.permissions, evolution=self.evolution,
                              scheduler=self.scheduler)
        # Wire LLM to EvolutionEngine so skill crystallization can use the same model
        self.evolution.set_llm(self.agent.llm)
        self.worker = WorkerAgent(self.config)
        self.gateway = Gateway(self.agent, self.session_mgr)

        # Desktop adapter (always enabled)
        self.desktop = DesktopAdapter()
        self.gateway.register_adapter("desktop", self.desktop)

        # Channel adapters — enabled by config
        self.channel_adapters: dict[str, object] = {}
        self._register_channels()

        # Pass wechat adapter to agent for proactive messaging
        self.agent.extended.set_wechat_adapter(self.channel_adapters.get("wechat"))

        # Share DingTalk adapter's authenticated CLI (from device-auth QR flow)
        self.agent.extended.set_dingtalk_adapter(self.channel_adapters.get("dingtalk"))

        # Share Feishu adapter's configured CLI (same reason — avoid unconfigured clone)
        self.agent.extended.set_feishu_adapter(self.channel_adapters.get("feishu"))

        # Inject channel adapters so the agent can introspect runtime status
        self.agent.set_channel_adapters(self.channel_adapters)

        # Wire worker agent for background task execution
        self.desktop._worker = self.worker

        # Wire management handler for non-chat WebSocket operations
        self.desktop.set_management_handler(
            ManagementHandler(
                config=self.config,
                scheduler=self.scheduler,
                skill_mgr=self.agent.skill_mgr,
                evolution=self.evolution,
                channel_adapters=self.channel_adapters,
                agent=self.agent,
            )
        )

        # Wire user-confirmation callback so that ask_user reaches the GUI
        self.permissions.set_confirm_callback(self.desktop.create_confirm_callback())

        # Start channel healthcheck loop (management handler is set inside desktop)
        if self.desktop._management is not None:
            self.desktop._management.start_healthcheck()

    def _register_channels(self):
        """Register channel adapters. Channels are enabled whenever credentials are present."""
        # WeChat iLink
        wc = self.config.wechat
        wechat = WeChatILinkAdapter(token=wc.iLink_token, bot_id=wc.iLink_bot_id, base_url=wc.base_url,
                                     ilink_user_id=wc.ilink_user_id)
        self.gateway.register_adapter("wechat", wechat)
        self.channel_adapters["wechat"] = wechat
        if wc.iLink_token:
            logger.info("Channel 'wechat' registered (authenticated)")
        else:
            logger.info("Channel 'wechat' registered (not authenticated)")

        # DingTalk
        dt = self.config.dingtalk
        dingtalk = DingTalkAdapter(client_id=dt.client_id, client_secret=dt.client_secret)
        self.gateway.register_adapter("dingtalk", dingtalk)
        self.channel_adapters["dingtalk"] = dingtalk
        if dt.client_id and dt.client_secret:
            logger.info("Channel 'dingtalk' registered (authenticated)")
        else:
            logger.info("Channel 'dingtalk' registered (not authenticated)")

        # Feishu
        fs = self.config.feishu
        feishu = FeishuAdapter(app_id=fs.app_id, app_secret=fs.app_secret)
        self.gateway.register_adapter("feishu", feishu)
        self.channel_adapters["feishu"] = feishu
        if fs.app_id and fs.app_secret:
            logger.info("Channel 'feishu' registered (authenticated)")
        else:
            logger.info("Channel 'feishu' registered (not authenticated)")

    async def start(self):
        logger.info("Starting Desktop WebSocket on :9765")
        await self.desktop.start()

        # Start enabled channel adapters
        for name, adapter in self.channel_adapters.items():
            if hasattr(adapter, 'start'):
                try:
                    # Channel adapters run in their own threads (dingtalk-stream,
                    # feishu-ws) or as long-running tasks; wrap in create_task to
                    # avoid blocking the startup sequence.
                    asyncio.create_task(adapter.start())
                    logger.info("Channel '%s' started", name)
                except Exception as exc:
                    logger.warning("Failed to start channel '%s': %s", name, exc)

        logger.info("Starting worker agent...")
        await self.worker.start()
        logger.info("Starting task scheduler...")

        # Executor callback — auto-runs scheduled tasks with execution_prompt
        async def _executor_run(task: dict):
            prompt = task.get("execution_prompt", "")
            name = task.get("name", "定时任务")
            delivery = task.get("delivery_channels", ["chat", "wechat"])
            logger.info("Executor running task: %s → channels: %s", name, delivery)
            try:
                full_prompt = (
                    f"【定时任务执行】{name}\n\n"
                    f"{prompt}\n\n"
                    f"执行完成后，将结果通过以下渠道交付：{', '.join(delivery)}。"
                    f"使用 wechat_send_text 发送微信、直接输出到对话页面。"
                )
                result_text = ""
                async for chunk in self.agent.process_message(full_prompt, history=[]):
                    if chunk["type"] == "done":
                        result_text = chunk.get("content", "")
                if "wechat" in delivery:
                    wechat = self.channel_adapters.get("wechat")
                    if wechat and wechat.is_authenticated and wechat._bot_user_id:
                        summary = (result_text[:500] + "...") if len(result_text) > 500 else result_text
                        await wechat.send_message(wechat._bot_user_id,
                            f"📋 [{name}] 执行完成：\n\n{summary or '(无文本输出)'}")
                logger.info("Scheduled task '%s' executed successfully", name)
            except Exception as exc:
                logger.error("Scheduled task '%s' execution failed: %s", name, exc)

        self.scheduler.set_due_callback(_executor_run)
        asyncio.create_task(self.scheduler.start_loop())

        # Start IdleInspector background task
        async def _inspector_notify(proposal: str):
            await self.desktop.notify_all(proposal)
            wechat = self.channel_adapters.get("wechat")
            if wechat and wechat.is_authenticated and wechat._bot_user_id:
                try:
                    import re as _re
                    simple = _re.sub(r"[🔍🔬🔧💡📊📝⏳]", "", proposal)
                    simple = simple.replace("**", "").replace("``", "")
                    if len(simple) > 600:
                        simple = simple[:600] + "\n\n（消息过长已截断，请在聊天页面查看完整内容）"
                    await wechat.send_message(wechat._bot_user_id,
                        f"💡 空闲巡检发现优化项：\n\n{simple}")
                except Exception as exc:
                    logger.debug("WeChat inspector notify failed: %s", exc)
        self.agent._idle_inspector.set_notify_callback(_inspector_notify)

        self.agent._idle_inspector.set_executor_callback(_executor_run)
        await self.agent._idle_inspector.start_background()

        # Startup self-check: background task that waits for GUI client
        self._startup_notified: set[str] = set()
        self._notify_lock = asyncio.Lock()
        asyncio.create_task(self._startup_check())

        # Register hook: when a Desktop GUI client connects, have agent report in
        self.desktop._on_first_client = self._notify_desktop

        # Wire a callback so management.py can send welcome when a new channel authenticates
        self.agent._on_channel_ready = self._notify_channel

        # Fix stale running proposals from previous session (app crash / kill)
        if self.agent._proposal_store:
            await self.agent._proposal_store.fix_stale_running()
            await self.agent._proposal_store.dedup_stale_pending()

        logger.info("OAA ready. Waiting for messages...")
        while True:
            await asyncio.sleep(3600)

    async def _notify_desktop(self):
        """Have the agent proactively check status and report to the Desktop GUI."""
        async with self._notify_lock:
            if "desktop" in self._startup_notified:
                return
            self._startup_notified.add("desktop")
        if not self.desktop._clients:
            return

        try:
            startup_prompt = (
                "【系统通知】服务已全部启动，所有通道就绪。"
                "请检查当前各通道的状态，然后主动向用户打招呼，"
                "用你平常的语气介绍你的身份、当前各通道的连接状态，"
                "告诉用户可以通过哪些方式给你布置工作。"
                "要热情自然，就像刚睡醒伸了个懒腰那样。"
            )
            async for chunk in self.agent.process_message(startup_prompt, history=[]):
                if chunk["type"] == "llm_output":
                    await self.desktop.notify_all(chunk["content"])
            # Finalize with a done message to push to chat history
            await self.desktop.notify_all("", msg_type="done")
            logger.info("Agent startup report sent to Desktop GUI")
        except Exception as exc:
            logger.warning("Agent startup report failed: %s", exc)

    async def _notify_channel(self, channel: str):
        """Have the agent generate a welcome message for a just-connected channel."""
        async with self._notify_lock:
            if channel in self._startup_notified:
                return
            self._startup_notified.add(channel)

        adapter = self.channel_adapters.get(channel)
        if not adapter:
            return
        wechat_ok = (
            channel == "wechat"
            and getattr(adapter, 'is_authenticated', False)
            and getattr(adapter, '_bot_user_id', None)
        )
        if not wechat_ok:
            return

        try:
            startup_prompt = (
                "【系统通知】微信通道刚刚连接成功。"
                "请检查当前各通道的状态，用你平常的语气主动向用户打招呼，"
                "告诉他你已准备好，可以通过微信给他提供服务。"
            )
            response_parts = []
            async for chunk in self.agent.process_message(startup_prompt, history=[]):
                if chunk["type"] == "llm_output":
                    response_parts.append(chunk["content"])

            full = "".join(response_parts).strip()
            if full:
                import re as _re
                simple = _re.sub(r"[🔍🔬🔧💡📊📝⏳✅❌🔵🟢]", "", full)
                simple = simple.replace("**", "").replace("``", "")
                if len(simple) > 600:
                    simple = simple[:600] + "\n\n（消息过长已截断，请在聊天页面查看完整内容）"
                result = await adapter.send_message(adapter._bot_user_id, simple)
                if isinstance(result, dict) and result.get("status") == "error":
                    logger.warning("WeChat send_message failed: %s", result.get("msg"))
                else:
                    logger.info("Agent welcome sent to WeChat (%s)", adapter._bot_user_id)
        except Exception as exc:
            logger.warning("Agent welcome for channel %s failed: %s", channel, exc)

    async def _startup_check(self):
        """Wait for Desktop GUI client, then trigger agent-driven startup reports."""
        try:
            if not self.desktop._clients:
                for _ in range(15):
                    await asyncio.sleep(2)
                    if self.desktop._clients:
                        break

            # Notify WeChat if already authenticated at startup
            wechat = self.channel_adapters.get("wechat")
            if wechat and getattr(wechat, 'is_authenticated', False) and getattr(wechat, '_bot_user_id', None):
                await self._notify_channel("wechat")

        except Exception as exc:
            logger.warning("Startup check failed: %s", exc)

    async def stop(self):
        logger.info("Shutting down...")
        await self.agent._idle_inspector.stop_background()
        await self.worker.stop()
        self.scheduler.stop_loop()
        # Stop all registered adapters
        for name, adapter in self.gateway._adapters.items():
            if hasattr(adapter, 'stop'):
                try:
                    await adapter.stop()
                    logger.debug("Adapter '%s' stopped", name)
                except Exception as exc:
                    logger.warning("Error stopping adapter '%s': %s", name, exc)
        # Close DB connections
        if hasattr(self, 'session_mgr'):
            self.session_mgr.close()
        logger.info("OAA stopped.")

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if sys.platform == "win32":
                # Set Windows Ctrl+C handler
                try:
                    import signal
                    signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(self._signal_stop))
                    signal.signal(signal.SIGTERM, lambda s, f: loop.call_soon_threadsafe(self._signal_stop))
                except Exception:
                    pass
            loop.run_until_complete(self.start())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            if not loop.is_closed():
                loop.run_until_complete(self.stop())
                loop.close()

    def _signal_stop(self):
        """Graceful shutdown on signal — stop services first, then cancel remainder."""
        logger.info("Received stop signal, shutting down...")
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self.stop())
        except Exception as exc:
            logger.warning("Graceful stop failed, forcing cancel: %s", exc)
            for task in asyncio.all_tasks():
                task.cancel()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OPC AI Assistant")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()
    if args.version:
        from . import __version__
        print(f"OAA v{__version__}")
        return
    app = OAAApp(args.config)
    try:
        app.run()
    except KeyboardInterrupt:
        print("\n[OAA] Bye!")

if __name__ == "__main__":
    main()

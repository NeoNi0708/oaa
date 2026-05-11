"""OAA main application — ties everything together."""
import asyncio
import os
import sys

from .agent.oaa_agent import OAAAgent
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

        # Configure logging
        log_file = os.path.join(self.config.data_dir, "oaa.log")
        setup_logging(log_file=log_file)
        logger.info("OAA starting: data_dir=%s", self.config.data_dir)

        # Core services
        self.permissions = PermissionsManager(self.config)
        self.session_mgr = SessionManager(os.path.join(self.config.data_dir, "db", "oaa.db"))
        self.evolution = EvolutionEngine(self.config.data_dir, llm=None)
        self.agent = OAAAgent(self.config, permissions=self.permissions, evolution=self.evolution)
        self.gateway = Gateway(self.agent, self.session_mgr)

        # Desktop adapter (always enabled)
        self.desktop = DesktopAdapter()
        self.gateway.register_adapter("desktop", self.desktop)

        # Channel adapters — enabled by config
        self.channel_adapters: dict[str, object] = {}
        self._register_channels()

        self.scheduler = TaskScheduler(os.path.join(self.config.data_dir, "tasks"))

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

    def _register_channels(self):
        """Register channel adapters based on configuration."""
        # WeChat iLink
        wc = self.config.wechat
        wechat = WeChatILinkAdapter(token=wc.iLink_token, bot_id=wc.iLink_bot_id)
        self.gateway.register_adapter("wechat", wechat)
        self.channel_adapters["wechat"] = wechat
        if wc.enabled:
            logger.info("Channel 'wechat' registered (enabled)")
        else:
            logger.info("Channel 'wechat' registered (disabled — enable via Settings)")

        # DingTalk
        dt = self.config.dingtalk
        dingtalk = DingTalkAdapter(client_id=dt.client_id, client_secret=dt.client_secret)
        self.gateway.register_adapter("dingtalk", dingtalk)
        self.channel_adapters["dingtalk"] = dingtalk
        if dt.enabled and dt.client_id:
            logger.info("Channel 'dingtalk' registered (enabled)")
        else:
            logger.info("Channel 'dingtalk' registered (disabled — enable via Settings)")

        # Feishu
        fs = self.config.feishu
        feishu = FeishuAdapter(app_id=fs.app_id, app_secret=fs.app_secret)
        self.gateway.register_adapter("feishu", feishu)
        self.channel_adapters["feishu"] = feishu
        if fs.enabled and fs.app_id:
            logger.info("Channel 'feishu' registered (enabled)")
        else:
            logger.info("Channel 'feishu' registered (disabled — enable via Settings)")

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

        logger.info("Starting task scheduler...")
        asyncio.create_task(self.scheduler.start_loop())
        logger.info("OAA ready. Waiting for messages...")
        while True:
            await asyncio.sleep(3600)

    async def stop(self):
        logger.info("Shutting down...")
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
        """Cancel the main asyncio task on signal."""
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

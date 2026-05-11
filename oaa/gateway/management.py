"""Management handler — non-chat operations for the Desktop WebSocket adapter.

Handles config, tasks, skills, evolution, and channel management requests.
Each handler receives a payload dict and returns a response dict.
"""
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.oaa_agent import OAAAgent
    from ..agent.skill_manager import SkillManager
    from ..config import AppConfig
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler

from ..logging_config import get_logger

logger = get_logger("gateway.management")

# Known management message types
VALID_TYPES = {
    "get_config", "save_config",
    "get_status",
    "get_tasks", "save_task", "delete_task", "toggle_task",
    "get_skills", "get_evolution",
    "qr_login", "poll_qr",
    "switch_model",
    "get_models",
}


class ManagementHandler:
    """Dispatches management requests to the appropriate backend service."""

    def __init__(
        self,
        config: "AppConfig",
        scheduler: "TaskScheduler",
        skill_mgr: "SkillManager",
        evolution: "EvolutionEngine",
        channel_adapters: dict,
        agent: "OAAAgent | None" = None,   # for hot-reloading LLM config
    ):
        self._config = config
        self._scheduler = scheduler
        self._skill_mgr = skill_mgr
        self._evolution = evolution
        self._channels = channel_adapters
        self._agent = agent

        # Agent runtime state (updated by DesktopAdapter during chat)
        self._agent_state = "idle"        # idle | thinking | executing | responding
        self._agent_state_since = time.time()
        self._start_time = time.time()    # process start for uptime
        self._chat_count = 0
        self._tool_call_count = 0

    def set_agent_state(self, state: str):
        """Update agent cognitive state. Called from DesktopAdapter."""
        self._agent_state = state
        self._agent_state_since = time.time()
        if state == "thinking":
            self._chat_count += 1

    def handle(self, msg_type: str, payload: dict) -> dict:
        """Dispatch a management request. Returns a response dict."""
        if msg_type not in VALID_TYPES:
            return {"ok": False, "error": f"Unknown management type: {msg_type}"}

        handler_name = f"_handle_{msg_type}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            return {"ok": False, "error": f"No handler for: {msg_type}"}

        try:
            return handler(payload)
        except Exception as exc:
            logger.exception("Management handler %s failed: %s", msg_type, exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Agent status
    # ------------------------------------------------------------------

    def _handle_get_status(self, _payload: dict) -> dict:
        """Return current agent runtime status."""
        channels = {}
        for name, adapter in self._channels.items():
            channels[name] = {
                "online": getattr(adapter, "is_authenticated", False) or getattr(adapter, "_running", False),
            }

        # Count active sessions from the _config reference (stale reference, but close enough)
        uptime_sec = int(time.time() - self._start_time)

        return {
            "ok": True,
            "agent_state": self._agent_state,
            "agent_state_since": self._agent_state_since,
            "channels": channels,
            "chat_count": self._chat_count,
            "uptime_sec": uptime_sec,
            "timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _handle_get_config(self, _payload: dict) -> dict:
        """Return the full app configuration as a JSON-serializable dict."""
        from dataclasses import asdict
        return {"ok": True, "config": asdict(self._config)}

    def _handle_save_config(self, payload: dict) -> dict:
        """Merge *payload.config* into current config and persist to disk."""
        data = payload.get("config", {})
        if not data:
            return {"ok": False, "error": "No config data provided"}

        # Merge top-level keys
        if "model" in data:
            m = data["model"]
            self._config.model.provider = m.get("provider", self._config.model.provider)
            self._config.model.plan = m.get("plan", self._config.model.plan)
            self._config.model.api_format = m.get("api_format", self._config.model.api_format)
            self._config.model.base_url = m.get("base_url", self._config.model.base_url)
            self._config.model.api_key = m.get("api_key", self._config.model.api_key)
            self._config.model.model_id = m.get("model_id", self._config.model.model_id)
            self._config.model.max_tokens = m.get("max_tokens", self._config.model.max_tokens)
            self._config.model.temperature = m.get("temperature", self._config.model.temperature)

        # Per-provider credentials (models dict)
        if "models" in data:
            self._config.models = data["models"]
            # Sync active provider's creds from models dict
            prov = self._config.model.provider
            if prov in self._config.models:
                saved = self._config.models[prov]
                if saved.get("api_key"):
                    self._config.model.api_key = saved["api_key"]
                if saved.get("model_id"):
                    self._config.model.model_id = saved["model_id"]
                if saved.get("base_url"):
                    self._config.model.base_url = saved["base_url"]

        if "wechat" in data:
            w = data["wechat"]
            self._config.wechat.enabled = w.get("enabled", self._config.wechat.enabled)
            self._config.wechat.iLink_token = w.get("iLink_token", self._config.wechat.iLink_token)
            self._config.wechat.iLink_bot_id = w.get("iLink_bot_id", self._config.wechat.iLink_bot_id)

        if "dingtalk" in data:
            d = data["dingtalk"]
            self._config.dingtalk.enabled = d.get("enabled", self._config.dingtalk.enabled)
            self._config.dingtalk.client_id = d.get("client_id", self._config.dingtalk.client_id)
            self._config.dingtalk.client_secret = d.get("client_secret", self._config.dingtalk.client_secret)

        if "feishu" in data:
            f = data["feishu"]
            self._config.feishu.enabled = f.get("enabled", self._config.feishu.enabled)
            self._config.feishu.app_id = f.get("app_id", self._config.feishu.app_id)
            self._config.feishu.app_secret = f.get("app_secret", self._config.feishu.app_secret)

        if "data_dir" in data:
            self._config.data_dir = data["data_dir"]

        if "permissions" in data:
            self._config.permissions = data["permissions"]

        self._config.save()

        # Hot-reload LLM client so the new API key / base URL / model take effect immediately
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)

        return {"ok": True}

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def _handle_get_models(self, _payload: dict) -> dict:
        """Return all configured models and their credentials."""
        from ..config import ModelConfig
        from dataclasses import asdict
        default_urls = {
            "deepseek": "https://api.deepseek.com",
            "volcengine": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            "tongyi": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1/chat/completions",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "moonshot": "https://api.moonshot.cn/v1/chat/completions",
            "baichuan": "https://api.baichuan-ai.com/v1",
            "stepfun": "https://api.stepfun.com/v1/chat/completions",
            "minimax": "https://api.minimaxi.com/v1",
            "lingyi": "https://api.lingyiwanwu.com/v1",
            "xunfei": "https://maas-api.cn-huabei-1.xf-yun.com/v2",
            "xiaomi": "https://api.xiaomimimo.com/v1/chat/completions",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "custom-openai": "",
            "custom-anthropic": "",
        }
        models = {}
        for prov, default_url in default_urls.items():
            saved = self._config.models.get(prov, {})
            models[prov] = {
                "api_key": saved.get("api_key", ""),
                "model_id": saved.get("model_id", ""),
                "base_url": saved.get("base_url", default_url),
            }
        return {
            "ok": True,
            "active": self._config.model.provider,
            "models": models,
        }

    def _handle_switch_model(self, payload: dict) -> dict:
        """Switch the active model provider without going through settings."""
        provider = payload.get("provider", "")
        if not provider:
            return {"ok": False, "error": "No provider specified"}
        saved = self._config.models.get(provider, {})
        self._config.model.provider = provider
        if "api_key" in saved:
            self._config.model.api_key = saved["api_key"]
        if "model_id" in saved:
            self._config.model.model_id = saved["model_id"]
        if "base_url" in saved and saved["base_url"]:
            self._config.model.base_url = saved["base_url"]
        # Ensure api_format matches provider
        if provider in ("anthropic", "custom-anthropic"):
            self._config.model.api_format = "anthropic"
        else:
            self._config.model.api_format = "openai"
        self._config.save()
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def _handle_get_tasks(self, _payload: dict) -> dict:
        """Return all tasks from the scheduler."""
        tasks = self._scheduler.list_tasks(include_disabled=True)
        return {"ok": True, "tasks": tasks}

    def _handle_save_task(self, payload: dict) -> dict:
        """Create or update a task."""
        task_data = payload.get("task", {})
        if not task_data:
            return {"ok": False, "error": "No task data provided"}

        task_id = task_data.get("id", "")
        if task_id and self._scheduler.get(task_id):
            # Update existing
            updated = self._scheduler.update(task_id, task_data)
            return {"ok": True, "task": updated}
        else:
            # Create new
            created = self._scheduler.create(task_data)
            return {"ok": True, "task": created}

    def _handle_delete_task(self, payload: dict) -> dict:
        """Delete a task by id."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        deleted = self._scheduler.delete(task_id)
        return {"ok": deleted, "id": task_id}

    def _handle_toggle_task(self, payload: dict) -> dict:
        """Toggle task enabled/disabled."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        task = self._scheduler.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task not found: {task_id}"}
        updated = self._scheduler.update(task_id, {"enabled": not task.get("enabled", True)})
        return {"ok": True, "task": updated}

    # ------------------------------------------------------------------
    # Skills & Evolution
    # ------------------------------------------------------------------

    def _handle_get_skills(self, _payload: dict) -> dict:
        """Return all active skills from SkillManager."""
        skills = []
        for info in self._skill_mgr.list_all():
            skills.append({
                "name": info.name,
                "category": info.category,
                "path": info.path,
                "loaded": bool(info.skill_md),
                "tools_count": len(info.tools) if info.tools else 0,
                "knowledge_count": len(info.knowledge) if info.knowledge else 0,
            })
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "skills": skills,
            "current": current.name if current else None,
            "total": len(skills),
        }

    def _handle_get_evolution(self, _payload: dict) -> dict:
        """Return evolution statistics and suggestions from EvolutionEngine."""
        suggestions = self._evolution.stats.get("suggestions", [])

        return {
            "ok": True,
            "stats": {
                "skill_usage": self._evolution.stats.get("skill_usage", {}),
                "sop_executions": self._evolution.stats.get("sop_executions", {}),
                "crystallized": self._evolution.stats.get("crystallized", []),
            },
            "suggestions": suggestions,
        }

    # ------------------------------------------------------------------
    # QR / Channel login
    # ------------------------------------------------------------------

    def _handle_qr_login(self, payload: dict) -> dict:
        """Initiate QR code login for a channel (wechat/dingtalk/feishu)."""
        channel = payload.get("channel", "")
        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "get_qrcode"):
            return {"ok": False, "error": f"Channel {channel} does not support QR login"}

        result = adapter.get_qrcode()
        if "error" in result:
            return {"ok": False, "error": result["error"]}

        return {
            "ok": True,
            "qr_code_url": result.get("qrcode_url", ""),
            "qr_code_id": result.get("qrcode_id", ""),
            "channel": channel,
        }

    def _handle_poll_qr(self, payload: dict) -> dict:
        """Poll QR code scan status."""
        channel = payload.get("channel", "")
        qrcode_id = payload.get("qrcode_id", "")

        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "poll_qrcode_status"):
            return {"ok": False, "error": f"Channel {channel} does not support QR polling"}

        result = adapter.poll_qrcode_status(qrcode_id)
        if result.get("status") == "scanned":
            # Save token/bot_id to config after successful scan
            if channel == "wechat":
                token = result.get("bot_token", "")
                if token:
                    self._config.wechat.iLink_token = token
                    self._config.wechat.iLink_bot_id = result.get("bot_id", self._config.wechat.iLink_bot_id)
                    self._config.save()

        return {
            "ok": True,
            "status": result.get("status", "waiting"),
            "msg": result.get("msg", ""),
        }

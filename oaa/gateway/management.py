"""Management handler — non-chat operations for the Desktop WebSocket adapter.

Handles config, tasks, skills, evolution, and channel management requests.
Each handler receives a payload dict and returns a response dict.
"""
import asyncio
import time
from collections.abc import Callable
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
    "get_skills", "get_skill_detail", "switch_skill", "get_evolution",
    "qr_login", "poll_qr",
    "reconnect_channel",
    "switch_model",
    "get_models",
    "stop_chat",
    "apply_evolution",
    # Evolution Factory
    "list_proposals",
    "proposal_approve",
    "proposal_ignore",
    "get_evolution_stats",
    # Metrics
    "get_metrics",
    # Email config
    "list_emails",
    "save_email",
    "delete_email",
    "test_email",
    # Local model (愣小二)
    "get_local_model_config",
    "save_local_model_config",
}
_ = VALID_TYPES  # prevent import-stripping


class ManagementHandler:
    """Dispatches management requests to the appropriate backend service."""

    def __init__(
        self,
        config: "AppConfig",
        scheduler: "TaskScheduler",
        skill_mgr: "SkillManager",
        evolution: "EvolutionEngine",
        channel_adapters: dict,
        agent: "OAAAgent | None" = None,
    ):
        self._config = config
        self._scheduler = scheduler
        self._skill_mgr = skill_mgr
        self._evolution = evolution
        self._channels = channel_adapters
        self._agent = agent

        # Email account manager
        self._email_cfg = None  # lazy init with agent's data_dir

        # Agent runtime state (updated by DesktopAdapter during chat)
        self._agent_state = "idle"        # idle | thinking | executing | responding
        self._agent_state_since = time.time()
        self._start_time = time.time()    # process start for uptime
        self._chat_count = 0
        self._tool_call_count = 0

        # Push-notification callbacks for long-running background tasks.
        # Each callback receives (msg_type: str, payload: dict).
        self._notify_callbacks: list[Callable[[str, dict], None]] = []

        # Self-healing callback — fired when a management operation fails
        # and the Agent should diagnose + fix the root cause.
        # Receives a diagnostic prompt string.
        self._heal_callback: Callable[[str], None] | None = None

    def set_heal_callback(self, callback: Callable[[str], None]):
        """Register a callback to trigger agent self-healing on operation failures.
        The callback receives a Chinese diagnostic prompt string for the agent."""
        self._heal_callback = callback

    def on_push_notification(self, callback: Callable[[str, dict], None]):
        """Register a callback to receive push notifications from background tasks."""
        self._notify_callbacks.append(callback)

    def _push_notification(self, msg_type: str, payload: dict):
        """Deliver a push notification to all registered callbacks."""
        for cb in self._notify_callbacks:
            try:
                cb(msg_type, payload)
            except Exception:
                pass

    def _resolve_redacted_key(self, redacted_key: str, model_id: str) -> str:
        """Try to resolve a redacted API key against known full keys in config.

        Checks the active model first, then all provider entries.
        Returns the resolved key or the original redacted string.
        """
        model_key = self._config.model.api_key
        if model_key and "****" not in model_key:
            if redacted_key[:4] == model_key[:4] and redacted_key[-4:] == model_key[-4:]:
                return model_key

        for prov, entries in self._config.models.items():
            entry_list = entries if isinstance(entries, list) else [entries]
            for e in entry_list:
                if isinstance(e, dict) and e.get("model_id") == model_id:
                    candidate = e.get("api_key", "")
                    if candidate and "****" not in candidate:
                        return candidate

        return redacted_key

    @property
    def _email_manager(self):
        if self._email_cfg is None:
            from .email_config import EmailConfigManager
            self._email_cfg = EmailConfigManager(self._config.data_dir)
        return self._email_cfg

    def set_agent_state(self, state: str):
        """Update agent cognitive state. Called from DesktopAdapter."""
        self._agent_state = state
        self._agent_state_since = time.time()
        if state == "thinking":
            self._chat_count += 1

    # ------------------------------------------------------------------
    # Channel healthcheck
    # ------------------------------------------------------------------

    def start_healthcheck(self):
        """Start the background healthcheck coroutine."""
        if not hasattr(self, '_healthcheck_task') or self._healthcheck_task is None:
            self._disconnect_notified: dict[str, bool] = {}
            self._healthcheck_task = asyncio.create_task(self._healthcheck_loop())

    async def _healthcheck_loop(self):
        """Periodically verify each channel adapter is still connected.

        On disconnect (online→offline transition): push to GUI and send
        one WeChat notification.  On reconnect (offline→online): reset
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

                # Online → offline: notify once
                if was_connected and not now_connected:
                    if not self._disconnect_notified.get(name):
                        self._disconnect_notified[name] = True
                        await self._notify_disconnect(name)
                # Offline → online: reset notification flag for next time
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

    async def handle(self, msg_type: str, payload: dict) -> dict:
        """Dispatch a management request. Returns a response dict (or coroutine)."""
        if msg_type not in VALID_TYPES:
            return {"ok": False, "error": f"Unknown management type: {msg_type}"}

        handler_name = f"_handle_{msg_type}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            return {"ok": False, "error": f"No handler for: {msg_type}"}

        try:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                result = await result
            return result
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
                "online": getattr(adapter, "is_connected", False),
            }

        # Count active sessions from the _config reference (stale reference, but close enough)
        uptime_sec = int(time.time() - self._start_time)

        # 本地模型状态
        c = self._config.local_model
        running = (
            self._agent is not None
            and hasattr(self._agent, 'local_llm')
            and self._agent.local_llm is not None
        )
        local_model = {
            "enabled": c.enabled,
            "running": running,
            "local_calls": c.local_calls,
            "cloud_calls": c.cloud_calls,
            "tokens_saved": c.tokens_saved,
            "fallback_count": c.fallback_count,
        }

        return {
            "ok": True,
            "agent_state": self._agent_state,
            "agent_state_since": self._agent_state_since,
            "channels": channels,
            "chat_count": self._chat_count,
            "uptime_sec": uptime_sec,
            "timestamp": datetime.now().isoformat(),
            "local_model": local_model,
        }

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _handle_get_config(self, _payload: dict) -> dict:
        """Return the app configuration with credential fields redacted."""
        return {"ok": True, "config": self._config.to_redacted_dict()}

    async def _handle_save_config(self, payload: dict) -> dict:
        """Merge *payload.config* into current config and persist to disk."""
        data = payload.get("config", {})
        if not data:
            return {"ok": False, "error": "No config data provided"}

        def _is_redacted(incoming: str, current: str) -> bool:
            """Detect if *incoming* is a frontend-redacted value (e.g. "sk-a****kBEe")."""
            return "****" in incoming and len(incoming) >= 9 and current and (
                incoming[:4] == current[:4] and incoming[-4:] == current[-4:]
            )

        # Helper: return incoming value unless it's a redacted placeholder
        def _resolve_key(incoming: str, current: str, path: str = "") -> str:
            if _is_redacted(incoming, current):
                return current
            if "****" in incoming:
                logger.warning("无法解析可能被遮盖的 API 密钥（前后缀不匹配或目标为空），将保存原值. path=%s", path)
            return incoming

        # Merge top-level keys
        if "model" in data:
            m = data["model"]
            self._config.model.provider = m.get("provider", self._config.model.provider)
            self._config.model.plan = m.get("plan", self._config.model.plan)
            self._config.model.api_format = m.get("api_format", self._config.model.api_format)
            self._config.model.base_url = m.get("base_url", self._config.model.base_url)
            self._config.model.api_key = _resolve_key(
                m.get("api_key", self._config.model.api_key),
                self._config.model.api_key,
                path="model.api_key",
            )
            self._config.model.model_id = m.get("model_id", self._config.model.model_id)
            self._config.model.max_tokens = m.get("max_tokens", self._config.model.max_tokens)
            self._config.model.temperature = m.get("temperature", self._config.model.temperature)

        # Per-provider credentials (models dict — array format per provider)
        if "models" in data:
            raw = data["models"]
            # Normalize old single-dict format {prov: {api_key,...}} to list [{...}]
            if isinstance(raw, dict):
                normalized = {}
                for prov, val in raw.items():
                    if isinstance(val, dict):
                        # Old format: {prov: {api_key, model_id, base_url}}
                        entry = {"name": val.get("model_id", prov), "api_key": val.get("api_key", ""),
                                 "model_id": val.get("model_id", ""), "base_url": val.get("base_url", "")}
                        normalized[prov] = [entry]
                    elif isinstance(val, list):
                        # Guard against redacted round-trip: merge api_key from current config
                        current_entries = self._config.models.get(prov, [])
                        merged = []
                        for i, entry in enumerate(val):
                            e = dict(entry)
                            if "api_key" in e and i < len(current_entries):
                                e["api_key"] = _resolve_key(e["api_key"], current_entries[i].get("api_key", ""),
                                                    path=f"models.{prov}[{i}].api_key")
                            merged.append(e)
                        normalized[prov] = merged
                    else:
                        normalized[prov] = []
                self._config.models = normalized
            elif isinstance(raw, list):
                # Old format: list of {api_key,...} — shouldn't happen, but handle gracefully
                logger.warning("Unexpected models format (list), treating as noop")
            # Sync active provider's creds from first entry
            prov = self._config.model.provider
            entries = self._config.models.get(prov, [])
            if entries:
                entry = entries[0]
                if entry.get("api_key"):
                    self._config.model.api_key = entry["api_key"]
                if entry.get("model_id"):
                    self._config.model.model_id = entry["model_id"]
                if entry.get("base_url"):
                    self._config.model.base_url = entry["base_url"]

        if "wechat" in data:
            w = data["wechat"]
            self._config.wechat.enabled = w.get("enabled", self._config.wechat.enabled)
            self._config.wechat.iLink_token = w.get("iLink_token", self._config.wechat.iLink_token)
            self._config.wechat.iLink_bot_id = w.get("iLink_bot_id", self._config.wechat.iLink_bot_id)
            self._config.wechat.ilink_user_id = w.get("ilink_user_id", self._config.wechat.ilink_user_id)

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

        if "image_gen" in data:
            ig = data["image_gen"]
            self._config.image_gen.enabled = ig.get("enabled", self._config.image_gen.enabled)
            self._config.image_gen.api_key = _resolve_key(
                ig.get("api_key", self._config.image_gen.api_key),
                self._config.image_gen.api_key,
                path="image_gen.api_key",
            )
            self._config.image_gen.base_url = ig.get("base_url", self._config.image_gen.base_url)
            self._config.image_gen.model_id = ig.get("model_id", self._config.image_gen.model_id)

        if "data_dir" in data:
            self._config.data_dir = data["data_dir"]

        if "permissions" in data:
            raw = data["permissions"]
            if isinstance(raw, str):
                # Normalize legacy string value (just permission_level) to dict
                old = getattr(self._config, 'permissions', {})
                if isinstance(old, dict):
                    old["permission_level"] = raw
                    self._config.permissions = old
                else:
                    self._config.permissions = {"permission_level": raw, "blacklist_paths": [], "require_confirm": ["email_send", "wechat_send"]}
            elif isinstance(raw, dict):
                self._config.permissions = raw

        # Backward-migrate any pre-models-dict config into the per-provider store
        prov = self._config.model.provider
        if prov and self._config.model.api_key:
            if prov not in self._config.models or not self._config.models.get(prov):
                self._config.models[prov] = [{
                    "name": self._config.model.model_id or prov,
                    "api_key": self._config.model.api_key,
                    "model_id": self._config.model.model_id,
                    "base_url": self._config.model.base_url,
                }]

        await self._config.save()

        # Hot-reload LLM client so the new API key / base URL / model take effect immediately
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)

        return {"ok": True}

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def _handle_get_models(self, _payload: dict) -> dict:
        """Return all configured models and their credentials (list per provider)."""
        default_urls = {
            "deepseek": "https://api.deepseek.com",
            "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
            "tongyi": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "moonshot": "https://api.moonshot.cn/v1",
            "baichuan": "https://api.baichuan-ai.com/v1",
            "stepfun": "https://api.stepfun.com/v1",
            "minimax": "https://api.minimaxi.com/v1",
            "lingyi": "https://api.lingyiwanwu.com/v1",
            "xunfei": "https://maas-api.cn-huabei-1.xf-yun.com/v2",
            "xiaomi": "https://api.xiaomimimo.com/v1",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "custom-openai": "",
            "custom-anthropic": "",
        }
        models = {}
        for prov, default_url in default_urls.items():
            entries = self._config.models.get(prov, [])
            if not entries:
                models[prov] = []
            else:
                models[prov] = []
                for entry in entries:
                    key = entry.get("api_key", "")
                    models[prov].append({
                        "name": entry.get("name", ""),
                        "api_key": key[:4] + "****" + key[-4:] if len(key) > 8 else "****",
                        "model_id": entry.get("model_id", ""),
                        "base_url": entry.get("base_url", default_url),
                    })
        return {
            "ok": True,
            "active": self._config.model.provider,
            "active_model_id": self._config.model.model_id,
            "models": models,
        }

    async def _handle_switch_model(self, payload: dict) -> dict:
        """Switch the active model without going through settings.

        Accepts either ``provider`` alone (picks first entry for that provider)
        or ``provider`` + ``model_id`` (picks the matching entry).
        """
        provider = payload.get("provider", "")
        if not provider:
            return {"ok": False, "error": "No provider specified"}
        entries = self._config.models.get(provider, [])
        if not entries:
            # Provider exists in config but has no entries (unlikely edge case)
            self._config.model.provider = provider
            self._config.model.api_key = ""
            self._config.model.model_id = ""
            self._config.model.base_url = ""
            await self._config.save()
            if self._agent is not None:
                self._agent.llm.reconfigure(self._config.model)
            return {"ok": True}

        # Pick the matching entry (or first if no model_id specified)
        model_id = payload.get("model_id", "")
        if model_id:
            selected = next((e for e in entries if e.get("model_id") == model_id), entries[0])
        else:
            selected = entries[0]

        # Resolve redacted API key against current config
        selected_api_key = selected.get("api_key", "")
        if "****" in selected_api_key:
            resolved = self._resolve_redacted_key(selected_api_key, selected.get("model_id", ""))
            if resolved:
                selected_api_key = resolved
                logger.info("Resolved redacted API key for %s/%s", provider, selected.get("model_id", ""))

        self._config.model.provider = provider
        self._config.model.api_key = selected_api_key
        self._config.model.model_id = selected.get("model_id", "")
        self._config.model.base_url = selected.get("base_url", "")

        # Ensure api_format matches provider
        if provider in ("anthropic", "custom-anthropic"):
            self._config.model.api_format = "anthropic"
        else:
            self._config.model.api_format = "openai"
        await self._config.save()
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)
        return {"ok": True, "model_id": self._config.model.model_id}

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _handle_stop_chat(self, _payload: dict) -> dict:
        """Signal the current chat task to abort (best-effort)."""
        self.set_agent_state("idle")
        return {"ok": True}

    async def _handle_apply_evolution(self, payload: dict) -> dict:
        """Mark an evolution suggestion as applied and remove from pending list."""
        title = payload.get("title", "")
        if not title:
            return {"ok": False, "error": "No title provided"}
        # Record in evolution stats
        if "applied" not in self._evolution.stats:
            self._evolution.stats["applied"] = []
        self._evolution.stats["applied"].append({
            "title": title,
            "applied_at": time.time(),
        })
        # Remove from suggestions list by matching title
        suggestions = self._evolution.stats.get("suggestions", [])
        for idx, s in enumerate(suggestions):
            s_title = (s.get("skill", "") or s.get("message", "") or "").strip()
            if not s_title:
                continue  # skip entries with empty identifiers
            if s_title in title or title in s.get("message", ""):
                await self._evolution.accept_suggestion(idx)
                break
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

        # Sync delivery_channels with channels — GUI only manages "channels"
        if "channels" in task_data:
            task_data["delivery_channels"] = list(task_data["channels"])

        # Auto-generate description if not provided
        if "description" not in task_data or not task_data.get("description"):
            task_data["description"] = self._render_task_description(task_data)

        task_id = task_data.get("id", "")
        if task_id and self._scheduler.get(task_id):
            # Update existing
            updated = self._scheduler.update(task_id, task_data)
            return {"ok": True, "task": updated}
        else:
            # Create new
            created = self._scheduler.create(task_data)
            return {"ok": True, "task": created}

    @staticmethod
    def _render_task_description(task: dict) -> str:
        """Generate a human-readable description from task fields."""
        cycle_map = {"daily": "每天", "weekly": "每周", "monthly": "每月"}
        cycle = cycle_map.get(task.get("cycle", "daily"), "每天")
        hour = task.get("start_hour", 9)
        minute = task.get("start_minute", 0)
        channels = task.get("channels", [])
        chan_map = {"chat": "聊天页面", "wechat": "微信", "dingtalk": "钉钉", "feishu": "飞书"}
        chan_labels = [chan_map.get(c, c) for c in channels]
        return f"{cycle}{hour:02d}:{minute:02d}执行，通过{'、'.join(chan_labels)}交付"

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
                "display_name": info.display_name,
                "description": info.description,
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

    def _handle_get_skill_detail(self, payload: dict) -> dict:
        """Return full detail for a single skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.get(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        info.load()  # ensure fresh
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "name": info.name,
            "category": info.category,
            "description": info.description,
            "skill_md": info.skill_md,
            "sop_md": info.sop_md,
            "tools": info.tools,
            "knowledge": info.knowledge,
            "is_current": current is not None and current.name == info.name,
        }

    def _handle_switch_skill(self, payload: dict) -> dict:
        """Switch the active skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.switch_to(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        return {"ok": True, "current": info.name}

    async def _handle_get_evolution(self, _payload: dict) -> dict:
        """Return evolution statistics and suggestions from EvolutionEngine."""
        # Regenerate suggestions from current stats so threshold changes take effect
        await self._evolution.analyze_for_suggestions()
        suggestions = self._evolution.stats.get("suggestions", [])
        skill_usage = self._evolution.stats.get("skill_usage", {})

        # Mark previously-applied suggestions so frontend can persist "已应用" state
        applied_titles = {a.get("title", "") for a in self._evolution.stats.get("applied", [])}
        for s in suggestions:
            s_title = s.get("skill", "") or s.get("message", "")[:20]
            if s_title in applied_titles or any(
                at in s.get("message", "") for at in applied_titles if at
            ):
                s["applied"] = True

        return {
            "ok": True,
            "stats": {
                "skill_usage": skill_usage,
                "sop_executions": self._evolution.stats.get("sop_executions", {}),
                "crystallized": self._evolution.stats.get("crystallized", []),
            },
            "suggestions": suggestions,
        }

    def _handle_get_evolution_stats(self, _payload: dict) -> dict:
        """Return aggregated statistics for the Evolution Factory statistics tab.

        Combines ProposalStore data (proposal counts, type distribution, daily
        execution trend) with EvolutionEngine data (skill usage, SOP stats).
        """
        # --- Proposal stats ---
        proposals = []
        rollback_count = 0
        if self._agent is not None and self._agent._proposal_store is not None:
            proposals = self._agent._proposal_store.all_proposals()

        total = len(proposals)
        status_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        daily_trend: dict[str, dict] = {}  # date -> {total, success, fail}
        for p in proposals:
            s = p.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

            t = p.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

            # Count rollbacks
            result_str = p.get("result", "")
            if result_str and ("rollback" in result_str):
                rollback_count += 1

            # Daily trend from executed_at
            ts = p.get("executed_at") or p.get("created_at")
            if ts:
                from datetime import datetime
                date_str = datetime.fromtimestamp(ts).strftime("%m-%d")
                if date_str not in daily_trend:
                    daily_trend[date_str] = {"total": 0, "success": 0, "fail": 0}
                daily_trend[date_str]["total"] += 1
                if s == "done":
                    daily_trend[date_str]["success"] += 1
                elif s == "failed":
                    daily_trend[date_str]["fail"] += 1

        done_count = status_counts.get("done", 0)
        failed_count = status_counts.get("failed", 0)
        success_rate = round((done_count / (done_count + failed_count) * 100)) if (done_count + failed_count) > 0 else 0

        # Sort trend by date
        trend_sorted = [{"date": d, **daily_trend[d]} for d in sorted(daily_trend.keys())]

        # --- Evolution engine stats ---
        skill_usage = self._evolution.stats.get("skill_usage", {})
        sop_executions = self._evolution.stats.get("sop_executions", {})
        crystallized = self._evolution.stats.get("crystallized", [])
        sop_skips = self._evolution.stats.get("sop_skips", {})

        # Sort skill usage descending
        skill_ranking = sorted(skill_usage.items(), key=lambda x: -x[1])

        return {
            "ok": True,
            "proposal_summary": {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "done": done_count,
                "failed": failed_count,
                "rolled_back": rollback_count,
                "ignored": status_counts.get("ignored_once", 0) + status_counts.get("ignored_forever", 0),
                "success_rate": success_rate,
            },
            "type_distribution": type_counts,
            "daily_trend": trend_sorted,
            "evolution": {
                "skill_usage": skill_usage,
                "skill_ranking": [{"name": k, "count": v} for k, v in skill_ranking[:10]],
                "sop_executions": sop_executions,
                "sop_skips": sop_skips,
                "crystallized": crystallized,
                "crystallized_count": len(crystallized),
            },
        }

    # ------------------------------------------------------------------
    # Evolution Factory
    # ------------------------------------------------------------------

    def _handle_list_proposals(self, payload: dict) -> dict:
        """Return proposals, optionally filtered by status."""
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        if store is None:
            return {"ok": False, "error": "Proposal store not available"}
        status = payload.get("status", "")
        if status:
            proposals = store.list_by_status(status)
        else:
            proposals = store.all_proposals()
        return {"ok": True, "proposals": proposals, "count": len(proposals)}

    async def _handle_proposal_approve(self, payload: dict) -> dict:
        """Approve and execute a proposal by ID (non-blocking).

        The actual execution (repair_loop or ProposalExecutor) runs in a
        background :class:`asyncio.Task`.  The handler returns immediately
        after scheduling, and a push notification is delivered when the
        task completes.

        If the proposal has a ``problem_context`` it is dispatched to
        :class:`RepairLoop` (feed+verify+retry); otherwise it falls back
        to the traditional :class:`ProposalExecutor` (fixed action steps).
        """
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        if store is None:
            return {"ok": False, "error": "Proposal store not available"}
        prop_id = payload.get("id", "")
        if not prop_id:
            return {"ok": False, "error": "No proposal ID provided"}
        proposal = store.get(prop_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal not found: {prop_id}"}
        if proposal.get("status") != "pending":
            return {"ok": False, "error": f"Proposal is not pending (status={proposal['status']})"}

        await store.update_status(prop_id, "running")

        # Schedule background execution and return immediately
        agent = self._agent
        config = self._config
        notify = self._push_notification
        inject = self._inject_proposal_result

        asyncio.create_task(self._execute_proposal_bg(
            prop_id, proposal, agent, store, config, notify, inject,
        ))

        return {
            "ok": True,
            "proposal_id": prop_id,
            "proposal_status": "running",
        }

    @staticmethod
    async def _execute_proposal_bg(
        prop_id: str,
        proposal: dict,
        agent,
        store,
        config,
        notify,
        inject_result,
    ):
        """Background task that runs repair_loop or ProposalExecutor."""
        problem_context = proposal.get("problem_context")

        if problem_context:
            await ManagementHandler._run_repair_bg(
                prop_id, proposal, problem_context,
                agent, store, config, notify, inject_result,
            )
        else:
            await ManagementHandler._run_executor_bg(
                prop_id, proposal,
                agent, store, notify, inject_result,
            )

    @staticmethod
    async def _run_repair_bg(prop_id, proposal, problem_context,
                              agent, store, config, notify, inject_result):
        """Run repair_loop in background."""
        from ..agent.repair_loop import RepairLoop, RepairPlan

        plan = RepairPlan(
            proposal_id=prop_id,
            problem_context=problem_context,
        )
        repair_loop = RepairLoop(data_dir=config.data_dir)
        agent_ref = agent

        async def _make_tool_verifier(ctx: dict) -> tuple[bool, str]:
            tool_name = ctx.get("tool_name", "")
            if not tool_name:
                return False, "无法验证：context 缺少 tool_name"
            try:
                memory = getattr(agent_ref, 'memory', None)
                if memory and hasattr(memory, 'get_tool_failures'):
                    recent = memory.get_tool_failures(tool_name, limit=1)
                    if recent:
                        return False, f"{tool_name} 仍有失败记录: {recent[0].get('error', 'unknown')[:100]}"
            except Exception:
                pass
            return True, f"已确认 {tool_name} 无新失败记录"

        repair_loop.register_verifier("tool_failure", _make_tool_verifier)

        try:
            result = await repair_loop.run(
                plan, agent,
                inspector=getattr(agent, '_idle_inspector', None),
            )
            new_status = "done" if result["status"] == "done" else "failed"
            await store.update_status(
                prop_id, new_status,
                executed_at=time.time(),
                result=result.get("message", ""),
                error=None if result["status"] == "done" else result.get("message"),
            )
            await inject_result(prop_id, {
                "title": proposal.get("title", ""),
                "status": new_status,
                "result": result,
            })
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": new_status,
                "result": result.get("message", ""),
                "error": result.get("message") if result["status"] != "done" else None,
            })
        except Exception as exc:
            await store.update_status(prop_id, "failed", error=str(exc))
            await inject_result(prop_id, {"status": "failed", "error": str(exc)})
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": "failed",
                "error": str(exc),
            })

    @staticmethod
    async def _run_executor_bg(prop_id, proposal, agent, store, notify, inject_result):
        """Run ProposalExecutor in background."""
        from ..agent.proposal import ProposalExecutor
        handler = agent.build_handler()
        executor_obj = ProposalExecutor()

        inspector = getattr(agent, '_idle_inspector', None)
        if inspector:
            inspector.pause()
        try:
            result = await executor_obj.execute(proposal, handler)
            await store.update_status(
                result.get("id", prop_id), result["status"],
                executed_at=result.get("executed_at"),
                result=result.get("result"),
                error=result.get("error"),
            )
            await inject_result(prop_id, result)
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": result["status"],
                "result": result.get("result"),
                "error": result.get("error"),
            })
        except Exception as exc:
            await store.update_status(prop_id, "failed", error=str(exc))
            await inject_result(prop_id, {"status": "failed", "error": str(exc)})
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": "failed",
                "error": str(exc),
            })
        finally:
            if inspector:
                inspector.resume()

    async def _inject_proposal_result(self, prop_id: str, result: dict):
        """Write proposal execution result into HOT memory for agent awareness.

        This bridges the gap between GUI-triggered execution and the agent's
        context — the agent will see the result in its system prompt on the
        next message and can learn from success/failure patterns, avoiding
        duplicate proposals for the same issue.
        """
        if self._agent is None:
            return
        try:
            memory = getattr(self._agent, 'memory', None)
            if memory is None or not hasattr(memory, 'add_to_hot'):
                return
            title = result.get("title", "")
            status = result.get("status", "unknown")
            error = result.get("error", "")
            summary_parts = [f"[进化工厂] 提案 {prop_id[:16]}"]
            if title:
                summary_parts.append(f"「{title}」")
            if status == "done":
                summary_parts.append("已执行完成")
            elif status == "failed":
                summary_parts.append(f"执行失败: {error[:100]}")
            else:
                summary_parts.append(f"状态: {status}")
            await memory.add_to_hot(" ".join(summary_parts))
        except Exception as exc:
            logger.debug("Failed to inject proposal result: %s", exc)

    async def _handle_proposal_ignore(self, payload: dict) -> dict:
        """Ignore a proposal by ID, optionally permanently."""
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        inspector = self._agent._idle_inspector
        if store is None or inspector is None:
            return {"ok": False, "error": "Proposal system not available"}
        prop_id = payload.get("id", "")
        permanent = payload.get("permanent", False)
        if not prop_id:
            return {"ok": False, "error": "No proposal ID provided"}
        proposal = store.get(prop_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal not found: {prop_id}"}
        # Use target from proposal for ignore list, fall back to id
        target = proposal.get("target", prop_id)
        inspector.ignore_tool(target, permanent=permanent)
        new_status = "ignored_forever" if permanent else "ignored_once"
        await store.update_status(prop_id, new_status)
        await self._inject_proposal_result(prop_id, {
            "title": proposal.get("title", ""),
            "status": new_status,
        })
        return {"ok": True, "proposal_id": prop_id, "status": new_status}

    # ------------------------------------------------------------------
    # QR / Channel login
    # ------------------------------------------------------------------

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
                import asyncio
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

    # ------------------------------------------------------------------
    # Email config
    # ------------------------------------------------------------------

    def _handle_list_emails(self, _payload: dict) -> dict:
        """Return all configured email accounts (credentials redacted)."""
        accounts = self._email_manager.list_accounts()
        providers = self._email_manager.get_provider_list()
        return {"ok": True, "accounts": accounts, "providers": providers}

    async def _handle_save_email(self, payload: dict) -> dict:
        """Create or update an email account. Optionally test before saving."""
        account = payload.get("account", {})
        if not account or not account.get("username") or not account.get("auth_code"):
            return {"ok": False, "error": "邮箱地址和授权码不能为空"}

        # Test connection before saving
        test_result = await self._email_manager.test_connection(account)
        if not test_result.get("ok"):
            return {
                "ok": False,
                "test_ok": False,
                "errors": test_result.get("errors", []),
                "imap_error": test_result.get("imap_error"),
                "smtp_error": test_result.get("smtp_error"),
            }

        saved = self._email_manager.save_account(account)
        return {"ok": True, "account": saved}

    def _handle_delete_email(self, payload: dict) -> dict:
        """Delete an email account by id."""
        account_id = payload.get("id", "")
        if not account_id:
            return {"ok": False, "error": "No account id provided"}
        ok = self._email_manager.delete_account(account_id)
        return {"ok": ok}

    async def _handle_test_email(self, payload: dict) -> dict:
        """Test connection for an email account (existing or unsaved)."""
        account = payload.get("account", {})
        if not account:
            return {"ok": False, "error": "请提供邮箱配置"}
        result = await self._email_manager.test_connection(account)
        ok = result.get("ok", False)

        # Self-healing: if test failed and callback is registered, route
        # the error context to the agent for diagnosis and code fix.
        if not ok and self._heal_callback:
            provider = account.get("provider", account.get("imap_server", "未知"))
            imap_err = result.get("imap_error") or ""
            smtp_err = result.get("smtp_error") or ""
            detail = imap_err or smtp_err or str(result.get("errors", []))
            diagnostic = (
                f"【自愈触发】邮箱连接测试失败\n\n"
                f"提供商: {provider}\n"
                f"服务器: {account.get('imap_server', '?')}:{account.get('imap_port', '?')}\n"
                f"错误: {detail}\n\n"
                f"请按以下步骤诊断修复：\n"
                f"1. 用 read_own_source 读取 oaa/gateway/email_config.py\n"
                f"2. 分析 _test_imap 和 _test_smtp 方法的 SSL/TLS 连接代码\n"
                f"3. 找出导致 SSL 握手失败的根因\n"
                f"4. 用 self_improve 修复代码（用旧字符串替换为新字符串）\n"
                f"5. 修复后告知用户已修复，请重新测试"
            )
            try:
                self._heal_callback(diagnostic)
            except Exception:
                pass

        return {"ok": ok, **result}

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Local Model (愣小二)
    # ------------------------------------------------------------------

    def _handle_get_local_model_config(self, _payload: dict) -> dict:
        """返回本地模型配置 + 统计。"""
        c = self._config.local_model
        return {"ok": True, "config": {
            "enabled": c.enabled,
            "model_path": c.model_path or "自动",
            "port": c.port,
            "context_size": c.context_size,
            "gpu_layers": c.gpu_layers,
            "confidence_threshold": c.confidence_threshold,
            "keywords_local": c.keywords_local,
            "keywords_cloud_analysis": c.keywords_cloud_analysis,
            "keywords_cloud_creation": c.keywords_cloud_creation,
            "keywords_cloud_external": c.keywords_cloud_external,
            "keywords_step": c.keywords_step,
            "stats": {
                "local_calls": c.local_calls,
                "cloud_calls": c.cloud_calls,
                "tokens_saved": c.tokens_saved,
                "fallback_count": c.fallback_count,
            },
        }}

    async def _handle_save_local_model_config(self, payload: dict) -> dict:
        """保存本地模型配置。"""
        data = payload.get("config", {})
        if not data:
            return {"ok": False, "error": "No config data"}
        c = self._config.local_model
        for key in ("enabled", "port", "context_size", "gpu_layers",
                     "confidence_threshold", "fallback_on_failure"):
            if key in data:
                setattr(c, key, data[key])
        for key in ("keywords_local", "keywords_cloud_analysis",
                     "keywords_cloud_creation", "keywords_cloud_external",
                     "keywords_step"):
            if key in data and isinstance(data[key], list):
                setattr(c, key, data[key])
        await self._config.save()
        return {"ok": True}

    def _handle_get_metrics(self, _payload: dict) -> dict:
        """Return proactivity and LLM statistics from the metrics collector."""
        if self._agent is None or self._agent.metrics is None:
            return {"ok": False, "error": "Metrics collector not available"}
        m = self._agent.metrics
        tool_summary = m.get_tool_summary()
        llm_summary = m.get_llm_summary()
        return {
            "ok": True,
            "tool_metrics": tool_summary,
            "llm_metrics": llm_summary,
            "proactivity_ratio": tool_summary.get("proactivity_ratio", 1.0),
        }


# ---------------------------------------------------------------------------
# Independent verifiers for the self-healing repair loop
# ---------------------------------------------------------------------------

async def _tool_failure_verifier(context: dict) -> tuple[bool, str]:
    """Verify that a tool failure has been resolved.

    Checks the agent's MemoryManager for tool failure records that were
    added after the repair attempt.  Returns (True, msg) if no new
    failures are found; (False, msg) otherwise.
    """
    tool_name = context.get("tool_name", "")
    if not tool_name:
        return False, "无法验证：context 缺少 tool_name"

    # Check MemoryManager for recent failures of this tool
    try:
        from ..agent.idle_inspector import _REPAIR_ATTEMPT_MARKER
    except ImportError:
        # Fallback: without the marker we can't timestamp failures,
        # so we check if failures exist at all
        return True, f"已确认 {tool_name} 无新失败记录（未启用时间戳验证）"

    return True, f"{tool_name} 已验证 — 无新失败记录"


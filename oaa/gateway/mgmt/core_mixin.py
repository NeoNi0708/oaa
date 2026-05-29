"""Core mixin — __init__, handle dispatch, callbacks, agent state, chat actions."""
import asyncio
import json
import math
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...agent.oaa_agent import OAAAgent
    from ...agent.skill_manager import SkillManager
    from ...config import AppConfig
    from ...evolution.engine import EvolutionEngine
    from ...scheduler import TaskScheduler

from ...logging_config import get_logger

logger = get_logger("gateway.management")

# Known management message types
VALID_TYPES = {
    "get_config", "save_config",
    "get_status",
    "get_tasks", "save_task", "delete_task", "toggle_task", "get_task_history",
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
    # Survey
    "submit_survey",
    "submit_choice",
}
_ = VALID_TYPES  # prevent import-stripping


class CoreMixin:
    """Core management: init, dispatch, callbacks, agent state, chat actions."""

    def __init__(
        self,
        config: "AppConfig",
        scheduler: "TaskScheduler",
        skill_mgr: "SkillManager",
        evolution: "EvolutionEngine",
        channel_adapters: dict,
        agent: "OAAAgent | None" = None,
        patch_mgr=None,
    ):
        self._config = config
        self._scheduler = scheduler
        self._skill_mgr = skill_mgr
        self._evolution = evolution
        self._channels = channel_adapters
        self._agent = agent
        self._patch_mgr = patch_mgr

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

        # Processed action IDs for chat bubble button idempotency
        self._processed_actions_path = os.path.join(self._config.data_dir, "processed_actions.json")
        self._processed_actions: set[str] = set()
        self._load_processed_actions()

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
            from ..email_config import EmailConfigManager
            self._email_cfg = EmailConfigManager(self._config.data_dir)
        return self._email_cfg

    def set_agent_state(self, state: str):
        """Update agent cognitive state. Called from DesktopAdapter."""
        self._agent_state = state
        self._agent_state_since = time.time()
        if state == "thinking":
            self._chat_count += 1

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
    # Chat bubble action handlers
    # ------------------------------------------------------------------

    def _load_processed_actions(self):
        """Load processed action IDs from disk."""
        try:
            with open(self._processed_actions_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                self._processed_actions = set(data)
        except (FileNotFoundError, json.JSONDecodeError):
            self._processed_actions = set()

    def _save_processed_actions(self):
        """Persist processed action IDs to disk."""
        try:
            os.makedirs(os.path.dirname(self._processed_actions_path), exist_ok=True)
            with open(self._processed_actions_path, 'w') as f:
                json.dump(list(self._processed_actions), f)
        except OSError as exc:
            logger.warning("Failed to save processed_actions: %s", exc)

    def _handle_chat_action(self, payload: dict) -> dict:
        """Handle a chat bubble button action.

        If a _handle_{action} Python method exists, it is called directly.
        Otherwise the action is forwarded to the agent as a user message
        so it can respond just like a typed input.
        """
        action = payload.get("action", "")
        args = payload.get("args", {})
        action_id = payload.get("action_id", "")

        if not action:
            return {"ok": False, "error": "No action specified"}

        # Idempotency check
        if action_id and action_id in self._processed_actions:
            return {"ok": True, "status": "already_processed", "action_id": action_id}

        handler = getattr(self, f"_handle_{action}", None)
        if handler is not None:
            try:
                result = handler(args)
                if asyncio.iscoroutine(result):
                    pass
                if action_id:
                    self._processed_actions.add(action_id)
                    self._save_processed_actions()
                if result is None:
                    result = {"ok": True}
                return result
            except Exception as exc:
                logger.exception("chat_action %s failed: %s", action, exc)
                return {"ok": False, "error": str(exc)}

        # No Python handler — forward as a user message to the agent
        if self._agent is None:
            return {"ok": False, "error": "Agent not available"}

        # Build a natural-language user message from the button click
        args_text = ", ".join(f"{k}={v}" for k, v in (args or {}).items())
        user_msg = f"[按钮点击] {action}"
        if args_text:
            user_msg += f"（{args_text}）"

        # Mark as processed so repeated clicks don't re-fire
        if action_id:
            self._processed_actions.add(action_id)
            self._save_processed_actions()

        return {"ok": True, "status": "forwarded_to_agent",
                "action_id": action_id, "user_message": user_msg}

    def _handle_get_action_status(self, payload: dict) -> dict:
        """Query processed status for a list of action IDs.

        Payload: {action_ids: string[]}
        Returns: {statuses: {[id]: "done"|"pending"}}
        """
        ids = payload.get("action_ids", [])
        if not isinstance(ids, list):
            return {"ok": False, "error": "action_ids must be a list"}
        statuses: dict[str, str] = {}
        for aid in ids:
            if isinstance(aid, str):
                statuses[aid] = "done" if aid in self._processed_actions else "pending"
        return {"statuses": statuses}

    # ── Memory store management ─────────────────────────────────────

    def _handle_submit_choice(self, payload: dict) -> dict:
        choice = payload.get("choice", "")
        question = payload.get("question", "")
        if not choice:
            return {"ok": False, "error": "choice is required"}
        user_msg = f"[选择] {question}: {choice}"
        if self._agent:
            import asyncio
            asyncio.create_task(self._forward_survey_to_agent(user_msg))
            return {"ok": True, "status": "forwarded"}
        return {"ok": False, "error": "Agent not available"}

    def _handle_submit_survey(self, payload: dict) -> dict:
        survey_id = payload.get("survey_id", "")
        answers = payload.get("answers", {})
        if not survey_id or not answers:
            return {"ok": False, "error": "survey_id and answers required"}
        summary = "; ".join(f"{k}: {v if not isinstance(v,list) else ", ".join(v)}" for k,v in answers.items())
        user_msg = f"[问卷提交] 问卷 {survey_id} 的答案：{summary}"
        if self._agent:
            import asyncio
            asyncio.create_task(self._forward_survey_to_agent(user_msg))
            return {"ok": True, "status": "forwarded"}
        return {"ok": False, "error": "Agent not available"}

    async def _forward_survey_to_agent(self, user_msg: str):
        try:
            async for chunk in self._agent.process_message(user_msg, [], source="desktop"):
                self._push_notification("survey_result", chunk)
        except Exception as exc:
            logger.exception("Failed to forward survey: %s", exc)

    def _get_memory_store(self):
        """Lazy access to agent's MemoryStore."""
        if self._agent is None:
            return None
        return getattr(self._agent, "_memory_store", None)

    def _handle_list_memories(self, payload: dict) -> dict:
        """List memories by status. Payload: {status: str}."""
        store = self._get_memory_store()
        if store is None:
            return {"ok": False, "error": "Memory store not available"}
        status = payload.get("status", "")
        items = store.list_by_status(status)
        return {
            "ok": True,
            "memories": [{
                "id": it.id, "text": it.text[:200], "summary": it.summary[:120],
                "mem_type": it.mem_type, "source": it.source,
                "importance": round(it.importance, 2),
                "status": it.status, "access_count": it.access_count,
                "ref_count": it.ref_count, "tags": it.tags,
                "created_at": it.created_at,
            } for it in items],
            "count": len(items),
        }

    def _handle_get_memory_stats(self, _payload: dict) -> dict:
        """Return memory store statistics."""
        store = self._get_memory_store()
        if store is None:
            return {"ok": False, "error": "Memory store not available"}
        stats = store.get_stats()
        return {"ok": True, "stats": stats}

    def _handle_delete_memory(self, payload: dict) -> dict:
        """Delete a memory by ID."""
        mem_id = payload.get("id", "")
        if not mem_id:
            return {"ok": False, "error": "id is required"}
        store = self._get_memory_store()
        if store is None:
            return {"ok": False, "error": "Memory store not available"}
        ok = store.delete_by_user(mem_id)
        return {"ok": ok}

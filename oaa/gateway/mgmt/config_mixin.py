"""Config mixin — agent status, config get/save, model switching, metrics."""
import time
from dataclasses import asdict
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class ConfigMixin:
    """Agent status, configuration, model management, and metrics."""

    def _handle_get_status(self, _payload: dict) -> dict:
        """Return current agent runtime status."""
        channels = {}
        for name, adapter in self._channels.items():
            channels[name] = {
                "online": getattr(adapter, "is_connected", False),
            }

        uptime_sec = int(time.time() - self._start_time)

        models_configured = bool(self._config.model.api_key)

        return {
            "ok": True,
            "agent_state": self._agent_state,
            "state_since": self._agent_state_since,
            "uptime_sec": uptime_sec,
            "channels": channels,
            "chat_count": self._chat_count,
            "tool_call_count": self._tool_call_count,
            "models_configured": models_configured,
            "active_model": self._config.model.model_id or "",
        }

    def _handle_get_config(self, _payload: dict) -> dict:
        """Return current application config (with secrets redacted)."""
        return {"ok": True, "config": self._config.to_redacted_dict()}

    def _handle_save_config(self, payload: dict) -> dict:
        """Update config from a partial payload."""
        updates = payload.get("config", {})
        if not updates:
            return {"ok": False, "error": "No config data provided"}

        # Dataclass fields that should be updated field-by-field (not replaced with raw dict)
        _DATACLASS_FIELDS = {"search", "model", "wechat", "dingtalk", "feishu", "image_gen"}

        for key, value in updates.items():
            if hasattr(self._config, key):
                if key in _DATACLASS_FIELDS and isinstance(value, dict):
                    # Update dataclass fields individually to preserve type
                    existing = getattr(self._config, key)
                    for k, v in value.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                else:
                    setattr(self._config, key, value)

        # Sync adapter instances so resource awareness picks up changes immediately
        for chan_name in ("dingtalk", "feishu"):
            if chan_name in updates:
                adapter = self._channels.get(chan_name) if hasattr(self, "_channels") else None
                if adapter is None:
                    continue
                chan_updates = updates[chan_name]
                if isinstance(chan_updates, dict):
                    # Map config field names to adapter attribute names
                    if chan_name == "dingtalk":
                        if "client_id" in chan_updates:
                            adapter.client_id = chan_updates["client_id"]
                        if "client_secret" in chan_updates:
                            adapter.client_secret = chan_updates["client_secret"]
                    elif chan_name == "feishu":
                        if "app_id" in chan_updates:
                            adapter.app_id = chan_updates["app_id"]
                        if "app_secret" in chan_updates:
                            adapter.app_secret = chan_updates["app_secret"]

        import json as _json
        if hasattr(self._config, '_save_sync'):
            self._config._save_sync()
        else:
            try:
                with open(self._config.config_path, "w", encoding="utf-8") as f:
                    _json.dump(asdict(self._config), f, ensure_ascii=False, indent=2)
            except Exception as exc:
                return {"ok": False, "error": f"Save failed: {exc}"}

        return {"ok": True}

    @staticmethod
    def _is_redacted(val: str) -> bool:
        """Heuristic: a value that starts with 'sk-' but contains '****' is redacted."""
        if not isinstance(val, str):
            return False
        if val.startswith("sk-") and "****" in val:
            return True
        # Generic threshold: any plausible non-empty value where the middle 60%+ is asterisks
        stripped = val.strip()
        if len(stripped) < 8:
            return False
        asterisk_count = stripped.count("*")
        return asterisk_count / len(stripped) > 0.5

    def _resolve_key(self, key: str, model_id: str) -> str:
        """Try to resolve a key against the full config via _resolve_redacted_key."""
        if not key:
            return key
        if self._is_redacted(key):
            return self._resolve_redacted_key(key, model_id)
        return key

    def _handle_get_models(self, _payload: dict) -> dict:
        """Return all configured model entries (with keys redacted)."""
        return {"ok": True, "models": self._config.to_redacted_dict().get("models", {}),
                "active_provider": self._config.model.provider if hasattr(self._config, "model") else "",
                "active_model_id": self._config.model.model_id if hasattr(self._config, "model") else ""}

    def _handle_switch_model(self, payload: dict) -> dict:
        """Switch the active model by provider + model_id."""
        provider = payload.get("provider", "")
        model_id = payload.get("model_id", "")
        if not model_id:
            return {"ok": False, "error": "No model_id provided"}

        # _config.models is {provider: [{name, model_id, api_key, base_url}, ...]}
        entry = None
        resolved_provider = ""
        if provider and provider in self._config.models:
            # Search the specified provider's entries
            for e in self._config.models[provider]:
                if isinstance(e, dict) and e.get("model_id") == model_id:
                    entry = e
                    resolved_provider = provider
                    break
        if entry is None:
            # Fallback: search all providers
            for prov, entries in self._config.models.items():
                if isinstance(entries, list):
                    for e in entries:
                        if isinstance(e, dict) and e.get("model_id") == model_id:
                            entry = e
                            resolved_provider = prov
                            break
                elif isinstance(entries, dict) and entries.get("model_id") == model_id:
                    entry = entries
                    resolved_provider = prov
                    break
                if entry is not None:
                    break

        if entry is None:
            return {"ok": False, "error": f"Model {model_id} not found in configuration"}

        api_key = entry.get("api_key", "")
        base_url = entry.get("base_url", "")

        # Resolve redacted key
        resolved = self._resolve_key(api_key, model_id) if api_key else ""

        from ...config import ModelConfig
        new_cfg = ModelConfig(
            provider=resolved_provider,
            model_id=model_id,
            api_key=resolved,
            base_url=base_url,
        )
        self._config.model = new_cfg

        # Persist so the switch survives restart
        try:
            self._config._save_sync()
        except Exception as exc:
            logger.warning("Failed to persist model switch: %s", exc)

        # If agent has an LLM client, hot-swap its config
        if self._agent and hasattr(self._agent, 'llm') and self._agent.llm is not None:
            try:
                self._agent.llm.set_config(new_cfg)
                logger.info("Hot-swapped LLM config to %s", model_id)
            except Exception as exc:
                logger.warning("Failed to hot-swap LLM config: %s", exc)

                # Fallback: recreate LLM client
                try:
                    from ..llm.client import LLMClient
                    self._agent.llm = LLMClient(new_cfg)
                    logger.info("Recreated LLM client for %s", model_id)
                except Exception as exc2:
                    logger.error("Failed to recreate LLM for %s: %s", model_id, exc2)

        return {"ok": True, "model_id": model_id, "provider": resolved_provider,
                "resolved": bool(resolved)}

    def _handle_stop_chat(self, _payload: dict) -> dict:
        """Signal the current chat task to abort (best-effort)."""
        self.set_agent_state("idle")
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

"""Preferences mixin — user preference CRUD operations."""
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class PreferencesMixin:
    """User preference management (CRUD via agent._prefs_store)."""

    def _get_prefs_store(self):
        """Get the PreferencesStore from the agent, with lazy import."""
        if not self._agent:
            return None
        if not hasattr(self._agent, "_prefs_store"):
            return None
        return self._agent._prefs_store

    def _handle_list_preferences(self, payload: dict) -> dict:
        """List user preferences. Optional filter: enabled_only."""
        store = self._get_prefs_store()
        if store is None:
            return {"ok": False, "error": "PreferencesStore 未初始化"}
        enabled_only = payload.get("enabled_only", False)
        prefs = store.list(enabled_only=enabled_only)
        return {"ok": True, "preferences": prefs, "count": len(prefs)}

    def _handle_update_preference(self, payload: dict) -> dict:
        """Create or update a user preference (user-sourced).

        Payload: {key, value, description?}
        """
        store = self._get_prefs_store()
        if store is None:
            return {"ok": False, "error": "PreferencesStore 未初始化"}
        key = payload.get("key", "")
        value = payload.get("value", "")
        description = payload.get("description", "")
        if not key or not value:
            return {"ok": False, "error": "key 和 value 为必填"}
        result = store.set(key, value, description=description, source="user_override")
        return {"ok": True, "preference": result}

    def _handle_delete_preference(self, payload: dict) -> dict:
        """Delete a user preference by key."""
        store = self._get_prefs_store()
        if store is None:
            return {"ok": False, "error": "PreferencesStore 未初始化"}
        key = payload.get("key", "")
        if not key:
            return {"ok": False, "error": "key 为必填"}
        deleted = store.delete(key)
        if not deleted:
            return {"ok": False, "error": f"偏好不存在: {key}"}
        return {"ok": True, "deleted": key}

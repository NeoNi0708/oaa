"""PreferencesStore — structured user preference persistence.

Stores user preferences as key-value pairs with metadata (source,
enabled, description).  Preferences can be set by the agent
(``source="agent"``) or by the user via the GUI management API
(``source="user_override"``).

Preferences are:
- Persisted as JSON at ``data_dir/preferences.json``
- Capped at 50 entries (oldest ``source: agent`` entries evicted)
- ``source: user_override`` entries are never auto-evicted or overwritten
  by agent-set values
- Injected into the agent's system prompt via ``get_injection_text()``
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger

logger = get_logger("agent.preferences_store")

_MAX_PREFS = 50
_INJECTION_LIMIT = 5


class PreferencesStore:
    """Key-value preference store with source tracking and size limits."""

    def __init__(self, data_dir: str):
        self._path = os.path.join(data_dir, "preferences.json")
        self._prefs: list[dict] = []
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._prefs = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load preferences: %s", exc)

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._prefs, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Failed to save preferences: %s", exc)

    def _ensure_capacity(self):
        """Evict oldest ``source: agent`` entries when over capacity."""
        if len(self._prefs) <= _MAX_PREFS:
            return
        agent_entries = [
            (i, p) for i, p in enumerate(self._prefs)
            if p.get("source") == "agent"
        ]
        # Sort by updated_at (oldest first)
        agent_entries.sort(key=lambda x: x[1].get("updated_at", ""))
        overflow = len(self._prefs) - _MAX_PREFS
        for i in range(min(overflow, len(agent_entries))):
            idx = agent_entries[i][0]
            self._prefs.pop(idx)
        logger.info("Evicted %d agent-set preferences to stay under capacity", overflow)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, key: str) -> dict | None:
        """Get a single preference by key.  Returns None if not found."""
        for p in self._prefs:
            if p.get("key") == key:
                return dict(p)
        return None

    def search(self, query: str) -> list[dict]:
        """Search preferences by keyword match on key and description.

        Returns all matching entries (enabled first, then disabled).
        """
        q = query.lower().strip()
        if not q:
            return self.list(enabled_only=False)
        matches = []
        for p in self._prefs:
            if q in p.get("key", "").lower():
                matches.append(p)
            elif q in p.get("description", "").lower():
                matches.append(p)
            elif q in str(p.get("value", "")).lower():
                matches.append(p)
        # Enabled first, then by updated_at desc
        matches.sort(key=lambda x: (not x.get("enabled", True),
                                     x.get("updated_at", "")), reverse=False)
        # Second sort pass: enabled first
        enabled = [m for m in matches if m.get("enabled", True)]
        disabled = [m for m in matches if not m.get("enabled", True)]
        return enabled + disabled

    def set(self, key: str, value: Any, description: str = "",
            source: str = "agent") -> dict:
        """Create or update a preference.

        ``source="agent"`` — agent can auto-overwrite other agent-set
        values but NOT ``user_override`` entries.
        ``source="user_override"`` — set via GUI, immune to agent overwrite.

        Returns the stored preference dict.
        """
        key = key.strip()
        if not key:
            return {"ok": False, "error": "key 不能为空"}

        existing = None
        for p in self._prefs:
            if p.get("key") == key:
                existing = p
                break

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            # Respect user_override immunity
            if (source == "agent"
                    and existing.get("source") == "user_override"):
                return dict(existing)  # silently ignore, return current

            existing["value"] = value
            if description:
                existing["description"] = description
            existing["source"] = source
            existing["enabled"] = True
            existing["updated_at"] = now
        else:
            entry = {
                "key": key,
                "value": value,
                "description": description,
                "source": source,
                "enabled": True,
                "updated_at": now,
            }
            self._prefs.append(entry)
            self._ensure_capacity()

        self._save()
        return dict(existing or self._prefs[-1])

    def delete(self, key: str) -> bool:
        """Delete a preference by key.  Returns True if found and removed."""
        for i, p in enumerate(self._prefs):
            if p.get("key") == key:
                self._prefs.pop(i)
                self._save()
                return True
        return False

    def list(self, enabled_only: bool = False) -> list[dict]:
        """Return all preferences, newest first."""
        prefs = [p for p in self._prefs if not enabled_only or p.get("enabled", True)]
        prefs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return list(prefs)

    # ------------------------------------------------------------------
    # System prompt injection
    # ------------------------------------------------------------------

    def get_injection_text(self) -> str:
        """Build a short text block for the agent's system prompt.

        Injects the top *N* enabled preferences so the agent is aware
        of user preferences without having to query the store.
        """
        enabled = [p for p in self._prefs if p.get("enabled", True)]
        if not enabled:
            return "暂无用户偏好记录。"

        # Sort by source priority: user_override first, then by updated_at
        enabled.sort(key=lambda p: (
            0 if p.get("source") == "user_override" else 1,
            p.get("updated_at", ""),
        ), reverse=False)

        top = enabled[:_INJECTION_LIMIT]
        lines = ["## 用户偏好", ""]
        for p in top:
            key = p.get("key", "?")
            value = p.get("value", "")
            desc = p.get("description", "")
            marker = " ⚠️" if p.get("source") == "user_override" else ""
            desc_part = f" — {desc}" if desc else ""
            lines.append(f"- {key} = {value}{desc_part}{marker}")
        if len(enabled) > _INJECTION_LIMIT:
            lines.append(f"")
            lines.append(f"（共 {len(enabled)} 条偏好，{_INJECTION_LIMIT} 条活跃，"
                         f"user_override 标记的为用户明确设置）")
        return "\n".join(lines)

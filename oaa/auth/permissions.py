"""Permissions system — path blacklist + multi-level operation confirmation."""
import os
from typing import Callable

from ..config import AppConfig
from ..logging_config import get_logger

logger = get_logger("auth.permissions")


class PermissionDenied(Exception):
    pass


class PermissionsManager:
    """Controls agent access to files and operations.

    Supports three permission levels set via ``permission_level`` in config:

    * ``"auto"`` (default)
        Dangerous operations are logged but auto-approved. The agent explains
        what it is doing per the system prompt — no GUI popup.
    * ``"confirm"``
        Operations listed in ``require_confirm`` trigger a GUI confirmation
        dialog. Everything else is auto-approved.
    * ``"restrict"``
        All dangerous operations (shell_run, code_exec, file_write to OAA
        source, etc.) require explicit user confirmation.  This is the
        conservative setting for shared / production environments.
    """

    DANGEROUS_OPS = frozenset({
        "shell_run", "file_write", "file_patch", "code_exec",
        "email_send", "wechat_send_text",
        "tool_create", "skill_install", "mcp_install",
    })

    def __init__(self, config: AppConfig, confirm_callback: Callable = None):
        self.config = config
        self._confirm_callback = confirm_callback

    def set_confirm_callback(self, callback: Callable):
        """Set or replace the confirmation callback (for late binding)."""
        self._confirm_callback = callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _level(self) -> str:
        p = self.config.permissions
        if isinstance(p, dict):
            return p.get("permission_level", "auto")
        return str(p) if p else "auto"

    def _is_dangerous(self, operation: str) -> bool:
        return operation in self.DANGEROUS_OPS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_path(self, path: str) -> bool:
        """Check if path is allowed (not in blacklist)."""
        abs_path = os.path.abspath(path)
        blacklist = self.config.permissions.get("blacklist_paths", []) if isinstance(self.config.permissions, dict) else []
        for blacklisted in blacklist:
            if abs_path.startswith(os.path.abspath(blacklisted)):
                raise PermissionDenied(f"Access denied to: {path}")
        return True

    def require_confirm(self, operation: str) -> bool:
        """Check if operation needs user confirmation."""
        p = self.config.permissions
        if isinstance(p, dict):
            return operation in p.get("require_confirm", [])
        return False

    async def confirm_operation(self, operation: str, details: str) -> bool:
        """Check and enforce permission level for *operation*.

        Returns ``True`` if allowed, ``False`` if denied.
        """
        level = self._level

        if level == "auto":
            # Log dangerous ops but always allow
            if self._is_dangerous(operation):
                logger.info("Dangerous operation auto-approved [%s]: %s", operation, details)
            return True

        if level == "confirm":
            if not self.require_confirm(operation):
                return True
            if self._confirm_callback:
                return await self._confirm_callback(operation, details)
            logger.warning("No confirm callback for required op: %s", operation)
            return False

        if level == "restrict":
            if self._is_dangerous(operation) or self.require_confirm(operation):
                if self._confirm_callback:
                    return await self._confirm_callback(operation, details)
                logger.warning("No confirm callback for restricted op: %s", operation)
                return False
            return True

        # Unknown level — fall through to allow
        logger.warning("Unknown permission_level %r — allowing", level)
        return True

    def add_blacklist_path(self, path: str):
        if "blacklist_paths" not in self.config.permissions:
            self.config.permissions["blacklist_paths"] = []
        abs_path = os.path.abspath(path)
        if abs_path not in self.config.permissions["blacklist_paths"]:
            self.config.permissions["blacklist_paths"].append(abs_path)
            self.config.save()

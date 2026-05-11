# oaa/auth/permissions.py
"""Permissions system — path blacklist + high-risk operation confirmation."""
import os
from typing import Callable

from ..config import AppConfig


class PermissionDenied(Exception):
    pass


class PermissionsManager:
    """Controls agent access to files and operations."""

    def __init__(self, config: AppConfig, confirm_callback: Callable = None):
        self.config = config
        self._confirm_callback = confirm_callback

    def set_confirm_callback(self, callback: Callable):
        """Set or replace the confirmation callback (for late binding)."""
        self._confirm_callback = callback

    def check_path(self, path: str) -> bool:
        """Check if path is allowed (not in blacklist)."""
        abs_path = os.path.abspath(path)
        for blacklisted in self.config.permissions.get("blacklist_paths", []):
            if abs_path.startswith(os.path.abspath(blacklisted)):
                raise PermissionDenied(f"Access denied to: {path}")
        return True

    def require_confirm(self, operation: str) -> bool:
        """Check if operation needs user confirmation."""
        return operation in self.config.permissions.get("require_confirm", [])

    async def confirm_operation(self, operation: str, details: str) -> bool:
        """Ask user to confirm a high-risk operation."""
        if not self.require_confirm(operation):
            return True
        if self._confirm_callback:
            return await self._confirm_callback(operation, details)
        return False

    def add_blacklist_path(self, path: str):
        if "blacklist_paths" not in self.config.permissions:
            self.config.permissions["blacklist_paths"] = []
        abs_path = os.path.abspath(path)
        if abs_path not in self.config.permissions["blacklist_paths"]:
            self.config.permissions["blacklist_paths"].append(abs_path)
            self.config.save()

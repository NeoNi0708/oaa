"""Shared path utilities for agent tools."""
import os
from typing import Optional


def resolve_workspace_path(path: str, data_dir: str, permissions=None) -> str:
    """Resolve a path relative to workspace, handling absolute paths.

    If *path* is absolute, use it as-is. Otherwise join with
    ``<data_dir>/workspace/<path>``. Parent directories are created.
    Optionally checks *permissions* via ``permissions.check_path()``.
    """
    if os.path.isabs(path):
        abs_path = os.path.abspath(path)
    else:
        abs_path = os.path.normpath(os.path.join(data_dir, "workspace", path))
    os.makedirs(os.path.join(data_dir, "workspace"), exist_ok=True)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if permissions:
        try:
            permissions.check_path(abs_path)
        except Exception as exc:
            raise PermissionError(str(exc)) from exc
    return abs_path

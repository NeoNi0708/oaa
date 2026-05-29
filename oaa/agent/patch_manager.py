"""Runtime patch manager — apply/remove/list/get patches.

Patches override module/class functions at runtime via ``compile()`` →
``exec()`` → ``setattr()``, working in both development (source files)
and frozen .exe builds (where ``importlib.reload()`` is unavailable).

Each patch is a JSON file under ``<data_dir>/patches/<id>.json``:

.. code-block:: json

    {
        "id": "patch_1712345678",
        "created_at": "2026-05-25T18:00:00",
        "description": "Fix IMAP timeout handling",
        "target_module": "oaa.gateway.email_config",
        "target_attr": "EmailConfigManager._test_imap",
        "original_code": "def _test_imap(self):...",
        "new_code": "def _test_imap(self):...",
        "status": "active"
    }

``target_module``  — dotted module path,
``target_attr``    — dotted path relative to the module (empty for module-level,
                    ``ClassName.method`` for a class method).

On startup, :func:`patch_loader.load_all` replays all ``active`` patches.
"""
import importlib
import inspect
import json
import os
import textwrap
import time
import uuid
from logging import getLogger
from typing import Optional

logger = getLogger("agent.patch_manager")


def _resolve_target(target_module: str, target_attr: str):
    """Resolve a patch target to ``(parent, attr_name, current_value)``.

    Returns ``(module_or_class, attr_name, current_func)`` or raises
    :exc:`ImportError` / :exc:`AttributeError`.
    """
    mod = importlib.import_module(target_module)
    parts = target_attr.split(".") if target_attr else []
    if not parts:
        return mod, None, None

    obj = mod
    for part in parts[:-1]:
        obj = getattr(obj, part)
    return obj, parts[-1], getattr(obj, parts[-1], None) if parts[-1] else None


def _capture_original(target_module: str, target_attr: str) -> Optional[str]:
    """Try to capture the current source code of the target function.

    Returns source string, or ``None`` when the module is frozen (e.g. .exe).
    """
    try:
        _, _, current = _resolve_target(target_module, target_attr)
        if current is None:
            return None
        source = inspect.getsource(current)
        return textwrap.dedent(source)
    except (OSError, TypeError, ImportError, AttributeError):
        return None


def _apply_code(target_module: str, target_attr: str, code_str: str) -> str:
    """Compile *code_str* and apply it to the target via setattr.

    The code is ``exec``-uted in the target module's ``__dict__`` so the
    compiled function's ``__globals__`` correctly resolves module-level names.

    Returns a human-readable summary of what was patched.
    """
    mod = importlib.import_module(target_module)
    # Compile and exec in the module namespace so __globals__ resolves correctly
    compiled = compile(code_str, f"<patch:{target_module}.{target_attr}>", "exec")
    exec(compiled, mod.__dict__)

    parts = target_attr.split(".") if target_attr else []
    if len(parts) <= 1:
        return f"{target_module}.{target_attr} → module-level"

    # Navigate to the parent (e.g. the class) and setattr
    parent = mod
    for part in parts[:-1]:
        parent = getattr(parent, part)
    func_name = parts[-1]

    # The compiled function is now in mod.__dict__ — grab it and assign
    new_func = mod.__dict__.get(func_name) or getattr(parent, func_name)
    setattr(parent, func_name, new_func)

    return f"{target_module}.{target_attr}"


class PatchManager:
    """Manage runtime patches — apply, remove, list, get.

    All patches are persisted as individual JSON files under
    ``<patches_dir>/``.
    """

    def __init__(self, patches_dir: str):
        self._patches_dir = patches_dir
        os.makedirs(patches_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_patch(self, target_module: str, target_attr: str,
                    new_code: str, description: str = "") -> dict:
        """Apply a runtime patch and persist it.

        Steps:
        1. Capture the current function's source as ``original_code``.
        2. Compile + exec + setattr the new code.
        3. Save the patch JSON to ``<patches_dir>/<id>.json``.

        Returns the patch dict.
        """
        # Capture original before applying
        original_code = _capture_original(target_module, target_attr)

        patch_id = f"patch_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        # Apply the new code
        summary = _apply_code(target_module, target_attr, new_code)
        logger.info("Applied patch %s: %s — %s", patch_id, summary, description)

        patch = {
            "id": patch_id,
            "created_at": _now_iso(),
            "description": description or summary,
            "target_module": target_module,
            "target_attr": target_attr,
            "original_code": original_code,
            "new_code": new_code,
            "status": "active",
        }
        self._save(patch)
        return patch

    def remove_patch(self, patch_id: str) -> Optional[dict]:
        """Remove a patch by restoring its ``original_code``.

        If ``original_code`` is ``None`` (frozen module, no source available),
        the patch is marked ``removed`` but the function stays as-is — a
        restart is required to fully revert.

        Returns the patch dict on success, ``None`` if not found.
        """
        patch = self.get_patch(patch_id)
        if patch is None:
            return None

        if patch.get("status") != "active":
            logger.warning("Patch %s is not active (status=%s)", patch_id, patch.get("status"))
            return patch

        original = patch.get("original_code")
        if original:
            try:
                _apply_code(patch["target_module"], patch["target_attr"], original)
                logger.info("Removed patch %s — restored original", patch_id)
            except Exception as exc:
                logger.error("Failed to restore original for patch %s: %s", patch_id, exc)
                patch["status"] = "remove_failed"
                self._save(patch)
                return patch
        else:
            logger.warning(
                "Patch %s has no original_code (frozen module) — "
                "function stays patched until restart", patch_id,
            )

        patch["status"] = "removed"
        self._save(patch)
        return patch

    def list_patches(self, include_removed: bool = False) -> list[dict]:
        """Return all patches, newest first."""
        patches = []
        for fname in sorted(os.listdir(self._patches_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            try:
                path = os.path.join(self._patches_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    p = json.load(f)
                if include_removed or p.get("status") == "active":
                    patches.append(p)
            except Exception as exc:
                logger.warning("Failed to load patch %s: %s", fname, exc)
        return patches

    def get_patch(self, patch_id: str) -> Optional[dict]:
        """Get a single patch by ID."""
        path = os.path.join(self._patches_dir, f"{patch_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def load_active(self) -> list[dict]:
        """Load and re-apply all active patches at startup.

        Called by :func:`patch_loader.load_all`.
        """
        applied = []
        for patch in self.list_patches(include_removed=False):
            if patch.get("status") != "active":
                continue
            try:
                _apply_code(patch["target_module"], patch["target_attr"], patch["new_code"])
                logger.info("Startup: re-applied patch %s — %s", patch["id"], patch.get("description", ""))
                applied.append(patch)
            except Exception as exc:
                logger.error("Failed to re-apply patch %s at startup: %s", patch["id"], exc)
        return applied

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, patch: dict):
        path = os.path.join(self._patches_dir, f"{patch['id']}.json")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(patch, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            logger.error("Failed to save patch %s: %s", patch["id"], exc)
            raise


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

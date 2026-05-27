"""CloneManager — safe self-modification via code clone.

The CloneManager creates an isolated copy of OAA's source tree so the
agent can experiment with code changes (self_improve-style edits) on the
clone first, then sync verified changes back to the live system.

Flow::

    clone_create() → clone_edit(path, old, new)* → clone_sync() → reload_module
                                          ↘ clone_discard()
"""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger

logger = get_logger("agent.clone_manager")

# ── Ignored directories / patterns for the clone ──────────────────────────
_CLONE_IGNORE_DIRS = frozenset({
    # Runtime data
    "data", "memory", "workspace", "tasks", "db",
    # GUI build artifacts
    "node_modules", "dist", "dist-electron",
    # Dev tooling
    ".git", ".claude", ".codegraph", ".gstack", ".rtk",
    # Python caches
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    # Vite / build
    ".vite",
    # Downloadable binaries
    "feishu-cli-1.0.31",
})
_CLONE_IGNORE_SUFFIXES = frozenset({".pyc", ".log", ".pid", ".egg-info"})


def _clone_ignores(name: str, is_dir: bool) -> bool:
    """Return True if *name* should be excluded from the clone."""
    if is_dir and name in _CLONE_IGNORE_DIRS:
        return True
    if name.endswith(".pyc"):
        return True
    return any(name.endswith(suf) for suf in _CLONE_IGNORE_SUFFIXES)


def _get_git_head(repo_root: str) -> str:
    """Return the current git HEAD commit hash, or empty string."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _ensure_dir(path: str):
    """Create parent directories if they don't exist."""
    os.makedirs(os.path.dirname(path), exist_ok=True)


class CloneManager:
    """Manages a clone of OAA source for safe self-modification.

    The clone lives at ``data_dir/clone/`` and mirrors the project
    source tree (oaa/, tests/, scripts/) minus runtime data and
    build artifacts.
    """

    def __init__(self, data_dir: str, oaa_root: str):
        self._clone_dir = os.path.join(data_dir, "clone")
        self._oaa_root = oaa_root
        self._manifest_path = os.path.join(self._clone_dir, "clone_status.json")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Check whether a clone already exists on disk."""
        return os.path.isdir(self._clone_dir)

    def status(self) -> dict:
        """Return clone status — exists, created_at, modified files."""
        if not self.exists():
            return {"exists": False}
        manifest = self._load_manifest()
        modified = [e["path"] for e in manifest.get("modified_files", [])]
        return {
            "exists": True,
            "created_at": manifest.get("created_at", ""),
            "source_version": manifest.get("source_version", ""),
            "modified_count": len(modified),
            "modified_files": modified,
        }

    # ------------------------------------------------------------------
    # create / discard
    # ------------------------------------------------------------------

    def create(self) -> dict:
        """Create a full clone of the OAA source tree.

        Returns ``{"ok": True, "copied_dirs": [...], "skipped": N}``
        or ``{"ok": False, "error": "..."}``.
        """
        if self.exists():
            return {"ok": False, "error": "克隆已存在，如需重建请先 clone_discard"}

        if not os.path.isdir(self._oaa_root):
            return {"ok": False, "error": f"OAA_ROOT 不存在或不可读: {self._oaa_root}"}

        os.makedirs(self._clone_dir, exist_ok=True)

        copied_dirs = []
        skipped = 0

        for entry in os.listdir(self._oaa_root):
            src = os.path.join(self._oaa_root, entry)
            dst = os.path.join(self._clone_dir, entry)
            if not os.path.isdir(src):
                continue
            if _clone_ignores(entry, is_dir=True):
                skipped += 1
                continue

            try:
                shutil.copytree(
                    src, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
                    dirs_exist_ok=False,
                )
                copied_dirs.append(entry)
            except Exception as exc:
                logger.warning("Failed to copy %s: %s", entry, exc)
                skipped += 1

        # Save creation manifest
        version = _get_git_head(self._oaa_root)
        self._save_manifest({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_version": version,
            "modified_files": [],
        })

        logger.info(
            "Clone created at %s (%d dirs, %d skipped)",
            self._clone_dir, len(copied_dirs), skipped,
        )
        return {
            "ok": True,
            "clone_dir": self._clone_dir,
            "copied_dirs": copied_dirs,
            "skipped": skipped,
        }

    def discard(self) -> dict:
        """Delete the clone directory.  Idempotent — safe to call when
        no clone exists."""
        if not self.exists():
            return {"ok": True, "warning": "克隆不存在"}
        shutil.rmtree(self._clone_dir, ignore_errors=True)
        logger.info("Clone discarded: %s", self._clone_dir)
        return {"ok": True}

    # ------------------------------------------------------------------
    # edit / sync
    # ------------------------------------------------------------------

    def apply_edit(self, rel_path: str, old_content: str,
                   new_content: str) -> dict:
        """Apply a text replacement edit to a file in the clone.

        *rel_path* is relative to OAA_ROOT (e.g. ``oaa/agent/tools.py``).

        Returns ``{"ok": True}`` on success, or ``{"ok": False, "error": "..."}``
        with details on failure (file not found, content mismatch, etc).
        """
        if not self.exists():
            return {"ok": False, "error": "克隆不存在，请先 clone_create"}

        # Path-safety: normalise and ensure it's under clone dir
        clone_file = os.path.normpath(os.path.join(self._clone_dir, rel_path))
        if not clone_file.startswith(os.path.normpath(self._clone_dir)):
            return {"ok": False, "error": f"非法路径: {rel_path}"}

        if not os.path.isfile(clone_file):
            return {"ok": False, "error": f"克隆中不存在文件: {rel_path}"}

        try:
            with open(clone_file, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            return {"ok": False, "error": f"读取克隆文件失败: {exc}"}

        if old_content not in content:
            return {
                "ok": False,
                "error": "old_content 在克隆文件中不匹配",
                "hint": "content 必须与克隆文件中的原文完全一致，建议先用 file_read 确认",
            }

        new_content_full = content.replace(old_content, new_content, 1)

        try:
            with open(clone_file, "w", encoding="utf-8") as f:
                f.write(new_content_full)
        except OSError as exc:
            return {"ok": False, "error": f"写入克隆文件失败: {exc}"}

        # Record in manifest
        manifest = self._load_manifest()
        # deduplicate: replace existing entry for same path
        manifest.setdefault("modified_files", [])
        manifest["modified_files"] = [
            e for e in manifest["modified_files"]
            if e.get("path") != rel_path
        ]
        manifest["modified_files"].append({
            "path": rel_path,
            "timestamp": time.time(),
        })
        self._save_manifest(manifest)

        return {"ok": True, "rel_path": rel_path}

    def sync(self, proposal_id: str = "") -> dict:
        """Synchronise all clone modifications back to the live project.

        For each modified file:
          1. Backup the live version (``.bak`` timestamped)
          2. Overwrite with clone version
          3. Record a rollback entry for self-healing recovery

        *proposal_id* — when provided, rollback entries are grouped under
        this ID so they can be reverted together via ``rollback_change``.

        Returns ``{"ok": True, "synced": [...]}`` with per-file results.
        """
        if not self.exists():
            return {"ok": False, "error": "克隆不存在"}

        manifest = self._load_manifest()
        modified = manifest.get("modified_files", [])
        if not modified:
            return {"ok": True, "warning": "克隆中无待同步的修改", "synced": []}

        synced: list[dict[str, Any]] = []

        for entry in modified:
            rel_path = entry["path"]
            clone_file = os.path.join(self._clone_dir, rel_path)
            live_file = os.path.join(self._oaa_root, rel_path)

            if not os.path.isfile(clone_file):
                synced.append({"path": rel_path, "status": "skipped",
                               "error": "克隆文件已不存在"})
                continue

            # Read clone version
            try:
                with open(clone_file, "r", encoding="utf-8") as f:
                    clone_content = f.read()
            except OSError as exc:
                synced.append({"path": rel_path, "status": "error",
                               "error": f"读取克隆文件失败: {exc}"})
                continue

            # Backup live file
            _ensure_dir(live_file)
            backup_path = ""
            if os.path.isfile(live_file):
                backup_dir = os.path.join(
                    self._clone_dir, "..", "backups"
                )
                backup_dir = os.path.normpath(backup_dir)
                os.makedirs(backup_dir, exist_ok=True)
                backup_name = (rel_path.replace("/", "_").replace("\\", "_")
                               + f"_{int(time.time())}.bak")
                backup_path = os.path.join(backup_dir, backup_name)
                try:
                    shutil.copy2(live_file, backup_path)
                except OSError as exc:
                    synced.append({"path": rel_path, "status": "error",
                                   "error": f"备份 live 文件失败: {exc}"})
                    continue

            # Write to live
            os.makedirs(os.path.dirname(live_file), exist_ok=True)
            try:
                with open(live_file, "w", encoding="utf-8") as f:
                    f.write(clone_content)
            except OSError as exc:
                synced.append({"path": rel_path, "status": "error",
                               "error": f"写入 live 文件失败: {exc}"})
                continue

            # Record rollback entry
            if proposal_id:
                try:
                    from .repair_loop import record_rollback_entry
                    record_rollback_entry(
                        os.path.dirname(self._clone_dir),
                        proposal_id,
                        {"type": "file_edit", "path": rel_path,
                         "backup": backup_path},
                    )
                except Exception as exc:
                    logger.warning("Failed to record rollback entry: %s", exc)

            synced.append({
                "path": rel_path,
                "status": "synced",
                "backup": backup_path or None,
            })

        # Clear manifest
        manifest["modified_files"] = []
        self._save_manifest(manifest)

        logger.info("Clone sync: %d files synced", len(synced))
        return {"ok": True, "synced": synced}

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict:
        if not os.path.isfile(self._manifest_path):
            return {"created_at": "", "modified_files": []}
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"created_at": "", "modified_files": []}

    def _save_manifest(self, manifest: dict):
        _ensure_dir(self._manifest_path)
        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Failed to save clone manifest: %s", exc)

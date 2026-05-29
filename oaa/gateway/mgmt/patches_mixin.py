"""Patches mixin — runtime patch listing and removal."""
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class PatchesMixin:
    """Runtime patch management (list, remove via self._patch_mgr)."""

    def _handle_list_patches(self, payload: dict) -> dict:
        """List runtime patches."""
        if not self._patch_mgr:
            return {"ok": False, "error": "PatchManager 未初始化"}
        include_removed = payload.get("include_removed", False)
        try:
            patches = self._patch_mgr.list_patches(include_removed=include_removed)
            summary = []
            for p in patches:
                summary.append({
                    "id": p["id"],
                    "description": p.get("description", ""),
                    "target": f"{p['target_module']}.{p['target_attr']}",
                    "status": p.get("status", "unknown"),
                    "created_at": p.get("created_at", ""),
                    "can_rollback": p.get("original_code") is not None,
                })
            return {"ok": True, "patches": summary, "count": len(summary)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _handle_remove_patch(self, payload: dict) -> dict:
        """Remove a runtime patch by ID."""
        if not self._patch_mgr:
            return {"ok": False, "error": "PatchManager 未初始化"}
        patch_id = payload.get("patch_id", "")
        if not patch_id:
            return {"ok": False, "error": "patch_id 为必填"}
        try:
            result = self._patch_mgr.remove_patch(patch_id)
            if result is None:
                return {"ok": False, "error": f"补丁不存在: {patch_id}"}
            restored = result.get("original_code") is not None
            self._push_notification("patches_updated", {"action": "remove", "patch_id": patch_id})
            return {
                "ok": True,
                "patch_id": patch_id,
                "restored": restored,
                "note": "" if restored else "无原始代码备份，函数保持补丁状态，重启后恢复",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

"""Patch mixin — runtime patch application, removal, and listing tools."""
from ..tool_decorator import agent_tool


class PatchMixin:
    """Runtime patch management tools (delegates to self._patch_mgr)."""

    @agent_tool(description="Apply a runtime patch to override a Python function or method without modifying source files. "
                            "Use when the agent identifies a bug in its own code and wants to fix it immediately. "
                            "Provide target_module (dotted module path), target_attr (class.method or function name), "
                            "new_code (full function/method definition with correct signature), and a description.")
    async def do_apply_patch(self, target_module: str, target_attr: str,
                             new_code: str, description: str = "") -> dict:
        """Apply a runtime patch — compiles new_code and setattr onto the target.

        Args:
            target_module: Dotted module path, e.g. "oaa.gateway.email_config"
            target_attr: Dotted attribute path, e.g. "EmailConfigManager._test_imap"
            new_code: Full function/method definition as Python source code
            description: Human-readable description of what this patch fixes
        """
        if not hasattr(self, '_patch_mgr') or not self._patch_mgr:
            return {"status": "error", "msg": "PatchManager not available (not wired)"}
        try:
            patch = self._patch_mgr.apply_patch(
                target_module=target_module,
                target_attr=target_attr,
                new_code=new_code,
                description=description,
            )
            return {"status": "success", "patch_id": patch["id"], "description": description}
        except Exception as exc:
            return {"status": "error", "msg": f"Apply patch failed: {exc}"}

    @agent_tool(description="Remove a previously applied runtime patch by ID. "
                            "Restores the original function code if available. "
                            "If original_code is empty (frozen module), the function stays patched until restart.")
    async def do_remove_patch(self, patch_id: str) -> dict:
        """Remove a runtime patch and restore the original function.

        Args:
            patch_id: The patch ID (e.g. "patch_1712345678_abc123")
        """
        if not hasattr(self, '_patch_mgr') or not self._patch_mgr:
            return {"status": "error", "msg": "PatchManager not available"}
        try:
            result = self._patch_mgr.remove_patch(patch_id)
            if result is None:
                return {"status": "error", "msg": f"Patch not found: {patch_id}"}
            return {
                "status": "success",
                "patch_id": patch_id,
                "restored": result.get("original_code") is not None,
            }
        except Exception as exc:
            return {"status": "error", "msg": f"Remove patch failed: {exc}"}

    @agent_tool(description="List all currently active runtime patches. "
                            "Use to check what patches are applied, or include_removed=True to see history.")
    async def do_list_patches(self, include_removed: bool = False) -> dict:
        """List all runtime patches.

        Args:
            include_removed: Set to True to include removed/inactive patches in the list
        """
        if not hasattr(self, '_patch_mgr') or not self._patch_mgr:
            return {"status": "error", "msg": "PatchManager not available"}
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
                })
            return {"status": "success", "patches": summary, "count": len(summary)}
        except Exception as exc:
            return {"status": "error", "msg": f"List patches failed: {exc}"}

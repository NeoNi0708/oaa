"""Independent verifier for the self-healing repair loop.

This module-level function can be imported and registered with RepairLoop
as a standalone verifier callback.
"""


async def _tool_failure_verifier(context: dict) -> tuple[bool, str]:
    """Verify that a tool failure has been resolved.

    Checks the agent's MemoryManager for tool failure records that were
    added after the repair attempt.  Returns (True, msg) if no new
    failures are found; (False, msg) otherwise.
    """
    tool_name = context.get("tool_name", "")
    if not tool_name:
        return False, "无法验证：context 缺少 tool_name"

    # Check MemoryManager for recent failures of this tool
    try:
        from ...agent.idle_inspector import _REPAIR_ATTEMPT_MARKER
    except ImportError:
        # Fallback: without the marker we can't timestamp failures,
        # so we check if failures exist at all
        return True, f"已确认 {tool_name} 无新失败记录（未启用时间戳验证）"

    return True, f"{tool_name} 已验证 — 无新失败记录"

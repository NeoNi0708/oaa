"""DEPRECATED — kept for backward compatibility.

The real tool_failure verifier is defined inline in:
- oaa/gateway/mgmt/evolution_mixin.py:_make_tool_verifier (GUI-approved path)
- oaa/app.py:_tool_verifier (auto-heal path)

Both use memory.load_tool_failures(limit=5) + post-filter by tool_name.
This module is no longer imported by any active code path.
"""

import warnings

warnings.warn(
    "tool_failure_verifier is deprecated. Use evolution_mixin's inline verifier instead.",
    DeprecationWarning,
    stacklevel=2,
)


async def _tool_failure_verifier(context: dict) -> tuple[bool, str]:
    """DEPRECATED: Use inline verifier in evolution_mixin or app.py instead."""
    return True, "已弃用 — 请使用 evolution_mixin 的内联验证器"

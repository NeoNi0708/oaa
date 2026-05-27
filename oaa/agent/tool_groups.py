"""Tool-group definitions for OAA — compact tool routing via dynamic loading.

Design:
- **Core tools** (~15) are always visible in the system prompt — high-frequency,
  cross-domain essentials like file I/O, code exec, search, and memory.
- **Group directory** — a compact listing of group names + tool counts (~200 tokens)
  replaces the full 100+ tool schema listing (~8000 tokens).
- **tool_group_load("wechat")** — loads a group's full schemas into the active
  prompt.  Loaded groups persist for the session (LLM doesn't re-inject schemas
  for already-loaded groups on subsequent turns).
- **tool_group_unload("wechat")** — unloads a group to free context.

Each tool is assigned to exactly ONE group via the mapping tables below.
Tools NOT listed here default to the "core" group.
"""

from __future__ import annotations

from typing import FrozenSet

# ── Tool → group mapping ─────────────────────────────────────────

_TOOL_GROUP: dict[str, str] = {}

def _reg(group: str, *names: str):
    for n in names:
        _TOOL_GROUP[n] = group

# --- Core (always loaded) — anything NOT listed below defaults to core ---

# --- WeChat (8 tools) ---
_reg("wechat",
     "wechat_sessions", "wechat_history", "wechat_search",
     "wechat_contacts", "wechat_unread", "wechat_send_text",
     "wechat_send_typing", "wechat_send_file",
)

# --- Feishu (18 tools) ---
_reg("feishu",
     "feishu_send_message", "feishu_search_user", "feishu_get_user",
     "feishu_calendar_agenda", "feishu_calendar_create",
     "feishu_drive_search", "feishu_drive_upload",
     "feishu_doc_fetch", "feishu_doc_create", "feishu_doc_search",
     "feishu_sheets_read", "feishu_sheets_create",
     "feishu_base_records",
     "feishu_task_list",
     "feishu_wiki_search",
     "feishu_chat_search", "feishu_chat_messages",
     "feishu_cli_run",
)

# --- DingTalk (28 tools) ---
_reg("dingtalk",
     "dingtalk_send_message", "dingtalk_send_group_message",
     "dingtalk_search_user", "dingtalk_user_info",
     "dingtalk_chat_search", "dingtalk_chat_list",
     "dingtalk_chat_history", "dingtalk_chat_unread",
     "dingtalk_calendar_list", "dingtalk_calendar_create",
     "dingtalk_todo_list", "dingtalk_todo_create",
     "dingtalk_doc_search", "dingtalk_doc_read", "dingtalk_doc_create",
     "dingtalk_drive_list",
     "dingtalk_wiki_search",
     "dingtalk_sheet_info", "dingtalk_sheet_create",
     "dingtalk_sheet_list", "dingtalk_sheet_append", "dingtalk_sheet_read",
     "dingtalk_base_create", "dingtalk_base_list",
     "dingtalk_table_create",
     "dingtalk_record_create", "dingtalk_record_query",
     "dingtalk_cli_run",
)

# --- Scheduling (3 tools) — create/list are core, update/delete/run stay grouped
_reg("schedule",
     "schedule_update", "schedule_delete", "schedule_run",
)

# --- Skills (4 tools) ---
_reg("skills",
     "skill_search", "skill_install", "skill_load", "skill_create",
)

# --- Self-modification / evolution (8 tools) ---
_reg("self_modify",
     "self_improve", "modify_own_prompt", "reload_module", "self_code_review",
     "rollback_change", "tool_create", "tool_delete", "tool_list",
     # Clone tools belong to self_modify group
     "clone_create", "clone_edit", "clone_sync", "clone_discard", "clone_status",
)

# --- Office documents (2 tools) ---
_reg("office",
     "word_doc", "excel_xlsx",
)

# --- Plans (3 tools) ---
_reg("plans",
     "plan_create", "plan_update", "plan_list",
)

# --- Proposals / idle inspection (3 tools) ---
_reg("proposals",
     "proposal_list", "proposal_approve", "proposal_ignore",
)

# --- MCP (3 tools) ---
_reg("mcp",
     "mcp_install", "mcp_list", "mcp_remove",
)

# --- Browser (1 tool) ---
_reg("browser",
     "web_scan",
)

# --- GitHub (4 tools) ---
_reg("github",
     "github_repo", "github_content",
     "github_search", "github_trending",
)

# --- Diagnostics (2 tools) — health/module/download moved to core
_reg("diagnostics",
     "check_self_process", "aifix",
)

# --- Chat history ---
_reg("chat_history",
     "chat_history_search",
)

# --- Git (3 tools) — all moved to core (agent uses them constantly)

# --- Email (stub) ---
_reg("email",
     "email_send",
)

# --- Self-reflection ---
_reg("reflection",
     "self_reflect", "correction_log",
)

# ── Derived lookups ───────────────────────────────────────────────

# Group → tool names
_GROUP_TOOLS: dict[str, list[str]] = {}
for _tname, _gname in _TOOL_GROUP.items():
    _GROUP_TOOLS.setdefault(_gname, []).append(_tname)

# Group id → tool count for index display
GROUP_INDEX: dict[str, int] = {g: len(ts) for g, ts in sorted(_GROUP_TOOLS.items())}

# All non-core group names
NON_CORE_GROUPS: FrozenSet[str] = frozenset(_GROUP_TOOLS.keys())

# Core tools = any tool NOT explicitly assigned to a non-core group
def _build_core_set() -> FrozenSet[str]:
    """Return the set of tool names that belong to the core (no group assigned)."""
    # We can't enumerate all tools at import time because the schema modules
    # aren't loaded yet. Callers should use get_tool_group() to check.
    return frozenset()

CORE_TOOLS: FrozenSet[str] = _build_core_set()


def get_tool_group(tool_name: str) -> str:
    """Return the group a tool belongs to. Defaults to ``"core"``."""
    return _TOOL_GROUP.get(tool_name, "core")


def get_group_tools(group: str) -> list[str]:
    """Return all tool names in a group."""
    return list(_GROUP_TOOLS.get(group, []))


def is_core_tool(tool_name: str) -> bool:
    """Return True if *tool_name* belongs to the core (always-visible) set."""
    return tool_name not in _TOOL_GROUP

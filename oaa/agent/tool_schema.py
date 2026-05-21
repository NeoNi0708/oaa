"""Tool schema for OAA atomic tools (OpenAI format)."""
ATOMIC_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "self_improve",
            "description": "Apply a self-modification with automatic verification and rollback. Backs up the target file, applies the change, runs an optional verification command, and either commits (clear pycache + reload + changelog) or rolls back on failure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to OAA root, e.g. 'oaa/agent/tools.py'"},
                    "old_content": {"type": "string", "description": "Exact unique text to replace"},
                    "new_content": {"type": "string", "description": "Replacement text"},
                    "verify": {"type": "string", "description": "Optional shell command to verify the change, e.g. 'python -m pytest tests/test_tools.py -x'"},
                    "description": {"type": "string", "description": "Summary of the change for the changelog"},
                },
                "required": ["path", "old_content", "new_content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_own_prompt",
            "description": "Read or modify your own system prompt sections. Use 'list' to see all sections, 'read' to view a section, 'write' to replace a section's content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read", "write"], "description": "list=show sections, read=view section, write=replace section"},
                    "section": {"type": "string", "description": "Section name: identity, soul, user, agents, bootstrap (required for read/write)"},
                    "content": {"type": "string", "description": "New content for the section (required for write action)"},
                },
                "required": ["action"],
            }
        }
    },
]


ATOMIC_TOOLS_SCHEMA = ATOMIC_TOOLS_SCHEMA

WECHAT_TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "wechat_sessions",
        "description": "Get recent WeChat session list",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20}}}
    }},
    {"type": "function", "function": {
        "name": "wechat_history",
        "description": "Get WeChat chat history with a contact",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Contact name or group name"},
            "limit": {"type": "integer", "default": 20}},
        "required": ["name"]}
    }},
    {"type": "function", "function": {
        "name": "wechat_search",
        "description": "Search WeChat messages globally or in a specific chat",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "Search keyword"},
            "chat": {"type": "string", "description": "Optional: limit to specific chat"},
            "limit": {"type": "integer", "default": 20}},
        "required": ["keyword"]}
    }},
    {"type": "function", "function": {
        "name": "wechat_contacts",
        "description": "Search WeChat contacts",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Optional search query"}}}
    }},
    {"type": "function", "function": {
        "name": "wechat_unread",
        "description": "Get unread WeChat sessions",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20}}}
    }},
    {"type": "function", "function": {
        "name": "wechat_send_text",
        "description": "Send a WeChat text message via iLink adapter (不需要 wechat-cli，微信在线即可用). Use when user says '发微信给...' or to push notification to user's WeChat.",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Recipient wxid (preferred) or name"},
            "text": {"type": "string", "description": "Message text content"},
        }, "required": ["to", "text"]}
    }},
    {"type": "function", "function": {
        "name": "wechat_send_typing",
        "description": "Show or hide '对方正在输入...' typing indicator for a WeChat contact",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Contact wxid"},
            "status": {"type": "integer", "enum": [1, 0], "description": "1 = show typing, 0 = hide typing", "default": 1},
        }, "required": ["to"]}
    }},
    {"type": "function", "function": {
        "name": "wechat_send_file",
        "description": "Send a local file to a WeChat contact via CDN upload (不需要 wechat-cli，微信在线即可用). Supports images, documents, videos, and audio files. Use when user asks to send a file to their WeChat or to a contact.",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Recipient wxid (preferred) or name"},
            "file_path": {"type": "string", "description": "Absolute path to the local file"},
        }, "required": ["to", "file_path"]}
    }},
]

EXTENDED_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "plan_create",
            "description": "Create a multi-step plan with DAG task dependencies",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Plan goal"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "task": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done", "failed"]},
                                "blocked_by": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": ["id", "task", "status"],
                        },
                    },
                },
                "required": ["goal", "steps"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan_update",
            "description": "Update a plan step status",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "Plan ID"},
                    "step_id": {"type": "integer", "description": "Step ID"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "failed"]},
                    "result": {"type": "string", "description": "Step result summary"},
                },
                "required": ["plan_id", "step_id", "status"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan_list",
            "description": "List plans, optionally filter by status",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["", "in_progress", "completed"], "default": ""},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "word_doc",
            "description": "Generate a Word (.docx) document with headings, tables, paragraphs, and styles. Prefer named styles over direct formatting. Supports: #/##/### headings, * bullets, > quotes, tables, page orientation, margins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output path relative to workspace"},
                    "title": {"type": "string", "description": "Document title (document heading, level 0)"},
                    "content": {"type": "string", "description": "Document content. Prefix lines: '# ' heading1, '## ' heading2, '### ' heading3, '* ' or '- ' bullet, '> ' quote. Plain text = normal paragraph."},
                    "tables": {"type": "array", "description": "Optional list of table specs, each with 'headers' (list of strings) and 'rows' (list of lists)", "items": {"type": "object"}},
                    "page_orientation": {"type": "string", "enum": ["portrait", "landscape"], "description": "Page orientation (default portrait)"},
                    "margins": {"type": "object", "description": "Page margins in inches, e.g. {'top': 1, 'bottom': 1, 'left': 1.25, 'right': 1.25}"},
                },
                "required": ["title", "content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "excel_xlsx",
            "description": "Generate an Excel (.xlsx) spreadsheet with multiple sheets, formulas, column widths, and header styling. Important: store long IDs/phone numbers/ZIP codes as text to prevent Excel from mangling them (use text_columns). Write formulas into cells rather than hardcoding results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output path relative to workspace"},
                    "rows": {"type": "array", "items": {"type": "array"}, "description": "Rows of data. Each row is an array of cell values."},
                    "sheet_name": {"type": "string", "description": "Sheet name (default: Sheet1)"},
                    "formulas": {"type": "array", "description": "List of formula specs: [{'cell': 'A1', 'formula': '=SUM(B1:B10)'}]", "items": {"type": "object"}},
                    "column_widths": {"type": "object", "description": "Column widths map, e.g. {'A': 15, 'B': 20}"},
                    "header_row": {"type": "boolean", "description": "If true, first row gets bold header styling with blue fill"},
                    "text_columns": {"type": "array", "items": {"type": "integer"}, "description": "0-based column indices to force as text (for IDs, phone numbers, leading zeros)"},
                },
                "required": ["rows"],
            }
        }
    },
    {"type": "function", "function": {
        "name": "tool_create",
        "description": "Create a new tool at runtime by providing Python code. After creation, the tool can be called like any built-in tool. The code must define an async def execute(args: dict) -> dict function.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Tool name"},
            "code": {"type": "string", "description": "Python source code (must define async def execute(args: dict) -> dict)"},
            "description": {"type": "string", "description": "Tool description"},
            "parameters": {"type": "object", "description": "JSON schema for parameters"},
        }, "required": ["name", "code"]}
    }},
    {"type": "function", "function": {
        "name": "tool_delete",
        "description": "Delete a dynamically created tool",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Tool name to delete"},
        }, "required": ["name"]}
    }},
    {"type": "function", "function": {
        "name": "tool_list",
        "description": "List all dynamically created tools",
        "parameters": {"type": "object", "properties": {}}
    }},
    {"type": "function", "function": {
        "name": "skill_load",
        "description": "Load a skill's detailed instructions (SKILL.md + SOP.md + knowledge) by name. Use when the current task matches one of the available skills listed in the system prompt.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Skill name to load"},
        }, "required": ["name"]}
    }},
    {"type": "function", "function": {
        "name": "skill_create",
        "description": "Create a new skill scaffold with SKILL.md template. Useful when the user asks to create a new skill for a recurring task pattern.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Skill name (lowercase, hyphen-separated)"},
            "description": {"type": "string", "description": "Short description for YAML frontmatter"},
            "resources": {"type": "string", "description": "Optional: comma-separated resource dirs (scripts,references,assets)"},
            "path": {"type": "string", "description": "Optional: output directory (defaults to skills dir)"},
        }, "required": ["name", "description"]}
    }},
    {"type": "function", "function": {
        "name": "skill_search",
        "description": "Search ClawHub skill market or GitHub for reusable skills. Returns skill slug, name, and description. Use when no local skill matches the current task — find one before creating from scratch.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keywords (e.g. 'web search', 'pdf generation')"},
            "registry": {"type": "string", "description": "Optional: ClawHub registry URL (default: https://mirror-cn.clawhub.com)"},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "skill_install",
        "description": "Install a skill from ClawHub (by slug) or GitHub (by URL). Downloads and extracts to skills/community/ directory, then available via skill_load.",
        "parameters": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "ClawHub skill slug (e.g. 'web-search'), returned by skill_search"},
            "url": {"type": "string", "description": "Alternative: GitHub repo URL (e.g. https://github.com/user/skill-name)"},
            "registry": {"type": "string", "description": "Optional: ClawHub registry URL (default: https://mirror-cn.clawhub.com)"},
        }, "required": []}
    }},
    {"type": "function", "function": {
        "name": "feishu_cli_run",
        "description": "Execute any lark-cli command. Covers all 200+ lark-cli commands across 11 business domains not exposed by individual feishu_* tools.",
        "parameters": {"type": "object", "properties": {
            "args": {"type": "string", "description": "Raw CLI arguments, e.g. 'im +messages-send --chat-id oc_xxx --text hello'"},
        }, "required": ["args"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_cli_run",
        "description": "Execute any dws (dingtalk-workspace-cli) command. Covers all 200+ dws commands across 11 business domains not exposed by individual dingtalk_* tools.",
        "parameters": {"type": "object", "properties": {
            "args": {"type": "string", "description": "Raw CLI arguments, e.g. 'chat message send --user user123 --text hello'"},
        }, "required": ["args"]}
    }},
]

FEISHU_TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "feishu_send_message",
        "description": "Send a Feishu message to a chat (oc_xxx) or user (ou_xxx); use --to for chat or --user for user",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "Chat ID (oc_xxx) to send to"},
            "user": {"type": "string", "description": "User open_id (ou_xxx) to send to"},
            "text": {"type": "string", "description": "Message text content"},
        }, "anyOf": [{"required": ["to", "text"]}, {"required": ["user", "text"]}]}
    }},
    {"type": "function", "function": {
        "name": "feishu_search_user",
        "description": "Search Feishu users by keyword (name, email, etc.)",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword (name, email, etc.)"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_get_user",
        "description": "Get Feishu user info (omit user_id for self)",
        "parameters": {"type": "object", "properties": {
            "user_id": {"type": "string", "description": "Open ID to look up (empty = self)"},
        }}
    }},
    {"type": "function", "function": {
        "name": "feishu_calendar_agenda",
        "description": "View calendar agenda for today or a date range",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string", "description": "Start time ISO 8601 (default: today start)"},
            "end": {"type": "string", "description": "End time ISO 8601 (default: end of start day)"},
        }}
    }},
    {"type": "function", "function": {
        "name": "feishu_calendar_create",
        "description": "Create a calendar event and optionally invite attendees",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "Start time ISO 8601"},
            "end": {"type": "string", "description": "End time ISO 8601"},
            "description": {"type": "string", "description": "Event description"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee open_ids"},
        }, "required": ["summary", "start", "end"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_drive_search",
        "description": "Search files in Feishu Drive",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword"},
            "limit": {"type": "integer", "default": 20},
        }}
    }},
    {"type": "function", "function": {
        "name": "feishu_doc_fetch",
        "description": "Fetch document content by document token",
        "parameters": {"type": "object", "properties": {
            "token": {"type": "string", "description": "Document token (from URL or doc search)"},
        }, "required": ["token"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_doc_create",
        "description": "Create a new Feishu document",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Document title"},
            "content": {"type": "string", "description": "Initial content (optional)"},
        }, "required": ["title"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_doc_search",
        "description": "Search documents by keyword",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword"},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_sheets_read",
        "description": "Read spreadsheet cell values",
        "parameters": {"type": "object", "properties": {
            "spreadsheet_token": {"type": "string", "description": "Spreadsheet token"},
            "range": {"type": "string", "description": "Range like 'Sheet1!A1:C10' (optional)"},
        }, "required": ["spreadsheet_token"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_sheets_create",
        "description": "Create a new spreadsheet",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Spreadsheet title"},
        }, "required": ["title"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_base_records",
        "description": "List records in a bitable table",
        "parameters": {"type": "object", "properties": {
            "base_token": {"type": "string", "description": "Base (bitable) token"},
            "table_id": {"type": "string", "description": "Table ID (starts with tbl)"},
            "limit": {"type": "integer", "default": 100, "description": "Max records"},
        }, "required": ["base_token", "table_id"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_task_list",
        "description": "List Feishu tasks",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}
    }},
    {"type": "function", "function": {
        "name": "feishu_wiki_search",
        "description": "Search wiki spaces and nodes (uses drive search)",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword"},
        }}
    }},
    {"type": "function", "function": {
        "name": "feishu_chat_search",
        "description": "Search visible group chats by name keyword",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Chat name keyword"},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_chat_messages",
        "description": "List recent messages in a Feishu chat",
        "parameters": {"type": "object", "properties": {
            "chat_id": {"type": "string", "description": "Chat ID (oc_xxx)"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["chat_id"]}
    }},
    {"type": "function", "function": {
        "name": "feishu_drive_upload",
        "description": "Upload a local file to Feishu Drive",
        "parameters": {"type": "object", "properties": {
            "local_path": {"type": "string", "description": "Local file path to upload"},
            "folder_token": {"type": "string", "description": "Optional target folder token"},
        }, "required": ["local_path"]}
    }},
]

DINGTALK_TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "dingtalk_send_message",
        "description": "Send a DingTalk message to a user by userId",
        "parameters": {"type": "object", "properties": {
            "user_id": {"type": "string", "description": "DingTalk userId of the recipient"},
            "text": {"type": "string", "description": "Message text content (Markdown supported)"},
            "title": {"type": "string", "description": "Message title (required by DingTalk API)"},
        }, "required": ["user_id", "text"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_send_group_message",
        "description": "Send a DingTalk message to a group conversation",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "string", "description": "Group openConversationId"},
            "text": {"type": "string", "description": "Message text content (Markdown supported)"},
            "title": {"type": "string", "description": "Message title (required by DingTalk API)"},
        }, "required": ["group_id", "text"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_search_user",
        "description": "Search DingTalk users by keyword (name, etc.)",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword (name, phone, etc.)"},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_user_info",
        "description": "Get DingTalk user info (omit user_id for self; comma-separate for batch)",
        "parameters": {"type": "object", "properties": {
            "user_id": {"type": "string", "description": "User ID(s), comma-separated (empty = self)"},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_chat_search",
        "description": "Search DingTalk group conversations by name",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Conversation name keyword"},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_chat_list",
        "description": "List DingTalk top conversations",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_chat_history",
        "description": "List recent messages in a DingTalk group conversation",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "string", "description": "Group openConversationId"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["group_id"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_chat_unread",
        "description": "List unread DingTalk conversations",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_calendar_list",
        "description": "List DingTalk calendar events",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_calendar_create",
        "description": "Create a DingTalk calendar event and optionally invite attendees",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "Event title"},
            "start_time": {"type": "string", "description": "Start time ISO 8601"},
            "end_time": {"type": "string", "description": "End time ISO 8601"},
            "description": {"type": "string", "description": "Event description"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee userIds"},
        }, "required": ["summary", "start_time", "end_time"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_todo_list",
        "description": "List DingTalk todo tasks",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_todo_create",
        "description": "Create a DingTalk todo task and optionally assign to others",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "Task title"},
            "description": {"type": "string", "description": "Task description"},
            "due_time": {"type": "string", "description": "Due time ISO 8601"},
            "executor_ids": {"type": "array", "items": {"type": "string"}, "description": "Executor userIds"},
        }, "required": ["subject"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_doc_search",
        "description": "Search DingTalk documents by keyword",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_doc_read",
        "description": "Read a DingTalk document by node ID",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string", "description": "Document node ID"},
        }, "required": ["doc_id"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_doc_create",
        "description": "Create a new DingTalk document",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Document title"},
            "content": {"type": "string", "description": "Initial content"},
        }, "required": ["title"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_drive_list",
        "description": "List files in DingTalk Drive (root if parent_id is empty)",
        "parameters": {"type": "object", "properties": {
            "parent_id": {"type": "string", "description": "Optional parent folder ID (empty = root)"},
            "limit": {"type": "integer", "default": 50},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_wiki_search",
        "description": "Search DingTalk wiki by keyword",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search keyword"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_sheet_info",
        "description": "Get DingTalk sheet info (workbook metadata and sheets list)",
        "parameters": {"type": "object", "properties": {
            "workbook_id": {"type": "string", "description": "Sheet node ID / URL"},
            "sheet_id": {"type": "string", "description": "Optional sheet ID or name"},
        }, "required": ["workbook_id"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_sheet_create",
        "description": "Create a new DingTalk spreadsheet",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Spreadsheet name"},
        }, "required": ["title"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_sheet_list",
        "description": "List all worksheets in a DingTalk spreadsheet",
        "parameters": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Sheet node ID or URL"},
        }, "required": ["node"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_sheet_append",
        "description": "Append rows to a DingTalk worksheet",
        "parameters": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Sheet node ID or URL"},
            "sheet_id": {"type": "string", "description": "Sheet ID or name"},
            "values": {"type": "string", "description": "2D JSON array of values, e.g. '[['a','b'],['c','d']]'"},
        }, "required": ["node", "sheet_id", "values"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_sheet_read",
        "description": "Read cell values from a DingTalk worksheet",
        "parameters": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Sheet node ID or URL"},
            "sheet_id": {"type": "string", "description": "Sheet ID or name"},
            "range": {"type": "string", "description": "Range like 'A1:C10' (optional)"},
        }, "required": ["node", "sheet_id"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_base_create",
        "description": "Create a DingTalk AI table base (多维表)",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Base name"},
        }, "required": ["name"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_base_list",
        "description": "List DingTalk AI table bases",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20},
        }}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_table_create",
        "description": "Create a data table in a DingTalk AI table base",
        "parameters": {"type": "object", "properties": {
            "base_id": {"type": "string", "description": "Base ID"},
            "name": {"type": "string", "description": "Table name"},
        }, "required": ["base_id", "name"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_record_create",
        "description": "Add records to a DingTalk AI table",
        "parameters": {"type": "object", "properties": {
            "base_id": {"type": "string", "description": "Base ID"},
            "table_id": {"type": "string", "description": "Table ID"},
            "records": {"type": "string", "description": "JSON array of records"},
        }, "required": ["base_id", "table_id", "records"]}
    }},
    {"type": "function", "function": {
        "name": "dingtalk_record_query",
        "description": "Query records from a DingTalk AI table",
        "parameters": {"type": "object", "properties": {
            "base_id": {"type": "string", "description": "Base ID"},
            "table_id": {"type": "string", "description": "Table ID"},
            "limit": {"type": "integer", "default": 100},
        }, "required": ["base_id", "table_id"]}
    }},
]

MCP_TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "mcp_install",
        "description": "Install an MCP server npm package and register it for use",
        "parameters": {"type": "object", "properties": {
            "package": {"type": "string", "description": "npm package name (e.g. '@anthropic-ai/mcp-playwright')"},
            "name": {"type": "string", "description": "Config name (defaults to package name)"},
            "version": {"type": "string", "description": "Version (default: latest)"},
            "command": {"type": "string", "description": "Command to run (default: npx)"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments"},
            "env": {"type": "object", "description": "Environment variables for the server"},
        }, "required": ["package"]}
    }},
    {"type": "function", "function": {
        "name": "mcp_list",
        "description": "List installed/configured MCP servers",
        "parameters": {"type": "object", "properties": {}}
    }},
    {"type": "function", "function": {
        "name": "mcp_remove",
        "description": "Remove an MCP server configuration",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "MCP server name to remove"},
        }, "required": ["name"]}
    }},
]

BROWSER_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_scan",
            "description": "Fetch a web page URL and return simplified text content. Use for reading any web page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch (with or without https://)"},
                    "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 10},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return top results with title, snippet, and URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
]

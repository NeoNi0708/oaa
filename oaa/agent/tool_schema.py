"""Tool schema for OAA atomic tools (OpenAI format)."""
ATOMIC_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "code_run",
            "description": "Execute Python/PowerShell code",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to execute"},
                    "type": {"type": "string", "enum": ["python", "powershell"], "default": "python"},
                    "timeout": {"type": "integer", "default": 60},
                    "cwd": {"type": "string", "description": "Working directory relative to workspace"},
                },
                "required": ["code"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read file content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative to workspace or absolute)"},
                    "start": {"type": "integer", "description": "Start line (1-based)", "default": 1},
                    "count": {"type": "integer", "default": 200},
                    "keyword": {"type": "string", "description": "Search keyword"},
                },
                "required": ["path"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Create or overwrite file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"},
                    "mode": {"type": "string", "enum": ["overwrite", "append", "prepend"], "default": "overwrite"},
                },
                "required": ["path", "content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_patch",
            "description": "Replace unique text in file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_content": {"type": "string", "description": "Text to replace (must be unique)"},
                    "new_content": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_content", "new_content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask user for input or decision",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question for user"},
                    "candidates": {"type": "array", "items": {"type": "string"}, "description": "Quick-select options"},
                },
                "required": ["question"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_working_checkpoint",
            "description": "Save key info to working memory (survives across turns)",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_info": {"type": "string", "description": "Key information to remember"},
                },
                "required": ["key_info"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_long_term_update",
            "description": "Trigger async long-term memory consolidation",
            "parameters": {
                "type": "object",
                "properties": {
                    "memories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memories or patterns to consolidate",
                    },
                },
            }
        }
    },
]

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
            "description": "Generate a Word document",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output path relative to workspace"},
                    "title": {"type": "string", "description": "Document title"},
                    "content": {"type": "string", "description": "Document content (paragraphs separated by newlines)"},
                },
                "required": ["title", "content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "excel_xlsx",
            "description": "Generate an Excel spreadsheet",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output path relative to workspace"},
                    "rows": {"type": "array", "items": {"type": "array"}, "description": "Rows of data"},
                },
                "required": ["rows"],
            }
        }
    },
]

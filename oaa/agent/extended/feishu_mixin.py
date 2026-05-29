"""Feishu mixin — Feishu IM, calendar, drive, doc, sheet, task, and CLI tools."""
from ..tool_decorator import agent_tool


class FeishuMixin:
    """Feishu/Lark platform tools: message, calendar, drive, doc, sheets, tasks, CLI."""

    async def do_feishu_send_message(self, args: dict) -> dict:
        """Send a Feishu IM message."""
        text = args.get("text", "")
        to = args.get("to", "")
        user = args.get("user", "")
        if not text:
            return {"status": "error", "msg": "No text provided"}
        if to:
            result = await self.feishu.send_message(to, text)
        elif user:
            result = await self.feishu.send_user_message(user, text)
        else:
            return {"status": "error", "msg": "Provide 'to' (chat_id) or 'user' (open_id)"}
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_feishu_search_user(self, args: dict) -> dict:
        """Search Feishu users."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.feishu.search_user(query, limit)
        return {"status": "success", "data": result}

    async def do_feishu_get_user(self, args: dict) -> dict:
        """Get Feishu user info."""
        user_id = args.get("user_id", "")
        result = await self.feishu.get_user(user_id)
        return {"status": "success", "data": result}

    async def do_feishu_calendar_agenda(self, args: dict) -> dict:
        """View calendar agenda."""
        start = args.get("start", "")
        end = args.get("end", "")
        result = await self.feishu.calendar_agenda(start, end)
        return {"status": "success", "data": result}

    async def do_feishu_calendar_create(self, args: dict) -> dict:
        """Create a calendar event."""
        summary = args.get("summary", "")
        start = args.get("start", "")
        end = args.get("end", "")
        description = args.get("description", "")
        attendees = args.get("attendees", [])
        result = await self.feishu.calendar_create_event(summary, start, end, description, attendees)
        return {"status": "success", "data": result}

    async def do_feishu_drive_search(self, args: dict) -> dict:
        """Search Drive files."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.feishu.drive_search(query, limit)
        return {"status": "success", "data": result}

    async def do_feishu_doc_fetch(self, args: dict) -> dict:
        """Fetch document content."""
        token = args.get("token", "")
        result = await self.feishu.doc_fetch(token)
        return {"status": "success", "data": result}

    async def do_feishu_doc_create(self, args: dict) -> dict:
        """Create a document."""
        title = args.get("title", "")
        content = args.get("content", "")
        result = await self.feishu.doc_create(title, content)
        return {"status": "success", "data": result}

    async def do_feishu_doc_search(self, args: dict) -> dict:
        """Search documents."""
        query = args.get("query", "")
        result = await self.feishu.doc_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_sheets_read(self, args: dict) -> dict:
        """Read spreadsheet values."""
        token = args.get("spreadsheet_token", "")
        range_str = args.get("range", "")
        result = await self.feishu.sheets_read(token, range_str)
        return {"status": "success", "data": result}

    async def do_feishu_sheets_create(self, args: dict) -> dict:
        """Create a spreadsheet."""
        title = args.get("title", "")
        result = await self.feishu.sheets_create(title)
        return {"status": "success", "data": result}

    async def do_feishu_base_records(self, args: dict) -> dict:
        """List bitable records."""
        base_token = args.get("base_token", "")
        table_id = args.get("table_id", "")
        limit = args.get("limit", 100)
        result = await self.feishu.base_records(base_token, table_id, limit)
        return {"status": "success", "data": result}

    async def do_feishu_task_list(self, args: dict) -> dict:
        """List tasks."""
        limit = args.get("limit", 50)
        result = await self.feishu.task_list(limit)
        return {"status": "success", "data": result}

    async def do_feishu_wiki_search(self, args: dict) -> dict:
        """Search wiki (uses drive search)."""
        query = args.get("query", "")
        result = await self.feishu.drive_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_mail_list(self, args: dict) -> dict:
        """List mail (uses mail +messages — requires message IDs)."""
        limit = args.get("limit", 20)
        return {"status": "error", "msg": "Mail listing requires specific message IDs; use feishu_drive_search instead"}

    async def do_feishu_chat_search(self, args: dict) -> dict:
        """Search group chats."""
        query = args.get("query", "")
        result = await self.feishu.chat_search(query)
        return {"status": "success", "data": result}

    async def do_feishu_chat_messages(self, args: dict) -> dict:
        """List chat messages."""
        chat_id = args.get("chat_id", "")
        limit = args.get("limit", 20)
        result = await self.feishu.chat_messages(chat_id, limit)
        return {"status": "success", "data": result}

    async def do_feishu_drive_upload(self, args: dict) -> dict:
        """Upload file to Drive."""
        local_path = args.get("local_path", "")
        folder_token = args.get("folder_token", "")
        result = await self.feishu.drive_upload(local_path, folder_token)
        return {"status": "success", "data": result}

    async def do_feishu_cli_run(self, args: dict) -> dict:
        """Execute an arbitrary lark-cli command."""
        cmd_args = args.get("args", "")
        if not cmd_args:
            return {"status": "error", "msg": "args is required"}
        import shlex
        arg_list = shlex.split(cmd_args)
        result = await self.feishu._run(arg_list)
        return {"status": "success" if result.get("ok") else "error", "data": result}

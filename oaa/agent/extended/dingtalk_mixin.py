"""DingTalk mixin — DingTalk message, calendar, doc, sheet, AI table, and CLI tools."""
from ..tool_decorator import agent_tool


class DingtalkMixin:
    """DingTalk platform tools: message, user, chat, calendar, todo, doc, drive, sheet, AI table, CLI."""

    async def do_dingtalk_send_message(self, args: dict) -> dict:
        """Send a DingTalk message to a user."""
        user_id = args.get("user_id", "")
        text = args.get("text", "")
        title = args.get("title", "")
        if not user_id or not text:
            return {"status": "error", "msg": "user_id and text are required"}
        result = await self.dingtalk.send_message(user_id, text, title)
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_dingtalk_send_group_message(self, args: dict) -> dict:
        """Send a DingTalk message to a group."""
        group_id = args.get("group_id", "")
        text = args.get("text", "")
        title = args.get("title", "")
        if not group_id or not text:
            return {"status": "error", "msg": "group_id and text are required"}
        result = await self.dingtalk.send_group_message(group_id, text, title)
        return {"status": "success" if result.get("ok") else "error", "data": result}

    async def do_dingtalk_search_user(self, args: dict) -> dict:
        """Search DingTalk users."""
        query = args.get("query", "")
        result = await self.dingtalk.search_user(query)
        return {"status": "success", "data": result}

    async def do_dingtalk_user_info(self, args: dict) -> dict:
        """Get DingTalk user info."""
        user_id = args.get("user_id", "")
        result = await self.dingtalk.get_user(user_id)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_search(self, args: dict) -> dict:
        """Search group conversations."""
        query = args.get("query", "")
        result = await self.dingtalk.chat_search(query)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_list(self, args: dict) -> dict:
        """List top conversations."""
        limit = args.get("limit", 20)
        result = await self.dingtalk.chat_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_history(self, args: dict) -> dict:
        """List recent messages in a group."""
        group_id = args.get("group_id", "")
        limit = args.get("limit", 20)
        if not group_id:
            return {"status": "error", "msg": "group_id is required"}
        result = await self.dingtalk.chat_history(group_id, limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_chat_unread(self, args: dict) -> dict:
        """List unread conversations."""
        limit = args.get("limit", 20)
        result = await self.dingtalk.chat_unread(limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_calendar_list(self, args: dict) -> dict:
        """List calendar events."""
        limit = args.get("limit", 50)
        result = await self.dingtalk.calendar_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_calendar_create(self, args: dict) -> dict:
        """Create a calendar event."""
        summary = args.get("summary", "")
        start_time = args.get("start_time", "")
        end_time = args.get("end_time", "")
        description = args.get("description", "")
        attendees = args.get("attendees", [])
        result = await self.dingtalk.calendar_create(
            summary, start_time, end_time, description, attendees,
        )
        return {"status": "success", "data": result}

    async def do_dingtalk_todo_list(self, args: dict) -> dict:
        """List todo tasks."""
        limit = args.get("limit", 50)
        result = await self.dingtalk.todo_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_todo_create(self, args: dict) -> dict:
        """Create a todo task."""
        subject = args.get("subject", "")
        description = args.get("description", "")
        due_time = args.get("due_time", "")
        executor_ids = args.get("executor_ids", [])
        result = await self.dingtalk.todo_create(subject, description, due_time, executor_ids)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_search(self, args: dict) -> dict:
        """Search documents."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.dingtalk.doc_search(query, limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_read(self, args: dict) -> dict:
        """Read a document."""
        doc_id = args.get("doc_id", "")
        if not doc_id:
            return {"status": "error", "msg": "doc_id is required"}
        result = await self.dingtalk.doc_read(doc_id)
        return {"status": "success", "data": result}

    async def do_dingtalk_doc_create(self, args: dict) -> dict:
        """Create a document."""
        title = args.get("title", "")
        content = args.get("content", "")
        if not title:
            return {"status": "error", "msg": "title is required"}
        result = await self.dingtalk.doc_create(title, content)
        return {"status": "success", "data": result}

    async def do_dingtalk_drive_list(self, args: dict) -> dict:
        """List Drive files."""
        parent_id = args.get("parent_id", "")
        limit = args.get("limit", 50)
        result = await self.dingtalk.drive_list(parent_id, limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_wiki_search(self, args: dict) -> dict:
        """Search wiki."""
        query = args.get("query", "")
        limit = args.get("limit", 20)
        result = await self.dingtalk.wiki_search(query, limit=limit)
        return {"status": "success", "data": result}

    # ------------------------------------------------------------------
    # DingTalk Sheet tools
    # ------------------------------------------------------------------

    async def do_dingtalk_sheet_info(self, args: dict) -> dict:
        """Get DingTalk sheet info."""
        workbook_id = args.get("workbook_id", "")
        sheet_id = args.get("sheet_id", "")
        if not workbook_id:
            return {"status": "error", "msg": "workbook_id is required"}
        result = await self.dingtalk.sheet_info(workbook_id, sheet_id)
        return {"status": "success", "data": result}

    async def do_dingtalk_sheet_create(self, args: dict) -> dict:
        """Create a DingTalk spreadsheet."""
        title = args.get("title", "")
        if not title:
            return {"status": "error", "msg": "title is required"}
        result = await self.dingtalk.sheet_create(title)
        return {"status": "success", "data": result}

    async def do_dingtalk_sheet_list(self, args: dict) -> dict:
        """List worksheets in a DingTalk spreadsheet."""
        node = args.get("node", "")
        if not node:
            return {"status": "error", "msg": "node is required"}
        result = await self.dingtalk.sheet_list(node)
        return {"status": "success", "data": result}

    async def do_dingtalk_sheet_append(self, args: dict) -> dict:
        """Append rows to a DingTalk worksheet."""
        node = args.get("node", "")
        sheet_id = args.get("sheet_id", "")
        values = args.get("values", "")
        if not node or not sheet_id or not values:
            return {"status": "error", "msg": "node, sheet_id, and values are required"}
        result = await self.dingtalk.sheet_append(node, sheet_id, values)
        return {"status": "success", "data": result}

    async def do_dingtalk_sheet_read(self, args: dict) -> dict:
        """Read cell values from a DingTalk worksheet."""
        node = args.get("node", "")
        sheet_id = args.get("sheet_id", "")
        range_str = args.get("range", "")
        if not node or not sheet_id:
            return {"status": "error", "msg": "node and sheet_id are required"}
        result = await self.dingtalk.sheet_read(node, sheet_id, range_str)
        return {"status": "success", "data": result}

    # ------------------------------------------------------------------
    # DingTalk AITable (多维表) tools
    # ------------------------------------------------------------------

    async def do_dingtalk_base_create(self, args: dict) -> dict:
        """Create a DingTalk AI table base (多维表)."""
        name = args.get("name", "")
        if not name:
            return {"status": "error", "msg": "name is required"}
        result = await self.dingtalk.aitable_base_create(name)
        return {"status": "success", "data": result}

    async def do_dingtalk_base_list(self, args: dict) -> dict:
        """List DingTalk AI table bases."""
        limit = args.get("limit", 20)
        result = await self.dingtalk.aitable_base_list(limit=limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_table_create(self, args: dict) -> dict:
        """Create a data table in a DingTalk AI table base."""
        base_id = args.get("base_id", "")
        name = args.get("name", "")
        if not base_id or not name:
            return {"status": "error", "msg": "base_id and name are required"}
        result = await self.dingtalk.aitable_table_create(base_id, name)
        return {"status": "success", "data": result}

    async def do_dingtalk_record_create(self, args: dict) -> dict:
        """Add records to a DingTalk AI table."""
        base_id = args.get("base_id", "")
        table_id = args.get("table_id", "")
        records = args.get("records", "")
        if not base_id or not table_id or not records:
            return {"status": "error", "msg": "base_id, table_id, and records are required"}
        result = await self.dingtalk.aitable_record_create(base_id, table_id, records)
        return {"status": "success", "data": result}

    async def do_dingtalk_record_query(self, args: dict) -> dict:
        """Query records from a DingTalk AI table."""
        base_id = args.get("base_id", "")
        table_id = args.get("table_id", "")
        limit = args.get("limit", 100)
        if not base_id or not table_id:
            return {"status": "error", "msg": "base_id and table_id are required"}
        result = await self.dingtalk.aitable_record_query(base_id, table_id, limit)
        return {"status": "success", "data": result}

    async def do_dingtalk_cli_run(self, args: dict) -> dict:
        """Execute an arbitrary dws command."""
        cmd_args = args.get("args", "")
        if not cmd_args:
            return {"status": "error", "msg": "args is required"}
        import shlex
        arg_list = shlex.split(cmd_args)
        result = await self.dingtalk._run(arg_list)
        return {"status": "success" if result.get("ok") else "error", "data": result}

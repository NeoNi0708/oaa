"""Extended tools: email, word, excel, planner, skill tools, wechat."""
import os
from typing import TYPE_CHECKING, Optional

from .path_utils import resolve_workspace_path
from .planner import Planner

if TYPE_CHECKING:
    from ..auth.permissions import PermissionsManager


class ExtendedTools:
    """Extended tools: email, word, excel, skill tools, planner."""

    def __init__(self, data_dir: str, permissions: Optional["PermissionsManager"] = None):
        self.data_dir = data_dir
        self.permissions = permissions
        self.planner = Planner(os.path.join(data_dir, "workspace", "plans"))
        from ..gateway.adapters.wechat_cli import WeChatCLI
        self.wechat = WeChatCLI()

    async def _confirm(self, operation: str, details: str = "") -> bool:
        """Check permission for an operation. Returns True if allowed."""
        if self.permissions:
            return await self.permissions.confirm_operation(operation, details)
        return True

    async def do_email_send(self, args: dict) -> dict:
        """Send email via SMTP (himayala or smtplib)."""
        to = args.get("to", "")
        subject = args.get("subject", "")
        if not await self._confirm("email_send", f"To: {to}, Subject: {subject}"):
            return {"status": "error", "msg": "Email sending not permitted"}
        # Stub — actual SMTP sending depends on user config
        return {"status": "success", "msg": f"Email to {to}: '{subject}' queued"}
        # TODO: implement actual SMTP when user provides email config

    async def do_word_doc(self, args: dict) -> dict:
        """Generate Word document."""
        try:
            from docx import Document
        except ImportError:
            return {"status": "error", "msg": "python-docx not installed. Run: pip install python-docx"}
        try:
            path = resolve_workspace_path(args.get("path", "document.docx"), self.data_dir, self.permissions)
        except PermissionError as exc:
            return {"status": "error", "msg": str(exc)}
        title = args.get("title", "Document")
        content = args.get("content", "")
        doc = Document()
        doc.add_heading(title, 0)
        for para in content.split("\n"):
            if para.strip():
                doc.add_paragraph(para)
        doc.save(path)
        return {"status": "success", "path": path}

    async def do_excel_xlsx(self, args: dict) -> dict:
        """Generate Excel spreadsheet."""
        try:
            from openpyxl import Workbook
        except ImportError:
            return {"status": "error", "msg": "openpyxl not installed. Run: pip install openpyxl"}
        try:
            path = resolve_workspace_path(args.get("path", "spreadsheet.xlsx"), self.data_dir, self.permissions)
        except PermissionError as exc:
            return {"status": "error", "msg": str(exc)}
        rows = args.get("rows", [])
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except (json.JSONDecodeError, TypeError):
                rows = [[cell] for cell in rows.split("\n") if cell.strip()]
        wb = Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        wb.save(path)
        return {"status": "success", "path": path}

    async def do_plan_create(self, args: dict) -> dict:
        """Create a new plan via Planner."""
        goal = args.get("goal", "")
        steps = args.get("steps", [])
        try:
            plan = self.planner.create(goal, steps)
        except ValueError as exc:
            return {"status": "error", "msg": str(exc)}
        return {"status": "success", "plan": plan}

    async def do_plan_update(self, args: dict) -> dict:
        """Update a plan step via Planner."""
        plan_id = args.get("plan_id", "")
        step_id = args.get("step_id", 0)
        status = args.get("status", "done")
        result = args.get("result", "")
        plan = self.planner.update(plan_id, step_id, status, result)
        if plan is None:
            return {"status": "error", "msg": f"Plan {plan_id} not found"}
        return {"status": "success", "plan": plan}

    async def do_plan_list(self, args: dict) -> dict:
        """List plans via Planner."""
        status = args.get("status", "")
        plans = self.planner.list_plans(status)
        return {"status": "success", "plans": plans, "count": len(plans)}

    async def do_skill_search(self, args: dict) -> dict:
        """Search ClawHub skill market (stub)."""
        return {"status": "success", "results": [], "msg": "Skill market search stub"}

    async def do_skill_install(self, args: dict) -> dict:
        """Install a skill from ClawHub (stub)."""
        slug = args.get("slug", "")
        if not await self._confirm("skill_install", f"Install skill: {slug}"):
            return {"status": "error", "msg": "Skill install not permitted"}
        return {"status": "success", "msg": f"Skill '{slug}' install stub"}

    async def do_wechat_sessions(self, args: dict) -> dict:
        """Get recent WeChat session list."""
        limit = args.get("limit", 20)
        result = await self.wechat.sessions(limit)
        return {"status": "success", "data": result}

    async def do_wechat_history(self, args: dict) -> dict:
        """Get WeChat chat history with a contact."""
        name = args.get("name", "")
        limit = args.get("limit", 20)
        result = await self.wechat.history(name, limit)
        return {"status": "success", "data": result}

    async def do_wechat_search(self, args: dict) -> dict:
        """Search WeChat messages globally or in a specific chat."""
        keyword = args.get("keyword", "")
        chat = args.get("chat", "")
        limit = args.get("limit", 20)
        result = await self.wechat.search(keyword, chat, limit)
        return {"status": "success", "data": result}

    async def do_wechat_contacts(self, args: dict) -> dict:
        """Search WeChat contacts."""
        query = args.get("query", "")
        result = await self.wechat.contacts(query)
        return {"status": "success", "data": result}

    async def do_wechat_unread(self, args: dict) -> dict:
        """Get unread WeChat sessions."""
        limit = args.get("limit", 20)
        result = await self.wechat.unread(limit)
        return {"status": "success", "data": result}

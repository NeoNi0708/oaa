"""WeChat mixin — WeChat session, message, and contact tools."""
from ..tool_decorator import agent_tool


class WechatMixin:
    """WeChat messaging and data tools (delegates to self.wechat / self._wechat_adapter)."""

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

    async def do_wechat_send_text(self, args: dict) -> dict:
        """Proactively send a WeChat text message to a contact."""
        to = self._resolve_recipient(args.get("to", ""))
        text = args.get("text", "")
        if not to:
            return {"status": "error", "msg": "请指定收件人（wxid），或在微信中说「发给我」自动使用当前用户"}
        if not text:
            return {"status": "error", "msg": "text is required"}
        if not self._wechat_adapter:
            return {"status": "error", "msg": "微信适配器未连接，请先扫码登录"}
        if not await self._confirm("wechat_send_text", f"To: {to}"):
            return {"status": "error", "msg": "WeChat send not permitted"}
        try:
            result = await self._wechat_adapter.send_message(to, text)
            return {"status": "success" if result.get("status") == "success" else "error", "data": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    async def do_wechat_send_file(self, args: dict) -> dict:
        """Send a local file to a WeChat contact via CDN upload."""
        to = self._resolve_recipient(args.get("to", ""))
        file_path = args.get("file_path", "")
        if not to:
            return {"status": "error", "msg": "请指定收件人（wxid），或在微信中说「发给我」自动使用当前用户"}
        if not file_path:
            return {"status": "error", "msg": "file_path is required"}
        if not self._wechat_adapter:
            return {"status": "error", "msg": "微信适配器未连接，请先扫码登录"}
        if not await self._confirm("wechat_send_file", f"To: {to}, File: {file_path}"):
            return {"status": "error", "msg": "WeChat send not permitted"}
        try:
            result = await self._wechat_adapter.send_file(to, file_path)
            if result.get("status") == "success":
                return {"status": "success", "data": result}

            # ret=-2: upload rejected by server, needs re-login
            ret = result.get("ret", 0)
            if ret == -2:
                qr = self._wechat_adapter.get_qrcode()
                return {
                    "status": "error",
                    "msg": "微信文件上传功能异常(ret=-2)，需要重新扫码登录。请在5分钟内扫描以下二维码完成重新认证后重试发送。",
                    "needs_reconnect": True,
                    "qrcode_url": qr.get("qrcode_url", ""),
                    "qrcode_id": qr.get("qrcode_id", ""),
                }

            return {"status": "error", "msg": result.get("msg") or "发送失败"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    async def do_wechat_send_typing(self, args: dict) -> dict:
        """Send typing indicator ('对方正在输入...'). status=1 show, 0 hide."""
        to = args.get("to", "")
        status = args.get("status", 1)
        if not to:
            return {"status": "error", "msg": "to is required"}
        if not self._wechat_adapter:
            return {"status": "error", "msg": "微信适配器未连接，请先扫码登录"}
        try:
            result = await self._wechat_adapter.send_typing(to, status)
            return {"status": "success" if result.get("status") == "success" else "error", "data": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

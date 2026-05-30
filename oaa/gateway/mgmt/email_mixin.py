"""Email mixin — email account configuration CRUD and test."""
import asyncio
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class EmailMixin:
    """Email account configuration: list, save, delete, test."""

    def _handle_list_emails(self, _payload: dict) -> dict:
        """Return all configured email accounts (credentials redacted)."""
        accounts = self._email_manager.list_accounts()
        providers = self._email_manager.get_provider_list()
        return {"ok": True, "accounts": accounts, "providers": providers}

    async def _handle_save_email(self, payload: dict) -> dict:
        """Create or update an email account. Optionally test before saving."""
        account = payload.get("account", {})
        if not account or not account.get("username") or not account.get("auth_code"):
            return {"ok": False, "error": "邮箱地址和授权码不能为空"}

        # Test connection before saving
        test_result = await self._email_manager.test_connection(account)
        if not test_result.get("ok"):
            return {
                "ok": False,
                "test_ok": False,
                "errors": test_result.get("errors", []),
                "imap_error": test_result.get("imap_error"),
                "smtp_error": test_result.get("smtp_error"),
            }

        saved = self._email_manager.save_account(account)
        return {"ok": True, "account": saved}

    def _handle_delete_email(self, payload: dict) -> dict:
        """Delete an email account by id."""
        account_id = payload.get("id", "")
        if not account_id:
            return {"ok": False, "error": "No account id provided"}
        ok = self._email_manager.delete_account(account_id)
        return {"ok": ok}

    async def _handle_test_email(self, payload: dict) -> dict:
        """Test connection for an email account (existing or unsaved)."""
        account = payload.get("account", {})
        if not account:
            return {"ok": False, "error": "请提供邮箱配置"}
        result = await self._email_manager.test_connection(account)
        ok = result.get("ok", False)

        # Self-healing: if test failed and callback is registered, route
        # the error context to the agent for diagnosis and code fix.
        if not ok and self._heal_callback:
            provider = account.get("provider", account.get("imap_server", "未知"))
            imap_err = result.get("imap_error") or ""
            smtp_err = result.get("smtp_error") or ""
            detail = imap_err or smtp_err or str(result.get("errors", []))
            diagnostic_text = (
                f"【自愈触发】邮箱连接测试失败\n\n"
                f"提供商: {provider}\n"
                f"服务器: {account.get('imap_server', '?')}:{account.get('imap_port', '?')}\n"
                f"错误: {detail}\n\n"
                f"请按以下步骤诊断修复：\n"
                f"1. 用 read_own_source 读取 oaa/gateway/email_config.py\n"
                f"2. 分析 _test_imap 和 _test_smtp 方法的 SSL/TLS 连接代码\n"
                f"3. 找出导致 SSL 握手失败的根因\n"
                f"4. 用 self_improve 修复代码（用旧字符串替换为新字符串）\n"
                f"5. 修复后告知用户已修复，请重新测试"
            )
            ctx = {
                "type": "diagnostic",
                "diagnostic_subtype": "email_test",
                "raw_prompt": diagnostic_text,
                "error_detail": detail,
                "account_username": account.get("username", ""),
                "account_provider": provider,
                "account_imap_server": account.get("imap_server", ""),
                "account_imap_port": account.get("imap_port", ""),
            }
            try:
                self._heal_callback(ctx)
            except Exception:
                pass

        return {"ok": ok, **result}

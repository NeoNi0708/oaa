"""Email account configuration manager — CRUD + SMTP/IMAP test."""
import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger

logger = get_logger("gateway.email")

# Common email provider definitions
PROVIDER_CONFIGS: dict[str, dict] = {
    "gmail": {
        "name": "Google Gmail",
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_tls": True,
    },
    "outlook": {
        "name": "Microsoft 365 / Outlook",
        "imap_server": "outlook.office365.com",
        "imap_port": 993,
        "smtp_server": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_tls": True,
    },
    "qq": {
        "name": "QQ邮箱",
        "imap_server": "imap.qq.com",
        "imap_port": 993,
        "smtp_server": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "qq_exmail": {
        "name": "腾讯企业邮",
        "imap_server": "imap.exmail.qq.com",
        "imap_port": 993,
        "smtp_server": "smtp.exmail.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "netease": {
        "name": "网易163邮箱",
        "imap_server": "imap.163.com",
        "imap_port": 993,
        "smtp_server": "smtp.163.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "netease_exmail": {
        "name": "网易企业邮",
        "imap_server": "imap.qiye.163.com",
        "imap_port": 993,
        "smtp_server": "smtp.qiye.163.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "aliyun": {
        "name": "阿里企业邮",
        "imap_server": "imap.mxhichina.com",
        "imap_port": 993,
        "smtp_server": "smtp.mxhichina.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "zoho": {
        "name": "Zoho Mail",
        "imap_server": "imap.zoho.com",
        "imap_port": 993,
        "smtp_server": "smtp.zoho.com",
        "smtp_port": 587,
        "smtp_tls": True,
    },
    "yahoo": {
        "name": "Yahoo Mail",
        "imap_server": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "icloud": {
        "name": "iCloud Mail",
        "imap_server": "imap.mail.me.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_tls": True,
    },
    "mail139": {
        "name": "中国移动 139邮箱",
        "imap_server": "imap.139.com",
        "imap_port": 993,
        "smtp_server": "smtp.139.com",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "mail189": {
        "name": "中国电信 189邮箱",
        "imap_server": "imap.189.cn",
        "imap_port": 993,
        "smtp_server": "smtp.189.cn",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "wo": {
        "name": "中国联通 沃邮箱",
        "imap_server": "imap.wo.cn",
        "imap_port": 993,
        "smtp_server": "smtp.wo.cn",
        "smtp_port": 465,
        "smtp_ssl": True,
    },
    "custom": {
        "name": "自建域名 / 其他",
        "imap_server": "",
        "imap_port": 993,
        "smtp_server": "",
        "smtp_port": 587,
        "smtp_tls": True,
        "custom": True,
    },
}


class EmailConfigManager:
    """Manages email account configurations stored as JSON."""

    def __init__(self, data_dir: str):
        self._path = Path(data_dir) / "memory" / "email_accounts.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._accounts: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load email accounts: %s", exc)
        return []

    def _save(self):
        self._path.write_text(
            json.dumps(self._accounts, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_accounts(self) -> list[dict]:
        """Return all accounts with credentials redacted."""
        result = []
        for a in self._accounts:
            entry = dict(a)
            key = entry.get("auth_code", "")
            if len(key) > 8:
                entry["auth_code"] = key[:4] + "****" + key[-4:]
            elif key:
                entry["auth_code"] = "****"
            result.append(entry)
        return result

    def get_account(self, account_id: str) -> Optional[dict]:
        for a in self._accounts:
            if a["id"] == account_id:
                return dict(a)
        return None

    def save_account(self, data: dict) -> dict:
        """Create or update an account. Returns the saved account."""
        account_id = data.get("id", "")
        now = datetime.now().isoformat()

        if account_id:
            for a in self._accounts:
                if a["id"] == account_id:
                    a.update({
                        "provider": data.get("provider", a["provider"]),
                        "display_name": data.get("display_name", a["display_name"]),
                        "username": data.get("username", a["username"]),
                        "auth_code": data.get("auth_code", a["auth_code"]),
                        "imap_server": data.get("imap_server", a["imap_server"]),
                        "imap_port": data.get("imap_port", a["imap_port"]),
                        "smtp_server": data.get("smtp_server", a["smtp_server"]),
                        "smtp_port": data.get("smtp_port", a["smtp_port"]),
                        "smtp_tls": data.get("smtp_tls", a["smtp_tls"]),
                        "updated_at": now,
                    })
                    self._save()
                    return dict(a)

        new_account = {
            "id": uuid.uuid4().hex[:12],
            "provider": data.get("provider", ""),
            "display_name": data.get("display_name", ""),
            "username": data.get("username", ""),
            "auth_code": data.get("auth_code", ""),
            "imap_server": data.get("imap_server", ""),
            "imap_port": data.get("imap_port", 993),
            "smtp_server": data.get("smtp_server", ""),
            "smtp_port": data.get("smtp_port", 587),
            "smtp_tls": data.get("smtp_tls", True),
            "created_at": now,
            "updated_at": now,
        }
        self._accounts.append(new_account)
        self._save()
        return dict(new_account)

    def delete_account(self, account_id: str) -> bool:
        before = len(self._accounts)
        self._accounts = [a for a in self._accounts if a["id"] != account_id]
        if len(self._accounts) < before:
            self._save()
            return True
        return False

    async def test_connection(self, account: dict) -> dict:
        """Test IMAP login + SMTP login for a single account.

        Returns {"ok": True} on success, or {"ok": False, "imap_error": ..., "smtp_error": ...}.
        """
        results: dict[str, str | None] = {}

        # Test IMAP
        imap_error = await self._test_imap(account)
        results["imap_error"] = imap_error

        # Test SMTP
        smtp_error = await self._test_smtp(account)
        results["smtp_error"] = smtp_error

        if not results["imap_error"] and not results["smtp_error"]:
            return {"ok": True}

        errors = []
        if results["imap_error"]:
            errors.append(f"IMAP: {results['imap_error']}")
        if results["smtp_error"]:
            errors.append(f"SMTP: {results['smtp_error']}")
        return {"ok": False, "errors": errors, **results}

    @staticmethod
    async def _test_imap(account: dict) -> Optional[str]:
        """Test IMAP connection. Returns error string or None on success."""
        import imaplib
        import ssl

        try:
            server = account.get("imap_server", "")
            port = account.get("imap_port", 993)
            username = account.get("username", "")
            password = account.get("auth_code", "")

            def _run():
                # 创建兼容的 SSL 上下文：允许 TLS 1.0+，最低安全级别以兼容旧邮件服务器（如139邮箱）
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.minimum_version = ssl.TLSVersion.TLSv1
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")

                conn = imaplib.IMAP4_SSL(server, port, timeout=15, ssl_context=ctx)
                try:
                    conn.login(username, password)
                    conn.list()
                    return None
                except imaplib.IMAP4.error as e:
                    return str(e)
                finally:
                    try:
                        conn.logout()
                    except Exception:
                        pass

            error = await asyncio.to_thread(_run)
            if error:
                if "LOGIN failed" in error or "authentication" in error.lower():
                    return "登录失败，请检查邮箱地址和授权码是否正确"
                return f"IMAP 连接失败: {error}"
            return None
        except Exception as e:
            return f"IMAP 连接异常: {e}"

    @staticmethod
    async def _test_smtp(account: dict) -> Optional[str]:
        """Test SMTP connection. Returns error string or None on success."""
        import smtplib
        import ssl

        try:
            server = account.get("smtp_server", "")
            port = account.get("smtp_port", 587)
            username = account.get("username", "")
            password = account.get("auth_code", "")
            use_tls = account.get("smtp_tls", True)

            def _run():
                if port == 465:
                    # 兼容旧邮件服务器的 SSL 上下文
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.minimum_version = ssl.TLSVersion.TLSv1
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
                    conn = smtplib.SMTP_SSL(server, port, timeout=15, context=ctx)
                else:
                    conn = smtplib.SMTP(server, port, timeout=15)
                    if use_tls:
                        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                        ctx.minimum_version = ssl.TLSVersion.TLSv1
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
                        conn.starttls(context=ctx)

                try:
                    conn.login(username, password)
                    return None
                except smtplib.SMTPAuthenticationError:
                    return "登录失败，请检查邮箱地址和授权码是否正确"
                except smtplib.SMTPException as e:
                    return str(e)
                finally:
                    try:
                        conn.quit()
                    except Exception:
                        pass

            error = await asyncio.to_thread(_run)
            if error:
                return f"SMTP 连接失败: {error}"
            return None
        except Exception as e:
            return f"SMTP 连接异常: {e}"

    @staticmethod
    def get_provider_list() -> list[dict]:
        """Return available provider definitions (for frontend dropdown)."""
        return [
            {
                "key": k,
                "name": v["name"],
                "imap_server": v.get("imap_server", ""),
                "imap_port": v.get("imap_port", 993),
                "smtp_server": v.get("smtp_server", ""),
                "smtp_port": v.get("smtp_port", 587),
                "smtp_tls": v.get("smtp_tls", True),
                "custom": v.get("custom", False),
            }
            for k, v in PROVIDER_CONFIGS.items()
        ]

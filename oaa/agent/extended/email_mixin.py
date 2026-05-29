"""Email mixin — email sending tools."""
import asyncio
from ..tool_decorator import agent_tool


class EmailMixin:
    """Email sending tools."""

    @agent_tool(description="Send email via SMTP using a configured email account. Requires an email account to be configured in 连接页面 → 邮箱配置 first.")
    async def do_email_send(self, to: str, subject: str, body: str,
                             account_id: str = "", cc: str = "", bcc: str = "") -> dict:
        """Send email via SMTP using a configured email account."""
        if not await self._confirm("email_send", f"To: {to}, Subject: {subject}"):
            return {"status": "error", "msg": "Email sending not permitted"}

        from ...gateway.email_config import EmailConfigManager
        mgr = EmailConfigManager(self.data_dir)
        accounts = mgr.list_accounts()
        if not accounts:
            return {"status": "error", "msg": "未配置邮箱账户，请在 连接页面 → 邮箱配置 中添加"}

        # Resolve which account to send from
        account = None
        if account_id:
            account = mgr.get_account(account_id)
            if not account:
                return {"status": "error", "msg": f"邮箱账户 {account_id} 不存在"}
        else:
            account = mgr.get_account(accounts[0]["id"])

        # Build and send email
        try:
            import smtplib
            import ssl
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = account["username"]
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            msg.attach(MIMEText(body, "plain", "utf-8"))

            # Collect all recipients
            recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
            if cc:
                recipients += [addr.strip() for addr in cc.split(",") if addr.strip()]
            if bcc:
                recipients += [addr.strip() for addr in bcc.split(",") if addr.strip()]

            server = account["smtp_server"]
            port = account["smtp_port"]
            use_tls = account.get("smtp_tls", True)
            username = account["username"]
            password = account["auth_code"]

            def _send():
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.minimum_version = ssl.TLSVersion.TLSv1
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")

                if port == 465:
                    conn = smtplib.SMTP_SSL(server, port, timeout=30, context=ctx)
                else:
                    conn = smtplib.SMTP(server, port, timeout=30)
                    if use_tls:
                        conn.starttls(context=ctx)
                try:
                    conn.login(username, password)
                    conn.sendmail(username, recipients, msg.as_string())
                finally:
                    try:
                        conn.quit()
                    except Exception:
                        pass

            await asyncio.to_thread(_send)
            return {"status": "success", "msg": f"邮件已发送至 {to}"}

        except smtplib.SMTPAuthenticationError:
            return {"status": "error", "msg": "SMTP 认证失败，请检查邮箱授权码是否正确"}
        except smtplib.SMTPException as e:
            return {"status": "error", "msg": f"SMTP 错误: {e}"}
        except ImportError:
            return {"status": "error", "msg": "smtplib not available (standard library — should never happen)"}
        except Exception as e:
            return {"status": "error", "msg": f"发送邮件失败: {e}"}

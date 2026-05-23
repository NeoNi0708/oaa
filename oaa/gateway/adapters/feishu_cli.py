"""Feishu (Lark) CLI integration — wraps lark-cli for subprocess execution.

Provides auto-install, config management, device auth, and command execution
for all lark-cli shortcuts (IM, Calendar, Contact, Drive, Docs, Sheets,
Base, Task, Wiki, etc.).
"""
import asyncio
import json
import os
import subprocess
import sys
from typing import Optional

from ...logging_config import get_logger

logger = get_logger("gateway.feishu_cli")


class FeishuCLI:
    """Wrapper around lark-cli for subprocess execution.

    Handles auto-install, credential configuration, device auth QR login,
    and domain-specific shortcut execution.  All public methods are async
    so they can be called from both sync contexts (feishu adapter QR flow)
    and async contexts (extended tools).
    """

    NPM_PACKAGE = "@larksuite/cli"
    CONFIG_DIR = os.path.expanduser("~/.lark-cli")

    def __init__(self):
        self._binary: Optional[str] = None
        self._last_error: str = ""

    # ------------------------------------------------------------------
    # Binary discovery & auto-install
    # ------------------------------------------------------------------

    @property
    def last_error(self) -> str:
        """Last error message from an operation."""
        return self._last_error

    def _find_binary(self) -> Optional[str]:
        """Locate the ``lark-cli`` binary on ``PATH`` or global npm root.

        On Windows the npm global install creates ``lark-cli.cmd`` (a batch
        wrapper); ``create_subprocess_exec`` needs the extension explicitly.
        """
        if self._binary:
            return self._binary

        # --- 0. Bundled CLI (shipped with OAA) ---
        import os as _os
        ext = ".cmd" if sys.platform == "win32" else ""
        _bundled = _os.path.normpath(
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "..", "..", "..", "cli", "node_modules", ".bin",
                          "lark-cli" + ext)
        )
        if _os.path.isfile(_bundled) or _os.path.isfile(_bundled.replace(".cmd", "")):
            self._binary = _bundled
            return self._binary

        found = None
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["where", "lark-cli"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    candidates = [s.strip() for s in result.stdout.split("\n") if s.strip()]
                    # Prefer .cmd over extensionless script on Windows
                    for c in candidates:
                        if c.endswith(".cmd"):
                            found = c
                            break
                    if not found and candidates:
                        found = candidates[0]
            else:
                result = subprocess.run(
                    ["which", "lark-cli"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    candidates = [s.strip() for s in result.stdout.split("\n") if s.strip()]
                    if candidates:
                        found = candidates[0]
        except Exception:
            pass

        if found:
            self._binary = found
            return self._binary

        # Fallback: try PATH directly
        candidates = ["lark-cli"]
        if sys.platform == "win32":
            candidates.insert(0, "lark-cli.cmd")
        for name in candidates:
            try:
                subprocess.run([name, "--help"], capture_output=True, timeout=5)
                self._binary = name
                return self._binary
            except FileNotFoundError:
                continue
        return None

    async def ensure_installed(self) -> bool:
        """Auto-install lark-cli via ``npm install -g @larksuite/cli``.

        Returns ``True`` if the binary is available (before or after install).
        """
        if self._find_binary():
            return True

        sys.stderr.write("[FeishuCLI] lark-cli not found, installing via npm...\n")
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", self.NPM_PACKAGE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")
            sys.stderr.write(f"[FeishuCLI] npm install failed: {err_text}\n")
            return False

        found = self._find_binary() is not None
        if found:
            sys.stderr.write("[FeishuCLI] lark-cli installed successfully\n")
        return found

    # ------------------------------------------------------------------
    # Credential configuration
    # ------------------------------------------------------------------

    def is_configured(self, app_id: str = "") -> bool:
        """Check if lark-cli has app credentials stored.

        If *app_id* is given, checks that the stored config matches it.
        """
        config_file = os.path.join(self.CONFIG_DIR, "config.json")
        if not os.path.exists(config_file):
            return False
        try:
            with open(config_file, encoding="utf-8") as f:
                cfg = json.load(f)
            apps = cfg.get("apps", [])
            if not apps:
                return False
            if app_id:
                return apps[0].get("appId") == app_id
            return bool(apps[0].get("appId"))
        except Exception:
            return False

    async def ensure_configured(self, app_id: str, app_secret: str) -> bool:
        """Configure lark-cli with Feishu app credentials.

        Uses ``lark-cli config init --new --app-id <id> --app-secret-stdin``
        so the secret is stored in the OS keychain rather than plaintext.

        Returns ``True`` on success.
        """
        if not await self.ensure_installed():
            return False

        binary = self._find_binary()
        if not binary:
            return False

        # Skip if already configured with the same app_id
        if self.is_configured(app_id):
            return True

        proc = await asyncio.create_subprocess_exec(
            binary, "config", "init", "--new",
            "--app-id", app_id,
            "--app-secret-stdin",
            "--brand", "feishu",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=app_secret.encode())
        if proc.returncode != 0:
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            detail = err or out or f"exit code {proc.returncode}"
            self._last_error = detail[:300]
            logger.error("[FeishuCLI] config init failed (code=%d): stdout=%.500s stderr=%.500s", proc.returncode, out, err)
            return False
        self._last_error = ""
        logger.info("[FeishuCLI] lark-cli configured for app %s...", app_id[:12])
        return True

    # ------------------------------------------------------------------
    # Subprocess runner
    # ------------------------------------------------------------------

    async def _run(self, args: list[str]) -> dict:
        """Run *args* as ``lark-cli <args>``, parse stdout as JSON.

        Returns a dict.  On success the dict contains at least ``"ok": True``.
        On failure it contains ``"ok": False``, ``"error"``, and
        optionally ``"returncode"``.
        """
        binary = self._find_binary()
        if not binary:
            return {"ok": False, "error": "lark-cli not installed"}

        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            # The command likely printed a JSON error envelope
            if out.startswith("{"):
                try:
                    result = json.loads(out)
                    result.setdefault("ok", False)
                    return result
                except json.JSONDecodeError:
                    pass
            return {"ok": False, "error": err or out, "returncode": proc.returncode}

        # Parse JSON response
        if out.startswith("{"):
            try:
                result = json.loads(out)
                result.setdefault("ok", True)
                return result
            except json.JSONDecodeError:
                pass
        elif out.startswith("["):
            try:
                return {"ok": True, "data": json.loads(out)}
            except json.JSONDecodeError:
                pass

        # Non-JSON output (e.g. config init "OK: ...")
        return {"ok": True, "text": out}

    # ------------------------------------------------------------------
    # Device auth flow (QR login)
    # ------------------------------------------------------------------

    async def get_qrcode(self) -> dict:
        """Initiate the lark-cli device authorization flow.

        Returns a dict compatible with the adapter's ``get_qrcode`` contract:
            ``qrcode_url`` — base64 PNG data URI of the QR code
            ``qrcode_id``  — device_code for polling
            ``user_code``  — human-readable code shown below QR
        """
        result = await self._run(
            ["auth", "login", "--domain", "all", "--no-wait", "--json"]
        )
        if not result.get("ok", False):
            err = result.get("error", {})
            if isinstance(err, dict):
                msg = err.get("message", str(err))
            else:
                msg = str(err)
            return {"error": msg}

        # The --no-wait --json output uses "verification_url" (not "verification_uri").
        verification_url = result.get("verification_url", "") or result.get("verification_uri", "")
        device_code = result.get("device_code", "")
        user_code = result.get("user_code", "")

        if not verification_url:
            return {"error": "No verification URL in device auth response"}

        # Render the verification URL as a QR code PNG
        try:
            import qrcode as qrcode_lib
            import io
            import base64
            img = qrcode_lib.make(verification_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            qr_data_uri = verification_url

        return {
            "qrcode_url": qr_data_uri,
            "qrcode_id": device_code,
            "user_code": user_code,
        }

    async def poll_qrcode_status(self, device_code: str) -> dict:
        """Poll the device authorization completion status.

        Returns a dict with ``"status"`` — one of:
            ``"waiting"``   — user hasn't authorised yet
            ``"confirmed"`` — authorisation complete
            ``"expired"``   — device code expired
            ``"error"``     — unexpected failure
        """
        result = await self._run(
            ["auth", "login", "--device-code", device_code, "--json"]
        )
        if result.get("ok", False):
            return {"status": "confirmed", **result}

        error = result.get("error", {})
        if isinstance(error, dict):
            err_msg = (error.get("message", "") or str(error)).lower()
            if "expired" in err_msg:
                return {"status": "expired", "msg": error.get("message", "")}
            if "authorization_pending" in err_msg or "slow_down" in err_msg:
                return {"status": "waiting"}
            return {"status": "error", "msg": error.get("message", "")}

        err_str = str(error).lower()
        if "expired" in err_str:
            return {"status": "expired", "msg": str(error)}
        return {"status": "waiting"}

    # ------------------------------------------------------------------
    # Domain operations — IM
    # ------------------------------------------------------------------

    async def send_message(self, chat_id: str, text: str) -> dict:
        """Send a text message to a group chat by *chat_id*."""
        return await self._run([
            "im", "+messages-send",
            "--chat-id", chat_id,
            "--text", text,
        ])

    async def send_user_message(self, user_id: str, text: str) -> dict:
        """Send a text message to a user by *open_id*."""
        return await self._run([
            "im", "+messages-send",
            "--user-id", user_id,
            "--text", text,
        ])

    async def chat_search(self, query: str) -> dict:
        """Search visible group chats by name keyword."""
        return await self._run([
            "im", "+chat-search",
            "--query", query,
            "--format", "json",
        ])

    async def chat_messages(self, chat_id: str, limit: int = 20) -> dict:
        """List recent messages in a chat."""
        return await self._run([
            "im", "+chat-messages-list",
            "--chat-id", chat_id,
            "--page-size", str(limit),
            "--format", "json",
        ])

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    async def calendar_agenda(self, start: str = "", end: str = "") -> dict:
        """View calendar agenda (defaults to today)."""
        args = ["calendar", "+agenda", "--format", "json"]
        if start:
            args.extend(["--start", start])
        if end:
            args.extend(["--end", end])
        return await self._run(args)

    async def calendar_create_event(
        self, summary: str, start: str, end: str,
        description: str = "", attendees: Optional[list[str]] = None,
    ) -> dict:
        """Create a calendar event."""
        args = [
            "calendar", "+create",
            "--summary", summary,
            "--start", start,
            "--end", end,
            "--format", "json",
        ]
        if description:
            args.extend(["--description", description])
        if attendees:
            args.extend(["--attendees", ",".join(attendees)])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Contact
    # ------------------------------------------------------------------

    async def search_user(self, query: str, page_size: int = 20) -> dict:
        """Search Feishu users by keyword."""
        return await self._run([
            "contact", "+search-user",
            "--query", query,
            "--page-size", str(page_size),
            "--format", "json",
        ])

    async def get_user(self, user_id: str = "") -> dict:
        """Get user info (omit *user_id* for self)."""
        args = ["contact", "+get-user", "--format", "json"]
        if user_id:
            args.extend(["--user-id", user_id])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Drive
    # ------------------------------------------------------------------

    async def drive_search(self, query: str = "", page_size: int = 20) -> dict:
        """Search files in Drive."""
        args = ["drive", "+search", "--page-size", str(page_size), "--format", "json"]
        if query:
            args.extend(["--query", query])
        return await self._run(args)

    async def drive_upload(self, local_path: str, folder_token: str = "") -> dict:
        """Upload a local file to Drive."""
        args = ["drive", "+upload", "--file", local_path, "--format", "json"]
        if folder_token:
            args.extend(["--folder-token", folder_token])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Docs
    # ------------------------------------------------------------------

    async def doc_fetch(self, token: str) -> dict:
        """Fetch document content by *token*."""
        return await self._run([
            "docs", "+fetch", "--token", token, "--format", "json",
        ])

    async def doc_create(self, title: str, content: str = "") -> dict:
        """Create a new document."""
        args = ["docs", "+create", "--title", title, "--format", "json"]
        if content:
            args.extend(["--content", content])
        return await self._run(args)

    async def doc_search(self, query: str) -> dict:
        """Search documents by keyword."""
        return await self._run([
            "docs", "+search", "--query", query, "--format", "json",
        ])

    # ------------------------------------------------------------------
    # Sheets
    # ------------------------------------------------------------------

    async def sheets_read(self, spreadsheet_token: str, range_str: str = "") -> dict:
        """Read spreadsheet cell values."""
        args = ["sheets", "+read",
                "--spreadsheet-token", spreadsheet_token,
                "--format", "json"]
        if range_str:
            args.extend(["--range", range_str])
        return await self._run(args)

    async def sheets_create(self, title: str) -> dict:
        """Create a new spreadsheet."""
        return await self._run([
            "sheets", "+create", "--title", title,
        ])

    # ------------------------------------------------------------------
    # Base (Bitable)
    # ------------------------------------------------------------------

    async def base_records(
        self, base_token: str, table_id: str, limit: int = 100,
    ) -> dict:
        """List records in a bitable table."""
        return await self._run([
            "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", str(limit),
            "--format", "json",
        ])

    # ------------------------------------------------------------------
    # Task
    # ------------------------------------------------------------------

    async def task_list(self, page_size: int = 50) -> dict:
        """List tasks assigned to me."""
        return await self._run([
            "task", "+get-my-tasks",
            "--page-limit", str(page_size),
            "--format", "json",
        ])

"""DingTalk CLI integration — wraps dws (dingtalk-workspace-cli) for subprocess execution.

Provides auto-install, device auth QR login, and command execution
for all dws service domains (chat, contact, calendar, doc, drive,
sheet, todo, wiki, aitable, etc.).
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional


class DingTalkCLI:
    """Wrapper around dws (dingtalk-workspace-cli) for subprocess execution.

    Handles auto-install via npm, device-authorization QR login (the ``dws``
    binary blocks during auth so we run it as a background subprocess and
    parse the initial text output for the verification URI), and
    domain-specific shortcut execution.  All public methods are async.
    """

    NPM_PACKAGE = "dingtalk-workspace-cli"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self._binary: Optional[str] = None
        self._auth_proc: Any = None  # winpty PtyProcess instance
        self._auth_user_code: str = ""
        self._client_id = client_id
        self._client_secret = client_secret

    # ------------------------------------------------------------------
    # Binary discovery & auto-install
    # ------------------------------------------------------------------

    def _find_binary(self) -> Optional[str]:
        """Locate the ``dws`` binary on ``PATH`` or global npm root.

        On Windows the npm global install creates ``dws.cmd`` (a batch
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
                          "dws" + ext)
        )
        if _os.path.isfile(_bundled) or _os.path.isfile(_bundled.replace(".cmd", "")):
            self._binary = _bundled
            return self._binary

        found = None
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["where", "dws"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    candidates = [s.strip() for s in result.stdout.split("\n") if s.strip()]
                    for c in candidates:
                        if c.endswith(".cmd"):
                            found = c
                            break
                    if not found and candidates:
                        found = candidates[0]
            else:
                result = subprocess.run(
                    ["which", "dws"],
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
        candidates = ["dws"]
        if sys.platform == "win32":
            candidates.insert(0, "dws.cmd")
        for name in candidates:
            try:
                subprocess.run([name, "--help"], capture_output=True, timeout=5)
                self._binary = name
                return self._binary
            except FileNotFoundError:
                continue
        return None

    async def ensure_installed(self) -> bool:
        """Auto-install dws via ``npm install -g dingtalk-workspace-cli``.

        Returns ``True`` if the binary is available (before or after install).
        """
        if self._find_binary():
            return True

        sys.stderr.write("[DingTalkCLI] dws not found, installing via npm...\n")
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", self.NPM_PACKAGE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")
            sys.stderr.write(f"[DingTalkCLI] npm install failed: {err_text}\n")
            return False

        found = self._find_binary() is not None
        if found:
            sys.stderr.write("[DingTalkCLI] dws installed successfully\n")
        return found

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    async def is_authenticated(self) -> bool:
        """Check dws auth status via ``dws auth status --format json``."""
        result = await self._run(["auth", "status", "--format", "json"])
        return bool(result.get("authenticated"))

    # ------------------------------------------------------------------
    # Subprocess runner
    # ------------------------------------------------------------------

    async def _run(self, args: list[str]) -> dict:
        """Run *args* as ``dws <args>``, parse stdout as JSON.

        If ``client_id`` was provided at construction time, ``--client-id``
        and ``--client-secret`` are prepended so every CLI call carries
        credentials — no need for the user to ``dws auth login`` first.

        Returns a dict.  On success the dict contains at least ``"ok": True``.
        On failure it contains ``"ok": False``, ``"error"``, and
        optionally ``"returncode"``.
        """
        binary = self._find_binary()
        if not binary:
            return {"ok": False, "error": "dws not installed"}

        full_args = args[:]
        if self._client_id and self._client_secret:
            full_args = [
                "--client-id", self._client_id,
                "--client-secret", self._client_secret,
            ] + full_args

        proc = await asyncio.create_subprocess_exec(
            binary, *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            if out.startswith("{"):
                try:
                    result = json.loads(out)
                    result.setdefault("ok", False)
                    return result
                except json.JSONDecodeError:
                    pass
            return {"ok": False, "error": err or out, "returncode": proc.returncode}

        # Parse JSON response (dws defaults to JSON output)
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

        return {"ok": True, "text": out}

    # ------------------------------------------------------------------
    # Device auth flow (QR login)
    #
    # dws auth login --device uses box-drawing characters and ANSI escape
    # sequences that require a PTY.  On Windows we use ``winpty`` (ConPTY)
    # to provide the pseudo-terminal.  Once running:
    #   1. Read output until we extract the verification URI
    #   2. Return the QR code immediately
    #   3. Poll ``dws auth status`` (a lightweight CLI command that works
    #      without PTY) until auth completes
    # ------------------------------------------------------------------

    async def get_qrcode(self) -> dict:
        """Initiate dws device-authorization flow via winpty ConPTY.

        Returns a dict compatible with the adapter's ``get_qrcode`` contract:
            ``qrcode_url`` — base64 PNG data URI of the QR code
            ``qrcode_id``  — user_code (used for polling reference)
            ``user_code``  — human-readable code
        """
        if self._auth_proc is not None:
            await self._cleanup_auth()

        if not await self.ensure_installed():
            return {"error": "dws not installed"}

        # Run dws in a winpty PTY (required for its isatty() check)
        try:
            import winpty as winpty_mod
        except ImportError:
            return {"error": "winpty not installed (pip install pywinpty)"}

        proc, verification_uri, user_code = await asyncio.to_thread(
            self._start_dws_auth_sync
        )
        if not verification_uri:
            try:
                proc.terminate()
                proc.close()
            except Exception:
                pass
            return {"error": "Could not parse verification URI from dws output"}

        # Store the PTY process for polling
        self._auth_proc = proc
        self._auth_user_code = user_code

        # Render QR code
        try:
            import qrcode as qrcode_lib
            import io
            import base64
            img = qrcode_lib.make(verification_uri)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            qr_data_uri = verification_uri

        return {
            "qrcode_url": qr_data_uri,
            "qrcode_id": user_code or "",
            "user_code": user_code or "",
        }

    def _start_dws_auth_sync(self) -> tuple[Any, str, str]:
        """Synchronous: spawn dws via winpty PTY and extract the verification URI.

        Runs in a thread via ``asyncio.to_thread`` so it doesn't block the
        event loop.

        Returns ``(winpty_process, verification_uri, user_code)``.
        """
        import winpty as winpty_mod

        binary = self._find_binary() or "dws.cmd"
        proc = winpty_mod.PtyProcess.spawn([binary, "auth", "login", "--device"])

        verification_uri = ""
        user_code = ""
        output = ""
        deadline = time.time() + 10

        while time.time() < deadline:
            try:
                data = proc.read()
                if data:
                    output += data
                    # Check the latest chunk for the URI
                    found_uri, found_code = self._parse_auth_output(output)
                    if found_uri:
                        verification_uri = found_uri
                    if found_code:
                        user_code = found_code
                    if verification_uri:
                        break
            except Exception:
                break
            # Non-blocking: small sleep to avoid busy-wait
            time.sleep(0.1)

        return proc, verification_uri, user_code

    def _parse_auth_output(self, text: str) -> tuple[str, str]:
        """Extract verification URI and user_code from raw dws output.

        Handles ANSI escape sequences and box-drawing characters.

        Returns ``(verification_uri, user_code)``.
        """
        # Strip ANSI escape sequences
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        # Also strip other control sequences like \x1b]
        clean = re.sub(r'\x1b\][^\x1b]*\x1b\\', '', clean)
        clean = re.sub(r'\x1b\[?[0-9]*[hl]', '', clean)

        uri = ""
        code = ""

        for line in clean.split("\n"):
            line = line.strip()

            # "https://login.dingtalk.com/oauth2/device/verify.htm?user_code=XYZ"
            if "login.dingtalk.com" in line and "user_code=" in line:
                m = re.search(r'https?://[^\s>]+', line)
                if m:
                    uri = m.group(0)

            # "link: https://login.dingtalk.com/oauth2/device/verify.htm"
            if "link:" in line and "login.dingtalk.com" in line and not uri:
                m = re.search(r'https?://[^\s>]+', line)
                if m:
                    uri = m.group(0)

            # "authorization code: XXXX-XXXX"
            if "authorization code:" in line and not code:
                m = re.search(r'([A-Z0-9]+-[A-Z0-9]+)', line)
                if m:
                    code = m.group(1)

        return uri, code

    async def poll_qrcode_status(self, qrcode_id: str) -> dict:
        """Poll device-authorization status.

        Checks whether the dws PTY process is still running.  Once it
        exits, queries ``dws auth status --format json`` to confirm.

        Returns a dict with ``"status"`` — one of:
            ``"waiting"``   — user hasn't authorised yet
            ``"confirmed"`` — authorisation complete
            ``"expired"``   — device code expired / process exited without auth
            ``"error"``     — unexpected failure
        """
        if self._auth_proc is not None:
            try:
                alive = self._auth_proc.isalive()
            except Exception:
                alive = False
            if alive:
                return {"status": "waiting"}

            # Process exited — clean up
            try:
                self._auth_proc.terminate()
                self._auth_proc.close()
            except Exception:
                pass
            self._auth_proc = None

        # Check dws auth status
        try:
            result = await self._run(["auth", "status", "--format", "json"])
            if result.get("authenticated"):
                return {"status": "confirmed"}
            return {"status": "expired", "msg": "Authorization not completed"}
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    async def _cleanup_auth(self):
        """Kill the auth PTY process if still alive."""
        if self._auth_proc is not None:
            try:
                if self._auth_proc.isalive():
                    self._auth_proc.terminate()
                self._auth_proc.close()
            except Exception:
                pass
            self._auth_proc = None

    # ------------------------------------------------------------------
    # Domain operations — Chat / IM
    # ------------------------------------------------------------------

    async def send_message(self, user_id: str, text: str, title: str = "") -> dict:
        """Send a DingTalk message to a user."""
        args = [
            "chat", "message", "send",
            "--user", user_id,
            "--text", text,
            "--title", title or "OAA",
            "--format", "json",
        ]
        return await self._run(args)

    async def send_group_message(self, group_id: str, text: str, title: str = "") -> dict:
        """Send a message to a group conversation."""
        args = [
            "chat", "message", "send",
            "--group", group_id,
            "--text", text,
            "--title", title or "OAA",
            "--format", "json",
        ]
        return await self._run(args)

    async def chat_search(self, query: str, cursor: str = "") -> dict:
        """Search conversations by name."""
        args = ["chat", "search", "--query", query, "--format", "json"]
        if cursor:
            args.extend(["--cursor", cursor])
        return await self._run(args)

    async def chat_list(self, cursor: str = "", limit: int = 20) -> dict:
        """List top conversations."""
        args = ["chat", "list-top-conversations", "--format", "json"]
        if limit:
            args.extend(["--limit", str(limit)])
        if cursor:
            args.extend(["--cursor", cursor])
        return await self._run(args)

    async def chat_history(self, group_id: str, limit: int = 20, cursor: str = "") -> dict:
        """List recent messages in a group conversation."""
        args = ["chat", "message", "list",
                "--group", group_id,
                "--limit", str(limit),
                "--format", "json"]
        if cursor:
            args.extend(["--forward", cursor])
        return await self._run(args)

    async def chat_unread(self, limit: int = 20) -> dict:
        """List unread conversations."""
        args = ["chat", "message", "list-unread-conversations",
                "--format", "json"]
        if limit:
            args.extend(["--count", str(limit)])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Contact
    # ------------------------------------------------------------------

    async def search_user(self, query: str) -> dict:
        """Search users by keyword."""
        return await self._run([
            "contact", "user", "search",
            "--query", query,
            "--format", "json",
        ])

    async def get_user(self, user_id: str = "") -> dict:
        """Get user(s) by ID.  Omit *user_id* for self; comma-separate for batch."""
        if user_id:
            return await self._run([
                "contact", "user", "get",
                "--user-id", user_id,
                "--format", "json",
            ])
        return await self._run(["contact", "user", "get-self", "--format", "json"])

    async def dept_list(self, dept_id: str = "") -> dict:
        """Search departments by keyword (dws uses search not list)."""
        args = ["contact", "dept", "search", "--format", "json"]
        if dept_id:
            args.extend(["--query", dept_id])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    async def calendar_list(self, cursor: str = "", limit: int = 50) -> dict:
        """List calendar events."""
        args = ["calendar", "event", "list", "--format", "json"]
        # dws calendar event list only supports --start and --end; no pagination
        return await self._run(args)

    async def calendar_create(
        self, summary: str, start_time: str, end_time: str,
        description: str = "", attendees: Optional[list[str]] = None,
    ) -> dict:
        """Create a calendar event.

        *start_time* and *end_time* should be ISO 8601 strings.
        """
        args = [
            "calendar", "event", "create",
            "--title", summary,
            "--start", start_time,
            "--end", end_time,
            "--format", "json",
        ]
        if description:
            args.extend(["--desc", description])
        if attendees:
            args.extend(["--attendees", ",".join(attendees)])
        return await self._run(args)

    async def calendar_get(self, event_id: str) -> dict:
        """Get a single calendar event."""
        return await self._run([
            "calendar", "event", "get",
            "--event-id", event_id,
            "--format", "json",
        ])

    # ------------------------------------------------------------------
    # Todo / Task
    # ------------------------------------------------------------------

    async def todo_list(self, cursor: str = "", limit: int = 50) -> dict:
        """List todo tasks."""
        args = ["todo", "task", "list", "--format", "json"]
        args.extend(["--page", cursor or "1"])
        if limit:
            args.extend(["--size", str(limit)])
        return await self._run(args)

    async def todo_create(self, subject: str, description: str = "",
                          due_time: str = "", executor_ids: Optional[list[str]] = None) -> dict:
        """Create a todo task."""
        args = ["todo", "task", "create",
                "--title", subject,
                "--format", "json"]
        if due_time:
            args.extend(["--due", due_time])
        if executor_ids:
            args.extend(["--executors", ",".join(executor_ids)])
        return await self._run(args)

    async def todo_get(self, task_id: str) -> dict:
        """Get a single todo task."""
        return await self._run([
            "todo", "task", "get",
            "--task-id", task_id,
            "--format", "json",
        ])

    # ------------------------------------------------------------------
    # Docs
    # ------------------------------------------------------------------

    async def doc_search(self, query: str, cursor: str = "", limit: int = 20) -> dict:
        """Search documents by keyword."""
        args = ["doc", "search", "--query", query, "--format", "json"]
        if cursor:
            args.extend(["--page-token", cursor])
        if limit:
            args.extend(["--page-size", str(limit)])
        return await self._run(args)

    async def doc_read(self, doc_id: str) -> dict:
        """Read document content by node ID."""
        return await self._run([
            "doc", "read", "--doc-id", doc_id, "--format", "json",
        ])

    async def doc_create(self, title: str, content: str = "", parent_id: str = "") -> dict:
        """Create a new document."""
        args = ["doc", "create", "--name", title, "--format", "json"]
        if content:
            args.extend(["--markdown", content])
        if parent_id:
            args.extend(["--folder", parent_id])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Drive
    # ------------------------------------------------------------------

    async def drive_list(self, parent_id: str = "", cursor: str = "", limit: int = 50) -> dict:
        """List files in a drive folder (root if *parent_id* is empty)."""
        args = ["drive", "list", "--format", "json"]
        if parent_id:
            args.extend(["--parent-id", parent_id])
        if cursor:
            args.extend(["--next-token", cursor])
        if limit:
            args.extend(["--max", str(limit)])
        return await self._run(args)

    async def drive_upload(self, local_path: str, parent_id: str = "") -> dict:
        """Upload a local file to Drive."""
        args = ["drive", "upload-info", "--format", "json"]
        if os.path.exists(local_path):
            args.extend(["--file-name", os.path.basename(local_path)])
            args.extend(["--file-size", str(os.path.getsize(local_path))])
        if parent_id:
            args.extend(["--parent-id", parent_id])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Sheet
    # ------------------------------------------------------------------

    async def sheet_info(self, workbook_id: str, sheet_id: str = "") -> dict:
        """Get sheet info (workbook metadata and sheets list)."""
        args = ["sheet", "info", "--node", workbook_id, "--format", "json"]
        if sheet_id:
            args.extend(["--sheet-id", sheet_id])
        return await self._run(args)

    async def sheet_create(self, title: str, folder: str = "", workspace: str = "") -> dict:
        """Create a new spreadsheet."""
        args = ["sheet", "create", "--name", title, "--format", "json"]
        if folder:
            args.extend(["--folder", folder])
        if workspace:
            args.extend(["--workspace", workspace])
        return await self._run(args)

    async def sheet_list(self, node: str) -> dict:
        """List all worksheets in a spreadsheet."""
        return await self._run([
            "sheet", "list", "--node", node, "--format", "json",
        ])

    async def sheet_append(self, node: str, sheet_id: str, values: str) -> dict:
        """Append rows to a worksheet."""
        return await self._run([
            "sheet", "append", "--node", node, "--sheet-id", sheet_id,
            "--values", values, "--format", "json",
        ])

    async def sheet_read(self, node: str, sheet_id: str, range_str: str = "") -> dict:
        """Read cell values from a worksheet."""
        args = ["sheet", "range", "read", "--node", node, "--sheet-id", sheet_id, "--format", "json"]
        if range_str:
            args.extend(["--range", range_str])
        return await self._run(args)

    # ------------------------------------------------------------------
    # AI Table (aitable)
    # ------------------------------------------------------------------

    async def aitable_base_create(self, name: str, template_id: str = "") -> dict:
        """Create an AI table (多维表) base."""
        args = ["aitable", "base", "create", "--name", name, "--format", "json"]
        if template_id:
            args.extend(["--template-id", template_id])
        return await self._run(args)

    async def aitable_base_list(self, cursor: str = "", limit: int = 20) -> dict:
        """List AI table bases."""
        args = ["aitable", "base", "list", "--format", "json"]
        if cursor:
            args.extend(["--cursor", cursor])
        if limit:
            args.extend(["--limit", str(limit)])
        return await self._run(args)

    async def aitable_table_create(self, base_id: str, name: str, fields: str = "") -> dict:
        """Create a data table in an AI table base."""
        args = ["aitable", "table", "create", "--base-id", base_id, "--name", name, "--format", "json"]
        if fields:
            args.extend(["--fields", fields])
        return await self._run(args)

    async def aitable_record_create(self, base_id: str, table_id: str, records: str) -> dict:
        """Add records to an AI table."""
        args = [
            "aitable", "record", "create",
            "--base-id", base_id, "--table-id", table_id,
            "--records", records, "--format", "json",
        ]
        return await self._run(args)

    async def aitable_record_query(self, base_id: str, table_id: str,
                                    limit: int = 100, cursor: str = "") -> dict:
        """Query records from an AI table."""
        args = [
            "aitable", "record", "query",
            "--base-id", base_id, "--table-id", table_id,
            "--format", "json",
        ]
        if limit:
            args.extend(["--limit", str(limit)])
        if cursor:
            args.extend(["--cursor", cursor])
        return await self._run(args)

    # ------------------------------------------------------------------
    # Wiki
    # ------------------------------------------------------------------

    async def wiki_search(self, query: str, cursor: str = "", limit: int = 20) -> dict:
        """Search wiki by keyword."""
        args = ["wiki", "search", "--query", query, "--format", "json"]
        if cursor:
            args.extend(["--cursor", cursor])
        if limit:
            args.extend(["--limit", str(limit)])
        return await self._run(args)

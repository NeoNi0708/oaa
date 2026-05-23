"""wechat-cli integration — local WeChat data query tools."""
import asyncio
import os
import subprocess
import sys
from typing import Optional


class WeChatCLI:
    def __init__(self, cli_path: str = "", decrypted_dir: str = ""):
        self._binary: Optional[str] = None
        self._binary_prefix: list[str] = []  # extra args before the command (for python -m wechat_cli)
        self.cli_path = cli_path or self._find_cli()
        self.decrypted_dir = decrypted_dir
        self._last_error: str = ""

    @property
    def last_error(self) -> str:
        """Last error message from binary discovery."""
        return self._last_error

    def _find_cli(self) -> str:
        """Platform-aware binary discovery for wechat-cli.

        On Windows, npm global install creates ``wechat-cli.cmd`` (a batch
        wrapper). On Unix, it creates an extensionless script.  Falls back
        through bundled dir, PATH, npm global prefix, and common user-local locations.
        """
        if self._binary:
            return self._binary

        binary_name = "wechat-cli"
        found: Optional[str] = None
        ext = ".cmd" if sys.platform == "win32" else ""

        # --- 0. pip-installed wechat-cli (Python package) ---
        import os as _os
        try:
            _pip_path = subprocess.run(
                [sys.executable, "-m", "wechat_cli", "--help"],
                capture_output=True, timeout=5,
            )
            if _pip_path.returncode == 0:
                self._binary = sys.executable
                self._binary_prefix = ["-m", "wechat_cli"]
                return self._binary
        except Exception:
            pass

        # --- 1. PATH lookup (platform-aware) ---
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["where", binary_name],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    candidates = [s.strip() for s in result.stdout.split("\n") if s.strip()]
                    # prefer .cmd batch wrapper on Windows
                    for c in candidates:
                        if c.endswith(".cmd"):
                            found = c
                            break
                    if not found and candidates:
                        found = candidates[0]
            else:
                result = subprocess.run(
                    ["which", binary_name],
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

        # --- 2. npm global prefix ---
        try:
            npm_prefix = subprocess.run(
                ["npm", "prefix", "-g"],
                capture_output=True, text=True, timeout=10,
            )
            if npm_prefix.returncode == 0:
                prefix_dir = npm_prefix.stdout.strip()
                if sys.platform == "win32":
                    npm_bin = os.path.join(prefix_dir, f"{binary_name}.cmd")
                else:
                    npm_bin = os.path.join(prefix_dir, "bin", binary_name)
                if os.path.exists(npm_bin):
                    self._binary = npm_bin
                    return self._binary
        except Exception:
            pass

        # --- 3. Common user-local paths ---
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            local_candidates = [
                os.path.expanduser(f"~/.local/bin/{binary_name}.exe"),
                os.path.expanduser(f"~/.local/bin/{binary_name}.cmd"),
                os.path.join(appdata, "npm", f"{binary_name}.cmd") if appdata else "",
            ]
        else:
            local_candidates = [
                os.path.expanduser(f"~/.local/bin/{binary_name}"),
                f"/usr/local/bin/{binary_name}",
            ]

        for c in local_candidates:
            if not c:
                continue
            try:
                if os.path.exists(c):
                    subprocess.run([c, "--help"], capture_output=True, timeout=5)
                    self._binary = c
                    return self._binary
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        # --- 4. Last resort: bare name (hoping it's on PATH) ---
        fallback_names = [binary_name]
        if sys.platform == "win32":
            fallback_names.insert(0, f"{binary_name}.cmd")
        for name in fallback_names:
            try:
                subprocess.run([name, "--help"], capture_output=True, timeout=5)
                self._binary = name
                return self._binary
            except FileNotFoundError:
                continue

        self._last_error = (
            f"找不到 wechat-cli 二进制（操作系统: {sys.platform}）。"
            f"请运行 npm install -g wechat-cli 安装。"
        )
        return binary_name

    async def _run(self, *args) -> str:
        cmd = [self.cli_path] + self._binary_prefix + list(args)
        if self.decrypted_dir:
            cmd.extend(["--decrypted-dir", self.decrypted_dir])
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Error: {stderr.decode('utf-8', errors='replace')}"
        return stdout.decode("utf-8", errors="replace")

    async def sessions(self, limit: int = 20) -> str:
        return await self._run("sessions", "--limit", str(limit), "--format", "json")

    async def history(self, chat_name: str, limit: int = 20) -> str:
        return await self._run("history", chat_name, "--limit", str(limit), "--format", "json")

    async def search(self, keyword: str, chat: str = "", limit: int = 20) -> str:
        args = ["search", keyword, "--limit", str(limit), "--format", "json"]
        if chat:
            args.extend(["--chat", chat])
        return await self._run(*args)

    async def contacts(self, query: str = "") -> str:
        args = ["contacts", "--format", "json"]
        if query:
            args.extend(["--query", query])
        return await self._run(*args)

    async def unread(self, limit: int = 20) -> str:
        return await self._run("unread", "--limit", str(limit), "--format", "json")

    async def stats(self, days: int = 7) -> str:
        return await self._run("stats", "--days", str(days), "--format", "json")

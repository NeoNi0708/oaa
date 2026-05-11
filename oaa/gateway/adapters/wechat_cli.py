"""wechat-cli integration — local WeChat data query tools."""
import asyncio
import os
import subprocess


class WeChatCLI:
    def __init__(self, cli_path: str = "", decrypted_dir: str = ""):
        self.cli_path = cli_path or self._find_cli()
        self.decrypted_dir = decrypted_dir

    def _find_cli(self) -> str:
        # Search common locations for wechat-cli executable
        candidates = [
            os.path.expanduser("~/.local/bin/wechat-cli"),
            "/usr/local/bin/wechat-cli",
            "wechat-cli",
        ]
        for c in candidates:
            try:
                subprocess.run([c, "--help"], capture_output=True, timeout=5)
                return c
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return "wechat-cli"

    async def _run(self, *args) -> str:
        cmd = [self.cli_path] + list(args)
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

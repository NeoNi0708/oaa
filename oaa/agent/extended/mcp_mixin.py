"""MCP mixin — Model Context Protocol server management tools."""
import asyncio
import json
import os
from ..tool_decorator import agent_tool


class McpMixin:
    """MCP (Model Context Protocol) server management tools."""

    async def do_mcp_install(self, args: dict) -> dict:
        """Install an MCP server (npm package) and register it for use.

        Installs the package via npm (optionally pinned to a version),
        then registers it in the MCP config file so tools can discover it.
        """
        package = args.get("package", "")
        name = args.get("name", "") or package
        version = args.get("version", "latest")
        command = args.get("command", "npx")
        args_list = args.get("args", [])
        env_vars = args.get("env", {})

        if not package:
            return {"status": "error", "msg": "package name is required"}

        if not await self._confirm("mcp_install", f"Install MCP server: {package}@{version}"):
            return {"status": "error", "msg": "MCP install not permitted"}

        # npm install the package globally
        full_pkg = f"{package}@{version}" if version != "latest" else package
        npm_proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", full_pkg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await npm_proc.communicate()
        if npm_proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return {"status": "error", "msg": f"npm install failed: {err}"}

        # Register in MCP config
        mcp_config = self._load_mcp_config()
        mcp_config["servers"][name] = {
            "command": command,
            "args": args_list,
            "env": env_vars,
        }
        self._save_mcp_config(mcp_config)

        return {
            "status": "success",
            "msg": f"MCP server '{name}' installed and registered",
            "package": full_pkg,
        }

    async def do_mcp_list(self, args: dict) -> dict:
        """List installed/configured MCP servers."""
        mcp_config = self._load_mcp_config()
        servers = mcp_config.get("servers", {})
        if not servers:
            return {"status": "success", "servers": {}, "count": 0, "msg": "No MCP servers configured"}
        result = {}
        for name, cfg in servers.items():
            result[name] = {
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "env_count": len(cfg.get("env", {})),
            }
        return {"status": "success", "servers": result, "count": len(result)}

    async def do_mcp_remove(self, args: dict) -> dict:
        """Remove an MCP server configuration."""
        name = args.get("name", "")
        if not name:
            return {"status": "error", "msg": "name is required"}

        mcp_config = self._load_mcp_config()
        if name not in mcp_config.get("servers", {}):
            return {"status": "error", "msg": f"MCP server '{name}' not found"}

        del mcp_config["servers"][name]
        self._save_mcp_config(mcp_config)

        return {"status": "success", "msg": f"MCP server '{name}' removed"}

    def _load_mcp_config(self) -> dict:
        """Load the MCP config file (creates default if missing)."""
        path = os.path.join(self.data_dir, "mcp_servers.json")
        if not os.path.exists(path):
            return {"servers": {}}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"servers": {}}

    def _save_mcp_config(self, config: dict):
        """Persist MCP config to disk."""
        path = os.path.join(self.data_dir, "mcp_servers.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

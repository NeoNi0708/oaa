"""OAA data directory initialization — creates all subdirectories and identity files."""
import os
import subprocess
import sys
from pathlib import Path

from .logging_config import get_logger

logger = get_logger("init")

REQUIRED_DIRS = [
    "workspace/documents",
    "workspace/clients",
    "workspace/orders",
    "workspace/leads",
    "workspace/vendors",
    "workspace/reports",
    "workspace/finance",
    "workspace/rfq",
    "workspace/plans",
    "memory",
    "db",
    "skills/外贸业务核心",
    "skills/办公文档",
    "skills/通信消息",
    "skills/系统与自进化",
    "skills/user_evolved",
]

IDENTITY_FILES = {
    "IDENTITY.md": "# 二愣\n\n- **称呼**: 二愣\n- **用户称呼**: 恒总\n- **核心信念**: 帮助恒总把生意做得更顺、更赚钱、更省心\n",
    "SOUL.md": "# 工作哲学\n\n- **靠谱**: 说到做到，不遗漏\n- **主动**: 看到问题就想动手解决，不需要等指令\n- **尊重**: 不确定先请示\n- **反思**: 每次任务后总结改进\n",
    "USER.md": "# 用户信息\n\n- **称呼**: 恒总\n- **公司**: 联轴器出口贸易（一人公司）\n- **偏好**: 待学习\n",
    "BOOTSTRAP.md": "# 二愣启动自我介绍\n\n恒总您好，我是二愣，您的 AI 业务助手。\n我可以帮您处理报价、跟单、搜客户、写邮件等外贸业务。\n有什么需要帮忙的吗？\n",
    "AGENTS.md": "# 工作边界\n\n- 所有操作在数据目录内进行\n- 发消息/邮件前需用户确认\n- 不确定时主动询问\n- 保护用户隐私和商业数据\n",
    "HEARTBEAT.md": "# 健康检查\n\n- 定期检查配置有效性\n- 监控消息通道连接状态\n- 报告异常情况\n",
}


def ensure_data_dir(data_dir: str) -> bool:
    """Create data directory structure if not exists. Returns True if first run."""
    data_dir = os.path.abspath(data_dir)
    first_run = not os.path.exists(os.path.join(data_dir, "db", "oaa.db"))
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    for subdir in REQUIRED_DIRS:
        Path(data_dir, subdir).mkdir(parents=True, exist_ok=True)
    # Write identity files
    memory_dir = Path(data_dir, "memory")
    for name, content in IDENTITY_FILES.items():
        path = memory_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    return first_run


def _find_node(cli_dir: Path) -> tuple[str | None, str | None]:
    """Return (node_exe, npm_cmd) for the best available Node.js.

    1. Bundled portable Node.js (cli/node/node.exe)
    2. System-installed Node.js
    """
    bundled_node = cli_dir / "node" / "node.exe"
    if bundled_node.is_file():
        npm_cli = cli_dir / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if npm_cli.is_file():
            return str(bundled_node), str(npm_cli)
    return None, None


def ensure_bundled_cli(data_dir: str) -> bool:
    """Auto-install bundled CLI tools (wechat-cli, lark-cli, dws).

    Called once during startup.  If ``cli/node_modules`` is missing,
    runs ``npm install`` in the bundled CLI directory using the bundled
    Node.js portable (or system node if unavailable).  Returns True
    on first install, False if already present.
    """
    # Resolve the cli/ directory relative to oaa package root
    pkg_root = Path(__file__).resolve().parent.parent  # oaa/oaa/ → oaa/
    cli_dir = pkg_root / "cli"
    if not (cli_dir / "package.json").exists():
        return False

    node_modules = cli_dir / "node_modules"
    if node_modules.is_dir():
        return False

    node_exe, npm_js = _find_node(cli_dir)
    if node_exe and npm_js:
        cmd = [node_exe, npm_js, "install"]
    else:
        cmd = ["npm", "install"]

    sys.stderr.write("[OAA] Installing bundled CLI tools (wechat-cli, lark-cli, dws)...\n")
    env = os.environ.copy()
    # Suppress npm funding/update nag
    env["NO_UPDATE_NOTIFIER"] = "1"
    try:
        subprocess.run(
            cmd,
            cwd=str(cli_dir),
            check=True,
            capture_output=True,
            timeout=120,
            env=env,
        )
        sys.stderr.write("[OAA] Bundled CLI tools installed.\n")
        return True
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"[OAA] npm install failed: {exc.stderr.decode().strip()[:200]}\n")
    except FileNotFoundError:
        sys.stderr.write("[OAA] Node.js not found — install Node.js or place portable in cli/node/\n")
    except Exception as exc:
        sys.stderr.write(f"[OAA] CLI install failed: {exc}\n")
    return False


def load_identity(data_dir: str) -> dict:
    """Load all identity files into a single dict."""
    memory_dir = Path(data_dir, "memory")
    result = {}
    for name in IDENTITY_FILES:
        path = memory_dir / name
        if path.exists():
            result[name.replace(".md", "").lower()] = path.read_text(encoding="utf-8")
    return result

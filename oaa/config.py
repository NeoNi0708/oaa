"""Configuration management — JSON file backed with env var override."""
import json
import os
from dataclasses import asdict, dataclass, field

from .logging_config import get_logger

logger = get_logger("config")

DEFAULT_CONFIG_PATH = os.path.expanduser("~/OAA/config.json")
_LEGACY_CONFIG_PATH = os.path.expanduser("~/.oaa/config.json")


@dataclass
class ModelConfig:
    provider: str = ""
    plan: str = "api"           # 'api' | 'token' | 'coding'
    api_format: str = "openai"  # 'openai' | 'anthropic'
    base_url: str = ""
    api_key: str = ""
    model_id: str = ""
    max_tokens: int = 8192
    temperature: float = 0.7

    @property
    def is_valid(self) -> bool:
        return bool(self.base_url and self.api_key and self.model_id)


@dataclass
class WeChatConfig:
    enabled: bool = False
    iLink_token: str = ""        # persisted session token
    iLink_bot_id: str = ""
    ilink_user_id: str = ""      # bot owner's wxid, used for proactive messaging
    base_url: str = ""           # iLink API base URL from QR scan
    wechat_cli_path: str = ""    # path to wechat-cli


@dataclass
class DingTalkConfig:
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""


@dataclass
class SearchConfig:
    tavily_api_key: str = ""
    exa_api_key: str = ""
    anysearch_api_key: str = ""


@dataclass
class AppConfig:
    DEFAULT_CONFIG_PATH: str = DEFAULT_CONFIG_PATH
    data_dir: str = os.path.expanduser("~/OAA")
    model: ModelConfig = field(default_factory=ModelConfig)
    models: dict = field(default_factory=dict)  # per-provider: {provider: {api_key, model_id, base_url}}
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    permissions: dict = field(default_factory=lambda: {
        "blacklist_paths": [],
        "require_confirm": ["email_send", "wechat_send"],
        "permission_level": "auto",
    })

    def save(self, path: str = ""):
        path = path or DEFAULT_CONFIG_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def _migrate_models(cls, raw: dict) -> dict:
        """Migrate old ``{provider: {api_key, ...}}`` → ``{provider: [{name, ...}, ...]}``.

        Old single-dict entries are wrapped in a list. Already-list entries pass through.
        """
        migrated = {}
        for prov, val in raw.items():
            if isinstance(val, dict):
                entry = {"name": "", **{k: v for k, v in val.items() if k in ("api_key", "model_id", "base_url")}}
                if not entry.get("name"):
                    entry["name"] = val.get("model_id", prov)
                migrated[prov] = [entry]
            elif isinstance(val, list):
                migrated[prov] = val
            else:
                migrated[prov] = []
        return migrated

    @classmethod
    def load(cls, path: str = "") -> "AppConfig":
        path = path or DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            # Fall back to legacy config location
            legacy = _LEGACY_CONFIG_PATH
            if legacy != path and os.path.exists(legacy):
                logger.info("Migrating config from %s to %s", legacy, path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(legacy, encoding="utf-8") as f:
                    data = json.load(f)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.remove(legacy)
                # Remove empty old dir
                old_dir = os.path.dirname(legacy)
                try:
                    os.rmdir(old_dir)
                except OSError:
                    pass
            else:
                return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        model = ModelConfig(**data.get("model", {}))
        models = cls._migrate_models(data.get("models", {}))
        wechat = WeChatConfig(**data.get("wechat", {}))
        dingtalk = DingTalkConfig(**data.get("dingtalk", {}))
        feishu = FeishuConfig(**data.get("feishu", {}))
        search = SearchConfig(**data.get("search", {}))
        perms = data.get("permissions", {})
        # Normalize legacy string format to dict
        if isinstance(perms, str):
            perms = {"permission_level": perms, "blacklist_paths": [], "require_confirm": ["email_send", "wechat_send"]}
        return cls(
            data_dir=data.get("data_dir") or cls.data_dir,
            model=model, models=models,
            wechat=wechat,
            dingtalk=dingtalk, feishu=feishu,
            search=search,
            permissions=perms,
        )

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
class AppConfig:
    DEFAULT_CONFIG_PATH: str = DEFAULT_CONFIG_PATH
    data_dir: str = os.path.expanduser("~/OAA")
    model: ModelConfig = field(default_factory=ModelConfig)
    models: dict = field(default_factory=dict)  # per-provider: {provider: {api_key, model_id, base_url}}
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    permissions: dict = field(default_factory=lambda: {
        "blacklist_paths": [],
        "require_confirm": ["email_send", "wechat_send"],
    })

    def save(self, path: str = ""):
        path = path or DEFAULT_CONFIG_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

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
        models = data.get("models", {})
        wechat = WeChatConfig(**data.get("wechat", {}))
        dingtalk = DingTalkConfig(**data.get("dingtalk", {}))
        feishu = FeishuConfig(**data.get("feishu", {}))
        perms = data.get("permissions", {})
        return cls(
            data_dir=data.get("data_dir", cls.data_dir),
            model=model, models=models,
            wechat=wechat,
            dingtalk=dingtalk, feishu=feishu,
            permissions=perms,
        )

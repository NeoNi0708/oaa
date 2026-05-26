"""Configuration management — JSON file backed with env var override."""
import asyncio
import json
import os
from dataclasses import asdict, dataclass, field

from .async_io import async_write_json
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
class ImageGenConfig:
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://token.sensenova.cn/v1"
    model_id: str = "sensenova-u1-fast"


@dataclass
class LocalModelConfig:
    enabled: bool = False
    model_path: str = ""
    port: int = 8080
    context_size: int = 32768
    gpu_layers: int = -1
    confidence_threshold: float = 0.3
    fallback_on_failure: bool = True
    keywords_local: list = field(default_factory=lambda: [
        "翻译", "总结", "提取", "分类", "整理",
        "编写", "生成", "列出", "列举", "改写",
        "translate", "summarize", "extract", "list",
    ])
    keywords_cloud_analysis: list = field(default_factory=lambda: [
        "分析", "对比", "评估", "预测", "推理",
        "优化", "诊断", "investigate", "analyze",
    ])
    keywords_cloud_creation: list = field(default_factory=lambda: [
        "创作", "设计", "策划", "制定", "撰写",
        "方案", "计划", "报告", "proposal",
    ])
    keywords_cloud_external: list = field(default_factory=lambda: [
        "汇率", "关税", "政策", "新闻", "天气",
        "股价", "搜索", "查询", "找一下",
    ])
    keywords_step: list = field(default_factory=lambda: [
        r"先.*再", r"首先.*然后", r"第一步.*第二步",
    ])
    local_calls: int = 0
    cloud_calls: int = 0
    tokens_saved: int = 0
    fallback_count: int = 0


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
    image_gen: ImageGenConfig = field(default_factory=ImageGenConfig)
    local_model: LocalModelConfig = field(default_factory=LocalModelConfig)
    permissions: dict = field(default_factory=lambda: {
        "blacklist_paths": [],
        "require_confirm": ["email_send", "wechat_send"],
        "permission_level": "auto",
    })

    @staticmethod
    def _redact(value: str) -> str:
        """Return a redacted version of a secret (show last 4 chars)."""
        if not value or len(value) < 8:
            return "********"
        return value[:4] + "****" + value[-4:]

    def to_redacted_dict(self) -> dict:
        """Return a copy of the config dict with credential fields masked."""
        data = asdict(self)
        # Redact LLM API keys
        if isinstance(data.get("model"), dict) and data["model"].get("api_key"):
            data["model"]["api_key"] = self._redact(data["model"]["api_key"])
        if isinstance(data.get("models"), dict):
            for prov, entries in data["models"].items():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and entry.get("api_key"):
                            entry["api_key"] = self._redact(entry["api_key"])
                elif isinstance(entries, dict) and entries.get("api_key"):
                    entries["api_key"] = self._redact(entries["api_key"])
        # Redact channel secrets
        if isinstance(data.get("wechat"), dict) and data["wechat"].get("iLink_token"):
            data["wechat"]["iLink_token"] = self._redact(data["wechat"]["iLink_token"])
        if isinstance(data.get("dingtalk"), dict):
            if data["dingtalk"].get("client_secret"):
                data["dingtalk"]["client_secret"] = self._redact(data["dingtalk"]["client_secret"])
            if data["dingtalk"].get("client_id"):
                data["dingtalk"]["client_id"] = self._redact(data["dingtalk"]["client_id"])
        if isinstance(data.get("feishu"), dict):
            if data["feishu"].get("app_secret"):
                data["feishu"]["app_secret"] = self._redact(data["feishu"]["app_secret"])
            if data["feishu"].get("app_id"):
                data["feishu"]["app_id"] = self._redact(data["feishu"]["app_id"])
        # Redact search API keys
        if isinstance(data.get("search"), dict):
            search_keys = ("tavily_api_key", "exa_api_key", "anysearch_api_key")
            for key in search_keys:
                if data["search"].get(key):
                    data["search"][key] = self._redact(data["search"][key])
        # Redact image_gen API key
        if isinstance(data.get("image_gen"), dict) and data["image_gen"].get("api_key"):
            data["image_gen"]["api_key"] = self._redact(data["image_gen"]["api_key"])
        return data

    async def save(self, path: str = ""):
        path = path or DEFAULT_CONFIG_PATH
        await async_write_json(path, asdict(self), indent=2, ensure_ascii=False)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Best-effort on Windows

    def _save_sync(self, path: str = ""):
        """Sync variant for CLI contexts (wizard)."""
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
        image_gen = ImageGenConfig(**data.get("image_gen", {}))
        local_model = LocalModelConfig(**data.get("local_model", {}))
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
            image_gen=image_gen,
            local_model=local_model,
            permissions=perms,
        )

"""First-run wizard — guides user through initial setup."""
import os

from .config import AppConfig, ModelConfig
from .init import ensure_data_dir
from .llm.presets import PROVIDER_PRESETS


class SetupWizard:
    def __init__(self):
        self.config = AppConfig()

    def run_text(self):
        """Text-based wizard for CLI fallback."""
        print("=" * 50)
        print("  OPC AI Assistant (OAA) — 首次启动向导")
        print("=" * 50)

        # Step 1: Data directory
        default_dir = os.path.expanduser("~/OAA")
        data_dir = input(f"\n[1/3] 数据目录 [{default_dir}]: ").strip() or default_dir
        self.config.data_dir = os.path.abspath(data_dir)
        first_run = ensure_data_dir(self.config.data_dir)
        print(f"  {'已创建' if first_run else '已存在'}: {self.config.data_dir}")

        # Step 2: Model configuration
        print("\n[2/3] 选择模型 Provider:")
        for i, p in enumerate(PROVIDER_PRESETS, 1):
            print(f"  {i}. {p['name']} — {p['note']}")
        choice = input(f"  请选择 (1-{len(PROVIDER_PRESETS)}, 默认 1): ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 0 < int(choice) <= len(PROVIDER_PRESETS) else 0
        preset = PROVIDER_PRESETS[idx]

        print(f"\n  已选: {preset['name']}")
        base_url = input(f"  Base URL [{preset['base_url']}]: ").strip() or preset['base_url']
        api_key = input("  API Key: ").strip()
        model_id = input(f"  Model ID [{preset['model_placeholder']}]: ").strip() or preset['model_placeholder']

        api_format = preset.get("api_format", "openai")

        self.config.model = ModelConfig(
            provider=preset["provider"],
            plan=preset.get("plan", "api"),
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
        )

        # Step 3: Save
        print("\n[3/3] 保存配置...")
        self.config.save()
        print("  配置已保存到 ~/.oaa/config.json")
        print("\n\U0001F389 设置完成！启动二愣中...")
        return self.config

"""Image generation mixin — SenseNova / OpenAI-compatible API."""
import os
import requests
import json
from ...logging_config import get_logger
from ..tool_decorator import agent_tool

logger = get_logger("agent.extended.image_gen")

DEFAULT_BASE_URL = "https://token.sensenova.cn/v1"
DEFAULT_MODEL = "sensenova-u1-fast"


class ImageGenMixin:
    """Mixin for AI image generation."""

    @agent_tool(
        name="generate_image",
        description="Generate an image using AI (sensenova-u1-fast / DALL-E compatible). "
                    "You can pass api_key directly, or it will use the config key from settings. "
                    "Prompts should be detailed for best results. "
                    "Returns a dict with image URL, local file path, and metadata."
    )
    async def do_generate_image(
        self,
        prompt: str,
        size: str = "1536x1024",
        n: int = 1,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ) -> dict:
        """Generate an image via SenseNova / OpenAI-compatible API.

        Args:
            prompt: Detailed image description in Chinese or English.
            size: Image dimensions (1024x1024, 1536x1024, 1024x1536, 2752x1536).
            n: Number of images to generate (1-4).
            api_key: Override API key. Falls back to config or preference.

        Returns:
            Dict with ``url``, ``local_path``, ``size``, ``model``.
        """
        if not prompt:
            return {"status": "error", "msg": "prompt is required"}

        # Resolve API key: passed -> config -> preference
        key = api_key
        if not key:
            key = getattr(self, "_image_gen_key", "")
        if not key:
            key = os.environ.get("SENSENOVA_API_KEY", "")
        if not key:
            # Try config
            try:
                cfg = getattr(self, "_config", None)
                if cfg and hasattr(cfg, "image_gen"):
                    key = cfg.image_gen.api_key
            except Exception:
                pass
        if not key:
            return {"status": "error", "msg": "API Key 未配置。请在设置页面→图片生成中配置，或传入 api_key 参数"}

        b_url = base_url or DEFAULT_BASE_URL
        mdl = model or DEFAULT_MODEL

        # Validate size
        valid_sizes = {"1024x1024", "1536x1024", "1024x1536", "2752x1536"}
        if size not in valid_sizes:
            return {"status": "error", "msg": f"不支持尺寸 {size}，支持: {', '.join(sorted(valid_sizes))}"}

        n = max(1, min(n, 4))

        try:
            resp = requests.post(
                f"{b_url.rstrip('/')}/images/generations",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": mdl,
                    "prompt": prompt,
                    "size": size,
                    "n": n,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            return {"status": "error", "msg": "图片生成请求超时，请稍后重试"}
        except requests.HTTPError as exc:
            body = ""
            try:
                body = exc.response.text[:300] if exc.response else ""
            except Exception:
                pass
            return {"status": "error", "msg": f"API 错误 ({exc.response.status_code if exc.response else '?'}): {body}"}
        except Exception as exc:
            return {"status": "error", "msg": f"请求失败: {exc}"}

        # Extract image URLs from response (OpenAI-compatible format)
        urls = []
        for item in data.get("data", []):
            url = item.get("url", "")
            if url:
                urls.append(url)

        if not urls:
            # Try alternative response field (b64_json)
            b64_data = data.get("data", [{}])[0].get("b64_json", "")
            if b64_data:
                urls.append(f"data:image/png;base64,{b64_data}")
            else:
                return {"status": "error", "msg": f"API 返回格式异常: {json.dumps(data)[:200]}"}

        # Download the first image
        local_path = ""
        try:
            from ..tools._core import OAA_ROOT
            workspace = os.path.join(str(OAA_ROOT), "workspace")
            os.makedirs(workspace, exist_ok=True)
            import hashlib
            safe_name = hashlib.md5(prompt.encode()).hexdigest()[:12]
            local_path = os.path.join(workspace, f"img_gen_{safe_name}.png")

            img_resp = requests.get(urls[0], timeout=30)
            img_resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(img_resp.content)
        except Exception as exc:
            logger.warning("Failed to download image: %s", exc)

        return {
            "status": "success",
            "url": urls[0],
            "local_path": local_path,
            "size": size,
            "model": mdl,
            "count": len(urls),
        }

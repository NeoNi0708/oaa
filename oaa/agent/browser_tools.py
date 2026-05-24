"""Browser tools — web fetching via HTTP, without requiring a browser extension."""
import asyncio

import requests
from bs4 import BeautifulSoup

from .handler import BaseHandler

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _simplify_html(html: str, max_chars: int = 8000) -> str:
    """Strip scripts, styles, hidden elements; return text-dense HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "meta", "link", "svg", "iframe"]):
            tag.decompose()
        body = soup.body or soup
        # Get text with block-level line breaks
        text = body.get_text(separator="\n", strip=True)
        # Collapse blank lines
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len("\n".join(lines)) > max_chars:
            return "\n".join(lines)[:max_chars] + "...[truncated]"
        return "\n".join(lines)
    except Exception:
        return html[:max_chars]


def _fetch_url(url: str, timeout: int = 10) -> dict:
    """Fetch a URL and return simplified text content."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            text = _simplify_html(resp.text)
        else:
            text = resp.text[:8000]
        return {
            "status": "success",
            "url": resp.url,
            "status_code": resp.status_code,
            "content": text,
        }
    except requests.Timeout:
        return {"status": "error", "msg": f"请求 {url} 超时"}
    except requests.ConnectionError:
        return {"status": "error", "msg": f"无法连接到 {url}，请检查网络或 URL"}
    except Exception as e:
        return {"status": "error", "msg": f"获取 {url} 失败: {e}"}


async def do_web_scan(args: dict) -> dict:
    """Fetch a URL and return simplified content."""
    url = args.get("url", "")
    if not url:
        return {"status": "error", "msg": "web_scan 需要 url 参数"}
    timeout = int(args.get("timeout", 10)) if args.get("timeout") else 10
    return await asyncio.to_thread(_fetch_url, url, timeout=timeout)


class BrowserTools(BaseHandler):
    """Handler that exposes web fetching tools to the agent loop."""

    @staticmethod
    async def do_web_scan(args: dict) -> dict:
        return await do_web_scan(args)

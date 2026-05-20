"""Unified AI search router — Tavily / Exa / AnySearch with auto-routing."""

import json
import logging
import re
from typing import Optional

import requests

from .handler import BaseHandler
from .tool_decorator import agent_tool

logger = logging.getLogger("agent.ai_search")

_HEADERS = {
    "User-Agent": "OAA-AiSearch/1.0",
    "Content-Type": "application/json",
}

_CHINESE_RE = re.compile(r"[一-鿿　-〿＀-￯]")


def _has_chinese(text: str) -> bool:
    """Check if text contains significant Chinese characters (>30%)."""
    if not text:
        return False
    chars = _CHINESE_RE.findall(text)
    return len(chars) / max(len(text), 1) > 0.3


def _auto_detect_intent(query: str) -> str:
    """Auto-detect search intent from query text."""
    q = query.lower()
    # Lead gen signals
    lead_keywords = [
        "company", "companies", "startup", "startups", "ceo", "founder", "email",
        "contact", "headquarter", "office", "员工", "公司", "企业", "创始人",
        "供应商", "客户", "潜在", "lead", "prospect", "vendor", "supplier",
    ]
    if any(kw in q for kw in lead_keywords):
        return "lead_gen"

    # Deep research signals
    deep_keywords = [
        "compare", "comparison", "analysis", "vs", "versus", "difference",
        "impact", "trend", "overview", "summary", "report", "research",
        "对比", "比较", "分析", "影响", "趋势", "报告", "综述",
    ]
    if any(kw in q for kw in deep_keywords):
        return "deep_research"

    return "general"


def _auto_detect_region(query: str) -> str:
    """Auto-detect desired search region from query."""
    if _has_chinese(query):
        return "cn"
    return "intl"


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def _call_tavily(query: str, api_key: str, max_results: int = 10) -> dict:
    """Call Tavily search API."""
    resp = requests.post(
        "https://api.tavily.com/search",
        headers=_HEADERS,
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    return {
        "engine": "tavily",
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0),
            }
            for r in results
        ],
        "total": len(results),
    }


def _call_exa(query: str, api_key: str, max_results: int = 10, mode: str = "auto") -> dict:
    """Call Exa search API.

    Modes: auto, instant, fast, deep, deep-reasoning.
    In ``deep`` mode, supports structured output for company/people data.
    """
    body: dict = {
        "query": query,
        "num_results": max_results,
        "type": "auto" if mode == "auto" else mode,
        "contents": {"highlights": True},
    }
    # Deep mode uses output_schema for structured extraction
    if mode in ("deep", "deep-reasoning"):
        body["contents"]["summary"] = {"query": f"Extract key information about: {query}"}

    resp = requests.post(
        "https://api.exa.ai/search",
        headers={"Authorization": f"Bearer {api_key}", **_HEADERS},
        json=body,
        timeout=30 if mode in ("deep", "deep-reasoning") else 15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])

    return {
        "engine": "exa",
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("highlights", [""])[0] if r.get("highlights") else r.get("text", ""),
                "score": r.get("score", 0),
                "source": r.get("source", ""),
                "published": r.get("published_date", ""),
            }
            for r in results
        ],
        "total": len(results),
    }


def _call_anysearch(query: str, api_key: str, max_results: int = 10,
                     region: str = "intl", domain: str = "") -> dict:
    """Call AnySearch API."""
    body: dict = {
        "query": query,
        "max_results": max_results,
        "zone": region,
    }
    if domain:
        body["domains"] = [domain]

    headers = _HEADERS.copy()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(
        "https://api.anysearch.com/v1/search",
        headers=headers,
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])

    return {
        "engine": "anysearch",
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content") or r.get("description", ""),
                "score": r.get("score", 0),
                "quality_score": r.get("quality_score"),
                "source": r.get("source", ""),
            }
            for r in results
        ],
        "total": len(results),
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_ROUTE_TABLE = {
    "lead_gen": {
        "engine": "exa",
        "mode": "auto",
        "reason": "Exa — 结构化输出 / 公司+人垂直索引，直接提取名称/联系方式",
    },
    "cn": {
        "engine": "anysearch",
        "region": "cn",
        "reason": "AnySearch — 支持中文互联网 / 国内站点覆盖",
    },
    "deep_research": {
        "engine": "exa",
        "mode": "deep",
        "reason": "Exa Deep — 多步推理+结构化输出，适合深度研究",
    },
}

_DEFAULT_ROUTE = {
    "engine": "tavily",
    "reason": "Tavily — 通用搜索最快（~180ms），覆盖全面",
}

_FALLBACK_CHAIN = {
    "tavily": ("exa", "auto"),
    "exa": ("anysearch", "intl"),
    "anysearch": ("tavily", None),
}


class AiSearchTools(BaseHandler):
    """Unified AI search tool — routes to Tavily / Exa / AnySearch automatically."""

    def __init__(self, tavily_api_key: str = "", exa_api_key: str = "",
                 anysearch_api_key: str = ""):
        self._keys = {
            "tavily": tavily_api_key,
            "exa": exa_api_key,
            "anysearch": anysearch_api_key,
        }

    # ------------------------------------------------------------------
    # Tool: ai_search
    # ------------------------------------------------------------------

    @agent_tool(
        name="ai_search",
        description="Unified web search that automatically routes to the best engine (Tavily/Exa/AnySearch) "
                    "based on intent and region. Supports lead generation, deep research, and general search. "
                    "Returns structured results with title, URL, content, score.",
    )
    async def do_ai_search(
        self,
        query: str,
        intent: str = "auto",
        region: str = "auto",
        max_results: int = 10,
        domain: str = "",
    ) -> dict:
        """Unified search with auto-routing.

        Args:
            query: Search query.
            intent: Search intent — ``auto`` (auto-detect), ``general``, ``lead_gen``, ``deep_research``.
            region: Search region — ``auto``, ``cn``, ``intl``.
            max_results: Number of results (1-50, default 10).
            domain: Optional domain hint (e.g. ``tech``, ``business``, ``academic``). Supported by AnySearch.

        Returns:
            Dict with ``status``, ``engine`` (selected engine), ``results`` (list of result dicts),
            ``total`` (result count), and ``fallback_history`` if a fallback was triggered.
        """
        if not query.strip():
            return {"status": "error", "msg": "query is required"}

        max_results = max(1, min(max_results, 50))

        # Step 1: resolve intent and region
        resolved_intent = intent if intent != "auto" else _auto_detect_intent(query)
        resolved_region = region if region != "auto" else _auto_detect_region(query)

        # Step 2: select primary engine from route table
        route_key = resolved_intent if resolved_intent != "general" else resolved_region
        route = _ROUTE_TABLE.get(route_key, _DEFAULT_ROUTE)
        engine = route["engine"]
        params: dict = route.copy()
        params.pop("engine", None)
        params.pop("reason", None)

        # Step 3: try primary engine, fallback on failure
        result = self._try_engine(engine, query, max_results, resolved_region, domain, params)
        fallback_history = []

        # Retry with fallback on error
        if result["status"] == "error":
            fallback_history.append(f"{engine}: {result.get('msg', 'unknown error')}")
            if engine in _FALLBACK_CHAIN:
                fb_engine, fb_mode = _FALLBACK_CHAIN[engine]
                logger.warning("ai_search: %s failed, falling back to %s", engine, fb_engine)
                fb_params = {"mode": fb_mode} if fb_mode else {}
                result = self._try_engine(
                    fb_engine, query, max_results, resolved_region, domain, fb_params,
                )
                fallback_history.append(f"fallback to {fb_engine}")

        result["intent"] = resolved_intent
        result["region"] = resolved_region
        result["selection"] = route.get("reason", _DEFAULT_ROUTE["reason"])
        if fallback_history:
            result["fallback_history"] = fallback_history
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _try_engine(self, engine: str, query: str, max_results: int,
                    region: str, domain: str, params: dict) -> dict:
        """Try calling a search engine. Returns unified result dict."""
        api_key = self._keys.get(engine, "")
        if not api_key:
            return {"status": "error", "msg": f"{engine} API key not configured"}

        try:
            if engine == "tavily":
                result = _call_tavily(query, api_key, max_results)
            elif engine == "exa":
                mode = params.get("mode", "auto")
                result = _call_exa(query, api_key, max_results, mode=mode)
            elif engine == "anysearch":
                r = params.get("region", region)
                result = _call_anysearch(query, api_key, max_results, region=r, domain=domain)
            else:
                return {"status": "error", "msg": f"Unknown engine: {engine}"}

            result["status"] = "success"
            return result

        except requests.Timeout:
            return {"status": "error", "msg": f"{engine} 请求超时"}
        except requests.ConnectionError:
            return {"status": "error", "msg": f"无法连接到 {engine}"}
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            body = ""
            try:
                body = exc.response.text[:200] if exc.response is not None else ""
            except Exception:
                pass
            return {"status": "error", "msg": f"{engine} HTTP {status}: {body}"}
        except Exception as exc:
            logger.exception("ai_search: %s unexpected error", engine)
            return {"status": "error", "msg": f"{engine} 异常: {exc}"}

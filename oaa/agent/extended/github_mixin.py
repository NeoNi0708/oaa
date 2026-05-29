"""GitHub mixin — GitHub search and trending tools."""
import re
from ..tool_decorator import agent_tool


class GithubMixin:
    """GitHub community tools: search repos, fetch trending."""

    @agent_tool(description="Search GitHub repositories by keywords. Uses GitHub's public search API. Returns repo name, description, stars, and URL. Use to discover relevant open-source tools, libraries, and projects.")
    async def do_github_search(self, query: str, language: str = "", sort: str = "stars", limit: int = 10) -> dict:
        """Search GitHub repositories. Useful for finding new tools, libraries, or references.

        Args:
            query: Search keywords (e.g. 'openai agent framework python')
            language: Filter by programming language (e.g. 'python', 'typescript')
            sort: Sort by 'stars', 'updated', or 'best-match' (default: stars)
            limit: Max results (default: 10, max: 30)
        """
        import requests
        params = {
            "q": query + (f"+language:{language}" if language else ""),
            "sort": sort if sort != "best-match" else "",
            "per_page": min(limit, 30),
            "order": "desc",
        }
        try:
            resp = requests.get(
                "https://api.github.com/search/repositories",
                params=params,
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            results = []
            for r in items[:min(limit, 30)]:
                results.append({
                    "name": r.get("full_name", ""),
                    "description": (r.get("description") or "")[:200],
                    "stars": r.get("stargazers_count", 0),
                    "url": r.get("html_url", ""),
                    "language": r.get("language") or "",
                    "updated": (r.get("updated_at") or "")[:10],
                })
            return {
                "status": "success",
                "total_count": data.get("total_count", 0),
                "results": results,
                "count": len(results),
            }
        except requests.RequestException as e:
            return {"status": "error", "msg": f"GitHub API search failed: {e}"}
        except Exception as e:
            return {"status": "error", "msg": f"GitHub search error: {e}"}

    @agent_tool(description="Fetch trending repositories from GitHub. Returns a list of trending repos for the specified language and time range. Returns both parsed trending page data and a fallback search-based result as a backup.")
    async def do_github_trending(self, language: str = "", since: str = "daily") -> dict:
        """Fetch trending GitHub repositories.

        Args:
            language: Programming language filter, e.g. 'python', 'typescript', '' for all
            since: Time range: 'daily', 'weekly', 'monthly' (default: daily)
        """
        import requests
        url = "https://github.com/trending"
        if language:
            url += f"/{language}"
        url += f"?since={since}"

        def _parse_trending_html(html: str) -> list[dict]:
            """Extract repo names from GitHub trending page HTML."""
            repos = []
            seen = set()

            # Pattern 1: <h2 class="h3 lh-condensed"><a href="/owner/name">
            for m in re.finditer(r'<h[23][^>]*>\s*<a\s+href="/([^"/]+/[^"/]+)"', html):
                name = m.group(1).strip()
                if name.count("/") == 1 and name not in seen:
                    seen.add(name)
                    repos.append(name)

            # Pattern 2: article.Box-row with h2 > a (newer GitHub layout)
            if not repos:
                for m in re.finditer(r'href="/([^"/]+/[^"/]+)"[^>]*>[^<]+</a>', html):
                    name = m.group(1).strip()
                    if name.count("/") == 1 and name not in seen:
                        seen.add(name)
                        repos.append(name)

            return [{"name": n} for n in repos[:25]]

        def _fallback_trending_search(language: str) -> list[dict]:
            """Fallback: use GitHub Search API to find trending repos by query."""
            try:
                q = "stars:>1000" + (f"+language:{language}" if language else "")
                sr = requests.get(
                    "https://api.github.com/search/repositories",
                    params={"q": q, "sort": "stars", "order": "desc", "per_page": 25},
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=15,
                )
                if sr.ok:
                    items = sr.json().get("items", [])
                    return [{
                        "name": r.get("full_name", ""),
                        "description": (r.get("description") or "")[:200],
                        "stars": r.get("stargazers_count", 0),
                        "url": r.get("html_url", ""),
                        "language": r.get("language") or "",
                    } for r in items]
            except Exception:
                pass
            return []

        try:
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; OAA/1.0)",
            }, timeout=20)
            resp.raise_for_status()
            html = resp.text

            repos = _parse_trending_html(html)
            source = "github.com/trending"

            if not repos:
                # Trending page parse failed → use search API as fallback
                repos = _fallback_trending_search(language)
                source = "GitHub Search API (trending page parse failed)"

            if not repos:
                return {
                    "status": "error",
                    "msg": "无法获取 GitHub trending 数据，请稍后重试或尝试 github_search 工具",
                }

            # Enrich with star counts via search API when we have name-only results
            if not repos[0].get("stars"):
                names = [r["name"] for r in repos[:10]]
                search_query = " OR ".join(f"repo:{n}" for n in names)
                try:
                    sr = requests.get(
                        "https://api.github.com/search/repositories",
                        params={"q": search_query, "per_page": 10},
                        headers={"Accept": "application/vnd.github.v3+json"},
                        timeout=15,
                    )
                    if sr.ok:
                        details = {d["full_name"]: d for d in sr.json().get("items", [])}
                        for r in repos:
                            d = details.get(r["name"])
                            if d:
                                r["description"] = (d.get("description") or "")[:200]
                                r["stars"] = d.get("stargazers_count", 0)
                                r["url"] = d.get("html_url", "")
                                r["language"] = d.get("language") or ""
                            else:
                                r["url"] = f"https://github.com/{r['name']}"
                except Exception:
                    for r in repos:
                        r["url"] = f"https://github.com/{r['name']}"

            return {
                "status": "success",
                "since": since,
                "language": language or "all",
                "results": repos,
                "count": len(repos),
                "source": source,
            }
        except requests.RequestException as e:
            # Network error → try search fallback
            repos = _fallback_trending_search(language)
            if repos:
                return {
                    "status": "success",
                    "since": since,
                    "language": language or "all",
                    "results": repos,
                    "count": len(repos),
                    "source": f"GitHub Search API (trending page error: {e})",
                }
            return {"status": "error", "msg": f"无法访问 GitHub trending: {e}"}
        except Exception as e:
            return {"status": "error", "msg": f"GitHub trending error: {e}"}

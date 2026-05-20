"""Conversation archiver — structured summaries + cross-session search.

Generates LLM-based summaries of completed conversation segments and stores
them as individual markdown files in ``memory/warm/conversations/``.  These
summaries are injected into the system prompt for context warmth and are
searchable by the agent via ``chat_history_search``.
"""
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..logging_config import get_logger

logger = get_logger("agent.conversation_archiver")

_SUMMARY_SYSTEM_PROMPT = """\
你是一个对话摘要工具。请用中文将以下对话提炼为结构化摘要。

## 输出格式

```markdown
## 用户目标
（用户这次想做什么，一句话概括）

## 关键信息
（对话中提到的关键事实、数字、名称等，每条一行）

## 完成事项
（实际完成的内容，每条一行）

## 遗留问题
（未解决的事项或后续需要跟进的内容，如果没有则写"无"）
```

## 规则
- 控制在 300 字以内
- 只保留对后续对话有用的信息，不要罗列细节
- 如果对话内容琐碎无信息量，只写用户目标即可，其他字段写"无"
- 不要输出 markdown 代码块标记 —— 直接输出内容
"""


class ConversationArchiver:
    """Generates structured conversation summaries and enables search across history.

    Directory layout::

        <memory_dir>/
            warm/
                conversations/
                    20260520_143000_客户Mueller报价.md
                    20260520_151200_技能优化讨论.md
                    ...                     # one file per summary
    """

    def __init__(self, memory_dir: str, llm: Any = None):
        self._conv_dir = Path(memory_dir) / "warm" / "conversations"
        self._conv_dir.mkdir(parents=True, exist_ok=True)
        self._llm = llm

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    async def summarize_and_archive(self, user_input: str, response: str) -> Optional[str]:
        """Generate a structured summary and persist it to disk.

        Args:
            user_input: The user's message (truncated internally).
            response: The assistant's final response (truncated internally).

        Returns:
            The filename stem (e.g. ``20260520_143000_客户Mueller报价``),
            or ``None`` on failure.
        """
        if not self._llm:
            return None

        summary = await self._generate_summary(user_input, response)
        if not summary:
            return None

        return self._save_summary(summary)

    async def _generate_summary(self, user_input: str, response: str) -> Optional[str]:
        """Call the LLM to produce a structured summary."""
        # Truncate to limit token usage
        user_trimmed = user_input[:800] if len(user_input) > 800 else user_input
        resp_trimmed = response[:2000] if len(response) > 2000 else response

        text = f"用户输入：{user_trimmed}\n\n助手回复：{resp_trimmed}"

        try:
            llm_response = await asyncio.wait_for(
                self._llm.chat([
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ]),
                timeout=15.0,
            )
            result = (llm_response.content or "").strip()
            return result if result else None
        except asyncio.TimeoutError:
            logger.warning("Summary generation timed out")
            return None
        except Exception as exc:
            logger.warning("Summary generation failed: %s", exc)
            return None

    def _save_summary(self, summary: str) -> str:
        """Write a single summary file.  Returns the filename stem."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Derive safe filename stem from the first substantive line
        first_line = ""
        for line in summary.strip().split("\n"):
            candidate = line.strip().strip("#").strip()
            if candidate and not candidate.startswith("```"):
                first_line = candidate
                break
        safe_name = re.sub(r'[^\w一-鿿\-]', "_", first_line)[:50]
        safe_name = safe_name.strip("_") or "conversation"

        stem = f"{timestamp}_{safe_name}"
        path = self._conv_dir / f"{stem}.md"
        # Prepend a title line if the LLM output didn't include one
        if not summary.lstrip().startswith("#"):
            summary = f"# 会话摘要 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{summary}"
        path.write_text(summary.strip() + "\n", encoding="utf-8")
        logger.info("Archived conversation summary: %s", stem)
        return stem

    # ------------------------------------------------------------------
    # Search across summaries
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword search across all conversation summary files.

        Returns results sorted by relevance (highest first).
        """
        q = query.lower()
        results: list[dict] = []

        if not self._conv_dir.exists():
            return results

        for fpath in sorted(self._conv_dir.iterdir(), reverse=True):
            if fpath.suffix != ".md":
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
            except Exception:
                continue

            if q not in content.lower():
                continue

            snippet = self._extract_snippet(content, q)
            title = self._extract_title(content, fpath)

            results.append({
                "type": "summary",
                "path": fpath.name,
                "title": title,
                "snippet": snippet,
                "score": self._score(content, q),
            })

        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    @staticmethod
    def _score(content: str, query: str) -> int:
        """Relevance score: higher = better match."""
        lower = content.lower()
        score = 1  # base match

        # Bonus for title match
        first_line = content.split("\n")[0].lower()
        if query in first_line:
            score += 3

        # Bonus for section-header proximity
        for header in ("用户目标", "关键信息", "完成事项", "遗留问题"):
            idx_h = lower.find(header)
            idx_q = lower.find(query)
            if idx_h >= 0 and idx_q >= 0 and abs(idx_h - idx_q) < 200:
                score += 2

        return score

    @staticmethod
    def _extract_snippet(content: str, query: str) -> str:
        """Return a short context window around the first match."""
        idx = content.lower().find(query.lower())
        if idx < 0:
            return content[:200].replace("\n", " | ")
        start = max(0, idx - 60)
        end = min(len(content), idx + len(query) + 120)
        snippet = content[start:end].replace("\n", " | ")
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        return snippet

    @staticmethod
    def _extract_title(content: str, fpath: Path) -> str:
        """Best-effort title from the markdown content."""
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return fpath.stem

    # ------------------------------------------------------------------
    # Context injection (system prompt warm-up)
    # ------------------------------------------------------------------

    def load_recent_summaries(self, limit: int = 3) -> str:
        """Return the N most recent summaries, formatted for system prompt injection.

        Each summary is trimmed to ~15 lines to avoid bloating the prompt.
        """
        if not self._conv_dir.exists():
            return ""

        files = [f for f in sorted(self._conv_dir.iterdir(), reverse=True) if f.suffix == ".md"]
        parts: list[str] = []
        for fpath in files[:limit]:
            try:
                content = fpath.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            lines = content.split("\n")
            parts.append("\n".join(lines[:15]))  # cap per-summary length

        if not parts:
            return ""

        return "\n\n---\n\n".join(parts)

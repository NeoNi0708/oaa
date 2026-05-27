"""Tiered memory system — HOT (always loaded), corrections, warm/ (on-demand), cold/ (archived).

Inspired by self-improving skill patterns.
"""
import asyncio
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..async_io import async_write, async_read


_HOT_MAX_LINES = 100
_CORRECTIONS_MAX = 50


class MemoryManager:
    """Manages tiered persistent memory across sessions.

    Directory layout::

        <memory_dir>/
            HOT.md              # Always loaded, ≤100 lines
            corrections.md      # Last N corrections
            warm/               # Topic files, loaded on demand
            cold/               # Archived / decayed entries
    """

    def __init__(self, memory_dir: str):
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "warm").mkdir(exist_ok=True)
        (self._dir / "cold").mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # HOT memory — always loaded into system prompt
    # ------------------------------------------------------------------

    def load_hot(self) -> str:
        """Return HOT.md content (empty string if missing)."""
        path = self._dir / "HOT.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    async def add_to_hot(self, entry: str) -> dict:
        """Append a new entry to HOT.md, then compact if over limit.

        *entry* should be a single line or short paragraph describing
        a user preference, rule, or learned pattern.
        """
        if not entry.strip():
            return {"status": "error", "msg": "Empty entry"}

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- [{timestamp}] {entry.strip()}\n"

        path = str(self._dir / "HOT.md")
        existing = await async_read(path) or ""
        await async_write(path, existing + line)

        lines = (await async_read(path) or "").splitlines()
        if len(lines) > _HOT_MAX_LINES:
            await self.compact_hot()

        return {"status": "success", "line_count": len(line)}

    async def compact_hot(self):
        """Demote older entries from HOT.md to a dated warm/ file.

        Keeps the most recent ``_HOT_MAX_LINES // 2`` lines in HOT.md;
        the rest are moved to ``warm/hot-archive-<date>.md``.
        """
        path = str(self._dir / "HOT.md")
        content = await async_read(path)
        if content is None:
            return
        lines = content.splitlines()
        if len(lines) <= _HOT_MAX_LINES:
            return

        keep = lines[: _HOT_MAX_LINES // 2 * -1]
        demote = lines[_HOT_MAX_LINES // 2 * -1 :]

        await async_write(path, "\n".join(keep) + "\n")

        date_str = datetime.now().strftime("%Y%m%d")
        archive_path = str(self._dir / "warm" / f"hot-archive-{date_str}.md")
        existing = (await async_read(archive_path)) or ""
        await async_write(archive_path,
            existing + f"# Archived from HOT on {date_str}\n" + "\n".join(demote) + "\n",
        )

    # ------------------------------------------------------------------
    # Corrections — user feedback learning
    # ------------------------------------------------------------------

    def load_recent_corrections(self, limit: int = 5) -> list[dict]:
        """Return the most recent *limit* corrections."""
        entries = self._parse_corrections()
        return entries[-limit:]

    async def add_correction(self, context: str, lesson: str) -> dict:
        """Log a user correction.

        *context* describes what the user said / what was being done.
        *lesson* is what should be done differently next time.
        """
        if not context or not lesson:
            return {"status": "error", "msg": "context and lesson are required"}

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n## {timestamp}: {context}\n"
            f"- **Lesson**: {lesson}\n"
        )

        path = str(self._dir / "corrections.md")
        existing = (await async_read(path)) or ""
        await async_write(path, existing + entry)

        # Trim to _CORRECTIONS_MAX
        entries = self._parse_corrections()
        if len(entries) > _CORRECTIONS_MAX:
            trimmed = entries[-_CORRECTIONS_MAX:]
            await self._write_corrections(trimmed)

        # Auto-promote: if same correction appears 3+ times, add to HOT
        similar = [e for e in self._parse_corrections() if e["lesson"] == lesson]
        if len(similar) >= 3:
            await self.add_to_hot(f"[Auto-promoted from corrections] {lesson}")

        return {"status": "success"}

    def _parse_corrections(self) -> list[dict]:
        """Parse corrections.md into list of {timestamp, context, lesson}."""
        path = self._dir / "corrections.md"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        entries = []
        for block in re.split(r"\n## ", text):
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            header = lines[0].strip()
            lesson = ""
            for l in lines:
                if l.startswith("- **Lesson**"):
                    lesson = l.split(":", 1)[-1].strip()
            # Extract timestamp from header format: "2026-05-15 14:30: context"
            ts = header
            context = header
            # Try to parse timestamp prefix
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\s*(.*)", header)
            if m:
                ts = m.group(1)
                context = m.group(2)
            entries.append({"timestamp": ts, "context": context, "lesson": lesson})
        return entries

    async def _write_corrections(self, entries: list[dict]):
        """Overwrite corrections.md with the given entries."""
        parts = []
        for e in entries:
            parts.append(
                f"## {e['timestamp']}: {e['context']}\n"
                f"- **Lesson**: {e['lesson']}\n"
            )
        await async_write(str(self._dir / "corrections.md"), "\n".join(parts))

    # ------------------------------------------------------------------
    # Warm memory — on demand
    # ------------------------------------------------------------------

    def list_warm_topics(self) -> list[str]:
        """Return filenames (without .md) of warm memory files."""
        warm_dir = self._dir / "warm"
        return sorted(
            f.stem for f in warm_dir.iterdir() if f.suffix == ".md"
        )

    def load_warm(self, topic: str) -> str:
        """Return content of a warm memory topic file."""
        path = self._dir / "warm" / f"{topic}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------------
    # Recall — search across all tiers
    # ------------------------------------------------------------------

    def search(self, query: str) -> dict:
        """Search HOT.md + corrections.md + warm/ for *query*.

        Returns matches grouped by tier.
        """
        q = query.lower()
        result: dict[str, list[str]] = {"hot": [], "corrections": [], "warm": []}

        hot = self.load_hot()
        for line in hot.split("\n"):
            if q in line.lower():
                result["hot"].append(line.strip())

        corrections = (self._dir / "corrections.md")
        if corrections.exists():
            for line in corrections.read_text(encoding="utf-8").split("\n"):
                if q in line.lower():
                    result["corrections"].append(line.strip())

        for topic in self.list_warm_topics():
            content = self.load_warm(topic)
            if q in content.lower():
                result["warm"].append(f"[{topic}] {content.strip()[:100]}")

        return {"status": "success", "matches": result, "query": query}

    # ------------------------------------------------------------------
    # Pending proposals — IdleInspector → agent injection
    # ------------------------------------------------------------------

    def save_pending_proposal(self, proposal: str):
        """Store a proposal from IdleInspector for the agent to see on next turn."""
        path = self._dir / "pending_proposals.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(proposal.strip() + "\n---\n")

    def load_pending_proposals(self) -> str:
        """Return all pending proposals, then clear them (consumed on read)."""
        path = self._dir / "pending_proposals.md"
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8").strip()
        # Clear after reading — agent gets one-shot delivery
        path.write_text("", encoding="utf-8")
        return content

    def has_pending_proposals(self) -> bool:
        path = self._dir / "pending_proposals.md"
        return path.exists() and path.stat().st_size > 0

    # ------------------------------------------------------------------
    # System prompt fragment
    # ------------------------------------------------------------------

    def build_memory_prompt(self) -> str:
        """Build the memory section for system prompt injection."""
        parts = []

        hot = self.load_hot()
        if hot:
            parts.append("# 持久记忆（HOT）\n\n以下是从过往交互中学习到的关于用户偏好和规则的信息：\n\n" + hot)
        else:
            parts.append("# 持久记忆（HOT）\n\n暂无已学习的用户偏好。")

        corrections = self.load_recent_corrections(5)
        if corrections:
            lines = []
            for c in corrections:
                lines.append(f"- {c['context']} → {c['lesson']}")
            parts.append("# 最近修正记录\n\n" + "\n".join(lines))

        # Pending proposals — one-shot injection, cleared after read
        proposals = self.load_pending_proposals()
        if proposals:
            parts.append("# ⏳ 待处理自愈提案\n\n以下自愈/优化机会待处理，系统规则第9条允许你自主执行无需等待用户确认：\n\n" + proposals)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Tool result tracking (failures + successes)
    # ------------------------------------------------------------------

    _FAILURE_CATEGORIES = frozenset({
        "tool_bug", "llm_error", "parameter_error", "infra_error", "unknown",
    })

    def add_tool_failure(self, tool_name: str, args: dict, error_msg: str,
                         category: str = "unknown",
                         task_context: str = "",
                         execution_chain: list | None = None) -> dict:
        """Record a tool execution failure to tool_failures.md.

        Args:
            category: root cause category — ``tool_bug`` (code bug),
                      ``llm_error`` (wrong tool/args chosen by LLM),
                      ``parameter_error`` (valid args but bad content),
                      ``infra_error`` (network/auth/permission transient),
                      or ``unknown`` (needs LLM analysis).
            task_context: what the agent was trying to do.
            execution_chain: recent tool calls leading to this failure,
                             each as ``{"tool": str, "args": dict, "status": str}``.
        """
        if category not in self._FAILURE_CATEGORIES:
            category = "unknown"
        import json
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        args_str = json.dumps(args, ensure_ascii=False)[:200]
        chain_str = json.dumps(execution_chain or [], ensure_ascii=False)[:800]
        entry = (
            f"\n## {timestamp}\n"
            f"- **tool**: {tool_name}\n"
            f"- **error**: {error_msg[:300]}\n"
            f"- **args**: {args_str}\n"
            f"- **category**: {category}\n"
        )
        if task_context:
            entry += f"- **context**: {task_context[:300]}\n"
        if execution_chain:
            entry += f"- **chain**: {chain_str}\n"
        path = self._dir / "tool_failures.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + entry, encoding="utf-8")
        return {"status": "success"}

    def load_tool_failures(self, limit: int = 50) -> list[dict]:
        """Return the most recent *limit* tool failure records."""
        path = self._dir / "tool_failures.md"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        entries = []
        for block in re.split(r"\n## ", text):
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            header = lines[0].strip()
            entry: dict = {
                "timestamp": header,
                "tool": "",
                "error": "",
                "args": "",
                "category": "unknown",
                "context": "",
                "chain": "",
            }
            for l in lines:
                key_val = l.split(":", 1)
                if len(key_val) < 2:
                    continue
                key_part = key_val[0].strip().lstrip("- **").rstrip("**")
                val = key_val[1].strip()
                if key_part == "tool":
                    entry["tool"] = val
                elif key_part == "error":
                    entry["error"] = val
                elif key_part == "args":
                    entry["args"] = val
                elif key_part == "category":
                    entry["category"] = val
                elif key_part == "context":
                    entry["context"] = val
                elif key_part == "chain":
                    entry["chain"] = val
            entries.append(entry)
        return entries[-limit:]

    def count_tool_failures(self, since_timestamp: str = "") -> dict:
        """Count tool failures grouped by tool name."""
        entries = self.load_tool_failures(500)
        if since_timestamp:
            entries = [e for e in entries if e["timestamp"] >= since_timestamp]
        tool_counts = Counter(e["tool"] for e in entries)
        return {"status": "success", "total": len(entries), "by_tool": dict(tool_counts)}

    # ------------------------------------------------------------------
    # Tool success tracking
    # ------------------------------------------------------------------

    def add_tool_success(self, tool_name: str, args: dict) -> dict:
        """Record a tool execution success to tool_successes.md."""
        import json
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        args_str = json.dumps(args, ensure_ascii=False)[:200]
        entry = (
            f"\n## {timestamp}\n"
            f"- **tool**: {tool_name}\n"
            f"- **args**: {args_str}\n"
        )
        path = self._dir / "tool_successes.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + entry, encoding="utf-8")
        return {"status": "success"}

    def load_tool_successes(self, limit: int = 50) -> list[dict]:
        """Return the most recent *limit* tool success records."""
        path = self._dir / "tool_successes.md"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        entries = []
        for block in re.split(r"\n## ", text):
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            header = lines[0].strip()
            tool = ""
            args = ""
            for l in lines:
                if l.startswith("- **tool**"):
                    tool = l.split(":", 1)[-1].strip()
                elif l.startswith("- **args**"):
                    args = l.split(":", 1)[-1].strip()
            entries.append({
                "timestamp": header,
                "tool": tool,
                "args": args,
            })
        return entries[-limit:]

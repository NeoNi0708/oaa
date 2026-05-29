"""Data models for the structured memory system.

Types: fact, event, pattern, decision, knowledge
Statuses: active → referenced → digested
"""

from dataclasses import dataclass, field
from typing import Optional

# ── constants ────────────────────────────────────────────────────────

MEMORY_TYPES = ("fact", "event", "pattern", "decision", "knowledge")
"""The five memory types an item can be classified into."""

STATUS_ACTIVE = "active"
STATUS_REFERENCED = "referenced"
STATUS_DIGESTED = "digested"

MAX_STORE = 10000
"""Hard cap — eviction kicks in above this number."""

EVICT_DAYS = 30
"""Age threshold for low-importance eviction candidates."""

DIGEST_THRESHOLD = 3
"""Automatic upgrade ``referenced → digested`` after N references."""

INJECT_TOP_K = 5
INJECT_FULL = 2
"""Top 2 get full text injected; remaining 3 get summary only."""

# ── data-classes ─────────────────────────────────────────────────────


@dataclass
class MemoryItem:
    """A single entry in the structured memory store."""

    id: str = ""
    text: str = ""
    summary: str = ""
    mem_type: str = "fact"
    source: str = "agent"          # "user" | "agent" | "system"

    importance: float = 0.5        # 0.0 – 1.0, initial score
    status: str = STATUS_ACTIVE

    created_at: float = 0.0
    updated_at: float = 0.0
    last_accessed: float = 0.0

    access_count: int = 0          # incremented on each retrieval
    ref_count: int = 0             # how many times the agent cited it

    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # Embedding (set by store, not caller)
    embedding: list[float] = field(default_factory=list)


@dataclass
class SearchResult:
    item: MemoryItem
    score: float = 0.0             # cosine similarity (0-1)
    full_text: str = ""            # full text if INJECT_FULL, else summary


# ── helpers ──────────────────────────────────────────────────────────


def importance_from_text(text: str) -> float:
    """A rough heuristic before LLM labels kick in.

    Returns a 0.0–1.0 importance value based on keyword hints.
    """
    keywords: dict[str, float] = {
        # user directives
        "记住": 0.9, "重要": 0.9, "规则": 0.85,
        "永远": 0.85, "以后": 0.8, "必须": 0.85,
        # decisions / failures
        "决策": 0.8, "失败": 0.7, "成功": 0.7,
        "修复": 0.75, "原因": 0.7, "教训": 0.8,
        # preferences
        "偏好": 0.7, "喜欢": 0.6, "习惯": 0.6,
        "称呼": 0.8, "公司": 0.7,
    }
    score = 0.5  # default
    for keyword, boost in keywords.items():
        if keyword in text:
            score = max(score, boost)
    return min(score, 1.0)

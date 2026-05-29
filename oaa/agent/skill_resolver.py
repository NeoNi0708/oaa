"""Skill resolver — semantic skill discovery without full listing.

Agent calls ``skill_find("用户需求")`` to find the best matching skills
instead of scanning a full listing in the system prompt.
"""
import re
from difflib import SequenceMatcher


def find_skills(skill_mgr, query: str, top_k: int = 3) -> list[dict]:
    """Find the best matching skills by name + description.

    Args:
        skill_mgr: SkillManager instance with discovered skills.
        query: User's task description (e.g. "做一份Excel报表").
        top_k: Max results to return.

    Returns:
        List of ``{name, category, description, score}`` sorted by relevance.
    """
    if not skill_mgr:
        return []

    skills = skill_mgr.list_with_descriptions()
    if not skills:
        return []

    keywords = _extract_keywords(query)
    scored = []

    for s in skills:
        score = _match_score(s, query, keywords)
        if score > 0:
            scored.append({
                "name": s.get("name", ""),
                "category": s.get("category", ""),
                "description": s.get("description", ""),
                "score": round(score, 2),
            })

    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from Chinese/English text."""
    # Split on whitespace/punctuation
    words = re.findall(r"[\w一-鿿]+", text.lower())
    # Filter common stop words
    stops = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
             "都", "一", "一个", "上", "也", "很", "到", "说", "要",
             "去", "你", "会", "着", "没有", "看", "好", "自己", "这",
             "the", "a", "an", "is", "are", "was", "were", "do", "does",
             "to", "in", "for", "of", "with", "on", "at", "by"}
    return [w for w in words if w not in stops and len(w) > 1]


def _match_score(skill: dict, query: str, keywords: list[str]) -> float:
    """Compute relevance score between a skill and the user query."""
    name = (skill.get("name") or "").lower()
    desc = (skill.get("description") or "").lower()
    text = f"{name} {desc}"
    score = 0.0

    # Exact name match → high confidence
    if name and name in query.lower():
        score += 0.8
    # Name substring in query
    if name and any(n in query.lower() for n in name.replace("-", " ").split()):
        score += 0.5

    # Keyword hits in description
    match_count = sum(1 for kw in keywords if kw in text)
    if keywords and match_count > 0:
        score += 0.3 * (match_count / len(keywords))

    # Fuzzy match on skill name
    if name:
        ratio = SequenceMatcher(None, name, query.lower()).ratio()
        if ratio > 0.4:
            score += ratio * 0.6

    return min(score, 1.0)

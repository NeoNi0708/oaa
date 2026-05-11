"""Skill package manager -- load SKILL.md + SOP.md at runtime, switch agent persona."""
import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..llm import LLMClient


class SkillInfo:
    """Holds metadata, SOP, knowledge and tools for a single skill.

    Uses mtime tracking to avoid re-reading unchanged files from disk.
    """

    def __init__(self, name: str, category: str, path: str):
        self.name = name
        self.category = category
        self.path = path
        self.skill_md: str = ""
        self.sop_md: str = ""
        self.knowledge: list[str] = []
        self.tools: list[dict] = []
        self._mtime_cache: dict[str, float] = {}

    def _mtime(self, file_path: Path) -> float:
        """Return mtime of *file_path* or 0 if it doesn't exist."""
        try:
            return file_path.stat().st_mtime
        except OSError:
            return 0.0

    def _changed(self, key: str, file_path: Path) -> bool:
        """Check if *file_path* has changed since last load."""
        mt = self._mtime(file_path)
        cached = self._mtime_cache.get(key, -1)
        if mt != cached:
            self._mtime_cache[key] = mt
            return True
        return False

    def load(self):
        """Load skill metadata, SOP, knowledge, and tools from disk.

        Only re-reads files whose mtime has changed since the last load.
        """
        skill_path = Path(self.path)

        # SKILL.md
        skill_file = skill_path / "SKILL.md"
        if self._changed("skill_md", skill_file) and skill_file.exists():
            self.skill_md = skill_file.read_text(encoding="utf-8")

        # SOP.md
        sop_file = skill_path / "SOP.md"
        if self._changed("sop_md", sop_file) and sop_file.exists():
            self.sop_md = sop_file.read_text(encoding="utf-8")

        # knowledge/  (sorted *.md / *.txt)
        know_dir = skill_path / "knowledge"
        know_changed = self._changed("knowledge_dir", know_dir)
        if know_changed and know_dir.exists() and know_dir.is_dir():
            knowledge: list[str] = []
            for f in sorted(know_dir.iterdir()):
                if f.is_file() and f.suffix in (".md", ".txt"):
                    knowledge.append(f.read_text(encoding="utf-8"))
            self.knowledge = knowledge

        # tools.json
        tools_file = skill_path / "tools.json"
        if self._changed("tools", tools_file) and tools_file.exists():
            self.tools = json.loads(tools_file.read_text(encoding="utf-8"))

    def build_system_prompt(self, identity: dict) -> str:
        """Build system prompt combining identity + skill role + SOP.

        *identity* is expected to contain keys like ``identity``, ``soul``,
        ``agents``, etc., typically loaded by ``oaa.init.load_identity``.
        """
        parts = [
            f"# Identity: {identity.get('identity', 'Er Leng')}",
            identity.get("soul", ""),
            identity.get("agents", ""),
        ]
        if self.skill_md:
            parts.append(f"\n# Current Skill: {self.name}\n{self.skill_md}")
        if self.sop_md:
            parts.append(f"\n# SOP -- Follow these steps:\n{self.sop_md}")
        for k in self.knowledge:
            parts.append(f"\n# Domain Knowledge:\n{k}")
        return "\n\n".join(parts)

    def __repr__(self) -> str:
        return f"<SkillInfo '{self.name}' ({self.category})>"


class SkillManager:
    """Manages skill discovery, loading, and runtime switching."""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self._skills: dict[str, SkillInfo] = {}
        self._current: Optional[SkillInfo] = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self):
        """Scan the skills directory and index all available skills.

        Directory layout expected::

            <skills_dir>/
                <category>/
                    <skill_name>/
                        SKILL.md
                        SOP.md
                        knowledge/
                        tools.json
        """
        self._skills = {}
        skills_path = Path(self.skills_dir)
        if not skills_path.exists():
            return

        for category_dir in skills_path.iterdir():
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            for skill_dir in category_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill = SkillInfo(skill_dir.name, category, str(skill_dir))
                self._skills[skill_dir.name] = skill

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[SkillInfo]:
        """Return a skill by name, or *None* if not found."""
        return self._skills.get(name)

    def get_current(self) -> Optional[SkillInfo]:
        """Return the currently active skill, or *None*."""
        return self._current

    def switch_to(self, name: str) -> Optional[SkillInfo]:
        """Load and activate a skill by name.

        Calls ``load()`` on the skill (which skips unchanged files via mtime
        cache). Returns the skill or *None* if the name is unknown.
        """
        skill = self.get(name)
        if skill:
            skill.load()
            self._current = skill
        return skill

    def list_all(self) -> list[SkillInfo]:
        """Return every discovered skill."""
        return list(self._skills.values())

    # ------------------------------------------------------------------
    # Intent matching
    # ------------------------------------------------------------------

    def match_intent(self, user_input: str) -> Optional[SkillInfo]:
        """Keyword-based skill matching for foreign-trade intents.

        Scans *user_input* for keywords and returns the first matching skill
        (discovered earlier via ``discover()``) or *None*.
        """
        keyword_map: dict[str, str] = {
            "报价": "business-assistant",
            "PI": "business-assistant",
            "合同": "business-assistant",
            "装箱单": "business-assistant",
            "汇率": "finance",
            "利润": "finance",
            "FOB": "finance",
            "CIF": "finance",
            "Form A": "finance",
            "原产地证": "finance",
            "跟单": "follow-up",
            "催货": "follow-up",
            "交期": "follow-up",
            "生产": "follow-up",
            "物流": "logistics-coordination",
            "运费": "logistics-coordination",
            "订舱": "logistics-coordination",
            "货代": "logistics-coordination",
            "搜客户": "market-researcher",
            "开发信": "market-researcher",
            "线索": "market-researcher",
            "潜在客户": "market-researcher",
            "邮件": "email-writer",
            "写邮件": "email-writer",
            "回复": "email-writer",
            "采购": "purchaser",
            "询价": "purchaser",
            "搜索": "search-execution",
            "查一下": "search-execution",
            "搜索一下": "search-execution",
            "行情": "market-analyst",
            "市场分析": "market-analyst",
            "分析": "market-analyst",
        }
        for keyword, skill_name in keyword_map.items():
            if keyword in user_input:
                return self.get(skill_name)
        return None

    async def _llm_match_intent(self, user_input: str, llm: "LLMClient") -> Optional[SkillInfo]:
        """LLM fallback when keyword matching yields nothing."""
        if not self._skills:
            return None
        skills_list = "\n".join(
            f"- {s.name} (分类: {s.category})"
            for s in sorted(self._skills.values(), key=lambda x: x.name)
        )
        prompt = (
            f"你是一个意图分类器。用户输入无法通过关键词匹配到任何技能。\n\n"
            f"可用技能列表:\n{skills_list}\n\n"
            f"用户输入: \"{user_input}\"\n\n"
            f"请判断用户输入与哪个技能最相关，或哪个都不相关。\n"
            f"只输出一个技能名称（不要分类），如果都不匹配则输出 \"none\"。\n"
            f"不要输出任何其他内容。"
        )
        try:
            response = await llm.chat([{"role": "user", "content": prompt}])
            skill_name = response.content.strip()
            if skill_name and skill_name != "none":
                return self.get(skill_name)
        except Exception:
            pass
        return None

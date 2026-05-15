"""Skill package manager -- load SKILL.md + SOP.md at runtime, switch agent persona."""
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..llm import LLMClient


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from markdown text.

    Returns (meta_dict, body_text).  Meta values are always strings.
    Returns ({}, text) when no frontmatter is found.
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, text[m.end():]


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
        self._description: str = ""
        self._mtime_cache: dict[str, float] = {}

    @property
    def description(self) -> str:
        """Short description from SKILL.md frontmatter, or name as fallback."""
        return self._description or self.name

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
            raw = skill_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            self._description = meta.get("description", "")
            self.skill_md = body

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
                if not (skill_dir / "SKILL.md").exists():
                    continue
                skill = SkillInfo(skill_dir.name, category, str(skill_dir))
                skill.load()  # load description from frontmatter
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

    def list_with_descriptions(self) -> list[dict]:
        """Return every skill as {name, category, description} for system prompt injection."""
        return [
            {"name": s.name, "category": s.category, "description": s.description}
            for s in sorted(self._skills.values(), key=lambda x: x.name)
        ]

    # ------------------------------------------------------------------
    # Intent matching (deprecated — kept for backward compat, always returns None)
    # ------------------------------------------------------------------

    def match_intent(self, user_input: str) -> Optional[SkillInfo]:
        """Legacy keyword matching - no longer used.  Returns None."""
        return None

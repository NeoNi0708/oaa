"""Test skill verification logic."""
import tempfile
import os
from pathlib import Path
from scripts.verify_skills import verify_skills, REQUIRED_SKILLS


def test_verify_skills_missing():
    """Verify returns False when no skills exist."""
    with tempfile.TemporaryDirectory() as tmp:
        result = verify_skills(tmp)
        assert result is False


def test_verify_skills_subset():
    """Verify detects when some skills exist and some don't."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create just one skill
        skill_dir = Path(tmp) / "skills" / "外贸业务核心" / "business-assistant"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Business Assistant")
        result = verify_skills(tmp)
        assert result is False  # 28 skills still missing

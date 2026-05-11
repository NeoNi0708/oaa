"""Test evolution engine."""
import tempfile
from pathlib import Path
from oaa.evolution.engine import EvolutionEngine


def test_evolution_basics():
    with tempfile.TemporaryDirectory() as tmp:
        e = EvolutionEngine(tmp)
        e.record_skill_usage("business-assistant")
        e.record_skill_usage("business-assistant")
        assert e.stats["skill_usage"]["business-assistant"] == 2

        suggestions = e.analyze_for_suggestions()
        assert isinstance(suggestions, list)

        result = e.crystallize_skill("test-skill", "# Test Skill", "## Steps\n1. Do X")
        assert "test-skill" in result
        assert (Path(tmp) / "skills" / "user_evolved" / "test-skill" / "SKILL.md").exists()

"""Test evolution engine."""
import tempfile
from pathlib import Path
import pytest
from oaa.evolution.engine import EvolutionEngine


@pytest.mark.asyncio
async def test_evolution_basics():
    with tempfile.TemporaryDirectory() as tmp:
        e = EvolutionEngine(tmp)
        await e.record_skill_usage("business-assistant")
        await e.record_skill_usage("business-assistant")
        assert e.stats["skill_usage"]["business-assistant"] == 2

        suggestions = await e.analyze_for_suggestions()
        assert isinstance(suggestions, list)

        result = await e.crystallize_skill("test-skill", "# Test Skill", "## Steps\n1. Do X")
        assert "test-skill" in result
        assert (Path(tmp) / "skills" / "user_evolved" / "test-skill" / "SKILL.md").exists()

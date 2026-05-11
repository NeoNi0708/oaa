"""Test skill discovery, switching, and intent matching."""
import json
import tempfile
from pathlib import Path

from oaa.agent.skill_manager import SkillManager, SkillInfo


def test_skill_discovery_and_switching():
    with tempfile.TemporaryDirectory() as tmp:
        # Create a test skill with all the components
        skill_dir = Path(tmp) / "外贸业务核心" / "business-assistant"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Business Assistant\nRole: quotation and order management\n",
            encoding="utf-8",
        )
        (skill_dir / "SOP.md").write_text(
            "1. Parse request\n2. Generate document\n", encoding="utf-8"
        )

        know_dir = skill_dir / "knowledge"
        know_dir.mkdir()
        (know_dir / "incoterms.md").write_text("FOB, CIF, EXW terms\n", encoding="utf-8")

        (skill_dir / "tools.json").write_text(
            json.dumps(
                [{"name": "generate_quote", "description": "Generate a quotation"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Create a second skill in a different category
        skill_dir2 = Path(tmp) / "通信消息" / "email-writer"
        skill_dir2.mkdir(parents=True)
        (skill_dir2 / "SKILL.md").write_text(
            "# Email Writer\nRole: compose business emails\n", encoding="utf-8"
        )

        # Test discovery
        mgr = SkillManager(tmp)
        mgr.discover()
        discovered = [s.name for s in mgr.list_all()]
        assert "business-assistant" in discovered, f"Discovered: {discovered}"
        assert "email-writer" in discovered, f"Discovered: {discovered}"

        # Test get
        skill = mgr.get("business-assistant")
        assert skill is not None

        # Test switch_to
        skill = mgr.switch_to("business-assistant")
        assert skill is not None
        assert "quotation" in skill.skill_md, "SKILL.md not loaded"
        assert "FOB" in skill.knowledge[0], "knowledge not loaded"
        assert skill.tools[0]["name"] == "generate_quote", "tools.json not loaded"
        assert mgr.get_current().name == "business-assistant"

        # Test build_system_prompt
        identity = {"identity": "Er Leng", "soul": "Be reliable", "agents": "Stay safe"}
        prompt = skill.build_system_prompt(identity)
        assert "Er Leng" in prompt
        assert "Business Assistant" in prompt
        assert "FOB" in prompt
        assert "1. Parse request" in prompt

        # Test unknown skill
        unknown_skill = mgr.get("nonexistent")
        assert unknown_skill is None


def test_intent_matching():
    with tempfile.TemporaryDirectory() as tmp:
        # Create ALL skills that the keyword_map references
        all_skills = [
            "business-assistant", "finance", "follow-up",
            "logistics-coordination", "market-researcher",
            "email-writer", "purchaser", "search-execution", "market-analyst",
        ]
        for skill_name in all_skills:
            d = Path(tmp) / "skills" / skill_name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {skill_name}\n", encoding="utf-8")

        mgr = SkillManager(tmp)
        mgr.discover()
        assert len(mgr.list_all()) == len(all_skills)

        # Test intent matching for various keywords
        test_cases = [
            ("报价", "business-assistant"),       # 报价
            ("PI", "business-assistant"),
            ("合同", "business-assistant"),        # 合同
            ("汇率", "finance"),                    # 汇率
            ("FOB", "finance"),
            ("CIF", "finance"),
            ("跟单", "follow-up"),                  # 跟单
            ("催货", "follow-up"),                  # 催货
            ("搜客户", "market-researcher"),   # 搜客户
            ("开发信", "market-researcher"),   # 开发信
            ("邮件", "email-writer"),              # 邮件
            ("采购", "purchaser"),                 # 采购
            ("搜索", "search-execution"),           # 搜索
            ("行情", "market-analyst"),            # 行情
            ("运费", "logistics-coordination"),    # 运费
        ]

        for keyword, expected_skill in test_cases:
            matched = mgr.match_intent(keyword)
            assert matched is not None, (
                "No match for keyword (expected '{}')".format(expected_skill)
            )
            assert matched.name == expected_skill, (
                "Expected '{}', got '{}'".format(expected_skill, matched.name)
            )

        # Test no match for irrelevant input
        no_match = mgr.match_intent("hello world")
        assert no_match is None, "Should not match irrelevant input"

        # Test compound input that should match first keyword
        compound = mgr.match_intent("报价 FOB 上海")
        assert compound is not None
        assert compound.name == "business-assistant"


def test_list_all():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "cat1" / "skill-a").mkdir(parents=True)
        (Path(tmp) / "cat1" / "skill-b").mkdir(parents=True)
        (Path(tmp) / "cat2" / "skill-c").mkdir(parents=True)

        mgr = SkillManager(tmp)
        mgr.discover()
        all_skills = mgr.list_all()
        assert len(all_skills) == 3
        names = {s.name for s in all_skills}
        assert names == {"skill-a", "skill-b", "skill-c"}
        categories = {s.category for s in all_skills}
        assert categories == {"cat1", "cat2"}


def test_skill_info_repr():
    si = SkillInfo("test", "category", "/tmp/path")
    assert "test" in repr(si)
    assert "category" in repr(si)


def test_skill_info_load_no_files():
    """Test that loading a skill with no files doesn't crash."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "cat" / "empty-skill"
        d.mkdir(parents=True)
        si = SkillInfo("empty-skill", "cat", str(d))
        si.load()
        assert si.skill_md == ""
        assert si.sop_md == ""
        assert si.knowledge == []
        assert si.tools == []


def test_switch_to_unknown_skill():
    """Test that switching to an unknown skill returns None gracefully."""
    with tempfile.TemporaryDirectory() as tmp:
        mgr = SkillManager(tmp)
        mgr.discover()
        result = mgr.switch_to("does-not-exist")
        assert result is None
        assert mgr.get_current() is None

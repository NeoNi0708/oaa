"""Verify all 29 pre-installed skills are present in data directory."""
import os
import sys
from pathlib import Path

REQUIRED_SKILLS = [
    # Category A - 外贸业务核心
    "外贸业务核心/foreign-trade-general",
    "外贸业务核心/business-assistant",
    "外贸业务核心/quotation-maker",
    "外贸业务核心/contract-review",
    "外贸业务核心/customer-support",
    "外贸业务核心/email-writer",
    "外贸业务核心/inquiry-handling",
    "外贸业务核心/customer-relationship",
    "外贸业务核心/finance",
    "外贸业务核心/follow-up",
    "外贸业务核心/logistics-coordination",
    "外贸业务核心/market-analyst",
    "外贸业务核心/market-researcher",
    "外贸业务核心/outreach-prospecting",
    "外贸业务核心/purchaser",
    "外贸业务核心/search-execution",
    # Category B - 办公文档
    "办公文档/word-docx",
    "办公文档/excel-xlsx",
    "办公文档/nano-pdf",
    # Category C - 通信消息
    "通信消息/himalaya-email",
    "通信消息/wechat-cli",
    "通信消息/clawhub",
    # Category D - 系统与自进化
    "系统与自进化/self-improving",
    "系统与自进化/agent-autonomy-kit",
    "系统与自进化/skill-creator",
    "系统与自进化/agent-memory",
    "系统与自进化/bb-browser",
    "系统与自进化/weather",
    "系统与自进化/summarize",
]


def verify_skills(data_dir: str) -> bool:
    """Verify all required skills exist with SKILL.md files."""
    skills_dir = Path(data_dir) / "skills"
    if not skills_dir.exists():
        print(f"Skills directory not found: {skills_dir}")
        return False

    missing = []
    for rel_path in REQUIRED_SKILLS:
        skill_path = skills_dir / rel_path
        if not skill_path.exists():
            missing.append(rel_path)
            print(f"  [MISS] {rel_path}")
        elif not (skill_path / "SKILL.md").exists():
            missing.append(f"{rel_path} (no SKILL.md)")
            print(f"  [MISS] {rel_path} (no SKILL.md)")
        else:
            print(f"  [OK] {rel_path}")
    if missing:
        print(f"\nMissing {len(missing)} skills!")
        return False
    print(f"\nAll {len(REQUIRED_SKILLS)} skills verified!")
    return True


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/OAA")
    sys.exit(0 if verify_skills(data_dir) else 1)

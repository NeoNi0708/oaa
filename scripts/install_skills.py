"""Install pre-installed skills from a source directory to OAA data directory.

Usage:
    python install_skills.py [data_dir] [skills_source_dir]

Environment:
    OAA_SKILLS_SOURCE: path to skills source directory (default: current dir)
"""
import os
import shutil
from pathlib import Path


# Map of (category/skill_name → source subdirectory name)
SKILL_MAP: dict[str, str] = {
    # Category A: 外贸业务核心 (16 skills)
    "外贸业务核心/foreign-trade-general": "外贸业务技能",
    "外贸业务核心/business-assistant": "business-assistant",
    "外贸业务核心/quotation-maker": "quotation_maker",
    "外贸业务核心/contract-review": "contract_review",
    "外贸业务核心/customer-support": "customer-support",
    "外贸业务核心/email-writer": "email-writer-2.0.0",
    "外贸业务核心/inquiry-handling": "inquiry_handling",
    "外贸业务核心/customer-relationship": "customer_relationship",
    "外贸业务核心/finance": "finance",
    "外贸业务核心/follow-up": "follow-up",
    "外贸业务核心/logistics-coordination": "logistics_coordination",
    "外贸业务核心/market-analyst": "market-analyst",
    "外贸业务核心/market-researcher": "market-researcher",
    "外贸业务核心/outreach-prospecting": "outreach-and-prospecting",
    "外贸业务核心/purchaser": "purchaser",
    "外贸业务核心/search-execution": "search_execution",
    # Category B: 办公文档 (3 skills)
    "办公文档/word-docx": "word-docx-1.0.2",
    "办公文档/excel-xlsx": "excel-xlsx-1.0.2",
    "办公文档/nano-pdf": "nano-pdf-1.0.0",
    # Category C: 通信消息 (3 skills)
    "通信消息/himalaya-email": "himalaya",
    "通信消息/wechat-cli": "wechat-cli",
    "通信消息/clawhub": "clawhub",
    # Category D: 系统与自进化 (7 skills)
    "系统与自进化/self-improving": "self-improving-1.2.16",
    "系统与自进化/agent-autonomy-kit": "agent-autonomy-kit-1.0.0",
    "系统与自进化/skill-creator": "skill-creator",
    "系统与自进化/agent-memory": "agent-memory-1.0.0",
    "系统与自进化/bb-browser": "bb-browser/skills/bb-browser-openclaw",
    "系统与自进化/weather": "weather",
    "系统与自进化/summarize": "summarize",
}


def install_skills(data_dir: str, skills_source: str = "") -> int:
    """Copy skills from *skills_source* to OAA data directory.

    Args:
        data_dir: OAA data directory (skills installed under ``<data_dir>/skills/``).
        skills_source: Directory containing skill subdirectories. Defaults to
                       the ``OAA_SKILLS_SOURCE`` env var, or ``os.getcwd()``.

    Returns:
        Number of skills installed.
    """
    source_base = skills_source or os.environ.get(
        "OAA_SKILLS_SOURCE", os.getcwd()
    )
    skills_target = Path(data_dir) / "skills"
    count = 0
    for rel_path, subdir in SKILL_MAP.items():
        target = skills_target / rel_path
        if target.exists():
            continue
        source = Path(source_base) / subdir
        if not source.exists():
            print(f"  [SKIP] Source not found: {source}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        count += 1
        print(f"  [OK] {rel_path}")
    print(f"Installed {count} skills to {skills_target}")
    return count


if __name__ == "__main__":
    import sys
    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/OAA")
    skills_source = sys.argv[2] if len(sys.argv) > 2 else ""
    install_skills(data_dir, skills_source)

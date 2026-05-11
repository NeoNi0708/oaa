"""Self-evolution engine — three levels of self-improvement.

Level 1: Execution optimization (automatic)
Level 2: Active refinement (suggestions for user)
Level 3: Skill crystallization (from trajectories → LLM-driven pattern extraction)
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..llm import LLMClient

logger = get_logger("evolution")


class EvolutionEngine:
    """Tracks task execution patterns and drives self-evolution."""

    def __init__(self, data_dir: str, llm: Optional["LLMClient"] = None):
        self.data_dir = data_dir
        self._llm = llm
        self._stats_path = os.path.join(data_dir, "memory", "evolution_stats.json")
        self._trajectory_dir = os.path.join(data_dir, "memory", "trajectories")
        Path(self._trajectory_dir).mkdir(parents=True, exist_ok=True)
        self._load_stats()

    def _load_stats(self):
        if os.path.exists(self._stats_path):
            with open(self._stats_path, encoding="utf-8") as f:
                self.stats = json.load(f)
        else:
            self.stats = {
                "skill_usage": {},
                "sop_executions": {},
                "sop_skips": {},
                "parameter_defaults": {},
                "crystallized": [],
                "suggestions": [],
            }

    def _save_stats(self):
        Path(self._stats_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._stats_path, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)

    def record_skill_usage(self, skill_name: str):
        """Level 1: Track how often each skill is used."""
        self.stats["skill_usage"][skill_name] = self.stats["skill_usage"].get(skill_name, 0) + 1
        self._save_stats()

    def record_sop_execution(self, skill_name: str, completed_steps: list[str],
                             skipped_steps: list[str]):
        """Level 1: Track SOP execution patterns."""
        self.stats["sop_executions"][skill_name] = self.stats["sop_executions"].get(skill_name, 0) + 1
        for step in skipped_steps:
            if skill_name not in self.stats["sop_skips"]:
                self.stats["sop_skips"][skill_name] = {}
            self.stats["sop_skips"][skill_name][step] = \
                self.stats["sop_skips"][skill_name].get(step, 0) + 1
        self._save_stats()

    def record_trajectory(self, skill_name: str, user_input: str,
                          trajectory: list[dict], result: str):
        """Level 3: Save execution trajectory for potential skill crystallization."""
        entry = {
            "skill": skill_name,
            "user_input": user_input[:200],
            "timestamp": datetime.now().isoformat(),
            "steps": trajectory,
            "result": result[:500],
        }
        fname = f"traj_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{skill_name[:20]}.json"
        with open(os.path.join(self._trajectory_dir, fname), "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)

    def analyze_for_suggestions(self) -> list[dict]:
        """Level 2: Detect patterns and generate improvement suggestions."""
        suggestions = []
        for skill, count in self.stats["skill_usage"].items():
            if count >= 5 and count % 5 == 0:
                suggestions.append({
                    "type": "optimize",
                    "skill": skill,
                    "message": f"技能「{skill}」已使用 {count} 次，是否查看优化建议？",
                    "usage_count": count,
                })
        for skill, skips in self.stats["sop_skips"].items():
            for step, skip_count in skips.items():
                if skip_count >= 3:
                    suggestions.append({
                        "type": "sop_refine",
                        "skill": skill,
                        "message": f"「{skill}」的步骤「{step}」已跳过 {skip_count} 次，是否从 SOP 中移除？",
                        "step": step,
                        "skip_count": skip_count,
                    })
        self.stats["suggestions"] = suggestions
        self._save_stats()
        return suggestions

    def accept_suggestion(self, idx: int) -> bool:
        """Accept a suggestion (user confirmed)."""
        if idx >= len(self.stats["suggestions"]):
            return False
        self.stats["suggestions"].pop(idx)
        self._save_stats()
        return True

    # ------------------------------------------------------------------
    # Level 3 — LLM-driven skill crystallization
    # ------------------------------------------------------------------

    def _get_trajectories_for_skill(self, skill_name: str, limit: int = 10) -> list[dict]:
        """Read recent trajectories for a given skill."""
        trajs = []
        if not os.path.isdir(self._trajectory_dir):
            return trajs
        for fname in sorted(os.listdir(self._trajectory_dir), reverse=True):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._trajectory_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    entry = json.load(f)
                if entry.get("skill") == skill_name:
                    trajs.append(entry)
                    if len(trajs) >= limit:
                        break
            except (json.JSONDecodeError, OSError):
                continue
        return trajs

    async def extract_and_crystallize(self, skill_name: str) -> Optional[str]:
        """Analyze trajectories for *skill_name* using the LLM and crystallize a skill.

        Reads recent trajectories, sends them to the LLM for pattern extraction,
        generates SKILL.md + SOP.md, and saves via ``crystallize_skill``.

        Returns the target directory path, or *None* if there aren't enough
        trajectories or no LLM is configured.
        """
        if not self._llm:
            return None

        trajectories = self._get_trajectories_for_skill(skill_name)
        if len(trajectories) < 3:
            return None

        traj_summary = json.dumps(
            [
                {"input": t["user_input"], "steps": t["steps"], "result": t["result"]}
                for t in trajectories
            ],
            indent=2, ensure_ascii=False,
        )

        prompt = (
            f"你是一个技能提取器。以下是从技能「{skill_name}」的执行轨迹中收集的数据。\n\n"
            f"请根据这些轨迹，生成：\n"
            f"1. 一个 SKILL.md，描述该技能的目标、适用场景和使用方法\n"
            f"2. 一个 SOP.md，列出标准操作步骤\n\n"
            f"轨迹数据:\n{traj_summary}\n\n"
            f"输出格式 (纯 JSON，不要其他内容):\n"
            f'{{"skill_md": "... markdown ...", "sop_md": "... markdown ..."}}'
        )

        try:
            response = await self._llm.chat([{"role": "user", "content": prompt}])
            content = response.content.strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(content)
            skill_md = result.get("skill_md", "")
            sop_md = result.get("sop_md", "")
            if not skill_md:
                return None
            return self.crystallize_skill(skill_name, skill_md, sop_md)
        except Exception as exc:
            logger.error("Skill crystallization failed for %s: %s", skill_name, exc)
            return None

    def crystallize_skill(self, name: str, skill_md: str, sop_md: str) -> str:
        """Level 3: Crystallize a new skill from execution patterns."""
        target = Path(self.data_dir) / "skills" / "user_evolved" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(skill_md, encoding="utf-8")
        if sop_md:
            (target / "SOP.md").write_text(sop_md, encoding="utf-8")
        self.stats["crystallized"].append({
            "name": name,
            "created": datetime.now().isoformat(),
        })
        self._save_stats()
        return str(target)

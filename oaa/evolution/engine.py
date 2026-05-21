"""Self-evolution engine — three levels of self-improvement.

Level 1: Execution optimization (automatic)
Level 2: Active refinement (suggestions for user)
Level 3: Skill crystallization (from trajectories → LLM-driven pattern extraction)
"""
import asyncio
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
            try:
                with open(self._stats_path, encoding="utf-8") as f:
                    self.stats = json.load(f)
                logger.info("EvolutionEngine loaded stats from %s (skill_usage=%s)",
                            self._stats_path, list(self.stats.get("skill_usage", {}).keys()))
                return
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load evolution stats (%s) — using empty stats", exc)
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

    def set_llm(self, llm: "LLMClient"):
        """Inject or replace the LLM client (e.g. wired after agent init)."""
        self._llm = llm

    def record_skill_usage(self, skill_name: str):
        """Level 1: Track how often each skill is used.

        Auto-triggers Level 3 crystallization when the skill hits 3+ uses
        and no crystallized version exists yet.
        """
        self.stats["skill_usage"][skill_name] = self.stats["skill_usage"].get(skill_name, 0) + 1
        self._save_stats()

        # Auto-crystallization: ≥3 uses, has LLM, no existing crystal → fire and forget
        count = self.stats["skill_usage"][skill_name]
        crystallized_names = {c["name"] for c in self.stats.get("crystallized", [])}
        if count >= 3 and self._llm and skill_name not in crystallized_names:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.extract_and_crystallize(skill_name))
                    logger.info("Auto-crystallization triggered for '%s' (%d uses)", skill_name, count)
            except RuntimeError:
                logger.debug("No event loop for auto-crystallization of '%s'", skill_name)

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
            if count >= 3:
                suggestions.append({
                    "type": "optimize",
                    "skill": skill,
                    "message": f"技能「{skill}」已使用 {count} 次，是否查看优化建议？",
                    "usage_count": count,
                })
        for skill, skips in self.stats["sop_skips"].items():
            if not isinstance(skips, dict):
                continue
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
    # Auto-refinements — actionable proposals for IdleInspector
    # ------------------------------------------------------------------

    def get_auto_refinements(self) -> list[dict]:
        """Return structured, actionable refinements for the IdleInspector.

        Each refinement includes:
          - type: "sop_skip" | "skill_optimize"
          - skill_name
          - file_path (absolute path to the file that needs changing)
          - description (human-readable)
          - action (what the agent should do)
          - old_content / new_content (for self_improve)
        """
        refinements: list[dict] = []
        data_dir = Path(self.data_dir)

        # SOP skips → suggest removing the skipped step from SOP.md
        for skill_name, skips in self.stats.get("sop_skips", {}).items():
            if not isinstance(skips, dict):
                continue
            for step_name, skip_count in skips.items():
                if skip_count >= 3:
                    sop_path = data_dir / "skills" / skill_name / "SOP.md"
                    if not sop_path.exists():
                        # Try category discovery
                        for cat_dir in (data_dir / "skills").iterdir():
                            if cat_dir.is_dir():
                                candidate = cat_dir / skill_name / "SOP.md"
                                if candidate.exists():
                                    sop_path = candidate
                                    break
                    if sop_path.exists():
                        refinements.append({
                            "type": "sop_skip",
                            "skill_name": skill_name,
                            "file_path": str(sop_path),
                            "description": f"SOP 步骤「{step_name}」已跳过 {skip_count} 次",
                            "action": "从 SOP.md 中移除该步骤",
                            "step_name": step_name,
                        })

        # Skill usage milestones → suggest optimization review
        for skill_name, count in self.stats.get("skill_usage", {}).items():
            if count >= 3:
                refinements.append({
                    "type": "skill_optimize",
                    "skill_name": skill_name,
                    "file_path": "",
                    "description": f"技能「{skill_name}」已使用 {count} 次",
                    "action": "用 code_exec 分析使用模式并生成优化建议",
                    "usage_count": count,
                })

        return refinements

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

"""Skill mixin — skill search, install, load, create tools."""
import io
import json
import os
import tarfile
import zipfile
from ..tool_decorator import agent_tool


class SkillMixin:
    """Skill marketplace and management tools."""

    async def do_skill_search(self, args: dict) -> dict:
        """Search ClawHub skill market or GitHub for reusable skills."""
        query = args.get("query", "")
        registry = args.get("registry", "https://mirror-cn.clawhub.com")
        if not query:
            return {"status": "error", "msg": "query is required"}
        import requests
        try:
            resp = requests.get(f"{registry}/api/v1/search", params={"q": query}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data if isinstance(data, list) else data.get("results", data.get("skills", []))
            return {
                "status": "success",
                "results": [{"slug": r.get("slug"), "name": r.get("name"), "description": r.get("description", "")} for r in results[:20]],
                "count": len(results),
                "source": registry,
            }
        except Exception as e:
            return {"status": "error", "msg": f"搜索技能市场失败: {e}，可尝试 ai_search 在 GitHub 上搜索"}

    async def do_skill_install(self, args: dict) -> dict:
        """Install a skill from ClawHub or GitHub."""
        slug = args.get("slug", "")
        url = args.get("url", "")
        registry = args.get("registry", "https://mirror-cn.clawhub.com")
        if not slug and not url:
            return {"status": "error", "msg": "需要 slug（ClawHub 技能名）或 url（GitHub 地址）"}
        import requests
        if slug:
            try:
                resolve = requests.get(f"{registry}/api/v1/resolve", params={"slug": slug}, timeout=15)
                resolve.raise_for_status()
                info = resolve.json()
                dl_path = info.get("downloadUrl", f"/api/v1/download/{slug}")
                dl_url = f"{registry}{dl_path}" if dl_path.startswith("/") else dl_path
                name = slug
            except Exception as e:
                return {"status": "error", "msg": f"解析技能 {slug} 失败: {e}"}
        else:
            dl_url = url
            name = url.rstrip("/").split("/")[-1].replace(".git", "")
        try:
            resp = requests.get(dl_url, timeout=30)
            resp.raise_for_status()
            content = resp.content
            skills_dir = os.path.join(self.data_dir, "skills", "community")
            target = os.path.join(skills_dir, name)
            os.makedirs(target, exist_ok=True)
            if content[:2] == b'\x1f\x8b':
                with tarfile.open(fileobj=io.BytesIO(content)) as tf:
                    tf.extractall(target)
            elif content[:4] == b'PK\x03\x04':
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    zf.extractall(target)
            else:
                with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write(resp.text)
            if self._skill_mgr:
                self._skill_mgr.discover()
            return {"status": "success", "msg": f"技能 '{name}' 已安装到 {target}", "path": target}
        except Exception as e:
            return {"status": "error", "msg": f"安装失败: {e}"}

    async def do_skill_load(self, args: dict) -> dict:
        """Load a skill's SKILL.md, SOP.md, and knowledge content by name.

        The model calls this when it determines a skill matches the current task.
        Returns the full content of the skill for the model to follow.
        """
        name = args.get("name", "")
        if not name or not self._skill_mgr:
            return {"status": "error", "msg": "Skill name required or skill manager not available"}

        skill = self._skill_mgr.get(name)
        if not skill:
            return {"status": "error", "msg": f"Skill '{name}' not found. Available: {', '.join(s.name for s in self._skill_mgr.list_all())}"}

        skill.load()
        result: dict[str, object] = {
            "status": "success",
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
        }
        if skill.skill_md:
            result["skill_md"] = skill.skill_md
        if skill.sop_md:
            result["sop_md"] = skill.sop_md
        if skill.knowledge:
            result["knowledge"] = skill.knowledge
        return result

    @agent_tool(
        name="skill_find",
        description="Search installed skills by intent. Call this when you need a skill for a task instead of guessing from memory. Returns the top 3 matching skills with name, description, and confidence score. Use skill_load to load the chosen skill."
    )
    async def do_skill_find(self, query: str) -> dict:
        """Find the best matching skills for a task."""
        if not query:
            return {"status": "error", "msg": "query is required"}
        try:
            from ..skill_resolver import find_skills
            results = find_skills(self._skill_mgr, query, top_k=3)
            return {
                "status": "success",
                "results": results,
                "count": len(results),
                "query": query,
            }
        except Exception as exc:
            return {"status": "error", "msg": f"Skill find failed: {exc}"}

    @agent_tool(
        name="skillify",
        description="Create a reusable skill from the current task execution. "
                    "Call this after successfully completing a multi-step task "
                    "with a clear, repeatable process. "
                    "Reads your current todo list, analyzes the execution pattern, "
                    "and generates a SKILL.md with the workflow steps. "
                    "Parameters: name (short skill name), description (what this skill does)."
    )
    async def do_skillify(self, name: str, description: str) -> dict:
        """Create a reusable skill from current todo list execution."""
        if not name or not description:
            return {"status": "error", "msg": "name and description are required"}

        # Read current todo items
        todo = getattr(self, "_todo_store", None)
        if not todo:
            return {"status": "error", "msg": "Todo store not available"}

        items = todo.get()
        if not items:
            return {"status": "error", "msg": "No todo items to skillify"}

        completed = [it for it in items if it["status"] == "completed"]
        if len(completed) < 2:
            completed = items  # include pending if less than 2 completed

        # Generate SKILL.md from todo items
        steps = []
        for it in completed:
            step = f"1. **{it['content']}**"
            if it.get("done_criteria"):
                step += f"\n   完成标准: {it['done_criteria']}"
            steps.append(step)

        skill_md_lines = [
            "---",
            f"name: {name}",
            f"description: {description}",
            "---",
            "",
            f"# {name}",
            "",
            f"{description}",
            "",
            "## 工作流程",
            "",
        ]
        skill_md_lines.extend(steps)

        if not steps:
            skill_md_lines.append("（暂无具体步骤，请根据实际场景补充）")

        skill_md_lines.extend([
            "",
            "## 注意事项",
            "",
            "- 执行前确保所需工具可用",
            "- 每步完成后自检完成标准",
            "- 遇到阻碍换方案而非放弃",
        ])

        skill_md = "\n".join(skill_md_lines)

        # Save via do_skill_create's logic (inline to avoid duplicate tool call)
        import os, re
        normalized = name.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        if not normalized or len(normalized) > 64:
            return {"status": "error", "msg": "Invalid skill name"}

        target_dir = os.path.join(self.data_dir, "skills", "user_evolved")
        target = os.path.join(target_dir, normalized)
        if os.path.exists(target):
            return {"status": "error", "msg": f"Skill '{normalized}' already exists at {target}"}

        try:
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(skill_md)
        except OSError as exc:
            return {"status": "error", "msg": f"Cannot create skill: {exc}"}

        # Discover so it's immediately available
        if self._skill_mgr:
            self._skill_mgr.discover()

        return {
            "status": "success",
            "name": normalized,
            "path": target,
            "steps": len(completed),
        }

    async def do_skill_create(self, args: dict) -> dict:
        """Create a new skill scaffold with SKILL.md template.

        Creates a skill directory with proper frontmatter and optional
        resource directories (scripts/, references/, assets/).
        """
        name = args.get("name", "")
        description = args.get("description", "")
        resources = args.get("resources", "")
        path = args.get("path", "")

        if not name or not description:
            return {"status": "error", "msg": "name and description are required"}

        # Validate/normalize name
        import re
        normalized = name.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
        normalized = normalized.strip("-")
        if not normalized:
            return {"status": "error", "msg": "Invalid skill name after normalization"}
        if len(normalized) > 64:
            return {"status": "error", "msg": f"Skill name too long: {len(normalized)} > 64"}

        # Determine target directory
        if not path:
            path = os.path.join(self.data_dir, "skills", "user_evolved")
        target = os.path.join(path, normalized)
        if os.path.exists(target):
            return {"status": "error", "msg": f"Skill directory already exists: {target}"}

        # Create directory structure
        try:
            os.makedirs(target, exist_ok=False)
        except OSError as exc:
            return {"status": "error", "msg": f"Cannot create directory: {exc}"}

        # Write SKILL.md from template
        title = " ".join(word.capitalize() for word in normalized.split("-"))
        skill_md = f"""---
name: {normalized}
description: {description}
---

# {title}

## Overview

[TODO: Describe what this skill does and when to use it]

## Usage

[TODO: Add instructions, examples, and workflows]

## Resources (optional)

Delete this section if no resources are required.

- **scripts/** — Executable code for automation
- **references/** — Documentation loaded on demand
- **assets/** — Output templates and resources
"""
        skill_path = os.path.join(target, "SKILL.md")
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(skill_md)

        # Create optional resource directories
        if resources:
            allowed = {"scripts", "references", "assets"}
            for r in resources.split(","):
                r = r.strip()
                if r in allowed:
                    os.makedirs(os.path.join(target, r), exist_ok=True)

        # Trigger skill manager refresh
        if self._skill_mgr:
            self._skill_mgr.discover()

        return {
            "status": "success",
            "msg": f"Skill '{normalized}' created at {target}",
            "path": target,
            "skill_name": normalized,
        }

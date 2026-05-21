"""Management handler — non-chat operations for the Desktop WebSocket adapter.

Handles config, tasks, skills, evolution, and channel management requests.
Each handler receives a payload dict and returns a response dict.
"""
import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.oaa_agent import OAAAgent
    from ..agent.skill_manager import SkillManager
    from ..config import AppConfig
    from ..evolution.engine import EvolutionEngine
    from ..scheduler import TaskScheduler

from ..logging_config import get_logger

logger = get_logger("gateway.management")

# Known management message types
VALID_TYPES = {
    "get_config", "save_config",
    "get_status",
    "get_tasks", "save_task", "delete_task", "toggle_task",
    "get_skills", "get_skill_detail", "switch_skill", "get_evolution",
    "qr_login", "poll_qr",
    "reconnect_channel",
    "switch_model",
    "get_models",
    "stop_chat",
    "apply_evolution",
    # Evolution Factory
    "list_proposals",
    "proposal_approve",
    "proposal_ignore",
    "get_evolution_stats",
}
_ = VALID_TYPES  # prevent import-stripping


class ManagementHandler:
    """Dispatches management requests to the appropriate backend service."""

    def __init__(
        self,
        config: "AppConfig",
        scheduler: "TaskScheduler",
        skill_mgr: "SkillManager",
        evolution: "EvolutionEngine",
        channel_adapters: dict,
        agent: "OAAAgent | None" = None,
    ):
        self._config = config
        self._scheduler = scheduler
        self._skill_mgr = skill_mgr
        self._evolution = evolution
        self._channels = channel_adapters
        self._agent = agent

        # Agent runtime state (updated by DesktopAdapter during chat)
        self._agent_state = "idle"        # idle | thinking | executing | responding
        self._agent_state_since = time.time()
        self._start_time = time.time()    # process start for uptime
        self._chat_count = 0
        self._tool_call_count = 0

    def set_agent_state(self, state: str):
        """Update agent cognitive state. Called from DesktopAdapter."""
        self._agent_state = state
        self._agent_state_since = time.time()
        if state == "thinking":
            self._chat_count += 1

    async def handle(self, msg_type: str, payload: dict) -> dict:
        """Dispatch a management request. Returns a response dict (or coroutine)."""
        if msg_type not in VALID_TYPES:
            return {"ok": False, "error": f"Unknown management type: {msg_type}"}

        handler_name = f"_handle_{msg_type}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            return {"ok": False, "error": f"No handler for: {msg_type}"}

        try:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            logger.exception("Management handler %s failed: %s", msg_type, exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Agent status
    # ------------------------------------------------------------------

    def _handle_get_status(self, _payload: dict) -> dict:
        """Return current agent runtime status."""
        channels = {}
        for name, adapter in self._channels.items():
            channels[name] = {
                "online": getattr(adapter, "is_authenticated", False) or getattr(adapter, "_running", False),
            }

        # Count active sessions from the _config reference (stale reference, but close enough)
        uptime_sec = int(time.time() - self._start_time)

        return {
            "ok": True,
            "agent_state": self._agent_state,
            "agent_state_since": self._agent_state_since,
            "channels": channels,
            "chat_count": self._chat_count,
            "uptime_sec": uptime_sec,
            "timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _handle_get_config(self, _payload: dict) -> dict:
        """Return the app configuration with credential fields redacted."""
        return {"ok": True, "config": self._config.to_redacted_dict()}

    def _handle_save_config(self, payload: dict) -> dict:
        """Merge *payload.config* into current config and persist to disk."""
        data = payload.get("config", {})
        if not data:
            return {"ok": False, "error": "No config data provided"}

        # Merge top-level keys
        if "model" in data:
            m = data["model"]
            self._config.model.provider = m.get("provider", self._config.model.provider)
            self._config.model.plan = m.get("plan", self._config.model.plan)
            self._config.model.api_format = m.get("api_format", self._config.model.api_format)
            self._config.model.base_url = m.get("base_url", self._config.model.base_url)
            self._config.model.api_key = m.get("api_key", self._config.model.api_key)
            self._config.model.model_id = m.get("model_id", self._config.model.model_id)
            self._config.model.max_tokens = m.get("max_tokens", self._config.model.max_tokens)
            self._config.model.temperature = m.get("temperature", self._config.model.temperature)

        # Per-provider credentials (models dict — array format per provider)
        if "models" in data:
            raw = data["models"]
            # Normalize old single-dict format {prov: {api_key,...}} to list [{...}]
            if isinstance(raw, dict):
                normalized = {}
                for prov, val in raw.items():
                    if isinstance(val, dict):
                        # Old format: {prov: {api_key, model_id, base_url}}
                        entry = {"name": val.get("model_id", prov), "api_key": val.get("api_key", ""),
                                 "model_id": val.get("model_id", ""), "base_url": val.get("base_url", "")}
                        normalized[prov] = [entry]
                    elif isinstance(val, list):
                        normalized[prov] = val
                    else:
                        normalized[prov] = []
                self._config.models = normalized
            elif isinstance(raw, list):
                # Old format: list of {api_key,...} — shouldn't happen, but handle gracefully
                logger.warning("Unexpected models format (list), treating as noop")
            # Sync active provider's creds from first entry
            prov = self._config.model.provider
            entries = self._config.models.get(prov, [])
            if entries:
                entry = entries[0]
                if entry.get("api_key"):
                    self._config.model.api_key = entry["api_key"]
                if entry.get("model_id"):
                    self._config.model.model_id = entry["model_id"]
                if entry.get("base_url"):
                    self._config.model.base_url = entry["base_url"]

        if "wechat" in data:
            w = data["wechat"]
            self._config.wechat.enabled = w.get("enabled", self._config.wechat.enabled)
            self._config.wechat.iLink_token = w.get("iLink_token", self._config.wechat.iLink_token)
            self._config.wechat.iLink_bot_id = w.get("iLink_bot_id", self._config.wechat.iLink_bot_id)
            self._config.wechat.ilink_user_id = w.get("ilink_user_id", self._config.wechat.ilink_user_id)

        if "dingtalk" in data:
            d = data["dingtalk"]
            self._config.dingtalk.enabled = d.get("enabled", self._config.dingtalk.enabled)
            self._config.dingtalk.client_id = d.get("client_id", self._config.dingtalk.client_id)
            self._config.dingtalk.client_secret = d.get("client_secret", self._config.dingtalk.client_secret)

        if "feishu" in data:
            f = data["feishu"]
            self._config.feishu.enabled = f.get("enabled", self._config.feishu.enabled)
            self._config.feishu.app_id = f.get("app_id", self._config.feishu.app_id)
            self._config.feishu.app_secret = f.get("app_secret", self._config.feishu.app_secret)

        if "data_dir" in data:
            self._config.data_dir = data["data_dir"]

        if "permissions" in data:
            raw = data["permissions"]
            if isinstance(raw, str):
                # Normalize legacy string value (just permission_level) to dict
                old = getattr(self._config, 'permissions', {})
                if isinstance(old, dict):
                    old["permission_level"] = raw
                    self._config.permissions = old
                else:
                    self._config.permissions = {"permission_level": raw, "blacklist_paths": [], "require_confirm": ["email_send", "wechat_send"]}
            elif isinstance(raw, dict):
                self._config.permissions = raw

        # Backward-migrate any pre-models-dict config into the per-provider store
        prov = self._config.model.provider
        if prov and self._config.model.api_key:
            if prov not in self._config.models or not self._config.models.get(prov):
                self._config.models[prov] = [{
                    "name": self._config.model.model_id or prov,
                    "api_key": self._config.model.api_key,
                    "model_id": self._config.model.model_id,
                    "base_url": self._config.model.base_url,
                }]

        self._config.save()

        # Hot-reload LLM client so the new API key / base URL / model take effect immediately
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)

        return {"ok": True}

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def _handle_get_models(self, _payload: dict) -> dict:
        """Return all configured models and their credentials (list per provider)."""
        default_urls = {
            "deepseek": "https://api.deepseek.com",
            "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
            "tongyi": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "moonshot": "https://api.moonshot.cn/v1",
            "baichuan": "https://api.baichuan-ai.com/v1",
            "stepfun": "https://api.stepfun.com/v1",
            "minimax": "https://api.minimaxi.com/v1",
            "lingyi": "https://api.lingyiwanwu.com/v1",
            "xunfei": "https://maas-api.cn-huabei-1.xf-yun.com/v2",
            "xiaomi": "https://api.xiaomimimo.com/v1",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "custom-openai": "",
            "custom-anthropic": "",
        }
        models = {}
        for prov, default_url in default_urls.items():
            entries = self._config.models.get(prov, [])
            if not entries:
                models[prov] = []
            else:
                models[prov] = []
                for entry in entries:
                    models[prov].append({
                        "name": entry.get("name", ""),
                        "api_key": entry.get("api_key", ""),
                        "model_id": entry.get("model_id", ""),
                        "base_url": entry.get("base_url", default_url),
                    })
        return {
            "ok": True,
            "active": self._config.model.provider,
            "active_model_id": self._config.model.model_id,
            "models": models,
        }

    def _handle_switch_model(self, payload: dict) -> dict:
        """Switch the active model without going through settings.

        Accepts either ``provider`` alone (picks first entry for that provider)
        or ``provider`` + ``model_id`` (picks the matching entry).
        """
        provider = payload.get("provider", "")
        if not provider:
            return {"ok": False, "error": "No provider specified"}
        entries = self._config.models.get(provider, [])
        if not entries:
            # Provider exists in config but has no entries (unlikely edge case)
            self._config.model.provider = provider
            self._config.model.api_key = ""
            self._config.model.model_id = ""
            self._config.model.base_url = ""
            self._config.save()
            if self._agent is not None:
                self._agent.llm.reconfigure(self._config.model)
            return {"ok": True}

        # Pick the matching entry (or first if no model_id specified)
        model_id = payload.get("model_id", "")
        if model_id:
            selected = next((e for e in entries if e.get("model_id") == model_id), entries[0])
        else:
            selected = entries[0]

        self._config.model.provider = provider
        self._config.model.api_key = selected.get("api_key", "")
        self._config.model.model_id = selected.get("model_id", "")
        self._config.model.base_url = selected.get("base_url", "")

        # Ensure api_format matches provider
        if provider in ("anthropic", "custom-anthropic"):
            self._config.model.api_format = "anthropic"
        else:
            self._config.model.api_format = "openai"
        self._config.save()
        if self._agent is not None:
            self._agent.llm.reconfigure(self._config.model)
        return {"ok": True, "model_id": self._config.model.model_id}

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _handle_stop_chat(self, _payload: dict) -> dict:
        """Signal the current chat task to abort (best-effort)."""
        self.set_agent_state("idle")
        return {"ok": True}

    def _handle_apply_evolution(self, payload: dict) -> dict:
        """Mark an evolution suggestion as applied and remove from pending list."""
        title = payload.get("title", "")
        if not title:
            return {"ok": False, "error": "No title provided"}
        # Record in evolution stats
        if "applied" not in self._evolution.stats:
            self._evolution.stats["applied"] = []
        self._evolution.stats["applied"].append({
            "title": title,
            "applied_at": time.time(),
        })
        # Remove from suggestions list by matching title
        suggestions = self._evolution.stats.get("suggestions", [])
        for idx, s in enumerate(suggestions):
            s_title = s.get("skill", "") or s.get("message", "")[:20]
            if s_title in title or title in s.get("message", ""):
                self._evolution.accept_suggestion(idx)
                break
        self._evolution._save_stats()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def _handle_get_tasks(self, _payload: dict) -> dict:
        """Return all tasks from the scheduler."""
        tasks = self._scheduler.list_tasks(include_disabled=True)
        return {"ok": True, "tasks": tasks}

    def _handle_save_task(self, payload: dict) -> dict:
        """Create or update a task."""
        task_data = payload.get("task", {})
        if not task_data:
            return {"ok": False, "error": "No task data provided"}

        task_id = task_data.get("id", "")
        if task_id and self._scheduler.get(task_id):
            # Update existing
            updated = self._scheduler.update(task_id, task_data)
            return {"ok": True, "task": updated}
        else:
            # Create new
            created = self._scheduler.create(task_data)
            return {"ok": True, "task": created}

    def _handle_delete_task(self, payload: dict) -> dict:
        """Delete a task by id."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        deleted = self._scheduler.delete(task_id)
        return {"ok": deleted, "id": task_id}

    def _handle_toggle_task(self, payload: dict) -> dict:
        """Toggle task enabled/disabled."""
        task_id = payload.get("id", "")
        if not task_id:
            return {"ok": False, "error": "No task id provided"}
        task = self._scheduler.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task not found: {task_id}"}
        updated = self._scheduler.update(task_id, {"enabled": not task.get("enabled", True)})
        return {"ok": True, "task": updated}

    # ------------------------------------------------------------------
    # Skills & Evolution
    # ------------------------------------------------------------------

    def _handle_get_skills(self, _payload: dict) -> dict:
        """Return all active skills from SkillManager."""
        skills = []
        for info in self._skill_mgr.list_all():
            skills.append({
                "name": info.name,
                "category": info.category,
                "path": info.path,
                "loaded": bool(info.skill_md),
                "tools_count": len(info.tools) if info.tools else 0,
                "knowledge_count": len(info.knowledge) if info.knowledge else 0,
            })
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "skills": skills,
            "current": current.name if current else None,
            "total": len(skills),
        }

    def _handle_get_skill_detail(self, payload: dict) -> dict:
        """Return full detail for a single skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.get(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        info.load()  # ensure fresh
        current = self._skill_mgr.get_current()
        return {
            "ok": True,
            "name": info.name,
            "category": info.category,
            "description": info.description,
            "skill_md": info.skill_md,
            "sop_md": info.sop_md,
            "tools": info.tools,
            "knowledge": info.knowledge,
            "is_current": current is not None and current.name == info.name,
        }

    def _handle_switch_skill(self, payload: dict) -> dict:
        """Switch the active skill by name."""
        name = payload.get("name", "")
        if not name:
            return {"ok": False, "error": "No skill name provided"}
        info = self._skill_mgr.switch_to(name)
        if not info:
            return {"ok": False, "error": f"Skill not found: {name}"}
        return {"ok": True, "current": info.name}

    def _handle_get_evolution(self, _payload: dict) -> dict:
        """Return evolution statistics and suggestions from EvolutionEngine."""
        # Regenerate suggestions from current stats so threshold changes take effect
        self._evolution.analyze_for_suggestions()
        suggestions = self._evolution.stats.get("suggestions", [])
        skill_usage = self._evolution.stats.get("skill_usage", {})

        return {
            "ok": True,
            "stats": {
                "skill_usage": skill_usage,
                "sop_executions": self._evolution.stats.get("sop_executions", {}),
                "crystallized": self._evolution.stats.get("crystallized", []),
            },
            "suggestions": suggestions,
        }

    def _handle_get_evolution_stats(self, _payload: dict) -> dict:
        """Return aggregated statistics for the Evolution Factory statistics tab.

        Combines ProposalStore data (proposal counts, type distribution, daily
        execution trend) with EvolutionEngine data (skill usage, SOP stats).
        """
        # --- Proposal stats ---
        proposals = []
        rollback_count = 0
        if self._agent is not None and self._agent._proposal_store is not None:
            proposals = self._agent._proposal_store.all_proposals()

        total = len(proposals)
        status_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        daily_trend: dict[str, dict] = {}  # date -> {total, success, fail}
        for p in proposals:
            s = p.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

            t = p.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

            # Count rollbacks
            result_str = p.get("result", "")
            if result_str and ("rollback" in result_str):
                rollback_count += 1

            # Daily trend from executed_at
            ts = p.get("executed_at") or p.get("created_at")
            if ts:
                from datetime import datetime
                date_str = datetime.fromtimestamp(ts).strftime("%m-%d")
                if date_str not in daily_trend:
                    daily_trend[date_str] = {"total": 0, "success": 0, "fail": 0}
                daily_trend[date_str]["total"] += 1
                if s == "done":
                    daily_trend[date_str]["success"] += 1
                elif s == "failed":
                    daily_trend[date_str]["fail"] += 1

        done_count = status_counts.get("done", 0)
        failed_count = status_counts.get("failed", 0)
        success_rate = round((done_count / (done_count + failed_count) * 100)) if (done_count + failed_count) > 0 else 0

        # Sort trend by date
        trend_sorted = [{"date": d, **daily_trend[d]} for d in sorted(daily_trend.keys())]

        # --- Evolution engine stats ---
        skill_usage = self._evolution.stats.get("skill_usage", {})
        sop_executions = self._evolution.stats.get("sop_executions", {})
        crystallized = self._evolution.stats.get("crystallized", [])
        sop_skips = self._evolution.stats.get("sop_skips", {})

        # Sort skill usage descending
        skill_ranking = sorted(skill_usage.items(), key=lambda x: -x[1])

        return {
            "ok": True,
            "proposal_summary": {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "done": done_count,
                "failed": failed_count,
                "rolled_back": rollback_count,
                "ignored": status_counts.get("ignored_once", 0) + status_counts.get("ignored_forever", 0),
                "success_rate": success_rate,
            },
            "type_distribution": type_counts,
            "daily_trend": trend_sorted,
            "evolution": {
                "skill_usage": skill_usage,
                "skill_ranking": [{"name": k, "count": v} for k, v in skill_ranking[:10]],
                "sop_executions": sop_executions,
                "sop_skips": sop_skips,
                "crystallized": crystallized,
                "crystallized_count": len(crystallized),
            },
        }

    # ------------------------------------------------------------------
    # Evolution Factory
    # ------------------------------------------------------------------

    def _handle_list_proposals(self, payload: dict) -> dict:
        """Return proposals, optionally filtered by status."""
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        if store is None:
            return {"ok": False, "error": "Proposal store not available"}
        status = payload.get("status", "")
        if status:
            proposals = store.list_by_status(status)
        else:
            proposals = store.all_proposals()
        return {"ok": True, "proposals": proposals, "count": len(proposals)}

    async def _handle_proposal_approve(self, payload: dict) -> dict:
        """Approve and execute a proposal by ID."""
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        if store is None:
            return {"ok": False, "error": "Proposal store not available"}
        prop_id = payload.get("id", "")
        if not prop_id:
            return {"ok": False, "error": "No proposal ID provided"}
        proposal = store.get(prop_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal not found: {prop_id}"}
        if proposal.get("status") != "pending":
            return {"ok": False, "error": f"Proposal is not pending (status={proposal['status']})"}

        from ..agent.proposal import ProposalExecutor
        handler = self._agent.build_handler()
        executor = ProposalExecutor()
        try:
            result = await executor.execute(proposal, handler)
            store.update_status(
                result.get("id", prop_id), result["status"],
                executed_at=result.get("executed_at"),
                result=result.get("result"),
                error=result.get("error"),
            )

            # Inject execution result into HOT memory so agent sees it next turn
            self._inject_proposal_result(prop_id, result)

            return {
                "ok": True,
                "proposal_id": prop_id,
                "proposal_status": result["status"],
                "result": result.get("result"),
                "error": result.get("error"),
            }
        except Exception as exc:
            store.update_status(prop_id, "failed", error=str(exc))
            self._inject_proposal_result(prop_id, {"status": "failed", "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _inject_proposal_result(self, prop_id: str, result: dict):
        """Write proposal execution result into HOT memory for agent awareness.

        This bridges the gap between GUI-triggered execution and the agent's
        context — the agent will see the result in its system prompt on the
        next message and can learn from success/failure patterns, avoiding
        duplicate proposals for the same issue.
        """
        if self._agent is None:
            return
        try:
            memory = getattr(self._agent, 'memory', None)
            if memory is None or not hasattr(memory, 'add_to_hot'):
                return
            title = result.get("title", "")
            status = result.get("status", "unknown")
            error = result.get("error", "")
            summary_parts = [f"[进化工厂] 提案 {prop_id[:16]}"]
            if title:
                summary_parts.append(f"「{title}」")
            if status == "done":
                summary_parts.append("已执行完成")
            elif status == "failed":
                summary_parts.append(f"执行失败: {error[:100]}")
            else:
                summary_parts.append(f"状态: {status}")
            memory.add_to_hot(" ".join(summary_parts))
        except Exception as exc:
            logger.debug("Failed to inject proposal result: %s", exc)

    def _handle_proposal_ignore(self, payload: dict) -> dict:
        """Ignore a proposal by ID, optionally permanently."""
        if self._agent is None:
            return {"ok": False, "error": "Agent not initialized"}
        store = self._agent._proposal_store
        inspector = self._agent._idle_inspector
        if store is None or inspector is None:
            return {"ok": False, "error": "Proposal system not available"}
        prop_id = payload.get("id", "")
        permanent = payload.get("permanent", False)
        if not prop_id:
            return {"ok": False, "error": "No proposal ID provided"}
        proposal = store.get(prop_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal not found: {prop_id}"}
        # Use target from proposal for ignore list, fall back to id
        target = proposal.get("target", prop_id)
        inspector.ignore_tool(target, permanent=permanent)
        new_status = "ignored_forever" if permanent else "ignored_once"
        store.update_status(prop_id, new_status)
        self._inject_proposal_result(prop_id, {
            "title": proposal.get("title", ""),
            "status": new_status,
        })
        return {"ok": True, "proposal_id": prop_id, "status": new_status}

    # ------------------------------------------------------------------
    # QR / Channel login
    # ------------------------------------------------------------------

    async def _handle_qr_login(self, payload: dict) -> dict:
        """Initiate QR code login for a channel (wechat/dingtalk/feishu)."""
        channel = payload.get("channel", "")
        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "get_qrcode"):
            return {"ok": False, "error": f"Channel {channel} does not support QR login"}

        if channel == "dingtalk":
            cid = payload.get("client_id", "")
            sec = payload.get("client_secret", "")
            if cid:
                adapter.client_id = cid
            if sec:
                adapter.client_secret = sec
        elif channel == "feishu":
            aid = payload.get("app_id")
            asec = payload.get("app_secret")
            if aid is not None:
                adapter.app_id = aid
            if asec is not None:
                adapter.app_secret = asec
            logger.info("[FeishuQR] app_id=%s app_secret=%s", "SET" if aid else "EMPTY", "SET" if asec else "EMPTY")

        result = adapter.get_qrcode()
        if asyncio.iscoroutine(result):
            result = await result
        if "error" in result:
            return {"ok": False, "error": result["error"]}

        return {
            "ok": True,
            "qr_code_url": result.get("qrcode_url", ""),
            "qr_code_id": result.get("qrcode_id", ""),
            "user_code": result.get("user_code", ""),
            "channel": channel,
        }

    async def _handle_poll_qr(self, payload: dict) -> dict:
        """Poll QR code scan status."""
        channel = payload.get("channel", "")
        qrcode_id = payload.get("qrcode_id", "")

        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if not hasattr(adapter, "poll_qrcode_status"):
            return {"ok": False, "error": f"Channel {channel} does not support QR polling"}

        result = adapter.poll_qrcode_status(qrcode_id)
        if asyncio.iscoroutine(result):
            result = await result
        logger.info("poll_qr channel=%s qrcode_id=%s result=%s", channel, qrcode_id[:16], result)
        if result.get("status") == "confirmed":
            if channel == "wechat":
                token = result.get("bot_token", "")
                if token:
                    self._config.wechat.iLink_token = token
                    self._config.wechat.iLink_bot_id = result.get("ilink_bot_id", self._config.wechat.iLink_bot_id)
                    self._config.wechat.ilink_user_id = result.get("ilink_user_id", self._config.wechat.ilink_user_id)
                    self._config.wechat.base_url = result.get("base_url", self._config.wechat.base_url)
                    self._config.wechat.enabled = True
                    self._config.save()
                    # Update adapter instance so it can actually send/receive
                    adapter.token = token
                    adapter.bot_id = self._config.wechat.iLink_bot_id
                    adapter.base_url = self._config.wechat.base_url
                    adapter._bot._base_url = self._config.wechat.base_url
                    # Restart polling with new credentials
                    if hasattr(adapter, "stop_polling"):
                        adapter.stop_polling()
                    if hasattr(adapter, "start_polling"):
                        asyncio.create_task(adapter.start_polling())
            elif channel == "dingtalk":
                self._config.dingtalk.client_id = getattr(adapter, "client_id", "")
                self._config.dingtalk.client_secret = getattr(adapter, "client_secret", "")
                self._config.dingtalk.enabled = True
                self._config.save()
                # Start the Stream client
                if hasattr(adapter, "start") and callable(adapter.start):
                    result_or_coro = adapter.start()
                    if asyncio.iscoroutine(result_or_coro):
                        asyncio.create_task(result_or_coro)
            elif channel == "feishu":
                self._config.feishu.app_id = getattr(adapter, "app_id", "")
                self._config.feishu.app_secret = getattr(adapter, "app_secret", "")
                self._config.feishu.enabled = True
                self._config.save()
                # Start the WebSocket event client
                if hasattr(adapter, "start") and callable(adapter.start):
                    result_or_coro = adapter.start()
                    if asyncio.iscoroutine(result_or_coro):
                        asyncio.create_task(result_or_coro)

            # Notify the newly-connected channel with a welcome message
            if self._agent and self._agent._on_channel_ready:
                asyncio.create_task(self._agent._on_channel_ready(channel))

        return {
            "ok": True,
            "status": result.get("status", "waiting"),
            "msg": result.get("msg", ""),
        }

    def _handle_reconnect_channel(self, payload: dict) -> dict:
        """Reconnect a channel using saved credentials (no QR scan)."""
        channel = payload.get("channel", "")
        if channel not in self._channels:
            return {"ok": False, "error": f"Unknown channel: {channel}"}

        adapter = self._channels[channel]
        if channel == "wechat":
            token = self._config.wechat.iLink_token
            base_url = self._config.wechat.base_url
            if not token or not base_url:
                return {"ok": False, "error": "微信未认证，请先扫码登录"}
            adapter.token = token
            adapter.base_url = base_url
            adapter.bot_id = self._config.wechat.iLink_bot_id
            adapter._bot._base_url = base_url
            # Restart polling with updated credentials
            if hasattr(adapter, "stop_polling"):
                adapter.stop_polling()
            if hasattr(adapter, "start_polling"):
                import asyncio
                asyncio.create_task(adapter.start_polling())
            if self._agent and self._agent._on_channel_ready:
                asyncio.create_task(self._agent._on_channel_ready(channel))
            return {"ok": True, "online": True, "msg": "微信已重连"}
        elif channel == "dingtalk":
            cid = self._config.dingtalk.client_id
            sec = self._config.dingtalk.client_secret
            if not cid or not sec:
                return {"ok": False, "error": "钉钉未配置凭证"}
            if hasattr(adapter, "client_id"):
                adapter.client_id = cid
            if hasattr(adapter, "client_secret"):
                adapter.client_secret = sec
            # Start the Stream client
            if hasattr(adapter, "start") and callable(adapter.start):
                result_or_coro = adapter.start()
                if asyncio.iscoroutine(result_or_coro):
                    asyncio.create_task(result_or_coro)
            return {"ok": True, "online": True, "msg": "钉钉已重连"}
        elif channel == "feishu":
            aid = self._config.feishu.app_id
            asec = self._config.feishu.app_secret
            if not aid or not asec:
                return {"ok": False, "error": "飞书未配置凭证"}
            if hasattr(adapter, "app_id"):
                adapter.app_id = aid
            if hasattr(adapter, "app_secret"):
                adapter.app_secret = asec
            # Start the WebSocket event client
            if hasattr(adapter, "start") and callable(adapter.start):
                result_or_coro = adapter.start()
                if asyncio.iscoroutine(result_or_coro):
                    asyncio.create_task(result_or_coro)
            return {"ok": True, "online": True, "msg": "飞书已重连"}
        else:
            return {"ok": False, "error": f"Channel {channel} reconnect not implemented"}


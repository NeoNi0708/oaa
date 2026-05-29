"""Evolution mixin — evolution engine operations and proposal execution."""
import asyncio
import time
from ...logging_config import get_logger

logger = get_logger("gateway.management")


class EvolutionMixin:
    """Evolution engine and proposal management."""

    async def _handle_apply_evolution(self, payload: dict) -> dict:
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
            s_title = (s.get("skill", "") or s.get("message", "") or "").strip()
            if not s_title:
                continue
            if s_title in title or title in s.get("message", ""):
                await self._evolution.accept_suggestion(idx)
                break
        return {"ok": True}

    async def _handle_get_evolution(self, _payload: dict) -> dict:
        """Return evolution statistics and suggestions from EvolutionEngine."""
        # Regenerate suggestions from current stats so threshold changes take effect
        await self._evolution.analyze_for_suggestions()
        suggestions = self._evolution.stats.get("suggestions", [])
        skill_usage = self._evolution.stats.get("skill_usage", {})

        # Mark previously-applied suggestions so frontend can persist "已应用" state
        applied_titles = {a.get("title", "") for a in self._evolution.stats.get("applied", [])}
        for s in suggestions:
            s_title = s.get("skill", "") or s.get("message", "")[:20]
            if s_title in applied_titles or any(
                at in s.get("message", "") for at in applied_titles if at
            ):
                s["applied"] = True

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
        """Approve and execute a proposal by ID (non-blocking)."""
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

        await store.update_status(prop_id, "running")

        # Schedule background execution and return immediately
        agent = self._agent
        config = self._config
        notify = self._push_notification
        inject = self._inject_proposal_result

        asyncio.create_task(self._execute_proposal_bg(
            prop_id, proposal, agent, store, config, notify, inject,
        ))

        return {
            "ok": True,
            "proposal_id": prop_id,
            "proposal_status": "running",
        }

    async def _execute_proposal_bg(
        self,
        prop_id: str,
        proposal: dict,
        agent,
        store,
        config,
        notify,
        inject_result,
    ):
        """Background task that runs repair_loop or ProposalExecutor."""
        problem_context = proposal.get("problem_context")

        if problem_context:
            await self._run_repair_bg(
                prop_id, proposal, problem_context,
                agent, store, config, notify, inject_result,
            )
        else:
            await self._run_executor_bg(
                prop_id, proposal,
                agent, store, notify, inject_result,
            )

    async def _run_repair_bg(self, prop_id, proposal, problem_context,
                              agent, store, config, notify, inject_result):
        """Run repair_loop in background."""
        from ..agent.repair_loop import RepairLoop, RepairPlan

        plan = RepairPlan(
            proposal_id=prop_id,
            problem_context=problem_context,
        )
        repair_loop = RepairLoop(data_dir=config.data_dir)
        agent_ref = agent

        async def _make_tool_verifier(ctx: dict) -> tuple[bool, str]:
            tool_name = ctx.get("tool_name", "")
            if not tool_name:
                return False, "无法验证：context 缺少 tool_name"
            try:
                memory = getattr(agent_ref, 'memory', None)
                if memory and hasattr(memory, 'get_tool_failures'):
                    recent = memory.get_tool_failures(tool_name, limit=1)
                    if recent:
                        return False, f"{tool_name} 仍有失败记录: {recent[0].get('error', 'unknown')[:100]}"
            except Exception as exc:
                logger.warning("Tool-failure verifier failed for %s: %s", tool_name, exc)
            return True, f"已确认 {tool_name} 无新失败记录"

        repair_loop.register_verifier("tool_failure", _make_tool_verifier)

        try:
            result = await repair_loop.run(
                plan, agent,
                inspector=getattr(agent, '_idle_inspector', None),
            )
            new_status = "done" if result["status"] == "done" else "failed"
            await store.update_status(
                prop_id, new_status,
                executed_at=time.time(),
                result=result.get("message", ""),
                error=None if result["status"] == "done" else result.get("message"),
            )
            await inject_result(prop_id, {
                "title": proposal.get("title", ""),
                "status": new_status,
                "result": result,
            })
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": new_status,
                "result": result.get("message", ""),
                "error": result.get("message") if result["status"] != "done" else None,
            })
        except Exception as exc:
            await store.update_status(prop_id, "failed", error=str(exc))
            await inject_result(prop_id, {"status": "failed", "error": str(exc)})
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": "failed",
                "error": str(exc),
            })

    async def _run_executor_bg(self, prop_id, proposal, agent, store, notify, inject_result):
        """Run ProposalExecutor in background."""
        from ..agent.proposal import ProposalExecutor
        handler = agent.build_handler()
        executor_obj = ProposalExecutor()

        inspector = getattr(agent, '_idle_inspector', None)
        if inspector:
            inspector.pause()
        try:
            result = await executor_obj.execute(proposal, handler)
            await store.update_status(
                result.get("id", prop_id), result["status"],
                executed_at=result.get("executed_at"),
                result=result.get("result"),
                error=result.get("error"),
            )
            await inject_result(prop_id, result)
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": result["status"],
                "result": result.get("result"),
                "error": result.get("error"),
            })
        except Exception as exc:
            await store.update_status(prop_id, "failed", error=str(exc))
            await inject_result(prop_id, {"status": "failed", "error": str(exc)})
            notify("proposal_completed", {
                "proposal_id": prop_id,
                "proposal_status": "failed",
                "error": str(exc),
            })
        finally:
            if inspector:
                inspector.resume()

    async def _inject_proposal_result(self, prop_id: str, result: dict):
        """Write proposal execution result into HOT memory for agent awareness."""
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
            await memory.add_to_hot(" ".join(summary_parts))
        except Exception as exc:
            logger.warning("Failed to inject proposal result: %s", exc)

    async def _handle_proposal_ignore(self, payload: dict) -> dict:
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
        await store.update_status(prop_id, new_status)
        await self._inject_proposal_result(prop_id, {
            "title": proposal.get("title", ""),
            "status": new_status,
        })
        return {"ok": True, "proposal_id": prop_id, "status": new_status}

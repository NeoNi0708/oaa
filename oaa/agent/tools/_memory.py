"""Memory and learning mixin — checkpoint, correction, memory_recall, chat_history, reflect."""

from ...logging_config import get_logger
from ..tool_decorator import agent_tool

logger = get_logger("agent.tools.memory")


class MemoryMixin:
    """Mixin for memory and learning tools."""

    @agent_tool(
        name="update_working_checkpoint",
        description="Save a user preference, rule, or key fact to persistent HOT memory. Automatically compacted when full. Survives across restarts and sessions."
    )
    async def do_update_working_checkpoint(self, key_info: str) -> dict:
        if not key_info:
            return {"status": "error", "msg": "key_info is required"}
        store = getattr(self, "_memory_store", None)
        if store is not None:
            store.add(key_info, mem_type="fact", source="user")
            return {"status": "success", "msg": "已存入结构化记忆"}
        # Fallback
        if self._memory_mgr:
            return await self._memory_mgr.add_to_hot(key_info)
        return {"status": "error", "msg": "Memory not available"}

    @agent_tool(
        name="correction_log",
        description="Log a user correction so the model remembers next time. Call when user says '不对', '不是', '你错了', '我告诉过你', or otherwise corrects you."
    )
    async def do_correction_log(self, context: str, lesson: str) -> dict:
        if not context or not lesson:
            return {"status": "error", "msg": "context and lesson are required"}
        store = getattr(self, "_memory_store", None)
        if store is not None:
            store.add(
                f"修正：{context} → {lesson}",
                mem_type="pattern", source="user",
                tags=["correction"],
            )
            return {"status": "success", "msg": "已存入结构化记忆"}
        # Fallback
        if self._memory_mgr:
            return self._memory_mgr.add_correction(context, lesson)
        return {"status": "error", "msg": "Memory manager not available"}

    @agent_tool(
        name="memory_recall",
        description="Search across all memory tiers (HOT + corrections + warm) for a keyword. Use when user asks '还记得吗', '我之前说过', or you need to find past learnings."
    )
    async def do_memory_recall(self, query: str) -> dict:
        if not query:
            return {"status": "error", "msg": "query is required"}
        # Try structured memory store first (semantic search)
        store = getattr(self, "_memory_store", None)
        if store is not None:
            results = store.search(query)
            return {
                "status": "success",
                "results": [{"id": r.item.id, "text": r.full_text,
                             "score": r.score, "type": r.item.mem_type,
                             "source": r.item.source,
                             "importance": r.item.importance}
                            for r in results],
                "count": len(results),
            }
        # Fallback to legacy keyword search
        if self._memory_mgr:
            return self._memory_mgr.search(query)
        return {"status": "error", "msg": "Memory not available"}

    @agent_tool(
        name="chat_history_search",
        description="Search past conversation summaries by keyword. Use when user asks about previous discussions ('我们之前聊过什么', '上次那个客户', '我记得说过...'). Returns structured summaries of past sessions sorted by relevance."
    )
    async def do_chat_history_search(self, query: str, limit: int = 10) -> dict:
        if not query:
            return {"status": "error", "msg": "query is required"}
        if not self._archiver:
            return {"status": "error", "msg": "对话归档模块未就绪"}
        limit = min(limit, 30)
        matches = self._archiver.search(query, limit=limit)
        return {"status": "success", "query": query, "matches": matches, "total": len(matches)}

    @agent_tool(
        name="self_reflect",
        description="After completing significant work, reflect on what went well and what could be improved. Call at the end of multi-step tasks to log lessons learned."
    )
    async def do_self_reflect(self, context: str, reflection: str, lesson: str = "") -> dict:
        if not context or not reflection:
            return {"status": "error", "msg": "context and reflection are required"}
        msg = f"[Self-reflection] {context}: {reflection}"
        if lesson:
            msg += f" → Lesson: {lesson}"
        store = getattr(self, "_memory_store", None)
        if store is not None:
            store.add(msg, mem_type="pattern", source="agent")
        elif self._memory_mgr:
            await self._memory_mgr.add_to_hot(msg)
        return {"status": "success", "msg": "Reflection saved"}

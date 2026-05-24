"""Base handler — tool dispatch mechanism, adapted from GenericAgent's BaseHandler."""
import time
from typing import Any

from ..logging_config import get_logger

logger = get_logger("agent.handler")


def _result_summary(result: Any, max_len: int = 100) -> str:
    """Short summary of a tool result for logging."""
    if isinstance(result, dict):
        status = result.get("status", "ok")
        if status == "error":
            return f"error: {str(result.get('msg', ''))[:80]}"
        # Try to extract meaningful size hint
        for key in ("result", "content", "output", "text"):
            val = result.get(key)
            if val is not None:
                s = str(val)
                return f"ok ({len(s)} chars)"
        return f"ok ({len(str(result))} bytes)"
    s = str(result)
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


class BaseHandler:
    """Tool dispatcher. Override ``do_<tool_name>`` for each tool.

    Subclasses implement methods named ``do_<tool_name>(self, args: dict) -> Any``.
    The base ``dispatch`` method resolves the tool name, calls the matching
    method, and returns the result.

    Tools can also be registered via the ``@agent_tool`` decorator (see
    :mod:`tool_decorator`).  Decorated methods are automatically collected
    into the class-level ``_tool_registry`` dict by ``__init_subclass__``.
    """

    _tool_registry: dict[str, Any] = {}  # tool_name -> bound-method wrapper
    _dynamic_tools: dict[str, dict] = {}  # tool_name -> {"path": str, "schema": dict}

    def __init_subclass__(cls, **kwargs):
        """Auto-collect tools decorated with ``@agent_tool``."""
        super().__init_subclass__(**kwargs)
        registry: dict[str, Any] = {}
        for name in dir(cls):
            method = getattr(cls, name, None)
            meta = getattr(method, "_tool_meta", None)
            if meta:
                registry[meta["name"]] = method
        cls._tool_registry = registry

    async def dispatch(self, tool_name: str, args: dict) -> Any:
        """Look up ``do_{tool_name}`` or ``_tool_registry`` and call it with *args*.

        Returns the result of the handler method, or an error dict if the
        tool is not recognised.
        """
        method_name = f"do_{tool_name}"
        start = time.time()
        try:
            if hasattr(self, method_name):
                method = getattr(self, method_name)
                result = await method(args)
            elif tool_name in self._tool_registry:
                result = await self._tool_registry[tool_name](self, args)
            else:
                logger.warning("Unknown tool: %s", tool_name)
                return {"status": "error", "msg": f"Unknown tool: {tool_name}"}
            elapsed = time.time() - start
            logger.debug("Handler %s → %s (%.2fs)", tool_name, _result_summary(result), elapsed)
            return result
        except Exception as exc:
            elapsed = time.time() - start
            logger.warning("Handler %s raised %s (%.2fs): %s", tool_name, type(exc).__name__, elapsed, exc)
            raise

    def register_dynamic(self, tool_name: str, filepath: str, schema: dict):
        """Register a dynamic tool created at runtime."""
        self._dynamic_tools[tool_name] = {"path": filepath, "schema": schema}

    def unregister_dynamic(self, tool_name: str):
        """Remove a dynamic tool from the registry."""
        self._dynamic_tools.pop(tool_name, None)

    def get_dynamic_tool_names(self) -> list[str]:
        """Return names of all registered dynamic tools."""
        return list(self._dynamic_tools.keys())

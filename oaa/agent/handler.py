"""Base handler — tool dispatch mechanism, adapted from GenericAgent's BaseHandler."""
from typing import Any


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
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(args)
        # Fall back to decorator registry
        if tool_name in self._tool_registry:
            return await self._tool_registry[tool_name](self, args)
        return {"status": "error", "msg": f"Unknown tool: {tool_name}"}

    def register_dynamic(self, tool_name: str, filepath: str, schema: dict):
        """Register a dynamic tool created at runtime."""
        self._dynamic_tools[tool_name] = {"path": filepath, "schema": schema}

    def unregister_dynamic(self, tool_name: str):
        """Remove a dynamic tool from the registry."""
        self._dynamic_tools.pop(tool_name, None)

    def get_dynamic_tool_names(self) -> list[str]:
        """Return names of all registered dynamic tools."""
        return list(self._dynamic_tools.keys())

"""Base handler -- tool dispatch mechanism, adapted from GenericAgent's BaseHandler."""
from typing import Any


class BaseHandler:
    """Tool dispatcher. Override ``do_<tool_name>`` for each tool.

    Subclasses implement methods named ``do_<tool_name>(self, args: dict) -> Any``.
    The base ``dispatch`` method resolves the tool name, calls the matching
    method, and returns the result.
    """

    async def dispatch(self, tool_name: str, args: dict) -> Any:
        """Look up ``do_{tool_name}`` and call it with *args*.

        Returns the result of the handler method, or an error dict if the
        tool is not recognised.
        """
        method_name = f"do_{tool_name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(args)
        return {"status": "error", "msg": f"Unknown tool: {tool_name}"}

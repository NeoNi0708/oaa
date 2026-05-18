"""Tool decorator — ``@agent_tool`` for automatic schema generation and registration.

Usage::

    from .tool_decorator import agent_tool

    class MyTools(BaseHandler):

        @agent_tool(description="Read file content")
        async def do_file_read(self, path: str, start: int = 1, count: int = 200) -> dict:
            ...

The decorator auto-generates an OpenAI-compatible function-calling schema from
the method's type-annotated signature and registers it in the class-level
``_tool_registry`` dict (collected automatically by ``BaseHandler.__init_subclass__``).

Legacy methods that accept ``(self, args: dict)`` are also supported — they are
passed through without wrapper and retain their manually-defined schema (from
``tool_schema.py``) until migrated.
"""
import functools
import inspect
from typing import get_origin


def agent_tool(name: str = "", description: str = ""):
    """Decorator that registers a method as an agent tool.

    Args:
        name: Tool name (defaults to ``do_``-stripped method name).
        description: Tool description (defaults to method docstring).
    """
    def decorator(func):
        tool_name = name or (
            func.__name__[3:] if func.__name__.startswith("do_") else func.__name__
        )
        desc = description or (func.__doc__ or "").strip()

        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        if params == ["self", "args"] or params == ["args"]:
            # Legacy style — pass ``args`` dict through unchanged
            func._tool_meta = {
                "name": tool_name,
                "description": desc,
                "schema": None,  # defined in tool_schema.py
                "style": "legacy",
            }
            return func

        # New style — explicit params, auto-generate schema, wrap for dispatch
        schema = _build_schema(func, tool_name, desc)

        @functools.wraps(func)
        def wrapper(self_, args: dict):
            kwargs = {
                k: args[k]
                for k in params
                if k != "self" and k in args
            }
            return func(self_, **kwargs)

        wrapper._tool_meta = {
            "name": tool_name,
            "description": desc,
            "schema": schema,
            "style": "explicit",
        }
        return wrapper

    return decorator


def _build_schema(func, tool_name: str, description: str) -> dict:
    """Build an OpenAI-compatible function-calling schema from *func*'s signature."""
    sig = inspect.signature(func)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        json_type = _annotation_to_json_type(param.annotation)
        prop: dict = {"type": json_type, "description": param_name}
        if param.default is not inspect.Parameter.empty:
            if json_type == "array" and param.default is None:
                prop["default"] = []
            else:
                prop["default"] = param.default
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        properties[param_name] = prop

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _annotation_to_json_type(annotation) -> str:
    """Map a Python type annotation to a JSON schema type string."""
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = get_origin(annotation)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is list or annotation is list:
        return "array"
    if annotation is dict or annotation is dict:
        return "object"
    if annotation is bytes:
        return "string"
    return "string"


def collect_tool_schemas(cls: type) -> list[dict]:
    """Collect all ``@agent_tool``-registered schemas from *cls* and its bases.

    Returns a flat list of OpenAI-compatible schema dicts, excluding legacy
    tools whose schema is ``None`` (still defined in ``tool_schema.py``).
    """
    schemas = []
    for klass in cls.__mro__:
        for _name, method in klass.__dict__.items():
            meta = getattr(method, "_tool_meta", None)
            if meta and meta.get("schema"):
                schemas.append(meta["schema"])
    return schemas

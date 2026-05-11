from .handler import BaseHandler
from .loop import AgentLoop
from .skill_manager import SkillManager


def __getattr__(name):
    """Lazy-import OAAAgent to break circular import (agent -> extended_tools -> gateway -> agent)."""
    if name == "OAAAgent":
        from .oaa_agent import OAAAgent
        return OAAAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AgentLoop", "BaseHandler", "SkillManager", "OAAAgent"]

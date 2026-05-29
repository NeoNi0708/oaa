"""Extended tools — aggregation class combining all domain mixins.

Import compatibility::

    from .extended_tools import ExtendedTools

remains unchanged.
"""
from .extended import (
    CoreMixin, EmailMixin, OfficeMixin, PlannerMixin,
    GithubMixin, SkillMixin, McpMixin, WechatMixin,
    FeishuMixin, DingtalkMixin, PatchMixin, ImageGenMixin,
)

# Re-export the constant so anything importing it from this module still works.
from .extended.core_mixin import DYNAMIC_TOOLS_DIR  # noqa: F401


class ExtendedTools(
    CoreMixin, EmailMixin, OfficeMixin, PlannerMixin,
    GithubMixin, SkillMixin, McpMixin, WechatMixin,
    FeishuMixin, DingtalkMixin, PatchMixin, ImageGenMixin,
):
    """Aggregation class for all extended tools.

    Every tool method is inherited from its domain mixin above.
    The MRO resolves in declaration order — CoreMixin.__init__ runs first.
    """

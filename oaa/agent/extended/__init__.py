"""Extended tools mixin package — each domain gets its own mixin class."""
from .core_mixin import CoreMixin
from .email_mixin import EmailMixin
from .office_mixin import OfficeMixin
from .planner_mixin import PlannerMixin
from .github_mixin import GithubMixin
from .skill_mixin import SkillMixin
from .mcp_mixin import McpMixin
from .wechat_mixin import WechatMixin
from .feishu_mixin import FeishuMixin
from .dingtalk_mixin import DingtalkMixin
from .patch_mixin import PatchMixin
from .image_gen_mixin import ImageGenMixin

__all__ = [
    "CoreMixin", "EmailMixin", "OfficeMixin", "PlannerMixin",
    "GithubMixin", "SkillMixin", "McpMixin", "WechatMixin",
    "FeishuMixin", "DingtalkMixin", "PatchMixin", "ImageGenMixin",
]

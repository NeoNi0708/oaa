"""Management handler mixin package — domain-split mixins for ManagementHandler."""
from .core_mixin import CoreMixin
from .healthcheck_mixin import HealthcheckMixin
from .config_mixin import ConfigMixin
from .evolution_mixin import EvolutionMixin
from .tasks_skills_mixin import TasksSkillsMixin
from .channel_mixin import ChannelMixin
from .email_mixin import EmailMixin
from .preferences_mixin import PreferencesMixin
from .patches_mixin import PatchesMixin

__all__ = [
    "CoreMixin", "HealthcheckMixin", "ConfigMixin", "EvolutionMixin",
    "TasksSkillsMixin", "ChannelMixin", "EmailMixin",
    "PreferencesMixin", "PatchesMixin",
]

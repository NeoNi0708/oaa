"""Management handler — aggregation class combining all domain mixins.

Import compatibility::

    from .management import ManagementHandler

remains unchanged.
"""
from .mgmt import (
    CoreMixin, HealthcheckMixin, ConfigMixin, EvolutionMixin,
    TasksSkillsMixin, ChannelMixin, EmailMixin,
    PreferencesMixin, PatchesMixin,
)

# Re-export constants / module-level helpers
from .mgmt.core_mixin import VALID_TYPES  # noqa: F401
from .mgmt.tool_failure_verifier import _tool_failure_verifier  # noqa: F401


class ManagementHandler(
    CoreMixin, HealthcheckMixin, ConfigMixin, EvolutionMixin,
    TasksSkillsMixin, ChannelMixin, EmailMixin,
    PreferencesMixin, PatchesMixin,
):
    """Aggregation class for all management handlers.

    Every ``_handle_*`` method is inherited from its domain mixin above.
    The ``handle()`` dispatcher in CoreMixin resolves via ``getattr(self, name)``
    and finds all handlers through normal MRO.
    """

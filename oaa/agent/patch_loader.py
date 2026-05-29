"""Startup patch loader — re-applies all active patches at startup.

Usage in ``app.py``::

    from oaa.agent.patch_loader import load_all

    patches_dir = os.path.join(self.config.data_dir, "patches")
    load_all(patches_dir)
"""
import os
from logging import getLogger

from .patch_manager import PatchManager

logger = getLogger("agent.patch_loader")


def load_all(patches_dir: str) -> int:
    """Scan *patches_dir* for active patches and re-apply them.

    Returns the number of patches successfully applied.
    """
    if not os.path.isdir(patches_dir):
        logger.debug("No patches directory at %s — skipping", patches_dir)
        return 0

    mgr = PatchManager(patches_dir)
    applied = mgr.load_active()
    if applied:
        logger.info("Startup: applied %d runtime patch(es)", len(applied))
        for p in applied:
            logger.info("  [%s] %s → %s.%s", p["id"], p.get("description", ""),
                        p["target_module"], p["target_attr"])
    else:
        logger.info("Startup: no active patches to apply")
    return len(applied)

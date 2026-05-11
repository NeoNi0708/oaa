"""Test permissions."""
import pytest
import tempfile
from oaa.auth.permissions import PermissionsManager, PermissionDenied
from oaa.config import AppConfig


def test_path_blacklist():
    config = AppConfig(permissions={"blacklist_paths": ["C:\\Windows"]})
    pm = PermissionsManager(config)
    with pytest.raises(PermissionDenied):
        pm.check_path("C:\\Windows\\System32")
    pm.check_path("C:\\Users")  # should not raise

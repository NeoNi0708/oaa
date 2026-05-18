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


@pytest.mark.asyncio
async def test_auto_level_allows_all():
    """Auto level should approve all ops (dangerous ones logged)."""
    config = AppConfig(permissions={"permission_level": "auto"})
    pm = PermissionsManager(config)
    assert await pm.confirm_operation("shell_run", "echo test")
    assert await pm.confirm_operation("file_write", "/etc/passwd")
    assert await pm.confirm_operation("email_send", "to: test@test.com")
    # Unknown ops also allowed
    assert await pm.confirm_operation("read_file", "/some/path")


@pytest.mark.asyncio
async def test_confirm_level_blocks_unlisted():
    """Confirm level only blocks ops in require_confirm list."""
    config = AppConfig(permissions={
        "permission_level": "confirm",
        "require_confirm": ["email_send"],
    })
    pm = PermissionsManager(config)
    # Not in require_confirm -> allowed (no callback, so returns False for required)
    # shell_run not in the list -> auto True
    # Actually with no callback set, require_confirm returns False
    assert await pm.confirm_operation("shell_run", "echo test")
    # email_send is in require_confirm but no callback -> False
    assert not await pm.confirm_operation("email_send", "test")


@pytest.mark.asyncio
async def test_confirm_level_with_callback():
    """Confirm level calls callback for listed ops."""
    called = []

    async def mock_cb(op, details):
        called.append((op, details))
        return True

    config = AppConfig(permissions={
        "permission_level": "confirm",
        "require_confirm": ["email_send"],
    })
    pm = PermissionsManager(config, confirm_callback=mock_cb)
    assert await pm.confirm_operation("email_send", "test@test.com")
    assert len(called) == 1
    assert called[0][0] == "email_send"


@pytest.mark.asyncio
async def test_restrict_level_blocks_dangerous():
    """Restrict level should block dangerous ops without callback."""
    config = AppConfig(permissions={"permission_level": "restrict"})
    pm = PermissionsManager(config)
    assert not await pm.confirm_operation("shell_run", "rm -rf /")
    assert not await pm.confirm_operation("code_exec", "import os; os.remove")
    # Non-dangerous ops still allowed
    assert await pm.confirm_operation("read_file", "/tmp/test")
    assert await pm.confirm_operation("web_search", "test query")


@pytest.mark.asyncio
async def test_dangerous_ops_set():
    """DANGEROUS_OPS should contain all expected operations."""
    assert "shell_run" in PermissionsManager.DANGEROUS_OPS
    assert "code_exec" in PermissionsManager.DANGEROUS_OPS
    assert "file_write" in PermissionsManager.DANGEROUS_OPS
    assert "file_patch" in PermissionsManager.DANGEROUS_OPS
    assert "email_send" in PermissionsManager.DANGEROUS_OPS
    assert "wechat_send_text" in PermissionsManager.DANGEROUS_OPS
    assert "tool_create" in PermissionsManager.DANGEROUS_OPS
    assert "skill_install" in PermissionsManager.DANGEROUS_OPS
    assert "mcp_install" in PermissionsManager.DANGEROUS_OPS
    assert "read_file" not in PermissionsManager.DANGEROUS_OPS
    assert "web_search" not in PermissionsManager.DANGEROUS_OPS

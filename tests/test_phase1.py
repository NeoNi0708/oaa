"""Unit tests for CloneManager and PreferencesStore."""
import os
import tempfile
import shutil

import pytest

from oaa.agent.clone_manager import CloneManager
from oaa.agent.preferences_store import PreferencesStore


# ======================================================================
# CloneManager tests
# ======================================================================

class TestCloneManager:
    """Tests for CloneManager — create, edit, sync, discard, status."""

    @pytest.fixture
    def setup(self):
        """Create a temp dir with an oaa_root mock and data_dir."""
        tmp = tempfile.mkdtemp()
        oaa_root = os.path.join(tmp, "oaa_root")
        data_dir = os.path.join(tmp, "data")

        # Create minimal mock source tree
        os.makedirs(os.path.join(oaa_root, "oaa", "agent"))
        os.makedirs(os.path.join(oaa_root, "oaa", "gateway"))
        os.makedirs(os.path.join(oaa_root, "tests"))
        os.makedirs(os.path.join(oaa_root, "data"))  # Should be excluded
        os.makedirs(os.path.join(oaa_root, "node_modules"))  # Should be excluded

        # Source files
        tools_py = os.path.join(oaa_root, "oaa", "agent", "tools.py")
        with open(tools_py, "w", encoding="utf-8") as f:
            f.write("VERSION = '1.0'\n\ndef hello():\n    return 'hello'\n")

        mgmt_py = os.path.join(oaa_root, "oaa", "gateway", "management.py")
        with open(mgmt_py, "w", encoding="utf-8") as f:
            f.write("MANAGER = 'default'\n")

        # A file in excluded dir (should not be cloned)
        data_secret = os.path.join(oaa_root, "data", "secret.txt")
        with open(data_secret, "w") as f:
            f.write("secret")

        yield oaa_root, data_dir

        shutil.rmtree(tmp)

    def test_create(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        assert not mgr.exists()

        result = mgr.create()
        assert result["ok"] is True
        assert "oaa" in result["copied_dirs"]
        assert "tests" in result["copied_dirs"]
        assert mgr.exists()

        # Verify excluded dirs not copied
        clone_oaa = os.path.join(data_dir, "clone", "oaa")
        assert os.path.isdir(os.path.join(clone_oaa, "agent"))
        assert os.path.isdir(os.path.join(clone_oaa, "gateway"))
        assert not os.path.isdir(os.path.join(clone_oaa, "data"))
        assert not os.path.isdir(os.path.join(clone_oaa, "node_modules"))

    def test_create_twice_fails(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()
        result = mgr.create()
        assert result["ok"] is False
        assert "已存在" in result["error"]

    def test_discard(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()
        assert mgr.exists()
        result = mgr.discard()
        assert result["ok"] is True
        assert not mgr.exists()

    def test_discard_idempotent(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        result = mgr.discard()
        assert result["ok"] is True

    def test_edit(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()

        result = mgr.apply_edit(
            "oaa/agent/tools.py",
            "VERSION = '1.0'",
            "VERSION = '2.0'",
        )
        assert result["ok"] is True

        # Verify clone file changed
        clone_file = os.path.join(data_dir, "clone", "oaa", "agent", "tools.py")
        with open(clone_file) as f:
            content = f.read()
        assert "VERSION = '2.0'" in content
        assert "VERSION = '1.0'" not in content

        # Verify live file NOT changed
        live_file = os.path.join(oaa_root, "oaa", "agent", "tools.py")
        with open(live_file) as f:
            content = f.read()
        assert "VERSION = '1.0'" in content

    def test_edit_no_clone(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        result = mgr.apply_edit("oaa/agent/tools.py", "a", "b")
        assert result["ok"] is False
        assert "克隆不存在" in result["error"]

    def test_edit_path_traversal(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()
        result = mgr.apply_edit("../../etc/passwd", "root", "admin")
        assert result["ok"] is False
        assert "非法路径" in result["error"]

    def test_edit_content_mismatch(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()
        result = mgr.apply_edit("oaa/agent/tools.py", "NONEXISTENT", "NEW")
        assert result["ok"] is False
        assert "不匹配" in result["error"]

    def test_sync(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()

        # Edit clone
        mgr.apply_edit("oaa/agent/tools.py", "VERSION = '1.0'", "VERSION = '2.0'")
        mgr.apply_edit("oaa/gateway/management.py", "MANAGER = 'default'", "MANAGER = 'custom'")

        # Sync
        result = mgr.sync(proposal_id="test_prop")
        assert result["ok"] is True
        assert len(result["synced"]) == 2
        assert all(s["status"] == "synced" for s in result["synced"])

        # Verify live files changed
        live_tools = os.path.join(oaa_root, "oaa", "agent", "tools.py")
        with open(live_tools) as f:
            assert "VERSION = '2.0'" in f.read()

        live_mgmt = os.path.join(oaa_root, "oaa", "gateway", "management.py")
        with open(live_mgmt) as f:
            assert "MANAGER = 'custom'" in f.read()

    def test_sync_no_clone(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        result = mgr.sync()
        assert result["ok"] is False
        assert "克隆不存在" in result["error"]

    def test_sync_no_changes(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        mgr.create()
        result = mgr.sync()
        assert result["ok"] is True
        assert "warning" in result

    def test_status(self, setup):
        oaa_root, data_dir = setup
        mgr = CloneManager(data_dir, oaa_root)
        assert mgr.status()["exists"] is False

        mgr.create()
        status = mgr.status()
        assert status["exists"] is True
        assert status["modified_count"] == 0

        mgr.apply_edit("oaa/agent/tools.py", "VERSION = '1.0'", "VERSION = '2.0'")
        status = mgr.status()
        assert status["modified_count"] == 1
        assert "oaa/agent/tools.py" in status["modified_files"]


# ======================================================================
# PreferencesStore tests
# ======================================================================

class TestPreferencesStore:
    """Tests for PreferencesStore — CRUD, capacity, injection."""

    @pytest.fixture
    def store(self):
        tmp = tempfile.mkdtemp()
        s = PreferencesStore(tmp)
        yield s
        shutil.rmtree(tmp)

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_set_and_get(self, store):
        store.set("report_style", "brief", "User prefers brief reports")
        pref = store.get("report_style")
        assert pref is not None
        assert pref["key"] == "report_style"
        assert pref["value"] == "brief"
        assert pref["source"] == "agent"

    def test_update(self, store):
        store.set("style", "brief")
        store.set("style", "detailed", "User prefers detailed reports")
        pref = store.get("style")
        assert pref["value"] == "detailed"
        assert pref["description"] == "User prefers detailed reports"

    def test_user_override_immunity(self, store):
        store.set("key1", "agent_value", source="agent")
        store.set("key1", "override", source="user_override")
        # Agent should NOT overwrite user_override
        store.set("key1", "ignored", source="agent")
        pref = store.get("key1")
        assert pref["value"] == "override"

    def test_delete(self, store):
        store.set("key1", "val1")
        assert store.get("key1") is not None
        assert store.delete("key1") is True
        assert store.get("key1") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("nonexistent") is False

    def test_search(self, store):
        store.set("report_style", "brief", "How reports are formatted")
        store.set("notify_channel", "dingtalk", "Preferred notification channel")
        store.set("language", "zh", "UI language")

        results = store.search("report")
        assert len(results) >= 1
        assert results[0]["key"] == "report_style"

        results = store.search("channel")
        assert len(results) >= 1
        assert results[0]["key"] == "notify_channel"

    def test_search_empty(self, store):
        store.set("a", "1")
        store.set("b", "2")
        results = store.search("")
        assert len(results) == 2

    def test_capacity(self, store):
        # Fill to capacity
        for i in range(55):
            store.set(f"key{i}", f"val{i}", source="agent")
        assert len(store.list()) <= 50
        # user_override entries should be preserved
        store.set("important", "keep", source="user_override")
        assert store.get("important") is not None

    def test_injection_text_empty(self, store):
        text = store.get_injection_text()
        assert "暂无" in text

    def test_injection_text(self, store):
        store.set("style", "brief", "Short reports")
        store.set("channel", "dingtalk", "Notify via DingTalk")
        text = store.get_injection_text()
        assert "style = brief" in text
        assert "channel = dingtalk" in text
        assert "用户偏好" in text

    def test_persistence(self, store):
        store.set("persist_test", "value1")
        path = store._path
        # Create new store reading same file
        store2 = PreferencesStore(os.path.dirname(path))
        pref = store2.get("persist_test")
        assert pref is not None
        assert pref["value"] == "value1"

    def test_list(self, store):
        store.set("a", "1")
        store.set("b", "2")
        prefs = store.list()
        assert len(prefs) == 2

    def test_list_enabled_only(self, store):
        store.set("a", "1")
        store.set("b", "2", source="agent")
        # Disable 'b' by setting enabled=False via the store internals
        for p in store._prefs:
            if p["key"] == "b":
                p["enabled"] = False
        store._save()
        prefs = store.list(enabled_only=True)
        assert len(prefs) == 1
        assert prefs[0]["key"] == "a"

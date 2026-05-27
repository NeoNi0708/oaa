"""Management API boundary tests — invalid IDs, duplicate ops, invalid status."""
import tempfile
import pytest

from oaa.agent.proposal import Proposal, ProposalStore, STATUS_PENDING, STATUS_DONE
from oaa.gateway.management import ManagementHandler, VALID_TYPES


# ---------------------------------------------------------------------------
# Mock dependencies
# ---------------------------------------------------------------------------

class MockConfig:
    model = type("obj", (), {"provider": "test", "api_key": "", "model_id": "", "base_url": "",
                             "max_tokens": 4096, "temperature": 0.7, "api_format": "openai"})()
    permissions = {"permission_level": "auto", "blacklist_paths": [], "require_confirm": []}
    models = {"test": [{"name": "test", "api_key": "", "model_id": "test", "base_url": ""}]}
    wechat = type("obj", (), {"enabled": False})()
    dingtalk = type("obj", (), {"enabled": False})()
    feishu = type("obj", (), {"enabled": False})()
    data_dir = ""
    async def save(self): pass
    def to_redacted_dict(self): return {}


class MockScheduler:
    def list_tasks(self, **kw): return []
    def get(self, _id): return None
    def create(self, data): return {"id": "new", **data}
    def update(self, _id, data): return None
    def delete(self, _id): return True


class MockSkillMgr:
    def list_all(self): return []
    def get(self, name): return None
    def get_current(self): return None
    def switch_to(self, name): return None


class MockEvolution:
    stats = {"skill_usage": {}, "crystallized": [], "suggestions": [], "sop_skips": {}}
    store = None
    async def analyze_for_suggestions(self): pass
    async def accept_suggestion(self, idx): pass


class MockAgent:
    def __init__(self, proposal_store=None):
        self._proposal_store = proposal_store
        self._idle_inspector = MockInspector()
        self.llm = None

    def build_handler(self):
        return MockHandler()


class MockInspector:
    def ignore_tool(self, target, permanent=False):
        pass
    def pause(self):
        pass
    def resume(self):
        pass


class MockHandler:
    async def dispatch(self, tool, args):
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mgr():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProposalStore(tmp)
        agent = MockAgent(proposal_store=store)
        m = ManagementHandler(
            config=MockConfig(),
            scheduler=MockScheduler(),
            skill_mgr=MockSkillMgr(),
            evolution=MockEvolution(),
            channel_adapters={},
            agent=agent,
        )
        yield m, store


# ===================================================================
# Unknown type
# ===================================================================

class TestUnknownType:
    @pytest.mark.asyncio
    async def test_unknown_msg_type(self, mgr):
        m, _ = mgr
        result = await m.handle("nonexistent_type", {})
        assert result.get("ok") is False
        assert "Unknown management type" in result.get("error", "")

    def test_unknown_type_sync(self, mgr):
        m, _ = mgr
        result = m.handle("nonexistent_type", {})
        # Sync methods are called directly in test, not through handle()
        # The handle() wraps sync results, so we test through handle()
        pass


# ===================================================================
# list_proposals boundary tests
# ===================================================================

class TestListProposals:
    @pytest.mark.asyncio
    async def test_empty_list(self, mgr):
        m, _ = mgr
        result = await m.handle("list_proposals", {})
        assert result.get("ok") is True
        assert result.get("count") == 0
        assert result.get("proposals") == []

    @pytest.mark.asyncio
    async def test_list_with_invalid_status_filter(self, mgr):
        """Filtering by a status that no proposal has should return empty."""
        m, _ = mgr
        result = await m.handle("list_proposals", {"status": "nonexistent"})
        assert result.get("ok") is True
        assert result.get("count") == 0


# ===================================================================
# proposal_approve boundary tests
# ===================================================================

class TestProposalApprove:
    @pytest.mark.asyncio
    async def test_missing_id(self, mgr):
        m, _ = mgr
        result = await m.handle("proposal_approve", {})
        assert result.get("ok") is False
        assert "No proposal ID" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_empty_id_string(self, mgr):
        m, _ = mgr
        result = await m.handle("proposal_approve", {"id": ""})
        assert result.get("ok") is False
        assert "No proposal ID" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_nonexistent_id(self, mgr):
        m, _ = mgr
        result = await m.handle("proposal_approve", {"id": "prop_0000000_nonexistent"})
        assert result.get("ok") is False
        assert "not found" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_duplicate_approve(self, mgr):
        m, store = mgr
        prop = Proposal(type="tool_fix", title="dup-test", target="dup_tool",
                        actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}])
        pid = await store.add(prop)

        # First approve should succeed
        r1 = await m.handle("proposal_approve", {"id": pid})
        assert r1.get("ok") is True, f"First approve failed: {r1}"

        # Second approve should fail (status is no longer pending)
        r2 = await m.handle("proposal_approve", {"id": pid})
        assert r2.get("ok") is False
        assert "not pending" in r2.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_approve_already_done(self, mgr):
        m, store = mgr
        prop = Proposal(type="tool_fix", title="already-done", target="x",
                        actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}])
        pid = await store.add(prop)
        await store.update_status(pid, STATUS_DONE)

        result = await m.handle("proposal_approve", {"id": pid})
        assert result.get("ok") is False
        assert "not pending" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_approve_ignored_once(self, mgr):
        m, store = mgr
        prop = Proposal(type="tool_fix", title="ignored-once", target="x",
                        actions=[{"tool": "shell_run", "args": {"command": "echo ok"}}])
        pid = await store.add(prop)
        await store.update_status(pid, "ignored_once")

        result = await m.handle("proposal_approve", {"id": pid})
        assert result.get("ok") is False


# ===================================================================
# proposal_ignore boundary tests
# ===================================================================

class TestProposalIgnore:
    @pytest.mark.asyncio
    async def test_missing_id(self, mgr):
        m, _ = mgr
        result = await m.handle("proposal_ignore", {})
        assert result.get("ok") is False
        assert "No proposal ID" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_nonexistent_id(self, mgr):
        m, _ = mgr
        result = await m.handle("proposal_ignore", {"id": "nonexistent"})
        assert result.get("ok") is False
        assert "not found" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_ignore_with_permanent_flag(self, mgr):
        m, store = mgr
        prop = Proposal(type="tool_fix", title="perm-ignore", target="perm_tool")
        pid = await store.add(prop)

        result = await m.handle("proposal_ignore", {"id": pid, "permanent": True})
        assert result.get("ok") is True
        assert result.get("status") == "ignored_forever"

    @pytest.mark.asyncio
    async def test_ignore_without_permanent(self, mgr):
        m, store = mgr
        prop = Proposal(type="tool_fix", title="temp-ignore", target="temp_tool")
        pid = await store.add(prop)

        result = await m.handle("proposal_ignore", {"id": pid, "permanent": False})
        assert result.get("ok") is True
        assert result.get("status") == "ignored_once"

    @pytest.mark.asyncio
    async def test_ignore_twice(self, mgr):
        """Re-ignoring an already-ignored proposal should still work (idempotent)."""
        m, store = mgr
        prop = Proposal(type="tool_fix", title="double-ignore", target="double_tool")
        pid = await store.add(prop)

        r1 = await m.handle("proposal_ignore", {"id": pid, "permanent": False})
        assert r1.get("ok") is True

        r2 = await m.handle("proposal_ignore", {"id": pid, "permanent": True})
        assert r2.get("ok") is True


# ===================================================================
# get_evolution_stats boundary tests
# ===================================================================

class TestEvolutionStats:
    @pytest.mark.asyncio
    async def test_empty_stats(self, mgr):
        m, _ = mgr
        result = await m.handle("get_evolution_stats", {})
        assert result.get("ok") is True
        summary = result.get("proposal_summary", {})
        assert summary["total"] == 0
        assert summary["pending"] == 0
        assert summary["success_rate"] == 0


# ===================================================================
# get_status sanity
# ===================================================================

class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_returns_ok(self, mgr):
        m, _ = mgr
        result = await m.handle("get_status", {})
        assert result.get("ok") is True
        assert "agent_state" in result
        assert "uptime_sec" in result

"""Integration tests — testing components together."""
import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_gateway_agent_integration():
    """Test Gateway + Agent + Session integration end-to-end."""
    # Late imports to avoid circular dependency in the project's module graph
    from oaa.config import AppConfig
    from oaa.agent.oaa_agent import OAAAgent
    from oaa.session.manager import SessionManager
    from oaa.gateway.gateway import Gateway, Message

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        # Setup
        config = AppConfig(data_dir=tmp)
        agent = OAAAgent(config)
        session_mgr = SessionManager(os.path.join(tmp, "db", "test.db"))
        gateway = Gateway(agent, session_mgr)

        # Process a message
        msg = Message("desktop", "test_user", "你好，二愣")
        results = []
        async for chunk in gateway.incoming_message(msg):
            results.append(chunk)

        # Verify we got results
        assert len(results) > 0
        print(f"Gateway produced {len(results)} chunks")
        for c in results:
            print(f"  type={c.get('type')}, content={str(c.get('content', ''))[:60]}")


def test_skill_manager_discovery():
    """Test skill manager finds installed skills."""
    from oaa.agent.skill_manager import SkillManager

    with tempfile.TemporaryDirectory() as tmp:
        # Create a fake skill
        skill_dir = os.path.join(tmp, "skills", "外贸业务核心", "test-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# Test Skill")

        mgr = SkillManager(os.path.join(tmp, "skills"))
        mgr.discover()
        assert mgr.get("test-skill") is not None
        print("Skill discovery OK")


if __name__ == "__main__":
    test_skill_manager_discovery()
    print("All integration tests passed!")

"""D13: ExtendedTools, Permissions integration, and AgentLoop tests."""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest


# ------------------------------------------------------------------
# ExtendedTools — word / excel
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extended_word_doc_missing_dep():
    """word_doc should return error if python-docx is not installed."""
    from oaa.agent.extended_tools import ExtendedTools

    with tempfile.TemporaryDirectory() as tmp:
        et = ExtendedTools(tmp)
        result = await et.do_word_doc({"title": "Test", "content": "Hello"})
        if result["status"] == "error":
            assert "python-docx" in result.get("msg", "")
        else:
            # If python-docx happens to be installed, it should succeed
            assert result["status"] == "success"


@pytest.mark.asyncio
async def test_extended_excel_xlsx_missing_dep():
    """excel_xlsx should return error if openpyxl is not installed."""
    from oaa.agent.extended_tools import ExtendedTools

    with tempfile.TemporaryDirectory() as tmp:
        et = ExtendedTools(tmp)
        result = await et.do_excel_xlsx({"rows": [["a", "b"], [1, 2]]})
        if result["status"] == "error":
            assert "openpyxl" in result.get("msg", "")
        else:
            assert result["status"] == "success"


# ------------------------------------------------------------------
# ExtendedTools — plan operations
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extended_plan_create_and_list():
    """Plan CRUD via ExtendedTools should work end-to-end."""
    from oaa.agent.extended_tools import ExtendedTools

    with tempfile.TemporaryDirectory() as tmp:
        et = ExtendedTools(tmp)

        # Create plan
        result = await et.do_plan_create({
            "goal": "Test plan",
            "steps": [
                {"id": 1, "task": "Step A", "status": "pending"},
                {"id": 2, "task": "Step B", "status": "pending"},
            ],
        })
        assert result["status"] == "success"
        plan = result["plan"]
        assert plan["goal"] == "Test plan"

        # List plans
        result = await et.do_plan_list({})
        assert result["status"] == "success"
        assert result["count"] >= 1


@pytest.mark.asyncio
async def test_extended_plan_update():
    """Plan update via ExtendedTools should work."""
    from oaa.agent.extended_tools import ExtendedTools

    with tempfile.TemporaryDirectory() as tmp:
        et = ExtendedTools(tmp)
        result = await et.do_plan_create({
            "goal": "Update test",
            "steps": [{"id": 1, "task": "Do it", "status": "pending"}],
        })
        plan_id = result["plan"]["id"]

        result = await et.do_plan_update({"plan_id": plan_id, "step_id": 1, "status": "done", "result": "OK"})
        assert result["status"] == "success"


# ------------------------------------------------------------------
# Permissions integration
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extended_tools_permission_check():
    """ExtendedTools with permissions should block blacklisted paths."""
    from oaa.agent.extended_tools import ExtendedTools
    from oaa.auth.permissions import PermissionsManager
    from oaa.config import AppConfig

    with tempfile.TemporaryDirectory() as tmp:
        config = AppConfig(data_dir=tmp, permissions={"blacklist_paths": [tmp], "require_confirm": []})
        pm = PermissionsManager(config)
        et = ExtendedTools(tmp, permissions=pm)

        result = await et.do_word_doc({"path": os.path.join(tmp, "test.docx"), "title": "X", "content": "X"})
        assert result["status"] == "error"
        assert "denied" in result.get("msg", "").lower() or "permission" in result.get("msg", "").lower()


@pytest.mark.asyncio
async def test_extended_tools_email_permission():
    """email_send should respect permission confirmations."""
    from oaa.agent.extended_tools import ExtendedTools
    from oaa.auth.permissions import PermissionsManager
    from oaa.config import AppConfig

    with tempfile.TemporaryDirectory() as tmp:
        config = AppConfig(data_dir=tmp, permissions={"permission_level": "confirm", "blacklist_paths": [], "require_confirm": ["email_send"]})
        pm = PermissionsManager(config)
        et = ExtendedTools(tmp, permissions=pm)

        result = await et.do_email_send({"to": "test@test.com", "subject": "Test"})
        # Without a confirm callback, confirm_operation returns False
        assert result["status"] == "error"
        assert "not permitted" in result.get("msg", "")


# ------------------------------------------------------------------
# AgentLoop basic tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_loop_no_llm():
    """AgentLoop should fail gracefully when LLM is None."""
    from oaa.agent.loop import AgentLoop

    handler = MagicMock()
    loop = AgentLoop(llm=None, handler=handler, tools_schema=[])
    results = []
    async for chunk in loop.run("hello"):
        results.append(chunk)
    assert any(c["type"] == "done" for c in results)
    done = [c for c in results if c["type"] == "done"][0]
    assert "No LLM" in done["content"]


@pytest.mark.asyncio
async def test_agent_loop_history():
    """AgentLoop should prepend history messages before user input."""
    from oaa.agent.loop import AgentLoop

    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="response", tool_calls=[]))
    handler = MagicMock()
    handler.dispatch = AsyncMock(return_value={})

    loop = AgentLoop(llm=llm, handler=handler, tools_schema=[])
    history = [{"role": "assistant", "content": "previous reply"}]
    results = []
    async for chunk in loop.run("new question", history=history):
        results.append(chunk)

    # Verify history was passed to LLM
    call_args = llm.chat.call_args[0][0]
    assert any("previous reply" in m["content"] for m in call_args)
    assert any("new question" in m["content"] for m in call_args)


@pytest.mark.asyncio
async def test_agent_loop_max_turns():
    """AgentLoop should stop when max_turns is exceeded."""
    from oaa.agent.loop import AgentLoop

    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content="",
        tool_calls=[MagicMock(id="call_1", function=MagicMock(name="file_read", arguments='{"path":"x"}'))],
    ))
    handler = MagicMock()
    handler.dispatch = AsyncMock(return_value={"status": "success"})

    loop = AgentLoop(llm=llm, handler=handler, tools_schema=[], max_turns=2)
    results = []
    async for chunk in loop.run("loop test"):
        results.append(chunk)

    assert any(c["type"] == "done" for c in results)
    done = [c for c in results if c["type"] == "done"][-1]
    assert "Max turns" in done["content"]

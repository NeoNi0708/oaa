"""D12: Channel adapter and Gateway tests."""
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_gateway_message_routing():
    """Gateway should route messages through registered adapters and collect results."""
    from oaa.gateway.gateway import Gateway, Message
    from oaa.agent.oaa_agent import OAAAgent
    from oaa.session.manager import SessionManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        agent = MagicMock(spec=OAAAgent)
        # Make process_message an async generator that yields chunks
        async def _mock_process(*args, **kwargs):
            yield {"type": "llm_output", "content": "thinking"}
            yield {"type": "done", "content": "finished"}
        agent.process_message = _mock_process

        session_mgr = SessionManager(os.path.join(tmp, "db", "test.db"))
        gateway = Gateway(agent, session_mgr)

        adapter = MagicMock()
        adapter.send_message = AsyncMock()
        gateway.register_adapter("test_channel", adapter)

        msg = Message("test_channel", "user1", "hello")
        results = []
        async for chunk in gateway.incoming_message(msg):
            results.append(chunk)

        assert len(results) > 0
        assert msg.session_id != ""
        session_mgr.close()


@pytest.mark.asyncio
async def test_gateway_send_to_channel():
    """Gateway.send_to_channel should route through the right adapter."""
    from oaa.gateway.gateway import Gateway
    from oaa.agent.oaa_agent import OAAAgent
    from oaa.session.manager import SessionManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        agent = MagicMock(spec=OAAAgent)
        session_mgr = SessionManager(os.path.join(tmp, "db", "test.db"))
        gateway = Gateway(agent, session_mgr)

        adapter = MagicMock()
        adapter.send_message = AsyncMock()
        gateway.register_adapter("test_channel", adapter)

        await gateway.send_to_channel("test_channel", "user1", "response", "sess_1")
        adapter.send_message.assert_called_once_with("user1", "response", "sess_1")
        session_mgr.close()


@pytest.mark.asyncio
async def test_gateway_unknown_channel():
    """Sending to an unregistered channel should not crash."""
    from oaa.gateway.gateway import Gateway
    from oaa.agent.oaa_agent import OAAAgent
    from oaa.session.manager import SessionManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        agent = MagicMock(spec=OAAAgent)
        session_mgr = SessionManager(os.path.join(tmp, "db", "test.db"))
        gateway = Gateway(agent, session_mgr)

        # Should not raise
        await gateway.send_to_channel("nonexistent", "u", "msg", "sess_1")
        session_mgr.close()


@pytest.mark.asyncio
async def test_desktop_adapter_send_chunk():
    """DesktopAdapter._send_chunk should send properly formatted JSON."""
    from oaa.gateway.adapters.desktop import DesktopAdapter

    adapter = DesktopAdapter()
    websocket = AsyncMock()

    # done
    await adapter._send_chunk(websocket, {"type": "done", "content": "finished"})
    sent = json.loads(websocket.send.call_args[0][0])
    assert sent["type"] == "done"
    assert sent["payload"]["content"] == "finished"

    # llm_output
    await adapter._send_chunk(websocket, {"type": "llm_output", "content": "thinking..."})
    sent = json.loads(websocket.send.call_args[0][0])
    assert sent["type"] == "llm_output"

    # tool_call
    await adapter._send_chunk(websocket, {"type": "tool_call", "name": "file_read", "args": {"path": "x"}})
    sent = json.loads(websocket.send.call_args[0][0])
    assert sent["type"] == "tool_call"
    assert sent["payload"]["name"] == "file_read"

    # tool_result
    await adapter._send_chunk(websocket, {"type": "tool_result", "name": "file_read", "result": "ok"})
    sent = json.loads(websocket.send.call_args[0][0])
    assert sent["type"] == "tool_result"

    # status
    await adapter._send_chunk(websocket, {"type": "status", "content": "working..."})
    sent = json.loads(websocket.send.call_args[0][0])
    assert sent["type"] == "status"

    # Unknown type should not send anything
    websocket.send.reset_mock()
    await adapter._send_chunk(websocket, {"type": "unknown"})
    websocket.send.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_adapter_send_message():
    """DesktopAdapter.send_message should broadcast to connected clients."""
    from oaa.gateway.adapters.desktop import DesktopAdapter

    adapter = DesktopAdapter()
    ws = AsyncMock()
    adapter._clients.add(ws)

    await adapter.send_message("user1", "hello", "sess_1")
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "message"
    assert sent["payload"]["content"] == "hello"

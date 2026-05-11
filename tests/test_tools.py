"""Tests for AtomicTools — file ops, code execution, working memory."""
import os
import sys
import tempfile
import pytest
from oaa.agent.tools import AtomicTools


@pytest.mark.asyncio
async def test_file_write_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)

        result = await t.do_file_write({"path": "test.txt", "content": "hello world"})
        assert result["status"] == "success"

        result = await t.do_file_read({"path": "test.txt"})
        assert result["status"] == "success"
        assert "hello world" in result["content"]


@pytest.mark.asyncio
async def test_file_read_nonexistent():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_file_read({"path": "nonexistent.txt"})
        assert result["status"] == "error"
        assert "not found" in result["msg"].lower()


@pytest.mark.asyncio
async def test_file_append():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        await t.do_file_write({"path": "log.txt", "content": "line1\n"})
        await t.do_file_write({"path": "log.txt", "content": "line2\n", "mode": "append"})
        result = await t.do_file_read({"path": "log.txt"})
        assert "line1" in result["content"]
        assert "line2" in result["content"]


@pytest.mark.asyncio
async def test_file_patch():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        await t.do_file_write({"path": "data.txt", "content": "before X after"})
        result = await t.do_file_patch({"path": "data.txt", "old_content": "X", "new_content": "Y"})
        assert result["status"] == "success"

        content = await t.do_file_read({"path": "data.txt"})
        assert "Y" in content["content"]


@pytest.mark.asyncio
async def test_file_patch_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        await t.do_file_write({"path": "dup.txt", "content": "X and X"})
        result = await t.do_file_patch({"path": "dup.txt", "old_content": "X", "new_content": "Y"})
        assert result["status"] == "error"
        assert "2 matches" in result["msg"]


@pytest.mark.asyncio
async def test_ask_user():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_ask_user({"question": "Confirm?"})
        assert result["status"] == "INTERRUPT"


@pytest.mark.asyncio
async def test_working_memory():
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        await t.do_update_working_checkpoint({"key_info": "important data"})
        assert t.working_memory["key_info"] == "important data"


# ------------------------------------------------------------------
# D11: code_run comprehensive tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_code_run_python():
    """Execute a simple Python script and capture output."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": 'print("hello from sandbox")', "type": "python"})
        assert result["status"] == "success"
        assert "hello from sandbox" in result.get("stdout", "")


@pytest.mark.asyncio
async def test_code_run_python_error():
    """Execute Python that raises an exception and verify non-zero exit."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": 'raise RuntimeError("boom")', "type": "python"})
        assert result["status"] == "error"
        assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_code_run_sandbox_blocks_dangerous_modules():
    """Sandbox should block importing os, subprocess, etc."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": 'import os; print(os.name)', "type": "python"})
        assert result["status"] == "error"
        # Should error out — sandbox blocks os
        assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_code_run_sandbox_allows_safe_modules():
    """Sandbox should allow standard library modules like json, re, math."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": 'import json; print(json.dumps({"ok": 1}))', "type": "python"})
        assert result["status"] == "success"
        assert '"ok"' in result.get("stdout", "")


@pytest.mark.asyncio
async def test_code_run_timeout():
    """Code that exceeds timeout should be killed."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({
            "code": "import time; time.sleep(10); print('done')",
            "type": "python",
            "timeout": 1,
        })
        assert result["status"] == "error"
        assert "Timeout" in result.get("msg", "")


@pytest.mark.asyncio
async def test_code_run_unknown_type():
    """Unsupported code type should return an error."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": "print('hi')", "type": "ruby"})
        assert result["status"] == "error"
        assert "Unsupported" in result.get("msg", "")


@pytest.mark.skipif(sys.platform == "win32", reason="PowerShell test requires Windows")
@pytest.mark.asyncio
async def test_code_run_powershell():
    """Execute a simple PowerShell command."""
    with tempfile.TemporaryDirectory() as tmp:
        t = AtomicTools(tmp)
        result = await t.do_code_run({"code": "echo hello-ps", "type": "powershell"})
        assert result["status"] == "success"
        assert "hello-ps" in result.get("stdout", "")

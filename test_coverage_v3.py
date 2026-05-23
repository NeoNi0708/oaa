#!/usr/bin/env python3
"""OAA 全覆盖功能测试 v3 — 25 个场景，直接调用 agent.process_message()"""
import asyncio
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oaa.config import AppConfig
from oaa.agent.oaa_agent import OAAAgent


# ── Verification helpers ──────────────────────────────────────────

def verify_replied(chunks, tools, text):
    """Agent produced a non-empty response."""
    return bool(text and len(text.strip()) > 5)

def verify_tool_called(tool_name):
    def _v(chunks, tools, text):
        return tool_name in tools
    return _v

def verify_any_of(*tool_names):
    def _v(chunks, tools, text):
        return any(t in tools for t in tool_names)
    return _v

def verify_text_contains(*keywords):
    def _v(chunks, tools, text):
        return any(kw.lower() in text.lower() for kw in keywords)
    return _v

def verify_no_error(chunks, tools, text):
    return "出错" not in text and "error" not in text.lower()


class TestRunner:
    def __init__(self, agent):
        self.agent = agent
        self.results = []

    async def run_case(self, name, prompt, verify_fn, timeout=300):
        start = time.time()
        chunks = []
        tools = []
        result_text = ""
        error = None

        async def _consume():
            nonlocal result_text
            async for chunk in self.agent.process_message(prompt):
                chunks.append(chunk)
                t = chunk.get("type", "")
                if t == "tool_call":
                    tools.append(chunk.get("name", "?"))
                elif t == "done":
                    result_text = chunk.get("content", "")

        try:
            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError:
            error = f"Timeout after {timeout}s"
        except Exception as exc:
            error = str(exc)
            traceback.print_exc()

        passed = not error and verify_fn(chunks, tools, result_text)
        duration = time.time() - start
        self.results.append({
            "name": name, "passed": passed,
            "tools": tools, "duration": duration, "error": error,
            "response_preview": result_text[:200],
        })
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name} ({duration:.1f}s) tools={tools[:6]}")
        if error:
            print(f"         ERROR: {error[:120]}")
        if not passed and not error:
            print(f"         RESP: {result_text[:150]}")

    def report(self):
        passed = sum(1 for r in self.results if r["passed"])
        total = len(self.results)
        print(f"\n{'='*60}")
        print(f"RESULTS: {passed}/{total} passed\n")
        for r in self.results:
            s = "PASS" if r["passed"] else "FAIL"
            tools_str = ", ".join(r["tools"][:6])
            print(f"  [{s}] {r['name']} ({r['duration']:.1f}s) [{tools_str}]")
            if r["error"]:
                print(f"       Error: {r['error'][:120]}")
        print(f"\n{passed}/{total} passed, {total-passed} failed")


# ── Main ──────────────────────────────────────────────────────────

async def main():
    config = AppConfig.load("C:/Users/Administrator/OAA/config.json")
    print(f"Model: {config.model.provider} / {config.model.model_id}")
    agent = OAAAgent(config)
    runner = TestRunner(agent)

    print("\n=== A. Basic Capabilities ===\n")

    await runner.run_case(
        "A1-basic-chat",
        "你好，用一句简短的话介绍你自己。",
        verify_replied,
    )

    await runner.run_case(
        "A2-file-write-read",
        "创建一个文件 test_hello.txt，写 'Hello from OAA!'，然后读取它确认内容正确。",
        lambda c, t, txt: "file_write" in t and ("file_read" in t or "Hello" in txt),
    )

    await runner.run_case(
        "A3-code-exec",
        "用 Python 代码计算 1 到 100 的累加和，告诉我结果。",
        lambda c, t, txt: "code_exec" in t and "5050" in txt,
    )

    await runner.run_case(
        "A4-shell-run",
        "用 shell 命令 echo 'OAA test OK'。",
        lambda c, t, txt: "shell_run" in t,
    )

    print("\n=== B. File System ===\n")

    await runner.run_case(
        "B5-list-structure",
        "列出 oaa/agent/ 目录下的文件结构。",
        verify_any_of("list_own_structure", "file_glob"),
    )

    await runner.run_case(
        "B6-file-glob",
        "用 glob 搜索 oaa/agent/ 下所有 .py 文件。",
        lambda c, t, txt: "file_glob" in t,
    )

    await runner.run_case(
        "B7-download-file",
        "下载 https://httpbin.org/json 保存到 workspace 目录下。",
        verify_tool_called("download_file"),
    )

    print("\n=== C. Code & Shell ===\n")

    await runner.run_case(
        "C8-code-exec-sandbox",
        "在沙盒模式下用 Python 生成前 10 个斐波那契数。",
        lambda c, t, txt: "code_exec" in t,
    )

    await runner.run_case(
        "C9-aifix",
        "写一个有小错误的 Python 代码（比如 print(hello) 没加引号），然后用 aifix 修复它，验证修复后能正常运行。",
        lambda c, t, txt: "aifix" in t or "code_exec" in t,
    )

    await runner.run_case(
        "C10-shell-process",
        "用 shell_run 列出当前目录下的文件。",
        verify_tool_called("shell_run"),
    )

    print("\n=== D. Introspection & Search ===\n")

    await runner.run_case(
        "D11-module-index",
        "用 module_index 列出所有可用的工具。",
        verify_tool_called("module_index"),
    )

    await runner.run_case(
        "D12-code-search",
        "搜索代码中所有包含 'process_message' 的文件。",
        verify_tool_called("code_search"),
    )

    await runner.run_case(
        "D13-read-source",
        "读取 oaa/agent/loop.py 的源代码。",
        verify_tool_called("read_own_source"),
    )

    print("\n=== E. Memory & Reflection ===\n")

    await runner.run_case(
        "E14-save-memory",
        "请记住：用户最喜欢蓝色，用户叫老张。保存到记忆中并确认。",
        verify_tool_called("update_working_checkpoint"),
    )

    await runner.run_case(
        "E15-recall-memory",
        "回忆一下，你之前记住了关于这个用户的什么信息？",
        lambda c, t, txt: "memory_recall" in t or "蓝" in txt or "老张" in txt,
    )

    print("\n=== F. Scheduling ===\n")

    await runner.run_case(
        "F16-schedule-create",
        "创建一个每天 21:00 执行的定时任务，名称'晚间签到'，执行内容是：在聊天中说'主人晚安'。",
        verify_tool_called("schedule_create"),
    )

    await runner.run_case(
        "F17-schedule-list",
        "列出所有已创建的定时任务。",
        verify_tool_called("schedule_list"),
    )

    print("\n=== G. Skills ===\n")

    await runner.run_case(
        "G18-skill-search",
        "搜索技能市场，找一个适合做报价单的技能。",
        lambda c, t, txt: verify_tool_called("skill_search")(c, t, txt) or "报价单" in txt,
    )

    await runner.run_case(
        "G19-skill-list",
        "列出当前系统中已安装的所有技能。",
        verify_replied,
    )

    print("\n=== H. Git ===\n")

    await runner.run_case(
        "H20-git-status",
        "查看当前 git 工作树状态。",
        verify_tool_called("git_status"),
    )

    await runner.run_case(
        "H21-git-log",
        "查看最近 3 条 git 提交记录。",
        verify_tool_called("git_log"),
    )

    print("\n=== I. Health ===\n")

    await runner.run_case(
        "I22-health-check",
        "做一次全面的系统健康诊断。",
        verify_tool_called("health_diagnose"),
    )

    print("\n=== J. Channels ===\n")

    await runner.run_case(
        "J23-channel-status",
        "检查当前各通道（微信、钉钉、飞书）的连接状态。",
        verify_any_of("wechat_sessions", "feishu_search_user", "dingtalk_user_info"),
    )

    print("\n=== K. Multi-turn Context ===\n")

    # K24 uses history to chain context
    await runner.run_case(
        "K24-multi-turn-1",
        "请记住我叫老王。",
        lambda c, t, txt: "update_working_checkpoint" in t or verify_replied(c, t, txt),
    )
    await runner.run_case(
        "K24-multi-turn-2",
        "我叫什么名字？",
        lambda c, t, txt: "王" in txt,
    )

    print("\n=== L. Self-Improvement ===\n")

    await runner.run_case(
        "L25-proposal-list",
        "检查是否有待处理的改进提案，列出它们。",
        verify_replied,
    )

    runner.report()
    return runner


if __name__ == "__main__":
    asyncio.run(main())

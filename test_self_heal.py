#!/usr/bin/env python3
"""Self-healing system focused test — verifies search→install→verify workflow."""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oaa.config import AppConfig
from oaa.agent.oaa_agent import OAAAgent

async def main():
    config = AppConfig.load("C:/Users/Administrator/OAA/config.json")
    agent = OAAAgent(config)

    # ── Check existing proposals and tool failures ──
    store = agent._proposal_store
    if store:
        all_props = store.all_proposals()
        print(f"Proposals: {len(all_props)} total")
        for p in all_props:
            print(f"  [{p.get('status','?')}] {p.get('title','?')} — {p.get('type','?')}")
        pending = store.list_pending()
        print(f"Pending: {len(pending)}")

    # ── Check tool failure records ──
    if agent.memory:
        try:
            from oaa.agent.memory_manager import MemoryManager
            failures_file = os.path.join(config.data_dir, "memory", "tool_failures.json")
            if os.path.exists(failures_file):
                with open(failures_file, encoding="utf-8") as f:
                    tf = json.load(f)
                print(f"\nTool failures recorded: {len(tf)} tools")
                for tool, entries in list(tf.items())[:5]:
                    print(f"  {tool}: {len(entries)} failures, last: {entries[-1].get('error','?')[:100] if entries else 'none'}")
        except Exception as e:
            print(f"  Could not read failures: {e}")

    # ── Test: send a message that forces the agent to find/install a missing tool ──
    # Use a deliberately wrong tool name to test search behavior
    print(f"\n{'='*60}")
    print("TEST: Agent must search before installing a missing capability")
    print(f"{'='*60}")

    prompt = (
        "我需要一个叫 'csv-to-json' 的命令行工具来做数据转换。请帮我安装它，"
        "安装后用 --help 试运行验证是否真的可用。如果网上有多个同名工具，"
        "请对比选择最合适的。如果找不到，请如实告诉我。"
    )

    print(f"\nSending: {prompt[:80]}...")
    tools_called = []
    searched = False
    verified = False
    installed = False

    async for chunk in agent.process_message(prompt):
        t = chunk.get("type", "")
        name = chunk.get("name", "")
        if t == "tool_call":
            tools_called.append(name)
            if name in ("ai_search", "code_search", "skill_search"):
                searched = True
            if name in ("shell_run", "code_exec"):
                verified = True
            if name in ("shell_run",):
                installed = True
            print(f"  [{name}]")
        elif t == "done":
            content = chunk.get("content", "")
            safe = content[:300].encode("ascii", errors="replace").decode("ascii")
            print(f"\nResponse: {safe}")

    print(f"\nResults:")
    print(f"  Tools called: {tools_called}")
    print(f"  Searched first: {'PASS' if searched else 'FAIL - did not search before acting'}")
    print(f"  Trial run: {'PASS' if verified else 'FAIL - did not verify installation'}")

if __name__ == "__main__":
    asyncio.run(main())

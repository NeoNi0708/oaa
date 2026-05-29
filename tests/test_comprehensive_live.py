"""OAA comprehensive test client — sends tasks via WebSocket and monitors responses."""
import asyncio
import json
import sys
import time

TASKS = [
    # Test 1: Creative problem solving — image creation
    {
        "name": "01-image",
        "message": "帮我把 OAA 的系统架构画成一张图，保存到桌面，图片名叫 oaa_architecture.png",
    },
    # Test 2: Creative problem solving — PPT
    {
        "name": "02-ppt",
        "message": "帮我做一个项目介绍 PPT，介绍 OAA 的核心功能，放在桌面上",
    },
    # Test 4: Research + output
    {
        "name": "04-research",
        "message": "最近 AI 代理框架有什么新进展？调研一下，把结果整理成一个 markdown 文件保存到桌面",
    },
    # Test 5: Cross-tool chain
    {
        "name": "05-toolchain",
        "message": "把当前项目的目录结构列出来，找到所有以 test_ 开头的 Python 文件，统计每个文件的测试函数数量，把结果做成一个表格保存到桌面",
    },
]


async def run_test(ws, task: dict):
    """Send a task to the agent and monitor the response."""
    print(f"\n{'=' * 60}")
    print(f"[TEST] {task['name']}")
    print(f"[PROMPT] {task['message']}")
    print(f"{'=' * 60}")

    payload = json.dumps({"type": "chat", "text": task["message"]})
    await ws.send(payload)

    # Collect response for up to 5 minutes
    timeout = 300
    collected = []
    start = time.time()

    while time.time() - start < timeout:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=5)
            if isinstance(resp, bytes):
                resp = resp.decode("utf-8", errors="replace")
            data = json.loads(resp)
            msg_type = data.get("type", "")

            if msg_type == "chat":
                content = data.get("content", "")
                if content:
                    print(f"[AGENT] {content[:200]}")
                    collected.append(content)

            elif msg_type == "tool_use":
                tool = data.get("tool", "")
                args = data.get("args", {})
                print(f"[TOOL] {tool}({json.dumps(args, ensure_ascii=False)[:200]})")

            elif msg_type == "tool_result":
                result = data.get("result", "")
                print(f"[RESULT] {str(result)[:200]}")

            elif msg_type == "done":
                print(f"[DONE] Agent finished")
                break

            elif msg_type == "error":
                print(f"[ERROR] {data.get('error', '')}")
                break

        except asyncio.TimeoutError:
            # Check if agent is still thinking (periodic status)
            print(f"[WAIT] ... still waiting ({int(time.time() - start)}s)")
            continue
        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(f"[EXCEPTION] {e}")
            break

    elapsed = time.time() - start
    print(f"[END] {task['name']} completed in {elapsed:.1f}s")
    return {"name": task["name"], "collected": collected, "elapsed": elapsed}


async def main():
    import aiohttp

    # Connect to OAA WebSocket
    uri = "ws://127.0.0.1:9765"
    print(f"Connecting to {uri}...")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(uri) as ws:
            print("Connected!\n")
            results = []

            for task in TASKS:
                result = await run_test(ws, task)
                results.append(result)
                # Pause between tests to let agent context settle
                print("\n--- waiting 10s before next test ---\n")
                await asyncio.sleep(10)

            # Summary
            print("\n\n" + "=" * 60)
            print("TEST SUMMARY")
            print("=" * 60)
            for r in results:
                status = "✅" if r["collected"] else "❌"
                print(f"  {status} {r['name']}: {r['elapsed']:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())

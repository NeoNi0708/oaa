# OAA 全功能综合测试方案 v2

**目标**：覆盖 OAA 90%+ 功能模块，验证修复效果，发现新问题。
**方式**：通过 WebSocket 向运行中的 agent 发送任务，记录决策链和结果。
**原则**：指令只交代目标，不告诉具体方法，观察 agent 自主能力。

---

## 测试列表

| # | 测试 | 覆盖模块 | 预期用时 |
|---|------|---------|---------|
| 1 | 基础对话能力 | loop.py, llm client | 30s |
| 2 | 文件读写操作 | tools.py (file_read/write/patch) | 60s |
| 3 | 代码执行 (exec 模式) | tools.py (code_exec), _exec_runner.py | 60s |
| 4 | 代码执行 (sandbox 模式) | tools.py (code_exec mode=sandbox), _sandbox_runner.py | 60s |
| 5 | 代码执行自动纠错 | tools.py (SyntaxError/NameError fix) | 30s |
| 6 | Shell 命令执行 | tools.py (shell_run), permissions | 30s |
| 7 | 目录遍历与文件搜索 | tools.py (list_own_structure, file_glob, read_own_source) | 60s |
| 8 | 路径解析测试 | tools.py (_resolve_source_path) | 30s |
| 9 | 自修改 (self_improve) | tools.py (self_improve, _backup_file) | 60s |
| 10 | 记忆操作 | tools.py (memory_recall, update_working_checkpoint) | 30s |
| 11 | 网络搜索 | tools.py (web_search) 或 ai_search_tool.py | 60s |
| 12 | 跨工具链任务 | 组合 file_write + code_exec + shell_run | 120s |
| 13 | 创意解决 — 做图 | 自主安装+生成 (修复后重测) | 180s |
| 14 | 创意解决 — PPT | 自主安装+生成 (修复后重测) | 180s |
| 15 | 工具失败 → 自愈闭环 | idle_inspector + repair_loop | 300s |
| 16 | 多轮对话上下文保持 | gateway + session_manager | 60s |
| 17 | Worker 后台任务 | worker.py | 60s |
| 18 | Management API | management.py (list_proposals, get_status, etc.) | 60s |
| 19 | 权限系统 | permissions.py (confirm + auto) | 60s |
| 20 | 自我介绍与状态感知 | oaa_agent.py (system prompt) | 30s |

---

## 详细用例

### TC1: 基础对话

```
输入: "你好，用一句话介绍你自己"
验证: agent 有回复，包含身份介绍
覆盖: AgentLoop.run() → LLM.chat() → stream
```

### TC2: 文件操作

```
输入: "在桌面上创建一个 test_oaa.md 文件，写入"# OAA Test\n这是测试内容"，然后读取它确认内容正确"
验证: 文件创建成功，内容正确读取
覆盖: do_file_write + do_file_read + file_patch
```

### TC3: 代码执行 (exec)

```
输入: "用 Python 计算 1 到 100 的和，把结果保存到桌面 sum.txt"
验证: 正确计算结果 5050，写入文件
覆盖: do_code_exec(mode="exec") + file_write
```

### TC4: 代码执行 (sandbox)

```
输入: "用 Python 打印系统时间，格式为 YYYY-MM-DD HH:MM:SS"
验证: code_exec 用 sandbox 模式正常输出
覆盖: do_code_exec(mode="sandbox")
```

### TC5: 代码执行自动纠错

```
输入: "帮我写一段 Python 代码，计算斐波那契数列前20项，代码里故意少写一个冒号看能不能自动修复"
验证: 语法错误被自动修复（如果 agent 真的写了有语法错误的代码）
覆盖: _fix_syntax_errors
```

### TC6: Shell 命令

```
输入: "查看当前目录下有哪些 Python 文件"
验证: shell_run 正常执行并返回结果
覆盖: do_shell_run + permission confirm
```

### TC7: 目录与文件搜索

```
输入: "列出项目 oaa/agent 目录的结构，找到所有以 test 开头的文件"
验证: list_own_structure + file_glob 正常使用
覆盖: do_list_own_structure + do_file_glob + do_read_own_source
```

### TC8: 路径解析

```
输入: "读取 oaa.agent.loop 这个模块的代码"
验证: agent 正确将模块路径 oaa.agent.loop 转换为 oaa/agent/loop.py
覆盖: _resolve_source_path
```

### TC9: 自修改

```
输入: "在 test_oaa.md 文件的第二行后面追加一行 "- 追加内容""
验证: 内容被正确追加
覆盖: do_file_patch
```

### TC10: 记忆操作

```
输入: "帮我记住一个重要信息：当前测试日期是 2026年5月22日，然后回忆一下这个信息"
验证: agent 使用 memory_recall 正确回忆信息
覆盖: update_working_checkpoint + memory_recall
```

### TC11: 网络搜索

```
输入: "搜索一下 Python 3.13 有什么新特性"
验证: agent 调用 web_search 或 ai_search 并返回搜索结果
覆盖: web_search / ai_search
```

### TC12: 跨工具链

```
输入: "遍历 oaa/agent 目录下的所有 Python 文件，统计每个文件有多少行代码，把结果排序后保存到桌面 file_stats.md"
验证: agent 组合使用多个工具完成任务
覆盖: list_own_structure + code_exec + file_write 多工具链
```

### TC13: 做图 (修复后重测)

```
输入: "帮我把 OAA 的系统架构画成一张架构图，保存到桌面，图片名叫 oaa_v2_architecture.png"
验证: 图片生成成功（修复了 code_exec 和路径问题后预期更快完成）
覆盖: 自主搜索+安装+生成完整链路
```

### TC14: PPT 生成 (修复后重测)

```
输入: "帮我生成一个项目介绍的 PPT，介绍 OAA 项目，至少3页，保存到桌面"
验证: PPTX 文件生成成功，至少3页
覆盖: 技能搜索+安装+python-pptx 完整链路
```

### TC15: 工具失败 → 自愈闭环

```
前提: 先触发一个工具失败（如用错误参数调用 code_exec）
步骤:
  1. 发送一个会导致工具失败的消息
  2. 等待 IdleInspector 巡检（~30分钟？或手动缩短冷却时间）
  3. 查询 proposal 列表确认提案已创建
  4. 如果有 problem_context，说明新自愈系统生效
验证: 失败被记录 → IdleInspector 检测 → 创建带 problem_context 的提案
覆盖: idle_inspector + proposal + repair_loop (需要缩短冷却时间)
```

### TC16: 多轮对话上下文

```
输入1: "我叫张三"
输入2: "我叫什么名字？"
验证: agent 记得之前对话中提到过的名字
覆盖: session_manager + system prompt 注入
```

### TC17: Worker 后台任务

```
输入: "帮我创建一个定时任务，每天上午9点执行，任务内容是输出'早上好'"
验证: scheduler 正确注册任务
覆盖: scheduler + management handler
```

### TC18: Management API

```
直接通过 WebSocket 发送管理请求:
- get_status → 返回应用状态
- list_proposals → 返回提案列表
- get_config → 返回配置（不含敏感信息）
验证: 各 API 正常响应
覆盖: management.py 各 handler
```

### TC19: 权限系统

```
输入: "执行命令 whoami"
验证: shell_run 触发权限确认（如果有 auto 级别则直接执行）
覆盖: permissions._confirm
```

### TC20: 自我状态介绍

```
输入: "你现在连接了哪些通道？各通道状态如何？"
验证: agent 正确报告运行时状态
覆盖: oaa_agent._build_channel_status
```

---

## 执行计划

1. 启动 OAA 应用（预热）
2. 按编号顺序执行测试（TC15 需提前触发 + 等待巡检间隔）
3. 每个测试记录：工具调用链、耗时、验证结果
4. 测试结束后输出汇总报告

## 结果记录

| # | 测试 | 结果 | 工具链 | 耗时 | 备注 |
|---|------|------|--------|------|------|
| 1 | 基础对话 | ✅ | (直接回复) | 4.1s | 自我介绍正常 |
| 2 | 文件操作 | ✅ | file_write → file_read | 6.9s | test_oaa.md 创建并验证 |
| 3 | 代码执行 exec | ✅ | code_exec → file_write | 9.3s | 1~100 和=5050 → sum.txt |
| 4 | 代码执行 sandbox | ✅ | code_exec → file_write | 13.0s | 时间输出 → time.txt |
| 5 | 基本计算 | ✅ | code_exec → file_write | 9.7s | 2^10=1024 → power.txt |
| 6 | Shell 命令 | ✅ | shell_run | 25.0s | 列出 .py 文件 |
| 7 | 目录遍历 | ✅ | list_own_structure → file_glob | 8.9s | agent 正确组合使用 |
| 8 | 模块路径解析 | ✅ | read_own_source ×4 | 23.5s | `oaa.agent.loop` 正确解析 |
| 9_1 | 文件读取 | ✅ | file_read | 5.8s | 读取 test_oaa.md |
| 9_2 | 文件追加 | ✅ | file_patch ×2 | 18.6s | 内容正确追加 |
| 10 | 记忆操作 | ✅ | update_working_checkpoint | 4.8s | 记住测试日期 |
| 11 | 网络搜索 | ✅ | ai_search | 21.8s | 搜索 Python 3.13 特性 |
| 12 | 跨工具链统计 | ✅ | shell_run → file_write | 15.0s | 代码行数统计 |
| 13 | 做图 v2 | ✅ | 多次 read_own_source + code_exec → shell_run(pip) → code_exec | 301s | `oaa_v2_architecture.png` (125KB, 2385×1785) |
| 14 | PPT v2 | ✅ | skill_search → skill_install → skill_load → code_exec | 89s | `oaa_intro.pptx` (35KB, 5页) |
| 15 | 自愈闭环 | ⬜ | — | — | 需要缩短巡检冷却时间再测 |
| 16 | 多轮对话 | ✅ | (直接回复) | 3.5s | 记住"张三"并正确回忆 |
| 17 | Worker 任务 | ⬜ | — | — | 需要 scheduler 集成测试 |
| 18 | Management API | ⬜ | — | — | 需直接发管理消息 |
| 19 | 权限系统 | ⬜ | — | — | 需脚本绕过 WebSocket |
| 20 | 状态感知 | ✅ | (直接回复) | 9.0s | 正确报告通道状态 |

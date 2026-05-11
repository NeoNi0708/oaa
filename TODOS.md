# TODOs — 来自 /plan-eng-review 评审

> 创建于 2026-05-11。优先级 P0=阻塞/安全，P1=重要，P2=改进。

## P0 — 安全与正确性

- [x] **D1: 代码沙箱 → 进程级隔离** — `tools.py:_sanitize_code` 的 `__import__` monkey-patch 可绕过，改为 subprocess 隔离执行
- [x] **D8: DesktopAdapter 丢弃 llm_output** — `desktop.py:_send_chunk` 未处理 `type="llm_output"`，GUI 看不到 LLM 实时输出。2 行修复
- [x] **OV1: AgentLoop 不加载对话历史** — `loop.py:run` 每次从 `[system, user_input]` 重建，SessionManager.get_messages() 从未被调用。对话无上下文
- [x] **OV2: 前端设置与后端配置不同步** — Electron 保存到 `%APPDATA%`，Python 读 `~/.oaa/config.json`。不同文件，无同步机制。GUI 设置页无效
- [x] **OV3: Electron 不启动 Python 后端** — `electron/main.ts` 无 `child_process.spawn`。用户需手动启动 `python -m oaa`
- [x] **OV4: ask_user 无法到达用户** — `desktop.py:_send_chunk` 未转发 `type="tool_result"`。人机交互机制结构上无效

## P1 — 功能完整性与架构

- [x] **D3: 自进化 L3 完整实现** — `evolution/engine.py:crystallize_skill` 目前只存 JSON 轨迹。需要增加 LLM 驱动的模式提取和 SOP 生成
- [x] **D4: 意图识别增加 LLM fallback** — `skill_manager.py:match_intent` 纯关键词，口语化表达会漏匹配。关键词未命中时调 LLM 判断
- [x] **D5: Planner DAG 完整解析** — `planner.py:update` 未检查 `blocked_by` 依赖。增加拓扑排序 + 循环检测 + 自动推进
- [x] **D7: LLM 弹性方案** — `loop.py:run` 无重试/退避/fallback/熔断。实现完整弹性
- [x] **CQ1: 补全 start_long_term_update schema** — `tool_schema.py` 缺少该工具定义，LLM 无法调用
- [x] **CQ3: 统一错误返回格式** — `handler.py:dispatch` 返回 `{"error":...}` 与其他工具 `{"status":"error","msg":...}` 不一致
- [x] **OV5: 自进化引擎无调用者** — EvolutionEngine 仅被测试文件导入，任何代码路径不调用
- [x] **OV6: 安装脚本硬编码路径** — `scripts/install_skills.py` 写死 `E:\skills\*`，仅开发机可用
- [x] **OV7: 打包不含 Python 运行时** — `scripts/bundle_python.py` 不打包 Python，生成安装包不可运行
- [x] **OV8: 定时任务 UI 无后端** — TaskView.vue 550 行完整 UI，Python 无任何调度器
- [x] **OV9: ExtendedTools 无权限检查** — `email_send`/`wechat_send` 等绕过权限系统
- [x] **OV10: 无优雅关闭** — `app.run()` 不清理 WebSocket/适配器/子进程

## P2 — 代码质量与性能

- [x] **D6: SessionManager 连接池 + 写队列** — 每次操作新建 SQLite 连接，多通道场景可能瓶颈
- [x] **D10: SkillManager 惰性加载 + mtime 缓存** — `switch_to()` 每次重读磁盘
- [x] **CQ2: 提取共享 `resolve_workspace_path()`** — `tools.py:_resolve_path` 和 `extended_tools.py:_workspace_path` 功能重复
- [x] **CQ4: 配置文件移到 data_dir 下** — `~/.oaa/config.json` 应改为 `~/OAA/config.json`
- [x] **D2: STATUS.md 修正完成度** — 标记 email_send/skill_search/skill_install 为 stub 状态

## P3 — 测试

- [x] **D11: code_run 完整测试** — 4 条路径（Python/PowerShell/超时/sandbox）各加测试
- [x] **D12: 全部通道适配器测试** — Desktop WS mock + Gateway 核心 + 外部通道基础测试
- [x] **D13: ExtendedTools + Permissions + AgentLoop 测试** — word/excel、权限管理、Agent 循环各加测试

## P4 — 前端断裂连接 (2026-05-11)

> UI 审查发现前端多个视图与后端服务断开，数据源为 localStorage / 硬编码，需逐一连接。

- [x] **FC1: DesktopAdapter 管理消息协议** — 新增 ManagementHandler，扩展 WebSocket 消息类型支持非聊天操作（get/save config、task CRUD、skills、evolution、qr_login/poll_qr），聊天处理改为 `create_task` 避免阻塞。request_id 匹配机制实现 Promise 响应。
- [x] **FC2: 前后端配置同步** — SettingsView 从后端加载/保存配置，新增通道配置区（WeChat/DingTalk/Feishu），对齐权限结构与后端 permissions 对象
- [x] **FC3: 定时任务连接后端** — TaskView 从 TaskScheduler 加载/创建/更新/删除任务，移除 localStorage 依赖
- [x] **FC4: 新建 ConnectionsView** — 独立"连接"标签页，4 通道状态卡片网格（Desktop/WeChat/DingTalk/Feishu），扫码登录触发、状态轮询、成功/失败反馈
- [x] **FC5: 技能/进化数据加载** — SkillView 从 SkillManager / EvolutionEngine 加载实际数据，结束硬编码
- [x] **FC6: 安装 anthropic-agent-skills 插件** — `claude plugin marketplace add anthropics/skills` + `claude plugin install example-skills@anthropic-agent-skills`，17 个技能已可用

## 已解决

- [x] **D9: 通道适配器异步问题** — WeChat iLink `requests` 阻塞、DingTalk `send_message` 同步接口、Feishu `asyncio.run()` 创建新事件循环，三者全部修复：
  - **WeChat iLink**: `send_message()`/`send_typing()`/`get_updates()` 改为 async，`requests` 调用通过 `asyncio.to_thread()` 异步化
  - **DingTalk**: `send_message()`/`_get_access_token()` 改为 async，适配 Gateway 的 `await adapter.send_message()` 接口约定
  - **Feishu**: 移除 `asyncio.run()` 反模式，改用 `asyncio.run_coroutine_threadsafe()` 将消息投递到主事件循环；`send_message()` 等全链路 async

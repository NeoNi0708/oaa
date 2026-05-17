# OAA 问题追踪

> 最后更新：2026-05-17（修复 dws CLI 12 处参数不匹配 + 新增 9 个多维表工具 + GUI 链接新窗口打开）

---

## 本次会话（2026-05-15）

### dingtalk-cli dws 钉钉 CLI 集成

| 文件 | 变更 | 说明 |
|------|------|------|
| `dingtalk_cli.py` | **新增** | dws 子进程封装：自动安装(npm)、Device Auth QR 登录、17 个领域快捷命令 |
| `tool_schema.py` | +`DINGTALK_TOOLS_SCHEMA` | 17 个钉钉工具：send_message, search_user, chat, calendar, todo, doc, drive, wiki |
| `extended_tools.py` | +`DingTalkCLI` 实例 + `do_dingtalk_*` | 17 个 handler 方法，调用 dws 子进程并解析 JSON 输出 |
| `oaa_agent.py` | +条件包含 | `dingtalk.enabled && client_id` 时追加 DINGTALK_TOOLS_SCHEMA |
| `dingtalk.py` | `get_qrcode` 改为 async + `poll_qrcode_status` | 优先使用 dws Device Auth QR，失败回退 OAuth 二维码 |
| `management.py` | poll_qr / reconnect 增加 start | 钉钉确认后启动 Stream 客户端 |

**架构**：DingTalkAdapter（dingtalk-stream SDK 收发 IM）+ DingTalkCLI（dws 子进程扩展能力），类似 Feishu 的 FeishuAdapter + FeishuCLI 模式。

**开箱即用流程**：
1. 用户点击钉钉卡片 → 输入 AppKey / AppSecret
2. 点击连接 → `get_qrcode()` → 自动 `npm install -g dingtalk-workspace-cli` → Device Auth 二维码
3. 用户手机扫码授权 → `poll_qrcode_status()` → dws auth 确认 → 启动 Stream 监听 + 工具可用
4. Agent 获得 17 个钉钉工具（聊天/通讯录/日历/文档/云盘/待办/知识库等）

### lark-cli 飞书 CLI 集成

| 文件 | 变更 | 说明 |
|------|------|------|
| `feishu_cli.py` | **新增** | lark-cli 子进程封装：自动安装(npm)、凭据配置、Device Auth QR 登录、28 个领域快捷命令 |
| `tool_schema.py` | +`FEISHU_TOOLS_SCHEMA` | 18 个飞书工具：send_message, calendar, search_user, drive, docs, sheets, base, task, wiki, chat |
| `extended_tools.py` | +`FeishuCLI` 实例 + `do_feishu_*` | 18 个 handler 方法，调用 lark-cli 子进程并解析 JSON 输出 |
| `oaa_agent.py` | +条件包含 | `feishu.enabled && app_id` 时追加 FEISHU_TOOLS_SCHEMA |
| `feishu.py` | `get_qrcode` 改为 async | 优先使用 lark-cli Device Auth QR，失败回退 OAuth 二维码 |
| `management.py` | `handle` / `_handle_qr_login` / `_handle_poll_qr` 改为 async | 支持异步 adapter 方法调用 |

**架构**：FeishuAdapter（lark-oapi SDK 收发 IM）+ FeishuCLI（lark-cli 子进程扩展能力），类似 WeChat 的 WeChatAdapter + WeChatCLI 模式。

**开箱即用流程**：
1. 用户点击飞书卡片 → 输入 App ID / App Secret
2. 点击连接 → `get_qrcode()` → 自动 `npm install -g @larksuite/cli` → `config init` 写入凭据 → Device Auth 二维码
3. 用户手机扫码授权 → `poll_qrcode_status()` → 确认 → 启动 WS 监听 + 工具可用
4. Agent 获得 18 个飞书工具（日历/文档/表格/多维表格/云盘/任务等）

---

## 本次会话（2026-05-15 傍晚）

### IdleInspector — 闲置自检

| 文件 | 变更 | 说明 |
|------|------|------|
| `idle_inspector.py` | **新增** | 双阶段自检：到期任务 → 自我改进 |
| `oaa_agent.py` | +依赖注入 + done 拦截 | message 结束时触发 `IdleInspector.inspect()` |
| `app.py` | 调整顺序 | `TaskScheduler` 移到 `OAAAgent` 之前创建并注入 |

- **冷却机制**：`_INSPECTION_COOLDOWN = 300s`，避免每次消息都触发
- **阶段 1 — 到期任务**：查 `scheduler.get_due_tasks()`，列出待执行定时任务
- **阶段 2 — 自我改进**：纯启发式（无 LLM 开销），检测：
  - 同一教训重复 ≥2 次（来自 feedback 记忆）
  - 记忆密度 >80 行（高密度）/ >50 行（中等）
  - Archive topics 计数
- **始终征求用户同意**：提案结尾加 "是否执行？请确认。"

### skill_create — 技能脚手架工具

| 文件 | 变更 | 说明 |
|------|------|------|
| `extended_tools.py` | +`do_skill_create()` | 70+ 行，生成 SKILL.md + 可选资源目录 |
| `tool_schema.py` | +`skill_create` | 参数：name(必填), description(必填), resources(可选), path(可选) |

- 名称规范化：自动转小写 + 连字符（如 `My Skill` → `my-skill`）
- 生成 YAML 前注的 SKILL.md（name / description / type / init_tools）
- 可选创建 `scripts/`、`references/`、`assets/` 子目录
- 创建后自动刷新 SkillManager 使其立即可用

### Autos 架构分析 — 真正的 Agent 自主性

对比 [autos.aardio](E:\aardio\example\AI\autos.aardio) 得出的关键结论：

| 维度 | OAA (当前) | Autos |
|------|-----------|-------|
| 主动机制 | IdleInspector 外挂定时器 | 系统提示词人格化 + 宽松工具权限 |
| 确认方式 | 每次询问用户 | 默认直接执行，失败再问 |
| 自修改 | 无 | `loadcodex` / `ide_replace_code` / `save_string` 可直接改自身代码 |
| 思考模式 | 单轮 tool call | DeepSeek reasoning effort max + 多轮 interleaved thinking |
| 工具集 | 功能隔离 | 20+ 工具涵盖 IDE/GitHub/下载/搜索/文件 I/O |
| 运行成本 | — | ~0.33 元/复杂任务 |

**核心洞察**：Agent 自主性不是靠一个 idle inspection 模块就能解决的。关键差距在于：
1. **自我修改工具** — 没有修改自身代码/提示词的能力，就无法形成进化闭环
2. **系统提示词设计** — "正闲着呢，摸鱼都摸累了" 这种人格化设定比任何定时器都有效
3. **执行自由度** — 不需要每步都征求同意，而是相信 agent 在大部分场景下能自主判断

**下一步方向**（待定）：
- 给 agent 增加 `read_own_source` / `modify_own_prompt` 工具
- 在系统提示词中加入主动性描述
- 降低 tool call 的确认频率，切换到可信场景自动执行

### WeChat 通信

| # | 问题 | 根因 | 修复 | 文件 |
|---|------|------|------|------|
| 53 | WeChat 通信完全不通 | 5 类协议错误：缺 HTTP 头、send 格式错、响应字段名错、QR 状态字段错、base_url 字段名错 | 对照 openilink-sdk-go 源码逐项修正 | `wechat_ilink.py` |
| 54 | 修正后仍不通 | 旧 `__pycache__` 缓存未清除 | 手动删除 `.pyc` + 重启 | — |
| 55 | Agent 重复发送消息 | loop.py 对 APIError 作 3 次重试；WeChat 轮询游标可能返回重复 | 1) APIError 立即返回不重试 2) `_seen_msg_ids` 去重 | `loop.py`, `wechat_ilink.py` |
| 56 | 微信发图片模型收不到 | 1) 用错 SDK（openilink 第三方） 2) 图片未提取 3) base64 太大超 token 限制 | 1) 替换为官方 `wechatbot-sdk` 2) `get_updates_sync` 提取 image_item 3) Pillow 缩放到 768px + JPEG 压缩 | `wechat_ilink.py` |

### 前端

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| 57 | `<KeepAlive>` 多 v-if 编译错误 | 改为 `<component :is>` | `App.vue` |
| 58 | `renderContent` 收到对象 → `marked.parse()` 崩溃 | typeof 检查 + JSON.stringify | `ChatView.vue` |
| 59 | `tool_result` 存原始对象 | 改用 stringified resultStr | `useWebSocket.ts` |
| 60 | 发送按钮在 Agent 处理中变灰 | streaming/loading 状态下按钮逻辑修正 | `ChatView.vue`, `useWebSocket.ts` |
| 61 | WeChat 卡片连接后无操作按钮 | 添加"重连"+"新二维码"双按钮 + 状态机 | `ConnectionsView.vue` |
| 62 | 重启后按钮状态错误 | online=true 时按钮灰、offline 时可点击 | `ConnectionsView.vue` |

### 后端

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| 63 | `update_working_checkpoint` 不持久化 | 写入 `data/memory/WORKING_CHECKPOINT.md`，启动注入系统提示词 | `tools.py`, `oaa_agent.py` |
| 64 | WeChat QR 确认后 adapter 未更新 | `_handle_poll_qr` 同步更新 adapter + 重启 polling | `management.py` |
| 65 | `excel_xlsx` 缺少 `import json` | 添加导入 | `extended_tools.py` |
| 66 | async handler 返回 coroutine | `desktop.py` 加 `asyncio.iscoroutine` 检测 | `desktop.py` |

### SDK 切换

| 方面 | 之前 | 现在 |
|------|------|------|
| 包名 | `openilink-sdk-python` v0.1.1 (第三方) | `wechatbot-sdk` v0.2.1 (**官方**) |
| HTTP | requests (阻塞) | aiohttp (原生异步) |
| 图片处理 | 手写 AES + CDN + pycryptodome | 内置 `download()` + `decrypt_aes_ecb` |
| 消息类型 | 手动取 item_list | `IncomingMessage` dataclass (text/images/voices/files/videos) |
| 加密 | 额外安装 pycryptodome | 内置 cryptography |

---

## 本次会话（2026-05-17）

### 修复 P4（#72, #73, #74）

| # | 问题 | 涉及文件 | 修复内容 |
|---|------|---------|---------|
| 72 | dws CLI 参数不匹配 | `dingtalk_cli.py` | 对照 dws help 修正 12 处参数名：`--limit`→`--count/--size/--page-size/--max`，`--cursor`→`--page/--page-token/--next-token`，`--subject`→`--title`，`--summary`→`--title`，`--content`→`--markdown`，`--file`→`--file-name` 等 |
| 73 | 缺失多维表工具 | `dingtalk_cli.py`, `extended_tools.py`, `tool_schema.py` | 新增 9 个工具：sheet_info/create/list/append/read，aitable base_create/list，table_create，record_create/query |
| 74 | 链接覆盖页面 | `ChatView.vue` | `marked.parse()` 输出注入 `target="_blank" rel="noopener noreferrer"` |

### 额外发现（全量 dws 审计）

对照 `dws <subcommand> --help` 逐一检查所有 19 个 DingTalkCLI 方法，发现 6 个 STATUS.md 未记载的额外参数不匹配：`todo_create`（3 处）、`doc_create`（2 处）、`drive_upload`（1 处）、`dept_list`（命令路径错）、`sheet_info`（1 处）、`sheet_create`（1 处）、`calendar_create`（2 处）。全部已修正。

---

## 待修复

### P1 — 已修复（2026-05-15）

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| 67 | 附件按钮无反应 | 绑定 @click → hidden file input，读取文件以 base64/text 发送 | `ChatView.vue` |
| 68 | 钉钉适配器 | 适配器已实现（Stream SDK 接收 + REST 发送 + QR 登录），更新 GUI 交互 | `dingtalk.py`, `ConnectionsView.vue` |
| 69 | 飞书适配器 | 适配器已实现（WS 事件订阅 + REST 发送 + QR 登录），更新 GUI 交互 | `feishu.py`, `ConnectionsView.vue` |

### P2 — 待验证

| # | 问题 | 说明 |
|---|------|------|
| 70 | 技能进化 | `EvolutionEngine` 是否正常工作 |
| 71 | 自生技能 | `SkillManager` 动态加载能力 |

### P3 — 已知限制

- Zhipu API 间歇性超时 → 切换模型厂商
- Xunfei 非流式模式空 content → Agent 默认流式无影响
- Vue 3 v-model 与 browser tool fill 不兼容 → QA 测试用 JS 直接操作 WS
- bb-browser MCP 需每次重装 → 运行 `/mcp add bb-browser -- node F:/opc_heng/bb-browser/dist/mcp.js` 持久化

### P4 — 已修复（2026-05-17）

| # | 问题 | 文件 | 修复 |
|---|------|------|------|
| 72 | dws CLI 参数名不匹配 | `dingtalk_cli.py` | 对照 dws help 逐一修正 12 处参数（详见下文审计结果） |
| 73 | 缺失钉钉 Sheet/多维表工具 | `dingtalk_cli.py`, `extended_tools.py`, `tool_schema.py` | 新增 sheet_info/create/list/append/read + aitable_base_create/list + table_create + record_create/query，共 9 个工具 |
| 74 | 聊天链接覆盖整个 GUI 页面 | `ChatView.vue` | `marked.parse()` 输出加 `.replace()` 注入 `target="_blank" rel="noopener noreferrer"` |

### 附加：dws CLI 全量参数审计（2026-05-17）

对照实际 `dws <subcommand> --help` 输出，除 #72 已知问题外，额外发现的参数不匹配：

| 方法 | 实际 dws 参数 | 原代码 | 修复 |
|------|-------------|--------|------|
| `chat_unread` | `--count` | `--limit` | ✅ |
| `calendar_list` | `--start` `--end`（无分页） | `--cursor` `--limit`（不存在） | ✅ |
| `todo_list` | `--page` `--size` | `--cursor` `--limit` | ✅ |
| `todo_create` | `--title` `--due` `--executors` | `--subject` `--due-time` `--executor-ids` | ✅ |
| `doc_search` | `--page-size` `--page-token` | `--limit` `--cursor` | ✅ |
| `doc_create` | `--name` `--markdown` `--folder` | `--name` `--content` `--parent-id` | ✅ |
| `drive_list` | `--max` `--next-token` | `--limit` `--cursor` | ✅ |
| `drive_upload` | `--file-name` `--file-size`（自动检测） | `--file`（不存在） | ✅ |
| `dept_list` | `contact dept search --query` | `contact dept list --dept-id`（不存在） | ✅ |
| `sheet_info` | `--node` `--sheet-id` | `--workbook-id`（不存在） | ✅ |
| `sheet_create` | `--name` | `--title` | ✅ |
| `calendar_create` | `--title` `--desc` | `--summary` `--description` | ✅ |

**已确认正确**（无需修改）：`send_message` `send_group_message` `chat_list` `chat_history` `chat_search` `search_user` `get_user` `wiki_search`


---

## 已修复历史

### 命令行验证通过（2026-05-13 01:00）

| 测试 | 结果 |
|------|------|
| 双 Agent 并发 | 通过 |
| 技能发现 28 项 | 通过 |
| 技能激活 | 通过 |
| 系统提示词 | 通过 |

### 命令行验证通过（22:25-22:50）

| 测试 | 结果 |
|------|------|
| 多工具工作流 | 通过 |
| 协议研究+代码修正 | 通过 |
| get_qrcode | 通过 |

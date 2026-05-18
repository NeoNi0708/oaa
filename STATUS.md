# OAA 问题追踪

> 最后更新：2026-05-18（P0 Phase 1 + Phase 2 完成：code_exec, 消息压缩, tool装饰器, 协议自动检测）

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

## 本次会话（2026-05-18 续）— P0 Phase 1

### 闭环断点 1：code_exec — 运行时代码执行

| 文件 | 变更 | 说明 |
|------|------|------|
| `_exec_runner.py` | **新增** | 安全 exec() 子进程，禁用 `os.system`/`subprocess.Popen`/`shutil.rmtree`/`exec`/`eval`/`compile`，通过 `result` 变量返回值，写 JSON 到结果文件 |
| `tools.py` | +`do_code_exec` | 异步方法，解析 JSON result，与 `code_run` 并存（沙箱 vs exec） |
| `tool_schema.py` | +`code_exec` | `{code: string, timeout: int}`，默认 15s，max 60 |

**安全策略**：`_exec_runner.py` 分三层防御：① 函数打补丁 — 在 `os`/`subprocess`/`shutil` 模块加载后替换危险函数为 stub；② 受限 builtins — 移除 `exec`/`eval`/`compile`；③ 子进程隔离 — 与 `_sandbox_runner.py` 同样以 `python -I` 独立进程运行。

**验证结果**：`result=42` ✓、`dict result` ✓、`os.system` 阻断 ✓、`subprocess.run` 阻断 ✓、`shutil.rmtree` 阻断 ✓、`exec` 阻断 ✓、标准库允许 ✓、无 result 返回 None ✓

### 闭环断点 4：消息压缩 — 防止 context overflow

| 文件 | 变更 | 说明 |
|------|------|------|
| `loop.py` | `AgentLoop.__init__` 加 `max_messages=60` | 默认保留 1 条 system + 59 条最近消息 |
| `loop.py` | `_compact_messages()` | 每轮 `_build_turn_messages` 后调用，保留 system + 最近 `max_messages-1` 条 |
| `loop.py` | +MemoryManager 联动 | 首次压缩时写入 HOT memory：`[消息压缩] 已压缩 N 条消息，原始请求: "..."` |

**策略**：截断式压缩（Phase 1），只保留最近上下文。压缩记录写入持久记忆，避免 agent 完全丢失原始请求上下文。后续 Phase 3 可升级为 LLM 摘要式压缩。

---

## 本次会话（2026-05-18 续）— P0 Phase 2

### 闭环断点 2：tool_decorator — 工具注册轻量化

| 文件 | 变更 | 说明 |
|------|------|------|
| `tool_decorator.py` | **新增** | `@agent_tool(name, description)` 装饰器，`inspect.signature()` 自动生成 OpenAI schema |
| `handler.py` | +`__init_subclass__` + `dispatch` 回退 | 自动收集 `_tool_meta` 到 `_tool_registry`；`dispatch()` 先查方法再查 registry |
| `oaa_agent.py` | `_MergedHandler.__getattr__` + registry 查询 | 新增 backend `_tool_registry` 回退链 |
| `oaa_agent.py` | schema 组装 + `collect_tool_schemas()` | `_tools_schema` 自动包含装饰器注册的工具 schema |
| `tools.py` | 迁移 `file_read` | `do_file_read(self, path: str, start: int = 1, count: int = 200, keyword: str = "")` 显式参数 + `@agent_tool` |
| `tool_schema.py` | 移除 `file_read` | schema 已由装饰器自动生成 |

**架构变化**：工具注册从"手动写 schema 在 `tool_schema.py` + 实现 `do_*` 在 `tools.py`"两处修改，变为"一处 `@agent_tool` 装饰器即可"。现有工具可逐步迁移，`file_read` 为示范。

### 闭环断点 6：协议自动检测

| 文件 | 变更 | 说明 |
|------|------|------|
| `client.py` | +`_detect_api_format()` | 内置 heuristic：`api_format` 显式设置优先 → URL 含 `anthropic.com` 则 `anthropic` → 其余默认 `openai` |

**兼容性**：`api_format` 现有配置完全不受影响（显式设置优先级最高）。自动检测仅在未设置时生效。

---

## 待修复

### P0 — 闭环补全计划（OAA v2）

**核心问题**：OAA 功能模块已不少，但彼此孤立，没有形成"执行→反馈→学习→改进"的闭环。Agent 的能力上限被锁定在预设工具集内。

**原则**：不改现有架构，只在断点处插入连接逻辑。

#### 闭环断点 1 — 没有运行时代码执行（`loadcodex` 等价物）

| 维度 | 现状 | 目标 | 状态 |
|------|------|------|------|
| `code_run` | 沙箱子进程，有 timeout，拿不到返回值 | 进程内 `exec()`，捕获 `locals()` 返回值 | ✅ **Phase 1 完成** |
| 能力上限 | 预设工具的并集 | 任意 Python 代码可执行 | ✅ **Phase 1 完成** |

工作项：
1. ✅ **完成** 创建 `oaa/agent/_exec_runner.py` — 安全 `exec()` 封装，限制 `builtins`（禁用 `os.system`, `subprocess`, `shutil.rmtree` 等），通过 `result` 变量约定返回值
2. ✅ **完成** 在 `AtomicTools` 新增 `do_code_exec(args)` — 与 `code_run` 并存，`code_run` 保持沙箱模式用于不信任代码，`code_exec` 用于 agent 自我扩展
3. ✅ **完成** schema 定义：`{code: string, timeout: int, description: "..."}`

#### 闭环断点 2 — 工具注册太重

| 维度 | 现状 | 目标 | 状态 |
|------|------|------|------|
| 新增工具 | 4-5 处修改跨多个文件 | 1 处修改，一个文件 | ✅ **Phase 2 完成** |

工作项：
1. ✅ **完成** 创建 `oaa/agent/tool_decorator.py` — `@agent_tool(name, description)` 装饰器
   - `inspect.signature()` 提取参数名+类型+默认值 → 自动生成 OpenAI 兼容 schema
   - 自动注册到类上的 `_tool_registry` 字典
2. ✅ **完成** 修改 `_MergedHandler` — 在 `__getattr__` 回退前查询各 backend 的 `_tool_registry`
3. ✅ **完成** 迁移 `file_read` 作为示范（显式参数签名 + @agent_tool 装饰器），其余逐步迁移

#### 闭环断点 3 — 代码执行无自动纠错

工作项：
1. 在 `do_code_exec` 中加入纠错层：
   - `SyntaxError` → 用 `ast` 解析 + 常见修复（缩进、缺 `import`、变量名拼写）
   - `NameError` → 自动插入 `import` 后重试
   - `TypeError`/`ValueError` → 返回完整 traceback 给 LLM 自修复
2. 如果修复成功，将修正后的代码也返回给 LLM 供学习

#### 闭环断点 4 — 消息队列无限增长

工作项：
1. ✅ **完成** `AgentLoop.__init__` 加 `max_messages: int = 60` 参数
2. ✅ **完成** 每轮结束后调用 `_compact_messages()`，逻辑：
   - 保留 system prompt（第 0 条）
   - 保留最近 `max_messages-1` 条消息
   - 移除中间的工具调用细节
3. ✅ **完成** 与 MemoryManager 联动：首次压缩时将原始请求写入 HOT memory

#### 闭环断点 5 — 工具失败未影响 agent 行为

工作项：
1. **已完成** `_memory_mgr.add_tool_failure()` 记录（`loop.py:197`）
2. **待完成** IdleInspector Phase 3 `_check_tool_failures()`：
   - 按工具名分组统计，检测重复失败模式
   - 对失败 ≥2 次的工具，生成修复建议
   - 提示用户 → 用户确认 → agent 执行 `read_own_source` → 出方案 → `file_patch` 修复
3. 修复后自动清除 `__pycache__` + 尝试 `reload_module`

#### 闭环断点 6 — 协议适配需手动配置

工作项：
1. ✅ **完成** `LLMClient._detect_api_format()` — URL 启发式检测：`api_format` 显式配置优先，其次 URL 含 `anthropic.com` 则 `anthropic`，其余默认 `openai`
2. **待定** 协议级参数适配（`max_tokens` vs `max_completion_tokens`）— 下游客户端已有区分，当前仅需路由正确

---

### 执行顺序

```
Phase 1（断点 1 + 4） ✅ 完成 → Phase 2（断点 2 + 6） ✅ 完成 → Phase 3（断点 3 + 5）
                                                             2 天
```

Phase 1 已完成：`code_exec` 给 agent 无限扩展能力，消息压缩防止 context overflow。Phase 3 形成真正的自我改进闭环。

---

### 真正的难点

功能实现本身不难。真正难的是：

1. **安全与自由的平衡**：`exec()` 给了 agent 无限能力，但也给了它删文件的能力。沙箱策略需要反复调优。
2. **LLM 对工具的使用意愿**：工具再好，LLM 不用就没用。系统提示词的写法、工具描述的清晰度、返回值的可读性，直接影响 agent 是否愿意调用 `code_exec` 而不是硬编答案。
3. **闭环的惯性**：`tool_failure → 记录 → 检测 → 修复` 链条只要有一步断掉，闭环就失效。每一步都需要监控——failure 记录了但 IdleInspector 没触发怎么办？触发了但 agent 修复方案出错怎么办？
4. **错误的自动修复 vs 让 LLM 自己悟**：aifix 式的自动纠错效率高，但会剥夺 LLM 从错误中学习的机会。过度纠错 = 替 agent 思考，反而阻碍自主性成长。

这三个难点比代码本身更值得关注。

---

### OAA v2 设计原则

以下原则指导所有闭环补全工作的具体实现。

#### 原则 1：分层执行，不搞 all-or-nothing 安全

`code_exec` 提供运行时代码执行能力，但不等于给 agent 操作系统全部权限。

| 风险等级 | 工具 | 限制 | 适用场景 |
|---------|------|------|---------|
| L1 只读 | `code_exec`（默认） | 禁用 `os`、`subprocess`、`shutil`、`open()` 写模式 | 数据计算、格式转换、分析 |
| L2 写文件 | `file_write` / `file_patch` | 代码不能直接写文件，通过工具写 | 保存结果、修改配置 |
| L3 系统操作 | `shell_run` | 每次执行前用户确认 | 安装包、改系统设置 |

系统提示词中明确告知 agent 边界：

> `code_exec` 是只读环境，不能直接操作文件或系统。需要写文件用 `file_write`，需要执行命令用 `shell_run`。试图在 `code_exec` 中绕过限制会返回安全错误。

#### 原则 2：制造"不调工具就会错"的处境

LLM 天然倾向凭训练数据编答案，而不是调用工具。优化工具 description 效果有限，需要从激励层面解决。

系统提示词中写规则：

> 涉及当前信息的问题 → 必须调用工具获取据实回答，不能凭训练数据编造。
> 涉及文件内容 → 必须 `file_read` 后回答。
> 涉及计算/数据处理 → 必须 `code_exec` 执行后回答。
> 直接凭记忆回答上述问题将被视为幻觉。

同时提供 few-shot 示例：

> 好的调用：用户问"计算 X" → 调 `code_exec` → 拿到返回值 → 直接输出结果。
> 坏的响应：用户问"计算 X" → 不调工具 → 凭记忆估算 → 精度丢失 → 视为幻觉。

工具返回值设计成"可直接作为答案输出"的格式，降低 LLM 加工成本。

#### 原则 3：每层独立容错，链断不丢记录

`tool_failure → 记录 → 检测 → 修复 → 验证` 链条中每步都可能断，但每步都必须独立容错：

- **记录**：`try/except` 包住，记录失败不影响正常流程。持久化到文件，进程重启不丢失。
- **检测**：IdleInspector 异常时 `logger.warning` 吞掉，不影响消息处理。冷却期过后自动重试，不丢 pending failure。
- **修复**：agent 修复方案出错时，`file_patch` 自动备份 + `rollback_change` 可回滚。备份文件留存 30 天。
- **验证**：`reload_module` 失败时提示"修改已保存但需重启生效"，不阻塞用户。

IdleInspector 不是唯一入口。加 `summary` 隐式入口——用户问"最近有什么问题？"时 agent 可直接读取 `tool_failures.md` 回答。

#### 原则 4：只自动修复 LLM 无法从中学习的东西

代码执行出错时，不是一刀切修复或全扔给 LLM。按错误类型分层：

| 错误类型 | 处理方式 | 理由 |
|---------|---------|------|
| `SyntaxError`（缩进/缺冒号/缺括号） | **自动修复**，返回原始代码 + 修正代码 + 差异 | 这是 token 预测的机械性遗漏，LLM 看了差异也学不到什么 |
| `NameError`（缺 import/变量名拼错） | **自动补全** import 后重试，注明补充内容 | 同上，机械性问题 |
| `TypeError`/`ValueError`（参数类型/值不对） | **不修复**，返回完整 traceback | 这是逻辑错误，LLM 需要看到错误自己推理 |
| 业务逻辑错误（算错了/逻辑不对） | **不修复**，返回结果 + 期望对比 | LLM 必须自己分析哪里错了 |

追踪 `code_exec` 首次成功率作为反馈指标——持续上升说明 LLM 在学习，停滞或下降说明策略需要调整。

#### 原则 5：模块功能 ≠ 系统能力，闭环才是

OAA 不缺模块。gateway、adapter、tool、memory、skill、MCP、evolution 都在。但模块之间没有形成"执行→反馈→学习→改进"的回路。

判断一项工作是否优先级高的标准：**它是否让某个闭环少一个断点？**
- `code_exec` → 是（agent 能自我扩展，不再锁死在预设工具集）
- 消息压缩 → 是（防止长对话自然死亡，否则 loop 跑不完就断了）
- IdleInspector Phase 3 → 是（tool_failure 从"死记录"变成"活反馈"）
- 优化某个工具的 description → 否（单点优化，不断环）

**每项功能实现后，必须自问：这次改动后，agent 能在无人干预的情况下比之前多走几步？如果答案是 0，这个改动不产生闭环价值。**

#### 原则 6：不要替 agent 思考

IdleInspector 只做模式检测和提案，不做决定。`code_exec` 出错时只返回错误，不自动重试。工具执行失败记录到 `tool_failures.md`，但不自动修复。

每个环节保留给 LLM 的思考空间。系统的作用是提供信息和工具，而不是替 agent 做决策。过度自动化 = 剥夺 LLM 的学习机会 = 阻碍自主性成长。

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

### A3 空泡修复 + 代码审查（2026-05-18）

| # | 问题 | 涉及文件 | 修复 |
|---|------|---------|------|
| A3 | 空聊天气泡（agent 假死） — 新消息替换旧任务时，后端发空 done 导致前端 loading 被错误关闭、streaming 与旧任务残留混合 | `desktop.py`, `useWebSocket.ts`, `ChatView.vue` | ⑤ `CancelledError` 检查 `_chat_tasks` 是否已被替换 → 被替换时静默退出（stop_chat 仍发送 done）⑥ `send()` 重置 `streamingContent` 防止内容混淆 ⑦ 移除 `watch(streaming)` 激进的 loading 控制 |
| — | stop_chat 取消 task 后仍调用 management handler 设 idle 状态（双重设置） | `desktop.py` | `_handle_management` 中 stop_chat 分支提前 return |
| — | 已知：快速换消息时 streaming 气泡有短暂间隙 | — | `send()` 清空 `streamingContent` 后到新任务首块到达前视觉空白 < 500ms，用户刚发消息注意力不在等待，可接受 |

### 输出截断自动续写（2026-05-18）

| # | 问题 | 涉及文件 | 修复 |
|---|------|---------|------|
| — | 输出截断自动续写 | `loop.py`, `client.py`, `anthropic_client.py` | `LLMResponse.finish_reason` 字段，OpenAI stream 和 Anthropic message_delta 分别捕获 → `loop.run()` 检测 `finish_reason in ("length","max_tokens")` 且无 tool_calls 时自动追加续写提示（最多 5 次），`_last_llm_content` 兜底空 content 场景。tool 结果截断 2000→8000 字符。 |

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

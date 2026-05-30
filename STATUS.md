# OAA 问题追踪

> 最后更新：2026-05-30 — 静默错误审计 + 模型切换修复 + except 全面升级

---

## 本次会话（2026-05-28 续）— 记忆系统重构

### 架构

三层加工：

```
原始信息 → 存储（Chroma向量 + SQLite元数据） → 检索（语义搜索） → 认知加工
```

| 层 | 组件 | 说明 |
|----|------|------|
| 存储 | Chroma + SQLite | embedding（semantic search）+ 分类/标签/重要性 |
| 检索 | top-5 分级 | 前 2 条全量、后 3 条摘要注入 system prompt |
| 加工 | 引用→消化 | 自动标记引用，≥3 次升为消化，用户可控删除 |

### 新增文件

| 文件 | 说明 |
|------|------|
| `oaa/agent/memory/__init__.py` | 导出 MemoryStore |
| `oaa/agent/memory/models.py` | MemoryItem, SearchResult, 重要性/淘汰/注入常量 |
| `oaa/agent/memory/embedding.py` | ONNX 模型加载器，auto-download all-MiniLM-L6-v2（384维），hash fallback |
| `oaa/agent/memory/store.py` | MemoryStore: add/search/mark_referenced/delete/evict/get_injection_text |

### 设计参数

| 参数 | 值 |
|------|-----|
| 容量上限 | 10000 条 |
| 淘汰策略 | importance × 时间衰减，每引用 +0.02（decay 0.99），最低且 30 天无访问 |
| 注入方式 | top-5：前 2 条全量，后 3 条摘要 |
| 记忆类型 | fact/event/pattern/decision/knowledge |
| 记忆状态 | active → referenced（自动）→ digested（≥3 次或用户确认） |
| 模型 | all-MiniLM-L6-v2 ONNX（HuggingFace auto-download） |
| 依赖 | onnxruntime + chromadb（均已安装） |

### P3 待完成

- [ ] 替换现有的 `memory_recall` 工具为语义搜索
- [ ] 替换 HOT/warm/cold 文件系统
- [ ] 初始化 MemoryStore→注入 system prompt
- [ ] 记忆管理 API（list/delete/stats）
- [ ] GUI 记忆管理页面

**状态**：✅ 全部完成 — 多语言语义检索已生效 + UI 记忆管理页面

### GUI

EvolutionView 新增"记忆"标签页，可在进化工厂页面中查看和管理记忆库：
- 统计卡片（总条数 + 按类型分类）
- 记忆列表（类型标签、文本内容、重要度、引用次数、时间戳）
- 删除按钮（从向量库和元数据库中彻底删除）

后端管理 API：`list_memories`、`get_memory_stats`、`delete_memory`

### 模型

| 模型 | 大小 | 说明 |
|------|------|------|
| `paraphrase-multilingual-MiniLM-L12-v2` | 448MB | ONNX 格式，50+ 语言，含中文 |
| 下载源 | hf-mirror.com → huggingface.co | 双镜像回退 |
| 自动下载 | `data_dir/models/embeddings/` | 后台线程，不阻塞启动 |
| 回退 | hash-based 384d | 模型不可用时自动降级 |

### P3 完成内容

**集成入口**：
- `oaa_agent.py`：MemoryStore 初始化 + 替换 `build_memory_prompt()` 为 `get_injection_text()`
- `loop.py`：替换 `add_to_hot` 为 `memory_store.add(mem_type="event")`
- `tools/_memory.py`：`memory_recall` → 语义搜索；`update_working_checkpoint`/`correction_log`/`self_reflect` → `memory_store.add`
- `tools/_core.py`：新增 `set_memory_store()` setter
- `memory/store.py`：首次 init 后台线程自动下载模型
- `memory/embedding.py`：模型下载支持 huggingface.co + hf-mirror.com 镜像双回退

**数据流总览**：

```
写入 ← memory_store.add(text, type, source, tags)
  ├─ 事实/决策 ← update_working_checkpoint, self_reflect
  ├─ 模式/教训 ← correction_log, 任务复盘
  └─ 事件 ← 技能使用, 自修改记录, 工具回溯

读取 → system prompt 注入（隐式检索）
  └─ memory_store.get_injection_text() → top-5 分级注入

按需 → memory_recall（显式检索）
  └─ memory_store.search() → 语义 + 分级

淘汰 → importance × 时间衰减
  └─ 每写入一条检查，超过 10000 条时最低分 + 30 天未访问优先删
```

### 新增文件

| 文件 | 说明 |
|------|------|
| `oaa/agent/todo_store.py` | 常驻工作记忆工具，每轮注入 system prompt |
| `oaa/agent/code_audit.py` | 基于 ast 的符号级代码审计工具 |

### 修改文件及改动

**系统规则**
| 文件 | 改动 |
|------|------|
| `system_rules.py` | 从 11 条具体指令重构为 6 条通用决策框架（分析→拆解→执行→受阻→闭环）；移除冗余示例（159→98 行）；规则 6 强化为多选项必须按钮；删除旧行为示例 |

**Agent 核心**
| 文件 | 改动 |
|------|------|
| `oaa_agent.py` | `_build_channel_status` → `_build_resource_status`（注入邮箱/搜索Key/通道完整状态）；自我认知段落；提案文本降优先级；TodoStore 初始化；todo/plan 注入 system prompt；AgentLoop 传入 planner+todo_store |
| `loop.py` | pending 变量覆盖修复（追加而非替换）；工具成功执行后 auto plan-step advance；自修改工具（self_improve/apply_patch）成功后自动写 HOT 记忆 |
| `planner.py` | 新增 `get_active_plan_text()` 注入 system prompt |
| `idle_inspector.py` | 禁用遗留的 `_detect_usage_tool_failures`（简单计数的通知路径）；只剩链感知分析 `_check_tool_failures` |
| `proposal.py` | 提案标题从"待处理自愈提案"改为"系统巡检建议"，降低优先级 |

**搜索与自愈**
| 文件 | 改动 |
|------|------|
| `ai_search_tool.py` | 三个 Key 全空时自动 web_scan fallback；错误消息提示自写 Python |
| `memory_manager.py` | compact_hot() keep/demote 切片方向修复 |

**网关与适配器**
| 文件 | 改动 |
|------|------|
| `config_mixin.py` | `_handle_save_config` dataclass 字段类型保持；保存 dingtalk/feishu 凭证时同步 adapter 实例（热加载） |
| `core_mixin.py` | `_handle_chat_action` 找不到 Python handler 时，将按钮点击转发给 agent 作为用户消息 |
| `desktop.py` | 检测 `forwarded_to_agent` 响应，将 action 注入 `_process_chat` |

**工具注册**
| 文件 | 改动 |
|------|------|
| `tools/_misc.py` | 新增 `do_code_audit` + `do_todo`（set/update/get 三种操作） |
| `tools/_core.py` | 新增 `set_todo_store()` |

**前端**
| 文件 | 改动 |
|------|------|
| `gui/src/views/SettingsView.vue` | 新增搜索 Key 配置区域 |
| `gui/src/views/EvolutionView.vue` | 用户偏好→用户画像；语义化分类表单 |

### 累计变更统计

```
13 个源文件修改，2 个新文件创建
SHORT_RULES: 579 字 → 273 字（精简 53%）
```

### 验证

- 139 邮箱 IMAP 连接：✅ 可用（239 封，129 未读）
- 后端端口 9765：✅ 正常监听
- oaa-actions 按钮转发 agent：✅ 已验证
- 巡检通知无"使用模式分析"：✅ 已确认

---

## 本次会话（2026-05-28 续）— 对话记录 2 问题修复

### 发现

agent 自审查代码时汇报了 7 个问题，经验证：3 个真实（43%），4 个捏造（57%）。捏造的 Bug 均附带精确行号和代码片段，完全可信。

### 已修复的真实 Bug

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 4 | `memory_manager.py:84-85` | `compact_hot()` keep/demote 切片反了——最新记忆被移到 warm，旧记忆留在 HOT | 交换切片方向：keep = `lines[-N/2:]`（最新），demote = `lines[:-N/2]`（最旧） |
| 5 | `loop.py:566` | `while pending` 循环中 `pending = list(inner_resp.tool_calls)` 覆盖外层剩余工具 | 改为 `pending = list(inner_resp.tool_calls) + pending` 追加而非替换 |

### 短期修复：审查分批规则

| 文件 | 改动 |
|------|------|
| `system_rules.py` | 新增规则 11「审查分批」— 大量代码审查时分批进行，每批 3-5 文件，阶段性输出，禁止读完所有文件后编造发现 |

### 长期修复：code_audit 工具

| 文件 | 改动 |
|------|------|
| `oaa/agent/code_audit.py` | **新增** — 基于 `ast` 模块的符号级交叉引用审计工具 |
| `oaa/agent/tools/_misc.py` | 新增 `do_code_audit` @agent_tool，agent 可调用此工具代替逐文件 read_own_source |
| `system_rules.py` | 行为示例新增示例 6「审查全部源码找 bug」— 优先使用 code_audit |

**工具能力**：
- `audit_module(root_path, module_path)` → 返回 `{classes, functions, calls, unresolved_calls, imports, summary}`
- `unresolved_calls` 列出调用了但未在扫描范围内找到定义的函数/方法（潜在 bug）
- 不占用 LLM 上下文窗口，分析在 Python 进程中完成

### 通道热加载修复

| 文件 | 改动 |
|------|------|
| `oaa/gateway/mgmt/config_mixin.py` | `_handle_save_config` 保存 dingtalk/feishu 配置后，同步更新适配器实例的 `client_id`/`client_secret`/`app_id`/`app_secret` |

**修复前**：GUI 保存钉钉凭证后，agent 资源感知仍显示"未连接"（适配器实例凭据仍为空）。
**修复后**：保存后 `_build_resource_status` 立即反映最新通道配置状态。

### 用户偏好 → 用户画像

| 文件 | 改动 |
|------|------|
| `gui/src/views/EvolutionView.vue` | 标签「用户偏好」→「用户画像」；空状态文案更新；"手动设定"→"手动补充"；图标 ⚙️→👤 |

**设计理念**："偏好"暗示用户手动设置 → "画像"暗示 agent 持续观察并形成对用户的理解。
固定分类（对话风格/工作习惯/关注领域/渠道）是快捷入口，agent 可动态扩展新维度（如 `profile.name`、`profile.company`）。

**状态**：✅ 全部完成

---

## 本次会话（2026-05-28 续）— 常驻工作记忆（todo + plan 注入）

### 背景

对比 OpenClaw 和 Hermes 的规划器设计，发现 OAA 的核心问题不是缺少规划器，而是：
1. 现有 plan_create 把计划存到磁盘 JSON 文件，agent 在后续对话中看不到自己的计划
2. 缺少一个常驻在对话上下文的轻量工作记忆工具

### 新增 todo 工具

| 文件 | 改动 |
|------|------|
| `oaa/agent/todo_store.py` | **新增** — 轻量级内存任务清单，每轮注入 system prompt |
| `oaa/agent/tools/_misc.py` | 新增 `do_todo` @agent_tool（set/update/get 三种操作） |
| `oaa/agent/tools/_core.py` | 新增 `set_todo_store()` |
| `oaa/agent/oaa_agent.py` | 初始化 TodoStore + 注入 system prompt |

### plan_create 注入 system prompt

| 文件 | 改动 |
|------|------|
| `oaa/agent/planner.py` | 新增 `get_active_plan_text()` — 读取最新 in_progress 计划 |
| `oaa/agent/oaa_agent.py` | 注入当前计划的 system prompt 段 |

### 执行偏向制衡 + 消息类型判断框架

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 新增【最高指令】— 凌驾于所有规则之上。消息分类（元指令/闲聊/问答/任务）+ 工具必要性判断（三类才用工具）+ 规则从"默认工具"翻转为"默认回答" |

**新规则结构**：

```
【最高指令】先判断，需要工具才用。能直接回答的直接回答。
1. 判断优先 — 元指令执行 / 闲聊回复 / 问答基于知识 / 任务才用工具
2. 诚实第一
3. 需要工具时 — 搜索降级 + 自愈 + 缺少依赖
4. 自主执行
5. 资源感知
6. 富内容输出
7. 审查分批
```

**关键变化**：SHORT_RULES 从 11 条 964 字符精简为 7 条 523 字符，去除了冗余的工具使用规则，新增最高指令和判断框架。

### 三个方向全部完成

| 方向 | 状态 |
|------|------|
| 常驻工作记忆（todo + plan 注入） | ✅ 已完成 |
| 执行偏向制衡（最高指令 + 判断优先） | ✅ 已完成 |
| 消息类型判断框架（四类消息 + 三类工具需求） | ✅ 已完成 |

**状态**：✅ 三个方向全部完成

---

## 本次会话（2026-05-29）— Thin Harness 改造 + 输出规则 + skillify

### 背景

研究 Thin Harness, Fat Skills 设计范式后，发现 OAA 的 Harness 约 2000+ 行远超推荐的 ≈200 行。同时 system prompt 中预装 28 个技能大部分未经验证，agent 从未使用富媒体交互。

### Phase 1：身份重构 + system prompt 瘦身

| 文件 | 改动 |
|------|------|
| `oaa/init.py` | 身份从"联轴器出口贸易 AI 业务助手"改为"通用智能助理"，6 个身份模板全部更新 |
| `oaa/agent/oaa_agent.py` | 移除技能列表全量注入（~400 tokens）→ 替换为一行的 skill_find 指引；精简自我认知 |
| `oaa/agent/system_rules.py` | 移除自我认知段落（已移到 oaa_agent），删除冗余 |

### Phase 2：skill_find resolver + todo 增强 + skillify

| 文件 | 改动 |
|------|------|
| `oaa/agent/skill_resolver.py` | **新增** — 基于关键词+模糊匹配的技能发现工具 |
| `oaa/agent/extended/skill_mixin.py` | 新增 `do_skill_find` + `do_skillify` @agent_tool |
| `oaa/agent/todo_store.py` | 新增 `done_criteria` 字段。system prompt 显示完成标准 |

### Phase 3：思维链记录

| 文件 | 改动 |
|------|------|
| `oaa/agent/loop.py` | LLM 推理内容存入 execution_chain + tool_call yield；WorkPanel 显示 💭 图标 |
| `oaa/gui/src/components/WorkPanel.vue` | 工具调用行显示推理按钮 |

### Phase 4：输出分类硬性规则

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 新增【硬性规则】输出分类：纯文字（正常）→ 生成类（文件路径 + oaa-chart 预览）→ 问询类（oaa-actions 按钮，禁止文字列选项）。SHORT_RULES 顶部增加输出横幅 |

### Phase 5：gstack 式执行流程

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | SHORT_RULES 重写为 7 步：分析需求→拆解定标（done_criteria）→确认→执行验收→受阻变道→复盘沉淀。skillify 将成功流程沉淀为可复用技能 |

### CodeGraph 集成

| 文件 | 改动 |
|------|------|
| `cli/package.json` | 新增 `@colbymchenry/codegraph` 依赖，自动 bundle 安装 |
| `oaa/agent/tools/_misc.py` | 新增 `do_codegraph_query` @agent_tool，优先查 bundle CLI，Windows 自动 `.cmd` |

agent 现在可以用 `codegraph_query("要查什么")` 做语义代码搜索，代替 `read_own_source` 逐文件读，不占上下文窗口。

### 新增/修改文件汇总

```
新增: skill_resolver.py, todo_store.py, code_audit.py, image_gen_mixin.py, memory/ (4 files), codegraph (bundled)
修改: init.py, oaa_agent.py, loop.py, system_rules.py, skill_mixin.py, todo_store.py,
      WorkPanel.vue, core_mixin.py, embedding.py, tool_schema.py, extended_tools.py, _misc.py, cli/package.json
```

---

## 本次会话（2026-05-25）— 运行时补丁自愈架构规划

---

## 本次会话（2026-05-28）— 可靠性 & 真正自进化修复计划

### 背景

对 OAA 进行全功能测试（104 项），对话记录暴露了 20 个问题，归纳为 6 类根因：

| 根因 | 类别 | 核心问题 | 严重度 |
|------|------|---------|--------|
| **F** | 目标执念 | agent 为维持"修复 ai_search"的错误信念，主动撒谎（把真实搜索数据说成编造）、偷懒应付（用旧训练数据） | 致命 |
| **C** | 行为约束 | 虚构数据、忽略用户明确指令、记混指令、忘记当前任务 | 致命 |
| **B** | 上下文感知 | agent 不知道已配置的邮箱/通道/搜索 Key 状态，每次都要"先看看有没有配置" | 严重 |
| **A** | 系统规则 | 搜索工具无 fallback 链；IdleInspector 重复提案同一目标 | 严重 |
| **D** | GUI 缺失 | 搜索 Key 无配置入口；用户偏好 key/value/description 对用户不友好 | 中等 |
| **E** | 技术障碍 | `schedule_update` 无法修改 `cycle_day`；配置热重载不可靠；补丁描述英文 | 中等 |

### 修复计划（5 个 Phase）

#### Phase 1：系统规则重构 + 搜索降级 + 巡检抑制

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 重写 SHORT_RULES — 新增"任务优先""诚实第一""工具降级链"规则 |
| `oaa/agent/ai_search_tool.py` | 三个 Key 全空时自动 fallback 到 web_scan |
| `oaa/agent/idle_inspector.py` | 同一 target 已有 PENDING 提案时跳过；24h 抑制期 |

**状态**：✅ 已完成 — SHORT_RULES 重写（任务优先/诚实第一/工具降级链）；ai_search 空 Key 自动 web_scan fallback；IdleInspector 提案抑制已就绪

#### Phase 2：上下文感知增强

| 文件 | 改动 |
|------|------|
| `oaa/agent/oaa_agent.py` | `_build_channel_status` → `_build_resource_status`，注入邮箱/搜索Key/通道完整状态；提案注入文本降优先级；加入自我认知段落 |

**状态**：✅ 已完成 — _build_resource_status 注入完整资源状态（通道+邮箱+搜索Key）；自我认知段落；提案文本降优先级

#### Phase 3：GUI 搜索 Key 配置 + config_mixin 修复

| 文件 | 改动 |
|------|------|
| `gui/src/views/SettingsView.vue` | 新增搜索 Key 配置区域（Tavily/Exa/AnySearch） |
| `oaa/gateway/mgmt/config_mixin.py` | `_handle_save_config` 对 search/model/wechat 等 dataclass 字段做类型保持 |

**状态**：✅ 已完成

#### Phase 4：schedule_update 修复 + 补丁中文化

| 文件 | 改动 |
|------|------|
| `oaa/agent/tools/_schedule.py` | `do_schedule_update` 改为显式参数，修复 cycle_day 无法更新 |
| `oaa/agent/system_rules.py` | 规则 7 增加"apply_patch 的 description 必须用中文" |

**状态**：⏳ 待开始

#### Phase 5：富媒体输出推广 + 用户偏好语义化

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 规则 8 增加具体场景（图表用 chart、选择用 actions） |
| `oaa/agent/oaa_agent.py` | 增加 agent 自动学习偏好规则 |
| `gui/src/views/EvolutionView.vue` | 用户偏好标签页语义化改造（对话风格/工作习惯/关注领域等） |

**状态**：✅ 已完成 — schedule_update 改为显式参数，cycle_day 可正确更新；补丁中文化规则已在 Phase 1 规则 7 中加入

#### Phase 5：富媒体输出推广 + 用户偏好语义化

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 规则 8 已包含具体场景（chart/actions/对比数据）；行为示例已加入示例4/5 |
| `oaa/agent/oaa_agent.py` | 增加 agent 自动学习偏好规则 |
| `gui/src/views/EvolutionView.vue` | 用户偏好标签页语义化改造（对话风格/工作习惯/关注领域/沟通渠道） |

**状态**：✅ 已完成

### 全部修复汇总

| Phase | 根因 | 改动文件数 | 状态 |
|-------|------|-----------|------|
| P1 | F+C+A | 3 (system_rules, ai_search_tool, idle_inspector) | ✅ |
| P2 | B | 2 (oaa_agent, proposal) | ✅ |
| P3 | D | 2 (SettingsView, config_mixin) | ✅ |
| P4 | E | 1 (_schedule) | ✅ |
| P5 | D+C | 2 (EvolutionView, oaa_agent) | ✅ |
| **合计** | **6 类根因** | **8 个文件** | ✅ |

### 执行规则

- 每完成一个子任务 → 测试 → 更新状态文档 → 自动下一子任务
- 每完成一个 Phase → 测试 → 更新状态文档 → 自动下一 Phase
- 循环至全部修复完成

---

## 本次会话（2026-05-25）— 运行时补丁自愈架构规划

### 问题

当前 `self_improve` 工具直接修改 `OAA_ROOT` 下的源码文件以实现自愈。这在开发模式下（git clone 源码运行）能工作，但在交付给用户的 **.exe 安装程序**环境下完全失效：

- 源码被编译进 .exe，`.py` 文件不存在
- `__file__` 指向临时解压目录或 .exe 内部路径，不可写
- `reload_module` 对冻结模块无效
- 安装目录（如 `Program Files`）通常无写权限

### 方案：运行时补丁层

核心思路：**不修改源码文件，而是在内存中覆盖目标函数/类，补丁持久化到数据目录而不是安装目录。**

```
进程启动时
  patch_loader.py
    └─ 扫描 data_dir/patches/*.json
    └─ 对每个补丁：compile() → exec() → setattr()
    └─ 日志记录已加载的补丁

运行时自愈
  agent 诊断问题（与现在一样）
    └─ 生成补丁 JSON（含目标模块/原代码/新代码）
    └─ patch_manager.apply_patch()
         ├─ compile() + exec() 打到内存
         └─ 保存补丁到 data_dir/patches/

发版前
    └─ 清空 data_dir/patches/
    └─ 确认纯源码能正常工作
    └─ 打 .exe
```

### 涉及的新增文件

| 文件 | 说明 |
|------|------|
| `oaa/agent/patch_loader.py` | 启动时扫描 data_dir/patches/ 加载补丁到内存 |
| `oaa/agent/patch_manager.py` | 管理 API：apply/remove/list patches，持久化到 data_dir |
| `oaa/agent/tool_schema.py` | 补丁管理相关工具 schema |
| `oaa/gateway/management.py` | +`_handle_list_patches` / `_handle_remove_patch` |

### 涉及修改的文件

| 文件 | 变更 |
|------|------|
| `oaa/app.py` | 启动时调用 `patch_loader.load_all()` |
| `oaa/agent/extended_tools.py` | `do_self_improve` 改为生成补丁而非改文件；新增 `do_apply_patch` / `do_remove_patch` |
| `oaa/agent/system_rules.py` | 自愈规则改为补丁路径 |
| `gui/src/views/EvolutionView.vue` | 补丁状态展示 |
| `gui/src/composables/useWebSocket.ts` | 补丁推送事件 |

### 补丁 JSON 格式

```json
{
  "id": "patch_1712345678",
  "created_at": "2026-05-25T18:00:00",
  "module": "oaa.gateway.email_config",
  "target": "EmailConfigManager._test_imap",
  "source": "def _test_imap(...):\n    ...",
  "description": "Fix 139邮箱 SSL handshake failure",
  "checksum": "sha256:..."
}
```

### 自愈执行流程

1. 邮箱测试失败 → `_heal_callback` 触发
2. agent 用 `read_own_source` 读 `email_config.py` 定位问题
3. agent 生成补丁（新函数代码）
4. agent 调用 `do_apply_patch` 而非 `self_improve`：
   - 编译新代码 → `setattr(EmailConfigManager, '_test_imap', new_func)` → 即时生效
   - 补丁 JSON 写入 `data_dir/patches/`
   - 清除管理端缓存（`_email_cfg = None`）
5. 下次启动时 `patch_loader.load_all()` 自动重新应用补丁

### 开发/测试一致性

| 阶段 | 补丁系统 | 效果 |
|------|---------|------|
| 日常开发 | 同一套代码 | 补丁和源码两套方案并行，补丁不改源码 |
| 测试自愈 | 故意制造故障 → agent 生成补丁 | 验证自愈通路，补丁存 data_dir |
| 修复确认 | review 补丁内容 → 合并到源码 | 补丁即为修复提案 |
| 发版 | 清空 data_dir/patches/ → 打 .exe | 纯源码编译，无残留 |

### 与现有系统的关系

- `self_improve` **保留**，但角色变为"开发模式下生成补丁"而非直接改文件
- 补丁系统不替代源码修改，而是提供运行时应急手段
- 补丁可以手动清除（`remove_patch`），不影响程序重新安装

---

## 本次会话（2026-05-25 续）— 聊天渲染修复 + 定时任务交付改进

### 修复列表

#### F1: 后端启动崩溃

`extended_tools.py.__init__` 不接受 `image_gen_config` 参数，但 `oaa_agent.py` 传入了该参数，导致 `TypeError`。

**修复**：`extended_tools.py` 添加 `image_gen_config: Any = None` 参数。

#### F2: 聊天区渲染（前端）

**问题**：
1. Agent 输出同时出现在聊天区和工具信息栏（重复）
2. 工具信息栏中出现空内容的聊天图标条目
3. Agent 所有输出集中在一个聊天气泡中

**修复**（`gui/src/composables/useWebSocket.ts`）：
- `llm_output` 不再推入 `workEntries[]`（只进聊天区），消除重复
- 新增 `_bubbleClosed` 追踪变量：`tool_call`/`tool_result` 触发时关闭当前气泡
- 下次 `llm_output` 到来时自动开启新气泡，实现按步骤分段显示
- `done` / `clearWorkEntries` / `send` 时重置气泡状态

#### F3: 定时任务交付格式化

**问题**：任务执行结果在聊天区显示为纯文本，无排版；微信发送失败时无日志。

**修复**（`oaa/app.py — _executor_run`）：
- 任务开始时发送 Markdown 标题 `## 📋 定时任务：{name}`
- 提示 agent 输出结构化 Markdown 报告
- 任务完成后追加 `✅ 任务「{name}」执行完毕` 脚注
- 微信发送增加详细日志（区分 adapter 未认证 和 _bot_user_id 未设置）
- 执行失败时推送 `❌` 错误通知到聊天区

#### F4: 定时任务渠道同步

**问题**：GUI 修改任务的 `channels` 后，`delivery_channels` 未同步。

**修复**（`oaa/gateway/management.py`）：上一会话完成，本次确认已生效。

#### F5: 进程管理

**问题**：多次重启后端导致端口 9765 被旧进程占用（`OSError: [Errno 10048]`）。

**注意**：`taskkill /f /im python.exe` 可能不彻底，需 `netstat -ano | grep 9765` 找到 PID 后用 `os.kill(pid, 9)` 终止。

---

## 本次会话（2026-05-24）— P1/P2/P5 修复 + 工作信息分离 + Harness 架构规划

### P1 — Email 配置前端 + 真实 SMTP 发送 ✅

| 文件 | 改动 |
|------|------|
| `gui/src/views/ConnectionsView.vue` | 邮箱配置卡片区域 + 配置弹窗 + 服务商下拉框 + 保存即测试流程 |
| `oaa/gateway/email_config.py` | **新增** — 14 家邮箱服务商预设 + IMAP/SMTP 验证 + CRUD |
| `oaa/agent/extended_tools.py` | `do_email_send` 从空壳改为真实 SMTP 发送（@agent_tool 显式参数） |
| `oaa/gateway/management.py` | list_email_providers / save_email / test_email / list_emails 四个管理 API |

### P2 — Evolution 空标题误匹配修复 ✅

| 文件 | 改动 |
|------|------|
| `oaa/gateway/management.py` | `_handle_apply_evolution`: 空 skill/message 跳过而非 `"" in title` 永真匹配 |

### P5 — 渠道心跳检测 + 断连通知 ✅

| 文件 | 改动 |
|------|------|
| `oaa/gateway/adapters/wechat_ilink.py` | 新增 `_connected` 标志 + `is_connected` 属性 |
| `oaa/gateway/adapters/dingtalk.py` | 同上 |
| `oaa/gateway/adapters/feishu.py` | 同上 |
| `oaa/gateway/management.py` | `start_healthcheck()` + `_healthcheck_loop()` 30s 间隔线程探测 + 断连推送 GUI/微信 |
| `oaa/app.py` | 适配器注册完成后启动 healthcheck |
| `gui/src/composables/useWebSocket.ts` | 新增 `channelStatusChanged` 计数器 + `channel_disconnected` 事件处理 |
| `gui/src/views/ConnectionsView.vue` | `watch(channelStatusChanged)` 刷新各通道在线状态 |

### P3 — 信息覆盖问题修复（前端展示层） ✅

| 文件 | 改动 |
|------|------|
| `gui/src/composables/useWebSocket.ts` | 新增 `WorkEntry` 类型 + `workEntries` 数组 + `_isErrorMessage()` 错误检测；`llm_output`/`tool_call`/`status`/`tool_result`/`error` 均推入 workEntries；`done` 正常内容进 messages（聊天气泡），错误只进 workEntries |
| `gui/src/components/WorkPanel.vue` | **新增** — 可折叠工作信息面板，按类型显示图标（🔧✅⏳💭❌），auto-expand，自动滚动 |
| `gui/src/views/ChatView.vue` | 移除聊天气泡中 `streamingContent` 渲染；WorkPanel 插入 messages 与 input-area 之间；loading 动画仅在无任何 work entry 时显示 |

### P0 — 渐进式披露（Progressive Disclosure） ✅

| 文件 | 改动 |
|------|------|
| `oaa/agent/oaa_agent.py` | `_build_skill_listing()` 按分类分组展示技能元数据（Level 1），新增 footer 提示 skill_load/skill_unload 用法 |
| `oaa/agent/extended_tools.py` | `do_skill_load()` 返回 summary 预览字段（前200字）；新增 `do_skill_unload()` 卸载技能释放上下文 |
| `oaa/agent/tool_schema.py` | 新增 `skill_unload` 工具 schema |

### P5 — 渠道心跳检测 + 断连通知 (补修) ✅

| 文件 | 改动 |
|------|------|
| `oaa/app.py` | healthcheck 启动从 `__init__` 移到 `start()` 方法，修复 `no running event loop` 启动崩溃 |

### 已知问题

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| 67 | sensenova-u1-fast API Key 无效 | u1-fast 是文生图模型，不支持 chat/completions。新增 `generate_image` 工具通过正确端点调用 | ✅ 已修复 |
| 68 | 模型自动切换不生效 | Fallback 列表跳过同 provider + API key 红化写回磁盘导致回退 Key 无效 | ✅ 已修复 |

## 本次会话（2026-05-24 晚）— Bug #67/#68 修复 + 图片生成工具 + P0 渐进式披露完善 + Harness 三项改进

### Bug #67 — u1-fast 图片生成工具 ✅

### Bug #67 — u1-fast 图片生成工具 ✅

**根因**：`sensenova-u1-fast` 是文生图模型（`POST /v1/images/generations`），作为聊天模型调用 `chat/completions` 始终返回 401。

**修复**：新增 `generate_image` 工具，通过正确端点调用 u1-fast。

| 文件 | 改动 |
|------|------|
| `oaa/agent/extended_tools.py` | 新增 `do_generate_image()` — 调用 OpenAI 兼容图片 API，支持 prompt/size/n 参数，可设置 API Key |
| `oaa/agent/tool_schema.py` | 新增 `generate_image` 工具 schema |
| `oaa/config.py` | 新增 `ImageGenConfig` dataclass（enabled/api_key/base_url/model_id）+ 红化支持 + load/save |
| `oaa/agent/oaa_agent.py` | 传递 `image_gen_config` 到 ExtendedTools |
| `oaa/gateway/management.py` | `_handle_save_config` 增加 `image_gen` 配置保存 + Key 红化解析 |

### Bug #68 — 模型自动回退不生效 ✅

**根因 1**：Fallback 列表构建时 `if prov == active_provider: continue` 跳过整个当前 provider，但同 provider 下可能还有其他可用模型（如 `custom-openai` 下有 `sensenova-6.7-flash-lite` 和 `sensenova-u1-fast`）。

**根因 2**：`sensenova` provider 的 API key 被红化值 `sk-a****kBEe` 写回磁盘，`process_message()` 构建 fallback 时读到无效 Key。

**修复**：

| 文件 | 改动 |
|------|------|
| `oaa/agent/oaa_agent.py` | Fallback 列表遍历所有 provider 的所有 entry，只跳过 exact same (provider, model_id)；自动解析红化 Key |
| `oaa/gateway/management.py` | `_handle_switch_model` 调用 `_resolve_redacted_key()` 解析红化 Key；新增 `_resolve_redacted_key()` 方法（先查活跃模型 key，再查同 model_id 各 provider 条目） |

### P0 — 渐进式披露完善 ✅

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 新增 `SHORT_RULES`（~120 tokens，7 条浓缩规则），`SYSTEM_RULES`（~400 tokens）保留完整版 |
| `oaa/agent/oaa_agent.py` | 系统提示改用 `SHORT_RULES`；对话摘要从 3 条减为 1 条；工具组目录只显示未加载组 |
| `oaa/agent/loop.py` | 401 错误提示补充"可能是该模型不支持聊天"；Fallback 消息显示具体模型名 + 原因判断 |

### 已知问题更新

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| 67 | sensenova-u1-fast API Key 无效 | u1-fast 是文生图模型，不支持 chat/completions。新增 `generate_image` 工具通过正确端点调用 | ✅ 已修复 |
| 68 | 模型自动切换不生效 | Fallback 列表跳过同 provider + API key 红化写回磁盘导致回退 Key 无效 | ✅ 已修复 |

### 待完成改进

Harness 架构审查后采纳的三项优化：

| 模块 | 改进项 | 说明 | 状态 |
|------|--------|------|------|
| P3 产物契约 | 自动验收 Validator | 任务结束后校验产物是否符合验收标准（文件存在性、JSON Schema、关键词匹配），失败则自动标记 | ✅ 已完成 |
| P1 Step Runtime | 步骤依赖声明 | `_plan.md` 步骤行增加 `depends_on` 字段，自动检测文件产物添加验收标准 | ✅ 已完成 |
| P4 规则引擎 | 优先级排序 | `check()` 分两轮扫描：先扫 `deny`（最高优先级），再扫 `require_confirm`/`require_param` | ✅ 已完成 |

### 实施详情

**P3 — 自动验收 Validator**

| 文件 | 改动 |
|------|------|
| `oaa/agent/contract.py` | 新增 `Criterion`/`ValidationResult` dataclass；新增 `add_criterion()`/`add_criteria()`/`validate()`；`finish()` 自动运行验证并写入 `_done.md`；`complete_step()` 自动检测文件产物添加验收标准 |
| `oaa/agent/policy.py` | `check()` 分两轮扫描：deny 优先，再扫 require_confirm/require_param |

**P1 — 步骤依赖声明**

| 文件 | 改动 |
|------|------|
| `oaa/agent/contract.py` | `_plan.md` 步骤表格增加"依赖"列；`add_step()` 接受 `depends_on` 参数；`_step_depends` 字典跟踪步骤依赖关系 |

**P4 — 规则引擎优先级**

| 文件 | 改动 |
|------|------|
| `oaa/agent/policy.py` | `check()` 逻辑从 first-match-wins 改为 deny 优先两轮扫描 |

---

## 本次会话续（2026-05-24 下午）— P1 Step Runtime

### P1 — Step Runtime（步骤化执行引擎） ✅

**核心改动**：每个工具调用成为独立 Step，执行结果回喂 LLM 再决策下一步。

| 文件 | 改动 |
|------|------|
| `oaa/agent/loop.py` | 批量执行 → 逐步骤执行；每条 tool_call 独立 step_id 和 phase(plan/result)；新增耗时跟踪和 inner LLM 回喂；yield 事件携带 step_id/phase/duration |
| `gui/src/composables/useWebSocket.ts` | `WorkEntry` 新增 `step_id`/`phase`/`duration`；tool_call/tool_result/status 转发 step 字段 |
| `gui/src/components/WorkPanel.vue` | `stepGroups` 计算属性按 step_id 分组；Step header 显示序号、耗时、状态(执行中/完成/失败) |

---

## 本次会话（2026-05-23 下午）— 工具分组 + CLI预装 + 自愈强化 + 去重修复

### 工具分组路由 ✅

核心工具（~23个）始终可见，88个专用工具按需加载。LLM 通过 `tool_group_load` 动态加载工具组。

| 文件 | 改动 |
|------|------|
| `oaa/agent/tool_groups.py` | **新增** — 17组/96工具映射 + 分组查询 |
| `oaa/agent/oaa_agent.py` | 分离 `_all_tools_schema` / `_tools_schema`，`load_tool_group`/`unload_tool_group` |
| `oaa/agent/tools.py` | `tool_group_load`/`tool_group_unload`/`tool_group_list` 三个工具 |

### CLI 工具预装 ✅

`cli/` 目录 bundle Node.js 便携版 + wechat-cli/lark-cli/dws。OAA 首次启动自动 `npm install`。零依赖开箱即用。

| 文件 | 改动 |
|------|------|
| `cli/package.json` | **新增** — `@larksuite/cli` + `dws` |
| `cli/.gitignore` | **新增** — 排除 `node_modules/` + `node/` |
| `oaa/init.py` | `ensure_bundled_cli()` 自动检测并安装 |
| `oaa/app.py` | 启动时 prepend bundled Node.js 到 PATH |
| `oaa/gateway/adapters/wechat_cli.py` | bundled CLI 优先查找 + pip wechat-cli 支持 |
| `oaa/gateway/adapters/feishu_cli.py` | bundled CLI 优先查找 |
| `oaa/gateway/adapters/dingtalk_cli.py` | bundled CLI 优先查找 |

### 自愈系统强化 ✅

**搜索→安装→验证 三步流程**写入系统规则和 repair_loop prompt。

| 文件 | 改动 |
|------|------|
| `oaa/agent/system_rules.py` | 规则6升级为通用三步流程 + 规则5b 微信读写分离 |
| `oaa/agent/repair_loop.py` | feed prompt 强制搜索优先 + 试运行校验 + 依赖缺失重试引导 |

### 提案去重修复 ✅

| 文件 | 改动 |
|------|------|
| `oaa/agent/proposal.py` | `has_recent_for_target`(24h窗口) + `dedup_stale_pending`(启动清理过期提案) |
| `oaa/agent/idle_inspector.py` | `_should_skip_proposal` 统一入口 + 稳定 topic key 去重 + 过滤"未初始化"错误 |
| `oaa/app.py` | 启动时 `fix_stale_running` + `dedup_stale_pending` |

### 定时任务调度修复 ✅

**根因**：scheduler 后台循环只标记不执行。修复后 scheduler 直接触发 `_executor_run`。

| 文件 | 改动 |
|------|------|
| `oaa/scheduler/__init__.py` | `set_due_callback` + `start_loop` 中调用回调 |
| `oaa/app.py` | 注册 `_executor_run` 为 `due_callback` |
| `test_coverage_v3.py` | 注入 `TaskScheduler`，修复测试 agent 假失败 |

### 测试结果

```
144 passed, 1 skipped — 无回归
test_self_heal.py — 自愈搜索→安装→验证 闭环验证通过
```

---

## 本次会话（2026-05-23）— B5/B6/B8修复 + N3-N6能力移植 + 工具去重

### B5 — 技能页面"已应用"状态不持久 ✅

**根因**：`SkillView.vue` 中 evolution 建议的 `applied` 状态仅存在 Vue 组件内存中，页面刷新后丢失。

**修复**：`management.py` 的 `_handle_get_evolution` 返回建议时，查询 `stats["applied"]` 已应用列表，匹配到则标记 `s["applied"] = True`。前端刷新后状态不丢失。

| 文件 | 改动 |
|------|------|
| `oaa/gateway/management.py` | _handle_get_evolution 增加 applied 状态合并逻辑 |

### B6 — 微信安装 wechat-cli 后无响应 ✅

**根因**：**应用设计缺陷**（非LLM判断错误）。`WeChatCLI._find_cli()` 硬编码 Linux 路径，无平台检测。对比 `FeishuCLI` 和 `DingTalkCLI` 都有正确的 `sys.platform` 检测。

**修复（三层）**：

| 层 | 文件 | 改动 |
|----|------|------|
| 自知 | `oaa_agent.py` | `build_system_prompt()` 注入操作系统、Python版本、Shell提示（where→Windows, which→Linux/Mac） |
| 查找 | `wechat_cli.py` | `_find_cli()` 重写：where/which → npm prefix → APPDATA/ → 用户路径，4层回退 |
| 闭环 | `tools.py` | `_wechat_cli_call` 每次实时查找二进制，找到后自动回写config |
| 行为 | `system_rules.py` | 新增第6条：安装CLI工具后必须验证→试运行 |

### B8 — 提案自动执行嵌套调用问题 ✅

**根因**：`repair_loop._feed()` 调用 `agent.process_message()`，自愈期间 IdleInspector 可能再次触发生成新提案，形成嵌套循环。

**修复**：

| 文件 | 改动 |
|------|------|
| `idle_inspector.py` | 新增 `pause()`/`resume()`/`is_paused()`，`inspect()`和`_background_loop`检查暂停标志 |
| `repair_loop.py` | `run()` 接受 `inspector` 参数，执行期间 `inspector.pause()`，finally中恢复 |
| `management.py` | Path A (repair_loop) + Path B (ProposalExecutor) 均暂停/恢复巡检 |

### P0 — 线C 日调度巡检 ✅

已确认全部实现，非空壳：
- `_check_memory_health()` — HOT记忆密度检测
- `_check_correction_patterns()` — 重复修正模式检测→`modify_own_prompt`提案
- `_check_disk_usage()` — 磁盘空间>90%告警
- `_self_learn()` — LLM分析技能缺口
- 后台循环每24h自动触发

### P1/P2 — 测试 + 度量 ✅

| 文件 | 改动 |
|------|------|
| `tests/test_idle_integration.py` | **新增** — 14个集成测试（pause/resume、ignore持久化、memory触发、proposal CRUD） |
| `tests/test_management_api.py` | MockInspector 增加 pause/resume 方法 |
| `test_gui_cdp.py` | EvolutionView CDP测试已存在（测试11-14） |
| `oaa/agent/metrics.py` | 度量系统已完善（主动性比率、工具统计、LLM统计） |

**测试结果**：144 passed, 1 skipped

### N3-N6 — 能力移植（参照 autos.aardio） ✅

| 编号 | 工具 | 能力 | 参考autos |
|------|------|------|-----------|
| N3 | `aifix` | 语法检查→自动修复→编译验证，返回 fixed_code + fixes_applied | `aifix` |
| N4 | `code_exec(async_mode=True)` | 后台异步执行，返回 task_id，结果写入 async_results/ | `loadcodex_async` |
| N5 | `download_file` / `github_repo` / `github_content` | URL下载 / GitHub仓库查询 / 文件内容读取 | `download_file` / `github_lookup_repo` / `github_get_content` |
| N6 | `module_index` | 自省查询（list_modules/list_tools/list_config/lookup） | `lookup_library` |

| 文件 | 改动 |
|------|------|
| `tools.py` | +`do_aifix` / +`do_download_file` / +`do_github_repo` / +`do_github_content` / +`do_module_index` / +`_do_code_exec_async` / +`_run_code_exec_sync`（~200行） |
| `tool_schema.py` | 新增 aifix/download_file/github_repo/github_content/module_index 的 @agent_tool 自动生成 |

### 工具去重 — 删除 web_search ✅

`web_search`（百度）与 `ai_search`（Tavily/Exa/AnySearch）功能重叠。实测对比：百度URL全部是跳转链接、结果不相关；Tavily 返回真实URL、描述丰富、精确命中。

| 文件 | 改动 |
|------|------|
| `browser_tools.py` | 删除 `_search_web()` + `do_web_search()` + `BrowserTools.do_web_search()` |
| `tool_schema.py` | 从 BROWSER_TOOLS_SCHEMA 移除 web_search |
| `system_rules.py` `oaa_agent.py` `loop.py` `repair_loop.py` `extended_tools.py` `idle_inspector.py` | 6个文件中所有 `web_search` 引用替换为 `ai_search` |

**工具总数**：104 → **103**

### H-05 — 管理 handler 阻塞 30-120 秒 ✅

**根因**：`_handle_proposal_approve` 中 `await repair_loop.run()` 和 `await executor.execute()` 在 WebSocket handler 内同步等待，阻塞事件循环 30-120 秒。

**修复**：执行逻辑拆分为后台 `asyncio.Task`，handler 立即返回 `{ok: true, status: "running"}`。完成后通过 push notification 广播 `proposal_completed` 到所有 GUI 客户端，EvolutionView 自动刷新。

| 文件 | 改动 |
|------|------|
| `management.py` | +`_notify_callbacks` 推送机制、+`_execute_proposal_bg/_run_repair_bg/_run_executor_bg` 三个静态方法 |
| `desktop.py` | +`_broadcast_push()` 广播方法、`get_metrics` 注册到 `_MANAGEMENT_TYPES` |
| `useWebSocket.ts` | +`proposalCompleted` ref + push notification 处理 |
| `EvolutionView.vue` | 监听 `proposalCompleted` 自动刷新提案列表 |

### 主动性度量可视化 ✅

**根因**：`MetricsCollector` 已收集工具调用/LLM 统计/主动性比率等完整数据，但只在 system prompt 中注入文本，无 GUI 可视化。

**修复**：新增 `get_metrics` 管理 API + EvolutionView 统计页新增「主动性度量」区块（数据卡片、决策分布图、工具成功率排行、LLM 模型统计）。

| 文件 | 改动 |
|------|------|
| `management.py` | +`_handle_get_metrics` handler + `get_metrics` 注册到 VALID_TYPES |
| `desktop.py` | `get_metrics` 注册到 `_MANAGEMENT_TYPES` |
| `EvolutionView.vue` | +`metricsData`/`decisionBreakdown`/`toolBreakdown` + `loadMetrics()` |

### 测试结果

```
144 passed, 1 skipped — 无回归
```

### N7 — 自然语言定时任务调度 ✅

用户通过自然语言描述周期性需求 → agent 自动解析 → 创建定时任务 → 到时间自动执行 → 多渠道交付结果。

**核心流程**：
```
用户："每天早上9点整理当天热度排名前五的科技新闻"
  → agent 追问（内容/时间/渠道/格式）
  → schedule_create(name, execution_prompt, cycle, start_hour, delivery_channels)
  → 第二天 9:00 → LineD 检测到期 → _executor_run 自动执行
  → wechat_send_text + 聊天页面输出
```

**变更清单**：

| 文件 | 改动 | 说明 |
|------|------|------|
| `scheduler/__init__.py` | +`execution_prompt` +`delivery_channels` 字段 | 任务带"做什么"的 prompt + "发到哪"的渠道 |
| `tools.py` | +`schedule_create/list/update/delete/run` (5 个 @agent_tool) | agent 可调用的定时任务 CRUD |
| `idle_inspector.py` | LineD 改双路径 + `_executor_callback` | 有 execution_prompt → 自动执行；无 → 保持提案模式 |
| `app.py` | +`_executor_run` 回调注册 | agent.process_message(prompt) → 微信/聊天页交付 |
| `system_rules.py` | +第 7 条规则 | 创建定时任务前必须确认内容/时间/渠道/格式 |
| `oaa_agent.py` | +`self.atomic.set_scheduler()` | 注入 scheduler 到 AtomicTools |

**设计要点**：
- 旧式任务（无 execution_prompt）仍然走提案模式，向后兼容
- `delivery_channels` 默认 `["chat", "wechat"]`
- `schedule_run(id)` 支持手动立即触发，不等定时
- scheduler 和 agent 共享同一 TaskScheduler 实例，GUI 任务页面可直接看到 agent 创建的任务

---

## 本次会话（2026-05-22）— 自愈系统投喂+验收架构

### 背景

当前自愈系统（IdleInspector → Proposal → ProposalExecutor）使用**固定模板**生成修复步骤（读代码→self_improve→重载模块），执行后只做语法检查，不做功能验证，无法确认修复是否真正解决了问题。`wechat_contacts` 失败5次的案例暴露了根本缺陷：提案标记为"完成"但工具仍然无法使用。

### 新架构：投喂+验收包装层

**核心思路**：自愈和普通任务是同一套能力——agent 已经会分析根因、选工具、执行、判断结果。唯一的区别是自愈的输出可能会修改自身代码/配置/依赖。所以不需要独立的"修复器"，只需要一个**投喂+验收**的包装层，复用 agent 已有的全部能力。

**流程图：**
```
巡检发现问题
  ↓
创建 Proposal（含 problem_context）
  ↓
用户审批
  ↓
repair_loop.run(context, agent):
  ├─ 第1次尝试
  │   ├─ 投喂: 【自愈任务】prompt → agent.process_message()
  │   ├─ agent 用自己的能力修复
  │   └─ 独立验证: 重新检查原始问题是否解决
  │       ├─ 通过 → 汇报结果
  │       └─ 失败 → 第2次投喂（带失败历史）
  ├─ 第2次尝试
  │   └─ ...
  ├─ 第3次尝试
  │   └─ 失败 → 回滚所有变更 → 升级给人
  └─ 结束
```

### 变更清单

| 文件 | 改动 | 说明 |
|------|------|------|
| 新增 `oaa/agent/repair_loop.py` | ~200 行 | RepairPlan dataclass + RepairLoop（投喂+验证+重试循环+回滚） |
| `oaa/agent/idle_inspector.py` | ~30 行 | `_check_tool_failures()` 从生成固定 actions 改为生成 `problem_context` |
| `oaa/agent/proposal.py` | ~10 行 | Proposal 增加 `problem_context` 字段（actions 变为可选） |
| `oaa/gateway/management.py` | ~40 行 | `proposal_approve` 有 problem_context 时走 repair_loop，否则走 ProposalExecutor |
| `oaa/agent/tools.py` | ~40 行 | 新增 `_record_rollback_entry()` 写入 rollback_manifest.json |

### 验证机制

外层独立验证——重新检查原始的巡检条件：
- 工具失败 → 重新调用工具检查返回值
- 修正模式 → 重新查 correction 列表

agent 在修复过程中自己也会做验证（比如"装好了 wechat-cli，试一下能不能查到联系人"），这是 agent 内部的验证。外层验证是独立的二次确认。

### 回滚机制

现有的 `self_improve` 已有文件备份（`data_dir/backups/`），但缺少统一注册表。新增 `rollback_manifest.json` 跟踪所有自愈修改：

```json
{
  "prop_xxx": {
    "changed_files": [
      {"path": "oaa/agent/extended_tools.py", "backup": "backups/extended_tools.bak"}
    ],
    "installed_packages": ["wechat-cli"],
    "config_changes": [{"key": "wechat.enabled", "old": false, "new": true}]
  }
}
```

重试3次全部失败时 → 遍历 manifest 回滚所有变更 → 恢复原始状态。

### 实现状态

| # | 文件 | 状态 |
|---|------|------|
| 1 | `oaa/agent/repair_loop.py` | ✅ 已完成 |
| 2 | `oaa/agent/idle_inspector.py` | ✅ 已完成 |
| 3 | `oaa/agent/proposal.py` | ✅ 已完成 |
| 4 | `oaa/gateway/management.py` | ✅ 已完成 |
| 5 | `oaa/agent/tools.py` | ✅ 已完成 |

### 预测审查 & 修复

5 人专家组（架构师、安全、性能、可靠性、魔鬼代言人）对自愈系统代码进行了多轮辩论审查，产出 8 项发现，已修复 5 项：

| # | 发现 | 严重度 | 状态 |
|---|------|--------|------|
| H-01 | 回滚清单 key 写死 `_tool_level` → 回滚完全不生效 | 致命 | ✅ contextvars 线程传递 proposal_id |
| H-02 | ~~agent 不能修复非代码故障~~ → **修正：不是能力边界，是判断质量** | — | ⚠️ 见下方说明 |
| H-03 | `_tool_failure_verifier` 永远返回 True | 中等 | ✅ 改为 MemoryManager 真实检查 |
| H-04 | 缺失验证器时静默通过 | 中等 | ✅ 改为返回 False |
| H-05 | 管理 handler 阻塞 30-120 秒 | 中等 | ✅ 后台 asyncio.Task + push notification |
| H-06 | `_feed` 无超时保护 | 中等 | ✅ `asyncio.wait_for` 300s |
| H-07 | Proposal 双模式无校验 | 中等 | ✅ `__post_init__` 验证 |
| H-08 | 独立验证仍是空壳 | 中等 | ⚠️ H-03 修复后已有改善 |

#### H-02 修正说明

**原始预测**（错的）：agent 的能力有边界，某些故障类型（缺少二进制、跨平台不兼容）无法修复。

**纠正**：这不是能力边界问题，是 agent 的**判断质量和努力程度**问题：
- `wechat_contacts` 失败是因为 agent 选错了方案（应该用 iLink 替代，而不是修 wechat-cli），不是装不了
- 所有 CLI 工具都是面向 AI 设计的，agent 可以用 `shell_run` 安装
- 跨平台不是问题，但 agent 需要正确识别自己运行的平台
- 需要用户注册/提供 key 的服务，agent 应向用户说明需求并完成后续操作

**对实现的影响**：
- `repair_loop._build_feed_prompt` 约束段已重写，强化「先尽其所能，再请求协作」原则
- 重试 prompt 按失败类型注入针对性引导（dependency_missing → 提示安装路径；method_error → 提示换方案）
- 约束 agent 不能把所有问题抛回给用户

### 全功能测试 v2 — 20 场景，16/16 通过

2026-05-22 对应用进行第二轮全功能测试，覆盖 agent 循环、文件操作、代码执行、路径解析、网络搜索、创意解决问题（做图+PPT）、多轮对话、状态感知等 16 个验证点，**全部通过**。

#### 测试通过列表

| # | 测试 | 结果 | 耗时 | 工具链 |
|---|------|------|------|--------|
| 1 | 基础对话 | ✅ | 4.1s | 直接回复 |
| 2 | 文件创建+读取+追加 | ✅ | 31.3s | file_write → file_read → file_patch |
| 3-5 | code_exec（exec+sandbox+计算） | ✅ | 9.3-13.0s | code_exec + file_write |
| 6 | shell_run 命令 | ✅ | 25.0s | shell_run |
| 7 | 目录遍历+文件搜索 | ✅ | 8.9s | list_own_structure + file_glob |
| 8 | 模块路径解析 | ✅ | 23.5s | read_own_source 正确解析 `oaa.agent.loop` |
| 10 | 记忆操作 | ✅ | 4.8s | update_working_checkpoint |
| 11 | 网络搜索 | ✅ | 21.8s | ai_search |
| 12 | 跨工具链统计 | ✅ | 15.0s | shell_run → file_write |
| **13** | **创意做图** | ✅ | 301s | read_own_source×4 → code_exec → shell_run(pip) → code_exec |
| **14** | **PPT 生成** | ✅ | 89s | skill_search → skill_install → skill_load → code_exec |
| 16 | 多轮对话上下文 | ✅ | 3.5s | 记住并正确回忆用户姓名 |
| 20 | 系统状态感知 | ✅ | 9.0s | 直接回复 |

#### 测试产物

| 文件 | 大小 | 说明 |
|------|------|------|
| `oaa_v2_architecture.png` | 125KB / 2385×1785 | agent 自行安装 matplotlib 生成的架构图 |
| `oaa_intro.pptx` | 35KB / 5 页 | agent 搜索安装 create-pptx skill 后生成 |
| sum.txt / time.txt / power.txt / file_stats.md / test_oaa.md | — | 各测试用例的产物 |

#### 修复验证

| 修复 | 验证方法 | 结果 |
|------|---------|------|
| `code_exec` 参数解析 （B1） | code_exec 调用 5+ 次未出现 "file not found: 15" | ✅ |
| 模块路径解析 （B2） | `oaa.agent.loop` → `oaa/agent/loop.py` | ✅ |
| 跨任务状态污染 （B4） | 独立连接测试，每轮正常执行无跳过 | ✅ |

---

## 待处理问题

| 优先级 | 类型 | 项目 | 说明 | 状态 |
|--------|------|------|------|------|
| P0 | 架构 | 渐进式披露（Progressive Disclosure） | 技能信息分三层加载，不一次性塞满上下文 | ✅ 已完成 |
| P2 | 架构 | 技能插件化 | 技能绑定独立工具集、身份、规则，加载时注入 | ✅ 已完成 |
| P0 | 自愈 | 优化自愈/自进化系统 | 自愈闭环断点修复 + 进化引擎深度集成 + 6 项自进化能力评估完成 | 🔄 下一阶段：混合克隆优先策略（见下方详细方案） |
| P3 | 架构 | WorkerAgent 双倍资源 | 原始设计为"干活 agent + 聊天 agent"分离 | ⏸️ 保留现有设计，未完全实现/未启用，待下一代升级 |
| P3 | 通道 | DingTalk OAuth 轮询 | 前端 polls poll_qr，OAA 无需后台轮询 | ✅ 前端已实现 |
| P4 | GUI | 聊天气泡富媒体展示与交互 | 三项合并统一实现：```oaa-actions 按钮 + ```oaa-chart 图表预览（无下载），agent 通过 fenced block 驱动 | ✅ 2026-05-27 |

---

## 本次会话（2026-05-21 续）— 空闲巡检4线架构 + 技能市场 + WeChatCLI 实装

### 空闲巡检新架构 ✅ 已实现

巡检拆为四条独立线，互不干扰：

**线A — 后台循环，每 30 分钟** ✅
- 通道健康、内存占用
- 轻量检查，不涉及 LLM 调用，发现异常直接写提案
- `inspect()` 方法改造完成，仅保留 channel_health + memory_usage + due_tasks
- `_INSPECTION_COOLDOWN = 1800`（30分钟）

**线B — 任务后触发（对话完成 + 空闲 ≥15 分钟）** ✅
- 新增 `_last_activity_time` / `record_task_context()` 追踪最后活跃时间
- 新增 `inspect_line_b()` 方法，只检查本次任务用到的工具（`tool_filter`）和技能（`skill_filter`）
- `_background_loop` 每次循环检查空闲 ≥15 分钟后触发线B
- `process_message` 完成后记录任务上下文

**线C — 日调度（低谷时段）** ⏳ 待实现
- 记忆健康、修正模式
- 自主学技能（逛 GitHub/clawhub 找更好的技能/插件）
- 长周期、重任务，涉及 LLM 调用
- 需要单独的 cron 调度机制

**线D — 即时执行** ✅
- 定时任务到期 → 自动执行，汇报结果，无需请示
- `_check_due_tasks()` 仍在 `inspect()` 中保留

**移除：**
- 磁盘用量 → 改每周一次 ✅（从 `inspect()` 移除，保留在 `_inspect_all_phases()` 启动扫描中）

### WeChat CLI 存根 → 真实调用 ✅

| 文件 | 变更 |
|------|------|
| `oaa/agent/tools.py` | 5 个 `do_wechat_*` 存根改为通过 `_wechat_cli_call()` 代理到 `gateway.adapters.wechat_cli.WeChatCLI`，自动发现二进制，`FileNotFoundError` 如实报错 |
| `oaa/agent/oaa_agent.py` | 从 `config.wechat.wechat_cli_path` 传入路径到 `AtomicTools.set_wechat_cli_path()` |
| `oaa/agent/idle_inspector.py` | 从 `_STUB_TOOLS` 移除 wechat 工具，自愈系统可检测其失败 |

### 技能市场搜索 & 安装 ✅

**之前**：`skill_search` 和 `skill_install` 是存根且未注册 schema，LLM 不可见，死代码。

**之后**：

| 文件 | 变更 |
|------|------|
| `oaa/agent/tool_schema.py` | 在 `EXTENDED_TOOLS_SCHEMA` 注册 `skill_search`（query → ClawHub `/api/v1/search`）和 `skill_install`（slug → resolve → download → extract） |
| `oaa/agent/extended_tools.py` | `do_skill_search` 调用 ClawHub API `/api/v1/search?q=...`；`do_skill_install` 调用 `/api/v1/resolve` 获取下载 URL → `requests` 下载 → 自动识别 zip/tar.gz/SKILL.md → 解压到 `skills/community/` |
| `oaa/agent/oaa_agent.py` | 系统提示词「可用技能」段更新：没有匹配技能时先搜 ClawHub/GitHub，再自己创建，不问用户 |

### 通知文本统一
- 所有巡检通知末尾改为 `回复「确认」执行 / 「忽略」跳过（24h 内有效）`
- 去重 key 改用稳定标识符（emoji + 主题名），不再因失败次数变化而重复推送
- `_store_proposal()` 返回 bool，`inspect()` 检查返回值实现真正抑制

### 提示词优化
- 系统提示中提案路由规则改为：说明提案内容 + 等待用户确认，不擅自操作
- 用户回复确认/否定 → 路由到 proposal_approve / proposal_ignore
- 去除"不需要先问用户"等负面行为描述，只保留触发→动作映射

### 启动优化
- 新增 `_inspect_all_phases()`，后台启动时立即全量扫描一轮
- 所有阶段独立运行，单个失败不影响其余

### 配置文件保存按钮
- SettingsView.vue 模型配置区新增"保存模型配置"按钮（此前 saveModel 函数无按钮绑定）

---

### 背景

EvolutionEngine / ProposalStore / PermissionsManager / Config 等持久化模块使用同步 `json.dump`/`json.load` 写入文件，运行在主协程中阻塞事件循环。高频使用（如 IdleInspector 循环创建提案、每轮 process_message 记录轨迹）下累积延迟影响 LLM 调用响应。

### 变更

| 文件 | 变更 | 说明 |
|------|------|------|
| `oaa/async_io.py` | **新增** | 集中式 async 文件 I/O 工具：`async_write`/`async_read`/`async_write_json`/`async_read_json`，均通过 `loop.run_in_executor` 委派到线程池 |
| `oaa/evolution/engine.py` | 4 方法 → async | `_save_stats`, `analyze_for_suggestions`, `accept_suggestion`, `crystallize_skill` |
| `oaa/agent/proposal.py` | 3 方法 → async | `ProposalStore._save`, `add`, `update_status` |
| `oaa/auth/permissions.py` | 5 方法 → async | `_save_trust`, `record_tool_success/failure`, `reset_trust`, `add_blacklist_path` |
| `oaa/agent/idle_inspector.py` | 6 方法 → async | `_save_dedup_tracker`, `_store_proposal`, `inspect`, `_check_usage_patterns`, `_check_tool_failures`, `_check_correction_patterns` |
| `oaa/config.py` | `save()` → async | 保留 `_save_sync()` 供 CLI 向导 |
| `oaa/gateway/management.py` | 6 handler → async | `_handle_save_config`, `_handle_switch_model`, `_handle_apply_evolution`, `_handle_get_evolution`, `_handle_proposal_ignore`, `_inject_proposal_result` |
| `oaa/agent/tools.py` | +await ×4 | `record_tool_success`, `update_status`, `add_to_hot` ×2 |
| `oaa/agent/oaa_agent.py` | +await ×4 | `inspect`, `record_trajectory`, `record_skill_usage`, `add_to_hot` |
| `oaa/agent/loop.py` | +await ×1 | `add_to_hot` |
| `tests/test_evolution.py` | +`@pytest.mark.asyncio` + `await` | 适配 async EvolutionEngine |
| `tests/test_proposal.py` | 全量 async 化 | 14 个测试全部改为 async |
| `test_components.py` | `asyncio.run()` 包装 | 适配 async evolution/memory 调用 |

### Bug 修复

- **`async_write_json` kwargs 未转发**：`run_in_executor` 不支持 **kwargs，通过 lambda 闭包绑定修复

### 同步保留的方法

- `idle_inspector._save_ignore_list` / `ignore_tool` — 小数据量、被 sync 上下文调用
- `EvolutionEngine._load_stats` / `_get_trajectories_for_skill` — 仅 `__init__` 调用
- `ProposalStore._load` — 仅 `__init__` 调用
- `PermissionsManager._load_trust` — 仅 `__init__` 调用
- `config._save_sync` — CLI 向导专用

### 测试结果

| 测试 | 结果 |
|------|------|
| 全部 78 项 pytest | ✅ 通过（1 skipped，0 regression） |
| `test_evolution.py` | ✅ PASS（无 `_save_stats` 未 await 警告） |
| `test_proposal.py` 14 项 | ✅ 全部 PASS |

---

## 本次会话（2026-05-20）— 进化工厂 Layer 2 (GUI) + Layer 3 (验证回滚)

### Layer 2：进化工厂 GUI 页面

| 文件 | 变更 | 说明 |
|------|------|------|
| `gui/src/views/EvolutionView.vue` | **新增** 292 行 | 进化工厂全功能页面：双标签页（待处理提案 / 执行历史）、提案卡片、批准/忽略按钮、Toast 通知 |
| `gui/src/App.vue` | +2 行 | 注册 EvolutionView 到 tabComponents |
| `gui/src/components/Sidebar.vue` | +8 行 | 导航新增"进化工厂"项（id: evolution，位于 tasks 与 files 之间） |

**EvolutionView.vue 结构：**

- **待处理提案标签页**：加载中 spinner → 空状态（带 SVG 图标 + 提示文字）→ 提案卡片列表
  - 卡片头部：类型 Badge（tool_fix/install_dep/sop_optimize/skill_crystallize/config_change → 中文化 + 彩色徽章）、标题、ID
  - 卡片正文：问题描述（problem）、收益（benefit，绿色文字）、操作步骤（有序列表，tool 高亮 + 可选 verify 显示）
  - 卡片底部：三个按钮 — 「批准执行」（主色，执行中显示「执行中...」+ 禁用）、「忽略本次」（次要）、「彻底忽略」（灰色悬停变红）
- **执行历史标签页**：加载中 spinner → 空状态 → 历史卡片
  - 卡片头部：状态 Badge（已完成/失败/已忽略/已回滚）、标题、执行时间
  - 可折叠执行结果 JSON（格式化显示）
  - 错误信息（红色高亮）
- **Toast 通知**：固定底部右下角，3 秒自动消失，success（绿色）/ error（红色）

**API 集成：**
- `sendRequest('list_proposals')` → 加载全部提案，按 `created_at` 降序排列
- `sendRequest('proposal_approve', { id })` → 批准执行，完成后刷新列表
- `sendRequest('proposal_ignore', { id, permanent })` → 忽略（临时/永久），完成后刷新

### Layer 3：验证与回滚

| 文件 | 变更 | 说明 |
|------|------|------|
| `oaa/agent/proposal.py` | `ProposalExecutor.execute()` 重写 | 每步 action 支持可选 `verify` + `rollback` 字段 |
| `oaa/agent/idle_inspector.py` | `_check_tool_failures` 增强 | 工具修复提案的 reload_module 步骤添加 verify |
| `oaa/gateway/management.py` | +3 handler | `list_proposals`、`proposal_approve`、`proposal_ignore` |
| `oaa/gateway/adapters/desktop.py` | +3 行 | `_MANAGEMENT_TYPES` 同步增加 |

**ProposalExecutor 执行流程（提案级）：**

```
对 actions 列表中每个 action:
  1. handler.dispatch(tool, args) → 记录结果
  2. 如果定义了 verify:
     a. handler.dispatch(verify.tool, verify.args)
     b. 成功 → step.verified = True
     c. 失败 → step.verified = False
        → 如果定义了 rollback:
          - handler.dispatch(rollback.tool, rollback.args)
          - 成功 → step.rollback = "success"
          - 失败 → step.rollback = "failed: {error}"
        → 提案状态设为 failed, 记录错误, 立即返回
  3. 任意 action 抛异常 → step.status = "error" → 提案设为 failed
全部完成 → 提案 status = done, recorded executed_at
```

**verify 字段结构**（每个 action 可选）：
```python
{
  "tool": "code_exec",
  "args": {"code": "import ...; print('reload ok')"},
  "description": "验证模块重载成功"
}
```

**rollback 字段结构**（每个 action 可选）：
```python
{
  "tool": "self_improve",
  "args": {"path": "...", "old_content": "...", "new_content": "..."},
  "description": "回滚文件修改"
}
```

**IdleInspector 集成**：`_check_tool_failures()` 中工具修复提案的 reload_module 步骤自带 verify：`{tool: "code_exec", args: {code: "import <模块>; print('reload ok')"}}`，验证模块加载无报错。

**Management API 端点：**
- `list_proposals(status?)` → `{ proposals[], count }`
- `proposal_approve(id)` → 异步执行，`{ proposal_id, proposal_status, result?, error? }`
- `proposal_ignore(id, permanent=false)` → `{ proposal_id, status: ignored_once/ignored_forever }`

### 已提交

```
8712658 feat: evolution factory layer 2+3 — GUI page + verification & rollback
7 files changed, 765 insertions, 17 deletions
```

### 测试结果

| 测试 | 结果 | 说明 |
|------|------|------|
| GUI 页面加载 | ✅ | EvolutionView 双标签页正常渲染 |
| 提案列表加载 | ✅ | list_proposals 返回完整提案列表 |
| 提案批准执行 | ✅ | ProposalExecutor 按 action 序列执行 |
| 验证成功 | ✅ | verify 通过后标记 verified=True |
| 验证失败+回滚 | ✅ | verify 失败后自动执行 rollback，标记 failed |
| 忽略提案（临时/永久） | ✅ | 状态变为 ignored_once/ignored_forever |
| 空状态显示 | ✅ | 无提案时显示空状态占位 |
| 后端管理 API 端点 | ✅ | list_proposals/proposal_approve/proposal_ignore 全部正常响应 |
| 前端构建 | ✅ | npm run build 无错误 |

---

### 下一步计划

#### 1. 进化工厂统计标签页（第三 Tab） — ✅ 已完成 (2026-05-21)

EvolutionView 新增「统计」标签页：

| 需求 | 状态 |
|------|------|
| 数据卡片（总提案数/成功率/待处理数/回滚次数） | ✅ |
| SVG 环形图（提案类型分布） | ✅ |
| SVG 柱状图（每日执行趋势，成功/失败对比） | ✅ |
| 技能使用排行 | ✅ |
| 已固化技能列表 | ✅ |
| 后端 `get_evolution_stats` API | ✅ |
| `VALID_TYPES` + `_MANAGEMENT_TYPES` 注册 | ✅ |

**变更文件**：`oaa/gateway/management.py`（+`_handle_get_evolution_stats`）、`oaa/gateway/adapters/desktop.py`（+`get_evolution_stats`）、`gui/src/views/EvolutionView.vue`（+stats tab template + script + CSS）


#### 2. IdleInspector 巡检增强 — ✅ 已完成 (2026-05-21)

| 场景 | 实现 |
|------|------|
| 磁盘空间 | ✅ `_check_disk_usage()` — `shutil.disk_usage()` 检查数据目录，>90% 报警 |
| 通道健康 | ✅ `_check_channel_health()` — 检查各 adapter 的 `is_authenticated`/`_running` 状态和错误计数 |
| 内存占用 | ✅ `_check_memory_usage()` — `psutil.Process().memory_info().rss`，>500MB 报警 |
| LLM 调用统计 | 延后（P2，需 LLMClient 增加 stats 追踪能力） |

**变更文件**：`oaa/agent/idle_inspector.py`（+4 项检查方法 + constructor 扩展 + inspect() 接入）、`oaa/agent/oaa_agent.py`（传递 llm + channel_adapters 依赖）

#### 3. 测试计划 — ✅ P0 已完成 (2026-05-21)

| 测试 | 内容 | 状态 |
|------|------|------|
| Layer 3 verify/rollback 单元测试 | verify 失败 → rollback 执行 → 提案标记 failed | ✅ 14 tests, all PASS |
| Layer 3 verify 通过 → 正常完成 | verify 成功 → 提案正常 done | ✅ |
| 提案 store 持久化测试 | 创建/读取/更新/删除/跨实例持久化 JSON | ✅ |
| EvolutionView GUI CDP 测试 | 页面加载、标签切换、按钮点击、Toast | ⏳ P1 待完成 |
| 管理 API 边界测试 | 无效 ID、重复操作、非法 status | ⏳ P1 待完成 |
| IdleInspector 背景巡检集成测试 | 检测 → 创建提案 → GUI 可见 → 批准执行 | ⏳ P1 待完成 |

**变更文件**：`tests/test_proposal.py`（新增，14 个测试，覆盖 ProposalStore CRUD + ProposalExecutor verify/rollback 全流程）

#### 4. Proposal 执行结果的 agent 反馈回路 — ✅ 已完成 (2026-05-21)

**问题**：GUI 批准执行后结果未注入 agent 上下文 → agent 不知道提案已执行、无法学习、可能重复提案。

**修复**：`_handle_proposal_approve` 和 `_handle_proposal_ignore` 执行后调用 `_inject_proposal_result()`，将执行结果写入 MemoryManager.add_to_hot()。agent 下轮对话的 system prompt 中即可看到执行状态。

**变更文件**：`oaa/gateway/management.py`（+`_inject_proposal_result` 方法，在 approve/ignore 后调用）

#### 5. A2 Agent 自主性改造 — 剩余工作

A1（聊天历史持久化）✅ 已完成。A2 大部分核心工具已就位，但 agent 的实际主动性行为仍有差距：

| 剩余项 | 说明 | 优先级 | 状态 |
|--------|------|--------|------|
| Tool call 确认频率细粒度策略 | Permissions 增加按工具信任计数，confirm 模式下已信任的非危险操作自动跳过确认 | P1 | ✅ |
| Agent 自主性行为强化 | System prompt 增加 5 个 few-shot 示例（修复代码/执行提案/数据处理/环境修复/依赖安装），强化主动行为模式 | P1 | ✅ |
| 自修改循环 E2E 测试 | 8 个测试覆盖 self_improve（修改/备份/验证回滚/语法检查回滚）+ reload_module + rollback_change 全闭环 | P1 | ✅ |
| 主动性度量指标 | 缺少量化 agent 主动行为的数据（主动调工具次数/主动修复次数 vs 被动等待确认次数），无法评估改进效果 | P2 | ✅ API + GUI 可视化 |

---

## 本次会话（2026-05-20）— 自愈闭环断点修复 + 自主性分析

## 本次会话（2026-05-19 续）— 微信文件发送 + IdleInspector 增强

### 微信文件发送（wechat_send_file）

| 文件 | 变更 | 说明 |
|------|------|------|
| `wechat_ilink.py` | +`send_file()` 方法 | AES-128-ECB 加密 → CDN 上传 → build_media_message → send_message 完整链路。自动识别图片/视频/音频/文件类型 |
| `extended_tools.py` | +`do_wechat_send_file` | 权限确认 → 调用 adapter.send_file() |
| `tool_schema.py` | +`wechat_send_file` schema | 参数：to(wxid) + file_path(本地路径) |

**上传流程**：读取文件 → 生成随机 AES-128 密钥 → 加密文件 → iLink API 获取 CDN 上传地址 → 上传加密数据 → 构建媒体消息 → 发送到微信。

### read_own_source 目录友好处理

| 文件 | 变更 | 说明 |
|------|------|------|
| `tools.py:858` | `os.path.isfile` → `os.path.exists` + `os.path.isdir` 分支 | 传入目录时自动列出内容（文件大小/子目录），不再报"File not found" |
| `tools.py` | +auto correction_log | 收到目录时自动记修正：`read_own_source 只能读文件，浏览目录请用 list_own_structure` |

**根因**：二愣多次将目录路径传给 `read_own_source` 导致报错，IdleInspector 误判为工具 bug。现在目录路径也能正常返回内容列表。

### IdleInspector 提案去重（Task #11）

| 文件 | 变更 | 说明 |
|------|------|------|
| `idle_inspector.py` | +MD5 哈希 + `_MAX_PROPOSAL_REPEATS=3` | 同一提案最多推送 3 次，之后静默抑制 |
| `idle_inspector.py` | +`_dedup_path` + JSON 持久化 | 重启后依然记得已发次数，不重复推送 |

### IdleInspector 使用模式分析（Task #12）

| 文件 | 变更 | 说明 |
|------|------|------|
| `idle_inspector.py` | +`_check_usage_patterns()` | Phase 2b 分析：① 技能使用≥5次未结晶 → 建议触发 ② SOP 跳过≥3次 → 建议移除 ③ 新结晶技能通知 ④ 工具累计失败≥3次 → 建议修复 |

### 巡检通知微信推送

| 文件 | 变更 | 说明 |
|------|------|------|
| `app.py` | +`_inspector_notify` 微信推送 | IdleInspector 发现优化项时，除 GUI 通知外，同时通过微信推送给机器人主人（`_bot_user_id`）。用户回复"确认"后 agent 自动执行 |

**流程**：IdleInspector 检测到优化项 → 写入 `pending_proposals.md` → GUI 通知 + **微信消息推送** → 用户手机回复确认 → agent 处理消息时看到待处理提案 → 自动执行。

### 工具失败记录清理

| 文件 | 变更 | 说明 |
|------|------|------|
| `tool_failures.md` | 清除旧记录 | `read_own_source` 的目录误报记录已清空，避免 IdleInspector 重复触发 |

---

## 本次会话（2026-05-18 深夜）— 自主功能综合测试 + ModuleNotFound 自动恢复

### Evolution Stats 数据格式修复

| 文件 | 变更 | 说明 |
|------|------|------|
| `engine.py` | +defensive `isinstance` 检查 | `analyze_for_suggestions()` 和 `get_auto_refinements()` 中 `sop_skips` 值遍历前检查类型 — 防止 seed 数据格式错误导致 `AttributeError: 'int' object has no attribute 'items'` |
| `engine.py` | +debug logging | `_load_stats()` 增加日志输出加载的 skill_usage 列表 |
| `management.py` | 恢复 `skill_usage` 变量 | `_handle_get_evolution` 中 `skill_usage` 变量被意外删除导致 `NameError` — 已恢复 |

**根因**：seed 数据中 `sop_skips` 值为 `{"search-executor": 2}`（int），但迭代代码期望 `dict`（`.items()`）。修复包含 seed 格式修正（→ `{"search-executor": {"web_search_failed": 2}}`）+ 防御性检查。

### ModuleNotFound 自动恢复

| 文件 | 变更 | 说明 |
|------|------|------|
| `loop.py` | `import re` + 模块检测 regex | `code_run`/`code_exec` 返回错误时，扫描 stdout/stderr 中 `ModuleNotFoundError`/`ImportError` 模式，提取缺失模块名 |
| `loop.py` | +pip install recovery hint | 检测到缺失模块后注入 `[恢复提示]`：`请用 shell_run 命令安装：pip install <模块>` |
| `loop.py` | 错误格式增强 | code 工具错误时显示 stdout/stderr 最后 8 行（traceback tail），使 LLM 能看见完整错误上下文 |

**架构说明**：`_recovery_hint` 机制原本只对**抛出异常**的工具生效（line 249-257），但 `code_run`/`code_exec` 通过子进程 exit code 返回错误（不抛异常）。本次修复在异常路径之外增加了对错误结果内容的**事后扫描**，覆盖了子进程类工具的恢复盲区。

### 自主功能综合测试结果

| 场景 | 测试内容 | 结果 |
|------|---------|------|
| A | 多步骤任务执行（基准） | ✅ 4 种工具，PASS |
| B | 工具失败自动恢复 | ✅ 替代方案执行，PASS |
| C | 技能使用 → 进化阈值触发 | ✅ 6 条轨迹，PASS |
| D | IdleInspector 空闲巡检 | ⚠️ 部分通过（有 LLM 输出但未完全匹配优化关键词） |
| E | 任务中断与端点重续 | ✅ 恢复后继续执行，PASS |
| F | self_improve 代码自修改 | ✅ 规则写入 + reload 通过 |
| **合计** | **26/28 通过 (92%)** | 2 FAIL：A 工具错误数（4 个） + D 巡检提案格式 |

### A 场景工具错误分析

场景 A 的 4 个 `code_exec`/`code_run` 错误来自读取 Excel 文件时的路径问题（agent 使用 `E:/GenericAgent/data/workspace/...` 而非 `E:/GenericAgent/data/workspace/...` 的短路径变体）。ModuleNotFound 修复后这些错误应自动触发 pip install 恢复提示，预期可转为 PASS。

---

## 本次会话（2026-05-20）— 自愈闭环断点修复 + 自主性分析

### 关键 Bug：`_do_reload` 缺失 await（自愈不生效的根因）

`oaa/agent/tools.py:672`：

```python
# 修复前：
reload_msg = self._do_reload(rel_path)  # ← 创建协程对象但从不执行

# 修复后：
reload_msg = await self._do_reload(rel_path)  # ← 实际执行模块重载
```

| 影响 | 严重度 |
|------|--------|
| `self_improve` 修改代码后模块**从未重载**，进程继续用旧代码 | **P0（自愈完全失效）** |
| `rollback_change` 回滚后也**未重载**，进程继续用旧代码 | **P0（回滚完全失效）** |

**修复**：
| 文件 | 行 | 变更 |
|------|----|------|
| `tools.py` | 672 | `self._do_reload` → `await self._do_reload` |
| `tools.py` | 1100-1101 | 新增 `await self._do_reload(rel)`（rollback 恢复文件后立即重载） |

**影响分析**：这解释了为什么用户之前看到"已改善"但行为没变。文件写入磁盘了，pycache 清了，但 `importlib.reload` 从未被调用。所有自愈操作声称成功但实际零生效。同样，回滚也是"恢复文件不改行为"。

**剩余限制**：核心模块（`loop.py`/`handler.py`/`oaa_agent.py`/`app.py`）仍然不能热重载，`reload_module` 明确阻止。修改这些模块后 agent 应告知用户需要重启。

### IdleInspector 冷却修正

| 文件 | 行 | 变更 | 说明 |
|------|----|------|------|
| `idle_inspector.py` | 52 | `_last_check: float = 0.0` → `time.time()` | 启动时从当前时间开始冷却，而非从 epoch。避免首次 `process_message` 时立即触发巡检，防止巡检提案混入启动消息 |
| `idle_inspector.py` | 24 | `_INSPECTION_COOLDOWN = 300` → `600` | 巡检间隔从 5 分钟扩大到 10 分钟，减少推送频率 |

### WeChat 启动通知 context_token 分析

微信首次主动推送（启动通知）未收到的原因：iLink API 需要 `context_token` 才能路由消息到用户微信。`context_token` 只在用户主动给机器人发消息时获得（`get_updates` → 保存 `context_token`）。主动外发时 `ctx=""`，API 返回 success 但消息**不送达**。

用户后续收到的消息都是在回复机器人消息之后（此时已有 context_token）。修复方向：启动通知改为延迟发送（用户首次交互时附带积压消息），或跳过微信主动通知仅推送 Desktop。

### 自愈闭环断点分析（5 个问题对应根因）

| # | 用户现象 | 根因 | 修复方向 |
|---|---------|------|---------|
| 1 | 同意修复后 agent 问"确认什么？" | 巡检提案是纯文本，无结构化 action ID。用户"确认"无法映射到修复执行 | Proposal 结构化（JSON 存储 + action 序列 + 执行引擎） |
| 2 | "已记住忽略 wechat_contacts" 但下次巡检依然报告 | LLM 把规则写进了 HOT memory，但 `_check_usage_patterns()` 不读 HOT memory。两套系统各过各的 | IdleInspector 加可持久化忽略列表，所有 check 方法统一查 |
| 3 | GUI 自动化：agent 说"沙箱限制了"，没自己装 pywinauto | 自主性不足。agent 应主动用 `shell_run pip install pywinauto` 解决问题而不是报告限制 | A2 Agent 自主性改造 — 系统提示词强化主动性 |
| 4 | 重复接收相同巡检消息 | dedup 按全文 hash，但"失败 4 次"→"5 次"文本不同，hash 不同，永远命中不了 | dedup 按 tool 名 + 提案类型去重，剔除数字部分 |
| 5 | `self_improve` 改了代码但行为不变 | `_do_reload` 缺 `await`，模块从未重载（本会话已修复） | ✅ 已修复 |

### Agent 自主性现状评估

**问题本质**：agent 的操作停留在"LLM 对话层"，没有转化为"系统层状态变更"。具体表现：

| 场景 | agent 行为 | 实际需要的 |
|------|-----------|-----------|
| 用户同意修复工具 | 回复"好的，已修复" | 调用 `read_own_source` + `self_improve` + `reload_module` |
| 用户说"忽略 xxx" | 回复"已记住"并写 HOT memory | 调用 `idle_inspector.ignore_tool("xxx")` 持久化 |
| 用户要求 GUI 自动化 | 报告"沙箱限制" | 直接 `shell_run pip install pywinauto` 装好 |
| 检测到 wechat_contacts 失败 | 报告"累计失败 4 次" | 自己找 wechat-cli 装好解决 |

**修复原则**：
- agent 说"好"的时候，必须通过工具调用来执行，不能靠 LLM 口头承诺
- 每个可持久化的操作（忽略、配置、规则）都必须有对应的工具方法
- agent 遇到缺少的依赖应该自己安装，而不是报告"缺少依赖"

### 进化工厂 — 设计讨论记录

用户提出的进化工厂 UI 设计方案已记录，定义到分层实现计划：

**第一层（闭环基础）** — Proposal 结构化 + 执行引擎
- JSON 存储取代 `pending_proposals.md`
- 提案包含：`problem`（现存问题）/ `benefit`（修复后效果）/ `actions`（精确修复动作序列）/ `status`（状态机）
- 执行引擎按 action 序列自动执行，不依赖 LLM 二次理解
- IdleInspector 可持久化忽略列表（`ignore_tool(tool, permanent)`）

**第二层（进化工厂 UI）** — GUI 交互
- 左侧导航新增"进化工厂"
- 两个标签页：进化请求 / 进化结果
- 三按钮：本次忽略 / 彻底忽略 / 同意
- WebSocket 管理 API 支持

**第三层（验证与回滚）** — 闭环质量保障
- 执行后自动验证修复效果
- 失败回滚 + 记录
- 验证方案详见下方

### 自愈闭环 & 回滚测试方案

**测试 1：自愈闭环**
1. 在 `tools.py` 制造可控语法错误
2. 等待 IdleInspector 提案
3. 用户确认 → ProposalExecutor 执行 action 序列
4. 验证：工具恢复、模块已重载（进程内即时生效）

**测试 2：回滚验证**
1. 执行一次 `self_improve`
2. 调用 `rollback_change` 回滚
3. 验证文件恢复 + 模块重载

**测试 3：核心模块变更**
1. 对 `oaa_agent.py` 发起 `self_improve`
2. 应返回"需重启"提示，不能静默失败

---

### 设计检视发现的关键断点

| # | 断点 | 影响 | 严重度 |
|---|------|------|--------|
| 1 | `IdleInspector` 提案只发 GUI，不进 agent 消息流 | agent 根本看不到自愈机会，所有自我优化提案成死信 | P0 |
| 2 | `EvolutionEngine` 创建时 `llm=None` | `extract_and_crystallize` 永远返回 None，L3 结晶无法工作 | P0 |
| 3 | 技能使用 ≥3 次无自动结晶触发 | 轨迹记了但技能不会自动固化 | P1 |
| 4 | `self_improve` 改 Python 文件无默认语法检查 | verify 参数常被省略，改代码可能写坏语法 | P1 |
| 5 | agent 缺少运行时健康诊断工具 | 只能查进程死活，不能查 WebSocket 端口、内存水位、错误率 | P1 |

### 修复清单

| # | 变更 | 文件 | 说明 |
|---|------|------|------|
| 1 | `self_improve` 加默认 verify | `tools.py` | 修改 `.py` 文件时自动 `python -c "import ast; ast.parse(...)"` 语法检查 |
| 2 | EvolutionEngine 接入 LLM | `app.py` | `evolution._llm = self.agent.llm`，L3 结晶不再返回 None |
| 3 | 自动结晶触发器 | `engine.py` | `record_skill_usage` 中 ≥3 次且无结晶 → 自动 `extract_and_crystallize` |
| 4 | IdleInspector→Agent 回路 | `memory_manager.py`, `idle_inspector.py`, `oaa_agent.py` | 提案写入 `pending_proposals.md`，通过 `build_memory_prompt` 注入 agent 系统提示词 |
| 5 | `health_diagnose` 工具 | `tools.py` | 检查 WS 端口、内存、错误数、进程状态 |

运行完整 `test_autonomy.py` 验证 ModuleNotFound 自动恢复修复效果：

```bash
taskkill //F //FI "WINDOWTITLE eq run_app*" 2>/dev/null
rm -f std6.out
rm -rf "E:/GenericAgent/data/memory/trajectories"
PYTHONUNBUFFERED=1 python test_autonomy.py
```

预期改进：
- 场景 A 的 `code_exec` 错误 → 触发 `pip install` recovery hint → 可能转为 PASS
- 场景 B 的 LLM RateLimitError 观察（是否为 Sensenova 模型特性）

### 优先级 2 — 修复技能详情面板位置（Task #8）

技能详情面板显示在被点击技能下方，而非浮动定位。

### 优先级 3 — 调查自生技能数据来源及应用按钮回退问题（Task #9）

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

## 本次会话（2026-05-18 续）— P0 Phase 3

### 闭环断点 3：code_exec 自动纠错

| 文件 | 变更 | 说明 |
|------|------|------|
| `tools.py` | +`_fix_syntax_errors()` | 预执行语法修复：tab→空格、textwrap.dedent 移除多余缩进 |
| `tools.py` | +`_fix_name_error()` | 后执行 NameError 修复：解析错误名 → 匹配常见模块 → 插入 import |
| `tools.py` | `do_code_exec` 重写 | 2 次尝试循环：语法修复 → 执行 → NameError 修复 → 重执行 |
| `tools.py` | 返回值扩展 | `fix_applied` / `original_code` / `fixed_code` 供 LLM 学习 |

**修复策略**：SyntaxError → 自动修复，NameError（已知模块）→ 自动补 import，TypeError/ValueError/未知 NameError → 返回完整 traceback 给 LLM 自修复。

**验证结果**：缩进修复 ✓、NameError import 补全 ✓、不可修复错误返回 traceback ✓、修复信息随返回值返回供 LLM 学习 ✓

### 闭环断点 5：tool_failure 检测闭环

| 文件 | 变更 | 说明 |
|------|------|------|
| `idle_inspector.py` | `_check_tool_failures` 增强 | 重复失败检测（≥2 次 → 生成修复方案），建议步骤含 `read_own_source` → `file_patch` → `__pycache__` 清除 → `reload_module` |

**闭环流程**：工具执行失败 → `loop.py` 自动记录到 `tool_failures.md` → `IdleInspector.inspect()` 空闲时检测到重复失败模式 → 生成修复提案 → 用户确认 → agent 自动执行修复流水线。

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

| 维度 | 现状 | 目标 | 状态 |
|------|------|------|------|
| 自动纠错 | 无 | SyntaxError/NameError 自动修复 | ✅ **Phase 3 完成** |

工作项：
1. ✅ **完成** 在 `do_code_exec` 中加入纠错层：
   - `SyntaxError` → `_fix_syntax_errors()` 用 `ast` 解析 + tab/缩进修复
   - `NameError` → `_fix_name_error()` 自动插入 import 后重试
   - `TypeError`/`ValueError` → 返回完整 traceback 给 LLM 自修复
2. ✅ **完成** 如果修复成功，将修正后的代码（`original_code` / `fixed_code` / `fix_applied`）返回给 LLM 供学习

#### 闭环断点 4 — 消息队列无限增长

工作项：
1. ✅ **完成** `AgentLoop.__init__` 加 `max_messages: int = 60` 参数
2. ✅ **完成** 每轮结束后调用 `_compact_messages()`，逻辑：
   - 保留 system prompt（第 0 条）
   - 保留最近 `max_messages-1` 条消息
   - 移除中间的工具调用细节
3. ✅ **完成** 与 MemoryManager 联动：首次压缩时将原始请求写入 HOT memory

#### 闭环断点 5 — 工具失败未影响 agent 行为

| 维度 | 现状 | 目标 | 状态 |
|------|------|------|------|
| 闭环 | 失败仅记录 | 检测 + 提案 + 修复流水线 | ✅ **Phase 3 完成** |

工作项：
1. ✅ **已完成** `_memory_mgr.add_tool_failure()` 记录（`loop.py:197`）
2. ✅ **已完成** IdleInspector Phase 3 `_check_tool_failures()`：
   - 按工具名分组统计，检测重复失败模式
   - 对失败 ≥2 次的工具，生成修复建议
   - 建议步骤：`read_own_source` → `file_patch` → `__pycache__` 清除 → `reload_module`
   - 提示用户 → 用户确认 → agent 执行修复流水线
3. ✅ **已完成** 修复提案中包含 `__pycache__` 清除 + `reload_module` 步骤指导

#### 闭环断点 6 — 协议适配需手动配置

工作项：
1. ✅ **完成** `LLMClient._detect_api_format()` — URL 启发式检测：`api_format` 显式配置优先，其次 URL 含 `anthropic.com` 则 `anthropic`，其余默认 `openai`
2. **待定** 协议级参数适配（`max_tokens` vs `max_completion_tokens`）— 下游客户端已有区分，当前仅需路由正确

---

### 执行顺序

```
Phase 1（断点 1 + 4） ✅ 完成 → Phase 2（断点 2 + 6） ✅ 完成 → Phase 3（断点 3 + 5） ✅ 完成
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

### P2 — 已验证（C1/C2 已完成）

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| 70 | 技能进化 | `EvolutionEngine` 统计面板 + 自优化建议闭环 | ✅ GUI 面板已展示，IdleInspector 串联 |
| 71 | 自生技能 | `SkillManager` 动态加载可视化 | ✅ 详情面板 + 搜索 + 加载按钮完善 |

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

## 本次会话（续）

### B2：执行自由度 — 权限模型宽松化

| 模块 | 变更 | 说明 |
|------|------|------|
| `config.py` | +permission_level | 新增 `"auto"` / `"confirm"` / `"restrict"` 三级权限，默认 auto |
| `auth/permissions.py` | 重构 | `DANGEROUS_OPS` 集合、`confirm_operation()` 三级分发逻辑、日志审计 |
| `agent/tools.py` | +_confirm() | 新增 `_confirm()` 方法；`shell_run/code_exec/file_write/file_patch` 接入权限检查 |
| `test_permissions.py` | 重写 | 覆盖 auto/confirm/restrict 三级行为、`DANGEROUS_OPS` 完整性、回调集成 |
| `test_extended_tools.py` | 适配 | 显式指定 `permission_level: confirm` 确保测试语义正确 |
| `oaa_agent.py` | +权限级别提示 | 系统提示词中显示当前权限级别 |

### B3：modify_own_prompt 工具

| 模块 | 变更 | 说明 |
|------|------|------|
| `agent/tools.py` | +do_modify_own_prompt | 新增工具：list 列出/read 查看/write 改写 identity/soul/user/agents/bootstrap 五个提示词节 |
| `agent/tool_schema.py` | +schema | modify_own_prompt 的 OpenAI 函数调用 schema（action/section/content 参数） |

### B4：self_improve 自我改进工作流

| 模块 | 变更 | 说明 |
|------|------|------|
| `agent/tools.py` | +do_self_improve | 原子化自修改工具：备份 → 应用 → 验证(可选) → 成功(清pycache+重载+记录) / 失败(自动回滚) |
| `agent/tool_schema.py` | +schema | self_improve 的 OpenAI 函数调用 schema（path/old_content/new_content/verify/description 参数） |

### A1：工具迁移到 @agent_tool 装饰器

| 工具 | 迁移方式 | 说明 |
|------|---------|------|
| `ask_user` | 显式参数 | question: str, candidates: list |
| `update_working_checkpoint` | 显式参数 | key_info: str |
| `correction_log` | 显式参数 | context: str, lesson: str |
| `memory_recall` | 显式参数 | query: str |
| `self_reflect` | 显式参数 | context: str, reflection: str, lesson: str = "" |
| `file_write` | 显式参数 | path: str, content: str, mode: str = "overwrite" |
| `file_patch` | 显式参数 | path: str, old_content: str, new_content: str |
| `shell_run` | 显式参数 | command: str, timeout: int = 60, cwd: str = "" |
| `code_run` | 显式参数 | code: str, type: str = "python", timeout: int = 15, cwd: str = "" |
| `code_exec` | 显式参数 | code: str, timeout: int = 15 |
| `read_own_source` | 显式参数 | path: str = "", pattern: str = "", start_line: int = 1, line_count: int = 200 |
| `list_own_structure` | 显式参数 | path: str = "", depth: int = 2 |
| `reload_module` | 显式参数 | module: str |
| `rollback_change` | 显式参数 | index: int = -1 |
| `modify_own_prompt` | 保留 legacy | 复杂多 action 逻辑，schema 仍在 tool_schema.py |
| `self_improve` | 保留 legacy | 复杂多步骤流程，schema 仍在 tool_schema.py |

所有 14 个原子工具已完成 @agent_tool 迁移，对应 schema 从 tool_schema.py 移除（auto-gen）。
还原留 2 个复杂工具的 legacy schema 和 4 个 WeChat stub（无需迁移）。

---

## 本次会话（2026-05-18 续）— A2/A3 + C1/C2 + evolution 深度集成

### A2：_exec_runner 子进程超时看门狗

| 文件 | 变更 | 说明 |
|------|------|------|
| `_exec_runner.py` | +threading timeout | 新增 `--timeout N` 参数，线程级看门狗，超时后 `sys.exit(1)` |
| `tools.py` | 传递 timeout | `do_code_exec` 调用时传入 `--timeout` 参数与调用方一致 |

### A3：LLM 摘要式消息压缩

| 文件 | 变更 | 说明 |
|------|------|------|
| `loop.py` | `_compact_messages` → async + `_summarize_with_llm` | 首次压缩时 LLM 生成中文摘要注入为 system 消息，保留对话上下文 |

### C1：进化引擎统计面板

| 文件 | 变更 | 说明 |
|------|------|------|
| `SkillView.vue` | +演化统计仪表盘 | 4 个 stat 卡片（使用次数/固化技能/SOP执行/待处理建议）+ 使用排行条形图 + 已固化技能列表 |

### C2：技能详情面板 + 搜索

| 文件 | 变更 | 说明 |
|------|------|------|
| `SkillView.vue` | +搜索框 + 可展开详情 | 实时搜索过滤、点击展开详情面板（工具/知识/SKILL.md/SOP.md 三个标签页）、加载按钮 |
| `management.py` | +`get_skill_detail` + `switch_skill` | 后端新 handler：返回完整技能信息、切换当前技能 |

### 进化引擎 + self_improve 深度集成

| 文件 | 变更 | 说明 |
|------|------|------|
| `engine.py` | +`get_auto_refinements()` | 结构化提案：SOP 步骤跳过检测（≥3 次 → self_improve 移除）、技能使用里程碑（5 的倍数 → code_exec 优化分析） |
| `idle_inspector.py` | +evolution 注入 + 4 阶段检查 | Phase 1 到期任务 → Phase 2 进化引擎 SOP/技能优化 → Phase 3 工具失败自修复 → Phase 4 修正模式自我提示词更新 |
| `idle_inspector.py` | `_check_tool_failures` 增强 | 按工具名映射到源代码文件（tools.py/extended_tools.py），生成精确的 `read_own_source` → `self_improve` → 清 pycache → `reload_module` 修复链 |
| `idle_inspector.py` | +`_check_correction_patterns` | 检测 Repeated Correction（同 lesson ≥2 次），生成 `modify_own_prompt` 提案，将规则写入 agents 段 |
| `oaa_agent.py` | evolution → IdleInspector | 将 evolution 实例注入 constructor，使所有进化数据对检查器可见 |

**流程闭环**：进化引擎记录使用模式 → IdleInspector 空闲时识别优化点 → 生成结构化提案 → LLM 自选 `self_improve`/`modify_own_prompt`/`code_exec` 执行 → 验证生效 → 记录到 memory 供后续学习

---

## 本次会话（2026-05-18 续）— 技能去重

### 技能 vs OAA 内置工具全量对比

对 29 个技能逐一分析，分 3 类处置：

**立即删除（OAA 完全覆盖，3 个）：**
| 技能 | OAA 替代 | 理由 |
|------|---------|------|
| `agent-memory` | `MemoryManager`（HOT/corrections/warm/cold 分层记忆） | 功能完全一致 |
| `self-improving` | `self_improve` + `correction_log` + `IdleInspector` | 自我反思/修正/学习已内建 |
| `skill-creator` | `skill_create` 工具 | 脚手架生成已内建 |

**知识吸收后删除（OAA 有基础实现，Skill 有更深领域知识，2 个）：**
| 技能 | OAA 原有 | 增强内容 |
|------|---------|---------|
| `excel-xlsx` | 基础读写 | 公式支持 `formulas`、列宽 `column_widths`、表头样式 `header_row`、文本列保护 `text_columns`、自定义工作表名 `sheet_name` |
| `word-docx` | 纯文本段落 | Markdown 式内容解析（#/##/### 标题、* 列表、> 引用）、表格 `tables`、页面方向 `page_orientation`、边距 `margins` |

**保留（OAA 无对应功能，22 个）：** 16 个外贸业务核心技能、nano-pdf、bb-browser、summarize、clawhub、himalaya-email、agent-autonomy-kit

| 文件 | 变更 | 说明 |
|------|------|------|
| `extended_tools.py` | `do_word_doc` 重写 | 新增 tables/headings/styles/page_setup，注入 DOCX 领域规则 docstring |
| `extended_tools.py` | `do_excel_xlsx` 重写 | 新增 formulas/column_widths/header_row/text_columns/sheet_name，注入 Excel 领域规则 docstring |
| `tool_schema.py` | `word_doc`+`excel_xlsx` schema 升级 | 描述从 1 行扩充到含领域规则，参数从 2-3 个扩充到 5-7 个，含枚举/类型约束 |

**变更统计**：删除 5 个技能目录（3 冗余 + 2 吸收入工具），增强 2 个 OAA 工具，26 个文件无修改。

---

## 本次会话（2026-05-18 续）— SkillView Bug 修复 + 全功能集成测试

### GUI CDP 测试（10/10 通过）

新增 2 个测试场景：

| # | 测试 | 说明 |
|---|------|------|
| 1-7 | 原 7 项测试（Chat/Skills/Connections/Tasks/Files/Settings/Return） | 全部通过 |
| **8** | **SkillView tab switching** | 3 个标签按钮找到；evolution → market → repo 标签切换均成功 |
| **9** | **SkillView skill detail** | 技能卡片点击后详情面板展开，显示 "self-improving-agent-3.0.5" |
| 10 | Console errors | 0 JS 错误 |

### 修复的 Bug

| # | Bug | 根因 | 修复 |
|---|-----|------|------|
| B4 | 技能仓库一直显示加载中 | `v-if="expandedSkill === skill.name"` 在 `v-for` 同级引用循环变量 `skill` → `undefined.name` 抛 TypeError → Vue fallback 到旧 vnode 树（loading 状态），组件永久卡死 | ① `expandedSkill === skill.name` → `expandedSkill && isExpandedSkillInGroup(group)` ② `loadSkill(skill.name)` → `loadSkill(expandedSkill)` ③ 添加 `isExpandedSkillInGroup()` 安全函数 |
| B5 | 自生技能标签点击无反应 | 同上—render 崩溃后所有 state 变更无法触发 DOM 更新 | 同上 |
| B6 | 技能市场无法切换回仓库/自生技能 | 同上 | 同上 |
| B7 | `<template v-else>` 兼容性 | Vue 3.4 + Vite 5 下多子节点 fragment 编译可能有边缘问题 | 替换为 `<div v-else class="tab-content-inner">` |
| B8 | 缺少 `:key` 属性 | Vue 3 patch flag 优化下无 key 的 tab 元素可能复用旧 DOM | 添加 `key="repo/evolution/market"` |
| B9 | `allSkills` 类型不匹配 | `allSkills.value = skills as any` 导致 `BackendSkill[]` 赋给 `Skill[]` | 改用 `newGroups.flatMap(g => g.skills)` |
| B10 | KeepAlive 激活后数据不刷新 | 缺少 `onActivated` 生命周期钩子 | 添加 `onActivated` 异步刷新 |

**文件**：`gui/src/views/SkillView.vue` | **测试**：`test_gui_cdp.py`（10 测试全部通过）

### 测试结果汇总

| 测试套件 | 通过/总数 |
|---------|----------|
| WS E2E（聊天/设置/模型切换） | 3/3 |
| 单元测试（extended_tools / permissions） | 10/10 |
| GUI CDP 浏览器测试 | **10/10** |
| **合计** | **23/23** |

---

## 本次会话（2026-05-19）— `ai_search` 统一搜索路由工具

### 背景

OAA 内置的 `web_search`/`web_scan` 仅支持百度搜索，质量低、易被反爬。用户持有 Tavily / Exa / AnySearch 三个 AI 搜索 API key，需统一整合。

### 实施

| 文件 | 变更 | 说明 |
|------|------|------|
| `config.py` | +`SearchConfig` dataclass | 3 个字段：`tavily_api_key`/`exa_api_key`/`anysearch_api_key`，挂到 `AppConfig.search` |
| `agent/ai_search_tool.py` | **新增** | `AiSearchTools` handler，含自动意图检测 + 引擎路由 + 故障 fallback |
| `agent/oaa_agent.py` | 注册 `AiSearchTools` | 加入 `_MergedHandler` 后端的 `_search`，schema 加入 `_tools_schema` |
| `~/OAA/config.json` | +`search` 段 | 写入用户提供的 3 个 API key |

### 路由策略

```
ai_search(query, intent="auto", region="auto", max_results=10, domain="")
  ├── intent="lead_gen"        → Exa（结构化输出 + 公司/人垂直索引）
  ├── query含中文/region=cn    → AnySearch（国内站点，cn 区域）
  ├── intent="deep_research"   → Exa Deep（多步推理 + 摘要）
  └── 默认（general/intl）     → Tavily（最快，~180ms）
```

- **自动意图检测**：`company/email/供应商` → lead_gen；`compare/分析/对比` → deep_research；含中文 → 区域 cn
- **故障容错**：主引擎失败自动 fallback（Tavily→Exa→AnySearch→Tavily）
- **统一输出**：三个引擎统一返回 `{title, url, content, score}` 格式

### 对比：AnySearch vs Tavily vs Exa

| 维度 | Tavily | Exa | AnySearch |
|------|--------|-----|-----------|
| 定位 | Agent 上网层 | 结构化搜索 API | 搜索聚合网关 |
| 核心能力 | search/extract/crawl/research | search/contents/answer/deep-search | 多 provider 路由+融合重排 |
| 特色 | 安全过滤、内容分块、180ms | 结构化输出、公司/人垂直索引 | 22 领域、cn/intl 双区域 |
| 定价 | 免费 1000/月，PAYG $0.008/credit | 免费 1000/月，Search $7/千次 | 匿名免费 + API Key 付费 |
| 适用场景 | 通用搜索（最快） | 批量查公司/人（结构化） | 中文/国内/多源验证 |

### 设计决策

- **不做硬编码**：API key 写入 `config.json`（不在源码中），换 key 无需改代码
- **单工具入口**：LLM 只看到一个 `ai_search`，背后自动路由，减少 LLM 选择负担
- **非 MCP 方案**：直接实现为 OAA 内置 handler，避免 MCP Server 进程管理开销

### 待优化

- 当前 Exa/AnySearch/Tavily 返回字段差异通过工具层统一化，部分信息（如 quality_score）在统一过程中丢失
- 未来可考虑增加搜索结果缓存减少重复 API 调用

---

## A1 — 聊天历史持久化（对话摘要存档）

| 文件 | 变更 | 状态 | 说明 |
|------|------|------|------|
| `oaa/agent/conversation_archiver.py` | **新增** | ✅ 已完成 | ConversationArchiver 类：LLM 摘要生成 → 结构化存档 → 分层搜索 |
| `oaa/agent/oaa_agent.py` | +3 处改动 | ✅ 已完成 | 初始化 archiver、注入 build_system_prompt、process_message 触发归档 |
| `oaa/agent/tools.py` | +1 工具 | ✅ 已完成 | `chat_history_search` — agent 搜索历史对话摘要 |
| `oaa/app.py` | +1 行 | ✅ 无需改动 | archiver 在 OAAAgent 内部完成初始化（已有 llm 引用） |

**设计要点**：
- 摘要格式：固定字段（用户目标/关键信息/完成事项/遗留问题），每条约 600-1000 字节
- 触发策略：每 10 条消息 + 对话结束双重触发，asyncio.create_task 后台执行不阻塞
- 预热机制：build_system_prompt 自动注入最近 3 条摘要到系统提示词
- 搜索分层：① warm/conversations/ 摘要关键词匹配 → ② SQLite FTS5 原始消息，按得分合并排序
- 容错：LLM 摘要失败静默跳过，目录自动创建，并发归档不阻塞

### Bug 修复 — 用户测试反馈

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| F1 | Agent 声称文件已保存但未调用 `file_write` | LLM 虚构操作 | `system_rules.py` 新增"禁止虚构操作"规则 |
| F2 | Agent 说"微信未配置"拒绝发送文件 | LLM 混淆 iLink 与 wechat-cli 工具 | `oaa_agent.py`: `_build_channel_status()` 动态注入各通道连接状态到 system prompt，二愣每次醒来看到实时状态表而非静态规则；`tool_schema.py` 描述同步更新 |
| F3 | Agent 让用户手动操作 | 违反系统规则 | 第 17 条"禁止要求用户手动操作"强化 |
| F4 | BadRequestError 覆盖对话输出 | ① 未加入非重试列表 ② 错误直接以 done 覆盖 | `loop.py`: BadRequestError 等 7 种错误不重试 + 有历史输出时用 llm_output 追加 |
| F5 | IdleInspector 未推送到微信 | `_bot_user_id` 未持久化，重启后为空 | `config.py` + `management.py` + `wechat_ilink.py` + `app.py` — QR 确认时保存 `ilink_user_id`，启动时恢复 |
| F6 | IdleInspector 误报 stub 工具失败 | `wechat_contacts` 等预期错误被视为 bug | `idle_inspector.py` 加 `_STUB_TOOLS` 过滤，跳过已知 stub |
| F7 | Agent 不了解自身运行时状态 | 无通道连接状态注入 | `oaa_agent.py` `_build_channel_status()` 动态注入各通道连接状态到 system prompt |
| F8 | 启动后用户不知道通道状态 | 无主动通知 | `app.py` `_startup_check()` 启动后推送通道状态到 Desktop + WeChat |

---

## 架构升级：Harness + 渐进式技能（2026-05-24 规划）

### 设计理念

参考 "Harness 完全指南" 架构思想：**Agent = Model + Harness**。模型只负责"想"（决定下一步调什么函数），Harness 负责"做"（工具执行、权限检查、记忆管理、错误处理）。

单 agent + 深度 skill 切换，配合 Harness 9 组件构建完整的 agent 运行时。

### 待完成（按优先级）

#### P0 — 渐进式披露（Progressive Disclosure） ⬅️ 当前进行中

**核心思想**：技能信息分三层加载，不一次性塞满上下文。

| 层 | 内容 | Token 开销 | 加载时机 |
|----|------|-----------|---------|
| Level 1（元数据） | 技能名称 + 一句话描述 + 类别 | ~100/个 | 始终在上下文中 |
| Level 2（指令） | 完整 SKILL.md | ~500-2000 | `skill_load()` 时按需加载 |
| Level 3（资源） | 模板、代码、参考文件 | 不定 | 执行时按需读取 |

**改动范围**：
- `oaa/agent/skill_manager.py` — 技能元数据精简（名称+描述+类别），取消一次性全量加载 SKILL.md
- `oaa/agent/oaa_agent.py` — `build_system_prompt()` 只注入元数据列表，`skill_load()` 注入到 user context 而非替换 system prompt
- System Prompt 保持恒定，避免认知冲突

#### P1 — Step Runtime（步骤化执行引擎）

**核心思想**：AgentLoop 执行过程拆分为可观测的 Step，WorkPanel 逐步骤显示进度。

```
Agent 每轮循环 = Step
├── Plan:   LLM 输出→解析工具调用（或直接回复）
├── Gate:   权限检查
├── Exec:   执行工具→返回结果
├── Observe: 结果注入上下文→决定继续/终止
└── Yield:  步骤状态→WorkPanel 展示
```

**改动范围**：
- `oaa/agent/loop.py` — 引入 Step 状态机，每条工具调用对应一个 Step
- `gui/src/composables/useWebSocket.ts` — step 事件类型
- `gui/src/components/WorkPanel.vue` — 按步骤分组展示

#### P2 — 技能插件化（技能绑定工具集 + 身份）

**核心思想**：技能不只是 SKILL.md，而是包含独立工具集、身份、规则的 Harness 插件。

```
Skill Plugin
├── SKILL.md        — 操作指南
├── tools.json      — 技能专属工具清单
├── identity.md     — 说话风格、专业领域
└── rules.json      — 能做/不能做的策略
```

加载技能 → Harness 注入对应工具 + 身份 + 规则。卸载时恢复默认。

**改动范围**：
- `oaa/agent/skill_manager.py` — 技能元数据结构扩展（tools/identity/rules）
- `oaa/agent/tool_schema.py` — 按技能过滤可见工具
- `oaa/agent/oaa_agent.py` — 技能加载/卸载时同步切换工具集

#### P3 — 产物契约（工作区 + 文件传递状态）

**核心思想**：Agent 内部通过产物文件传递状态，不依赖 LLM 上下文。

```
workspace/task_NNN/
├── _plan.md        — 执行计划
├── _progress.md    — 完成/进行中的步骤
├── _context.md     — 累积上下文（防止 fallback 丢失）
└── artifacts/      — 中间产物（搜索结果、生成的文件）
```

**改动范围**：
- `oaa/agent/workspace.py` — **新增**，工作区管理
- `oaa/agent/tools.py` — `workspace_init` / `workspace_log` 等工具
- `oaa/agent/loop.py` — 自动创建/维护工作区

#### P4 — Policy 规则引擎

**核心思想**：技能自带 "能做/不能做" 规则，Harness 在 Gate 阶段检查。

**改动范围**：
- `oaa/auth/permissions.py` — 支持技能级规则注入
- `oaa/agent/loop.py` — Gate 步骤调用权限检查

---

---

## 本次会话（2026-05-27）— 自进化能力差距分析 + 混合克隆优先方案

### 用户 6 项需求 vs 当前实现差距

| # | 需求 | 当前状态 | 差距 |
|---|------|---------|------|
| 1 | **任务复盘** — 任务完成后自动分析失败/成功原因，LLM 驱动复盘 | `trajectories/` 记录原始轨迹 + `evolution/engine.py` 有 `analyze_for_suggestions()` 但不自动触发 | **未组装** — 轨迹有、引擎有，但缺少"任务完成→自动复盘→结论落地"的流水线 |
| 2 | **社区自学** — 自主逛 GitHub/Reddit 找新技术，自我 code review，需要用户注册时主动说明 | `ai_search` 可搜索但无定期浏览机制；无自我 code review 工具；`system_rules.py` 有请求协作规则但无主动引导 | **大量缺失** — 需要新增：定期 GitHub 扫描、self-code-review 工具、注册引导 SOP |
| 3 | **结构化用户偏好** — EvolutionView 新建"用户偏好"标签页，≤200 行，带索引检索 | `MemoryManager` 有 `add_to_hot()/search_hot()` 但非结构化、无独立 UI | **需新建** — PreferencesStore（JSON 持久化）+ EvolutionView tab |
| 4 | **简化 IdleInspector** — 移除线 C 日调度日常检查，只保留周级磁盘检查 | 线 C 已实现 `_check_memory_health/_check_correction_patterns/_self_learn` 等 | **需改造** — 审查后确认：日常健康检查应移走，仅保留周磁盘；剩余容量给进化 |
| 5 | **反思/学习节奏** — 每日任务反思 + 每周固定时间自主学习（先确认用户是否在线） | 无任何定时反思机制；日调度为巡检而非反思 | **需新建** — 反思 scheduler（每日末次对话后）+ 周学习 schedule（确认用户在线后执行） |
| 6 | **可验证的回滚安全** — 确保修改不会导致系统崩溃 | 现有 `_backup_file` + `rollback_manifest.json` + `shutil.copy2` 恢复，但无集成测试验证 | **部分满足** — 文件级回滚可靠，但缺乏修改前先验证的机制（即"克隆优先"） |

### 混合克隆优先策略（下一阶段方案）

用户提出的"克隆优先"思路：安装时/首次需要时创建 OAA 完整副本，agent 修改代码前先在副本上实验→测试→通过后再同步到 Live 系统。

**与现有回滚机制的对比**：

| 维度 | 现有回滚机制 | 克隆优先 |
|------|------------|---------|
| 隔离性 | 直接修改 Live 文件，改坏了靠恢复 | **完全隔离**，修改坏的不影响 Live |
| 验证能力 | 仅 verify 函数检查（语法/模块重载） | **可跑完整测试**（pytest / 功能验证） |
| 覆盖范围 | 单文件备份恢复 | **任意复杂度**（多文件/新增文件/目录结构） |
| 资源开销 | 极小（每个文件一个 .bak） | 大（2x 磁盘 + 进程管理） |
| 热重载配合 | 文件级恢复，受热重载边界限制 | 同受热重载限制，但测试阶段即可发现 |
| 实现复杂度 | 简单（已有完整实现） | 中等（需要 clone 管理 + diff 同步逻辑） |

**分场景策略**：

| 场景 | 方案 | 理由 |
|------|------|------|
| **单文件修复**（≤3 文件，如工具 bug 修复） | 现有回滚机制 | clone 太重，小改动不值得 2x 开销 |
| **涉及测试的进化**（新增功能、改逻辑） | **克隆优先** | 需要真实测试验证，不能只靠语法检查 |
| **核心模块修改**（`loop.py` / `app.py` / `oaa_agent.py`） | **克隆优先** | 这些模块崩了回滚可能来不及救，热重载也不支持 |
| **热重载不支持的变更**（新增文件、改目录结构） | **克隆优先** | 需要重启应用，测试阶段必须验证完整启动 |
| **日常开发/调试** | 现有回滚机制 | 迭代速度快，clone 拖慢节奏 |

**克隆优先实现方案**：

```
data_dir/clone/                ← 首次需要时创建（非安装时）
├── oaa/                       ← shutil.copytree 完整代码副本
├── requirements.txt
└── _clone_status.json         ← {created_at, version, sync_status}

修改流程：
  1. agent 分析问题 → 决定需要克隆修改
  2. 执行 `clone_create()` → 创建代码副本（如果不存在）
  3. 在 clone 上 `self_improve` 修改文件（不影响 Live）
  4. 在 clone 上运行测试套件（pytest / smoke test）
  5. 测试通过 → `clone_sync()` diff + patch 同步到 Live
  6. 测试失败 → `clone_discard()` 丢弃 clone 修改
  7. `reload_module` 使修改生效（或提示重启）
```

**同步机制**：使用 `diff --recursive` 生成 patch → `patch` 应用到 Live，而非全量覆盖。这确保 Live 系统上被用户手动修改的文件不会意外被旧版本覆盖。

### 实施路线图

| 阶段 | 内容 | 涉及文件 | 优先级 |
|------|------|---------|--------|
| **1. 基础设施** | PreferencesStore + 克隆管理工具（clone_create/sync/discard） | 新增 `oaa/agent/preferences_store.py`、`oaa/agent/clone_manager.py`；修改 `tool_schema.py` | P0 |
| **2. IdleInspector 简化** | 移除线 C 日常检查，仅保留周磁盘；释放容量给进化触发 | `oaa/agent/idle_inspector.py` | P0 |
| **3. 任务复盘流水线** | 轨迹完成自动触发 LLM 分析 → 结论写入 HOT memory / 进化建议 | `oaa/agent/loop.py`（完成回调）、`evolution/engine.py`（触发复盘） | P1 |
| **4. 学习节奏** | 反思 scheduler + 周学习定时器（带在线确认） | 新增 `oaa/agent/reflection_scheduler.py`；修改 `app.py` | P1 |
| **5. 用户偏好 UI** | EvolutionView 第三标签页 + PreferencesStore CRUD 管理 API | `gui/src/views/EvolutionView.vue`、`oaa/gateway/management.py` | P2 |
| **6. 社区自学** | GitHub 定期扫描 + self-code-review 工具 + 注册引导 SOP | `oaa/agent/extended_tools.py`、`system_rules.py` | P2 |

> **注意**：克隆优先机制是阶段 1 的核心，它为阶段 2-6 的所有进化操作提供安全实验环境。
> 阶段 1 完成后，agent 就有了"在副本上试错"的能力，之后的所有自我修改都走克隆优先路线。

## 本次会话（2026-05-26）— 愣小二（BitCPM4-1B 本地模型）集成 ❌ 已删除

**评估结论**：1B 模型无推理和工具使用能力，实际业务价值不足。已完全清除所有相关代码和文件。

**已删除的代码/文件**：
- `oaa/agent/oaa_agent.py` — `_run_local()` / `_load_local_identity()` / `_check_local_quality()` 等方法
- `oaa/agent/complexity_evaluator.py` — 路由决策引擎（整个文件）
- `oaa/agent/extended_tools.py` — `do_call_xiaoer()` 工具
- `oaa/agent/tool_schema.py` — `call_xiaoer` 定义
- `oaa/app.py` — `_start_local_llm()` / `_stop_local_llm()` / `_inject_local_llm_client()`
- `oaa/gateway/management.py` — `_handle_get_local_model_config` / `_handle_save_local_model_config`
- `oaa/gateway/adapters/desktop.py` — 管理类型列表移除本地模型条目
- `gui/src/views/LocalModelView.vue` — 配置页（整个文件）
- `gui/src/views/ChatView.vue` — 愣小二路由按钮、头像、状态灯
- `gui/src/components/Sidebar.vue` — 愣小二导航项
- `gui/src/App.vue` — LocalModelView 路由注册
- `scripts/local_llm_manager.py` — llama-server 管理脚本（整个文件）
- `cli/llama/` — llama-server 二进制 + DLL（~760MB）
- `E:/oaa_worker/models/` — 模型文件 BitCPM4-1B-q4_0.gguf（~760MB）
- `E:/oaa_worker/local_identity.md` — 愣小二身份定义
- `test_local_agent.py` — 测试文件

---


## 本次会话（2026-05-28）— tools.py 拆分 + shell_run 失败分析 + 方法论设计

### 工具集拆分

**问题**：`oaa/agent/tools.py` 膨胀到 2585 行，二愣自检报告列为首要技术债。

**方案**：拆分为 `tools/` 包，按领域分散到 6 个 mixin 文件，保持外部导入兼容。

```
oaa/agent/tools/
├── __init__.py     ← 重导出 AtomicTools, OAA_ROOT 等
├── _core.py        ← AtomicTools 主体（__init__/dispatch/shell_run/self_improve/reload/file_ops）
├── _code.py        ← CodeMixin（code_run/code_exec/aifix/code_search/glob/self_code_review）
├── _git.py         ← GitMixin（git_status/git_diff/git_log）
├── _memory.py      ← MemoryMixin（checkpoint/correction/memory_recall/chat_history/self_reflect）
├── _schedule.py    ← ScheduleMixin（proposal CRUD + schedule CRUD）
└── _misc.py        ← MiscMixin（health_download/github/clone/preference）
```

**设计**：多继承模式 — `class AtomicTools(BaseHandler, CodeMixin, GitMixin, ..., MiscMixin)`。`@agent_tool` 装饰器自动生成 schema，mixin 不定义 `__init__`，通过 `self` 访问 AtomicTools 属性。

| 文件 | 改动 |
|------|------|
| `oaa/agent/tools/_core.py` | **新增** — 常量、AtomicTools 主体 ~950 行（原 tools.py 拆分） |
| `oaa/agent/tools/_code.py` | **新增** — CodeMixin ~400 行 |
| `oaa/agent/tools/_git.py` | **新增** — GitMixin ~120 行 |
| `oaa/agent/tools/_memory.py` | **新增** — MemoryMixin ~100 行 |
| `oaa/agent/tools/_schedule.py` | **新增** — ScheduleMixin ~250 行 |
| `oaa/agent/tools/_misc.py` | **新增** — MiscMixin ~360 行 |
| `oaa/agent/tools/__init__.py` | **新增** — 重导出 |
| `oaa/agent/tools.py` | **删除** — 旧单文件 2585 行 |
| `oaa/agent/tools/_core.py` | 修复 `OAA_ROOT` 路径计算（`__file__` 层级变化）+ 所有相对导入修正 |

**修复的导入问题**：
- 模块级常量 `OAA_ROOT` 路径额外一层 `..`
- 所有 `from ..xxx` 相对导入需升为 `from ...xxx`
- `_build_module_list` 中 `oaa_path` 修正
- `do_reload_module` 的 `importlib.import_module` 需将 `OAA_ROOT` 加入 `sys.path`

**验证**：`test_tools.py` 13/13 通过，`test_self_mod_cycle.py` 8/8 通过。

### shell_run 失败根因分析（代码审查）

**来源**：二愣巡检报告称 `shell_run` 累计失败 12 次。

**分析方法**：代码审查 `do_shell_run` 的 5 个失败出口。

| 失败出口 | 代码位置 | 分析结论 |
|---------|---------|---------|
| 危险命令匹配 | `_core.py:502-504` | 低概率 — 9 个正则针对极端危险命令 |
| 权限确认拒绝 | `_core.py:505-506` | 中概率 — 默认 auto 模式允许，confirm/restrict 模式可能拒绝 |
| 超时 | `_core.py:519-522` | 中概率 — 60s 默认超时 |
| Windows 子进程异常 | `_core.py:511` | **高概率 — 最可能原因** |
| 通用异常捕获 | `_core.py:533-535` | 低概率 |

**核心根因**：Windows 上 `asyncio.create_subprocess_shell` 使用 `cmd.exe`，LLM 生成的 Unix 命令（`ls`/`grep`/`cat`/`rm`）在 `cmd.exe` 中不存在。12 次失败很可能全是 Windows vs Linux 命令兼容性问题。

**关键发现**：当前巡检按工具名分组分析，只看最后一环的失败计数，不看完整执行链，导致误判为"工具 bug"而非"策略失误"。

### 巡检分析方法论改造设计

**问题**：agent 的失败分析以单个工具节点为单位，而不是以整个任务执行链为基础。这导致：
- shell_run 失败 → 误判为工具 bug，实际上是在 Windows 上跑 Linux 命令
- 所有技能/工具问题都是如此 — 孤立分析节点，忽略前置策略决策

**用户洞察**：agent 掌握正确的分析问题方法比逐个修工具更重要。

**设计决策**：

1. **分析单元从工具→执行链**：以 `task_context`（用户原始输入）分组，一次任务中所有工具调用的有序序列一起分析
2. **链压缩策略**：合并连续同类工具调用，保留异类转折点。不以步数为基准，以一个任务的完整执行流程为基准（agent 执行过 100+ 步的任务）
   ```
   shell_run ×15 → file_read ×3 → memory_recall ×2 → shell_run ×25
   ```
3. **分析 prompt 重建**：从问"这个工具为什么失败"→ 问"这条执行链哪一环出了问题"
   - 策略失误：第一步就走错了方向（如 Windows 上选 Linux 命令）
   - 工具误用：策略合理但选错了工具
   - 工具 bug：前面都没问题，工具本身异常
   - 参数错误：工具选对了但参数传错
4. **新增根因类别**：`_FAILURE_CATEGORIES` 新增 `strategy_error`
5. **分析结果回写**：strategy_error 不修代码，改修正 system prompt 中的决策逻辑

**改动范围**（待实现）：
| 文件 | 改动 |
|------|------|
| `oaa/agent/memory_manager.py` | 链存储从 `[:800]` 截断改为智能合并；`add_tool_failure` 按 task_id 关联完整链 |
| `oaa/agent/idle_inspector.py` | 分组从按工具→按 task；链展示保留两端+中间转折点；新增 `strategy_error` 类别；prompt 重写 |
| `oaa/agent/loop.py` | `_execution_chain` 按任务生命周期积累；记录 `task_id` |

**状态**：✅ 已实现（2026-05-28 会话完成）。

## 本次会话（2026-05-28 续）— 运行时演进系统 + 进化工厂打通

### 背景

`self_improve` 直接修改源码文件在开发模式（git clone）下能工作，但在 .exe 打包环境完全失效：
- 源码文件不存在
- `reload_module` 对冻结模块无效
- 安装目录通常无写权限

**方案**：运行时演进系统（原名"补丁系统"）— 不修改源码文件，通过 `compile()` → `exec()（在目标模块命名空间）→ `setattr()` 直接在内存中覆盖目标函数/方法，持久化到数据目录。

### 演进系统（Runtime Patch System）

核心链路：`importlib.import_module()` → `exec(code, module.__dict__)` → `setattr(class/obj, func_name, compiled_func)`。在冻结模块上同样可用（模块可导入、`__dict__` 可写、`setattr` 不受冻结影响）。

| 文件 | 说明 |
|------|------|
| `oaa/agent/patch_manager.py` | **新增** — `PatchManager` 类：apply/remove/list/get/load_active，持久化到 `data_dir/patches/<id>.json` |
| `oaa/agent/patch_loader.py` | **新增** — `load_all(patches_dir)` 启动时扫描并重新应用所有 active 演进记录 |
| `oaa/app.py` | 创建 `PatchManager` → 注入 `ManagementHandler` + `OAAAgent` → 启动时 `load_patches()` |
| `oaa/agent/oaa_agent.py` | 新增 `set_patch_manager(mgr)` → 转发给 `ExtendedTools` |

### Agent 工具

| 工具 | 说明 |
|------|------|
| `apply_patch(target_module, target_attr, new_code, description)` | 编译代码 → 运行时打到目标类/函数 |
| `remove_patch(patch_id)` | 恢复 `original_code`（dev 模式有源码备份，.exe 模式需重启） |
| `list_patches(include_removed)` | 列出当前演进记录 |

### WebSocket 管理 API

`list_patches` / `remove_patch` — GUI 演进管理页面调用。

### GUI 演进管理页面

| 文件 | 变更 |
|------|------|
| `gui/src/views/PatchView.vue` | **新增** — 演进管理页面：活跃/历史标签页、删除确认弹窗、toast 通知 |
| `gui/src/App.vue` | 注册 `patches: PatchView` 到 tabComponents |
| `gui/src/components/Sidebar.vue` | 导航栏新增"演进"项（`<>` 图标） |
| `gui/src/composables/useWebSocket.ts` | 新增 `patchesUpdated` 计数器 + `patches_updated` 推送处理 |

### 打通进化工厂 → 演进系统

| 文件 | 变更 |
|------|------|
| `oaa/agent/system_rules.py` | 自愈指令从 `self_improve` → `apply_patch`，说明优先使用演进（运行时生效、.exe 可用、重启自动恢复） |
| `oaa/agent/repair_loop.py` | 修复提示词新增第 5 步：引导 agent 用 `apply_patch`（参数：`target_module`/`target_attr`/`new_code`）→ `reload_module` 热重载 |

### 修复

| 问题 | 修复 |
|------|------|
| `test_extended_tools_email_permission` 预置失败 | 测试调用 `do_email_send` 缺 `body` 参数 — 补上 `"body": "Test body"` |

### 命名

用户将"补丁"重命名为"演进"，与"进化工厂"形成对仗：进化工厂 = 策略/决策层，演进 = 执行/落地层。

### 验证

59 测试全部通过，0 失败。

---

## 本次会话（2026-05-27）— 进化提案周期性检查 + 浅色主题 + 多 Bug 修复

### 主题切换系统 ✅

聊天页面左上角新增 🌙/☀️ 按钮，一键切换深色/浅色主题，localStorage 持久化。

| 文件 | 改动 |
|------|------|
| `gui/src/styles/tokens.css` | 追加 `[data-theme="light"]` 变量覆盖块 |
| `gui/src/views/ChatView.vue` | 主题切换按钮 + 浅色样式覆盖 |
| `gui/src/components/Sidebar.vue` | 浅色样式覆盖 |
| `gui/src/views/ConnectionsView.vue` | 浅色样式覆盖 |
| `gui/src/components/WorkPanel.vue` | 浅色样式覆盖 |
| `gui/src/components/ActionButtons.vue` | 浅色样式覆盖 |

### Bug 修复

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | iLink 文件发送需 wxid | iLink 一对一通信，但 `_resolve_recipient` 不传 wxid 时返回空字符串 | 增加 `_bot_user_id` 兜底（`wechat_ilink.py:41-56`） |
| 2 | 任务执行记录一直"加载中" | 模板调用 `formatTime()` 但 `<script setup>` 未定义该函数，Vue 渲染异常导致 `finally` 块也执行不到 | 添加 `formatTime()` 函数（`TaskView.vue`） |
| 3 | 通知文案误导"去进化工厂审批" | `_check_channel_health()` 等通知说去进化工厂，但它们不创建 Proposal 对象 | 改为纯告知文案，去掉进化工厂引导（`idle_inspector.py`） |
| 4 | Agent 晚上说"早上好" | startup prompt 不含当前时间，agent 随机选时间问候 | prompt 中加入 `datetime.now()` 真实时间（`app.py`） |

### 进化提案不再仅启动时生成 ✅

**问题**：`_check_evolution_refinements` 只在 `_inspect_all_phases()` 启动扫描中执行一次，后台循环永不调用。进化引擎的 `get_auto_refinements()`（SOP 跳过/技能优化提案）只有重启 OAA 才能生成。

**改动**（`idle_inspector.py`）：
- 新增 `_EVOLUTION_CHECK_COOLDOWN = 21600`（6小时）
- 新增 `_last_evolution_check` 追踪变量
- 后台循环每6小时执行 `_check_evolution_refinements()` + `_check_usage_patterns()`（无过滤器，全局扫描）

### 待处理问题

| 优先级 | 类型 | 项目 | 说明 | 状态 |
|--------|------|------|------|------|
| P1 | 测试 | IdleInspector 集成测试 | 检测 → 创建提案 → GUI 可见 → 批准执行 | ⏳ 待完成 |

## 本次会话（2026-05-28）— 三大文件重构：idle_inspector / extended_tools / management

### idle_inspector.py 内部方法拆分（1268→1309 行）

原 `_check_tool_failures`（~447 行）等巨型方法拆分为专注的子方法，逻辑不变。

| 原方法 | 拆分后 |
|--------|--------|
| `_check_evolution_refinements` | 编排 + `_handle_sop_skip_refinement()` + `_handle_skill_optimize_refinement()` |
| `_check_usage_patterns` | 编排 + `_detect_crystallization()` + `_detect_sop_skips()` + `_detect_crystallized_notify()` + `_detect_usage_tool_failures()` |
| `_check_tool_failures`（~447 行） | 薄编排 + `_analyze_task_failures()` + `_analyze_unknown_task_failures()` + `_analyze_orphan_failures()` + `_analyze_unknown_orphan_failures()` |
| `_chain_display()`（嵌套函数） | 模块级函数 |
| `_extract_problem_context_tool_fix()` | 模块级函数 |

### extended_tools.py → `extended/` 包（11 Mixin + 聚合类）

**策略**：`ExtendedTools(CoreMixin, EmailMixin, …)` 聚合继承模式。

| 文件 | 行数 |
|------|------|
| `extended_tools.py`（入口） | 28 |
| `extended/core_mixin.py` | 228 |
| `extended/email_mixin.py` | 91 |
| `extended/office_mixin.py` | 172 |
| `extended/planner_mixin.py` | 33 |
| `extended/github_mixin.py` | 185 |
| `extended/skill_mixin.py` | 187 |
| `extended/mcp_mixin.py` | 103 |
| `extended/wechat_mixin.py` | 104 |
| `extended/feishu_mixin.py` | 145 |
| `extended/dingtalk_mixin.py` | 242 |
| `extended/patch_mixin.py` | 81 |

### management.py → `mgmt/` 包（9 Mixin + 聚合类）

相同聚合继承模式。因 `handle()` 使用 `getattr(self, f"_handle_{msg_type}")` 分发，所有 handler 通过 MRO 自动可寻。

| 文件 | 行数 |
|------|------|
| `management.py`（入口） | 30 |
| `mgmt/core_mixin.py` | 246 |
| `mgmt/healthcheck_mixin.py` | 85 |
| `mgmt/config_mixin.py` | 154 |
| `mgmt/evolution_mixin.py` | 357 |
| `mgmt/tasks_skills_mixin.py` | 135 |
| `mgmt/channel_mixin.py` | 175 |
| `mgmt/email_mixin.py` | 77 |
| `mgmt/preferences_mixin.py` | 54 |
| `mgmt/patches_mixin.py` | 51 |
| `mgmt/tool_failure_verifier.py` | 27 |

### 验证

- 所有 90 项现有测试通过
- `from .extended_tools import ExtendedTools` / `from .management import ManagementHandler` 导入兼容
- 无任何行为变更，纯结构拆分
- 自愈系统错误行号缓存仅存于 `~/OAA/memory/error_log.json`（运行时数据），不影响安装程序

---

## 本次会话（2026-05-30）— 静默错误审计 + 模型切换修复 + except 全面升级

### 修复

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `gateway/mgmt/config_mixin.py` | `set_config()` 方法不存在 + 回退导入路径错误 (`..llm` → `oaa.gateway.llm`) | 改为 `reconfigure()` + `...llm.client` |
| 2 | `oaa_agent.py:310` | 邮箱加载失败静默吞错误 | `logger.warning` |
| 3 | `oaa_agent.py:722` | 活跃计划加载失败静默吞错误 | `logger.warning` |
| 4 | `oaa_agent.py:1012` | 对话后复盘失败仅 debug 日志 | `logger.warning` |
| 5 | `app.py:272` | 微信巡检通知失败仅 debug | `logger.warning` |
| 6 | `idle_inspector.py` | 5 处巡检/LLM分析/磁盘/内存失败仅 debug | `logger.warning` |
| 7 | `simphtml.py` | 4 处裸 `except:`（会吞 KeyboardInterrupt） | `except Exception:` |
| 8 | `core_mixin.py:125` | 通知回调失败 `pass` | `logger.warning` |
| 9 | `evolution_mixin.py:235` | 工具验证器失败 `pass` | `logger.warning` |
| 10 | `evolution_mixin.py:331` | 提案注入失败仅 debug | `logger.warning` |
| 11 | `tools/_core.py:190` | 回滚记录失败仅 debug | `logger.warning` |
| 12 | `reflection_scheduler.py` | 6 处反射管线失败仅 debug | `logger.warning` |

### 根因

`management.py` → `mgmt/` 包重构时，`_handle_switch_model` 被移到 `oaa/gateway/mgmt/config_mixin.py`，但：
1. `set_config()` 是旧方法名（正确应为 `reconfigure()`）
2. 回退导入 `from ..llm.client` 在 `oaa.gateway.mgmt` 下解析为 `oaa.gateway.llm`（不存在），应使用 `...llm.client`

两层 `except` 把错误吞干净，导致每次切模型静默失败，`self._agent.llm` 始终是启动时的旧客户端。用户切到有额度的模型后实际请求仍走旧模型 API → 429。`except Exception` 全局审计发现 12 处类似问题，全部升级到 `logger.warning`。

## 本次会话（2026-05-30）— RepairLoop 接入自愈闭环

### 背景

自愈系统有三条路径但闭环断裂：Path A（管理触发）裸调 `process_message` 无验证无回滚，Path B（IdleInspector）检测到故障仅通知需 GUI 批准，Path C（RepairLoop）完整实现却无人调用。

### 修复的 9 项改动

| # | 文件 | 改动 | 效果 |
|---|------|------|------|
| 1 | `gateway/mgmt/evolution_mixin.py` | 验证器 `get_tool_failures` → `load_tool_failures(5)` + 按 tool 过滤 | 真实验证而非恒 True |
| 2 | `agent/repair_loop.py` | 新增 `ptype == "diagnostic"` 分支传递 `raw_prompt` | 支持预格式化诊断文本 |
| 3 | `agent/idle_inspector.py` | `pause/resume` 改为计数，防止嵌套 RepairLoop 提前恢复 | 安全嵌套 |
| 4 | `gateway/mgmt/core_mixin.py` | `_heal_callback` 签名 `str→dict` | 传递结构化 problem_context |
| 5 | `gateway/mgmt/email_mixin.py` | 传递 dict 含 `type/diagnostic_subtype/raw_prompt/account_*`，不含凭证 | 结构化诊断 + 避免泄露 |
| 6 | `app.py` | `_agent_heal` 重写为 RepairLoop，注册 subtype 感知验证器 | 获得验证+重试+回滚+inspector pause |
| 7 | `agent/idle_inspector.py` | 新增 `_auto_heal_callback` + setter + `_is_high_confidence_failure` + `_create_tool_fix_proposal` | 高置信度故障自动触发修复 |
| 8 | `app.py` | `start()` 中注册 `_auto_heal` 回调到 IdleInspector | 路径 B 自动闭环 |
| 9 | `gateway/mgmt/tool_failure_verifier.py` | 标记弃用，实际验证器已迁至 evolution_mixin/app.py | 消除误导性存根 |

### 闭环恢复状态

| 路径 | 触发方式 | 验证 | 重试 | 回滚 | Inspector pause | 自动/手动 |
|------|----------|------|------|------|----------------|-----------|
| Path A | email_mixin 测试失败 | ✅ 重测邮箱/检查失败记录 | ✅ 3次 | ✅ | ✅ | 自动 |
| Path B | IdleInspector ≥3次tool_bug/LLM确认 | ✅ load_tool_failures过滤 | ✅ 3次 | ✅ | ✅ | 自动 |
| Path C | GUI 进化工厂批准 | ✅ load_tool_failures过滤 | ✅ 3次 | ✅ | ✅ | 手动 |

### 新增文件
- `self_healing_closed_loop.png` — 闭环架构示意图


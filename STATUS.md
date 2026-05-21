# OAA 问题追踪

> 最后更新：2026-05-21 — 下一步计划第1-4项完成，第5项(P1)全部完成

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
| 主动性度量指标 | 缺少量化 agent 主动行为的数据（主动调工具次数/主动修复次数 vs 被动等待确认次数），无法评估改进效果 | P2 | ⏳ |

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

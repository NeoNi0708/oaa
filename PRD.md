# OPC AI Assistant (OAA) — 产品需求文档

> 版本：v0.1 | 日期：2026-05-09 | 状态：定稿 ✓

---

## 1. 产品概述

### 1.1 一句话定义
**OPC AI Assistant（OAA）** 是一款面向一人公司（One Person Company）的 Windows 桌面 AI Agent，以 GenericAgent 为基座，预装外贸业务技能，具备自我进化能力，帮助用户从重复性工作中解放出来。

### 1.2 产品定位
- **桌面原生**：Windows 桌面应用，系统托盘常驻，独立 GUI 窗口交互
- **开箱即用**：预装外贸业务所需的核心技能，安装配置完即可开始工作
- **自我进化**：每次任务执行后自动结晶经验，越长越聪明
- **跨通道连续对话**：桌面窗口 ↔ 微信 ↔ 钉钉 ↔ 飞书，对话不中断

### 1.3 目标用户
**恒总**（及类似 OPC 用户）：
- 一人经营联轴器出口贸易公司
- 不擅长命令行，仅能通过图形界面操作
- 需要 Agent 帮忙处理报价、跟单、搜客户、写邮件等日常业务
- 有时在电脑前工作，有时出门用手机继续

### 1.4 命名与标识
- **中文名**：OPC AI 助手
- **英文名**：OPC AI Assistant
- **简称**：OAA
- **安装程序图标**：`E:\GenericAgent\icon.ico`
- **桌面快捷方式**：指向 OAA 主程序，使用同一图标

---

## 2. 核心设计原则

| 原则 | 说明 |
|------|------|
| **单智能体 + 技能包** | 只有"二愣"一个智能体，通过切换技能包切换角色身份 |
| **技能即文件** | 每个技能是一个目录（SOP + 知识 + 模板 + 工具），可随时添加/修改 |
| **开箱有生产力** | 预置 29 个技能覆盖核心外贸场景 |
| **持续自进化** | 现有技能可被精炼优化，新技能可在执行中结晶 |
| **Windows 原生** | 不依赖云端、不依赖 Docker、不依赖 WSL |
| **数据本地** | 用户数据全部存储在本地用户指定的数据目录 |

---

## 3. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     OAA Windows Desktop                          │
│                                                                  │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐  │
│  │  Vue3 + Electron GUI   │  │  Windows 系统托盘             │  │
│  │  (聊天/技能/任务/设置)   │  │  (常驻后台/通知/快速入口)    │  │
│  └───────────┬─────────────┘  └──────────────┬───────────────┘  │
│              │  WebSocket                     │ IPC              │
├──────────────┼────────────────────────────────┼──────────────────┤
│              │   同一 Python 进程 (OAA Core)                    │
│  ┌───────────┴──────────────────────────────────────────────┐   │
│  │  Session Manager — 会话管理层                             │   │
│  │  • 统一会话 ID（不分通道）                                │   │
│  │  • 历史记录持久化（SQLite FTS5）                          │   │
│  │  • 上下文保持（桌面/微信/钉钉/飞书同一对话流）            │   │
│  └───────────────────────┬──────────────────────────────────┘   │
│                          │                                      │
│  ┌───────────────────────┴──────────────────────────────────┐   │
│  │  二愣 (Agent Core)                                       │   │
│  │                                                          │   │
│  │  → 输入 → 意图识别 → 技能包加载                          │   │
│  │  → Agent Loop (GA ~100行核心)                            │   │
│  │  → 自进化引擎（执行优化/精炼/结晶）                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  消息通道                                                       │
│  ┌──────────┬──────────────┬──────────┬──────────┐              │
│  │ 桌面 GUI │ 微信 (双通道) │ 钉钉     │ 飞书     │              │
│  │ (WS)     │ iLink+查询   │ (OAuth)  │ (OAuth)  │              │
│  └──────────┴──────────────┴──────────┴──────────┘              │
│                                                                  │
│  技能仓库                                                        │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  29 预置技能 (4 类) + user_evolved/ + ClawHub 市场          ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  工具层                                                          │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  原子工具 (GA: code_run/file_read/file_patch/...)            ││
│  │  扩展工具 (email/word/excel/skill_search/...)                ││
│  │  微信查询 (wechat-cli: sessions/history/search/contacts)     ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  基础设施层                                                      │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ 模型: 火山/DeepSeek/通义千问/Claude/OAI兼容  存储: SQLite    ││
│  │ 打包: Electron Builder (全依赖打包)         配置: 首次向导   ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 模块详细设计

### 4.1 GUI 界面（Vue3 + Electron）

#### 4.1.1 布局

```
┌──────────────────────────────────────────────────────────┐
│  OPC AI Assistant  [—] [□] [×]                          │
├───────┬──────────────────────────────────────────────────┤
│       │                                                  │
│  💬 对话│  主内容区域                                     │
│  🛠 技能 │                                                  │
│  📋 任务 │  ▶ 对话时：聊天界面                              │
│  📁 文件 │  ▶ 技能时：技能仓库/自生可能/技能市场            │
│  ⚙ 设置 │  ▶ 任务时：任务看板                              │
│       │  ▶ 文件时：工作区文件浏览器                        │
│       │  ▶ 设置时：配置面板                                │
│       │                                                  │
└───────┴──────────────────────────────────────────────────┘
```

#### 4.1.2 左侧导航

| 图标 | 标签 | 功能 |
|------|------|------|
| 💬 | 对话 | 聊天界面，与二愣对话的主入口 |
| 🛠 | 技能 | 技能仓库 / 自生可能 / 技能市场 三个子标签页 |
| 📋 | 任务 | 任务看板（待办/进行中/已完成） |
| 📁 | 文件 | 工作区文件浏览器 |
| ⚙ | 设置 | 模型配置/消息通道/数据目录/权限管理 |

#### 4.1.3 技能标签页细节

**技能仓库（本地已安装技能）**
- 按类别分组展示（外贸业务核心 / 办公文档 / 通信消息 / 系统与自进化 / 用户自生）
- 每个技能显示：名称、版本、描述、使用次数
- 操作：启用/禁用、卸载、查看详情

**自生可能（Agent 进化相关）**
- "二愣自己长出来的技能"列表（使用次数、最后使用时间）
- "二愣的建议"面板（检测到的优化机会、合并建议、拆分建议）
- 用户可一键接受或拒绝建议

**技能市场**
- 内嵌显示 `https://cn.clawhub-mirror.com`（使用 `<webview>` 或 `<iframe>`）
- 不单独打开浏览器
- Agent 可以在对话中执行 `clawhub search/install` 操作

#### 4.1.4 对话界面
- 聊天气泡风格，类似微信/钉钉
- 消息支持：文本、代码块（语法高亮）、图片、文件附件
- 二愣的消息支持 tool call 的展开/折叠显示（用户看到的是任务执行过程，而非 JSON）
- 输入框支持多行文本 + Enter 发送

### 4.2 网关层（Gateway）

#### 4.2.1 会话管理 (Session Manager)

核心逻辑：
```
消息进入 → 提取 source (desktop/wechat/dingtalk/feishu) + user_id
         → SessionManager.lookup_or_create(user_id)
         → 消息加入会话上下文队列
         → 转发给 Agent Core 处理
         → 响应回 SessionManager
         → SessionManager 根据 source 路由回对应适配器
```

数据库设计（SQLite FTS5）：
```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,       -- 'desktop' | 'wechat' | 'dingtalk' | 'feishu'
    role TEXT NOT NULL,         -- 'user' | 'assistant'
    content TEXT NOT NULL,
    metadata TEXT,              -- JSON, 含 context_token 等平台特定字段
    created_at TEXT
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, content=messages, content_rowid=id
);
```

#### 4.2.2 桌面适配器
- 通过 WebSocket 与 Vue3 GUI 通信
- JSON 消息格式：`{ type: "message" | "tool_call" | "status", payload: {...} }`
- 支持流式输出（Agent 执行过程中逐步推送结果到界面）

#### 4.2.3 微信适配器（iLink ClawBot + wechat-cli 双通道）

微信是 OAA 最重要的外部通信通道，设计上采用**双通道架构**：

**通道一：iLink ClawBot — 实时消息收发**

基于 `weixin-channel-sdk`（PyPI: `weixin-channel-sdk`）：

```
适配器启动流程：
1. 检查本地持久化的 session token
2. 无 token → 调用 GET /ilink/bot/get_bot_qrcode?bot_type=3
3. 获取二维码链接 → 转发给 GUI 显示二维码
4. 长轮询 GET /ilink/bot/get_qrcode_status?qrcode=<id>
5. 用户扫码确认 → 获取 bot_token + baseurl + bot_id
6. 持久化 token，建立长轮询消息接收循环
7. 消息收发基于 /getupdates 和 /sendmessage
```

核心 iLink 接口：

| 操作 | 接口 | 说明 |
|------|------|------|
| 获取二维码 | `GET /ilink/bot/get_bot_qrcode?bot_type=3` | 首次接入扫码 |
| 轮询扫码状态 | `GET /ilink/bot/get_qrcode_status?qrcode=<id>` | 长轮询 35s |
| 接收消息 | `POST /ilink/bot/getupdates` | 长轮询 35s |
| 发送消息 | `POST /ilink/bot/sendmessage` | 需携带 context_token |
| 输入状态 | `POST /ilink/bot/sendtyping` | 正在输入提示 |

关于主动推送限制：
- iLink 协议限制 24h 内主动推送约 10 条消息
- **用户发消息后配额立即重置**，日常使用中恒总会多次与二愣对话，实际不会触发限制
- 钉钉/飞书作为备选主动推送通道（无限制）

**通道二：wechat-cli — 本地数据查询**

集成 `wechat-cli`（`E:\wechat-cli`，已克隆到本地）作为工具链的一部分：

```
wechat-cli 启动：
1. 用户微信需保持登录状态
2. OAA 安装时自动执行 wechat-cli init → 提取解密密钥
3. 密钥持久化，后续可离线查询本地微信数据
```

注册为二愣的扩展工具：

| 工具名 | 映射命令 | 功能 |
|--------|---------|------|
| `wechat_sessions` | `wechat-cli sessions` | 获取最近会话列表 |
| `wechat_history` | `wechat-cli history <name>` | 获取指定聊天历史记录 |
| `wechat_search` | `wechat-cli search <keyword>` | 全局搜索聊天内容 |
| `wechat_contacts` | `wechat-cli contacts` | 搜索/列出联系人 |
| `wechat_unread` | `wechat-cli unread` | 查看未读消息 |
| `wechat_stats` | `wechat-cli stats` | 消息统计（发言排行/活跃图） |
| `wechat_export` | `wechat-cli export` | 导出聊天记录 |

**双通道协同场景：**

```
场景一：实时对话
  恒总微信发："二愣，ORD-001 怎么样了"
  → iLink 通道实时接收
  → 二愣加载 follow-up 技能，检查订单档案
  → iLink 通道回复

场景二：搜索历史
  恒总："查一下和越南ABC公司之前聊过什么"
  → 二愣调 wechat-cli search "ABC" --limit 20
  → 直接读本地加密数据库，即时返回结果

场景三：数据汇总
  恒总："统计一下最近一周客户沟通情况"
  → wechat-cli stats --days 7
  → 结合工作区数据生成报告

场景四：主动通知（突破主动推送限制）
  iLink 的主动推送配额主要由回复消耗
  如需紧急通知且配额用尽 → 走钉钉或飞书通道
```

#### 4.2.4 钉钉适配器
- 钉钉开放平台创建企业内部应用
- OAuth 2.0 网页扫码登录 → 获取 access_token
- 基于钉钉官方 API 收发消息（webhook 或主动轮询）
- 消息格式转换为统一内部格式

#### 4.2.5 飞书适配器
- 飞书开放平台创建自建应用
- 网页扫码授权 → 获取 tenant_access_token
- 基于飞书官方 API 收发消息

### 4.3 智能体层（二愣）

#### 4.3.1 身份系统

二愣的身份由以下文件定义（借鉴你已有的设计）：

```
data/memory/
├── IDENTITY.md        — 名称、缩写、核心信念
├── SOUL.md            — 工作哲学、价值观、行为原则
├── USER.md            — 用户信息（称呼、偏好等）
├── BOOTSTRAP.md       — 启动自我介绍
├── HEARTBEAT.md       — 健康检查和自我维护
└── AGENTS.md          — 工作边界和决策原则
```

这些文件构成二愣的**永久人格**，不会被技能覆盖。

#### 4.3.2 技能切换机制

```
用户输入 → Intent Recognizer → 匹配技能包
                                  │
                          ┌───────┴───────┐
                          │               │
                    匹配成功           匹配失败
                          │               │
                   加载技能包内容     使用默认技能包
                   (SKILL/SOP/知识)  (仅原子工具)
                          │               │
                          └───────┬───────┘
                                  │
                    注入系统提示词（二愣人格 + 技能角色）
                    注入工作记忆（SOP 步骤作为 checkpoint）
                    注入扩展工具（技能注册的额外工具）
                                  │
                        Agent Loop 开始执行
```

技能匹配基于关键词 + 语义分析：
- "报价"、"PI"、"合同" → business-assistant
- "汇率"、"利润"、"报价计算" → finance
- "跟单"、"催货"、"物流" → follow-up
- "搜客户"、"开发信"、"线索" → market-researcher
- 等等

#### 4.3.3 自进化引擎

三个层次的自进化：

**层次一：执行优化（自动）**
- 检测 SOP 步骤的跳过模式
- 记录常用参数默认值
- 分析用户反馈调整行为

**层次二：主动精炼（建议）**
- 同一 SOP 执行 N 次后提出优化建议
- 检测到重复模式 → 建议结晶为新技能
- 技能使用频率分析 → 建议合并或拆分

**层次三：技能结晶（从0到1）**
- 记录任务执行轨迹
- 提取通用步骤 → 生成 SOP
- 保存到 `skills/user_evolved/` 目录

#### 4.3.4 跨天规划器

二愣的默认工作方式是"接收指令→LLM即时推理→执行→结束"，这对于一步到位的任务（查汇率、做报价单）足够。但对于跨天、多步骤的任务（"开发越南市场"），需要显式规划。

**规划器工作机制：**

```
恒总周一："开发一下越南市场"
  → 二愣：检测到这是一个多步骤任务，不是一句话能干完的
  → 调用 plan_create()
  → 自动拆解步骤，保存 plan 文件
  → 开始执行第 1 步

恒总周三："继续越南那个"
  → 调用 plan_list() → 找到进行中的计划
  → "开发越南市场做到第 3 步了，开发信草稿写好了，要发吗？"
```

**Plan 文件格式：**
```json
{
  "id": "plan_dev_vn_20260509",
  "goal": "开发越南联轴器市场",
  "created": "2026-05-09",
  "updated": "2026-05-11",
  "status": "in_progress",
  "steps": [
    {"id": 1, "task": "搜索越南买家线索",      "status": "done",    "result": "找到 15 个线索"},
    {"id": 2, "task": "筛选高质量线索",        "status": "done",    "result": "筛选出 5 个 high 置信度"},
    {"id": 3, "task": "写开发信",              "status": "in_progress"},
    {"id": 4, "task": "等待人工确认后发送",     "status": "pending", "blocked_by": [3]},
    {"id": 5, "task": "跟进回复",              "status": "pending", "blocked_by": [4]}
  ]
}
```

**三个工具：**
- `plan_create(goal, steps[])` — 创建计划，自动保存到 `workspace/plans/`
- `plan_update(step_id, status, result?)` — 更新某一步状态
- `plan_list(status?)` — 列出进行中/已完成/全部计划

**与自进化的关系：** 规划器和自进化引擎是两层不同粒度——规划器管"这个任务怎么做"，自进化管"这类任务下次怎么做"。一次成功的规划执行，为自进化提供了完整轨迹素材。

### 4.4 技能系统

#### 4.4.1 技能包结构

```
skills/<category>/<skill-name>/
├── SKILL.md          — 元信息 + 角色描述（YAML frontmatter + markdown）
├── SOP.md            — 工作流程（可选，复杂任务用）
├── knowledge/        — 领域知识（HS 编码表、贸易术语等）
├── templates/        — 文件模板（报价单模板、邮件模板等）
└── tools.json        — 此技能注册的额外工具描述（可选）
```

#### 4.4.2 预置技能完整清单

见本文档第 7 节。

#### 4.4.3 技能市场
- 基于 ClawHub 协议
- Agent 端命令：`clawhub search <keyword>` / `clawhub install <slug>`
- GUI 内嵌 https://cn.clawhub-mirror.com 供用户浏览

### 4.5 工具层

#### 4.5.1 原子工具（移植自 GenericAgent）

| 工具 | 功能 | 来源文件 |
|------|------|---------|
| `code_run` | 执行 Python/PowerShell 代码 | ga.py:12 |
| `file_read` | 读文件 | ga.py |
| `file_patch` | 精确替换文件内容 | ga.py |
| `file_write` | 创建/覆盖/追加文件 | ga.py |
| `web_scan` | 浏览器页面扫描 | ga.py |
| `web_execute_js` | 浏览器 JS 注入 | ga.py |
| `update_working_checkpoint` | 工作记忆更新 | ga.py |
| `ask_user` | 询问用户 | ga.py |
| `start_long_term_update` | 触发长时记忆蒸馏 | ga.py |

#### 4.5.2 扩展工具（新增）

| 工具 | 功能 | 依赖 |
|------|------|------|
| `email_send` | 通过 SMTP 发送邮件 | himalaya 或 smtplib |
| `wechat_send` | 通过 iLink API 发送微信消息 | weixin-channel-sdk |
| `dingtalk_send` | 发送钉钉消息 | 钉钉 SDK |
| `feishu_send` | 发送飞书消息 | 飞书 SDK |
| `word_doc` | 生成 Word 文档 | python-docx |
| `excel_xlsx` | 生成 Excel 表格 | openpyxl |
| `skill_search` | 搜索技能市场 | clawhub CLI |
| `skill_install` | 安装技能 | clawhub CLI |
| `load_skill` | 运行时切换技能包 | 内部 API |
| `plan_create` | 创建多步骤执行计划，含 DAG 任务依赖 | 内部 API |
| `plan_update` | 更新计划步骤状态（done/failed/blocked） | 内部 API |
| `plan_list` | 列出进行中的计划（用于跨天恢复上下文） | 内部 API |

### 4.6 模型适配器

用户可在设置中配置：
- **Base URL**（预设国内常用：火山引擎、DeepSeek、通义千问、硅基流动等）
- **API Key**
- **Model ID**
- 支持 OpenAI 兼容格式的所有 Provider

首次启动向导引导用户完成模型配置，提供预设选项供选择。

---

## 5. 安装与部署

### 5.1 安装程序
- 使用 Inno Setup 或 NSIS 打包
- 安装程序图标：`icon.ico`
- 用户可以选安装路径（默认 `C:\Program Files\OAA`）
- 创建桌面快捷方式（使用 `icon.ico`）
- 可选：开机自启（后台托盘常驻）

### 5.2 首次启动向导
1. 欢迎页（展示 OAA 名称 + 图标 + 一句话介绍）
2. 选择数据目录（用户选一个根目录，自动创建 workspace/ 和 memory/）
3. 配置模型 Provider（从预设列表选一个 + 填 Key + 选模型）
4. 二愣自我介绍（加载 BOOTSTRAP.md）
5. 进入主界面

### 5.3 数据目录结构

```
<data_dir>/
├── workspace/           — 用户业务文件
│   ├── documents/       — 报价单、合同、PI 等生成文件
│   ├── clients/         — 客户信息
│   ├── orders/          — 订单档案
│   ├── leads/           — 线索卡
│   ├── vendors/         — 厂商信息
│   ├── reports/         — 分析报告
│   ├── finance/         — 财务台账
│   ├── rfq/             — 询盘相关
│   ├── plans/           — 跨天执行计划（plan_create 输出）
│   └── ... (用户自由使用)
├── memory/              — Agent 记忆
│   ├── IDENTITY.md      — 二愣人格
│   ├── SOUL.md          — 工作哲学
│   ├── USER.md          — 用户信息
│   ├── global_mem.txt   — L2 全局记忆
│   ├── L4_raw_sessions/ — L4 会话归档
│   └── ... (自进化数据)
├── skills/              — 技能仓库
│   ├── 外贸业务核心/
│   ├── 办公文档/
│   ├── 通信消息/
│   ├── 系统与自进化/
│   └── user_evolved/    — 自生技能
└── db/                  — 数据库
    └── oaa.db           — SQLite（会话、消息、配置）
```

### 5.4 权限管理
- Agent 在数据目录内默认完全权限
- 用户可在设置中配置黑名单路径（Agent 不可访问）
- 高风险操作（发邮件、发消息、网络请求）可在设置中要求确认
- 首次执行特定类操作时提示用户设置权限偏好

---

## 6. 用户使用流程

### 6.1 典型的一天

```
早晨 9:00 — 恒总打开电脑
  系统托盘 OAA 图标常驻（开机自启）
  打开 GUI 窗口 → 对话界面

恒总："二愣，昨晚越南客户有回邮件吗？"
  二愣 → 检查邮箱 → "有一封，胡志明市的 ABC Machinery 回复了开发信，
  说对梅花联轴器感兴趣。要不要我跟进？"
  恒总："跟一下"

  二愣 → 加载 customer-support 技能包
       → 读取客户邮件
       → 生成回复草稿
       → "回复草稿写好了，要发吗？"
  恒总："发"

上午 10:30 — 恒总出门
  路上用手机微信打开
  发微信："二愣，ORD-001 生产进度怎么样了？"
  二愣 → 加载 follow-up 技能包
       → 检查订单档案
       → "ORD-001 目前已完成 80%，厂商说后天可以交货。
        需要在交期前 3 天发友好提醒给厂商吗？"
  恒总："发吧"

下午 2:00 — 回到电脑前
  打开电脑 GUI，对话记录自动同步
  （桌面看到的和微信上的聊天是同一个会话流）

下午 4:00 — "二愣，搜一下越南市场做水泵的客户"
  二愣 → 加载 market-researcher 技能包
       → 执行三级搜索
       → 输出线索卡
       → "搜到 8 个潜在客户，要不要看看详情？"
```

### 6.2 自进化示例

```
场景一：报价单流程优化
  第一次：二愣按 business-assistant/SOP.md 一步步生成报价单
  第五次：二愣发现恒总每次都让 TA 用同样的银行信息
         → 自动将银行信息固化到 memory 中，以后不再问
  第十次：二愣发现恒总对报价单的格式有固定偏好
         → 优化 SOP，跳过格式确认步骤

场景二：新技能诞生
  恒总："二愣，帮我做一下这个产品的运费对比"
  二愣 → 按步骤完成
  下次恒总再说同样的话
  二愣 → 检测到模式 → "我总结了一个运费对比的技能，要不要保存？"
  恒总同意 → 技能结晶 → 存入 user_evolved/
```

---

## 7. 预置技能完整清单

### 类别 A：外贸业务核心（16 技能）

| # | 技能名 | 来源路径 | 对应角色 | 功能简述 |
|---|--------|---------|---------|---------|
| 1 | `foreign-trade-general` | `skills/外贸业务技能` | 业务总纲 | 领域边界和行为约束，始终加载 |
| 2 | `business-assistant` | `skills/business-assistant` | 商务助理 | 报价单/合同/PI/装箱单生成 |
| 3 | `quotation-maker` | `skills/quotation_maker` | 商务助理 | 同功能备选，偏报价计算 |
| 4 | `contract-review` | `skills/contract_review` | 商务助理 | 合同条款审核 |
| 5 | `customer-support` | `skills/customer-support` | 客户支持 | 询盘回复流程和话术 |
| 6 | `email-writer` | `skills/email-writer-2.0.0` | 客户支持 | 中英双语邮件模板 |
| 7 | `inquiry-handling` | `skills/inquiry_handling` | 客户支持 | 询盘分类识别（同功能备选） |
| 8 | `customer-relationship` | `skills/customer_relationship` | 客户支持 | 客户建档和分类管理 |
| 9 | `finance` | `skills/finance` | 财务助手 | FOB/CIF 计算、汇率、台账 |
| 10 | `follow-up` | `skills/follow-up` | 跟单员 | 12 节点全程跟踪 |
| 11 | `logistics-coordination` | `skills/logistics_coordination` | 跟单员 | 纯物流视角（同功能备选） |
| 12 | `market-analyst` | `skills/market-analyst` | 市场分析师 | 行情分析、月度简报 |
| 13 | `market-researcher` | `skills/market-researcher` | 市场调研员 | 三级搜索、线索卡、开发信 |
| 14 | `outreach-prospecting` | `skills/outreach-and-prospecting` | 市场调研员 | 英文地区冷开发（同功能备选） |
| 15 | `purchaser` | `skills/purchaser` | 采购专员 | 5 步需求解析+厂商搜索+询盘 |
| 16 | `search-execution` | `skills/search_execution` | 采购专员 | 纯搜索策略工具 |

### 类别 B：办公文档工具（3 技能）

| # | 技能名 | 来源路径 | 功能简述 |
|---|--------|---------|---------|
| 17 | `word-docx` | `skills/word-docx-1.0.2` | Word 文档生成 |
| 18 | `excel-xlsx` | `skills/excel-xlsx-1.0.2` | Excel 表格生成（报价单/装箱单核心） |
| 19 | `nano-pdf` | `skills/nano-pdf-1.0.0` | PDF 读写 |

### 类别 C：通信消息（3 技能）

| # | 技能名 | 来源路径 | 功能简述 |
|---|--------|---------|---------|
| 20 | `himalaya-email` | `skills/himalaya` | 终端邮件发送（IMAP/SMTP） |
| 21 | `wechat-cli` | `E:/wechat-cli` | 微信本地数据查询（历史/搜索/联系/统计） |
| 22 | `clawhub` | `skills/clawhub` | 技能市场访问 |

### 类别 D：系统与自进化（7 技能）

| # | 技能名 | 来源路径 | 功能简述 |
|---|--------|---------|---------|
| 23 | `self-improving` | `skills/self-improving-1.2.16` | 自我反思→纠正→学习循环 |
| 24 | `agent-autonomy-kit` | `skills/agent-autonomy-kit-1.0.0` | 任务队列+主动推进 |
| 25 | `skill-creator` | `skills/skill-creator` | 技能创建工具 |
| 26 | `agent-memory` | `skills/agent-memory-1.0.0` | 分层记忆管理 |
| 27 | `bb-browser` | `skills/bb-browser/skills/bb-browser-openclaw` | 浏览器自动化 |
| 28 | `weather` | `skills/weather` | 天气查询 |
| 29 | `summarize` | `skills/summarize` | 内容摘要 |

**合计：29 个预置技能**（含 4 组同功能备选，用户可按偏好选择主要使用的版本）

---

## 8. 技术选型

| 层级 | 技术 | 理由 |
|------|------|------|
| GUI 框架 | Vue3 + Electron | 用户确认。Electron Builder 全量打包（含Python runtime + Node + npm） |
| 网关+Agent | 同一 Python 3.10+ asyncio 进程 | 不拆两个进程，减少 IPC 复杂度 |
| 会话存储 | SQLite + FTS5 | 零依赖，本地化，全文搜索 |
| 模型协议 | OpenAI 兼容 API | 火山引擎/DeepSeek/通义千问/Claude 均支持 |
| 打包 | Electron Builder | 全依赖打包，用户一键安装 |
| 微信实时 | weixin-channel-sdk (PyPI) | iLink 协议，收发消息 |
| 微信查询 | wechat-cli (本地项目) | 本地数据解密查询 |
| 钉钉 | dingtalk-stream SDK | 官方 SDK |
| 飞书 | lark-oapi | 官方 SDK |
| Word 生成 | python-docx | 成熟稳定 |
| Excel 生成 | openpyxl | 成熟稳定 |

---

## 9. 风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| 微信 iLink API 政策变化 | 微信通道不可用 | 适配器架构隔离变化；钉钉/飞书为稳定主力通道 |
| 模型 API 成本高 | 用户不愿使用 | 支持多模型切换；火山引擎 Coding Plan 有免费额度 |
| 预置技能与用户实际流程不匹配 | 用户觉得不好用 | 自进化引擎可调整技能；用户可手动编辑 SOP |
| 数据安全 | 担心敏感数据泄露 | 数据全本地存储；用户可配置权限边界 |
| Windows 兼容性问题 | 某些 Win 版本不兼容 | 目标 Win 10/11，安装时检测系统版本 |
| 技能市场依赖 ClawHub | 外部服务不可用 | 核心功能不依赖技能市场；预置技能已覆盖高频场景 |

---

## 10. 版本规划

### v0.1 — MVP（最小可行产品）
- [x] 基于 GenericAgent 的 Agent Loop + 原子工具
- [x] 二愣身份系统（IDENTITY/SOUL/USER/BOOTSTRAP）
- [x] 技能包加载机制（运行时切换 SKILL.md + SOP）
- [x] 预置 29 个技能
- [x] 跨天规划器（plan_create/update/list）
- [x] Windows 系统托盘
- [x] 模型配置（火山引擎 + 预设 Provider 列表）
- [x] 首次启动向导（选数据目录 + 配模型）

### v0.2 — GUI + 可交互
- [x] Vue3 + Electron 桌面 GUI
- [x] 对话界面（聊天气泡 + 流式输出）
- [x] 导航栏（对话/技能/任务/文件/设置）
- [x] 技能标签页（技能仓库展示）
- [x] 任务看板（展示进行中的 plan + 步骤进度）
- [x] 设置页面（模型/数据目录/权限）

### v0.3 — 通信网关
- [x] 统一网关进程（Session Manager）
- [x] 桌面 ↔ Gateway WebSocket 通信
- [x] 微信 iLink ClawBot 接入（扫码登录 + 消息收发）
- [x] 钉钉接入（扫码登录 + 消息收发）
- [x] 飞书接入（扫码登录 + 消息收发）
- [x] SQLite 会话历史和 FTS5 搜索

### v0.4 — 技能生态
- [x] 技能市场内嵌显示（iframe/webview）
- [x] Agent 自主搜索安装技能（clawhub CLI）
- [x] 自生可能页面（进化建议 + 新技能展示）
- [x] 技能精炼流程（用户确认后优化 SOP）

### v0.5 — 精炼与稳定
- [x] 自进化引擎（执行优化 + 主动精炼 + 技能结晶）
- [x] 任务看板（进行中/待办/已完成）
- [x] 文件浏览器（工作区文件管理）
- [x] 权限管理（黑名单路径 + 操作确认）
- [x] 系统托盘右键菜单
- [x] 安装程序（Inno Setup + icon.ico）

---

## 11. 附录

### A. 二愣人格参考

借鉴你在 `E:\Agent\main\` 中的设计：

- **称呼**：二愣
- **用户称呼**：恒总（可配置）
- **核心信念**：帮助恒总把生意做得更顺、更赚钱、更省心
- **工作精神**：靠谱、主动、尊重、反思
- **决策原则**：不确定就请示、先学习再自主、保护利益与隐私、主动汇报但避免打扰

### B. 术语表

| 术语 | 说明 |
|------|------|
| OAA | OPC AI Assistant 的缩写 |
| OPC | One Person Company，一人公司 |
| Skill/SOP | 技能包，含角色描述和工作流程 |
| 技能结晶 | 从任务执行轨迹中提取通用步骤生成新技能 |
| 技能精炼 | 优化已有技能的执行流程 |
| iLink | 微信 ClawBot 的底层协议 |
| Gateway | 统一网关进程，管理消息路由和会话 |

### C. 参考项目

- **GenericAgent** (https://github.com/lsdefine/GenericAgent) — 基座，Agent Loop + 9 原子工具 + 自进化机制
- **HermesAgent** (https://github.com/NousResearch/hermes-agent) — 参考其网关架构、工具注册、会话搜索等设计模式
- **ClawHub** (https://cn.clawhub-mirror.com) — 技能市场
- **weixin-channel-sdk** (https://pypi.org/project/weixin-channel-sdk/) — 微信 iLink 协议 Python SDK

---

> 文档结束。如有修改需求，请直接指出需要调整的章节。

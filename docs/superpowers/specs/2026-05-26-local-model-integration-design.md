# 本地模型集成设计 (愣小二)

## 概述

将 BitCPM4-1B-q4_0 GGUF 模型及其推理引擎 llama-server 集成到 OAA 中，使其作为辅助 AI 助手（"愣小二"），承担两类角色：

1. **快速应答** — evaluator 将简单任务路由到本地模型直接回复，零 API 成本
2. **Agent 子任务** — 云端大模型在处理复杂任务时，通过 `call_xiaoer` 工具调用本地模型处理翻译/提取/格式化等子任务

---

## 一、整体架构

```
用户输入 → ComplexityEvaluator ─┬→ 本地路径（愣小二）→ 质量门禁 ─→ 输出
                                │                              │
                                │                    ┌─ 降级 ──┘
                                │                    ▼
                                └→ 云端路径（二愣 + agent loop）
                                              │
                                              ▼
                                    必要时调 call_xiaoer 干杂活
```

### 路由决策

| 层级 | 规则 | 权重 | 说明 |
|------|------|------|------|
| P0 | `@local` / `@cloud` | 强制 | 用户显式指令，不参与评分 |
| P1 | 本地关键词 | +0.6 | 翻译/总结/提取/生成代码 + 英文等价词，单条封顶 |
| P2 | 云端分析类关键词 | -0.5 | 分析/对比/评估/推理，可叠加 |
| P3 | 云端创作类关键词 | -0.3 | 创作/设计/策划/制定，可叠加 |
| P4 | 外部知识/步骤模式 | -0.5/-0.3 | P4a: 汇率/搜索/查询 -0.5; P4b: 先…再…然后 -0.3 |
| Session | 反馈黑名单 | 强制 | 用户 @cloud 纠正历史黑名单 |
| P6 | 默认 | — | score > +0.3 → local，否则 cloud |

---

## 二、模组详情

### 2.1 ComplexityEvaluator

**文件:** `oaa/agent/complexity_evaluator.py`

```python
class RouteDecision:
    route: str          # "local" | "cloud"
    score: float        # -1.0 ~ 1.0
    reasons: list[str]  # 命中规则说明
    override: bool      # P0 强制路由标记

class ComplexityEvaluator:
    def evaluate(self, text: str) -> RouteDecision
    def record_correction(self, text: str)  # session 黑名单记录
```

- P0: `@local`/`@cloud` 关键词检测 → 直接返回强制路由
- P1: 本地关键词命中 +0.6（单条封顶）
- P2~P4: 云端关键词分组加权，可叠加
- Session 黑名单命中 → 直接 cloud
- 阈值 0.3（可配置）

### 2.2 本地模型身份（愣小二）

**极简 system prompt:**

```
你是愣小二，二愣（AI 助手）的得力小弟。

你能做:
- 翻译、总结、提取信息、分类整理
- 编写简单代码、格式化输出
- 回答常识问题（不需查资料）

你不能做（说"这个得叫我大哥来"）:
- 数学计算、复杂推理
- 查资料、搜索、读文件
- 分析商业问题、多步推理

回答简短直接，不确定不强答。
```

### 2.3 本地调用路径

**文件:** `oaa/agent/loop.py` 新增 `_run_local()` 方法

- 不走 agent loop，直接单次 LLM chat 调用
- 无 tool calling 能力
- 上下文 32K，按固定策略截断：
  - 用户输入超过 24K tokens → 截断至 24K（留 8K 给输出），仅限本地路径
  - 云端路径不受此限制
- 首次本地请求如果 tps < 2.0，自动将 context_size 减半并重启 llama-server（硬模式下立刻降级云端，不重启）
- 结果经过质量门禁

**质量门禁（`_check_quality`）:**

| 检查 | 触发条件 | 处理 |
|------|----------|------|
| 空输出 | len < 5 tokens | 降级云端 |
| 重复循环 | n-gram 重复检测 | 降级云端 |
| 模型认输 | 包含"叫大哥来"等短语 | 降级云端 |
| 与输入无关 | 关键词重叠率 < 0.1 | 降级云端 |

### 2.4 Agent 工具调用（call_xiaoer）

**工具名:** `call_xiaoer`

云端 agent loop 中可调用的原子工具，接受 `prompt` 参数，调用本地模型处理子任务后返回结果。用途：翻译、提取、格式化等云端模型的杂活。

### 2.5 配置结构（LocalModelConfig）

**文件:** `oaa/config.py`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | False | 总开关 |
| model_path | str | "" | 留空自动查找 |
| port | int | 8080 | llama-server 端口 |
| context_size | int | 32768 | 32K 上限，动态调整 |
| gpu_layers | int | -1 | -1=自动，0=CPU，>0=指定 |
| confidence_threshold | float | 0.3 | evaluator 阈值 |
| fallback_on_failure | bool | True | 失败自动降级 |
| keywords_local | list | [...] | P1 关键词 |
| keywords_cloud_analysis | list | [...] | P2 分析类 |
| keywords_cloud_creation | list | [...] | P3 创作类 |
| keywords_cloud_external | list | [...] | P4 外部知识 |
| keywords_step | list | [...] | 步骤模式 regex |
| local_calls | int | 0 | 统计 |
| cloud_calls | int | 0 | 统计 |
| tokens_saved | int | 0 | 统计 |
| fallback_count | int | 0 | 统计 |

### 2.6 生命周期管理

**启动（`app.py`）:**

- `start()` 中 `create_task(_start_local_llm())` 后台启动，不阻塞主流程
- GPU 自动检测（CUDA CC >= 5.0 → GPU，否则 CPU）
- 启动失败不影响主服务，evaluator 判 local 时自动降级云端

**关闭（`app.py`）:**

- `stop()` 中 terminate llama-server 进程

**自动安装（`init.py`）:**

- `ensure_local_llm()` 检查 GGUF 和 llama-server，缺失则从 HuggingFace/GitHub 下载
- 沿用现有 `ensure_bundled_cli()` 模式

### 2.7 Management API

**新增端点:**

| Type | 说明 |
|------|------|
| `get_local_model_config` | 读取配置 + 统计 |
| `save_local_model_config` | 保存配置，启停 llama-server |

**状态扩展:**

`get_status` 响应追加 `local_model` 字段（enabled/running/统计）。

### 2.8 GUI 改动

#### 消息气泡路由 Badge

每条 assistant 消息右上角显示 `🏠 愣小二` 或 `☁️ 大哥` 标记来源。

#### Header 状态指示

模型选择器旁显示 `🟢 愣小二` 状态灯（绿/红/灰），悬浮 tooltip 显示今日统计。

#### 配置页

导航栏新增独立配置页面，展示：
- 启用开关
- 模型/引擎/上下文信息
- 置信度阈值滑块
- 四组关键词（标签式编辑，可增删）
- 今日统计（调用次数、节省 tokens、降级次数）

#### 输入框快捷切换

输入框上方 `[⚡ 自动] [💪 云端] [🔋 本地]` 三个按钮，手动选择等价于自动加 `@local`/`@cloud`。

---

## 三、与现有系统的关系

| 现有组件 | 影响 |
|----------|------|
| `loop.py` `run()` | 入口加 evaluator 判断，新增 `_run_local()` 方法 |
| `config.py` | 新增 `LocalModelConfig` dataclass + `AppConfig.local_model` |
| `app.py` | `start()`/`stop()` 加 llama-server 管理 |
| `init.py` | 新增 `ensure_local_llm()` |
| `management.py` | 2 个新 handler + 状态扩展 |
| `ChatView.vue` | 消息 badge + header 指示 + 快捷切换 |
| 配置页（Vue） | 全新页面 |
| `oaa/llm/client.py` | 无变更（OpenAI 兼容 API） |
| `scripts/local_llm_manager.py` | 沿用已有代码（GPU 检测 + server 管理） |

---

## 四、未包含/未来可做

- 本地模型 OTA 更新
- 本地模型输出评分与增量学习
- 多本地模型切换
- 本地模型训练微调接口

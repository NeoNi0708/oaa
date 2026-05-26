# 愣小二（BitCPM4-1B 本地模型）集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 BitCPM4-1B-q4_0 GGUF 模型集成到 OAA 作为本地 AI 助手"愣小二"，支持 evaluator 路由和 agent 子任务调用。

**Architecture:** 在现有 OAA 框架上新增 ComplexityEvaluator 路由引擎，本地路径不走 agent loop，直接单次 LLM 调用 + 质量门禁。本地模型通过 llama-server 提供 OpenAI 兼容 API，复用已有 `LLMClient`。

**Tech Stack:** Python GGUF / llama.cpp / llama-server / Vue 3 / WebSocket

---

## 文件结构

### 修改的文件

| 文件 | 责任 |
|------|------|
| `oaa/config.py` | 新增 `LocalModelConfig` dataclass |
| `oaa/agent/complexity_evaluator.py` | **新建** — 路由引擎 |
| `oaa/agent/oaa_agent.py` | `process_message()` 入口加 evaluator 判断，新增本地调用路径 |
| `oaa/agent/extended_tools.py` | 新增 `do_call_xiaoer()` 工具 |
| `oaa/agent/tool_schema.py` | 新增 `call_xiaoer` 工具 schema |
| `oaa/init.py` | 新增 `ensure_local_llm()` |
| `oaa/app.py` | llama-server 生命周期管理 |
| `oaa/gateway/management.py` | `get_local_model_config` / `save_local_model_config` + 状态扩展 |
| `gui/src/App.vue` | 注册新页面组件 |
| `gui/src/components/Sidebar.vue` | 添加导航项 |
| `gui/src/views/LocalModelView.vue` | **新建** — 配置页 |
| `gui/src/views/ChatView.vue` | 路由 Badge + Header 指示 + 输入切换 |
| `gui/src/composables/useWebSocket.ts` | 消息类型扩展 |

---

### Task 1: 配置结构 — LocalModelConfig

**Files:**
- Modify: `oaa/config.py`（在 `ImageGenConfig` 后追加）

- [ ] **Step 1: 添加 LocalModelConfig dataclass**

在 `ImageGenConfig` 定义之后追加：

```python
@dataclass
class LocalModelConfig:
    enabled: bool = False
    model_path: str = ""
    port: int = 8080
    context_size: int = 32768
    gpu_layers: int = -1
    confidence_threshold: float = 0.3
    fallback_on_failure: bool = True
    keywords_local: list = field(default_factory=lambda: [
        "翻译", "总结", "提取", "分类", "整理",
        "编写", "生成", "列出", "列举", "改写",
        "translate", "summarize", "extract", "list",
    ])
    keywords_cloud_analysis: list = field(default_factory=lambda: [
        "分析", "对比", "评估", "预测", "推理",
        "优化", "诊断", "investigate", "analyze",
    ])
    keywords_cloud_creation: list = field(default_factory=lambda: [
        "创作", "设计", "策划", "制定", "撰写",
        "方案", "计划", "报告", "proposal",
    ])
    keywords_cloud_external: list = field(default_factory=lambda: [
        "汇率", "关税", "政策", "新闻", "天气",
        "股价", "搜索", "查询", "找一下",
    ])
    keywords_step: list = field(default_factory=lambda: [
        r"先.*再", r"首先.*然后", r"第一步.*第二步",
    ])
    local_calls: int = 0
    cloud_calls: int = 0
    tokens_saved: int = 0
    fallback_count: int = 0
```

- [ ] **Step 2: 在 AppConfig 中注册**

```python
# 在 AppConfig 的字段列表追加
local_model: LocalModelConfig = field(default_factory=LocalModelConfig)
```

- [ ] **Step 3: 在 AppConfig.load() 中还原**

```python
# 在 load() 方法的 return 之前加
local_model = LocalModelConfig(**data.get("local_model", {}))
# 在 return cls(...) 中加入 local_model=local_model
```

- [ ] **Step 4: 在 to_redacted_dict() 中添加支持**

将 `local_model` 字段转为 dict 输出（无敏感信息，不需要红化处理）。

- [ ] **Step 5: 提交**

```bash
git add oaa/config.py
git commit -m "feat: add LocalModelConfig dataclass"
```

---

### Task 2: ComplexityEvaluator 路由引擎

**Files:**
- Create: `oaa/agent/complexity_evaluator.py`

- [ ] **Step 1: 创建 RouteDecision 和 ComplexityEvaluator**

```python
# oaa/agent/complexity_evaluator.py
import re
from dataclasses import dataclass, field


@dataclass
class RouteDecision:
    route: str          # "local" | "cloud"
    score: float        # -1.0 ~ 1.0
    reasons: list[str] = field(default_factory=list)
    override: bool = False  # P0 强制路由


class ComplexityEvaluator:
    """优先级路由引擎 — 根据关键词/规则判断请求走本地还是云端。

    优先级链:
      P0: @local/@cloud → 强制路由
      P1: 本地关键词 → +0.6（单条封顶）
      P2: 云端分析类 → -0.5（可叠加）
      P3: 云端创作类 → -0.3（可叠加）
      P4: 外部知识 → -0.5 / 步骤模式 → -0.3
      Session黑名单 → 强制 cloud
      P6: score > +0.3 → local, else cloud
    """

    def __init__(self, config: dict):
        self._threshold = float(config.get("confidence_threshold", 0.3))
        self._local_kw = config.get("keywords_local", [])
        self._cloud_analysis = config.get("keywords_cloud_analysis", [])
        self._cloud_creation = config.get("keywords_cloud_creation", [])
        self._cloud_external = config.get("keywords_cloud_external", [])
        self._step_patterns = config.get("keywords_step", [])
        self._override_re = re.compile(r"@(local|cloud)\b")
        self._compiled_steps = [re.compile(p, re.IGNORECASE) for p in self._step_patterns]
        # Session 黑名单（不持久化）
        self._session_blacklist: list[str] = []

    def evaluate(self, text: str) -> RouteDecision:
        # P0: 检查显式指令
        override_m = self._override_re.search(text)
        if override_m:
            return RouteDecision(
                route=override_m.group(1),
                score=1.0 if override_m.group(1) == "local" else -1.0,
                override=True,
                reasons=[f"用户显式指定: @{override_m.group(1)}"],
            )

        # Session 黑名单
        clean = text.lower()
        for pattern in self._session_blacklist:
            if pattern in clean:
                return RouteDecision(
                    route="cloud", score=-1.0,
                    reasons=[f"命中 session 黑名单: {pattern}"],
                )

        score = 0.0
        reasons = []

        # P1: 本地关键词
        if any(kw in clean for kw in self._local_kw):
            score += 0.6
            reasons.append("本地关键词命中")

        # P2: 云端分析类
        analysis_hits = [kw for kw in self._cloud_analysis if kw in clean]
        if analysis_hits:
            score -= 0.5
            reasons.append(f"分析类关键词: {','.join(analysis_hits[:3])}")

        # P3: 云端创作类
        creation_hits = [kw for kw in self._cloud_creation if kw in clean]
        if creation_hits:
            score -= 0.3
            reasons.append(f"创作类关键词: {','.join(creation_hits[:3])}")

        # P4a: 外部知识
        external_hits = [kw for kw in self._cloud_external if kw in clean]
        if external_hits:
            score -= 0.5
            reasons.append(f"外部知识: {','.join(external_hits[:3])}")

        # P4b: 步骤模式
        step_matched = any(p.search(clean) for p in self._compiled_steps)
        if step_matched:
            score -= 0.3
            reasons.append("步骤模式匹配")

        route = "local" if score > self._threshold else "cloud"
        return RouteDecision(route=route, score=round(score, 2), reasons=reasons)

    def record_correction(self, text: str):
        """用户 @cloud 纠正时记录到 session 黑名单。取前 20 字作为模式。"""
        clean = text.lower().strip()
        if len(clean) > 20:
            clean = clean[:20]
        if clean not in self._session_blacklist:
            self._session_blacklist.append(clean)
```

- [ ] **Step 2: 提交**

```bash
git add oaa/agent/complexity_evaluator.py
git commit -m "feat: add ComplexityEvaluator with priority-based routing"
```

---

### Task 3: OAAAgent 集成 — process_message 路由判断

**Files:**
- Modify: `oaa/agent/oaa_agent.py`

- [ ] **Step 1: 导入并初始化 evaluator**

在 `OAAAgent.__init__` 尾部追加：

```python
# 本地模型 evaluator
from .complexity_evaluator import ComplexityEvaluator

# 构建 evaluator 配置（从 local_model 配置提取关键词）
_local_cfg = config.local_model
_eval_config = {
    "confidence_threshold": _local_cfg.confidence_threshold,
    "keywords_local": _local_cfg.keywords_local,
    "keywords_cloud_analysis": _local_cfg.keywords_cloud_analysis,
    "keywords_cloud_creation": _local_cfg.keywords_cloud_creation,
    "keywords_cloud_external": _local_cfg.keywords_cloud_external,
    "keywords_step": _local_cfg.keywords_step,
}
self._evaluator = ComplexityEvaluator(_eval_config)
```

- [ ] **Step 2: 添加 `_run_local` 方法**

```python
async def _run_local(self, user_input: str) -> AsyncGenerator[dict, None]:
    """本地模型简化路径——单次 LLM 调用，无 agent loop。"""
    local_prompt = (
        "你是愣小二，二愣（AI 助手）的得力小弟。\n\n"
        "你能做:\n"
        "- 翻译、总结、提取信息、分类整理\n"
        "- 编写简单代码、格式化输出\n"
        "- 回答常识问题（不需查资料）\n\n"
        "你不能做（说'这个得叫我大哥来'）:\n"
        "- 数学计算、复杂推理\n"
        "- 查资料、搜索、读文件\n"
        "- 分析商业问题、多步推理\n\n"
        "回答简短直接，不确定不强答。"
    )
    yield {"type": "status", "content": "愣小二正在处理..."}
    try:
        response = await self.local_llm.chat([
            {"role": "system", "content": local_prompt},
            {"role": "user", "content": user_input},
        ])
        content = (response.content or "").strip()

        # 质量门禁
        quality = self._check_local_quality(content, user_input)
        if not quality["passed"]:
            logger.info(f"Local quality check failed: {quality['reason']}, falling back")
            self.config.local_model.fallback_count += 1
            async for chunk in self._run_with_cloud(user_input):
                yield chunk
            return

        # 统计
        self.config.local_model.local_calls += 1
        if response.usage:
            self.config.local_model.tokens_saved += (
                response.usage.get("total_tokens", 0)
            )

        yield {"type": "llm_output", "content": content}
        yield {"type": "done", "content": content, "route": "local"}

    except Exception as e:
        logger.warning(f"Local model failed: {e}, falling back to cloud")
        self.config.local_model.fallback_count += 1
        async for chunk in self._run_with_cloud(user_input):
            yield chunk
```

- [ ] **Step 3: 添加质量门禁方法**

```python
def _check_local_quality(self, output: str, input_text: str) -> dict:
    """检查本地模型输出质量。返回 {"passed": bool, "reason": str}。"""
    if len(output) < 5:
        return {"passed": False, "reason": "output_too_short"}
    # 检测重复循环（连续重复的短句）
    words = output.split()
    if len(words) >= 6:
        for window in [2, 3]:
            segments = [
                " ".join(words[i:i+window])
                for i in range(0, len(words), window)
            ]
            if any(segments.count(s) > 3 for s in segments):
                return {"passed": False, "reason": "repetition"}
    # 模型明确认输
    GIVEUP = ("这个得叫我大哥来", "叫大哥", "需要更强大的模型")
    if any(kw in output for kw in GIVEUP):
        return {"passed": False, "reason": "model_gave_up"}
    # 与输入关键词重叠率太低（简单校验）
    input_kws = set(w for w in input_text.split() if len(w) > 1)
    output_kws = set(w for w in output.split() if len(w) > 1)
    if input_kws and output_kws:
        overlap = len(input_kws & output_kws) / len(input_kws)
        if overlap < 0.05:
            return {"passed": False, "reason": "irrelevant_output"}
    return {"passed": True, "reason": ""}
```

- [ ] **Step 4: 在 process_message 入口加 evaluator 判断**

在 `process_message` 的 `logger.info("Processing message: %s...", user_input[:80])` 之后，现有的 `system_prompt = self.build_system_prompt()` 之前，插入：

```python
# Step 0: 评估路由（仅在本地模型启用且就绪时）
if self.config.local_model.enabled and hasattr(self, 'local_llm') and self.local_llm:
    decision = self._evaluator.evaluate(user_input)
    if decision.route == "local":
        logger.info(f"Route to LOCAL (score={decision.score}): {decision.reasons}")
        async for chunk in self._run_local(user_input):
            yield chunk
        return
    elif decision.override and decision.route == "cloud":
        logger.info(f"Route to CLOUD (user override @cloud)")
        self._evaluator.record_correction(user_input)
    else:
        self.config.local_model.cloud_calls += 1
else:
    self.config.local_model.cloud_calls += 1
```

- [ ] **Step 5: 在 stop 时确保 local_llm 清理**

```python
# 在 OAAAgent 添加
async def stop_local_llm(self):
    if hasattr(self, 'local_llm') and self.local_llm:
        await self.local_llm.close()
        self.local_llm = None
```

- [ ] **Step 6: 提交**

```bash
git add oaa/agent/oaa_agent.py
git commit -m "feat: integrate local model routing into agent"
```

---

### Task 4: call_xiaoer 工具（Agent 子任务调用）

**Files:**
- Modify: `oaa/agent/extended_tools.py`
- Modify: `oaa/agent/tool_schema.py`

- [ ] **Step 1: 在 tool_schema.py 添加 schema**

```python
# 追加到 ATOMIC_TOOLS_SCHEMA 列表末尾
{
    "type": "function",
    "function": {
        "name": "call_xiaoer",
        "description": "让愣小二（本地轻量模型）处理简单子任务：翻译、提取、格式化、分类等。结果返回文本。适合于把杂活甩给小弟干。",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "给愣小二的指令，要清晰具体，例如'把以下内容翻译成英文'、'从这段话中提取所有日期'"
                },
            },
            "required": ["prompt"],
        }
    }
},
```

- [ ] **Step 2: 在 extended_tools.py 添加 do_call_xiaoer**

```python
@agent_tool
async def do_call_xiaoer(self, prompt: str) -> dict:
    """让愣小二处理简单子任务。返回文本结果。"""
    # 从 agent 引用中获取 local_llm
    agent = getattr(self, '_oaa_agent', None)
    if agent is None or not hasattr(agent, 'local_llm') or not agent.local_llm:
        return {"status": "error", "msg": "愣小二未就绪，请确认本地模型已启用并正在运行"}
    try:
        response = await agent.local_llm.chat([
            {"role": "system", "content": "你是愣小二，专注于处理简单任务。回答要简短直接。"},
            {"role": "user", "content": prompt},
        ])
        content = (response.content or "").strip()
        if not content:
            return {"status": "error", "msg": "愣小二返回了空结果"}
        return {"status": "ok", "result": content}
    except Exception as e:
        return {"status": "error", "msg": f"愣小二调用失败: {e}"}
```

- [ ] **Step 3: 注册 agent 引用到 ExtendedTools**

在 `oaa_agent.py` 的 `ExtendedTools` 初始化后注入引用：

```python
self.extended = ExtendedTools(...)
self.extended._oaa_agent = self  # 让工具能访问 local_llm
```

- [ ] **Step 4: 提交**

```bash
git add oaa/agent/tool_schema.py oaa/agent/extended_tools.py oaa/agent/oaa_agent.py
git commit -m "feat: add call_xiaoer tool for agent sub-task delegation"
```

---

### Task 5: 自动安装 — ensure_local_llm

**Files:**
- Modify: `oaa/init.py`

- [ ] **Step 1: 添加 ensure_local_llm 函数**

在 `ensure_bundled_cli` 之后追加：

```python
def ensure_local_llm(data_dir: str) -> dict:
    """检查并下载本地模型运行所需文件。

    Returns:
        dict: {"downloaded": bool, "model_path": str, "server_path": str, "error": str}
    """
    result = {"downloaded": False, "model_path": "", "server_path": "", "error": ""}
    pkg_root = Path(__file__).resolve().parent.parent
    llama_dir = pkg_root / "cli" / "llama"
    model_dir = Path(data_dir) / "models"

    # 创建目录
    llama_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    # 检查 GGUF 模型
    gguf_files = list(model_dir.glob("*.gguf"))
    if not gguf_files:
        model_url = (
            "https://huggingface.co/openbmb/BitCPM4-1B-QAT-Int4-GGUF"
            "/resolve/main/BitCPM4-1B-q4_0.gguf"
        )
        model_path = model_dir / "BitCPM4-1B-q4_0.gguf"
        logger.info(f"正在下载本地模型 (~760MB)...")
        try:
            import urllib.request
            sys.stderr.write(f"[OAA] 下载本地模型 BitCPM4-1B-q4_0 (~760MB)...\n")
            urllib.request.urlretrieve(str(model_url), str(model_path))
            result["model_path"] = str(model_path)
        except Exception as e:
            result["error"] = f"模型下载失败: {e}"
            return result
    else:
        result["model_path"] = str(gguf_files[0])

    # 检查 llama-server
    server_exe = llama_dir / "llama-server.exe"
    cuda_dll = llama_dir / "ggml-cuda.dll"
    if not server_exe.exists():
        # 先尝试 CPU 版本（小）
        cpu_url = "https://github.com/ggml-org/llama.cpp/releases/download/b4319/llama-b4319-bin-win-msvc-x64.zip"
        sys.stderr.write(f"[OAA] 下载 llama-server (CPU, ~15MB)...\n")
        try:
            import zipfile, urllib.request, tempfile, shutil
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                urllib.request.urlretrieve(str(cpu_url), tmp.name)
                with zipfile.ZipFile(tmp.name) as zf:
                    for member in zf.namelist():
                        if member.endswith("llama-server.exe"):
                            zf.extract(member, str(llama_dir))
                            extracted = llama_dir / member
                            extracted.rename(server_exe)
                            break
                os.unlink(tmp.name)
        except Exception as e:
            result["error"] = f"llama-server 下载失败: {e}"
            return result

    result["server_path"] = str(server_exe)
    result["downloaded"] = True
    return result
```

- [ ] **Step 2: 在 OAAAgent 初始化时调用**

在 `oaa_agent.py` 的 `__init__` 中，`ensure_data_dir` 和 `ensure_bundled_cli` 之后追加：

```python
# 本地模型自动安装（只在启用配置时才检查）
if config.local_model.enabled:
    from ..init import ensure_local_llm
    install_result = ensure_local_llm(config.data_dir)
    if install_result.get("error"):
        logger.warning(f"本地模型安装失败: {install_result['error']}")
```

- [ ] **Step 3: 提交**

```bash
git add oaa/init.py oaa/agent/oaa_agent.py
git commit -m "feat: add ensure_local_llm auto-download"
```

---

### Task 6: llama-server 生命周期管理

**Files:**
- Modify: `oaa/app.py`
- Modify: `oaa/agent/oaa_agent.py`

- [ ] **Step 1: 在 OAAAgent 添加 set_local_llm 方法**

```python
def set_local_llm(self, llm_client):
    """注入本地模型的 LLMClient 实例。由 OAAApp 在 llama-server 就绪后调用。"""
    self.local_llm = llm_client
    logger.info("本地模型 LLMClient 已注入")
```

- [ ] **Step 2: 在 OAAApp.start 中启动 llama-server**

```python
# 在 self.desktop._on_first_client = self._notify_desktop 之前追加
if self.config.local_model.enabled:
    asyncio.create_task(self._start_local_llm())
```

- [ ] **Step 3: 添加 _start_local_llm 方法**

```python
async def _start_local_llm(self):
    """后台启动 llama-server，不阻塞主流程。"""
    from ..scripts.local_llm_manager import (
        detect_gpu, get_llama_server_path, find_model,
        start_llama_server, wait_for_server,
    )
    from ..llm import LLMClient
    from ..config import ModelConfig

    try:
        model_path = find_model() or ""
        if not model_path:
            logger.warning("未找到 GGUF 模型，愣小二不可用")
            return

        gpu = detect_gpu()
        opts = get_llama_server_path(gpu)[1]
        self._llama_proc = start_llama_server(
            model_path, gpu_info=gpu,
            port=self.config.local_model.port,
            context_size=self.config.local_model.context_size,
        )
        if not self._llama_proc:
            logger.warning("llama-server 启动失败")
            return

        ready = await asyncio.get_event_loop().run_in_executor(
            None, wait_for_server, self.config.local_model.port, 60
        )
        if ready:
            logger.info("愣小二就绪")
            # 创建指向本地模型的 LLMClient
            local_cfg = ModelConfig(
                provider="local-gguf",
                base_url=f"http://127.0.0.1:{self.config.local_model.port}/v1",
                api_key="not-needed",
                model_id="local-gguf",
            )
            local_llm = LLMClient(local_cfg)
            self.agent.set_local_llm(local_llm)
        else:
            logger.warning("llama-server 未能在 60s 内就绪")
    except Exception as e:
        logger.warning(f"本地模型启动失败: {e}")
```

- [ ] **Step 4: 在 stop 中关闭**

```python
async def stop(self):
    # 关闭本地模型
    if hasattr(self, '_llama_proc') and self._llama_proc:
        try:
            self._llama_proc.terminate()
            self._llama_proc.wait(timeout=10)
        except Exception:
            if self._llama_proc:
                self._llama_proc.kill()
        self._llama_proc = None
    if hasattr(self.agent, 'stop_local_llm'):
        await self.agent.stop_local_llm()
    
    # ... 原有 stop 代码 ...
```

- [ ] **Step 5: 添加 _run_with_cloud（OAAAgent 现有的完整链路）**

注：现有的 `process_message` 里已经有完整 AgentLoop 的创建。我们需要把那段代码抽象成 `_run_with_cloud`：

```python
# 在 oaa_agent.py 添加
async def _run_with_cloud(self, user_input: str, history=None) -> AsyncGenerator[dict, None]:
    """云端完整 agent loop（将现有 process_message 的 agent loop 部分提取至此）。"""
    system_prompt = self.build_system_prompt()
    handler = self.build_handler()
    # ... 从 process_message 中提取 agent loop 创建和执行的代码 ...
    # 注：与现有 process_message 中 Step 2~4 的代码相同
```

为简化实施，也可以不改动现有的 process_message，而是直接在 _run_local 的降级路径中重新构造完整的 process_message 调用。

- [ ] **Step 6: 提交**

```bash
git add oaa/app.py oaa/agent/oaa_agent.py
git commit -m "feat: add llama-server lifecycle management"
```

---

### Task 7: Management API

**Files:**
- Modify: `oaa/gateway/management.py`

- [ ] **Step 1: 添加 VALID_TYPES**

```python
VALID_TYPES = {
    # ... 现有 ...
    "get_local_model_config",
    "save_local_model_config",
}
```

- [ ] **Step 2: 添加 get_local_model_config handler**

```python
def _handle_get_local_model_config(self, _payload: dict) -> dict:
    """返回本地模型配置 + 统计。"""
    c = self._config.local_model
    return {"ok": True, "config": {
        "enabled": c.enabled,
        "model_path": c.model_path or "自动",
        "port": c.port,
        "context_size": c.context_size,
        "gpu_layers": c.gpu_layers,
        "confidence_threshold": c.confidence_threshold,
        "keywords_local": c.keywords_local,
        "keywords_cloud_analysis": c.keywords_cloud_analysis,
        "keywords_cloud_creation": c.keywords_cloud_creation,
        "keywords_cloud_external": c.keywords_cloud_external,
        "keywords_step": c.keywords_step,
        "stats": {
            "local_calls": c.local_calls,
            "cloud_calls": c.cloud_calls,
            "tokens_saved": c.tokens_saved,
            "fallback_count": c.fallback_count,
        },
    }}
```

- [ ] **Step 3: 添加 save_local_model_config handler**

```python
async def _handle_save_local_model_config(self, payload: dict) -> dict:
    """保存本地模型配置。如果启用/禁用则启停 llama-server。"""
    data = payload.get("config", {})
    if not data:
        return {"ok": False, "error": "No config data"}
    c = self._config.local_model
    # merge
    for key in ("enabled", "port", "context_size", "gpu_layers",
                 "confidence_threshold", "fallback_on_failure"):
        if key in data:
            setattr(c, key, data[key])
    # 关键词列表
    for key in ("keywords_local", "keywords_cloud_analysis",
                 "keywords_cloud_creation", "keywords_cloud_external",
                 "keywords_step"):
        if key in data and isinstance(data[key], list):
            setattr(c, key, data[key])
    await self._config.save()
    # 如果 enabled 状态变更，触发后台重启
    # （重启逻辑在 OAAApp 监听，这里仅持久化）
    return {"ok": True}
```

- [ ] **Step 4: 在 get_status 中追加本地模型状态**

```python
def _handle_get_status(self, _payload: dict) -> dict:
    result = { ... }  # 现有代码
    # 追加
    c = self._config.local_model
    running = (
        self._agent is not None
        and hasattr(self._agent, 'local_llm')
        and self._agent.local_llm is not None
    )
    result["local_model"] = {
        "enabled": c.enabled,
        "running": running,
        "local_calls": c.local_calls,
        "cloud_calls": c.cloud_calls,
        "tokens_saved": c.tokens_saved,
        "fallback_count": c.fallback_count,
    }
    return result
```

- [ ] **Step 5: 提交**

```bash
git add oaa/gateway/management.py
git commit -m "feat: add local model management API endpoints"
```

---

### Task 8: GUI — 路由 Badge + Header 状态

**Files:**
- Modify: `gui/src/composables/useWebSocket.ts`
- Modify: `gui/src/views/ChatView.vue`

- [ ] **Step 1: 扩展消息类型支持 route 字段**

在 `useWebSocket.ts` 中，扩展 `ChatMessage` 接口：

```typescript
export interface ChatMessage {
  role: string
  content: string
  route?: 'local' | 'cloud'  // 新增
}
```

在收到 `done` 消息时，如果包含 `route` 字段，把它保存到上一条 assistant 消息上。

- [ ] **Step 2: 在 ChatView.vue 消息气泡旁添加路由 Badge**

在消息渲染部分，assistant 气泡右下角添加：

```vue
<div class="msg-meta" v-if="msg.role === 'assistant' && msg.route">
  <span :class="['route-badge', msg.route]">
    {{ msg.route === 'local' ? '🏠 愣小二' : '☁️ 大哥' }}
  </span>
</div>
```

```css
.msg-meta {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}
.route-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  opacity: 0.6;
}
.route-badge.local {
  background: rgba(34, 197, 94, 0.12);
  color: var(--oaa-green-400);
}
.route-badge.cloud {
  background: rgba(59, 130, 246, 0.12);
  color: var(--oaa-blue-400);
}
```

- [ ] **Step 3: Header 添加本地模型状态指示**

模型选择器旁边加状态点：

```vue
<span
  v-if="localModelStatus.enabled"
  :class="['local-status-dot', localModelStatus.running ? 'running' : 'stopped']"
  :title="`愣小二: ${localModelStatus.running ? '运行中' : '未启动'} | 今日: ${localModelStatus.local_calls}次 | 节省: ~${localModelStatus.tokens_saved} tokens`"
></span>
```

```css
.local-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-left: 6px;
  display: inline-block;
}
.local-status-dot.running {
  background: var(--oaa-green-500);
  box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);
}
.local-status-dot.stopped {
  background: var(--oaa-color-disabled);
}
```

状态数据从 `get_status` 获取，通过 WebSocket 轮询或 push 更新。

- [ ] **Step 4: 提交**

```bash
git add gui/src/composables/useWebSocket.ts gui/src/views/ChatView.vue
git commit -m "feat: add local model route badge and header status indicator"
```

---

### Task 9: GUI — 输入框快捷切换

**Files:**
- Modify: `gui/src/views/ChatView.vue`

- [ ] **Step 1: 添加路由选择状态和切换按钮**

在 input-area 上方添加：

```vue
<div class="route-toggles" v-if="localModelStatus.enabled">
  <button
    v-for="opt in routeOptions"
    :key="opt.value"
    :class="['route-btn', { active: selectedRoute === opt.value }]"
    @click="selectedRoute = opt.value"
  >
    {{ opt.label }}
  </button>
</div>
```

```typescript
const selectedRoute = ref<'auto' | 'local' | 'cloud'>('auto')
const routeOptions = [
  { value: 'auto', label: '⚡ 自动' },
  { value: 'cloud', label: '💪 云端' },
  { value: 'local', label: '🔋 本地' },
]
```

- [ ] **Step 2: 在 sendMsg 中处理路由前缀**

```typescript
function sendMsg() {
  if (!input.value.trim()) return
  let text = input.value.trim()
  if (selectedRoute.value === 'local' && !text.startsWith('@local')) {
    // 如果已包含显式路由则不覆盖
    if (!text.startsWith('@cloud')) {
      text = '@local ' + text
    }
  } else if (selectedRoute.value === 'cloud' && !text.startsWith('@cloud')) {
    if (!text.startsWith('@local')) {
      text = '@cloud ' + text
    }
  }
  loading.value = true
  send(text)
  input.value = ''
  selectedRoute.value = 'auto'  // 发送后重置为自动
}
```

- [ ] **Step 3: 提交**

```bash
git add gui/src/views/ChatView.vue
git commit -m "feat: add local/cloud route toggle in chat input"
```

---

### Task 10: GUI — 本地模型配置页

**Files:**
- Create: `gui/src/views/LocalModelView.vue`
- Modify: `gui/src/App.vue`
- Modify: `gui/src/components/Sidebar.vue`

- [ ] **Step 1: 创建 LocalModelView.vue**

```vue
<template>
  <div class="local-model-view">
    <h1>愣小二配置</h1>

    <section class="card">
      <h2>状态</h2>
      <div class="status-row">
        <span :class="['status-dot', config.enabled ? 'enabled' : 'disabled']"></span>
        <span>{{ config.enabled ? '已启用' : '已禁用' }}</span>
      </div>
      <div class="info-grid">
        <div class="info-item">
          <label>模型</label>
          <span>BitCPM4-1B-q4_0</span>
        </div>
        <div class="info-item">
          <label>推理引擎</label>
          <span>llama-server</span>
        </div>
        <div class="info-item">
          <label>上下文</label>
          <span>{{ config.context_size }} tokens</span>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>路由设置</h2>
      <div class="slider-row">
        <label>置信度阈值: {{ config.confidence_threshold }}</label>
        <input type="range" min="0" max="1" step="0.05"
          v-model.number="config.confidence_threshold" @change="saveConfig" />
        <span class="hint">越高越倾向云端，越低越倾向本地</span>
      </div>
    </section>

    <section class="card">
      <h2>关键词</h2>
      <div v-for="(group, key) in keywordGroups" :key="key" class="kw-group">
        <h3>{{ group.label }}</h3>
        <div class="kw-tags">
          <span v-for="kw in config[key]" :key="kw" class="kw-tag">
            {{ kw }}
            <button class="kw-remove" @click="removeKeyword(key, kw)">&times;</button>
          </span>
          <input class="kw-input" v-model="newKeywords[key]" @keydown.enter="addKeyword(key)"
            placeholder="添加关键词..." />
        </div>
      </div>
    </section>

    <section class="card">
      <h2>今日统计</h2>
      <div class="stats-grid">
        <div class="stat">
          <span class="stat-value">{{ stats.local_calls }}</span>
          <span class="stat-label">本地调用</span>
        </div>
        <div class="stat">
          <span class="stat-value">{{ stats.cloud_calls }}</span>
          <span class="stat-label">云端调用</span>
        </div>
        <div class="stat">
          <span class="stat-value">~{{ stats.tokens_saved }}</span>
          <span class="stat-label">节省 Tokens</span>
        </div>
        <div class="stat">
          <span class="stat-value">{{ stats.fallback_count }}</span>
          <span class="stat-label">降级次数</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest } = useWebSocket()

const config = reactive({
  enabled: false,
  context_size: 32768,
  confidence_threshold: 0.3,
  keywords_local: [] as string[],
  keywords_cloud_analysis: [] as string[],
  keywords_cloud_creation: [] as string[],
  keywords_cloud_external: [] as string[],
  keywords_step: [] as string[],
})

const stats = reactive({
  local_calls: 0,
  cloud_calls: 0,
  tokens_saved: 0,
  fallback_count: 0,
})

const keywordGroups: Record<string, { label: string }> = {
  keywords_local: { label: '🏠 本地（走愣小二）' },
  keywords_cloud_analysis: { label: '☁️ 分析类（走云端）' },
  keywords_cloud_creation: { label: '☁️ 创作类（走云端）' },
  keywords_cloud_external: { label: '🌐 外部知识（走云端）' },
  keywords_step: { label: '步骤模式' },
}

const newKeywords = reactive<Record<string, string>>({
  keywords_local: '',
  keywords_cloud_analysis: '',
  keywords_cloud_creation: '',
  keywords_cloud_external: '',
  keywords_step: '',
})

async function loadConfig() {
  const resp = await sendRequest('get_local_model_config')
  if (resp.ok) {
    Object.assign(config, resp.config)
    Object.assign(stats, resp.config.stats || {})
  }
}

async function saveConfig() {
  await sendRequest('save_local_model_config', { config })
}

function addKeyword(group: string) {
  const val = newKeywords[group].trim()
  if (!val) return
  if (!config[group as keyof typeof config].includes(val)) {
    ;(config[group as keyof typeof config] as string[]).push(val)
    saveConfig()
  }
  newKeywords[group] = ''
}

function removeKeyword(group: string, kw: string) {
  const arr = config[group as keyof typeof config] as string[]
  const idx = arr.indexOf(kw)
  if (idx >= 0) arr.splice(idx, 1)
  saveConfig()
}

onMounted(loadConfig)
</script>
```

- [ ] **Step 2: 注册到 App.vue**

```typescript
import LocalModelView from './views/LocalModelView.vue'

const tabComponents: Record<string, any> = {
  // ... 现有 ...
  'local-model': LocalModelView,
}
```

- [ ] **Step 3: 添加到 Sidebar.vue**

```typescript
const navItems = [
  // ... 在 'settings' 之前插入
  {
    id: 'local-model',
    icon: `<svg width="20" height="20" ...>...</svg>`,
    label: '愣小二',
  },
  // ... settings
]
```

使用机器人/芯片图标。

- [ ] **Step 4: 提交**

```bash
git add gui/src/views/LocalModelView.vue gui/src/App.vue gui/src/components/Sidebar.vue
git commit -m "feat: add local model configuration page"
```

---

### Task 11: 集成验证

- [ ] **Step 1: 验证 evaluator 规则**

启动后端，在未启用本地模型时发送测试消息，确认所有请求走云端路径。

- [ ] **Step 2: 验证本地模型启动**

启用 local_model.enabled=true，确认日志显示 "愣小二就绪"。

- [ ] **Step 3: 验证路由**

发一条"翻译 hello world"，确认 evaluator 判 local，消息显示 🏠 愣小二 Badge。

发一条"分析这个市场趋势"，确认走云端，消息显示 ☁️ 大哥 Badge。

- [ ] **Step 4: 验证 @cloud/@local 强制路由**

发送 "@local 分析这个市场趋势"，确认强制走本地并触发降级（"叫我大哥来"）。

- [ ] **Step 5: 验证 call_xiaoer 工具**

在云端对话中说"把这段中文翻译成英文，让愣小二干"，确认 agent 调用了 call_xiaoer。

- [ ] **Step 6: 验证配置页**

打开左侧"愣小二"页面，确认配置显示、关键词增删、阈值滑块、统计正常工作。

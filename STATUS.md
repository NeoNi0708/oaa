# OAA 问题追踪

> 最后更新：2026-05-11 21:45（命令行测试后）

---

## 已修复（本轮 2026-05-11）

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| 1 | preload.js 缺失 → Electron 黑屏 | vite.config.ts 添加 preload 入口 | `vite.config.ts` |
| 2 | FileView `currentDepth` 未定义 | `currentDepth` → `depth` | `FileView.vue` |
| 3 | LLM API Key 保存后不生效 | `LLMClient.reconfigure()` + `save_config` 联动 | `client.py`, `management.py` |
| 4 | 通道禁用时 QR 登录报错 | `app.py` 始终注册所有通道适配器 | `app.py` |
| 5 | `done` 事件不清 `statusText` → "Thinking" 卡住 | `statusText.value = ''` | `useWebSocket.ts` |
| 6 | data_dir 乱码 | 修复为 `E:/GenericAgent/data` | config |
| 7 | 端口冲突（双 Python 进程） | Electron 统一 spawn Python | — |
| 8 | 聊天页模型选择器不可见 | `modelList.length` → `Object.keys(modelList).length` + WS 连接后重试 | `ChatView.vue` |
| 9 | Agent 10 分钟超时 | `httpx.AsyncClient(timeout=30s)` + `asyncio.wait_for(90s)` | `client.py`, `loop.py` |
| 10 | 429 静默失败 | `_friendly_error()` 中文错误提示 | `loop.py` |
| 11 | 部分 Agent 回复不显示（BUG-2） | `done` 为空时回退 `streamingContent` | `useWebSocket.ts` |
| 12 | 模型配置数据迁移空洞 | `save_config` 自动从 `model.*` 迁入 `models[provider]` | `management.py` |
| 13 | 钉钉/飞书 QR 码显示为裂图 | `qrcode` 库生成 base64 PNG data URI | `dingtalk.py`, `feishu.py` |
| 14 | 技能页"应用"按钮无反应 | `@click` 绑定 + `apply_evolution` 端点 | `SkillView.vue`, `management.py` |
| 15 | wechat_search 无 handler → "Unknown tool" | stub handler 返回可恢复错误 | `tools.py` |
| 16 | 输入框 CSS 双框 | 玻璃效果合并到单层 `.input-wrapper` | `ChatView.vue` |
| 17 | Agent 运行中无法中止 | stop 按钮 + `stop_chat` 端点 | `ChatView.vue`, `management.py` |

---

## 待修复

### P1 — `excel_xlsx` 工具 `rows` 参数类型错误

**发现**: 命令行测试中 LLM 3 次调用 `excel_xlsx` 全部失败，错误为 `Value must be a list...Supplied value is <class 'str'>`。
LLM 传入的 `rows` 是字符串而非 list。agent 随后降级到 `code_run` 自行生成，6 轮后才完成。

**修复**: `extended_tools.py:do_excel_xlsx` 增加参数类型转换——如果 `rows` 是 str，尝试 `json.loads()` 或按行 split。

**文件**: `oaa/agent/extended_tools.py`

---

### P2 — `_friendly_error` 误将 Xunfei 引擎错误归类为 Auth 错误

**发现**: Xunfei 返回 `APIError: EngineInternalError:error / InvalidParamError (10012)` 时，
`_friendly_error` 匹配不到已知模式，fallback 到 `"API Key 无效或已过期"`——完全错误的提示。

**修复**: 增加 `APIError` 和 `EngineInternalError` 匹配，返回 "模型服务内部错误，请重试或切换模型"。

**文件**: `oaa/agent/loop.py:_friendly_error()`

---

### P2 — 前端 Vue 3 `v-model` 与 browser tool `fill` 不兼容

**发现**: `/qa` 测试中 `$B fill` 和 `nativeInputValueSetter` 均无法触发 Vue 3 的 textarea `v-model`
更新，导致 send 按钮始终 disabled。聊天消息始终需通过 JS 直接操作 WS 发送。

**修复**: 无通用修复（是 browser automation tool 与 Vue 3 的已知兼容问题）。Q&A 测试改用 `$B js` 直接操作 WS。

---

### P3 — Zhipu API 间歇性超时

**发现**: 同一 `glm-4.7-flash` 请求有时 1.2s 快速返回（1305 限流），有时 15s+ ReadTimeout。
httpx 同步客户端的 DNS/SSL 解析在 Windows 上可能阻塞 event loop。

**当前方案**: 切换模型厂商（Xunfei 正常）。

---

### P3 — Xunfei 非流式模式返回空 content

**发现**: `stream=False` 时 `choices[0].message.content` 为空字符串（len=0），`stream=True` 时正常。
Agent 默认使用流式模式，不受影响。

---

### P3 — Xunfei Anthropic 端点需要单独 API Key

**发现**: `https://maas-coding-api.cn-huabei-1.xf-yun.com/anthropic` 返回 401 "HMAC signature cannot be verified"。
OpenAI 兼容端点的 Key（`id:secret` 格式）不适用于 Anthropic 端点。

---

### P4 — 聊天输入框 Vue 3 `v-model` + browse tool fill 不兼容

**发现**: `$B fill` 使用 `element.value = text` + `dispatchEvent('input')` 无法触发 Vue 3 `<textarea v-model>` 的响应式更新。
`$B type` 导致文本重复（原有内容 + 新输入叠加）。native value setter + input/change 事件均无效。

**影响**: QA 自动化测试无法通过 UI 发送消息，需绕过 UI 直接操作 WebSocket。

---

### P4 — 技能仓库技能状态依赖后端数据

**发现**: fallback 数据中 29 个技能的状态标签（可用/已安装/待激活）是硬编码值。
后端 `get_skills` 返回的实际 `loaded`/`tools_count`/`knowledge_count` 数据尚需验证是否正确反映真实技能状态。

---

## 命令行验证结果 (21:30)

| 测试 | 状态 | 耗时 |
|------|------|------|
| Zhipu API 直连 | ⚠️ 间歇超时 | 1.2s~15s+ |
| Xunfei API 直连 | ✅ 正常 | 4-6s |
| Agent 简单对话 | ✅ 正常 | 3.3s |
| Agent file_write 工具 | ✅ 正常 | 6.8s |
| Agent excel_xlsx 工具 | ⚠️ 参数类型错误 | 62s（降级到 code_run） |
| Agent 报价单技能 | ✅ 正确识别并追问 | 5.8s |

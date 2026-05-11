# OAA 问题追踪

> 最后更新：2026-05-11 17:40

---

## 用户测试反馈 (2026-05-11 — 待分析)

### UX-1: 聊天页面缺少模型切换控件

**状态**: 待验证

前端声称已实现模型选择器（ChatView.vue），但用户实际看到的下拉菜单不存在。
需要确认 DOM 渲染、WS 请求链路。

---

### UX-2: web_search 工具调用 10+ 分钟无响应

**状态**: 待排查

查询"今天的实时金价" → agent 调用 web_search → 无后续 turn，长时间阻塞。

可能：
- web_search HTTP 请求无超时
- agent loop 卡在 `await handler.dispatch()` 等待工具结果
- 工具内部异常未被 yield（沉没了）

---

### UX-3: 微信/钉钉/飞书扫码接入

**状态**: 待排查

- 微信：`HTTPSConnection Pool` 错误。用户确认微信接入二维码在其他应用中可正常显示。需要检查 iLink API 请求参数、参数加密等
- 钉钉/飞书：扫码成功后 Client ID/Secret 应自动填入。poll_qrcode_status 返回 token 后需写入 config 中的对应字段
- 微信：bot_token 扫码后自动填入同理

---

### UX-4: 输入框 UI + 发送按钮锁定

**状态**: 待修复

1. 大框套小框：`.input-area` + `.input-wrapper` 双层容器，CSS 收敛不完全
2. 发送按钮在 agent 响应期间 disabled，用户无法发送新消息或打断当前对话

需要：
- 彻底重构为单层输入容器
- 允许随时发送（取消/打断当前请求）
- 或添加取消按钮

---

### UX-5: 技能页面状态 + 应用按钮无反应

**状态**: 待排查

1. 技能仓库 29 个预置技能 — 仅显示名称，无状态标识（可用/需安装依赖/待激活）
2. 自生技能中，"上下文感知记忆"、"工具链式调用"、"分析面板"、"多模态输入输出" — 仅"分析面板"显示已应用
3. 其余三个点击"应用"按钮无反应，状态不变

---

### BUG-1: 智谱 API 认证失败 vs 讯飞成功

**状态**: 待定位根因

用户选择智谱 → AuthenticationError，切换讯飞星辰 → 成功。之前的分析方向被用户否定。
需要实际发送请求对比差异，查看完整错误日志。

---

## 已知未解决

- **OV4** (ask_user → GUI 确认) — 代码已实现但 UI 端未测试
- ChatView.vue 修改较大（~750 行），文件复杂
- GUI 日志中的 `Property "currentDepth"` 等 Vue 警告已修复（未启动验证）
- preload.js 缺失问题已修复（vite.config.ts ESLint fix）

---

## 已解决

| # | 日期 | 问题 | 修复 |
|---|------|------|------|
| 1 | 05-11 | preload.js not found → Electron 黑屏 | vite.config.ts 添加 preload 入口 |
| 2 | 05-11 | FileView currentDepth → Vue warn | 统一为 `depth` |
| 3 | 05-11 | LLM key 保存后不生效 | LLMClient.reconfigure() + management.py 联动 |
| 4 | 05-11 | 通道禁用时 QR 登录报 "Unknown channel" | app.py 注册所有通道 |
| 5 | 05-11 | Thinking (Turn N) 卡住 | done 事件中清理 statusText |
| 6 | 05-11 | data_dir 乱码 | 修复为 E:/GenericAgent/data |
| 7 | 05-11 | 端口冲突（双 Python 进程） | 由 Electron 统一 spawn，不再手动启动 Python |

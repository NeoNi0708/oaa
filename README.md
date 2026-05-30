<div align="center">

# OAA — Open AI Agent

**一个基于 GenericAgent 的通用 AI 代理框架，支持多通道、多模型、自我进化**

</div>

---

## 概览

OAA（Open AI Agent）是一款通用 AI 代理框架，以 [GenericAgent](https://github.com/lsdefine/GenericAgent) 为基座，在其简洁的原子工具和 Agent Loop 之上构建了多通道通信网关、语义记忆系统、技能进化引擎和跨平台 GUI。

### 核心特性

- **多通道接入** — 支持桌面 GUI（Vue + Electron）、钉钉、飞书、微信同时接入，对话不中断
- **多模型支持** — OpenAI / Anthropic Claude 双 API，支持运行时热切换
- **自我进化** — 每次任务执行后自动结晶经验为技能，能力持续增长
- **语义记忆系统** — Chroma 向量数据库 + SQLite 元数据，三层认知加工（存储→检索→消化）
- **技能系统** — 预置业务技能，支持动态加载与热更新
- **管理 API** — WebSocket 管理通道，支持通道管理、技能管理、参数调节、健康检查

### 架构

```
┌─────────────────────────────────────────────────────┐
│                   GUI (Vue + Electron)               │
├────────┬────────┬────────┬────────┬──────────────────┤
│ 钉钉    │ 飞书   │ 微信   │ Desktop │  管理 API        │
├────────┴────────┴────────┴────────┴──────────────────┤
│                   Gateway 层                         │
├──────────────────────────────────────────────────────┤
│               Agent Loop + 技能引擎                   │
├──────────────────┬───────────────────────────────────┤
│   原子工具集       │  语义记忆系统                     │
│   (GenericAgent)  │  (Chroma + SQLite + ONNX)        │
└──────────────────┴───────────────────────────────────┘
```

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置
python -m oaa init

# 启动
python run_app.py
```

### GUI 模式

```bash
cd gui
npm install
npm run dev
```

---

## 项目结构

```
oaa/             核心 Agent 框架
├── agent/       Agent Loop、工具、技能管理、记忆系统
├── gateway/     多通道网关（桌面/钉钉/飞书/微信）
├── llm/         LLM 客户端（OpenAI / Anthropic）
├── auth/        权限管理
├── scheduler/   任务调度
├── session/     会话管理
└── evolution/   进化引擎
gui/             Vue 3 + Electron 桌面客户端
cli/             CLI 工具
tests/           测试套件
scripts/         构建与安装脚本
```

---

## 致谢

OAA 基于 [GenericAgent](https://github.com/lsdefine/GenericAgent) 开发。

**GenericAgent** 是一个极简的自我进化自主 agent 框架，核心仅约 **3K 行代码**，通过 9 个原子工具 + 约 100 行的 Agent Loop 赋予 LLM 对本地计算机的系统级控制能力。其设计哲学"不要预载技能，让技能自我进化"深刻影响了 OAA 的架构设计。

OAA 在 GenericAgent 的原子工具和 Agent Loop 基础上，扩展了多通道网关、语义记忆、进化引擎、GUI 客户端等上层能力。

**感谢 GenericAgent 团队的开源贡献，为社区提供了一个优雅、高效、可扩展的 Agent 基座。**

<p align="center">
  <a href="https://github.com/lsdefine/GenericAgent">
    <img src="https://img.shields.io/badge/GenericAgent-基座项目-181717?logo=github" alt="GenericAgent">
  </a>
</p>

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

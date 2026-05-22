# OAA 全功能综合测试方案

**目标**：验证 agent 在缺乏专用工具的情况下，能否通过搜索、安装、编码等方式自主完成任务。
**原则**：指令只交代目标，不告诉具体方法，观察 agent 的自主决策链。

---

## 测试 1：创意解决问题 — 做图

**指令：**
> 帮我把 OAA 的系统架构画成一张图，保存到桌面，图片名叫 oaa_architecture.png

**预期 agent 决策链：**
1. 接到指令 → 发现自己没有画图工具
2. `web_search` 搜索 Python 画图方案（Pillow / matplotlib / graphviz）
3. `shell_run pip install pillow`（或 matplotlib）
4. `code_exec` 或 `file_write` 编写绘图代码
5. `shell_run python xxx.py` 执行生成图片
6. `file_write` 或 `shell_run` 将图片放到桌面

**检查点：**
- [ ] agent 没有直接说"我没有这个功能"放弃
- [ ] agent 主动搜索解决方案
- [ ] agent 自行安装依赖
- [ ] agent 成功生成图片到桌面
- [ ] 图片内容有意义（不是空白图）

---

## 测试 2：创意解决问题 — 做 PPT

**指令：**
> 帮我做一个项目介绍 PPT，介绍 OAA 的核心功能，放在桌面上

**预期 agent 决策链：**
1. 发现自己没有 PPT 工具
2. `web_search` 搜索 "python create ppt" → 找到 python-pptx
3. `shell_run pip install python-pptx`
4. `code_exec` 或写 Python 脚本生成 PPTX 文件
5. 保存到桌面

**检查点：**
- [ ] agent 成功安装 python-pptx
- [ ] agent 生成的 PPT 有至少 3 页（封面、功能介绍、总结）
- [ ] 文件可正常打开

---

## 测试 3：工具失败 → 自愈修复

**前提：** 确保某个工具处于可触发失败的状态（如 wechat_contacts 没有 wechat-cli）

**操作：**
1. `desktop` 通道调用 `wechat_contacts`
2. 等待 IdleInspector 巡检发现工具失败
3. 用户批准自愈提案
4. 观察 RepairLoop 执行

**预期流程：**
1. wechat_contacts 调用失败 → 记录到 tool_failures
2. IdleInspector 检测到 ≥2 次失败 → 创建 Proposal（含 problem_context）
3. 用户批准 → ManagementHandler → RepairLoop.run()
4. agent 收到自愈 prompt → 分析根因 → 制定修复方案

**检查点：**
- [ ] 失败被正确记录
- [ ] IdleInspector 正确创建提案
- [ ] RepairLoop 正常启动
- [ ] agent 尝试修复（至少尝试一种方案，不立刻放弃）

---

## 测试 4：多步骤信息搜集 + 产出

**指令：**
> 最近 AI 代理框架有什么新进展？调研一下，把结果整理成一个 markdown 文件保存到桌面

**预期 agent 决策链：**
1. `web_search` 搜索相关主题
2. 阅读搜索结果
3. 分析整理
4. `file_write` 输出 markdown 到桌面

**检查点：**
- [ ] agent 正确使用 web_search
- [ ] 输出内容有结构、有信息量
- [ ] 文件成功保存到桌面

---

## 测试 5：跨工具链任务

**指令：**
> 把当前项目的目录结构列出来，找到所有以 test_ 开头的 Python 文件，统计每个文件的测试函数数量，把结果做成一个表格保存到桌面

**预期 agent 决策链：**
1. `list_own_structure` 或 `shell_run` 列出目录
2. `shell_run find/grep` 查找 test_*.py 文件
3. `code_exec` 或 `shell_run` 分析每个文件的测试函数
4. `file_write` 输出表格

**检查点：**
- [ ] agent 正确处理目录遍历
- [ ] 测试函数统计准确
- [ ] 表格格式清晰

---

## 结果记录

| 测试 | 结果 | 备注 |
|------|------|------|
| 1. 做图 | ✅ 通过 | 成功生成 `oaa_architecture.png`（2385×1785, 164KB）。agent 自行分析架构 → 选择方案 → 创建图片。发现 `code_exec` bug（"code file not found: 15"）和路径混淆问题 |
| 2. 做 PPT | ⚠️ 部分通过 | agent 决策链完全正确：搜技能 → 安装 `create-pptx` → 加载 → `pip install python-pptx`。但 pip install 耗时过长导致 LLM 响应超时，未执行到生成代码 |
| 3. 自愈 | ⬜ | 未执行（需先触发工具失败 + 等待 IdleInspector + GUI 批准，流程较长） |
| 4. 调研产出 | ⬜ | 未执行（网络搜索需联网环境） |
| 5. 跨工具链 | ⬜ | 未执行 |

### 测试发现的问题（已全部修复）

| # | 问题 | 严重度 | 修复 | 文件 |
|---|------|--------|------|------|
| B1 | `code_exec` 报错 "code file not found: 15" | 中 | `--timeout` 的值被误当文件路径 → 修正参数解析跳过 flag 值 | `_exec_runner.py` |
| B2 | `read_own_source` 路径混淆（`oaa/oaa/app.py`）| 低 | 新增 `_resolve_source_path()`：支持模块路径（`oaa.app`）、重复前缀剥离（`oaa/oaa/` → `oaa/`），应用于 `read_own_source` 和 `list_own_structure` | `tools.py` |
| B3 | LLM 超时重试间隔太短 | 高 | 超时错误固定 30s 重试间隔（原 1s→2s→4s） | `loop.py` |
| B4 | 跨任务状态污染 | 中 | 以 `[系统错误]` 开头的响应不存入会话历史，避免下轮 agent 看到残留的错误 | `gateway.py` |

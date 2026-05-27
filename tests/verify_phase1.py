#!/usr/bin/env python3
"""Phase 1 集成验收 — 模拟完整业务流。

故意使用真实模块（非 mock），模拟一次完整的：
克隆 → 编辑 → 同步 → 清理 + 偏好 CRUD → 注入
"""
import os, sys, shutil, tempfile, json, time

# ── 确保能找到 oaa 模块 ──────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))
sys.path.insert(0, _PROJECT_ROOT)

from oaa.agent.clone_manager import CloneManager
from oaa.agent.preferences_store import PreferencesStore

PASS = 0
FAIL = 0

def ok(msg):
    global PASS; PASS += 1
    print(f"  ✅ {msg}")

def ng(msg, detail=""):
    global FAIL; FAIL += 1
    print(f"  ❌ {msg}" + (f" — {detail}" if detail else ""))

# ── 准备临时工程目录 ──────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix="oaa_phase1_")
OAA_ROOT = os.path.join(TMP, "source")
DATA_DIR = os.path.join(TMP, "data")

os.makedirs(os.path.join(OAA_ROOT, "oaa", "agent"))
os.makedirs(os.path.join(OAA_ROOT, "oaa", "gateway"))
os.makedirs(os.path.join(OAA_ROOT, "tests"))
os.makedirs(os.path.join(OAA_ROOT, "scripts"))

# 模拟目标文件（后续 agent 要修改的）
TOOLS_PY = os.path.join(OAA_ROOT, "oaa", "agent", "tools.py")
with open(TOOLS_PY, "w", encoding="utf-8") as f:
    f.write("# tools.py\nVERSION = '1.0'\ndef greet():\n    return 'hello'\n")

MGMT_PY = os.path.join(OAA_ROOT, "oaa", "gateway", "management.py")
with open(MGMT_PY, "w", encoding="utf-8") as f:
    f.write("# management.py\nMANAGER = 'default'\n")

# 测试文件
with open(os.path.join(OAA_ROOT, "tests", "test_dummy.py"), "w") as f:
    f.write("def test_dummy(): pass\n")

# 模拟运行时数据（应被排除在克隆之外）
os.makedirs(os.path.join(OAA_ROOT, "data"))
with open(os.path.join(OAA_ROOT, "data", "secret.txt"), "w") as f:
    f.write("secret")
os.makedirs(os.path.join(OAA_ROOT, "node_modules"))

print(f"\n{'='*60}")
print("Phase 1 集成验收 — 完整业务流程")
print(f"{'='*60}\n")

mgr = CloneManager(DATA_DIR, OAA_ROOT)
store = PreferencesStore(DATA_DIR)

# ═══════════════════════════════════════════════════════════════
# 1. clone_create
# ═══════════════════════════════════════════════════════════════
print("── 1. clone_create ──────────────────────────────")
assert not mgr.exists(), "预期无克隆，但 exists 返回 True"
ok("克隆不存在")

r = mgr.create()
assert r["ok"] is True, f"create 失败: {r}"
assert mgr.exists(), "create 后 exists 应为 True"
ok(f"克隆创建成功，包含目录: {r['copied_dirs']}")

# 验证关键文件存在
for fname in ["oaa/agent/tools.py", "oaa/gateway/management.py", "tests/test_dummy.py"]:
    assert os.path.isfile(os.path.join(DATA_DIR, "clone", fname)), f"克隆缺少: {fname}"
ok("核心文件已克隆")

# 验证排除项
for excluded in ["data", "node_modules"]:
    assert not os.path.isdir(os.path.join(DATA_DIR, "clone", excluded)), f"排除项未跳过: {excluded}"
ok("运行时数据/构建产物已排除")

# 重复创建应拒绝
r = mgr.create()
assert r["ok"] is False
ok("重复创建已拒绝")

# ═══════════════════════════════════════════════════════════════
# 2. clone_edit
# ═══════════════════════════════════════════════════════════════
print("\n── 2. clone_edit ────────────────────────────────")

# 编辑 tools.py：改版本号
r = mgr.apply_edit("oaa/agent/tools.py", "VERSION = '1.0'", "VERSION = '2.0'")
assert r["ok"] is True, f"apply_edit 失败: {r}"
ok("tools.py 版本号已修改")

# 验证克隆文件已改
clone_tools = os.path.join(DATA_DIR, "clone", "oaa", "agent", "tools.py")
with open(clone_tools) as f:
    assert "VERSION = '2.0'" in f.read()
ok("克隆文件内容已更新")

# 验证 Live 文件未改
with open(TOOLS_PY) as f:
    assert "VERSION = '1.0'" in f.read()
ok("Live 文件未受影响")

# 编辑 management.py
r = mgr.apply_edit("oaa/gateway/management.py", "MANAGER = 'default'", "MANAGER = 'custom'")
assert r["ok"] is True
ok("management.py 已修改")

# 测试 old_content 不匹配
r = mgr.apply_edit("oaa/agent/tools.py", "NONEXISTENT", "X")
assert r["ok"] is False and "不匹配" in r.get("error", "")
ok("old_content 不匹配被拒绝")

# 测试路径穿越
r = mgr.apply_edit("../../etc/passwd", "root", "x")
assert r["ok"] is False
ok("路径穿越攻击被拒绝")

# 测试文件不存在
r = mgr.apply_edit("oaa/nonexistent.py", "a", "b")
assert r["ok"] is False
ok("不存在文件被拒绝")

# ═══════════════════════════════════════════════════════════════
# 3. clone_status
# ═══════════════════════════════════════════════════════════════
print("\n── 3. clone_status ──────────────────────────────")
s = mgr.status()
assert s["exists"] is True
assert s["modified_count"] == 2, f"预期 2 个修改文件，实际 {s['modified_count']}"
ok(f"状态正确: {s['modified_count']} 个文件待同步")

# ═══════════════════════════════════════════════════════════════
# 4. clone_sync
# ═══════════════════════════════════════════════════════════════
print("\n── 4. clone_sync ────────────────────────────────")
r = mgr.sync(proposal_id="integration_test")
assert r["ok"] is True
assert len(r["synced"]) == 2, f"预期 2 文件同步，实际 {len(r['synced'])}"
ok(f"同步完成: {len(r['synced'])} 个文件")

# 验证 Live 文件已变更
with open(TOOLS_PY) as f:
    content = f.read()
    assert "VERSION = '2.0'" in content, f"tools.py 未同步: {content}"
ok("Live tools.py 已更新")

with open(MGMT_PY) as f:
    assert "MANAGER = 'custom'" in f.read()
ok("Live management.py 已更新")

# 验证备份存在
backup_dir = os.path.join(DATA_DIR, "backups")
if os.path.isdir(backup_dir):
    backup_files = os.listdir(backup_dir)
    if backup_files:
        ok(f"备份文件已创建: {backup_files}")
    else:
        ng("备份目录为空")
else:
    ng("备份目录不存在")

# 再次 sync 应为无待同步（manifest 已被清空）
r = mgr.sync()
assert r["ok"] is True
assert "warning" in r or len(r.get("synced", [])) == 0
ok("重复 sync 无副作用")

# ═══════════════════════════════════════════════════════════════
# 5. clone_discard
# ═══════════════════════════════════════════════════════════════
print("\n── 5. clone_discard ─────────────────────────────")
assert mgr.exists()
r = mgr.discard()
assert r["ok"] is True
assert not mgr.exists()
ok("克隆已删除")

# 幂等
r = mgr.discard()
assert r["ok"] is True
ok("幂等删除通过")

# 删除后 edit/sync 应拒绝
r = mgr.apply_edit("oaa/agent/tools.py", "a", "b")
assert r["ok"] is False
ok("删除后 edit 已拒绝")

r = mgr.sync()
assert r["ok"] is False
ok("删除后 sync 已拒绝")

# ═══════════════════════════════════════════════════════════════
# 6. PreferencesStore 完整工作流
# ═══════════════════════════════════════════════════════════════
print("\n── 6. PreferencesStore CRUD ────────────────────")

# Agent 设偏好
store.set("report_style", "brief", "User prefers brief reports")
p = store.get("report_style")
assert p is not None and p["value"] == "brief"
ok("agent 设置偏好: report_style = brief")

# 搜索
results = store.search("report")
assert len(results) >= 1 and results[0]["key"] == "report_style"
ok("关键词搜索可用")

# 更新
store.set("report_style", "detailed", "Detailed format")
p = store.get("report_style")
assert p["value"] == "detailed"
ok("偏好更新成功")

# 用户覆盖 — 免疫 agent 后续写入
store.set("notify_channel", "dingtalk", source="user_override")
store.set("notify_channel", "wechat", source="agent")  # 应被忽略
p = store.get("notify_channel")
assert p["value"] == "dingtalk", f"user_override 被覆盖: {p}"
ok("user_override 免疫 agent 覆盖")

# 删除
store.set("temp_key", "temp_val")
assert store.delete("temp_key") is True
assert store.get("temp_key") is None
ok("删除可用")

# 删除不存在的 key
assert store.delete("nonexistent") is False
ok("删除不存在 key 安全")

# 列出全部
store.set("multi_1", "v1")
store.set("multi_2", "v2")
all_prefs = store.list()
assert len(all_prefs) >= 2
ok("list 返回全部偏好")

# ═══════════════════════════════════════════════════════════════
# 7. 持久化验证
# ═══════════════════════════════════════════════════════════════
print("\n── 7. 持久化验证 ───────────────────────────────")
pref_path = os.path.join(DATA_DIR, "preferences.json")
assert os.path.isfile(pref_path), "preferences.json 不存在"
with open(pref_path) as f:
    saved = json.load(f)
assert isinstance(saved, list) and len(saved) > 0
ok("preferences.json 已持久化")

# 新建 store 实例读取同一文件
store2 = PreferencesStore(DATA_DIR)
p = store2.get("report_style")
assert p is not None and p["value"] == "detailed"
ok("新实例可读取已持久化的偏好")

# ═══════════════════════════════════════════════════════════════
# 8. System prompt 注入文本
# ═══════════════════════════════════════════════════════════════
print("\n── 8. get_injection_text ──────────────────────")
text = store.get_injection_text()
assert "report_style" in text, f"注入文本缺少 report_style: {text}"
assert "notify_channel" in text, f"注入文本缺少 notify_channel"
assert "用户偏好" in text
ok("注入文本格式正确，包含活跃偏好")

# 空 store
empty_store = PreferencesStore(os.path.join(TMP, "empty_data"))
text = empty_store.get_injection_text()
assert "暂无" in text
ok("空偏好注入文本正确")

# ═══════════════════════════════════════════════════════════════
# 9. 容量限制
# ═══════════════════════════════════════════════════════════════
print("\n── 9. 容量限制 ────────────────────────────────")
big_store = PreferencesStore(os.path.join(TMP, "big_data"))
for i in range(55):
    big_store.set(f"auto_key_{i}", f"val_{i}", source="agent")
big_store.set("manual_key", "protected", source="user_override")
total = len(big_store.list())
assert total <= 50, f"超过容量上限: {total}"
assert big_store.get("manual_key") is not None, "user_override 被淘汰"
ok(f"50 条上限生效（当前 {total} 条），user_override 保留")

# ── 汇总 ─────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"验收结果: {PASS} ✅ / {FAIL} ❌ / 总计 {PASS+FAIL}")
print(f"{'='*60}")

# ── 清理 ─────────────────────────────────────────────────────
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if FAIL == 0 else 1)

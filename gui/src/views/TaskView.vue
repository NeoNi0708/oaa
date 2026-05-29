<template>
  <div class="view-container" style="position:relative">
    <div class="view-header">
      <h2>定时任务</h2>
      <p class="view-subtitle">管理定时任务与提醒</p>
    </div>

    <!-- 子标签 -->
    <div class="tab-bar">
      <button
        v-for="tab in subTabs"
        :key="tab.id"
        :class="['tab-btn', { active: activeSubTab === tab.id }]"
        @click="activeSubTab = tab.id; if (tab.id === 'done') loadExecutionHistory()"
      >
        <span class="tab-icon">{{ tab.icon }}</span>
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <!-- 已有任务 -->
    <div v-if="activeSubTab === 'active'" class="tab-content">
      <div class="section-header-inline">
        <h3>已有定时任务</h3>
      </div>
      <div v-if="loading" class="empty-state">
        <span class="task-spinner"></span>
        <p>加载中...</p>
      </div>
      <div v-else-if="tasks.length === 0" class="empty-state">
        <p>暂无定时任务，可让 OAA 帮你创建</p>
      </div>
      <div v-else class="task-list">
        <div v-for="task in activeTasks" :key="task.id" class="task-card">
          <div class="task-card-header">
            <span class="task-type-badge" :class="task.type === 'reminder' ? 'type-reminder' : 'type-fixed'">
              {{ task.type === 'reminder' ? '提醒' : '固定' }}
            </span>
            <span :class="['task-status-dot', task.enabled ? 'enabled' : 'disabled']"></span>
          </div>
          <div class="task-card-title">{{ task.name }}</div>
          <div class="task-card-desc">{{ task.description }}</div>
          <div class="task-card-meta">
            <span>🔄 {{ cycleLabel(task) }}</span>
            <span>⏰ {{ padZero(task.startHour) }}:{{ padZero(task.startMinute) }}</span>
          </div>
          <div class="task-card-channels">
            <span v-for="ch in task.channels" :key="ch" class="channel-tag">{{ channelLabel(ch) }}</span>
          </div>
          <div class="task-card-actions">
            <button class="oaa-btn oaa-btn--sm" :class="task.enabled ? 'oaa-btn--secondary' : 'oaa-btn--primary'" @click="toggleTask(task.id)">
              {{ task.enabled ? '暂停' : '启用' }}
            </button>
            <button class="oaa-btn oaa-btn--sm oaa-btn--ghost" @click="openEditModal(task)">编辑</button>
            <button class="oaa-btn oaa-btn--sm oaa-btn--ghost" @click="confirmDeleteTask(task.id)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 已完成 — 执行历史 -->
    <div v-if="activeSubTab === 'done'" class="tab-content">
      <div class="section-header-inline">
        <h3>执行历史</h3>
      </div>
      <div v-if="historyLoading" class="empty-state">
        <span class="task-spinner"></span>
        <p>加载中...</p>
      </div>
      <div v-else-if="executionHistory.length === 0" class="empty-state">
        <p>暂无执行记录，任务执行后将自动显示在这里</p>
      </div>
      <div v-else class="task-list">
        <div v-for="rec in executionHistory" :key="rec.timestamp" class="task-card done">
          <div class="task-card-header">
            <span class="task-type-badge type-reminder">{{ rec.task_name }}</span>
            <span :class="['done-mark', rec.status === 'success' ? 'text-success' : 'text-error']">
              {{ rec.status === 'success' ? '✅ 成功' : '❌ 失败' }}
            </span>
          </div>
          <div class="task-card-meta">
            <span>🕐 {{ formatTime(rec.timestamp) }}</span>
          </div>
          <div v-if="rec.summary" class="task-card-desc">{{ rec.summary }}</div>
        </div>
      </div>
    </div>
    <!-- 编辑任务弹窗 -->
    <div v-if="editModalVisible" class="modal-overlay" @click.self="closeEditModal">
      <div class="modal-card">
        <div class="modal-header">
          <h3>编辑任务</h3>
          <button class="modal-close-btn" @click="closeEditModal">&times;</button>
        </div>
        <div class="modal-body">
          <div v-if="editForm" class="edit-form">
            <!-- 执行周期 -->
            <div class="form-group">
              <label class="oaa-label">执行周期</label>
              <select v-model="editForm.cycle" class="oaa-select">
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
              </select>
              <div v-if="editForm.cycle === 'weekly'" class="cycle-sub">
                <span v-for="d in weekDays" :key="d.value"
                  :class="['day-chip', { active: editForm.cycleDay === d.value }]"
                  @click="editForm.cycleDay = d.value">
                  {{ d.label }}
                </span>
              </div>
              <div v-if="editForm.cycle === 'monthly'" class="cycle-sub">
                <select v-model.number="editForm.cycleDay" class="oaa-select cycle-select">
                  <option v-for="d in 31" :key="d" :value="d">{{ d }} 号</option>
                </select>
              </div>
            </div>

            <!-- 执行时间 -->
            <div class="form-row">
              <div class="form-group flex-1">
                <label class="oaa-label">执行时间</label>
                <div class="time-picker">
                  <select v-model.number="editForm.startHour" class="oaa-select time-select">
                    <option v-for="h in 24" :key="h-1" :value="h-1">{{ padZero(h-1) }}</option>
                  </select>
                  <span class="time-sep">:</span>
                  <select v-model.number="editForm.startMinute" class="oaa-select time-select">
                    <option v-for="m in 60" :key="m-1" :value="m-1">{{ padZero(m-1) }}</option>
                  </select>
                </div>
              </div>
            </div>

            <!-- 任务名称 -->
            <div class="form-group">
              <label class="oaa-label">任务名称</label>
              <input v-model="editForm.name" type="text" class="oaa-input" placeholder="输入任务名称" />
            </div>

            <!-- 内容要求 (execution_prompt) -->
            <div class="form-group">
              <label class="oaa-label">内容要求</label>
              <textarea v-model="editForm.executionPrompt" class="oaa-input form-textarea" rows="4"
                placeholder='告诉 OAA 到时间后应该做什么，如「搜集当天10条热点新闻并按格式整理」'></textarea>
            </div>

            <!-- 交付渠道 -->
            <div class="form-group">
              <label class="oaa-label">交付渠道</label>
              <div class="checkbox-group">
                <label v-for="ch in allChannels" :key="ch.value" class="checkbox-label">
                  <input type="checkbox" :value="ch.value" v-model="editForm.channels" class="checkbox-input" />
                  <span class="checkbox-custom"></span>
                  <span class="checkbox-text">{{ ch.label }}</span>
                </label>
              </div>
            </div>

            <!-- 汇报设置 -->
            <div class="form-group">
              <label class="oaa-label">任务汇报</label>
              <div class="radio-group">
                <label class="radio-label">
                  <input type="radio" v-model="editForm.report" :value="true" class="radio-input" />
                  <span class="radio-custom"></span>
                  <span class="radio-text">是</span>
                </label>
                <label class="radio-label">
                  <input type="radio" v-model="editForm.report" :value="false" class="radio-input" />
                  <span class="radio-custom"></span>
                  <span class="radio-text">否</span>
                </label>
              </div>
              <div v-if="editForm.report" class="sub-option">
                <label class="oaa-label" style="margin-top: 8px;">汇报渠道</label>
                <div class="checkbox-group">
                  <label v-for="ch in allChannels" :key="ch.value" class="checkbox-label">
                    <input type="checkbox" :value="ch.value" v-model="editForm.reportChannels" class="checkbox-input" />
                    <span class="checkbox-custom"></span>
                    <span class="checkbox-text">{{ ch.label }}</span>
                  </label>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="oaa-btn oaa-btn--secondary" @click="closeEditModal">取消</button>
          <button class="oaa-btn oaa-btn--primary" @click="saveEdit" :disabled="!editForm?.name.trim() || editSaving">
            <span v-if="editSaving" class="task-btn-spinner"></span>
            {{ editSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

type TaskType = 'fixed' | 'reminder'
type Cycle = 'daily' | 'weekly' | 'monthly'
type Channel = 'chat' | 'wechat' | 'dingtalk' | 'feishu' | 'email'

interface ScheduledTask {
  id: string
  type: TaskType
  name: string
  description: string
  executionPrompt: string
  cycle: Cycle
  cycleDay: number
  startHour: number
  startMinute: number
  channels: Channel[]
  report: boolean
  reportChannels: Channel[]
  confirmReceipt: boolean
  enabled: boolean
  status: 'active' | 'completed'
  createdAt: string
}

// Backend task format (snake_case)
interface BackendTask {
  id: string
  type: string
  name: string
  description: string
  enabled: boolean
  cycle: string
  cycle_day: number
  start_hour: number
  start_minute: number
  channels: string[]
  report: boolean
  report_channels: string[]
  confirm_receipt: boolean
  execution_prompt?: string
  created_at: string
  updated_at?: string
  last_run?: string | null
}

function fromBackendTask(bt: BackendTask): ScheduledTask {
  return {
    id: bt.id,
    type: bt.type as TaskType,
    name: bt.name,
    description: bt.description,
    executionPrompt: bt.execution_prompt || '',
    cycle: bt.cycle as Cycle,
    cycleDay: bt.cycle_day,
    startHour: bt.start_hour,
    startMinute: bt.start_minute,
    channels: bt.channels as Channel[],
    report: bt.report,
    reportChannels: bt.report_channels as Channel[],
    confirmReceipt: bt.confirm_receipt,
    enabled: bt.enabled,
    status: bt.enabled ? 'active' : 'completed',
    createdAt: bt.created_at,
  }
}

function toBackendTask(task: ScheduledTask): Record<string, unknown> {
  return {
    id: task.id,
    type: task.type,
    name: task.name,
    description: task.description,
    cycle: task.cycle,
    cycle_day: task.cycleDay,
    start_hour: task.startHour,
    start_minute: task.startMinute,
    channels: task.channels,
    report: task.report,
    report_channels: task.reportChannels,
    confirm_receipt: task.confirmReceipt,
    enabled: task.status === 'active',
  }
}

const { sendRequest, tasksUpdated } = useWebSocket()

const historyLoading = ref(false)
const executionHistory = ref<any[]>([])

async function loadExecutionHistory() {
  if (historyLoading.value) return
  historyLoading.value = true
  try {
    const resp = await Promise.race([
      sendRequest('get_task_history'),
      new Promise<any>(resolve => setTimeout(() => resolve(null), 5000)),
    ])
    if (resp && resp.ok && Array.isArray(resp.history)) {
      executionHistory.value = resp.history
    } else {
      executionHistory.value = []
    }
  } catch {
    executionHistory.value = []
  } finally {
    historyLoading.value = false
  }
}

const weekDays = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]

const allChannels: { value: Channel; label: string }[] = [
  { value: 'chat', label: '聊天页面' },
  { value: 'wechat', label: '微信' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'email', label: '邮件' },
]

const channelLabels: Record<Channel, string> = {
  chat: '聊天页面', wechat: '微信', dingtalk: '钉钉', feishu: '飞书', email: '邮件',
}

function channelLabel(ch: Channel) { return channelLabels[ch] }

const subTabs = [
  { id: 'active', icon: '📋', label: '已有任务' },
  { id: 'done', icon: '✅', label: '任务记录' },
]

const activeSubTab = ref('active')
const loading = ref(true)
const tasks = ref<ScheduledTask[]>([])

// Edit modal state
const editModalVisible = ref(false)
const editSaving = ref(false)
interface EditForm {
  id: string
  name: string
  cycle: Cycle
  cycleDay: number
  startHour: number
  startMinute: number
  executionPrompt: string
  channels: Channel[]
  report: boolean
  reportChannels: Channel[]
}
const editForm = ref<EditForm | null>(null)

// ------------------------------------------------------------------
// Backend sync
// ------------------------------------------------------------------

async function loadTasksFromBackend() {
  try {
    const resp = await sendRequest('get_tasks')
    if (resp.ok && Array.isArray(resp.tasks)) {
      tasks.value = (resp.tasks as BackendTask[]).map(fromBackendTask)
      return
    }
  } catch {
    // Backend unavailable — fall back to localStorage
  }
  // localStorage fallback
  try {
    const raw = localStorage.getItem('oaa_tasks')
    if (raw) {
      tasks.value = JSON.parse(raw)
    }
  } catch { /* empty */ }
}

onMounted(async () => {
  await loadTasksFromBackend()
  loading.value = false
  if (activeSubTab.value === 'done') {
    await loadExecutionHistory()
  }
})

// Auto-reload when backend pushes task updates
watch(tasksUpdated, () => {
  loadTasksFromBackend()
  if (activeSubTab.value === 'done') {
    loadExecutionHistory()
  }
})

const activeTasks = computed(() => tasks.value.filter(t => t.status === 'active'))
const completedTasks = computed(() => tasks.value.filter(t => t.status === 'completed'))

function cycleLabel(task: ScheduledTask) {
  if (task.cycle === 'daily') return '每天'
  if (task.cycle === 'weekly') return `每周${weekDays.find(d => d.value === task.cycleDay)?.label || ''}`
  return `每月 ${task.cycleDay} 号`
}

function padZero(n: number) { return n.toString().padStart(2, '0') }

function formatTime(ts: string) {
  try {
    const d = new Date(ts)
    return `${d.getFullYear()}-${padZero(d.getMonth()+1)}-${padZero(d.getDate())} ${padZero(d.getHours())}:${padZero(d.getMinutes())}`
  } catch {
    return ts
  }
}

// ------------------------------------------------------------------
// CRUD
// ------------------------------------------------------------------

function openEditModal(task: ScheduledTask) {
  editForm.value = {
    id: task.id,
    name: task.name,
    cycle: task.cycle,
    cycleDay: task.cycleDay,
    startHour: task.startHour,
    startMinute: task.startMinute,
    executionPrompt: task.executionPrompt || '',
    channels: [...task.channels],
    report: task.report,
    reportChannels: [...task.reportChannels],
  }
  editModalVisible.value = true
}

function closeEditModal() {
  editModalVisible.value = false
  editForm.value = null
  editSaving.value = false
}

async function saveEdit() {
  if (!editForm.value || !editForm.value.name.trim()) return
  editSaving.value = true

  const payload: Record<string, unknown> = {
    id: editForm.value.id,
    name: editForm.value.name,
    cycle: editForm.value.cycle,
    cycle_day: editForm.value.cycleDay,
    start_hour: editForm.value.startHour,
    start_minute: editForm.value.startMinute,
    channels: editForm.value.channels,
    report: editForm.value.report,
    report_channels: editForm.value.reportChannels,
  }
  // Only send execution_prompt if user filled it in
  if (editForm.value.executionPrompt.trim()) {
    payload.execution_prompt = editForm.value.executionPrompt.trim()
  }

  try {
    const resp = await sendRequest('save_task', { task: payload })
    if (resp.ok) {
      // Reload tasks to get updated server state
      await loadTasksFromBackend()
    }
  } catch {
    // Offline: update locally
    const t = tasks.value.find(t => t.id === editForm.value!.id)
    if (t) {
      t.name = editForm.value.name
      t.cycle = editForm.value.cycle
      t.cycleDay = editForm.value.cycleDay
      t.startHour = editForm.value.startHour
      t.startMinute = editForm.value.startMinute
      t.channels = [...editForm.value.channels]
      t.report = editForm.value.report
      t.reportChannels = [...editForm.value.reportChannels]
      localStorage.setItem('oaa_tasks', JSON.stringify(tasks.value))
    }
  }

  closeEditModal()
}

async function toggleTask(id: string) {
  const t = tasks.value.find(t => t.id === id)
  if (!t) return

  // Optimistic toggle
  t.status = t.status === 'active' ? 'completed' : 'active'
  t.enabled = t.status === 'active'

  try {
    const resp = await sendRequest('toggle_task', { id })
    if (resp.ok && resp.task) {
      // Sync back the server state
      const updated = fromBackendTask(resp.task as BackendTask)
      Object.assign(t, updated)
    }
  } catch {
    // Revert on error
    t.status = t.status === 'active' ? 'completed' : 'active'
    t.enabled = t.status === 'active'
  }
}

async function confirmDeleteTask(id: string) {
  if (!confirm('确定删除此任务？')) return

  tasks.value = tasks.value.filter(t => t.id !== id)

  try {
    const resp = await sendRequest('delete_task', { id })
    if (!resp.ok) {
      // Reload if server delete failed
      await loadTasksFromBackend()
    }
  } catch {
    // Keep local state when offline
    localStorage.setItem('oaa_tasks', JSON.stringify(tasks.value))
  }
}
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
}

.view-header { margin-bottom: var(--oaa-space-6); }

.view-header h2 {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}

.view-subtitle { color: var(--oaa-color-muted); font-size: var(--oaa-text-base); }

.tab-bar {
  display: flex;
  gap: var(--oaa-space-1);
  background: var(--oaa-bg-surface);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-6);
  border: 1px solid var(--oaa-border-subtle);
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-base);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--oaa-transition-fast), color var(--oaa-transition-fast);
}

.tab-btn:hover { color: var(--oaa-color-primary); background: var(--oaa-primary-light); }
.tab-btn.active { background: var(--oaa-primary); color: #fff; }
.tab-icon { font-size: 1.05rem; }

.tab-content { animation: fadeIn 0.2s ease; }
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.section-header-inline {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-4);
}
.section-header-inline h3 { font-size: var(--oaa-text-lg); font-weight: 600; color: var(--oaa-color-secondary); }

.empty-state {
  padding: var(--oaa-space-10);
  text-align: center;
  color: var(--oaa-color-muted);
}

/* Task list */
.task-list { display: flex; flex-direction: column; gap: var(--oaa-space-3); }

.task-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
}
.task-card.done { opacity: 0.6; }

.task-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.task-type-badge {
  font-size: var(--oaa-text-xs);
  font-weight: 700;
  padding: 2px 10px;
  border-radius: var(--oaa-radius-full);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.type-fixed { background: var(--oaa-primary-light); color: var(--oaa-primary); }
.type-reminder { background: rgba(234, 179, 8, 0.15); color: var(--oaa-amber-500); }

.task-status-dot {
  width: 8px; height: 8px; border-radius: 50%;
}
.task-status-dot.enabled { background: var(--oaa-success); box-shadow: 0 0 6px var(--oaa-success); }
.task-status-dot.disabled { background: var(--oaa-color-disabled); }

.task-card-title { font-size: var(--oaa-text-lg); font-weight: 600; color: var(--oaa-color-primary); }
.task-card-desc { font-size: var(--oaa-text-sm); color: var(--oaa-color-muted); }
.task-card-meta { display: flex; gap: var(--oaa-space-4); font-size: var(--oaa-text-sm); color: var(--oaa-color-muted); }
.task-card-channels { display: flex; gap: var(--oaa-space-2); flex-wrap: wrap; }

.channel-tag {
  font-size: var(--oaa-text-xs);
  padding: 2px 10px;
  border-radius: var(--oaa-radius-full);
  background: var(--oaa-bg-input);
  color: var(--oaa-color-secondary);
}

.task-card-actions {
  display: flex;
  gap: var(--oaa-space-2);
  margin-top: var(--oaa-space-1);
}

.done-mark { font-size: var(--oaa-text-sm); color: var(--oaa-green-500); }

/* Form */
.form-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-xl);
  padding: var(--oaa-space-6);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
}

.form-group { display: flex; flex-direction: column; gap: var(--oaa-space-1); }
.form-row { display: flex; gap: var(--oaa-space-4); }
.flex-1 { flex: 1; }

.form-textarea { resize: vertical; min-height: 60px; }

.radio-group { display: flex; gap: var(--oaa-space-4); }
.radio-label { display: flex; align-items: center; gap: var(--oaa-space-2); cursor: pointer; user-select: none; }
.radio-input { display: none; }
.radio-custom {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid var(--oaa-border-default);
  background: var(--oaa-bg-input);
  position: relative;
  flex-shrink: 0;
  transition: border-color var(--oaa-transition-fast);
}
.radio-input:checked + .radio-custom { border-color: var(--oaa-primary); }
.radio-input:checked + .radio-custom::after {
  content: ''; position: absolute; width: 8px; height: 8px;
  background: var(--oaa-primary); border-radius: 50%;
  top: 3px; left: 3px;
}
.radio-text { color: var(--oaa-color-secondary); font-size: var(--oaa-text-base); }

.checkbox-group { display: flex; flex-wrap: wrap; gap: var(--oaa-space-3); }
.checkbox-label { display: flex; align-items: center; gap: var(--oaa-space-2); cursor: pointer; user-select: none; }
.checkbox-input { display: none; }
.checkbox-custom {
  width: 18px; height: 18px;
  border: 2px solid var(--oaa-border-default);
  border-radius: var(--oaa-radius-sm);
  background: var(--oaa-bg-input);
  position: relative;
  flex-shrink: 0;
  transition: border-color var(--oaa-transition-fast), background var(--oaa-transition-fast);
}
.checkbox-input:checked + .checkbox-custom { background: var(--oaa-primary); border-color: var(--oaa-primary); }
.checkbox-input:checked + .checkbox-custom::after {
  content: ''; position: absolute; left: 4px; top: 1px;
  width: 5px; height: 9px;
  border: solid #fff; border-width: 0 2px 2px 0; transform: rotate(45deg);
}
.checkbox-text { color: var(--oaa-color-secondary); font-size: var(--oaa-text-base); }

.sub-option { padding-left: var(--oaa-space-4); margin-top: var(--oaa-space-1); }

.cycle-sub { display: flex; gap: var(--oaa-space-2); margin-top: var(--oaa-space-2); flex-wrap: wrap; }
.day-chip {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: var(--oaa-bg-input);
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-sm);
  cursor: pointer;
  transition: background var(--oaa-transition-fast), color var(--oaa-transition-fast);
}
.day-chip.active { background: var(--oaa-primary); color: #fff; }
.day-chip:hover:not(.active) { background: var(--oaa-primary-light); }

.cycle-select { width: auto; min-width: 120px; }

.time-picker { display: flex; align-items: center; gap: var(--oaa-space-1); }
.time-select { width: auto; min-width: 80px; }
.time-sep { color: var(--oaa-color-secondary); font-size: var(--oaa-text-lg); font-weight: 700; }

.form-actions { display: flex; justify-content: flex-end; gap: var(--oaa-space-3); margin-top: var(--oaa-space-2); }

/* --- Loading / Saving spinners --- */
.task-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: taskSpin 0.6s linear infinite;
  margin-bottom: var(--oaa-space-2);
}

.task-btn-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: taskSpin 0.6s linear infinite;
  vertical-align: middle;
}

@keyframes taskSpin {
  to { transform: rotate(360deg); }
}

/* --- Edit modal --- */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.15s ease;
}

.modal-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-xl);
  width: 520px;
  max-width: 92vw;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--oaa-space-5) var(--oaa-space-6);
  border-bottom: 1px solid var(--oaa-border-subtle);
}
.modal-header h3 {
  font-size: var(--oaa-text-xl);
  font-weight: 600;
  color: var(--oaa-color-primary);
  margin: 0;
}

.modal-close-btn {
  background: none;
  border: none;
  font-size: 1.5rem;
  color: var(--oaa-color-muted);
  cursor: pointer;
  padding: 0;
  line-height: 1;
}
.modal-close-btn:hover { color: var(--oaa-color-primary); }

.modal-body {
  padding: var(--oaa-space-5) var(--oaa-space-6);
  overflow-y: auto;
  flex: 1;
}

.edit-form {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-4) var(--oaa-space-6);
  border-top: 1px solid var(--oaa-border-subtle);
}
</style>

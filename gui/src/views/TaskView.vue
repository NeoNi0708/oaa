<template>
  <div class="view-container">
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
        @click="activeSubTab = tab.id"
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
        <p>暂无定时任务，点击上方「新建」创建</p>
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
            <button class="oaa-btn oaa-btn--sm oaa-btn--ghost" @click="copyTask(task)">复制</button>
            <button class="oaa-btn oaa-btn--sm oaa-btn--ghost" @click="confirmDeleteTask(task.id)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建任务表单 -->
    <div v-if="activeSubTab === 'new'" class="tab-content">
      <div class="form-card">
        <div class="form-group">
          <label class="oaa-label">任务类型</label>
          <div class="radio-group">
            <label class="radio-label">
              <input type="radio" v-model="form.type" value="fixed" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">固定任务</span>
            </label>
            <label class="radio-label">
              <input type="radio" v-model="form.type" value="reminder" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">提醒任务</span>
            </label>
          </div>
        </div>

        <div class="form-group">
          <label class="oaa-label">任务名称</label>
          <input v-model="form.name" type="text" class="oaa-input" placeholder="输入任务名称" />
        </div>

        <div class="form-group">
          <label class="oaa-label">任务描述</label>
          <textarea v-model="form.description" class="oaa-input form-textarea" rows="2" placeholder="输入任务描述"></textarea>
        </div>

        <div class="form-group">
          <label class="oaa-label">执行周期</label>
          <select v-model="form.cycle" class="oaa-select">
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
          </select>
          <div v-if="form.cycle === 'weekly'" class="cycle-sub">
            <span v-for="d in weekDays" :key="d.value"
              :class="['day-chip', { active: form.cycleDay === d.value }]"
              @click="form.cycleDay = d.value">
              {{ d.label }}
            </span>
          </div>
          <div v-if="form.cycle === 'monthly'" class="cycle-sub">
            <select v-model.number="form.cycleDay" class="oaa-select cycle-select">
              <option v-for="d in 31" :key="d" :value="d">{{ d }} 号</option>
            </select>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group flex-1">
            <label class="oaa-label">开始时间</label>
            <div class="time-picker">
              <select v-model.number="form.startHour" class="oaa-select time-select">
                <option v-for="h in 24" :key="h-1" :value="h-1">{{ padZero(h-1) }}</option>
              </select>
              <span class="time-sep">:</span>
              <select v-model.number="form.startMinute" class="oaa-select time-select">
                <option v-for="m in 60" :key="m-1" :value="m-1">{{ padZero(m-1) }}</option>
              </select>
            </div>
          </div>
        </div>

        <div class="form-group">
          <label class="oaa-label">交付渠道</label>
          <div class="checkbox-group">
            <label v-for="ch in allChannels" :key="ch.value" class="checkbox-label">
              <input type="checkbox" :value="ch.value" v-model="form.channels" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text">{{ ch.label }}</span>
            </label>
          </div>
        </div>

        <div class="form-group">
          <label class="oaa-label">
            <span>任务汇报</span>
          </label>
          <div class="radio-group">
            <label class="radio-label">
              <input type="radio" v-model="form.report" :value="true" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">是</span>
            </label>
            <label class="radio-label">
              <input type="radio" v-model="form.report" :value="false" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">否</span>
            </label>
          </div>
          <div v-if="form.report" class="sub-option">
            <label class="oaa-label" style="margin-top: 8px;">汇报渠道</label>
            <div class="checkbox-group">
              <label v-for="ch in allChannels" :key="ch.value" class="checkbox-label">
                <input type="checkbox" :value="ch.value" v-model="form.reportChannels" class="checkbox-input" />
                <span class="checkbox-custom"></span>
                <span class="checkbox-text">{{ ch.label }}</span>
              </label>
            </div>
          </div>
        </div>

        <div v-if="form.type === 'reminder'" class="form-group">
          <label class="oaa-label">收到确认</label>
          <div class="radio-group">
            <label class="radio-label">
              <input type="radio" v-model="form.confirmReceipt" :value="true" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">是（用户必须回复"收到"，否则每 5 分钟重发）</span>
            </label>
            <label class="radio-label">
              <input type="radio" v-model="form.confirmReceipt" :value="false" class="radio-input" />
              <span class="radio-custom"></span>
              <span class="radio-text">否</span>
            </label>
          </div>
        </div>

        <div class="form-actions">
          <button class="oaa-btn oaa-btn--secondary" @click="resetForm">取消</button>
          <button class="oaa-btn oaa-btn--primary" @click="saveTask" :disabled="!form.name.trim() || saving">
            <span v-if="saving" class="task-btn-spinner"></span>
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 已完成 -->
    <div v-if="activeSubTab === 'done'" class="tab-content">
      <div class="section-header-inline">
        <h3>已完成任务</h3>
      </div>
      <div v-if="completedTasks.length === 0" class="empty-state">
        <p>暂无已完成任务</p>
      </div>
      <div v-else class="task-list">
        <div v-for="task in completedTasks" :key="task.id" class="task-card done">
          <div class="task-card-header">
            <span class="task-type-badge" :class="task.type === 'reminder' ? 'type-reminder' : 'type-fixed'">
              {{ task.type === 'reminder' ? '提醒' : '固定' }}
            </span>
            <span class="done-mark">✅ 已完成</span>
          </div>
          <div class="task-card-title">{{ task.name }}</div>
          <div class="task-card-desc">{{ task.description }}</div>
          <div class="task-card-meta">
            <span>🔄 {{ cycleLabel(task) }}</span>
            <span>⏰ {{ padZero(task.startHour) }}:{{ padZero(task.startMinute) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

type TaskType = 'fixed' | 'reminder'
type Cycle = 'daily' | 'weekly' | 'monthly'
type Channel = 'chat' | 'wechat' | 'dingtalk' | 'feishu' | 'email'

interface ScheduledTask {
  id: string
  type: TaskType
  name: string
  description: string
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

const { sendRequest } = useWebSocket()

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
  { id: 'new', icon: '➕', label: '新建' },
  { id: 'done', icon: '✅', label: '已完成' },
]

const activeSubTab = ref('active')
const loading = ref(true)
const saving = ref(false)

const defaultForm = {
  type: 'fixed' as TaskType,
  name: '',
  description: '',
  cycle: 'daily' as Cycle,
  cycleDay: 1,
  startHour: 9,
  startMinute: 0,
  channels: ['chat'] as Channel[],
  report: true,
  reportChannels: ['chat', 'wechat'] as Channel[],
  confirmReceipt: true,
}

const form = ref({ ...defaultForm })
const tasks = ref<ScheduledTask[]>([])

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
})

const activeTasks = computed(() => tasks.value.filter(t => t.status === 'active'))
const completedTasks = computed(() => tasks.value.filter(t => t.status === 'completed'))

function cycleLabel(task: ScheduledTask) {
  if (task.cycle === 'daily') return '每天'
  if (task.cycle === 'weekly') return `每周${weekDays.find(d => d.value === task.cycleDay)?.label || ''}`
  return `每月 ${task.cycleDay} 号`
}

function padZero(n: number) { return n.toString().padStart(2, '0') }

// ------------------------------------------------------------------
// CRUD
// ------------------------------------------------------------------

async function saveTask() {
  if (!form.value.name.trim()) return
  saving.value = true

  const task: ScheduledTask = {
    id: '',  // backend generates the id
    ...form.value,
    enabled: true,
    status: 'active',
    createdAt: new Date().toISOString(),
  }

  try {
    const resp = await sendRequest('save_task', { task: toBackendTask(task) })
    if (resp.ok && resp.task) {
      tasks.value.push(fromBackendTask(resp.task as BackendTask))
    } else {
      // Fallback: local save with generated id
      task.id = `task_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
      tasks.value.push(task)
    }
  } catch {
    // Offline: local save
    task.id = `task_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
    tasks.value.push(task)
    localStorage.setItem('oaa_tasks', JSON.stringify(tasks.value))
  }

  resetForm()
  activeSubTab.value = 'active'
  saving.value = false
}

function resetForm() {
  form.value = { ...defaultForm }
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

function copyTask(task: ScheduledTask) {
  form.value = { ...task, name: task.name + ' (副本)' }
  activeSubTab.value = 'new'
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
</style>

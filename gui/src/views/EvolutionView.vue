<template>
  <div class="view-container">
    <div class="view-header">
      <h2>进化工厂</h2>
      <p class="view-subtitle">自我改进提案管理、执行与回滚</p>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        :class="['tab-btn', { active: activeTab === tab.id }]"
        @click="activeTab = tab.id"
      >
        <span class="tab-icon">{{ tab.icon }}</span>
        <span class="tab-label">{{ tab.label }}</span>
        <span v-if="tab.id === 'pending' && pendingCount > 0" class="tab-badge">{{ pendingCount }}</span>
      </button>
    </div>

    <!-- ================================ -->
    <!-- 待处理提案 -->
    <!-- ================================ -->
    <div v-if="activeTab === 'pending'" key="pending" class="tab-content">
      <div v-if="loading" class="loading-row">
        <span class="loading-spinner"></span>
        <span>加载提案列表...</span>
      </div>

      <div v-else-if="pendingProposals.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
            <polyline points="22 4 12 14.01 9 11.01"/>
          </svg>
        </div>
        <p class="empty-text">暂无待处理提案</p>
        <p class="empty-hint">空闲巡检会自动生成改进提案，届时将显示在此处</p>
      </div>

      <div v-else class="proposal-list">
        <div v-for="prop in pendingProposals" :key="prop.id" class="oaa-card proposal-card">
          <div class="proposal-header">
            <span :class="['oaa-badge', typeBadgeClass(prop.type)]">{{ typeLabel(prop.type) }}</span>
            <span class="proposal-title">{{ prop.title }}</span>
            <span class="proposal-id">{{ prop.id }}</span>
          </div>

          <div class="proposal-body">
            <div v-if="prop.problem" class="proposal-field">
              <span class="field-label">问题</span>
              <p class="field-value">{{ prop.problem }}</p>
            </div>
            <div v-if="prop.benefit" class="proposal-field">
              <span class="field-label">收益</span>
              <p class="field-value benefit">{{ prop.benefit }}</p>
            </div>
            <div class="proposal-field">
              <span class="field-label">操作步骤</span>
              <ol class="action-list">
                <li v-for="(action, i) in prop.actions" :key="i" class="action-item">
                  <code class="action-tool">{{ action.tool }}</code>
                  <span v-if="action.description" class="action-desc">{{ action.description }}</span>
                  <div v-if="action.verify" class="action-verify">验证: {{ action.verify.description || action.verify.tool }}</div>
                </li>
              </ol>
            </div>
          </div>

          <div class="proposal-footer">
            <div class="footer-actions">
              <button class="oaa-btn oaa-btn--primary oaa-btn--sm" @click="approveProposal(prop.id)" :disabled="executing === prop.id">
                {{ executing === prop.id ? '执行中...' : '批准执行' }}
              </button>
              <button class="oaa-btn oaa-btn--secondary oaa-btn--sm" @click="ignoreProposal(prop.id, false)" :disabled="executing === prop.id">
                忽略本次
              </button>
              <button class="oaa-btn oaa-btn--ghost oaa-btn--sm ignore-forever" @click="ignoreProposal(prop.id, true)" :disabled="executing === prop.id">
                彻底忽略
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================ -->
    <!-- 执行历史 -->
    <!-- ================================ -->
    <div v-if="activeTab === 'history'" key="history" class="tab-content">
      <div v-if="loading" class="loading-row">
        <span class="loading-spinner"></span>
        <span>加载执行历史...</span>
      </div>

      <div v-else-if="historyProposals.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
        </div>
        <p class="empty-text">暂无执行历史</p>
        <p class="empty-hint">批准的提案执行后将在此处记录结果</p>
      </div>

      <div v-else class="proposal-list">
        <div v-for="prop in historyProposals" :key="prop.id" class="oaa-card history-card">
          <div class="history-header">
            <span :class="['oaa-badge', statusBadgeClass(prop.status)]">{{ statusLabel(prop.status) }}</span>
            <span class="history-title">{{ prop.title }}</span>
            <span class="history-date">{{ formatDate(prop.executed_at || prop.created_at) }}</span>
          </div>

          <div v-if="prop.result" class="history-result">
            <details>
              <summary class="result-summary">查看执行结果</summary>
              <pre class="result-json">{{ formatResult(prop.result) }}</pre>
            </details>
          </div>
          <div v-if="prop.error" class="history-error">
            <span class="error-label">错误:</span>
            <code>{{ prop.error }}</code>
          </div>
        </div>
      </div>
    </div>

    <!-- Toast notification -->
    <div v-if="toast.show" :class="['toast', toast.type]">
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest } = useWebSocket()

const activeTab = ref('pending')
const loading = ref(true)
const executing = ref('')
const proposals = ref<any[]>([])
const toast = ref({ show: false, type: 'success', message: '' })

const tabs = [
  { id: 'pending', icon: '📋', label: '待处理提案' },
  { id: 'history', icon: '📜', label: '执行历史' },
]

const pendingProposals = computed(() =>
  proposals.value.filter(p => p.status === 'pending')
)

const historyProposals = computed(() =>
  proposals.value.filter(p => p.status !== 'pending')
)

const pendingCount = computed(() => pendingProposals.value.length)

const typeLabels: Record<string, string> = {
  tool_fix: '工具修复',
  install_dep: '安装依赖',
  sop_optimize: 'SOP 优化',
  skill_crystallize: '技能固化',
  config_change: '配置变更',
}

function typeLabel(type: string): string {
  return typeLabels[type] || type
}

function typeBadgeClass(type: string): string {
  const map: Record<string, string> = {
    tool_fix: 'oaa-badge--error',
    install_dep: 'oaa-badge--warning',
    sop_optimize: 'oaa-badge--accent',
    skill_crystallize: 'oaa-badge--success',
    config_change: 'oaa-badge--count',
  }
  return map[type] || 'oaa-badge--count'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    done: '已完成',
    failed: '失败',
    pending: '待处理',
    running: '执行中',
    approved: '已批准',
    ignored_once: '已忽略',
    ignored_forever: '永久忽略',
    rolled_back: '已回滚',
  }
  return map[status] || status
}

function statusBadgeClass(status: string): string {
  const map: Record<string, string> = {
    done: 'oaa-badge--success',
    failed: 'oaa-badge--error',
    running: 'oaa-badge--warning',
    ignored_once: 'oaa-badge--count',
    ignored_forever: 'oaa-badge--count',
    rolled_back: 'oaa-badge--warning',
  }
  return map[status] || 'oaa-badge--count'
}

function formatDate(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatResult(resultStr: string): string {
  if (!resultStr) return '(空)'
  try {
    const parsed = JSON.parse(resultStr)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return resultStr
  }
}

function showToast(message: string, type: 'success' | 'error' = 'success') {
  toast.value = { show: true, type, message }
  setTimeout(() => { toast.value.show = false }, 3000)
}

async function loadProposals() {
  loading.value = true
  try {
    const resp = await sendRequest('list_proposals')
    if (resp.ok) {
      proposals.value = (resp.proposals || []).sort((a: any, b: any) => (b.created_at || 0) - (a.created_at || 0))
    } else {
      showToast(resp.error || '加载失败', 'error')
    }
  } catch (e: any) {
    showToast('加载提案失败: ' + (e.message || e), 'error')
  }
  loading.value = false
}

async function approveProposal(id: string) {
  executing.value = id
  try {
    const resp = await sendRequest('proposal_approve', { id })
    if (resp.ok) {
      showToast(`提案 ${id.slice(0, 20)} 执行完毕 (${resp.proposal_status})`)
      await loadProposals()
    } else {
      showToast(resp.error || '执行失败', 'error')
      await loadProposals()
    }
  } catch (e: any) {
    showToast('执行出错: ' + (e.message || e), 'error')
  }
  executing.value = ''
}

async function ignoreProposal(id: string, permanent: boolean) {
  try {
    const resp = await sendRequest('proposal_ignore', { id, permanent })
    if (resp.ok) {
      showToast(permanent ? '已彻底忽略' : '已忽略本次')
      await loadProposals()
    } else {
      showToast(resp.error || '忽略失败', 'error')
    }
  } catch (e: any) {
    showToast('忽略出错: ' + (e.message || e), 'error')
  }
}

onMounted(() => {
  loadProposals()
})
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
}
.view-header {
  margin-bottom: var(--oaa-space-6);
}
.view-header h2 {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}
.view-subtitle {
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-base);
}

/* Tab bar */
.tab-bar {
  display: flex;
  gap: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-6);
  border-bottom: 1px solid var(--oaa-border-subtle);
  padding-bottom: var(--oaa-space-2);
}
.tab-btn {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-family: inherit;
  font-size: var(--oaa-text-sm);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--oaa-transition-fast);
  position: relative;
}
.tab-btn:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--oaa-color-primary);
}
.tab-btn.active {
  background: var(--oaa-primary-light);
  color: var(--oaa-primary);
}
.tab-icon {
  font-size: var(--oaa-text-base);
}
.tab-badge {
  background: var(--oaa-primary);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: var(--oaa-radius-full);
  min-width: 18px;
  text-align: center;
}

/* Loading */
.loading-row {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-10) 0;
  justify-content: center;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}
.loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--oaa-border-default);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--oaa-space-12) 0;
  color: var(--oaa-color-muted);
}
.empty-text {
  font-size: var(--oaa-text-lg);
  margin-top: var(--oaa-space-4);
  color: var(--oaa-color-secondary);
}
.empty-hint {
  font-size: var(--oaa-text-sm);
  margin-top: var(--oaa-space-2);
}

/* Proposal cards */
.proposal-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
}

.proposal-card {
  overflow: hidden;
}
.proposal-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-4) var(--oaa-space-5);
  padding-bottom: 0;
  flex-wrap: wrap;
}
.proposal-title {
  font-size: var(--oaa-text-base);
  font-weight: 600;
  color: var(--oaa-color-primary);
  flex: 1;
}
.proposal-id {
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
}

.proposal-body {
  padding: var(--oaa-space-4) var(--oaa-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-3);
}
.proposal-field {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}
.field-label {
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-disabled);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.field-value {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  line-height: 1.5;
}
.field-value.benefit {
  color: var(--oaa-green-400);
}

.action-list {
  margin: 0;
  padding-left: var(--oaa-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
}
.action-item {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  line-height: 1.4;
}
.action-tool {
  background: var(--oaa-bg-input);
  color: var(--oaa-primary);
  padding: 1px 6px;
  border-radius: var(--oaa-radius-sm);
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
}
.action-desc {
  margin-left: var(--oaa-space-2);
}
.action-verify {
  margin-top: 2px;
  font-size: var(--oaa-text-xs);
  color: var(--oaa-green-400);
  opacity: 0.7;
}

.proposal-footer {
  padding: var(--oaa-space-3) var(--oaa-space-5);
  border-top: 1px solid var(--oaa-border-subtle);
}
.footer-actions {
  display: flex;
  gap: var(--oaa-space-2);
}
.ignore-forever {
  margin-left: auto;
  color: var(--oaa-color-disabled);
}
.ignore-forever:hover:not(:disabled) {
  color: var(--oaa-red-400);
  background: rgba(239, 68, 68, 0.1);
}

/* History cards */
.history-card {
  overflow: hidden;
}
.history-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-3) var(--oaa-space-5);
}
.history-title {
  font-size: var(--oaa-text-base);
  font-weight: 500;
  color: var(--oaa-color-primary);
  flex: 1;
}
.history-date {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
}
.history-result {
  padding: 0 var(--oaa-space-5) var(--oaa-space-3);
}
.result-summary {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  cursor: pointer;
  user-select: none;
  padding: var(--oaa-space-1) 0;
}
.result-summary:hover {
  color: var(--oaa-color-secondary);
}
.result-json {
  margin-top: var(--oaa-space-2);
  background: var(--oaa-bg-input);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
  padding: var(--oaa-space-3);
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-secondary);
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.history-error {
  padding: 0 var(--oaa-space-5) var(--oaa-space-3);
  display: flex;
  align-items: baseline;
  gap: var(--oaa-space-2);
}
.error-label {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-red-400);
  font-weight: 600;
}
.history-error code {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-red-400);
  font-family: var(--oaa-font-mono);
  background: rgba(239, 68, 68, 0.1);
  padding: 1px 6px;
  border-radius: var(--oaa-radius-sm);
}

/* Toast */
.toast {
  position: fixed;
  bottom: var(--oaa-space-8);
  right: var(--oaa-space-8);
  padding: var(--oaa-space-3) var(--oaa-space-5);
  border-radius: var(--oaa-radius-md);
  font-size: var(--oaa-text-sm);
  font-weight: 500;
  z-index: 1000;
  animation: toastIn 0.3s ease;
  box-shadow: var(--oaa-shadow-lg);
}
.toast.success {
  background: var(--oaa-green-600);
  color: #fff;
}
.toast.error {
  background: var(--oaa-red-500);
  color: #fff;
}
@keyframes toastIn {
  from { transform: translateY(20px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
</style>

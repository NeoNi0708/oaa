<template>
  <div class="view-container">
    <div class="view-header">
      <h2>运行时演进</h2>
      <p class="subtitle">管理运行时函数覆盖演进记录 — 在不修改源码的情况下修复或增强代码行为</p>
    </div>

    <div class="tab-bar">
      <button v-for="tab in tabs" :key="tab.id"
        :class="['tab-btn', { active: activeTab === tab.id }]"
        @click="activeTab = tab.id">
        <span class="tab-icon">{{ tab.icon }}</span>
        <span class="tab-label">{{ tab.label }}</span>
        <span v-if="tab.id === 'active' && activeCount > 0" class="tab-badge">{{ activeCount }}</span>
      </button>
    </div>

    <!-- Active patches -->
    <div v-if="activeTab === 'active'">
      <div v-if="loading" class="loading-hint">加载中...</div>
      <div v-else-if="patches.length === 0" class="empty-hint">
        暂无活跃演进记录
      </div>
      <div v-else class="patch-list">
        <div v-for="p in patches" :key="p.id" class="patch-card oaa-card">
          <div class="patch-header">
            <span class="patch-description">{{ p.description }}</span>
            <span :class="['oaa-badge', statusBadgeClass(p.status)]">{{ statusLabel(p.status) }}</span>
          </div>
          <div class="patch-meta">
            <span class="meta-item">
              <span class="meta-label">目标</span>
              <code class="meta-value">{{ p.target }}</code>
            </span>
            <span class="meta-item">
              <span class="meta-label">创建时间</span>
              <span class="meta-value">{{ formatTime(p.created_at) }}</span>
            </span>
            <span class="meta-item">
              <span class="meta-label">回滚</span>
              <span class="meta-value" :class="p.can_rollback ? 'text-success' : 'text-warning'">
                {{ p.can_rollback ? '支持' : '不支持（重启恢复）' }}
              </span>
            </span>
          </div>
          <div class="patch-actions">
            <button v-if="p.status === 'active'" class="oaa-btn btn-sm btn-danger"
              @click="confirmRemove(p)">删除演进</button>
          </div>
        </div>
      </div>
    </div>

    <!-- History -->
    <div v-if="activeTab === 'history'">
      <div v-if="loading" class="loading-hint">加载中...</div>
      <div v-else-if="history.length === 0" class="empty-hint">
        暂无历史记录
      </div>
      <div v-else class="patch-list">
        <div v-for="p in history" :key="p.id" class="patch-card oaa-card muted">
          <div class="patch-header">
            <span class="patch-description">{{ p.description }}</span>
            <span :class="['oaa-badge', statusBadgeClass(p.status)]">{{ statusLabel(p.status) }}</span>
          </div>
          <div class="patch-meta">
            <span class="meta-item">
              <span class="meta-label">ID</span>
              <code class="meta-value">{{ p.id }}</code>
            </span>
            <span class="meta-item">
              <span class="meta-label">目标</span>
              <code class="meta-value">{{ p.target }}</code>
            </span>
            <span class="meta-item">
              <span class="meta-label">创建时间</span>
              <span class="meta-value">{{ formatTime(p.created_at) }}</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Remove confirmation dialog -->
    <div v-if="removeTarget" class="dialog-overlay" @click.self="removeTarget = null">
      <div class="dialog">
        <h3>确认删除演进记录</h3>
        <p>确定要删除演进记录「{{ removeTarget.description }}」吗？</p>
        <p class="text-muted">{{ removeTarget.can_rollback ? '原始函数将被恢复。' : '无原始代码备份，函数将保持补丁状态，重启应用后恢复。' }}</p>
        <div class="dialog-actions">
          <button class="oaa-btn" @click="removeTarget = null">取消</button>
          <button class="oaa-btn btn-danger" @click="doRemove" :disabled="removing">
            {{ removing ? '删除中...' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Toast -->
    <div v-if="toast.show" :class="['toast', toast.type]">
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest, patchesUpdated } = useWebSocket()

interface PatchInfo {
  id: string
  description: string
  target: string
  status: string
  created_at: string
  can_rollback: boolean
}

const activeTab = ref('active')
const loading = ref(true)
const patches = ref<PatchInfo[]>([])
const history = ref<PatchInfo[]>([])
const toast = ref({ show: false, type: 'success', message: '' })
const removeTarget = ref<PatchInfo | null>(null)
const removing = ref(false)

const tabs = [
  { id: 'active', icon: '🛡️', label: '活跃演进' },
  { id: 'history', icon: '📋', label: '历史记录' },
]

const activeCount = computed(() => patches.value.length)

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    active: '活跃',
    removed: '已删除',
    remove_failed: '删除失败',
  }
  return map[status] || status
}

function statusBadgeClass(status: string): string {
  const map: Record<string, string> = {
    active: 'badge-success',
    removed: 'badge-secondary',
    remove_failed: 'badge-danger',
  }
  return map[status] || ''
}

function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return iso
  }
}

function showToast(msg: string, type: 'success' | 'error' = 'success') {
  toast.value = { show: true, type, message: msg }
  setTimeout(() => { toast.value.show = false }, 3000)
}

async function loadData() {
  loading.value = true
  try {
    const resp = await sendRequest('list_patches', { include_removed: false })
    if (resp.ok) {
      patches.value = (resp.patches || []) as PatchInfo[]
    } else {
      showToast(resp.error || '加载失败', 'error')
    }

    const histResp = await sendRequest('list_patches', { include_removed: true })
    if (histResp.ok) {
      const all = (histResp.patches || []) as PatchInfo[]
      history.value = all.filter((p: PatchInfo) => p.status !== 'active')
    }
  } catch (e: any) {
    showToast('加载演进记录失败: ' + (e.message || e), 'error')
  }
  loading.value = false
}

function confirmRemove(p: PatchInfo) {
  removeTarget.value = p
}

async function doRemove() {
  if (!removeTarget.value) return
  removing.value = true
  try {
    const resp = await sendRequest('remove_patch', { patch_id: removeTarget.value.id })
    if (resp.ok) {
      showToast('演进记录已删除')
      removeTarget.value = null
      await loadData()
    } else {
      showToast(resp.error || '删除失败', 'error')
    }
  } catch (e: any) {
    showToast('删除失败: ' + (e.message || e), 'error')
  }
  removing.value = false
}

onMounted(() => loadData())

watch(patchesUpdated, () => loadData())
</script>

<style scoped>
.view-container {
  padding: 24px 32px;
  max-width: 960px;
  margin: 0 auto;
}

.view-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--oaa-text-primary);
}

.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--oaa-text-secondary);
}

/* Tab bar */
.tab-bar {
  display: flex;
  gap: 4px;
  margin: 20px 0 16px;
  border-bottom: 1px solid var(--oaa-border);
  padding-bottom: 0;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--oaa-text-secondary);
  cursor: pointer;
  font-size: 14px;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: all .15s;
}

.tab-btn:hover {
  color: var(--oaa-text-primary);
  background: var(--oaa-bg-hover);
}

.tab-btn.active {
  color: var(--oaa-primary);
  border-bottom-color: var(--oaa-primary);
}

.tab-icon {
  font-size: 16px;
}

.tab-badge {
  background: var(--oaa-primary);
  color: white;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
  min-width: 18px;
  text-align: center;
}

/* Loading */
.loading-hint, .empty-hint {
  text-align: center;
  padding: 48px 0;
  color: var(--oaa-text-tertiary);
  font-size: 14px;
}

/* Patch cards */
.patch-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.patch-card {
  padding: 16px 20px;
}

.patch-card.muted {
  opacity: 0.7;
}

.patch-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.patch-description {
  font-size: 15px;
  font-weight: 500;
  color: var(--oaa-text-primary);
  flex: 1;
}

.patch-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 10px;
  font-size: 13px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.meta-label {
  color: var(--oaa-text-tertiary);
}

.meta-value {
  color: var(--oaa-text-secondary);
}

.meta-value code {
  background: var(--oaa-bg-hover);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--oaa-text-primary);
}

.text-success {
  color: var(--oaa-success, #22c55e);
}

.text-warning {
  color: var(--oaa-warning, #f59e0b);
}

.text-muted {
  color: var(--oaa-text-tertiary);
  font-size: 13px;
}

.patch-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

/* Badge overrides */
.badge-success {
  background: #dcfce7;
  color: #166534;
}

.badge-secondary {
  background: #f1f5f9;
  color: #475569;
}

.badge-danger {
  background: #fef2f2;
  color: #991b1b;
}

/* Dialog */
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  background: var(--oaa-bg-card);
  border-radius: 12px;
  padding: 24px;
  min-width: 360px;
  max-width: 480px;
  box-shadow: 0 8px 32px rgba(0,0,0,.15);
}

.dialog h3 {
  margin: 0 0 12px;
  font-size: 16px;
}

.dialog p {
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--oaa-text-secondary);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

/* Toast */
.toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 14px;
  z-index: 2000;
  box-shadow: 0 4px 16px rgba(0,0,0,.12);
}

.toast.success {
  background: #dcfce7;
  color: #166534;
}

.toast.error {
  background: #fef2f2;
  color: #991b1b;
}

/* Button */
.btn-danger {
  background: #ef4444;
  color: white;
  border: none;
}

.btn-danger:hover {
  background: #dc2626;
}

.btn-danger:disabled {
  opacity: .5;
  cursor: not-allowed;
}

.btn-sm {
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 6px;
}
</style>

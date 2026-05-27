<template>
  <div class="action-buttons" v-if="actions.length > 0">
    <button
      v-for="act in actions"
      :key="act.action_id"
      :class="['action-btn', statusClass(act)]"
      :disabled="isDisabled(act)"
      @click="handleClick(act)"
    >
      <span v-if="getStatus(act) === 'pending'" class="btn-spinner"></span>
      <span v-else-if="getStatus(act) === 'done'">✅</span>
      <span v-else-if="getStatus(act) === 'error'">❌</span>
      <span class="btn-label">{{ getStatus(act) === 'done' ? '已处理' : act.label }}</span>
    </button>
    <div v-if="errorMsg" class="action-error">{{ errorMsg }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { ActionDef } from '../utils/contentParser'

const props = defineProps<{
  actions: ActionDef[]
  disabled: boolean
  sendRequest: (type: string, payload?: Record<string, unknown>, timeout?: number) => Promise<any>
}>()

// Per-action status: 'idle' | 'pending' | 'done' | 'error'
const actionStatus = ref<Record<string, string>>({})
const errorMsg = ref('')

function getStatus(act: ActionDef): string {
  return actionStatus.value[act.action_id] || 'idle'
}

function statusClass(act: ActionDef): string {
  const s = getStatus(act)
  return {
    idle: '',
    pending: 'is-pending',
    done: 'is-done',
    error: 'is-error',
  }[s] || ''
}

function isDisabled(act: ActionDef): boolean {
  const s = getStatus(act)
  return props.disabled || s === 'pending' || s === 'done'
}

async function handleClick(act: ActionDef) {
  if (isDisabled(act)) return

  actionStatus.value[act.action_id] = 'pending'
  errorMsg.value = ''

  try {
    const resp = await props.sendRequest('chat_action', {
      action: act.action,
      args: act.args || {},
      action_id: act.action_id,
    })
    if (resp?.ok === false) {
      actionStatus.value[act.action_id] = 'error'
      errorMsg.value = resp.error || '操作失败'
      // Auto-clear error after 5s
      setTimeout(() => {
        if (actionStatus.value[act.action_id] === 'error') {
          actionStatus.value[act.action_id] = 'idle'
          errorMsg.value = ''
        }
      }, 5000)
    } else {
      actionStatus.value[act.action_id] = 'done'
    }
  } catch (e: any) {
    actionStatus.value[act.action_id] = 'error'
    errorMsg.value = e?.message || '请求失败'
  }
}

// On mount, query backend for processed statuses
onMounted(async () => {
  if (props.actions.length === 0) return
  const ids = props.actions.map(a => a.action_id)
  try {
    const resp = await props.sendRequest('get_action_status', { action_ids: ids })
    if (resp?.statuses) {
      for (const [id, status] of Object.entries(resp.statuses)) {
        if (status === 'done') {
          actionStatus.value[id] = 'done'
        }
      }
    }
  } catch {
    // Silently fail — buttons remain in 'idle' state
  }
})
</script>

<style scoped>
.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0;
}
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 6px;
  background: rgba(148, 163, 184, 0.08);
  color: var(--oaa-text, #e2e8f0);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}
.action-btn:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.15);
  border-color: rgba(59, 130, 246, 0.4);
}
.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.action-btn.is-pending {
  border-color: rgba(59, 130, 246, 0.5);
  background: rgba(59, 130, 246, 0.1);
}
.action-btn.is-done {
  border-color: rgba(34, 197, 94, 0.4);
  background: rgba(34, 197, 94, 0.1);
  cursor: default;
}
.action-btn.is-error {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.1);
}
.btn-label {
  font-weight: 500;
}
.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(148, 163, 184, 0.3);
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.action-error {
  width: 100%;
  font-size: 12px;
  color: var(--oaa-danger, #ef4444);
  padding: 4px 0;
}
</style>

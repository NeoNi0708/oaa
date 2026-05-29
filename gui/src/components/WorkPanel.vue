<template>
  <div v-if="hasEntries" class="work-panel" :class="{ collapsed }">
    <!-- Toggle bar -->
    <button class="work-toggle" @click="togglePanel" :title="collapsed ? '展开工作信息' : '折叠工作信息'">
      <span class="work-toggle-icon">{{ collapsed ? '▶' : '▼' }}</span>
      <span class="work-toggle-label">工作信息</span>
      <span class="work-count">{{ workEntries.length }}条</span>
      <span v-if="liveActive" class="work-live-dot"></span>
    </button>

    <!-- Content -->
    <div v-if="!collapsed" class="work-body" ref="workBody">
      <template v-for="group in stepGroups" :key="group.key">
        <!-- Step header (grouped by step_id) -->
        <div v-if="group.step_id" class="step-header">
          <span class="step-header-num">步骤 {{ group.step_id }}</span>
          <span class="step-header-status" :class="group.statusClass">{{ group.statusLabel }}</span>
          <span v-if="group.duration" class="step-header-duration">{{ group.duration.toFixed(1) }}s</span>
        </div>

        <!-- Entries inside this group -->
        <div v-for="entry in group.entries" :key="entry.id" :class="['work-entry', `work-${entry.type}`]">
          <!-- Status -->
          <div v-if="entry.type === 'status'" class="entry-line entry-status">
            <span class="entry-icon">⏳</span>
            <span class="entry-text">{{ entry.content }}</span>
          </div>

          <!-- Tool call -->
          <div v-else-if="entry.type === 'tool_call'" class="entry-line entry-tool">
            <span class="entry-icon">🔧</span>
            <span class="entry-name">{{ entry.name }}</span>
            <span v-if="entry.args" class="entry-args">{{ entry.args }}</span>
            <span v-if="entry.reasoning" class="entry-reasoning" :title="entry.reasoning">💭</span>
          </div>

          <!-- Tool result -->
          <div v-else-if="entry.type === 'tool_result'" class="entry-line entry-result">
            <span class="entry-icon">✅</span>
            <span class="entry-name">{{ entry.name }}</span>
            <span v-if="entry.result" class="entry-args">{{ entry.result }}</span>
          </div>

          <!-- LLM output (thinking) — collapsible per-turn -->
          <div v-else-if="entry.type === 'llm_output'" class="entry-line entry-thinking">
            <span class="entry-icon">💭</span>
            <span class="entry-text">{{ entry.content }}</span>
          </div>

          <!-- Error -->
          <div v-else-if="entry.type === 'error'" class="entry-line entry-error">
            <span class="entry-icon">❌</span>
            <span class="entry-text">{{ entry.content }}</span>
          </div>

          <!-- Done -->
          <div v-else-if="entry.type === 'done'" class="entry-line entry-done">
            <span class="entry-icon">✅</span>
            <span class="entry-text">{{ entry.content }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { WorkEntry } from '../composables/useWebSocket'

const props = defineProps<{
  workEntries: WorkEntry[]
  streaming: boolean
}>()

const collapsed = ref(true)
const workBody = ref<HTMLElement | null>(null)

const hasEntries = computed(() => props.workEntries.length > 0)
const liveActive = computed(() => props.streaming)

interface StepGroup {
  key: string
  step_id: number
  tool_name: string
  duration: number | null
  statusLabel: string
  statusClass: string
  entries: WorkEntry[]
}

const stepGroups = computed(() => {
  const groups: StepGroup[] = []
  let current: StepGroup | null = null

  for (const entry of props.workEntries) {
    if (entry.step_id && entry.step_id > 0) {
      // Start a new group if step_id changes
      if (!current || current.step_id !== entry.step_id) {
        const tool_name = entry.type === 'tool_call' ? (entry.name || '') : ''
        current = {
          key: `step-${entry.step_id}`,
          step_id: entry.step_id,
          tool_name,
          duration: null,
          statusLabel: '执行中',
          statusClass: 'step-running',
          entries: [],
        }
        groups.push(current)
      }

      // Track duration and status from tool_result
      if (entry.type === 'tool_result') {
        if (entry.duration) current.duration = entry.duration
        const isError = entry.result && (
          entry.result.startsWith('status: error') || entry.result.includes('"status": "error"')
        )
        current.statusLabel = isError ? '失败' : '完成'
        current.statusClass = isError ? 'step-error' : 'step-ok'
        // Store tool name from result if missing
        if (entry.name && !current.tool_name) current.tool_name = entry.name
      }

      current.entries.push(entry)
    } else {
      // No step_id — standalone entry (status, llm_output, etc.)
      current = {
        key: `no-step-${entry.id}`,
        step_id: 0,
        tool_name: '',
        duration: null,
        statusLabel: '',
        statusClass: '',
        entries: [entry],
      }
      groups.push(current)
    }
  }

  return groups
})

function togglePanel() {
  collapsed.value = !collapsed.value
}
</script>

<style scoped>
.work-panel {
  border-top: 1px solid var(--oaa-border-subtle);
  background: rgba(15, 23, 42, 0.6);
  backdrop-filter: blur(8px);
  flex-shrink: 0;
  max-height: 40vh;
  display: flex;
  flex-direction: column;
}

.work-panel.collapsed {
  max-height: none;
}

.work-toggle {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border: none;
  background: transparent;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-xs);
  cursor: pointer;
  transition: color var(--oaa-transition-fast), background var(--oaa-transition-fast);
  user-select: none;
}

.work-toggle:hover {
  color: var(--oaa-color-secondary);
  background: rgba(255, 255, 255, 0.03);
}

.work-toggle-icon {
  font-size: 8px;
  width: 12px;
  text-align: center;
}

.work-toggle-label {
  font-weight: 500;
}

.work-count {
  color: var(--oaa-color-disabled);
  font-variant-numeric: tabular-nums;
}

.work-live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--oaa-primary);
  animation: statusPulse 1.5s ease-in-out infinite;
}

@keyframes statusPulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.work-body {
  overflow-y: auto;
  padding: 0 var(--oaa-space-4) var(--oaa-space-2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.work-entry {
  font-size: var(--oaa-text-xs);
  line-height: 1.4;
}

.entry-line {
  display: flex;
  align-items: flex-start;
  gap: var(--oaa-space-2);
  padding: 2px 0;
}

.entry-icon {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  font-size: 11px;
}

.entry-text {
  color: var(--oaa-color-muted);
  word-break: break-word;
  min-width: 0;
}

.entry-name {
  color: var(--oaa-primary);
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
}

.entry-args {
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 400px;
}

/* Per-type styling */
.entry-tool .entry-name {
  font-weight: 500;
}

.entry-thinking .entry-text {
  color: var(--oaa-color-disabled);
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-thinking:not(:first-child) .entry-icon {
  opacity: 0.4;
}

.entry-error .entry-text {
  color: var(--oaa-error);
}

.entry-status .entry-text {
  color: var(--oaa-amber-400);
}

.work-body:hover .entry-thinking .entry-text {
  white-space: normal;
  overflow: visible;
}

.entry-done .entry-text {
  color: var(--oaa-green-400);
}

/* Step header */
.step-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: 4px 0 2px;
  margin-top: 4px;
  border-top: 1px solid rgba(255,255,255,0.06);
  font-size: var(--oaa-text-xs);
}
.step-header:first-child {
  border-top: none;
  margin-top: 0;
}
.step-header-num {
  color: var(--oaa-color-secondary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.step-header-status {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 4px;
}
.step-header-status.step-running {
  color: var(--oaa-amber-400);
  background: rgba(251, 191, 36, 0.1);
}
.step-header-status.step-ok {
  color: var(--oaa-green-400);
  background: rgba(34, 197, 94, 0.1);
}
.step-header-status.step-error {
  color: var(--oaa-error);
  background: rgba(239, 68, 68, 0.1);
}
.step-header-duration {
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
  font-size: 10px;
  margin-left: auto;
}

/* ------------------------------------------------------------------ */
/* Light theme — 暖米白                                                 */
/* ------------------------------------------------------------------ */
[data-theme="light"] .work-panel {
  background: var(--oaa-bg-surface);
  border-top-color: rgba(0, 0, 0, 0.08);
}

[data-theme="light"] .work-toggle {
  color: var(--oaa-color-secondary);
}

[data-theme="light"] .work-toggle:hover {
  background: rgba(184, 74, 58, 0.04);
  color: var(--oaa-blue-500);
}

[data-theme="light"] .work-step {
  background: var(--oaa-bg-app);
}

[data-theme="light"] .work-step:hover {
  background: var(--oaa-bg-surface-hover);
}
</style>

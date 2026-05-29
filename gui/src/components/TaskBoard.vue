<template>
  <div class="taskboard">
    <div class="taskboard-header">
      <span class="taskboard-title">任务清单</span>
      <span class="taskboard-progress">{{ completed }}/{{ total }} · {{ pct }}%</span>
    </div>
    <div class="taskboard-bar">
      <div class="taskboard-bar-fill" :style="{ width: pct + '%' }"></div>
    </div>
    <div class="taskboard-items">
      <div v-for="item in items" :key="item.id" class="task-item" :class="'task--' + item.status">
        <span class="task-icon">{{ statusIcon(item.status) }}</span>
        <span class="task-text">{{ item.content }}</span>
        <span v-if="item.done_criteria && item.status === 'in_progress'" class="task-criteria">{{ item.done_criteria }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ items: any[] }>()

const total = computed(() => props.items.length)
const completed = computed(() => props.items.filter(i => i.status === 'completed').length)
const pct = computed(() => total.value ? Math.round(completed.value / total.value * 100) : 0)

function statusIcon(s: string) {
  const icons: Record<string, string> = { pending: '⬜', in_progress: '🔄', completed: '✅', cancelled: '❌' }
  return icons[s] || '⬜'
}
</script>

<style scoped>
.taskboard {
  background: var(--oaa-bg-card, #fff);
  border: 1px solid var(--oaa-border, #e0e0e0);
  border-radius: 8px;
  padding: 12px;
  margin: 8px 0;
}
.taskboard-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.taskboard-title { font-size: 13px; font-weight: 600; }
.taskboard-progress { font-size: 11px; color: var(--oaa-color-muted, #888); }
.taskboard-bar { height: 4px; background: var(--oaa-bg-input, #eee); border-radius: 2px; margin-bottom: 10px; }
.taskboard-bar-fill { height: 100%; background: var(--oaa-primary, #4a6cf7); border-radius: 2px; transition: width 0.3s; }
.taskboard-items { display: flex; flex-direction: column; gap: 6px; }
.task-item { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; line-height: 1.4; }
.task-icon { flex-shrink: 0; }
.task-text { }
.task-criteria { font-size: 11px; color: var(--oaa-color-muted, #888); margin-left: auto; }
</style>

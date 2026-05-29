<template>
  <div class="file-card" :class="['file-type--' + fileType]">
    <div class="file-icon">{{ fileIcon }}</div>
    <div class="file-info">
      <span class="file-name">{{ title || fileName }}</span>
      <span class="file-meta">{{ fileType.toUpperCase() }} · {{ formatSize(size) }}</span>
    </div>
    <div class="file-actions">
      <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="openFile">打开文件</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  path: string
  fileType: string
  title: string
  size: number
}>()

const fileName = computed(() => props.path.split(/[\\/]/).pop() || '')

const fileIcon = computed(() => {
  const icons: Record<string, string> = {
    docx: '📄', xlsx: '📊', png: '🖼️', jpg: '🖼️', jpeg: '🖼️',
    pdf: '📕', svg: '🎨', txt: '📝', csv: '📋', json: '📋',
    py: '🐍', js: '📜', ts: '📜', html: '🌐', css: '🎨',
  }
  return icons[props.fileType] || '📎'
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1048576).toFixed(1) + 'MB'
}

function openFile() {
  // For Electron, use IPC to open the file
  if (window.oaa?.shell?.openPath) {
    window.oaa.shell.openPath(props.path)
  } else {
    // Fallback: create a download link (works for browser-accessible paths)
    const a = document.createElement('a')
    a.href = 'file:///' + props.path.replace(/\\/g, '/')
    a.download = fileName.value
    a.click()
  }
}
</script>

<style scoped>
.file-card {
  display: flex; align-items: center; gap: 12px;
  background: var(--oaa-bg-card, #fff);
  border: 1px solid var(--oaa-border, #e0e0e0);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 8px 0;
}
.file-icon { font-size: 28px; line-height: 1; }
.file-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.file-name { font-size: 13px; font-weight: 500; word-break: break-all; }
.file-meta { font-size: 11px; color: var(--oaa-color-muted, #888); }
.file-actions { flex-shrink: 0; }
</style>

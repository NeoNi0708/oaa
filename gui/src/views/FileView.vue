<template>
  <div class="view-container">
    <div class="view-header">
      <h2>文件</h2>
      <p class="view-subtitle">浏览工作空间目录结构</p>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="breadcrumb">
        <span class="crumb-item" @click="navigateTo(null)">工作空间</span>
        <span v-for="(part, idx) in currentPath" :key="idx" class="crumb-segment">
          <span class="crumb-sep">/</span>
          <span
            :class="['crumb-item', { active: idx === currentPath.length - 1 }]"
            @click="navigateTo(part)"
          >{{ part }}</span>
        </span>
      </div>
      <div class="toolbar-actions">
        <button class="oaa-btn oaa-btn--secondary oaa-btn--sm" @click="changeRoot">📂 切换目录</button>
      </div>
    </div>

    <!-- 列表表头 -->
    <div class="list-header">
      <span class="col-name">名称</span>
      <span class="col-type">类型</span>
      <span class="col-size">大小</span>
      <span class="col-modified">修改时间</span>
    </div>

    <!-- 文件列表 -->
    <div class="file-list">
      <div v-if="loading" class="empty-state loading-state">
        <span class="file-spinner"></span>
        <p>加载中...</p>
      </div>

      <template v-else>
        <div v-if="depth > 0" class="file-row parent-row" @click="goUp">
          <span class="file-icon">📂</span>
          <span class="file-name">..</span>
          <span class="file-type">上级目录</span>
          <span class="file-size">—</span>
          <span class="file-modified">—</span>
        </div>

        <div v-for="item in dirs" :key="item.name" class="file-row" @click="openDir(item)">
          <span class="file-icon">{{ item.icon }}</span>
          <span class="file-name">{{ item.name }}</span>
          <span class="file-type">文件夹</span>
          <span class="file-size">—</span>
          <span class="file-modified">{{ formatTime(item.modified) }}</span>
        </div>

        <div v-for="item in files" :key="item.name" class="file-row">
          <span class="file-icon">{{ fileIcon(item.name) }}</span>
          <span class="file-name">{{ item.name }}</span>
          <span class="file-type">{{ fileType(item.name) }}</span>
          <span class="file-size">{{ formatSize(item.size) }}</span>
          <span class="file-modified">{{ formatTime(item.modified) }}</span>
        </div>
      </template>
    </div>

    <div v-if="!loading && allItems.length === 0 && depth === 0" class="empty-state">
      <span class="empty-icon">📂</span>
      <p class="empty-title">目录为空</p>
      <p class="empty-desc">选择工作空间目录开始浏览</p>
    </div>

    <div class="status-bar">
      <span>{{ currentRoot || '未选择目录' }}</span>
      <span>{{ allItems.length }} 项</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

declare const oaa: any

interface DirEntry {
  name: string
  isDir: boolean
  size: number
  modified: string
}

const currentRoot = ref('')
const loading = ref(false)
const entries = ref<DirEntry[]>([])
const pathStack = ref<string[][]>([[]])
const depth = ref(0)

const currentPath = computed(() => pathStack.value[depth.value] || [])
const dirs = computed(() => entries.value.filter(e => e.isDir))
const files = computed(() => entries.value.filter(e => !e.isDir))
const allItems = computed(() => entries.value)

onMounted(async () => {
  const saved = localStorage.getItem('oaa_file_root')
  if (saved) {
    currentRoot.value = saved
    await loadDir()
  }
})

async function changeRoot() {
  if (window.oaa?.dialog?.openDirectory) {
    const dir = await window.oaa.dialog.openDirectory()
    if (dir) {
      currentRoot.value = dir
      localStorage.setItem('oaa_file_root', dir)
      pathStack.value = [[]]
      depth.value = 0
      await loadDir()
    }
  }
}

async function loadDir() {
  if (!currentRoot.value || !window.oaa?.fs?.readDir) return
  loading.value = true
  const subPath = pathStack.value[depth.value] || []
  const fullPath = subPath.length > 0
    ? currentRoot.value + '/' + subPath.join('/')
    : currentRoot.value
  const result = await window.oaa.fs.readDir(fullPath)
  entries.value = (result || []).sort((a: DirEntry, b: DirEntry) => {
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  loading.value = false
}

async function openDir(item: DirEntry) {
  if (!item.isDir) return
  const newPath = [...(pathStack.value[depth.value] || []), item.name]
  depth.value++
  pathStack.value[depth.value] = newPath
  await loadDir()
}

async function navigateTo(name: string | null) {
  if (name === null) {
    pathStack.value = [[]]
    depth.value = 0
    await loadDir()
    return
  }
  const idx = currentPath.value.indexOf(name)
  if (idx === -1) return
  depth.value = idx + 1
  await loadDir()
}

async function goUp() {
  if (depth.value <= 0) return
  depth.value--
  await loadDir()
}

function fileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase()
  if (['js', 'ts', 'py', 'java', 'go', 'rs'].includes(ext || '')) return '📄'
  if (['vue', 'html', 'css', 'scss', 'less'].includes(ext || '')) return '🎨'
  if (['json', 'xml', 'yaml', 'yml', 'toml'].includes(ext || '')) return '📋'
  if (['md', 'txt', 'doc', 'docx'].includes(ext || '')) return '📝'
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'ico'].includes(ext || '')) return '🖼️'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext || '')) return '🗜️'
  if (['xls', 'xlsx', 'csv'].includes(ext || '')) return '📊'
  if (['pdf'].includes(ext || '')) return '📕'
  if (['exe', 'msi', 'dmg', 'apk'].includes(ext || '')) return '⚙️'
  return '📄'
}

function fileType(name: string) {
  const ext = name.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    js: 'JavaScript', ts: 'TypeScript', py: 'Python', vue: 'Vue', html: 'HTML',
    css: 'CSS', json: 'JSON', md: 'Markdown', xml: 'XML', yaml: 'YAML',
    ico: '图标', png: '图片', jpg: '图片', pdf: 'PDF', docx: 'Word',
    xlsx: 'Excel', csv: 'CSV', zip: '压缩包', exe: '可执行文件',
  }
  return map[ext || ''] || (ext ? ext.toUpperCase() : '文件')
}

function formatSize(bytes: number) {
  if (bytes === 0) return '—'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

function formatTime(iso: string) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
  display: flex;
  flex-direction: column;
  height: 100%;
}

.view-header { margin-bottom: var(--oaa-space-5); }

.view-header h2 {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}

.view-subtitle { color: var(--oaa-color-muted); font-size: var(--oaa-text-base); }

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  margin-bottom: var(--oaa-space-1);
}

.breadcrumb { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; }

.crumb-item {
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
  cursor: pointer;
  transition: color var(--oaa-transition-fast);
  padding: 2px 4px;
  border-radius: var(--oaa-radius-sm);
}
.crumb-item:hover { color: var(--oaa-color-primary); background: var(--oaa-primary-light); }
.crumb-item.active { color: var(--oaa-primary); font-weight: 600; cursor: default; }
.crumb-sep { color: var(--oaa-color-disabled); margin: 0 2px; font-size: var(--oaa-text-sm); }
.crumb-segment { display: inline-flex; align-items: center; }

.toolbar-actions { display: flex; gap: var(--oaa-space-1); }

.list-header {
  display: grid;
  grid-template-columns: 3fr 1fr 1fr 1.5fr;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-disabled);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--oaa-border-subtle);
  margin-top: var(--oaa-space-2);
}

.file-list { flex: 1; overflow-y: auto; }

.file-row {
  display: grid;
  grid-template-columns: 3fr 1fr 1fr 1.5fr;
  gap: var(--oaa-space-2);
  align-items: center;
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border-bottom: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-sm);
  cursor: default;
  transition: background var(--oaa-transition-fast);
}
.file-row:hover { background: var(--oaa-primary-light); }

.parent-row { cursor: pointer; color: var(--oaa-primary); }
.parent-row:hover { background: var(--oaa-primary-light); }

.file-icon { font-size: 1.05rem; margin-right: var(--oaa-space-2); flex-shrink: 0; }

.file-name {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.parent-row .file-name { color: var(--oaa-primary); font-weight: 600; }

.file-type, .file-size, .file-modified {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-disabled);
  font-variant-numeric: tabular-nums;
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--oaa-color-disabled);
  padding: var(--oaa-space-12);
}

.empty-icon { font-size: 3rem; margin-bottom: var(--oaa-space-3); opacity: 0.5; }
.empty-title { font-size: var(--oaa-text-lg); font-weight: 600; color: var(--oaa-color-muted); margin-bottom: var(--oaa-space-1); }
.empty-desc { font-size: var(--oaa-text-sm); color: var(--oaa-color-disabled); }

.loading-state { gap: var(--oaa-space-2); }
.file-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: fileSpin 0.6s linear infinite;
}

@keyframes fileSpin {
  to { transform: rotate(360deg); }
}

.status-bar {
  padding: var(--oaa-space-2) var(--oaa-space-4);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  border-top: 1px solid var(--oaa-border-subtle);
  margin-top: var(--oaa-space-1);
  display: flex;
  justify-content: space-between;
}
</style>

<template>
  <div class="chat-view">
    <!-- Error fallback -->
    <div v-if="crashError" class="chat-error-fallback">
      <div class="error-icon">⚠</div>
      <h3>聊天组件异常</h3>
      <pre class="error-detail">{{ crashError }}</pre>
    </div>

    <!-- Header -->
    <div class="chat-header">
      <div class="chat-header-info">
        <div class="title-row">
          <h1 class="chat-title">二愣</h1>
          <span :class="['phase-pill', agentPhase]">{{ agentPhaseLabel }}</span>
          <div class="model-selector" v-if="Object.keys(flattenedModelList).length > 0">
            <button class="model-btn" @click.stop="toggleModelMenu" title="切换模型">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 1 0 10 10H12V2z"/><path d="M12 2a10 10 0 0 1 10 10h-10V2z"/></svg>
              <span class="model-label">{{ activeModelLabel }}</span>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <div v-if="showModelMenu" class="model-menu" @click.stop>
              <div
                v-for="item in flattenedModelList"
                :key="item.key"
                :class="['model-item', { active: item.key === activeModelKey }]"
                @click="switchModel(item.provider, item.model_id)"
              >
                <div class="model-item-name">{{ item.label }}</div>
                <div class="model-item-meta">{{ item.model_id || '未配置' }}</div>
              </div>
            </div>
          </div>
        </div>
        <span class="chat-subtitle">OPC AI 助手</span>
      </div>
      <div class="chat-header-metrics">
        <div class="metric-item" title="已处理对话">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          <span>{{ chatCount }}</span>
        </div>
        <div class="metric-item" title="运行时长">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <span>{{ formatUptime() }}</span>
        </div>
        <div class="connection-mini">
          <span :class="['conn-mini-dot', connected ? 'online' : 'offline']"></span>
        </div>
      </div>
    </div>

    <!-- Status bar -->
    <div v-if="statusText || currentTool" class="status-bar">
      <div v-if="currentTool" class="tool-indicator">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
        </svg>
        <span>Using: {{ currentTool.name }}</span>
      </div>
      <div v-else class="status-indicator">
        <span class="status-pulse"></span>
        <span>{{ statusText }}</span>
      </div>
    </div>

    <!-- Messages -->
    <div class="messages" ref="msgContainer">
      <div v-if="messages.length === 0 && !streaming" class="welcome">
        <div class="welcome-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </div>
        <h2 class="welcome-title">你好，我是二愣</h2>
        <p class="welcome-desc">随时可以找我聊天、查资料、处理文档</p>
        <div class="welcome-hints">
          <div class="hint-chip" @click="sendHint('帮我做一个报价单')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            做报价单
          </div>
          <div class="hint-chip" @click="sendHint('查一下最近的客户邮件')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
            查邮件
          </div>
          <div class="hint-chip" @click="sendHint('现在有哪些进行中的任务？')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/></svg>
            查看任务
          </div>
        </div>
      </div>

      <div v-for="(msg, i) in messages" :key="i" :class="['msg-row', msg.role]">
        <div class="msg-avatar">
          <span v-if="msg.role === 'assistant'" class="avatar-bot">二</span>
          <span v-else class="avatar-user">恒</span>
        </div>
        <div class="msg-content">
          <div class="msg-sender">{{ roleLabel(msg) }}</div>
          <div class="msg-bubble" v-html="renderContent(msg.content)"></div>
        </div>
      </div>

      <!-- Streaming message (live LLM output) -->
      <div v-if="streaming && streamingContent" class="msg-row assistant">
        <div class="msg-avatar">
          <span class="avatar-bot">二</span>
        </div>
        <div class="msg-content">
          <div class="msg-sender">二愣</div>
          <div class="msg-bubble streaming" v-html="renderContent(streamingContent)"></div>
        </div>
      </div>

      <!-- Loading pulse (before first token) -->
      <div v-if="loading && !streaming" class="msg-row assistant">
        <div class="msg-avatar">
          <span class="avatar-bot">二</span>
        </div>
        <div class="msg-content">
          <div class="msg-sender">二愣</div>
          <div class="msg-bubble thinking">
            <span class="dot-pulse"></span>
          </div>
        </div>
      </div>
    </div>

    <!-- Confirmation dialog -->
    <div v-if="confirmRequest" class="confirm-overlay" @click.self="respondToConfirm(false)">
      <div class="confirm-dialog">
        <div class="confirm-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <h3 class="confirm-title">操作确认</h3>
        <p class="confirm-op">{{ opLabel(confirmRequest.operation) }}</p>
        <p v-if="confirmRequest.details" class="confirm-details">{{ confirmRequest.details }}</p>
        <div class="confirm-actions">
          <button class="oaa-btn oaa-btn--secondary" @click="respondToConfirm(false)">取消</button>
          <button class="oaa-btn oaa-btn--primary" @click="respondToConfirm(true)">确认</button>
        </div>
      </div>
    </div>

    <!-- QR Code overlay -->
    <div v-if="qrCode" class="qr-overlay" @click.self="clearQRCode">
      <div class="qr-card">
        <button class="qr-close" @click="clearQRCode">&times;</button>
        <h3 class="qr-title">扫码登录</h3>
        <p class="qr-channel">{{ qrCode.channel === 'wechat' ? '微信' : qrCode.channel }}</p>
        <img class="qr-image" :src="qrCode.url" alt="QR Code" />
        <p class="qr-hint">请使用{{ qrCode.channel === 'wechat' ? '微信' : qrCode.channel }}扫描二维码登录</p>
      </div>
    </div>

    <!-- Input -->
    <div class="input-area">
      <button class="attach-btn" @click="triggerAttach" title="附件">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
        </svg>
      </button>
      <button v-if="loading || streaming" class="stop-btn" @click="stopAgent" title="停止生成">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
        <span class="stop-label">停止</span>
      </button>
      <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,.json,.py,.js,.ts,.vue,.html,.css,.md" @change="handleFiles" style="display:none" />
      <textarea
        v-model="input"
        @keydown.enter.prevent="sendMsg"
        placeholder="输入消息，Enter 发送"
        rows="1"
        class="input-field"
        @input="autoResize"
      ></textarea>
      <button
        class="send-btn"
        @click="sendMsg"
        :disabled="!input.trim() || !connected"
        title="发送"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted, onErrorCaptured } from 'vue'
import { marked } from 'marked'
import { useWebSocket, type ChatMessage } from '../composables/useWebSocket'
import { useAgentStatus } from '../composables/useAgentStatus'

interface ModelInfo {
  name: string
  api_key: string
  model_id: string
  base_url: string
}

const {
  connected,
  messages,
  streaming,
  streamingContent,
  qrCode,
  statusText,
  currentTool,
  confirmRequest,
  send,
  sendRequest,
  respondToConfirm,
  clearQRCode,
} = useWebSocket()

const { phase: agentPhase, phaseLabel: agentPhaseLabel, chatCount, formatUptime } = useAgentStatus(sendRequest)

// Error boundary — captures render errors so we see the error, not a black screen
const crashError = ref<string | null>(null)
onErrorCaptured((err: Error) => {
  crashError.value = `${err.message}\n${err.stack?.split('\n').slice(0, 3).join('\n') || ''}`
  console.error('[ChatView] Error captured:', err)
  return false // let error propagate
})

const input = ref('')
const loading = ref(false)
const msgContainer = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const MAX_FILE_SIZE = 10 * 1024 * 1024  // 10 MB

function triggerAttach() {
  fileInput.value?.click()
}

async function handleFiles(e: Event) {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return

  const parts: string[] = []
  for (const file of Array.from(files)) {
    if (file.size > MAX_FILE_SIZE) {
      parts.push(`[文件: ${file.name} (超过 10MB 限制，已跳过)]`)
      continue
    }
    const base64 = await fileToBase64(file)
    if (file.type.startsWith('image/')) {
      parts.push(`[图片: ${file.name}](data:${file.type};base64,${base64})`)
    } else {
      const text = await readFileAsText(file).catch(() => '')
      if (text) {
        parts.push(`[文件: ${file.name}]\n\`\`\`\n${text.slice(0, 50000)}\n\`\`\``)
      } else {
        parts.push(`[文件: ${file.name}](data:${file.type || 'application/octet-stream'};base64,${base64})`)
      }
    }
  }
  target.value = ''  // reset so same file can be re-selected

  if (parts.length === 0) return

  // Prepend to input or send directly
  const text = parts.join('\n\n')
  if (input.value.trim()) {
    input.value += '\n\n' + text
  } else {
    input.value = text
  }
  sendMsg()
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      resolve(result.split(',')[1] || '')
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsText(file)
  })
}

// Model switching — supports multiple entries per provider
interface ModelListEntry {
  provider: string
  model_id: string
  api_key: string
  base_url: string
  name: string
}

const modelList = ref<Record<string, ModelListEntry[]>>({})
const activeProvider = ref('')
const activeModelId = ref('')
const showModelMenu = ref(false)
const activeModelLabel = ref('')

const activeModelKey = computed(() => `${activeProvider.value}::${activeModelId.value}`)

/** Flatten provider→entries into a single sorted list for the dropdown. */
const flattenedModelList = computed(() => {
  const result: { key: string; provider: string; model_id: string; label: string }[] = []
  for (const [prov, entries] of Object.entries(modelList.value)) {
    for (const entry of entries) {
      if (!entry.api_key && !entry.model_id) continue
      const key = `${prov}::${entry.model_id}`
      const label = entry.name
        ? `${providerLabel(prov)}[${entry.name}]`
        : `${providerLabel(prov)}: ${entry.model_id}`
      result.push({ key, provider: prov, model_id: entry.model_id, label })
    }
  }
  // Put active provider first, then alphabetical
  result.sort((a, b) => {
    if (a.provider === activeProvider.value && b.provider !== activeProvider.value) return -1
    if (a.provider !== activeProvider.value && b.provider === activeProvider.value) return 1
    return a.label.localeCompare(b.label)
  })
  return result
})

function toggleModelMenu() { showModelMenu.value = !showModelMenu.value }

function providerLabel(prov: string) {
  const labels: Record<string, string> = {
    deepseek: 'DeepSeek', volcengine: '豆包', tongyi: '通义千问',
    siliconflow: 'SiliconFlow', zhipu: '智谱', moonshot: 'Kimi',
    baichuan: '百川', stepfun: '阶跃星辰', minimax: 'MiniMax',
    lingyi: '零一万物', xunfei: '讯飞星辰', xiaomi: '小米',
    openai: 'OpenAI', anthropic: 'Claude',
    'custom-openai': '自定义(OpenAI)', 'custom-anthropic': '自定义(Anthropic)',
  }
  return labels[prov] || prov
}

async function loadModels() {
  try {
    const resp = await sendRequest('get_models')
    if (resp.ok) {
      modelList.value = (resp.models as Record<string, ModelListEntry[]>) || {}
      activeProvider.value = (resp.active as string) || ''
      activeModelId.value = (resp.active_model_id as string) || ''
      updateActiveModelLabel()
    }
  } catch { /* ignore */ }
}

async function switchModel(provider: string, model_id?: string) {
  const key = `${provider}::${model_id}`
  if (key === activeModelKey.value && model_id) { showModelMenu.value = false; return }
  showModelMenu.value = false
  try {
    const resp = await sendRequest('switch_model', { provider, model_id: model_id || '' })
    if (resp.ok) {
      activeProvider.value = provider
      activeModelId.value = (resp.model_id as string) || model_id || ''
      updateActiveModelLabel()
    }
  } catch { /* ignore */ }
}

function updateActiveModelLabel() {
  const entries = modelList.value[activeProvider.value] || []
  const entry = entries.find(e => e.model_id === activeModelId.value) || entries[0]
  if (entry?.model_id) {
    activeModelLabel.value = entry.name
      ? `${providerLabel(activeProvider.value)}[${entry.name}]`
      : `${providerLabel(activeProvider.value)}: ${entry.model_id}`
  } else {
    activeModelLabel.value = providerLabel(activeProvider.value)
  }
}

function onDocumentClick() { showModelMenu.value = false }

onMounted(() => {
  if (connected.value) { loadModels() }
  document.addEventListener('click', onDocumentClick)
})
onUnmounted(() => { document.removeEventListener('click', onDocumentClick) })

// Retry model loading when WebSocket connects
let _modelWatchStop: ReturnType<typeof watch> | null = null
_modelWatchStop = watch(connected, (val) => {
  if (val && Object.keys(modelList.value).length === 0) {
    loadModels()
    if (_modelWatchStop) { _modelWatchStop(); _modelWatchStop = null }
  }
})

function renderContent(text: any) {
  const safe = typeof text === 'string' ? text : JSON.stringify(text, null, 2)
  try {
    return marked.parse(safe, { async: false, breaks: true })
      .replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ')
  } catch (e) {
    console.error('renderContent error:', e)
    return safe // 如果解析失败，直接显示原始内容
  }
}

function roleLabel(msg: ChatMessage) {
  if (msg.role === 'user') return '恒总'
  if (msg.role === 'assistant') return '二愣'
  if (msg.role === 'tool') return 'Tool'
  return ''
}

const opLabels: Record<string, string> = {
  email_send: '发送邮件',
  wechat_send: '发送微信消息',
}

function opLabel(operation: string) {
  return opLabels[operation] || operation
}

function sendMsg() {
  if (!input.value.trim()) return
  loading.value = true
  send(input.value.trim())
  input.value = ''
  nextTick(() => scrollToBottom())
}

function sendHint(text: string) {
  input.value = text
  sendMsg()
}

// Loading stops when streaming starts or first assistant message appears
watch([streaming, messages], () => {
  if (streaming.value || messages.value.length > 0) {
    loading.value = false
  }
  nextTick(() => scrollToBottom())
})

// Auto-scroll on streaming content changes
watch(streamingContent, () => {
  nextTick(() => scrollToBottom())
})

function scrollToBottom() {
  if (msgContainer.value) {
    msgContainer.value.scrollTop = msgContainer.value.scrollHeight
  }
}

function stopAgent() {
  loading.value = false
  streaming.value = false
  streamingContent.value = ''
  statusText.value = ''
  // Send stop signal so backend can abort the chat task
  sendRequest('stop_chat', {}).catch(() => {})
}

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}
</script>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* --- Header --- */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--oaa-space-4) var(--oaa-space-6);
  border-bottom: 1px solid var(--oaa-glass-border);
  background: rgba(30, 41, 59, 0.5);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

.chat-title {
  font-size: var(--oaa-text-lg);
  font-weight: 700;
  background: linear-gradient(135deg, var(--oaa-slate-50), var(--oaa-slate-300));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.title-row {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
}

/* Phase pill */
.phase-pill {
  font-size: var(--oaa-text-xs);
  padding: 2px 10px;
  border-radius: var(--oaa-radius-full);
  background: rgba(34, 197, 94, 0.12);
  color: var(--oaa-green-400);
  font-weight: 500;
  transition: background 0.3s ease, color 0.3s ease;
}

.phase-pill.thinking {
  background: rgba(59, 130, 246, 0.15);
  color: var(--oaa-blue-400);
  animation: pillPulse 1.2s ease-in-out infinite;
}

.phase-pill.executing {
  background: rgba(245, 158, 11, 0.15);
  color: var(--oaa-amber-400);
  animation: pillPulse 0.6s ease-in-out infinite;
}

.phase-pill.responding {
  background: rgba(139, 92, 246, 0.15);
  color: var(--oaa-purple-400);
  animation: pillPulse 0.8s ease-in-out infinite;
}

@keyframes pillPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.chat-subtitle {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  margin-top: 2px;
}

/* --- Model selector --- */
.model-selector {
  position: relative;
  margin-left: var(--oaa-space-1);
}

.model-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--oaa-space-1);
  padding: 1px 8px;
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-sm);
  background: rgba(255, 255, 255, 0.04);
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-xs);
  cursor: pointer;
  transition: color var(--oaa-transition-fast), background var(--oaa-transition-fast);
  white-space: nowrap;
}
.model-btn:hover {
  color: var(--oaa-color-secondary);
  background: rgba(255, 255, 255, 0.08);
}

.model-label {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 180px;
  max-height: 280px;
  overflow-y: auto;
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-md);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  z-index: 100;
  padding: var(--oaa-space-1);
}

.model-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: var(--oaa-space-2) var(--oaa-space-3);
  border-radius: var(--oaa-radius-sm);
  cursor: pointer;
  transition: background var(--oaa-transition-fast);
}
.model-item:hover { background: var(--oaa-primary-light); }
.model-item.active { background: rgba(59, 130, 246, 0.15); }

.model-item-name {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-primary);
  font-weight: 500;
}
.model-item-meta {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
}

/* Header metrics */
.chat-header-metrics {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-4);
}

.metric-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  font-variant-numeric: tabular-nums;
}

.metric-item svg {
  opacity: 0.4;
}

.connection-mini {
  display: flex;
  align-items: center;
  margin-left: var(--oaa-space-1);
}

.conn-mini-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transition: background 0.3s ease, box-shadow 0.3s ease;
}

.conn-mini-dot.online {
  background: var(--oaa-green-500);
  box-shadow: 0 0 6px var(--oaa-success-glow);
}

.conn-mini-dot.offline {
  background: var(--oaa-error);
}

/* --- Messages area --- */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: var(--oaa-space-6);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
}

/* Welcome */
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-3);
  color: var(--oaa-color-muted);
  text-align: center;
  padding: var(--oaa-space-10);
}

.welcome-icon {
  opacity: 0.15;
  margin-bottom: var(--oaa-space-2);
  color: var(--oaa-blue-400);
}

.welcome-title {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  background: linear-gradient(135deg, var(--oaa-slate-100), var(--oaa-blue-400), var(--oaa-slate-300));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.welcome-desc {
  font-size: var(--oaa-text-base);
  color: var(--oaa-color-muted);
  margin-bottom: var(--oaa-space-4);
  opacity: 0.7;
}

.welcome-hints {
  display: flex;
  gap: var(--oaa-space-3);
  flex-wrap: wrap;
  justify-content: center;
}

.hint-chip {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-3) var(--oaa-space-5);
  background: rgba(30, 41, 59, 0.6);
  border: 1px solid var(--oaa-glass-border);
  backdrop-filter: blur(8px);
  border-radius: var(--oaa-radius-full);
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-sm);
  cursor: pointer;
  transition:
    border-color var(--oaa-transition-base),
    background var(--oaa-transition-base),
    transform var(--oaa-transition-base),
    box-shadow var(--oaa-transition-base);
}
.hint-chip:hover {
  border-color: var(--oaa-primary);
  background: var(--oaa-primary-light);
  color: var(--oaa-primary);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
}

/* Message row */
.msg-row {
  display: flex;
  gap: var(--oaa-space-3);
  max-width: 80%;
  animation: fadeSlideIn 0.35s var(--oaa-ease-out-expo);
}
.msg-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

.msg-avatar {
  flex-shrink: 0;
}

.avatar-bot, .avatar-user {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--oaa-radius-md);
  font-size: var(--oaa-text-xs);
  font-weight: 700;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.avatar-bot {
  background: linear-gradient(135deg, var(--oaa-blue-500), var(--oaa-blue-600));
  color: #fff;
}
.avatar-user {
  background: linear-gradient(135deg, var(--oaa-purple-400), var(--oaa-purple-500));
  color: #fff;
}

.msg-content {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}

.msg-sender {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  padding: 0 var(--oaa-space-1);
}

.msg-bubble {
  padding: var(--oaa-space-3) var(--oaa-space-4);
  border-radius: var(--oaa-radius-lg);
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(30, 41, 59, 0.4));
  border: 1px solid var(--oaa-glass-border);
  font-size: var(--oaa-text-sm);
  line-height: 1.65;
  color: var(--oaa-color-primary);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
}

.msg-row.user .msg-bubble {
  background: linear-gradient(135deg, var(--oaa-blue-600), var(--oaa-blue-700));
  border-color: rgba(255, 255, 255, 0.1);
  color: #fff;
  box-shadow: 0 2px 12px rgba(59, 130, 246, 0.25);
}

.msg-bubble :deep(pre) {
  background: var(--oaa-bg-input);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
  padding: var(--oaa-space-3);
  margin: var(--oaa-space-2) 0;
  overflow-x: auto;
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-sm);
}

.msg-bubble :deep(code) {
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-sm);
  background: var(--oaa-bg-input);
  padding: 2px 6px;
  border-radius: 4px;
}

.msg-bubble :deep(p) {
  margin: var(--oaa-space-1) 0;
}

.msg-bubble :deep(ul), .msg-bubble :deep(ol) {
  padding-left: var(--oaa-space-5);
  margin: var(--oaa-space-1) 0;
}

.msg-bubble :deep(a) {
  color: var(--oaa-blue-400);
  text-decoration: none;
}
.msg-bubble :deep(a:hover) {
  text-decoration: underline;
}

.thinking {
  min-width: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Three-dot loading */
.dot-pulse {
  display: inline-flex;
  gap: 5px;
}
.dot-pulse::before,
.dot-pulse::after,
.dot-pulse {
  /* use a wrapper span in template to get 3 dots */
}
.dot-pulse::before,
.dot-pulse::after {
  content: '';
  width: 6px;
  height: 6px;
  background: var(--oaa-color-muted);
  border-radius: 50%;
  animation: dotBounce 1.4s infinite ease-in-out;
}
.dot-pulse::before { animation-delay: 0s; }
.dot-pulse::after { animation-delay: 0.2s; }

@keyframes dotBounce {
  0%, 80%, 100% { opacity: 0.2; transform: scale(0.7); }
  40% { opacity: 1; transform: scale(1); }
}

/* --- Input area --- */
.input-area {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  margin: 0 var(--oaa-space-4) var(--oaa-space-3);
  padding: var(--oaa-space-1) var(--oaa-space-3);
  border: 1px solid var(--oaa-border-default);
  border-radius: var(--oaa-radius-lg);
  background: rgba(30, 41, 59, 0.35);
  min-height: 44px;
}

.input-field {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--oaa-color-primary);
  font-family: inherit;
  font-size: var(--oaa-text-sm);
  line-height: 1.3;
  padding: 0;
  resize: none;
  outline: none;
  max-height: 160px;
  align-self: center;
}
.input-field::placeholder {
  color: var(--oaa-color-disabled);
}

.attach-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-muted);
  cursor: pointer;
  transition: color var(--oaa-transition-fast), background var(--oaa-transition-fast);
  flex-shrink: 0;
}
.attach-btn:hover {
  color: var(--oaa-color-secondary);
  background: rgba(255, 255, 255, 0.06);
}

.stop-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--oaa-space-1);
  padding: 0 var(--oaa-space-3);
  height: 32px;
  border: none;
  border-radius: var(--oaa-radius-md);
  background: rgba(239, 68, 68, 0.18);
  color: var(--oaa-error);
  cursor: pointer;
  transition: background var(--oaa-transition-fast), transform var(--oaa-transition-fast);
  flex-shrink: 0;
  animation: stopPulse 1.2s ease-in-out infinite;
  font-size: var(--oaa-text-xs);
  font-weight: 500;
  white-space: nowrap;
}
.stop-btn:hover {
  background: rgba(239, 68, 68, 0.3);
}
.stop-label {
  line-height: 1;
}

@keyframes stopPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
}

.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--oaa-radius-md);
  background: linear-gradient(135deg, var(--oaa-blue-500), var(--oaa-blue-600));
  color: #fff;
  cursor: pointer;
  transition:
    transform var(--oaa-transition-fast),
    box-shadow var(--oaa-transition-fast),
    opacity var(--oaa-transition-fast);
  flex-shrink: 0;
}
.send-btn:hover:not(:disabled) {
  transform: scale(1.05);
  box-shadow: 0 4px 16px rgba(59, 130, 246, 0.4);
}
.send-btn:active:not(:disabled) {
  transform: scale(0.95);
}
.send-btn:disabled {
  opacity: 0.25;
  cursor: not-allowed;
}

/* --- Status bar --- */
.status-bar {
  display: flex;
  align-items: center;
  padding: var(--oaa-space-2) var(--oaa-space-6);
  background: var(--oaa-bg-surface);
  border-bottom: 1px solid var(--oaa-border-subtle);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  min-height: 28px;
}

.tool-indicator {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  color: var(--oaa-primary);
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
}

.status-pulse {
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

/* --- Streaming bubble --- */
.msg-bubble.streaming {
  border-color: var(--oaa-blue-500);
  border-style: solid;
  box-shadow: 0 0 16px rgba(59, 130, 246, 0.12);
}

/* --- Confirm dialog --- */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.15s ease;
}

.confirm-dialog {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-xl);
  padding: var(--oaa-space-8);
  text-align: center;
  min-width: 320px;
  max-width: 420px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}

.confirm-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: rgba(245, 158, 11, 0.12);
  color: var(--oaa-amber-400);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto var(--oaa-space-3);
}

.confirm-title {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-2);
}

.confirm-op {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-amber-400);
  font-weight: 500;
  margin-bottom: var(--oaa-space-1);
}

.confirm-details {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  margin-bottom: var(--oaa-space-5);
  line-height: 1.5;
  word-break: break-all;
}

.confirm-actions {
  display: flex;
  gap: var(--oaa-space-3);
  justify-content: center;
}

.confirm-actions .oaa-btn {
  min-width: 80px;
}

/* --- QR Code overlay --- */
.qr-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.qr-card {
  background: var(--oaa-bg-surface);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-8);
  text-align: center;
  position: relative;
  min-width: 280px;
  max-width: 360px;
}

.qr-close {
  position: absolute;
  top: var(--oaa-space-3);
  right: var(--oaa-space-3);
  background: none;
  border: none;
  color: var(--oaa-color-muted);
  font-size: 20px;
  cursor: pointer;
  line-height: 1;
}

.qr-title {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}

.qr-channel {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  margin-bottom: var(--oaa-space-4);
}

.qr-image {
  width: 200px;
  height: 200px;
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
  margin-bottom: var(--oaa-space-3);
}

.qr-hint {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
}
</style>

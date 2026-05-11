<template>
  <div class="view-container">
    <div class="view-header">
      <h2>连接</h2>
      <p class="view-subtitle">管理通道连接与扫码登录</p>
    </div>

    <div v-if="loading" class="loading-state">
      <span class="load-spinner"></span>
      <span>加载通道状态...</span>
    </div>

    <div v-else class="channel-grid">
      <!-- Desktop -->
      <div class="channel-card" :class="{ online: true }">
        <div class="card-glow"></div>
        <div class="card-body">
          <div class="card-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">桌面端</h3>
            <p class="card-desc">GUI 本地连接</p>
          </div>
          <div class="card-status">
            <span class="status-dot online-dot"></span>
            <span class="status-label">在线</span>
          </div>
        </div>
        <div class="card-footer">
          <span class="card-meta">WebSocket :9765</span>
        </div>
      </div>

      <!-- WeChat -->
      <div class="channel-card" :class="{ online: channels.wechat.online }">
        <div class="card-glow" :class="{ active: channels.wechat.online }"></div>
        <div class="card-body">
          <div class="card-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">微信</h3>
            <p class="card-desc">iLink 通道</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.wechat.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.wechat.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.wechat.qrCodeUrl" class="qr-section">
            <div class="qr-mini">
              <img :src="channels.wechat.qrCodeUrl" alt="微信扫码登录" />
            </div>
            <div class="qr-actions">
              <span v-if="channels.wechat.polling" class="poll-hint">
                <span class="poll-spinner"></span> 等待扫码...
              </span>
              <span v-else-if="channels.wechat.scanned" class="scan-ok">已扫码登录</span>
              <span v-else-if="channels.wechat.error" class="scan-err">{{ channels.wechat.error }}</span>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('wechat')">取消</button>
            </div>
          </div>
          <button v-else class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="startQrLogin('wechat')" :disabled="channels.wechat.loading">
            {{ channels.wechat.loading ? '生成中...' : '扫码登录' }}
          </button>
          <span v-if="channels.wechat.error && !channels.wechat.qrCodeUrl" class="card-error">{{ channels.wechat.error }}</span>
        </div>
      </div>

      <!-- DingTalk -->
      <div class="channel-card" :class="{ online: channels.dingtalk.online }">
        <div class="card-glow" :class="{ active: channels.dingtalk.online }"></div>
        <div class="card-body">
          <div class="card-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">钉钉</h3>
            <p class="card-desc">DingTalk Stream</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.dingtalk.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.dingtalk.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.dingtalk.qrCodeUrl" class="qr-section">
            <div class="qr-mini">
              <img :src="channels.dingtalk.qrCodeUrl" alt="钉钉扫码登录" />
            </div>
            <div class="qr-actions">
              <span v-if="channels.dingtalk.polling" class="poll-hint">
                <span class="poll-spinner"></span> 等待扫码...
              </span>
              <span v-else-if="channels.dingtalk.scanned" class="scan-ok">已扫码登录</span>
              <span v-else-if="channels.dingtalk.error" class="scan-err">{{ channels.dingtalk.error }}</span>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('dingtalk')">取消</button>
            </div>
          </div>
          <button v-else class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="startQrLogin('dingtalk')" :disabled="channels.dingtalk.loading">
            {{ channels.dingtalk.loading ? '生成中...' : '扫码登录' }}
          </button>
          <span v-if="channels.dingtalk.error && !channels.dingtalk.qrCodeUrl" class="card-error">{{ channels.dingtalk.error }}</span>
        </div>
      </div>

      <!-- Feishu -->
      <div class="channel-card" :class="{ online: channels.feishu.online }">
        <div class="card-glow" :class="{ active: channels.feishu.online }"></div>
        <div class="card-body">
          <div class="card-icon">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17.5 19H9a7 7 0 1 1 0-14h8.5a5.5 5.5 0 1 1 0 11H9a3.5 3.5 0 1 1 0-7h8"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">飞书</h3>
            <p class="card-desc">Lark Feishu</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.feishu.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.feishu.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.feishu.qrCodeUrl" class="qr-section">
            <div class="qr-mini">
              <img :src="channels.feishu.qrCodeUrl" alt="飞书扫码登录" />
            </div>
            <div class="qr-actions">
              <span v-if="channels.feishu.polling" class="poll-hint">
                <span class="poll-spinner"></span> 等待扫码...
              </span>
              <span v-else-if="channels.feishu.scanned" class="scan-ok">已扫码登录</span>
              <span v-else-if="channels.feishu.error" class="scan-err">{{ channels.feishu.error }}</span>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('feishu')">取消</button>
            </div>
          </div>
          <button v-else class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="startQrLogin('feishu')" :disabled="channels.feishu.loading">
            {{ channels.feishu.loading ? '生成中...' : '扫码登录' }}
          </button>
          <span v-if="channels.feishu.error && !channels.feishu.qrCodeUrl" class="card-error">{{ channels.feishu.error }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { connected, sendRequest } = useWebSocket()

interface ChannelState {
  online: boolean
  loading: boolean
  qrCodeUrl: string
  qrCodeId: string
  polling: boolean
  scanned: boolean
  error: string
}

const loading = ref(true)

const channels = reactive<Record<string, ChannelState>>({
  wechat:   { online: false, loading: false, qrCodeUrl: '', qrCodeId: '', polling: false, scanned: false, error: '' },
  dingtalk: { online: false, loading: false, qrCodeUrl: '', qrCodeId: '', polling: false, scanned: false, error: '' },
  feishu:   { online: false, loading: false, qrCodeUrl: '', qrCodeId: '', polling: false, scanned: false, error: '' },
})

let pollTimers: Record<string, ReturnType<typeof setInterval>> = {}

onMounted(async () => {
  // Load config to determine online status for each channel
  try {
    const resp = await sendRequest('get_config')
    if (resp.ok && resp.config) {
      const config = resp.config as Record<string, unknown>
      const wc = (config.wechat as Record<string, unknown>) || {}
      const dt = (config.dingtalk as Record<string, unknown>) || {}
      const fs = (config.feishu as Record<string, unknown>) || {}
      // Online if enabled and has auth credentials
      channels.wechat.online = !!(wc.enabled && wc.iLink_token)
      channels.dingtalk.online = !!(dt.enabled && dt.client_id)
      channels.feishu.online = !!(fs.enabled && fs.app_id)
    }
  } catch { /* use defaults */ }
  loading.value = false
})

// ------------------------------------------------------------------
// QR login flow
// ------------------------------------------------------------------

async function startQrLogin(channel: string) {
  const ch = channels[channel]
  if (!ch) return

  ch.loading = true
  ch.error = ''
  ch.scanned = false

  try {
    const resp = await sendRequest('qr_login', { channel })
    ch.loading = false

    if (resp.ok && resp.qr_code_url) {
      ch.qrCodeUrl = resp.qr_code_url as string
      ch.qrCodeId = resp.qr_code_id as string
      // Start polling
      startPolling(channel)
    } else {
      ch.error = (resp.error as string) || '获取二维码失败'
    }
  } catch (e) {
    ch.loading = false
    ch.error = `请求失败: ${(e as Error).message}`
  }
}

function startPolling(channel: string) {
  const ch = channels[channel]
  if (!ch) return

  ch.polling = true
  // Poll every 3 seconds
  pollTimers[channel] = setInterval(async () => {
    try {
      const resp = await sendRequest('poll_qr', { channel, qrcode_id: ch.qrCodeId })
      if (resp.status === 'scanned') {
        stopPolling(channel)
        ch.polling = false
        ch.scanned = true
        ch.online = true
      } else if (resp.status === 'error') {
        stopPolling(channel)
        ch.polling = false
        ch.error = (resp.msg as string) || '扫码失败'
      }
    } catch {
      // Network error during poll — keep trying
    }
  }, 3000)
}

function stopPolling(channel: string) {
  if (pollTimers[channel]) {
    clearInterval(pollTimers[channel])
    delete pollTimers[channel]
  }
}

function cancelQR(channel: string) {
  stopPolling(channel)
  const ch = channels[channel]
  if (ch) {
    ch.qrCodeUrl = ''
    ch.qrCodeId = ''
    ch.polling = false
    ch.scanned = false
    ch.error = ''
  }
}

onUnmounted(() => {
  // Clear all polling timers when leaving this view
  for (const ch of Object.keys(pollTimers)) {
    stopPolling(ch)
  }
})
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
}

.view-header { margin-bottom: var(--oaa-space-8); }

.view-header h2 {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}

.view-subtitle { color: var(--oaa-color-muted); font-size: var(--oaa-text-base); }

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  height: 200px;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}

.load-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: connSpin 0.6s linear infinite;
}

@keyframes connSpin { to { transform: rotate(360deg); } }

/* --- Card grid --- */
.channel-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--oaa-space-5);
}

.channel-card {
  background: rgba(30, 41, 59, 0.5);
  border: 1px solid var(--oaa-glass-border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-radius: var(--oaa-radius-xl);
  padding: var(--oaa-space-6);
  position: relative;
  overflow: hidden;
  transition:
    border-color var(--oaa-transition-base),
    box-shadow var(--oaa-transition-base),
    transform var(--oaa-transition-base);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.channel-card:hover {
  border-color: rgba(255, 255, 255, 0.12);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  transform: translateY(-3px);
}

.channel-card.online {
  border-color: rgba(34, 197, 94, 0.2);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2), 0 0 24px rgba(34, 197, 94, 0.06);
}

/* Glow behind online cards */
.card-glow {
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at 50% 0%, rgba(59, 130, 246, 0.08) 0%, transparent 70%);
  opacity: 0;
  transition: opacity var(--oaa-transition-slow);
  pointer-events: none;
}
.card-glow.active {
  opacity: 1;
  background: radial-gradient(ellipse at 50% 0%, rgba(34, 197, 94, 0.1) 0%, transparent 60%);
}

.card-body {
  display: flex;
  align-items: flex-start;
  gap: var(--oaa-space-4);
  margin-bottom: var(--oaa-space-4);
}

.card-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--oaa-radius-lg);
  background: rgba(59, 130, 246, 0.1);
  color: var(--oaa-blue-400);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition:
    background var(--oaa-transition-base),
    color var(--oaa-transition-base),
    transform var(--oaa-transition-base);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.channel-card.online .card-icon {
  background: rgba(34, 197, 94, 0.12);
  color: var(--oaa-green-400);
}

.channel-card:hover .card-icon {
  transform: scale(1.05);
}

.card-info { flex: 1; min-width: 0; }

.card-name {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
}

.card-desc {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  margin-top: 2px;
}

.card-status {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  flex-shrink: 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.online-dot {
  background: var(--oaa-green-500);
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.4);
}

.offline-dot {
  background: var(--oaa-color-disabled);
}

.status-label {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
}

.card-footer {
  padding-top: var(--oaa-space-3);
  border-top: 1px solid var(--oaa-glass-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
}

.card-meta {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
}

.card-error {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-error);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* --- QR section --- */
.qr-section {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  width: 100%;
}

.qr-mini {
  width: 64px;
  height: 64px;
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-md);
  overflow: hidden;
  flex-shrink: 0;
  background: #fff;
}

.qr-mini img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.qr-actions {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
  flex: 1;
}

.poll-hint {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-1);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-primary);
}

.poll-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: connSpin 0.6s linear infinite;
  display: inline-block;
}

.scan-ok {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-green-500);
  font-weight: 600;
}

.scan-err {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-error);
}

/* --- Responsive --- */
@media (max-width: 720px) {
  .channel-grid {
    grid-template-columns: 1fr;
  }
}
</style>

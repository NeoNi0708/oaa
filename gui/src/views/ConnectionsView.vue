<template>
  <div class="view-container">
    <div class="view-header">
      <h2>通道连接</h2>
      <p class="view-subtitle">管理通道连接与扫码登录 · 桌面端 / 微信 / 钉钉 / 飞书</p>
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
          <div class="card-icon desktop-icon">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">桌面端</h3>
            <p class="card-desc">GUI 本地 WebSocket · 直连 Agent</p>
          </div>
          <div class="card-status">
            <span class="status-dot online-dot"></span>
            <span class="status-label">在线</span>
          </div>
        </div>
        <div class="card-footer">
          <span class="card-meta">ws://127.0.0.1:9765</span>
        </div>
      </div>

      <!-- WeChat -->
      <div class="channel-card" :class="{ online: channels.wechat.online }">
        <div class="card-glow" :class="{ active: channels.wechat.online }"></div>
        <div class="card-body">
          <div class="card-icon wechat-icon">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">微信</h3>
            <p class="card-desc">iLink Bot · openilink SDK 直连</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.wechat.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.wechat.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.wechat.qrCodeUrl && !channels.wechat.online" class="qr-section">
            <div class="qr-mini"><img :src="channels.wechat.qrCodeUrl" alt="微信扫码登录" /></div>
            <div class="qr-actions">
              <span v-if="channels.wechat.polling" class="poll-hint"><span class="poll-spinner"></span> 等待扫码...</span>
              <span v-else-if="channels.wechat.scanned" class="scan-ok">已扫码，请在手机上确认</span>
              <span v-else-if="channels.wechat.error" class="scan-err">{{ channels.wechat.error }}</span>
              <div v-if="channels.wechat.userCode && !channels.wechat.scanned && !channels.wechat.error" class="user-code-row">
                <span class="user-code-label">授权码：</span>
                <code class="user-code-value">{{ channels.wechat.userCode }}</code>
              </div>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('wechat')">取消</button>
            </div>
          </div>
          <div v-else-if="channels.wechat.online" class="connected-footer">
            <div class="connection-details">
              <span class="scan-ok">已连接</span>
              <span v-if="channels.wechat.botId" class="bot-id" :title="channels.wechat.botId">{{ channels.wechat.botId }}</span>
            </div>
            <div class="connection-actions">
              <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('wechat')" :disabled="channels.wechat.reconnecting">重连</button>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('wechat')" :disabled="channels.wechat.loading">新二维码</button>
            </div>
          </div>
          <div v-else class="offline-actions">
            <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('wechat')" :disabled="channels.wechat.reconnecting || channels.wechat.loading">
              {{ channels.wechat.reconnecting ? '重连中...' : '重连' }}
            </button>
            <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('wechat')" :disabled="channels.wechat.reconnecting || channels.wechat.loading">
              {{ channels.wechat.loading ? '生成中...' : '新二维码' }}
            </button>
          </div>
          <span v-if="channels.wechat.error && !channels.wechat.qrCodeUrl && !channels.wechat.online" class="card-error">{{ channels.wechat.error }}</span>
        </div>
        <!-- iLink capability summary -->
        <div v-if="channels.wechat.online" class="cli-config-section">
          <div class="cli-config-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg>
            <span>iLink 已连接 · 收发消息、发送文件</span>
          </div>
        </div>
        <!-- wechat-cli config (local WeChat data query) -->
        <div class="cli-config-section">
          <div class="cli-config-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            <span>wechat-cli · 联系人/历史/搜索（本地数据查询）</span>
          </div>
          <p class="cli-hint">需先在终端运行 <code>wechat-cli init</code> 提取密钥（微信需在运行中）</p>
          <div class="credential-row">
            <input v-model="channels.wechat.wechatCliPath" placeholder="wechat-cli 路径（留空自动检测）" class="oaa-input oaa-input--sm" />
            <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="saveWechatCliConfig">保存</button>
          </div>
          <span v-if="channels.wechat.cliSaved" class="scan-ok">已保存</span>
        </div>
      </div>

      <!-- DingTalk -->
      <div class="channel-card" :class="{ online: channels.dingtalk.online }">
        <div class="card-glow" :class="{ active: channels.dingtalk.online }"></div>
        <div class="card-body">
          <div class="card-icon dingtalk-icon">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-5"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">钉钉</h3>
            <p class="card-desc">DingTalk Stream · 消息接收与发送</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.dingtalk.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.dingtalk.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.dingtalk.qrCodeUrl && !channels.dingtalk.online" class="qr-section">
            <div class="qr-mini"><img :src="channels.dingtalk.qrCodeUrl" alt="钉钉扫码登录" /></div>
            <div class="qr-actions">
              <span v-if="channels.dingtalk.polling" class="poll-hint"><span class="poll-spinner"></span> 等待扫码...</span>
              <span v-else-if="channels.dingtalk.scanned" class="scan-ok">已扫码，请在手机上确认</span>
              <span v-else-if="channels.dingtalk.error" class="scan-err">{{ channels.dingtalk.error }}</span>
              <div v-if="channels.dingtalk.userCode && !channels.dingtalk.scanned && !channels.dingtalk.error" class="user-code-row">
                <span class="user-code-label">授权码：</span>
                <code class="user-code-value">{{ channels.dingtalk.userCode }}</code>
              </div>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('dingtalk')">取消</button>
            </div>
          </div>
          <div v-else-if="channels.dingtalk.online" class="connected-footer">
            <div class="connection-details">
              <span class="scan-ok">已连接</span>
              <span v-if="channels.dingtalk.clientId" class="bot-id" :title="channels.dingtalk.clientId">{{ channels.dingtalk.clientId }}</span>
            </div>
            <div class="connection-actions">
              <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('dingtalk')" :disabled="channels.dingtalk.reconnecting || channels.dingtalk.loading">重连</button>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('dingtalk')" :disabled="channels.dingtalk.loading">新二维码</button>
            </div>
          </div>
          <div v-else class="offline-actions">
            <div class="credential-row">
              <input v-model="channels.dingtalk.clientId" placeholder="AppKey" class="oaa-input oaa-input--sm" />
              <input v-model="channels.dingtalk.clientSecret" type="password" placeholder="AppSecret" class="oaa-input oaa-input--sm" />
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="saveChannelCredentials('dingtalk')">保存</button>
            </div>
            <div class="button-row" style="margin-top: 8px;">
              <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('dingtalk')" :disabled="channels.dingtalk.reconnecting || channels.dingtalk.loading">
                {{ channels.dingtalk.reconnecting ? '重连中...' : '重连' }}
              </button>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('dingtalk')" :disabled="channels.dingtalk.reconnecting || channels.dingtalk.loading">
                {{ channels.dingtalk.loading ? '生成中...' : '新二维码' }}
              </button>
            </div>
          </div>
          <span v-if="channels.dingtalk.error && !channels.dingtalk.qrCodeUrl && !channels.dingtalk.online" class="card-error">{{ channels.dingtalk.error }}</span>
        </div>
      </div>

      <!-- Feishu -->
      <div class="channel-card" :class="{ online: channels.feishu.online }">
        <div class="card-glow" :class="{ active: channels.feishu.online }"></div>
        <div class="card-body">
          <div class="card-icon lark-icon">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17.5 19H9a7 7 0 1 1 0-14h8.5a5.5 5.5 0 1 1 0 11H9a3.5 3.5 0 1 1 0-7h8"/>
            </svg>
          </div>
          <div class="card-info">
            <h3 class="card-name">飞书</h3>
            <p class="card-desc">Lark API · WebSocket 事件订阅</p>
          </div>
          <div class="card-status">
            <span :class="['status-dot', channels.feishu.online ? 'online-dot' : 'offline-dot']"></span>
            <span class="status-label">{{ channels.feishu.online ? '在线' : '离线' }}</span>
          </div>
        </div>
        <div class="card-footer">
          <div v-if="channels.feishu.qrCodeUrl && !channels.feishu.online" class="qr-section">
            <div class="qr-mini"><img :src="channels.feishu.qrCodeUrl" alt="飞书扫码登录" /></div>
            <div class="qr-actions">
              <span v-if="channels.feishu.polling" class="poll-hint"><span class="poll-spinner"></span> 等待扫码...</span>
              <span v-else-if="channels.feishu.scanned" class="scan-ok">已扫码，请在手机上确认</span>
              <span v-else-if="channels.feishu.error" class="scan-err">{{ channels.feishu.error }}</span>
              <div v-if="channels.feishu.userCode && !channels.feishu.scanned && !channels.feishu.error" class="user-code-row">
                <span class="user-code-label">授权码：</span>
                <code class="user-code-value">{{ channels.feishu.userCode }}</code>
              </div>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="cancelQR('feishu')">取消</button>
            </div>
          </div>
          <div v-else-if="channels.feishu.online" class="connected-footer">
            <div class="connection-details">
              <span class="scan-ok">已连接</span>
              <span v-if="channels.feishu.appId" class="bot-id" :title="channels.feishu.appId">{{ channels.feishu.appId }}</span>
            </div>
            <div class="connection-actions">
              <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('feishu')" :disabled="channels.feishu.reconnecting || channels.feishu.loading">重连</button>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('feishu')" :disabled="channels.feishu.loading">新二维码</button>
            </div>
          </div>
          <div v-else class="offline-actions">
            <div class="credential-row">
              <input v-model="channels.feishu.appId" placeholder="App ID" class="oaa-input oaa-input--sm" />
              <input v-model="channels.feishu.appSecret" type="password" placeholder="App Secret" class="oaa-input oaa-input--sm" />
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="saveChannelCredentials('feishu')">保存</button>
            </div>
            <div class="button-row" style="margin-top: 8px;">
              <button class="oaa-btn oaa-btn--sm oaa-btn--primary" @click="reconnectChannel('feishu')" :disabled="channels.feishu.reconnecting || channels.feishu.loading">
                {{ channels.feishu.reconnecting ? '重连中...' : '重连' }}
              </button>
              <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="startQrLogin('feishu')" :disabled="channels.feishu.reconnecting || channels.feishu.loading">
                {{ channels.feishu.loading ? '生成中...' : '新二维码' }}
              </button>
            </div>
          </div>
          <span v-if="channels.feishu.error && !channels.feishu.qrCodeUrl && !channels.feishu.online" class="card-error">{{ channels.feishu.error }}</span>
        </div>
      </div>
    </div>

    <!-- ================================ -->
    <!-- Email Accounts Section -->
    <!-- ================================ -->
    <div class="email-section">
      <div class="section-header">
        <h3>邮箱配置</h3>
        <p class="view-subtitle" style="margin:0">用于发送邮件的 SMTP/IMAP 账户</p>
      </div>

      <div v-if="emailLoading" class="loading-state" style="height:100px">
        <span class="load-spinner"></span>
        <span>加载邮箱配置...</span>
      </div>

      <div v-else class="email-grid">
        <!-- Existing accounts -->
        <div v-for="acc in emailAccounts" :key="acc.id" class="email-card">
          <div class="email-card-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 4L12 13 2 4"/>
            </svg>
          </div>
          <div class="email-card-body">
            <span class="email-card-name">{{ acc.display_name || providerLabel(acc.provider) }}</span>
            <span class="email-card-user">{{ acc.username }}</span>
          </div>
          <button class="oaa-btn oaa-btn--ghost oaa-btn--sm" @click="deleteEmail(acc.id)" :disabled="emailDeleting === acc.id">
            {{ emailDeleting === acc.id ? '…' : '删除' }}
          </button>
        </div>

        <!-- Add card -->
        <div class="email-card email-card--add" @click="openEmailModal">
          <span class="add-icon">+</span>
          <span class="add-label">新增邮箱</span>
        </div>
      </div>
    </div>

    <!-- Email config modal -->
    <div v-if="showEmailModal" class="modal-overlay" @click.self="closeEmailModal">
      <div class="modal-panel email-modal">
        <div class="modal-header">
          <h3>配置邮箱</h3>
          <button class="modal-close" @click="closeEmailModal">✕</button>
        </div>

        <div class="modal-body">
          <div class="form-group">
            <label>服务商</label>
            <select v-model="emailForm.provider" class="oaa-input" @change="onProviderChange">
              <option value="">请选择服务商</option>
              <option v-for="p in providers" :key="p.key" :value="p.key">{{ p.name }}</option>
            </select>
          </div>

          <div class="form-group">
            <label>显示名称</label>
            <input v-model="emailForm.display_name" class="oaa-input" placeholder="例如：我的谷歌邮箱" />
          </div>

          <div class="form-group">
            <label>邮箱地址</label>
            <input v-model="emailForm.username" class="oaa-input" placeholder="your@email.com" />
          </div>

          <div class="form-group">
            <label>授权码 / 密码</label>
            <input v-model="emailForm.auth_code" class="oaa-input" type="password" placeholder="授权码或应用专用密码" />
            <p class="form-hint">需先在邮箱网页端开启 IMAP/SMTP 服务并生成授权码</p>
          </div>

          <details class="advanced-details">
            <summary class="advanced-summary">高级设置</summary>
            <div class="advanced-body">
              <div class="form-row">
                <div class="form-group">
                  <label>IMAP 服务器</label>
                  <input v-model="emailForm.imap_server" class="oaa-input" placeholder="imap.example.com" />
                </div>
                <div class="form-group form-group--sm">
                  <label>端口</label>
                  <input v-model.number="emailForm.imap_port" class="oaa-input" type="number" />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>SMTP 服务器</label>
                  <input v-model="emailForm.smtp_server" class="oaa-input" placeholder="smtp.example.com" />
                </div>
                <div class="form-group form-group--sm">
                  <label>端口</label>
                  <input v-model.number="emailForm.smtp_port" class="oaa-input" type="number" />
                </div>
              </div>
            </div>
          </details>

          <div v-if="emailError" class="form-error-msg">{{ emailError }}</div>
          <div v-if="emailTestErrors.length > 0" class="form-error-list">
            <p v-for="(e, i) in emailTestErrors" :key="i">{{ e }}</p>
          </div>
        </div>

        <div class="modal-footer">
          <button class="oaa-btn oaa-btn--ghost" @click="closeEmailModal">取消</button>
          <button class="oaa-btn oaa-btn--primary" @click="saveEmail" :disabled="emailSaving">
            {{ emailSaving ? '测试连接中…' : '保存' }}
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
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { connected, sendRequest, channelStatusChanged } = useWebSocket()

interface ChannelState {
  online: boolean
  loading: boolean
  reconnecting: boolean
  qrCodeUrl: string
  qrCodeId: string
  userCode: string
  polling: boolean
  scanned: boolean
  error: string
  botId: string
  clientId: string
  clientSecret: string
  appId: string
  appSecret: string
  wechatCliPath: string
  cliSaved: boolean
}

const loading = ref(true)

const channels = reactive<Record<string, ChannelState>>({
  wechat:   { online: false, loading: false, reconnecting: false, qrCodeUrl: '', qrCodeId: '', userCode: '', polling: false, scanned: false, error: '', botId: '', clientId: '', clientSecret: '', appId: '', appSecret: '', wechatCliPath: '', cliSaved: false },
  dingtalk: { online: false, loading: false, reconnecting: false, qrCodeUrl: '', qrCodeId: '', userCode: '', polling: false, scanned: false, error: '', botId: '', clientId: '', clientSecret: '', appId: '', appSecret: '', wechatCliPath: '', cliSaved: false },
  feishu:   { online: false, loading: false, reconnecting: false, qrCodeUrl: '', qrCodeId: '', userCode: '', polling: false, scanned: false, error: '', botId: '', clientId: '', clientSecret: '', appId: '', appSecret: '', wechatCliPath: '', cliSaved: false },
})

let pollTimers: Record<string, ReturnType<typeof setInterval>> = {}

onMounted(async () => {
  try {
    const resp = await sendRequest('get_config')
    if (resp.ok && resp.config) {
      const config = resp.config as Record<string, unknown>
      const wc = (config.wechat as Record<string, unknown>) || {}
      const dt = (config.dingtalk as Record<string, unknown>) || {}
      const fs = (config.feishu as Record<string, unknown>) || {}
      channels.wechat.online = !!(wc.iLink_token)
      channels.wechat.botId = (wc.iLink_bot_id as string) || ''
      channels.dingtalk.online = !!(dt.client_id && dt.client_secret)
      channels.feishu.online = !!(fs.app_id && fs.app_secret)
      channels.dingtalk.clientId = (dt.client_id as string) || ''
      channels.dingtalk.clientSecret = (dt.client_secret as string) || ''
      channels.feishu.appId = (fs.app_id as string) || ''
      channels.feishu.appSecret = (fs.app_secret as string) || ''
      channels.wechat.wechatCliPath = (wc.wechat_cli_path as string) || ''
    }
  } catch { /* use defaults */ }
  loading.value = false
})

// Refresh channel online status when backend pushes a disconnect event
watch(channelStatusChanged, async () => {
  try {
    const resp = await sendRequest('get_status')
    if (resp.ok && resp.channels) {
      for (const [name, st] of Object.entries(resp.channels as Record<string, any>)) {
        if (channels[name]) {
          channels[name].online = !!st.online
        }
      }
    }
  } catch { /* ignore */ }
})

async function saveWechatCliConfig() {
  const ch = channels.wechat
  ch.cliSaved = false
  try {
    const resp = await sendRequest('save_config', {
      config: { wechat: { wechat_cli_path: ch.wechatCliPath } }
    })
    if (resp.ok) { ch.cliSaved = true; ch.error = '' }
    else { ch.error = (resp.error as string) || '保存失败' }
  } catch (e) { ch.error = `保存失败: ${(e as Error).message}` }
}

// ------------------------------------------------------------------
// QR login flow
// ------------------------------------------------------------------

async function saveChannelCredentials(channel: string) {
  const ch = channels[channel]
  if (!ch) return
  const payload: Record<string, unknown> = { channel }
  if (channel === 'dingtalk') {
    payload.config = {
      dingtalk: {
        client_id: ch.clientId,
        client_secret: ch.clientSecret,
      },
    }
  } else if (channel === 'feishu') {
    payload.config = {
      feishu: {
        app_id: ch.appId,
        app_secret: ch.appSecret,
      },
    }
  }
  try {
    const resp = await sendRequest('save_config', payload)
    if (resp.ok) { ch.error = '' } else { ch.error = (resp.error as string) || '保存失败' }
  } catch (e) { ch.error = `保存失败: ${(e as Error).message}` }
}

async function startQrLogin(channel: string) {
  const ch = channels[channel]
  if (!ch) return
  ch.loading = true; ch.error = ''; ch.scanned = false
  const payload: Record<string, unknown> = { channel }
  if (channel === 'dingtalk') {
    if (ch.clientId) payload.client_id = ch.clientId
    if (ch.clientSecret) payload.client_secret = ch.clientSecret
  } else if (channel === 'feishu') {
    payload.app_id = ch.appId || ''
    payload.app_secret = ch.appSecret || ''
  }
  try {
    const resp = await sendRequest('qr_login', payload)
    ch.loading = false
    if (resp.ok && resp.qr_code_url) {
      ch.qrCodeUrl = resp.qr_code_url as string
      ch.qrCodeId = resp.qr_code_id as string
      ch.userCode = (resp.user_code as string) || ''
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
  pollTimers[channel] = setInterval(async () => {
    try {
      const resp = await sendRequest('poll_qr', { channel, qrcode_id: ch.qrCodeId })
      if (resp.status === 'scanned') { ch.scanned = true; ch.error = '' }
      else if (resp.status === 'confirmed') {
        stopPolling(channel); ch.polling = false; ch.scanned = true; ch.online = true
        ch.qrCodeUrl = ''; ch.qrCodeId = ''; ch.error = ''
      } else if (resp.status === 'expired') {
        stopPolling(channel); ch.polling = false; ch.qrCodeUrl = ''; ch.qrCodeId = ''
        ch.error = '二维码已过期，请重新获取'
      } else if (resp.status === 'error') {
        stopPolling(channel); ch.polling = false
        ch.error = (resp.msg as string) || '扫码失败'
      }
    } catch { /* ignore */ }
  }, 2000)
}

function stopPolling(channel: string) {
  if (pollTimers[channel]) { clearInterval(pollTimers[channel]); delete pollTimers[channel] }
}

function cancelQR(channel: string) {
  stopPolling(channel)
  const ch = channels[channel]
  if (ch) { ch.qrCodeUrl = ''; ch.qrCodeId = ''; ch.userCode = ''; ch.polling = false; ch.scanned = false; ch.error = '' }
}

async function reconnectChannel(channel: string) {
  const ch = channels[channel]
  if (!ch) return
  ch.reconnecting = true; ch.error = ''
  try {
    const resp = await sendRequest('reconnect_channel', { channel })
    if (resp.ok) { ch.online = true } else { ch.online = false; ch.error = (resp.error as string) || '重连失败' }
  } catch (e) { ch.online = false; ch.error = `重连失败: ${(e as Error).message}` }
  ch.reconnecting = false
}

onUnmounted(() => {
  for (const ch of Object.keys(pollTimers)) stopPolling(ch)
})

// ------------------------------------------------------------------
// Email configuration
// ------------------------------------------------------------------

interface EmailAccount {
  id: string
  provider: string
  display_name: string
  username: string
  auth_code: string
  imap_server: string
  imap_port: number
  smtp_server: string
  smtp_port: number
  smtp_tls: boolean
}

interface EmailProvider {
  key: string
  name: string
  imap_server: string
  imap_port: number
  smtp_server: string
  smtp_port: number
  smtp_tls: boolean
  custom: boolean
}

const emailAccounts = ref<EmailAccount[]>([])
const providers = ref<EmailProvider[]>([])
const emailLoading = ref(true)
const showEmailModal = ref(false)
const emailSaving = ref(false)
const emailDeleting = ref('')
const emailError = ref('')
const emailTestErrors = ref<string[]>([])
const toast = ref({ show: false, type: 'success', message: '' })

const emailForm = reactive({
  provider: '',
  display_name: '',
  username: '',
  auth_code: '',
  imap_server: '',
  imap_port: 993,
  smtp_server: '',
  smtp_port: 587,
  smtp_tls: true,
})

function resetEmailForm() {
  emailForm.provider = ''
  emailForm.display_name = ''
  emailForm.username = ''
  emailForm.auth_code = ''
  emailForm.imap_server = ''
  emailForm.imap_port = 993
  emailForm.smtp_server = ''
  emailForm.smtp_port = 587
  emailForm.smtp_tls = true
  emailError.value = ''
  emailTestErrors.value = []
}

function providerLabel(key: string): string {
  const p = providers.value.find(p => p.key === key)
  return p ? p.name : key
}

function onProviderChange() {
  const p = providers.value.find(p => p.key === emailForm.provider)
  if (p && !p.custom) {
    emailForm.imap_server = p.imap_server
    emailForm.imap_port = p.imap_port
    emailForm.smtp_server = p.smtp_server
    emailForm.smtp_port = p.smtp_port
    emailForm.smtp_tls = p.smtp_tls ?? true
  } else {
    emailForm.imap_server = ''
    emailForm.imap_port = 993
    emailForm.smtp_server = ''
    emailForm.smtp_port = 587
    emailForm.smtp_tls = true
  }
}

function openEmailModal() {
  resetEmailForm()
  showEmailModal.value = true
}

function closeEmailModal() {
  showEmailModal.value = false
}

async function saveEmail() {
  if (!emailForm.provider) { emailError.value = '请选择服务商'; return }
  if (!emailForm.username) { emailError.value = '请输入邮箱地址'; return }
  if (!emailForm.auth_code) { emailError.value = '请输入授权码'; return }

  emailSaving.value = true
  emailError.value = ''
  emailTestErrors.value = []

  try {
    const resp = await sendRequest('save_email', { account: { ...emailForm } })
    if (resp.ok) {
      showEmailModal.value = false
      await loadEmails()
      showToast('邮箱配置保存成功', 'success')
    } else if (resp.test_ok === false) {
      if (resp.errors?.length) {
        emailTestErrors.value = resp.errors
      } else {
        const errs: string[] = []
        if (resp.imap_error) errs.push(`IMAP: ${resp.imap_error}`)
        if (resp.smtp_error) errs.push(`SMTP: ${resp.smtp_error}`)
        emailTestErrors.value = errs
      }
    } else {
      emailError.value = resp.error || '保存失败'
    }
  } catch (e: any) {
    emailError.value = '保存失败: ' + (e.message || e)
  }
  emailSaving.value = false
}

async function deleteEmail(id: string) {
  if (!confirm('确定要删除此邮箱配置吗？')) return
  emailDeleting.value = id
  try {
    const resp = await sendRequest('delete_email', { id })
    if (resp.ok) {
      await loadEmails()
      showToast('已删除', 'success')
    } else {
      showToast(resp.error || '删除失败', 'error')
    }
  } catch (e: any) {
    showToast('删除失败: ' + (e.message || e), 'error')
  }
  emailDeleting.value = ''
}

async function loadEmails() {
  try {
    const resp = await sendRequest('list_emails')
    if (resp.ok) {
      emailAccounts.value = resp.accounts || []
      providers.value = resp.providers || []
    }
  } catch { /* ignore */ }
  emailLoading.value = false
}

function showToast(message: string, type: 'success' | 'error' = 'success') {
  toast.value = { show: true, type, message }
  setTimeout(() => { toast.value.show = false }, 3000)
}

// Load email accounts on mount (after channel config loads)
setTimeout(() => loadEmails(), 100)
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
}
.view-header { margin-bottom: var(--oaa-space-8); }
.view-header h2 { font-size: var(--oaa-text-2xl); font-weight: 700; color: var(--oaa-color-primary); margin-bottom: var(--oaa-space-1); }
.view-subtitle { color: var(--oaa-color-muted); font-size: var(--oaa-text-sm); }

.channel-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--oaa-space-5); }

.channel-card {
  background: rgba(30, 41, 59, 0.5); border: 1px solid var(--oaa-glass-border);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  border-radius: var(--oaa-radius-xl); padding: var(--oaa-space-6);
  position: relative; overflow: hidden;
  transition: border-color var(--oaa-transition-base), box-shadow var(--oaa-transition-base), transform var(--oaa-transition-base);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}
.channel-card:hover { border-color: rgba(255, 255, 255, 0.12); box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4); transform: translateY(-3px); }
.channel-card.online { border-color: rgba(34, 197, 94, 0.2); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2), 0 0 24px rgba(34, 197, 94, 0.06); }

.card-glow { position: absolute; inset: 0; background: radial-gradient(ellipse at 50% 0%, rgba(59, 130, 246, 0.08) 0%, transparent 70%); opacity: 0; transition: opacity var(--oaa-transition-slow); pointer-events: none; }
.card-glow.active { opacity: 1; background: radial-gradient(ellipse at 50% 0%, rgba(34, 197, 94, 0.1) 0%, transparent 60%); }

.card-body { display: flex; align-items: flex-start; gap: var(--oaa-space-4); margin-bottom: var(--oaa-space-4); }

.card-icon { width: 52px; height: 52px; border-radius: var(--oaa-radius-lg); display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: background var(--oaa-transition-base), color var(--oaa-transition-base), transform var(--oaa-transition-base); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2); }
.channel-card:hover .card-icon { transform: scale(1.05); }

.desktop-icon { background: rgba(59, 130, 246, 0.12); color: var(--oaa-blue-400); }
.wechat-icon { background: rgba(34, 197, 94, 0.12); color: var(--oaa-green-400); }
.dingtalk-icon { background: rgba(0, 150, 250, 0.12); color: #0096FA; }
.lark-icon { background: rgba(59, 130, 246, 0.12); color: var(--oaa-blue-400); }

.channel-card.online .wechat-icon { background: rgba(34, 197, 94, 0.12); color: var(--oaa-green-400); }

.card-info { flex: 1; min-width: 0; }
.card-name { font-size: var(--oaa-text-lg); font-weight: 600; color: var(--oaa-color-primary); }
.card-desc { font-size: var(--oaa-text-xs); color: var(--oaa-color-muted); margin-top: 2px; line-height: 1.4; }

.card-status { display: flex; align-items: center; gap: var(--oaa-space-2); flex-shrink: 0; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.online-dot { background: var(--oaa-green-500); box-shadow: 0 0 8px rgba(34, 197, 94, 0.4); }
.offline-dot { background: var(--oaa-color-disabled); }
.status-label { font-size: var(--oaa-text-sm); color: var(--oaa-color-muted); }

.card-footer { padding-top: var(--oaa-space-3); border-top: 1px solid var(--oaa-glass-border); display: flex; align-items: center; justify-content: space-between; }
.card-meta { font-size: var(--oaa-text-xs); color: var(--oaa-color-disabled); font-family: var(--oaa-font-mono); }
.card-error { font-size: var(--oaa-text-xs); color: var(--oaa-error); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.qr-section { display: flex; align-items: center; gap: var(--oaa-space-3); width: 100%; flex-wrap: wrap; }
.qr-mini { min-width: 100px; min-height: 100px; max-width: 160px; max-height: 160px; flex: 1 1 auto; aspect-ratio: 1 / 1; border: 1px solid var(--oaa-glass-border); border-radius: var(--oaa-radius-md); overflow: hidden; background: #fff; }
.qr-mini img { width: 100%; height: 100%; object-fit: contain; }
.qr-actions { display: flex; flex-direction: column; gap: var(--oaa-space-1); flex: 1; }

.poll-hint { display: flex; align-items: center; gap: var(--oaa-space-1); font-size: var(--oaa-text-xs); color: var(--oaa-primary); }
.poll-spinner { width: 10px; height: 10px; border: 2px solid var(--oaa-border-subtle); border-top-color: var(--oaa-primary); border-radius: 50%; animation: connSpin 0.6s linear infinite; display: inline-block; }
.scan-ok { font-size: var(--oaa-text-xs); color: var(--oaa-green-500); font-weight: 600; }
.scan-err { font-size: var(--oaa-text-xs); color: var(--oaa-error); }

@keyframes connSpin { to { transform: rotate(360deg); } }

.connected-footer { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--oaa-space-3); flex-wrap: wrap; width: 100%; }
.connection-details { display: flex; flex-direction: column; gap: var(--oaa-space-1); min-width: 0; flex: 1; }
.connection-actions { display: flex; gap: var(--oaa-space-2); flex-shrink: 0; }
.bot-id { font-size: var(--oaa-text-xs); color: var(--oaa-color-disabled); font-family: var(--oaa-font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.offline-actions { display: flex; gap: var(--oaa-space-2); flex-wrap: wrap; }

.loading-state { display: flex; align-items: center; justify-content: center; gap: var(--oaa-space-2); height: 200px; color: var(--oaa-color-muted); font-size: var(--oaa-text-sm); }
.load-spinner { width: 16px; height: 16px; border: 2px solid var(--oaa-border-subtle); border-top-color: var(--oaa-primary); border-radius: 50%; animation: connSpin 0.6s linear infinite; }

.credential-row { display: flex; gap: var(--oaa-space-2); width: 100%; align-items: center; }
.credential-row .oaa-input--sm { flex: 1; min-width: 0; padding: 4px 8px; font-size: var(--oaa-text-xs); }
.oaa-input--sm { background: var(--oaa-bg-input); border: 1px solid var(--oaa-border-default); border-radius: var(--oaa-radius-sm); color: var(--oaa-color-primary); }

.user-code-row { display: flex; align-items: center; gap: var(--oaa-space-1); margin: 4px 0; }
.user-code-label { font-size: var(--oaa-text-xs); color: var(--oaa-color-muted); }
.user-code-value { font-size: var(--oaa-text-sm); font-weight: 700; color: var(--oaa-primary); font-family: var(--oaa-font-mono); letter-spacing: 2px; background: rgba(59, 130, 246, 0.1); padding: 2px 8px; border-radius: var(--oaa-radius-sm); }

/* wechat-cli config section */
.cli-config-section {
  margin-top: var(--oaa-space-3);
  padding-top: var(--oaa-space-3);
  border-top: 1px solid var(--oaa-glass-border);
}
.cli-config-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  margin-bottom: var(--oaa-space-2);
}
.cli-config-section .credential-row {
  display: flex;
  gap: var(--oaa-space-2);
  width: 100%;
  align-items: center;
}
.cli-config-section .oaa-input--sm {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  font-size: var(--oaa-text-xs);
}
.cli-hint {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  margin: 0 0 var(--oaa-space-2) 0;
}
.cli-hint code {
  background: var(--oaa-bg-input);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}

@media (max-width: 720px) { .channel-grid { grid-template-columns: 1fr; } }

/* ============================== */
/* Email accounts section */
/* ============================== */
.email-section {
  margin-top: var(--oaa-space-10);
}
.section-header {
  display: flex;
  align-items: baseline;
  gap: var(--oaa-space-3);
  margin-bottom: var(--oaa-space-4);
}
.section-header h3 {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
}

.email-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--oaa-space-4);
}
@media (max-width: 960px) { .email-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 640px) { .email-grid { grid-template-columns: 1fr; } }

.email-card {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  background: rgba(30, 41, 59, 0.5);
  border: 1px solid var(--oaa-glass-border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-4);
  transition: border-color var(--oaa-transition-base), transform var(--oaa-transition-base);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}
.email-card:hover {
  border-color: rgba(255, 255, 255, 0.12);
  transform: translateY(-2px);
}

.email-card-icon {
  width: 38px;
  height: 38px;
  border-radius: var(--oaa-radius-md);
  background: rgba(59, 130, 246, 0.12);
  color: var(--oaa-blue-400);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.email-card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.email-card-name {
  font-size: var(--oaa-text-sm);
  font-weight: 600;
  color: var(--oaa-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.email-card-user {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.email-card--add {
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  border-style: dashed;
  border-color: var(--oaa-glass-border);
  color: var(--oaa-color-muted);
  min-height: 72px;
  transition: all var(--oaa-transition-base);
}
.email-card--add:hover {
  border-color: var(--oaa-primary);
  color: var(--oaa-primary);
  background: rgba(59, 130, 246, 0.06);
}
.add-icon {
  font-size: 22px;
  font-weight: 300;
  line-height: 1;
}
.add-label {
  font-size: var(--oaa-text-sm);
}

/* ============================== */
/* Modal overlay */
/* ============================== */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
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

.modal-panel {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-xl);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
  width: 480px;
  max-width: 90vw;
  height: 85vh;
  max-height: 680px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: slideUp 0.25s ease;
}
@keyframes slideUp {
  from { transform: translateY(20px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--oaa-space-5) var(--oaa-space-6);
  border-bottom: 1px solid var(--oaa-border-subtle);
  flex-shrink: 0;
}
.modal-header h3 {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
  margin: 0;
}
.modal-close {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--oaa-radius-sm);
  background: transparent;
  color: var(--oaa-color-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--oaa-text-base);
}
.modal-close:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--oaa-color-primary);
}

.modal-body {
  padding: var(--oaa-space-5) var(--oaa-space-6);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
  flex: 1;
  min-height: 0;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}
.form-group label {
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.form-group .oaa-input {
  padding: var(--oaa-space-2) var(--oaa-space-3);
  border-radius: var(--oaa-radius-md);
}
.form-hint {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  margin: 2px 0 0 0;
}

.form-row {
  display: flex;
  gap: var(--oaa-space-3);
}
.form-row .form-group {
  flex: 1;
}
.form-group--sm {
  max-width: 100px;
}

.advanced-details {
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
}
.advanced-summary {
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-muted);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  cursor: pointer;
  user-select: none;
}
.advanced-summary:hover {
  color: var(--oaa-color-secondary);
  background: rgba(255, 255, 255, 0.03);
}
.advanced-body {
  padding: var(--oaa-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-3);
  border-top: 1px solid var(--oaa-border-subtle);
}

.form-error-msg {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-error);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  background: rgba(239, 68, 68, 0.08);
  border-radius: var(--oaa-radius-md);
}
.form-error-list {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-error);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  background: rgba(239, 68, 68, 0.08);
  border-radius: var(--oaa-radius-md);
}
.form-error-list p {
  margin: 2px 0;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-4) var(--oaa-space-6);
  border-top: 1px solid var(--oaa-border-subtle);
  flex-shrink: 0;
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
  z-index: 1100;
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

/* ------------------------------------------------------------------ */
/* Light theme — 暖米白                                                 */
/* ------------------------------------------------------------------ */
[data-theme="light"] .connections-view {
  background: var(--oaa-bg-app);
}

[data-theme="light"] .channel-card {
  background: var(--oaa-bg-surface);
}

[data-theme="light"] .channel-card-inner {
  background: var(--oaa-bg-surface);
}

[data-theme="light"] .email-item {
  background: var(--oaa-bg-surface);
}

[data-theme="light"] .email-item:hover {
  background: var(--oaa-bg-surface-hover);
}

[data-theme="light"] .modal-overlay {
  background: rgba(247, 243, 238, 0.7);
}

[data-theme="light"] .modal-panel {
  background: var(--oaa-bg-surface);
}

[data-theme="light"] .toast.success {
  background: #dcfce7;
  color: #166534;
}

[data-theme="light"] .toast.error {
  background: #fef2f2;
  color: #991b1b;
}
</style>

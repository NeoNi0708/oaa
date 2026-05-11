<template>
  <div class="view-container">
    <div class="view-header">
      <h2>设置</h2>
      <p class="view-subtitle">配置应用偏好与权限</p>
    </div>

    <div v-if="configLoading" class="settings-loading">
      <span class="settings-spinner"></span>
      <span>加载配置中...</span>
    </div>

    <div v-else class="settings-content">
      <!-- 模型配置 -->
      <section class="settings-section">
        <div class="section-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2a10 10 0 1 0 10 10H12V2z"/><path d="M12 2a10 10 0 0 1 10 10h-10V2z"/>
          </svg>
          <h3>模型配置</h3>
        </div>
        <div class="form-group">
          <label class="oaa-label">模型厂商</label>
          <select v-model="form.provider" class="oaa-select" @change="onProviderChange">
            <optgroup v-for="group in providerGroups" :key="group.label" :label="group.label">
              <option v-for="p in group.options" :key="p.value" :value="p.value">{{ p.label }}</option>
            </optgroup>
          </select>
        </div>
        <div class="form-group">
          <label class="oaa-label">计费模式</label>
          <select v-model="form.planType" class="oaa-select" @change="onPlanChange">
            <option v-for="p in availablePlans" :key="p.value" :value="p.value">{{ p.label }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="oaa-label">API 格式</label>
          <select v-model="form.apiFormat" class="oaa-select">
            <option value="openai">OpenAI 兼容</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>
        <div class="form-group">
          <label class="oaa-label">Base URL</label>
          <input v-model="form.baseUrl" type="text" class="oaa-input" placeholder="https://api.example.com/v1" />
        </div>
        <div class="form-group">
          <label class="oaa-label">API Key</label>
          <input v-model="form.apiKey" type="password" class="oaa-input" placeholder="sk-..." />
        </div>
        <div class="form-group">
          <label class="oaa-label">模型 ID</label>
          <input v-model="form.modelId" type="text" class="oaa-input" placeholder="例: deepseek-chat" />
        </div>
      </section>

      <!-- 数据目录 -->
      <section class="settings-section">
        <div class="section-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <h3>数据目录</h3>
        </div>
        <div class="form-group">
          <label class="oaa-label">工作空间路径</label>
          <div class="input-row">
            <input v-model="form.dataDir" type="text" class="oaa-input" placeholder="~/OAA" />
            <button class="oaa-btn oaa-btn--secondary" @click="browseDirectory">浏览</button>
          </div>
        </div>
      </section>

      <!-- 通道配置 -->
      <section class="settings-section">
        <div class="section-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
          </svg>
          <h3>通道配置</h3>
        </div>

        <!-- WeChat iLink -->
        <div class="channel-config">
          <div class="channel-toggle">
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.wechatEnabled" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text"><strong>微信 (iLink)</strong></span>
            </label>
            <span v-if="form.wechatToken" class="channel-auth-badge">已认证</span>
          </div>
          <div v-if="form.wechatEnabled" class="channel-fields">
            <div class="form-group">
              <label class="oaa-label">iLink Token</label>
              <input v-model="form.wechatToken" type="password" class="oaa-input" placeholder="扫码登录后自动填入" />
            </div>
            <div class="form-group">
              <label class="oaa-label">Bot ID</label>
              <input v-model="form.wechatBotId" type="text" class="oaa-input" placeholder="扫码登录后自动填入" readonly />
            </div>
            <p class="form-hint">前往「连接」页扫码登录微信后，Token 和 Bot ID 将自动填入此处。</p>
          </div>
        </div>

        <!-- DingTalk -->
        <div class="channel-config">
          <div class="channel-toggle">
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.dingtalkEnabled" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text"><strong>钉钉</strong></span>
            </label>
          </div>
          <div v-if="form.dingtalkEnabled" class="channel-fields">
            <div class="form-group">
              <label class="oaa-label">Client ID</label>
              <input v-model="form.dingtalkClientId" type="text" class="oaa-input" placeholder="钉钉应用 Client ID" />
            </div>
            <div class="form-group">
              <label class="oaa-label">Client Secret</label>
              <input v-model="form.dingtalkClientSecret" type="password" class="oaa-input" placeholder="钉钉应用 Client Secret" />
            </div>
          </div>
        </div>

        <!-- Feishu -->
        <div class="channel-config">
          <div class="channel-toggle">
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.feishuEnabled" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text"><strong>飞书</strong></span>
            </label>
          </div>
          <div v-if="form.feishuEnabled" class="channel-fields">
            <div class="form-group">
              <label class="oaa-label">App ID</label>
              <input v-model="form.feishuAppId" type="text" class="oaa-input" placeholder="飞书应用 App ID" />
            </div>
            <div class="form-group">
              <label class="oaa-label">App Secret</label>
              <input v-model="form.feishuAppSecret" type="password" class="oaa-input" placeholder="飞书应用 App Secret" />
            </div>
          </div>
        </div>
      </section>

      <!-- 权限管理 -->
      <section class="settings-section">
        <div class="section-header">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
          <h3>权限管理</h3>
        </div>
        <div class="form-group">
          <label class="oaa-label">高风险操作确认</label>
          <p class="form-hint">选中的操作在执行前需要用户确认</p>
          <div class="checkbox-group" style="margin-top: 8px;">
            <label class="checkbox-label">
              <input type="checkbox" :value="'email_send'" v-model="form.requireConfirm" :true-value="'email_send'" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text">发送邮件前确认</span>
            </label>
            <label class="checkbox-label">
              <input type="checkbox" :value="'wechat_send'" v-model="form.requireConfirm" :true-value="'wechat_send'" class="checkbox-input" />
              <span class="checkbox-custom"></span>
              <span class="checkbox-text">发送微信消息前确认</span>
            </label>
          </div>
        </div>
        <div class="form-group">
          <label class="oaa-label">路径黑名单</label>
          <p class="form-hint">禁止 Agent 访问的目录或文件（每行一个绝对路径）</p>
          <textarea
            v-model="form.blacklistPathsText"
            class="oaa-input form-textarea"
            rows="3"
            placeholder="例: C:\Windows\System32"
            style="margin-top: 8px;"
          ></textarea>
        </div>
      </section>

      <!-- 保存 -->
      <div class="save-row">
        <span v-if="saveStatus" class="save-status">{{ saveStatus }}</span>
        <button class="oaa-btn oaa-btn--primary" @click="saveSettings" :disabled="saving">
          <svg v-if="saving" class="btn-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          保存设置
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useWebSocket, type MgmtResponse } from '../composables/useWebSocket'

declare const oaa: any

const { sendRequest } = useWebSocket()

const providerGroups = [
  {
    label: '国内厂商',
    options: [
      { value: 'deepseek', label: 'DeepSeek' },
      { value: 'volcengine', label: '豆包（火山引擎）' },
      { value: 'tongyi', label: '通义千问（百炼）' },
      { value: 'siliconflow', label: '硅基流动（SiliconFlow）' },
      { value: 'zhipu', label: '智谱 GLM' },
      { value: 'moonshot', label: 'Kimi（Moonshot）' },
      { value: 'baichuan', label: '百川（Baichuan）' },
      { value: 'stepfun', label: '阶跃星辰（StepFun）' },
      { value: 'minimax', label: 'MiniMax' },
      { value: 'lingyi', label: '零一万物（01.AI）' },
      { value: 'xunfei', label: '讯飞星辰' },
      { value: 'xiaomi', label: '小米' },
    ],
  },
  {
    label: '海外厂商',
    options: [
      { value: 'openai', label: 'OpenAI' },
      { value: 'anthropic', label: 'Anthropic Claude' },
    ],
  },
  {
    label: '自定义',
    options: [
      { value: 'custom-openai', label: '自定义（OpenAI 兼容）' },
      { value: 'custom-anthropic', label: '自定义（Anthropic）' },
    ],
  },
]

const planOptions: Record<string, { value: string; label: string }[]> = {
  deepseek: [{ value: 'api', label: '按量计费' }],
  volcengine: [
    { value: 'api', label: '按量计费' },
    { value: 'token', label: 'Token Plan' },
    { value: 'coding', label: 'Coding Plan' },
  ],
  tongyi: [
    { value: 'api', label: '按量计费' },
    { value: 'token', label: 'Token Plan' },
  ],
  siliconflow: [{ value: 'api', label: '按量计费' }],
  zhipu: [
    { value: 'api', label: '按量计费' },
    { value: 'coding', label: 'Coding Plan' },
  ],
  moonshot: [
    { value: 'api', label: '按量计费' },
    { value: 'coding', label: 'Coding Plan' },
  ],
  stepfun: [
    { value: 'api', label: '按量计费' },
    { value: 'step', label: 'Step Plan' },
  ],
  minimax: [
    { value: 'api', label: '按量计费' },
    { value: 'token', label: 'Token Plan' },
  ],
  xunfei: [{ value: 'coding', label: 'Coding Plan' }],
  xiaomi: [
    { value: 'api', label: '按量计费' },
    { value: 'token', label: 'Token Plan' },
  ],
}

const defaultPlan: Record<string, string> = {
  deepseek: 'api', volcengine: 'api', tongyi: 'api', siliconflow: 'api',
  zhipu: 'api', moonshot: 'api', stepfun: 'api', minimax: 'api',
  xunfei: 'coding', xiaomi: 'api', baichuan: 'api', lingyi: 'api',
  openai: 'api', anthropic: 'api',
  'custom-openai': 'api', 'custom-anthropic': 'api',
}

const availablePlans = computed(() => planOptions[form.provider] || [{ value: 'api', label: '按量计费' }])

// ------------------------------------------------------------------
// Plan URL lookup (maintained for provider/plan switching)
// ------------------------------------------------------------------

const planUrls: Record<string, Record<string, string>> = {
  deepseek: { api: 'https://api.deepseek.com' },
  volcengine: {
    api: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    token: 'https://ark.cn-beijing.volces.com/api/plan/v3',
    coding: 'https://ark.cn-beijing.volces.com/api/coding/v3',
  },
  tongyi: {
    api: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    token: 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
  },
  siliconflow: { api: 'https://api.siliconflow.cn/v1/chat/completions' },
  zhipu: {
    api: 'https://open.bigmodel.cn/api/paas/v4',
    coding: 'https://open.bigmodel.cn/api/coding/paas/v4',
  },
  moonshot: {
    api: 'https://api.moonshot.cn/v1/chat/completions',
    coding: 'https://api.kimi.com/coding/v1',
  },
  stepfun: {
    api: 'https://api.stepfun.com/v1/chat/completions',
    step: 'https://api.stepfun.com/step_plan/v1/chat/completions',
  },
  minimax: {
    api: 'https://api.minimaxi.com/v1',
    token: 'https://api.minimaxi.com/v1',
  },
  xunfei: { coding: 'https://maas-coding-api.cn-huabei-1.xf-yun.com/v2' },
  xiaomi: {
    api: 'https://api.xiaomimimo.com/v1/chat/completions',
    token: 'https://token-plan-cn.xiaomimimo.com/v1',
  },
  baichuan: { api: 'https://api.baichuan-ai.com/v1' },
  lingyi: { api: 'https://api.lingyiwanwu.com/v1' },
  openai: { api: 'https://api.openai.com/v1' },
  anthropic: { api: 'https://api.anthropic.com' },
  'custom-openai': { api: '' },
  'custom-anthropic': { api: '' },
}

// ------------------------------------------------------------------
// Form — flat layout, mapped to/from backend config structure
// ------------------------------------------------------------------

const defaultForm = {
  // model
  provider: 'deepseek',
  planType: 'api',
  apiFormat: 'openai' as 'openai' | 'anthropic',
  baseUrl: 'https://api.deepseek.com',
  apiKey: '',
  modelId: 'deepseek-chat',
  // data_dir
  dataDir: '',
  // wechat
  wechatEnabled: false,
  wechatToken: '',
  wechatBotId: '',
  // dingtalk
  dingtalkEnabled: false,
  dingtalkClientId: '',
  dingtalkClientSecret: '',
  // feishu
  feishuEnabled: false,
  feishuAppId: '',
  feishuAppSecret: '',
  // permissions
  requireConfirm: [] as string[],
  blacklistPathsText: '',
  // per-provider credentials — loaded from backend
  models: {} as Record<string, { api_key: string; model_id: string; base_url: string }>,
}

const form = reactive({ ...defaultForm })
const saveStatus = ref('')
const saving = ref(false)
const configLoading = ref(true)

// ------------------------------------------------------------------
// Backend ↔ Form mapping
// ------------------------------------------------------------------

function applyConfig(backendConfig: Record<string, unknown>) {
  const m = (backendConfig.model as Record<string, string>) || {}
  form.provider = m.provider || 'deepseek'
  form.planType = m.plan || 'api'
  form.apiFormat = (m.api_format as 'openai' | 'anthropic') || 'openai'
  form.baseUrl = m.base_url || ''
  form.apiKey = m.api_key || ''
  form.modelId = m.model_id || ''

  // Load per-provider credentials
  form.models = (backendConfig.models as Record<string, { api_key: string; model_id: string; base_url: string }>) || {}

  form.dataDir = (backendConfig.data_dir as string) || ''

  const wc = (backendConfig.wechat as Record<string, unknown>) || {}
  form.wechatEnabled = !!wc.enabled
  form.wechatToken = (wc.iLink_token as string) || ''
  form.wechatBotId = (wc.iLink_bot_id as string) || ''

  const dt = (backendConfig.dingtalk as Record<string, unknown>) || {}
  form.dingtalkEnabled = !!dt.enabled
  form.dingtalkClientId = (dt.client_id as string) || ''
  form.dingtalkClientSecret = (dt.client_secret as string) || ''

  const fs = (backendConfig.feishu as Record<string, unknown>) || {}
  form.feishuEnabled = !!fs.enabled
  form.feishuAppId = (fs.app_id as string) || ''
  form.feishuAppSecret = (fs.app_secret as string) || ''

  const perms = (backendConfig.permissions as Record<string, unknown>) || {}
  form.requireConfirm = (perms.require_confirm as string[]) || []
  form.blacklistPathsText = ((perms.blacklist_paths as string[]) || []).join('\n')
}

function buildConfigPayload() {
  // Save current provider's credentials into models dict
  const updatedModels = { ...form.models }
  updatedModels[form.provider] = {
    api_key: form.apiKey,
    model_id: form.modelId,
    base_url: form.baseUrl,
  }
  return {
    model: {
      provider: form.provider,
      plan: form.planType,
      api_format: form.apiFormat,
      base_url: form.baseUrl,
      api_key: form.apiKey,
      model_id: form.modelId,
      max_tokens: 8192,
      temperature: 0.7,
    },
    models: updatedModels,
    data_dir: form.dataDir,
    wechat: {
      enabled: form.wechatEnabled,
      iLink_token: form.wechatToken,
      iLink_bot_id: form.wechatBotId,
      wechat_cli_path: '',
    },
    dingtalk: {
      enabled: form.dingtalkEnabled,
      client_id: form.dingtalkClientId,
      client_secret: form.dingtalkClientSecret,
    },
    feishu: {
      enabled: form.feishuEnabled,
      app_id: form.feishuAppId,
      app_secret: form.feishuAppSecret,
    },
    permissions: {
      blacklist_paths: form.blacklistPathsText
        .split('\n')
        .map(s => s.trim())
        .filter(s => s.length > 0),
      require_confirm: form.requireConfirm,
    },
  }
}

// ------------------------------------------------------------------
// Lifecycle
// ------------------------------------------------------------------

onMounted(async () => {
  // Try WebSocket backend first
  try {
    const resp = await sendRequest('get_config')
    if (resp.ok && resp.config) {
      applyConfig(resp.config as Record<string, unknown>)
      configLoading.value = false
      return
    }
  } catch {
    // Backend not available — fall back to localStorage
  }

  // Electron IPC fallback
  if (window.oaa?.config?.load) {
    try {
      const data = await window.oaa.config.load()
      if (data) {
        const saved = JSON.parse(data)
        Object.assign(form, saved)
      }
    } catch { /* use defaults */ }
  }

  // localStorage fallback
  try {
    const raw = localStorage.getItem('oaa_settings')
    if (raw) {
      const saved = JSON.parse(raw)
      Object.assign(form, saved)
    }
  } catch { /* use defaults */ }

  configLoading.value = false
})

// ------------------------------------------------------------------
// Provider / plan helpers
// ------------------------------------------------------------------

function updateBaseUrl() {
  const providerUrls = planUrls[form.provider]
  if (providerUrls && providerUrls[form.planType]) {
    form.baseUrl = providerUrls[form.planType]
  }
}

function onProviderChange() {
  const formatMap: Record<string, string> = {
    anthropic: 'anthropic',
    'custom-anthropic': 'anthropic',
  }
  form.planType = defaultPlan[form.provider] || availablePlans.value[0]?.value || 'api'
  form.apiFormat = (formatMap[form.provider] || 'openai') as 'openai' | 'anthropic'

  // Load stored credentials for the selected provider
  const saved = form.models[form.provider]
  if (saved) {
    form.apiKey = saved.api_key || ''
    form.modelId = saved.model_id || ''
    if (saved.base_url) {
      form.baseUrl = saved.base_url
    }
  } else {
    form.apiKey = ''
    form.modelId = ''
    updateBaseUrl()
  }
}

function onPlanChange() {
  updateBaseUrl()
}

async function browseDirectory() {
  if (window.oaa?.dialog?.openDirectory) {
    const dir = await window.oaa.dialog.openDirectory()
    if (dir) form.dataDir = dir
  }
}

// ------------------------------------------------------------------
// Save
// ------------------------------------------------------------------

async function saveSettings() {
  saving.value = true
  saveStatus.value = ''

  const configPayload = buildConfigPayload()

  // Try WebSocket backend first
  try {
    const resp = await sendRequest('save_config', { config: configPayload })
    if (resp.ok) {
      saveStatus.value = '已保存到后端'
    } else {
      saveStatus.value = `保存失败: ${resp.error || '未知错误'}`
    }
    setTimeout(() => { saveStatus.value = '' }, 3000)
    saving.value = false
    return
  } catch {
    // Backend not available — fall through
  }

  // Electron IPC fallback
  if (window.oaa?.config?.save) {
    try {
      const ok = await window.oaa.config.save(JSON.stringify(configPayload))
      saveStatus.value = ok ? '已保存（Electron）' : '保存失败'
      setTimeout(() => { saveStatus.value = '' }, 3000)
      saving.value = false
      return
    } catch { /* fall through */ }
  }

  // localStorage fallback
  localStorage.setItem('oaa_settings', JSON.stringify(configPayload))
  saveStatus.value = '已保存（本地）'
  setTimeout(() => { saveStatus.value = '' }, 3000)
  saving.value = false
}
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: 720px;
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

.settings-content { display: flex; flex-direction: column; gap: var(--oaa-space-6); }

.settings-section {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-xl);
  padding: var(--oaa-space-6);
}

.section-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  margin-bottom: var(--oaa-space-5);
  padding-bottom: var(--oaa-space-3);
  border-bottom: 1px solid var(--oaa-border-subtle);
  color: var(--oaa-color-muted);
}

.section-header h3 { font-size: var(--oaa-text-lg); font-weight: 600; color: var(--oaa-color-primary); }

.form-group { margin-bottom: var(--oaa-space-4); }
.form-group:last-child { margin-bottom: 0; }
.form-hint { font-size: var(--oaa-text-xs); color: var(--oaa-color-disabled); margin-top: 2px; }

.input-row { display: flex; gap: var(--oaa-space-2); }
.input-row .oaa-input { flex: 1; }

.form-textarea { resize: vertical; min-height: 60px; }

/* --- Channel config --- */
.channel-config {
  padding: var(--oaa-space-3) 0;
  border-top: 1px solid var(--oaa-border-subtle);
}
.channel-config:first-of-type { border-top: none; padding-top: 0; }

.channel-toggle {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
}

.channel-auth-badge {
  font-size: var(--oaa-text-xs);
  padding: 1px 8px;
  border-radius: var(--oaa-radius-full);
  background: rgba(34, 197, 94, 0.15);
  color: var(--oaa-green-500);
}

.channel-fields {
  margin-top: var(--oaa-space-3);
  padding-left: var(--oaa-space-6);
}

/* --- Checkboxes --- */
.checkbox-group { display: flex; flex-direction: column; gap: var(--oaa-space-3); }

.checkbox-label { display: flex; align-items: center; gap: var(--oaa-space-2); cursor: pointer; user-select: none; }
.checkbox-input { display: none; }
.checkbox-custom {
  width: 20px; height: 20px;
  border: 2px solid var(--oaa-border-default);
  border-radius: var(--oaa-radius-sm);
  background: var(--oaa-bg-input);
  position: relative;
  flex-shrink: 0;
  transition: border-color var(--oaa-transition-fast), background var(--oaa-transition-fast);
}
.checkbox-input:checked + .checkbox-custom { background: var(--oaa-primary); border-color: var(--oaa-primary); }
.checkbox-input:checked + .checkbox-custom::after {
  content: ''; position: absolute; left: 5px; top: 1px;
  width: 6px; height: 10px;
  border: solid #fff; border-width: 0 2px 2px 0; transform: rotate(45deg);
}
.checkbox-text { color: var(--oaa-color-secondary); font-size: var(--oaa-text-base); }
.checkbox-text strong { color: var(--oaa-color-primary); }

/* --- Save bar --- */
.save-row { display: flex; align-items: center; justify-content: flex-end; gap: var(--oaa-space-3); padding-top: var(--oaa-space-2); }
.save-status { font-size: var(--oaa-text-sm); color: var(--oaa-success); }

.btn-spinner {
  animation: saveSpin 0.8s linear infinite;
}

@keyframes saveSpin {
  to { transform: rotate(360deg); }
}

/* --- Loading --- */
.settings-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  height: 300px;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}

.settings-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: settingsSpin 0.6s linear infinite;
}

@keyframes settingsSpin {
  to { transform: rotate(360deg); }
}
</style>

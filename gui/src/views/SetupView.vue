<template>
  <div class="setup-wrapper">
    <div class="setup-card">
      <!-- Logo / icon -->
      <div class="setup-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M12 2a10 10 0 1 0 10 10H12V2z"/><path d="M12 2a10 10 0 0 1 10 10h-10V2z"/>
        </svg>
      </div>
      <h1 class="setup-title">首次使用引导</h1>
      <p class="setup-desc">配置模型后即可开始使用 OAA</p>

      <!-- Provider -->
      <div class="form-group">
        <label class="oaa-label">模型厂商</label>
        <select v-model="form.provider" class="oaa-select" @change="onProviderChange">
          <optgroup v-for="group in providerGroups" :key="group.label" :label="group.label">
            <option v-for="p in group.options" :key="p.value" :value="p.value">{{ p.label }}</option>
          </optgroup>
        </select>
      </div>

      <!-- Base URL -->
      <div class="form-group">
        <label class="oaa-label">Base URL</label>
        <input v-model="form.baseUrl" type="text" class="oaa-input" placeholder="https://api.example.com/v1" />
      </div>

      <!-- Model ID -->
      <div class="form-group">
        <label class="oaa-label">模型 ID</label>
        <input v-model="form.modelId" type="text" class="oaa-input" placeholder="例: deepseek-chat" />
      </div>

      <!-- API Key -->
      <div class="form-group">
        <label class="oaa-label">API Key</label>
        <input v-model="form.apiKey" type="password" class="oaa-input" placeholder="sk-..." />
      </div>

      <!-- Error -->
      <div v-if="error" class="error-msg">{{ error }}</div>

      <!-- Actions -->
      <button class="oaa-btn oaa-btn--primary setup-btn" @click="save" :disabled="saving">
        <svg v-if="saving" class="btn-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
        </svg>
        {{ saving ? '保存中...' : '保存并开始使用' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const emit = defineEmits<{ (e: 'setup-complete'): void }>()

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

const planUrls: Record<string, Record<string, string>> = {
  deepseek: { api: 'https://api.deepseek.com' },
  volcengine: { api: 'https://ark.cn-beijing.volces.com/api/v3' },
  tongyi: { api: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  siliconflow: { api: 'https://api.siliconflow.cn/v1' },
  zhipu: { api: 'https://open.bigmodel.cn/api/paas/v4' },
  moonshot: { api: 'https://api.moonshot.cn/v1' },
  stepfun: { api: 'https://api.stepfun.com/v1' },
  minimax: { api: 'https://api.minimaxi.com/v1' },
  xunfei: { api: 'https://maas-api.cn-huabei-1.xf-yun.com/v2' },
  xiaomi: { api: 'https://api.xiaomimimo.com/v1' },
  baichuan: { api: 'https://api.baichuan-ai.com/v1' },
  lingyi: { api: 'https://api.lingyiwanwu.com/v1' },
  openai: { api: 'https://api.openai.com/v1' },
  anthropic: { api: 'https://api.anthropic.com' },
  'custom-openai': { api: '' },
  'custom-anthropic': { api: '' },
}

const apiFormatMap: Record<string, string> = {
  anthropic: 'anthropic',
  'custom-anthropic': 'anthropic',
}

const form = reactive({
  provider: 'deepseek',
  baseUrl: 'https://api.deepseek.com',
  modelId: '',
  apiKey: '',
})

const saving = ref(false)
const error = ref('')

function onProviderChange() {
  const urls = planUrls[form.provider]
  if (urls) {
    form.baseUrl = urls.api || urls[Object.keys(urls)[0]] || ''
  }
}

async function save() {
  if (!form.apiKey.trim()) {
    error.value = '请输入 API Key'
    return
  }
  if (!form.modelId.trim()) {
    error.value = '请输入模型 ID'
    return
  }
  if (!form.baseUrl.trim()) {
    error.value = '请输入 Base URL'
    return
  }

  saving.value = true
  error.value = ''

  try {
    const resp = await sendRequest('save_config', {
      config: {
        model: {
          provider: form.provider,
          plan: 'api',
          api_format: apiFormatMap[form.provider] || 'openai',
          base_url: form.baseUrl,
          api_key: form.apiKey,
          model_id: form.modelId,
        },
      },
    })
    if (resp.ok) {
      emit('setup-complete')
    } else {
      error.value = resp.error || '保存失败'
    }
  } catch (e: any) {
    error.value = '网络错误: ' + (e.message || e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.setup-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--oaa-bg-app);
}

.setup-card {
  width: 420px;
  padding: 48px 40px;
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-glass-border);
  border-radius: var(--oaa-radius-xl);
  text-align: center;
}

.setup-icon {
  color: var(--oaa-blue-400);
  opacity: 0.6;
  margin-bottom: 16px;
}

.setup-title {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin: 0 0 4px;
}

.setup-desc {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  margin: 0 0 32px;
}

.form-group {
  text-align: left;
  margin-bottom: 16px;
}

.form-group .oaa-label {
  display: block;
  margin-bottom: 6px;
}

.form-group .oaa-input,
.form-group .oaa-select {
  width: 100%;
  box-sizing: border-box;
}

.error-msg {
  margin-top: 8px;
  font-size: var(--oaa-text-sm);
  color: var(--oaa-error);
  text-align: left;
}

.setup-btn {
  width: 100%;
  margin-top: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.btn-spinner {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>

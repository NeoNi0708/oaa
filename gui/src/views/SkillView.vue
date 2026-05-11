<template>
  <div class="view-container">
    <div class="view-header">
      <h2>技能</h2>
      <p class="view-subtitle">管理、安装与发现技能</p>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        :class="['tab-btn', { active: activeTab === tab.id }]"
        @click="activeTab = tab.id"
      >
        <span class="tab-icon">{{ tab.icon }}</span>
        <span class="tab-label">{{ tab.label }}</span>
      </button>
    </div>

    <!-- 技能仓库 -->
    <div v-if="activeTab === 'repo'" class="tab-content">
      <div v-if="loading" class="skill-loading">
        <span class="skill-spinner"></span>
        <span>加载技能列表...</span>
      </div>
      <template v-else>
      <div class="section-header-inline">
        <h3>已安装技能</h3>
        <span class="oaa-badge oaa-badge--count">{{ allSkills.length }}</span>
      </div>

      <div v-for="group in skillGroups" :key="group.category" class="skill-group">
        <div class="group-header">{{ group.category }}</div>
        <div class="skill-list">
          <div v-for="skill in group.skills" :key="skill.name" class="skill-card">
            <div class="skill-icon-wrapper">
              <span class="skill-icon">{{ skill.icon }}</span>
            </div>
            <div class="skill-info">
              <div class="skill-name">{{ skill.name }}</div>
              <div class="skill-desc">{{ skill.description }}</div>
            </div>
          </div>
        </div>
      </div>
      </template>
    </div>

    <!-- 自生技能 -->
    <div v-if="activeTab === 'evolution'" class="tab-content">
      <div v-if="loading" class="skill-loading">
        <span class="skill-spinner"></span>
        <span>加载进化建议...</span>
      </div>
      <template v-else>
      <div class="section-header-inline">
        <h3>自生技能推荐</h3>
        <span class="oaa-badge oaa-badge--accent">AI</span>
      </div>
      <div class="evolution-grid">
        <div v-for="item in evolutionSuggestions" :key="item.title" class="evolution-card">
          <div class="evo-header">
            <span class="evo-icon">{{ item.icon }}</span>
            <span class="oaa-badge" :class="item.badgeClass">{{ item.tag }}</span>
          </div>
          <div class="evo-title">{{ item.title }}</div>
          <div class="evo-desc">{{ item.description }}</div>
          <div class="evo-footer">
            <span class="evo-confidence">置信度: {{ item.confidence }}%</span>
            <button class="oaa-btn oaa-btn--sm" :class="item.applied ? 'btn-applied' : 'oaa-btn--primary'">
              {{ item.applied ? '已应用' : '应用' }}
            </button>
          </div>
        </div>
      </div>
      </template>
    </div>

    <!-- 技能市场 -->
    <div v-if="activeTab === 'market'" class="tab-content">
      <div class="section-header-inline">
        <h3>技能市场</h3>
      </div>
      <div v-if="iframeLoading" class="market-loading">
        <span class="market-spinner"></span>
        <span>加载中...</span>
      </div>
      <div v-if="iframeError" class="market-error">
        <span class="error-icon">!</span>
        <p>无法加载技能市场</p>
        <p class="error-detail">{{ iframeError }}</p>
        <button class="oaa-btn oaa-btn--sm oaa-btn--secondary" @click="retryIframe">重试</button>
      </div>
      <div class="iframe-container" :class="{ hidden: iframeLoading || iframeError }">
        <iframe
          ref="marketIframe"
          src="https://cn.clawhub-mirror.com"
          class="market-iframe"
          sandbox="allow-scripts allow-same-origin allow-forms"
          title="技能市场"
          @load="onIframeLoad"
          @error="onIframeError"
        ></iframe>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest } = useWebSocket()

const activeTab = ref('repo')
const marketIframe = ref<HTMLIFrameElement | null>(null)
const iframeLoading = ref(true)
const iframeError = ref('')
const loading = ref(true)

function onIframeLoad() {
  iframeLoading.value = false
  iframeError.value = ''
}

function onIframeError() {
  iframeLoading.value = false
  iframeError.value = '请检查网络连接后重试'
}

function retryIframe() {
  iframeLoading.value = true
  iframeError.value = ''
  if (marketIframe.value) {
    marketIframe.value.src = 'https://cn.clawhub-mirror.com'
  }
}

const tabs = [
  { id: 'repo', icon: '📦', label: '技能仓库' },
  { id: 'evolution', icon: '🧬', label: '自生技能' },
  { id: 'market', icon: '🏪', label: '技能市场' },
]

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface Skill {
  icon: string
  name: string
  description: string
}

interface SkillGroup {
  category: string
  skills: Skill[]
}

interface EvolutionSuggestion {
  icon: string
  title: string
  description: string
  tag: string
  badgeClass: string
  confidence: number
  applied: boolean
}

// ------------------------------------------------------------------
// Fallback data — used when backend is unavailable
// ------------------------------------------------------------------

const fallbackSkillGroups: SkillGroup[] = [
  {
    category: '外贸业务核心',
    skills: [
      { icon: '🌐', name: '外贸业务综合', description: '综合处理外贸业务流程、政策咨询与方案建议' },
      { icon: '💼', name: '业务助理', description: '协助处理日常业务事务与工作安排' },
      { icon: '📄', name: '报价单制作', description: '快速生成专业报价单文档' },
      { icon: '📝', name: '合同审核', description: '审核合同条款，识别风险并提供修改建议' },
      { icon: '🤝', name: '客户支持', description: '提供客户售前售后咨询与服务' },
      { icon: '✉', name: '邮件撰写', description: '撰写与优化外贸业务邮件' },
      { icon: '📋', name: '询盘处理', description: '处理客户询盘，生成回复方案' },
      { icon: '👥', name: '客户关系管理', description: '管理客户信息与跟进记录' },
      { icon: '💰', name: '财务助理', description: '协助处理报价核算、成本分析等财务事务' },
      { icon: '📞', name: '跟进提醒', description: '自动跟进客户与项目进度提醒' },
      { icon: '🚚', name: '物流协调', description: '协助处理物流运输与报关事务' },
      { icon: '📊', name: '市场分析', description: '分析行业趋势与市场数据' },
      { icon: '🔍', name: '市场调研', description: '执行市场调研并生成调研报告' },
      { icon: '🎯', name: '客户开发', description: '自动化客户开发与外拓流程' },
      { icon: '🛒', name: '采购管理', description: '协助供应商筛选与采购流程管理' },
      { icon: '🔎', name: '搜索执行', description: '执行定向搜索任务，收集信息' },
    ],
  },
  {
    category: '办公文档',
    skills: [
      { icon: '📝', name: 'Word 文档', description: '生成与编辑 Word 文档' },
      { icon: '📊', name: 'Excel 表格', description: '生成与编辑 Excel 电子表格' },
      { icon: '📕', name: 'PDF 处理', description: '读取与处理 PDF 文件' },
    ],
  },
  {
    category: '通信消息',
    skills: [
      { icon: '💬', name: '微信 CLI', description: '通过命令行工具收发微信消息' },
      { icon: '📧', name: '邮件客户端', description: '收发电子邮件' },
      { icon: '🔌', name: 'ClawHub', description: '技能市场与插件管理' },
    ],
  },
  {
    category: '系统与自进化',
    skills: [
      { icon: '🧠', name: '自改进', description: '自动分析与优化自身行为模式' },
      { icon: '🤖', name: '自主代理工具包', description: '提供自主决策与任务规划能力' },
      { icon: '🔧', name: '技能创建器', description: '根据需求自动创建新技能' },
      { icon: '💾', name: '代理记忆', description: '管理与持久化跨会话记忆' },
      { icon: '🌐', name: '浏览器', description: '网页浏览与信息采集' },
      { icon: '🌤', name: '天气查询', description: '查询实时天气信息' },
      { icon: '📃', name: '摘要生成', description: '自动生成文本摘要' },
    ],
  },
]

const fallbackEvolution: EvolutionSuggestion[] = [
  {
    icon: '🧠', title: '上下文感知记忆',
    description: '扩展工作记忆，自动保留跨会话的用户上下文和偏好',
    tag: '热门', badgeClass: 'oaa-badge--error', confidence: 87, applied: false,
  },
  {
    icon: '🔗', title: '工具链式调用',
    description: '允许多个技能编排为流水线，前一个输出自动成为下一个输入',
    tag: '新', badgeClass: 'oaa-badge--count', confidence: 73, applied: false,
  },
  {
    icon: '📊', title: '分析面板',
    description: '可视化展示技能使用指标、性能数据和演化趋势',
    tag: '稳定', badgeClass: 'oaa-badge--success', confidence: 91, applied: true,
  },
  {
    icon: '🗣', title: '多模态输入输出',
    description: '支持图像、音频、视频在技能流水线中的输入与输出',
    tag: '内测', badgeClass: 'oaa-badge--warning', confidence: 64, applied: false,
  },
]

// ------------------------------------------------------------------
// Reactive state — initialized from fallback, replaced by backend
// ------------------------------------------------------------------

const skillGroups = ref<SkillGroup[]>([...fallbackSkillGroups])
const allSkills = ref<Skill[]>(skillGroups.value.flatMap(g => g.skills))
const evolutionSuggestions = ref<EvolutionSuggestion[]>([...fallbackEvolution])

// ------------------------------------------------------------------
// Icon assignment by name keyword
// ------------------------------------------------------------------

const iconMap: Record<string, string> = {
  '报价': '📄', '合同': '📝', '客户': '🤝', '邮件': '✉', '询盘': '📋',
  '财务': '💰', '物流': '🚚', '市场分析': '📊', '市场调研': '🔍',
  '开发': '🎯', '采购': '🛒', '搜索': '🔎', '业务': '💼', '外贸': '🌐',
  '跟进': '📞', 'word': '📝', 'excel': '📊', 'pdf': '📕', '微信': '💬',
  'clawhub': '🔌', '记忆': '💾', '改进': '🧠', '工具包': '🤖',
  '创建': '🔧', '浏览器': '🌐', '天气': '🌤', '摘要': '📃',
}

function inferIcon(name: string): string {
  const lower = name.toLowerCase()
  for (const [keyword, icon] of Object.entries(iconMap)) {
    if (lower.includes(keyword)) return icon
  }
  return '📦'
}

const tagBootstrap = ['oaa-badge--error', 'oaa-badge--count', 'oaa-badge--success', 'oaa-badge--warning']
let tagIdx = 0

// ------------------------------------------------------------------
// Backend sync
// ------------------------------------------------------------------

interface BackendSkill {
  name: string
  category: string
  path: string
  loaded: boolean
  tools_count: number
  knowledge_count: number
}

interface BackendEvolutionItem {
  type: string
  skill?: string
  message: string
  usage_count?: number
  step?: string
  skip_count?: number
}

onMounted(async () => {
  // Load skills from backend
  try {
    const resp = await sendRequest('get_skills')
    if (resp.ok && Array.isArray(resp.skills)) {
      const skills = resp.skills as BackendSkill[]
      // Group by category
      const groupMap = new Map<string, Skill[]>()
      for (const s of skills) {
        const cat = s.category || '未分类'
        if (!groupMap.has(cat)) groupMap.set(cat, [])
        const desc = s.tools_count > 0
          ? `已激活 · ${s.tools_count} 工具 · ${s.knowledge_count} 知识`
          : '待激活'
        groupMap.get(cat)!.push({ icon: inferIcon(s.name), name: s.name, description: desc })
      }
      skillGroups.value = Array.from(groupMap, ([category, skills]) => ({ category, skills }))
      allSkills.value = skills
    }
  } catch {
    // Keep fallback data
  }

  // Load evolution suggestions from backend
  try {
    const resp = await sendRequest('get_evolution')
    if (resp.ok && Array.isArray(resp.suggestions)) {
      const items = resp.suggestions as BackendEvolutionItem[]
      if (items.length > 0) {
        evolutionSuggestions.value = items.map((item, i) => ({
          icon: '🧬',
          title: item.skill ? `「${item.skill}」优化建议` : item.message.slice(0, 20),
          description: item.message,
          tag: item.type === 'optimize' ? '热门' : item.type === 'sop_refine' ? '内测' : '稳定',
          badgeClass: tagBootstrap[i % 4],
          confidence: item.usage_count ? Math.min(99, item.usage_count * 15) : 70,
          applied: false,
        }))
      }
    }
  } catch {
    // Keep fallback data
  }

  loading.value = false
})
</script>

<style scoped>
.view-container {
  padding: var(--oaa-space-8);
  max-width: var(--oaa-view-max-width);
  margin: 0 auto;
  color: var(--oaa-color-primary);
}

.view-header {
  margin-bottom: var(--oaa-space-6);
}

.view-header h2 {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-color-primary);
  margin-bottom: var(--oaa-space-1);
}

.view-subtitle {
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-base);
}

.tab-bar {
  display: flex;
  gap: var(--oaa-space-1);
  background: var(--oaa-bg-surface);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-6);
  border: 1px solid var(--oaa-border-subtle);
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-base);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--oaa-transition-fast), color var(--oaa-transition-fast);
}

.tab-btn:hover {
  color: var(--oaa-color-primary);
  background: var(--oaa-primary-light);
}

.tab-btn.active {
  background: var(--oaa-primary);
  color: #fff;
}

.tab-icon { font-size: 1.05rem; }

.tab-content {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.section-header-inline {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-4);
}

.section-header-inline h3 {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-secondary);
}

.skill-group {
  margin-bottom: var(--oaa-space-6);
}

.group-header {
  font-size: var(--oaa-text-sm);
  font-weight: 600;
  color: var(--oaa-color-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: var(--oaa-space-2);
  padding: 0 var(--oaa-space-1);
}

.skill-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}

.skill-card {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  background: var(--oaa-bg-surface);
  border: 1px solid transparent;
  border-radius: var(--oaa-radius-md);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  transition: border-color var(--oaa-transition-fast), background var(--oaa-transition-fast);
}

.skill-card:hover {
  border-color: var(--oaa-border-default);
  background: var(--oaa-bg-surface-hover);
}

.skill-icon-wrapper {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--oaa-primary-light);
  border-radius: var(--oaa-radius-sm);
  flex-shrink: 0;
}

.skill-icon { font-size: 1rem; }

.skill-info {
  flex: 1;
  min-width: 0;
}

.skill-name {
  font-size: var(--oaa-text-base);
  font-weight: 600;
  color: var(--oaa-color-primary);
}

.skill-desc {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.evolution-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--oaa-space-3);
}

.evolution-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-xl);
  padding: var(--oaa-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
  transition: border-color var(--oaa-transition-fast), transform var(--oaa-transition-fast);
}

.evolution-card:hover {
  border-color: var(--oaa-primary);
  transform: translateY(-2px);
}

.evo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.evo-icon { font-size: 1.4rem; }

.evo-title {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  color: var(--oaa-color-primary);
}

.evo-desc {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  line-height: 1.45;
}

.evo-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--oaa-space-1);
}

.evo-confidence {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
}

.btn-applied {
  background: rgba(34, 197, 94, 0.15);
  color: var(--oaa-green-500);
  cursor: default;
  pointer-events: none;
}

.iframe-container {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-xl);
  overflow: hidden;
  height: 560px;
}

.iframe-container.hidden {
  display: none;
}

.market-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  height: 200px;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}

.market-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.market-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  height: 200px;
  color: var(--oaa-color-muted);
  text-align: center;
}

.error-icon {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--oaa-error-light, rgba(239, 68, 68, 0.15));
  color: var(--oaa-error);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 700;
  margin-bottom: var(--oaa-space-1);
}

.error-detail {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  max-width: 280px;
}

.market-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

.skill-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--oaa-space-2);
  height: 200px;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}

.skill-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--oaa-border-subtle);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: skillSpin 0.6s linear infinite;
}

@keyframes skillSpin {
  to { transform: rotate(360deg); }
}
</style>

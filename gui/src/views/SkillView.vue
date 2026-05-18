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

      <div class="skill-search">
        <span class="search-icon">🔍</span>
        <input
          v-model="searchQuery"
          type="text"
          class="search-input"
          placeholder="搜索技能名称或描述..."
        />
        <button v-if="searchQuery" class="search-clear" @click="searchQuery = ''">✕</button>
      </div>

      <div v-for="group in filteredGroups" :key="group.category" class="skill-group">
        <div class="group-header">{{ group.category }}</div>
        <div class="skill-list">
          <div
            v-for="skill in group.skills"
            :key="skill.name"
            :class="['skill-card', { expanded: expandedSkill === skill.name }]"
            @click="toggleSkillDetail(skill.name)"
          >
            <div class="skill-icon-wrapper">
              <span class="skill-icon">{{ skill.icon }}</span>
            </div>
            <div class="skill-info">
              <div class="skill-name">
                {{ skill.name }}
                <span v-if="skill.current" class="skill-current-tag">当前</span>
              </div>
              <div class="skill-desc">{{ skill.description }}</div>
            </div>
            <div class="skill-status">
              <span v-if="skill.current" class="status-badge status-active">已加载</span>
              <span v-else-if="skill.toolsCount > 0" class="status-badge status-ready">已安装</span>
              <span v-else class="status-badge status-inactive">待激活</span>
            </div>
          </div>

          <!-- Detail panel for expanded skill -->
          <div v-if="expandedSkill === skill.name && skillDetail" class="skill-detail-panel">
            <div class="detail-tabs">
              <button
                v-for="dt in detailTabs"
                :key="dt.id"
                :class="['detail-tab-btn', { active: activeDetailTab === dt.id }]"
                @click.stop="activeDetailTab = dt.id"
              >{{ dt.label }}</button>
            </div>

            <!-- Description tab -->
            <div v-if="activeDetailTab === 'desc'" class="detail-content">
              <div class="detail-desc">{{ skillDetail.description || '暂无描述' }}</div>
              <div v-if="skillDetail.tools && skillDetail.tools.length" class="detail-section">
                <h4>工具 ({{ skillDetail.tools.length }})</h4>
                <div v-for="t in skillDetail.tools" :key="t.name || t.function?.name" class="detail-tool-item">
                  <span class="tool-name">{{ t.name || t.function?.name }}</span>
                  <span class="tool-desc">{{ t.description || t.function?.description || '' }}</span>
                </div>
              </div>
              <div v-if="skillDetail.knowledge && skillDetail.knowledge.length" class="detail-section">
                <h4>知识文档 ({{ skillDetail.knowledge.length }})</h4>
                <div v-for="(k, i) in skillDetail.knowledge" :key="i" class="detail-knowledge-item">
                  <details>
                    <summary>知识 #{{ i + 1 }} ({{ k.length }} 字)</summary>
                    <pre class="knowledge-body">{{ k }}</pre>
                  </details>
                </div>
              </div>
            </div>

            <!-- SKILL.md tab -->
            <div v-if="activeDetailTab === 'skill'" class="detail-content">
              <pre v-if="skillDetail.skill_md" class="detail-markdown">{{ skillDetail.skill_md }}</pre>
              <div v-else class="detail-empty">无 SKILL.md</div>
            </div>

            <!-- SOP.md tab -->
            <div v-if="activeDetailTab === 'sop'" class="detail-content">
              <pre v-if="skillDetail.sop_md" class="detail-markdown">{{ skillDetail.sop_md }}</pre>
              <div v-else class="detail-empty">无 SOP.md</div>
            </div>

            <div class="detail-actions">
              <button
                v-if="!skillDetail.is_current"
                class="oaa-btn oaa-btn--primary oaa-btn--sm"
                @click.stop="loadSkill(skill.name)"
              >加载此技能</button>
              <span v-else class="current-skill-badge">✓ 当前已加载</span>
            </div>
          </div>

        </div>
      </div>
      <div v-if="filteredGroups.length === 0" class="no-results">
        没有找到匹配 "{{ searchQuery }}" 的技能
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
      <!-- Stats dashboard -->
      <div v-if="evolutionStats" class="evo-stats-row">
        <div class="evo-stat-card">
          <span class="stat-value">{{ statTotalUsage }}</span>
          <span class="stat-label">技能使用次数</span>
        </div>
        <div class="evo-stat-card">
          <span class="stat-value">{{ evolutionStats.crystallized.length }}</span>
          <span class="stat-label">已固化技能</span>
        </div>
        <div class="evo-stat-card">
          <span class="stat-value">{{ Object.keys(evolutionStats.sop_executions).length }}</span>
          <span class="stat-label">SOP 执行数</span>
        </div>
        <div class="evo-stat-card">
          <span class="stat-value">{{ evolutionSuggestions.length }}</span>
          <span class="stat-label">待处理建议</span>
        </div>
      </div>

      <!-- Usage ranking -->
      <div v-if="usageRanking.length" class="evo-usage-section">
        <h3>技能使用排行</h3>
        <div class="usage-list">
          <div v-for="(item, i) in usageRanking" :key="item.name" class="usage-row">
            <span class="usage-rank">#{{ i + 1 }}</span>
            <span class="usage-name">{{ item.name }}</span>
            <div class="usage-bar-bg">
              <div class="usage-bar-fill" :style="{ width: item.pct + '%' }"></div>
            </div>
            <span class="usage-count">{{ item.count }} 次</span>
          </div>
        </div>
      </div>

      <!-- Crystallized skills -->
      <div v-if="evolutionStats.crystallized.length" class="evo-section">
        <h3>已固化技能</h3>
        <div class="crystallized-list">
          <div v-for="c in evolutionStats.crystallized" :key="c.name" class="crystallized-item">
            <span class="cryst-icon">🧊</span>
            <span class="cryst-name">{{ c.name }}</span>
            <span class="cryst-date">{{ formatDate(c.created) }}</span>
          </div>
        </div>
      </div>

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
            <button
              class="oaa-btn oaa-btn--sm"
              :class="item.applied ? 'btn-applied' : 'oaa-btn--primary'"
              @click="applyEvolution(item)"
            >
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
import { ref, computed, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest } = useWebSocket()

const activeTab = ref('repo')
const marketIframe = ref<HTMLIFrameElement | null>(null)
const iframeLoading = ref(true)
const iframeError = ref('')
const loading = ref(true)

// Search & detail state
const searchQuery = ref('')
const expandedSkill = ref<string | null>(null)
const skillDetail = ref<BackendSkillDetail | null>(null)
const activeDetailTab = ref('desc')

const detailTabs = [
  { id: 'desc', label: '详情' },
  { id: 'skill', label: 'SKILL.md' },
  { id: 'sop', label: 'SOP.md' },
]

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
  active: boolean
  current: boolean
  toolsCount: number
  knowledgeCount: number
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

interface BackendSkillDetail {
  name: string
  category: string
  description: string
  skill_md: string
  sop_md: string
  tools: Array<{name?: string; description?: string; function?: {name: string; description: string}}>
  knowledge: string[]
  is_current: boolean
}

interface EvolutionStats {
  skill_usage: Record<string, number>
  sop_executions: Record<string, number>
  crystallized: Array<{name: string; created: string}>
}

// ------------------------------------------------------------------
// Fallback data — used when backend is unavailable
// ------------------------------------------------------------------

const fallbackSkillGroups: SkillGroup[] = [
  {
    category: '外贸业务核心',
    skills: [
      { icon: '🌐', name: '外贸业务综合', description: '综合处理外贸业务流程', active: true, current: false, toolsCount: 3, knowledgeCount: 5 },
      { icon: '💼', name: '业务助理', description: '协助处理日常业务事务', active: true, current: false, toolsCount: 4, knowledgeCount: 3 },
      { icon: '📄', name: '报价单制作', description: '快速生成专业报价单文档', active: true, current: false, toolsCount: 2, knowledgeCount: 2 },
      { icon: '📝', name: '合同审核', description: '审核合同条款，识别风险', active: true, current: false, toolsCount: 1, knowledgeCount: 4 },
      { icon: '🤝', name: '客户支持', description: '提供客户售前售后咨询', active: true, current: false, toolsCount: 2, knowledgeCount: 3 },
      { icon: '✉', name: '邮件撰写', description: '撰写与优化外贸业务邮件', active: true, current: false, toolsCount: 1, knowledgeCount: 2 },
      { icon: '📋', name: '询盘处理', description: '处理客户询盘，回复方案', active: true, current: false, toolsCount: 2, knowledgeCount: 2 },
      { icon: '👥', name: '客户关系管理', description: '管理客户信息与跟进记录', active: false, current: false, toolsCount: 0, knowledgeCount: 2 },
      { icon: '💰', name: '财务助理', description: '报价核算、成本分析', active: true, current: false, toolsCount: 3, knowledgeCount: 2 },
      { icon: '📞', name: '跟进提醒', description: '自动跟进客户与项目进度', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🚚', name: '物流协调', description: '物流运输与报关事务', active: false, current: false, toolsCount: 0, knowledgeCount: 2 },
      { icon: '📊', name: '市场分析', description: '分析行业趋势与市场数据', active: true, current: false, toolsCount: 2, knowledgeCount: 3 },
      { icon: '🔍', name: '市场调研', description: '执行市场调研并生成报告', active: true, current: false, toolsCount: 3, knowledgeCount: 2 },
      { icon: '🎯', name: '客户开发', description: '自动化客户开发与外拓', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🛒', name: '采购管理', description: '供应商筛选与采购流程', active: false, current: false, toolsCount: 0, knowledgeCount: 2 },
      { icon: '🔎', name: '搜索执行', description: '定向搜索任务，收集信息', active: true, current: false, toolsCount: 1, knowledgeCount: 1 },
    ],
  },
  {
    category: '办公文档',
    skills: [
      { icon: '📝', name: 'Word 文档', description: '生成与编辑 Word 文档', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '📊', name: 'Excel 表格', description: '生成与编辑 Excel 表格', active: true, current: false, toolsCount: 1, knowledgeCount: 1 },
      { icon: '📕', name: 'PDF 处理', description: '读取与处理 PDF 文件', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
    ],
  },
  {
    category: '通信消息',
    skills: [
      { icon: '💬', name: '微信 CLI', description: '微信本地数据查询', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '📧', name: '邮件客户端', description: '收发电子邮件', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🔌', name: 'ClawHub', description: '技能市场与插件管理', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
    ],
  },
  {
    category: '系统与自进化',
    skills: [
      { icon: '🧠', name: '自改进', description: '自动分析与优化自身行为', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🤖', name: '自主代理工具包', description: '自主决策与任务规划', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🔧', name: '技能创建器', description: '根据需求自动创建新技能', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '💾', name: '代理记忆', description: '管理跨会话持久化记忆', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🌐', name: '浏览器', description: '网页浏览与信息采集', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '🌤', name: '天气查询', description: '查询实时天气信息', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
      { icon: '📃', name: '摘要生成', description: '自动生成文本摘要', active: false, current: false, toolsCount: 0, knowledgeCount: 1 },
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
// Reactive state
// ------------------------------------------------------------------

const skillGroups = ref<SkillGroup[]>([...fallbackSkillGroups])
const allSkills = ref<Skill[]>(skillGroups.value.flatMap(g => g.skills))
const evolutionSuggestions = ref<EvolutionSuggestion[]>([...fallbackEvolution])
const evolutionStats = ref<EvolutionStats | null>(null)

// ------------------------------------------------------------------
// Computed: filtered groups by search query
// ------------------------------------------------------------------

const filteredGroups = computed(() => {
  const groups = skillGroups.value
  if (!searchQuery.value) return groups
  const q = searchQuery.value.toLowerCase()
  return groups.map(group => ({
    ...group,
    skills: group.skills.filter(s =>
      s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
    ),
  })).filter(g => g.skills.length > 0)
})

// ------------------------------------------------------------------
// Computed: evolution stats
// ------------------------------------------------------------------

const usageRanking = computed(() => {
  if (!evolutionStats.value) return []
  const usage = evolutionStats.value.skill_usage
  const entries = Object.entries(usage)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
  const maxCount = entries.length > 0 ? entries[0].count : 1
  return entries.map(e => ({ ...e, pct: (e.count / maxCount) * 100 }))
})

const statTotalUsage = computed(() => {
  if (!evolutionStats.value) return 0
  return Object.values(evolutionStats.value.skill_usage).reduce((a, b) => a + b, 0)
})

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
// Backend helpers
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

async function applyEvolution(item: EvolutionSuggestion) {
  if (item.applied) return
  try {
    const resp = await sendRequest('apply_evolution', { title: item.title })
    if (resp.ok) {
      item.applied = true
    }
  } catch {
    // Keep current state
  }
}

async function toggleSkillDetail(name: string) {
  if (expandedSkill.value === name) {
    expandedSkill.value = null
    skillDetail.value = null
    return
  }
  expandedSkill.value = name
  skillDetail.value = null
  activeDetailTab.value = 'desc'
  await fetchSkillDetail(name)
}

async function fetchSkillDetail(name: string) {
  try {
    const resp = await sendRequest('get_skill_detail', { name })
    if (resp.ok) {
      skillDetail.value = resp as unknown as BackendSkillDetail
    }
  } catch {
    // Keep current state
  }
}

async function loadSkill(name: string) {
  try {
    const resp = await sendRequest('switch_skill', { name })
    if (resp.ok) {
      // Update current skill indicator across all groups
      for (const group of skillGroups.value) {
        for (const s of group.skills) {
          s.current = s.name === name
        }
      }
      // Refresh detail
      await fetchSkillDetail(name)
    }
  } catch {
    // Keep current state
  }
}

function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return iso.slice(0, 10)
  }
}

// ------------------------------------------------------------------
// Load data on mount
// ------------------------------------------------------------------

onMounted(async () => {
  // Load skills from backend
  try {
    const resp = await sendRequest('get_skills')
    if (resp.ok && Array.isArray(resp.skills)) {
      const skills = resp.skills as BackendSkill[]
      const currentName = resp.current as string | null
      // Group by category
      const groupMap = new Map<string, Skill[]>()
      for (const s of skills) {
        const cat = s.category || '未分类'
        if (!groupMap.has(cat)) groupMap.set(cat, [])
        const desc = s.tools_count > 0
          ? `${s.tools_count} 工具 · ${s.knowledge_count} 知识文档`
          : '需安装依赖或配置'
        groupMap.get(cat)!.push({
          icon: inferIcon(s.name),
          name: s.name,
          description: desc,
          active: s.loaded && s.tools_count > 0,
          current: s.name === currentName,
          toolsCount: s.tools_count,
          knowledgeCount: s.knowledge_count,
        })
      }
      skillGroups.value = Array.from(groupMap, ([category, skills]) => ({ category, skills }))
      allSkills.value = skills as any
    }
  } catch {
    // Keep fallback data
  }

  // Load evolution suggestions from backend
  try {
    const resp = await sendRequest('get_evolution')
    if (resp.ok) {
      if (resp.stats) {
        evolutionStats.value = resp.stats as EvolutionStats
      }
      if (Array.isArray(resp.suggestions)) {
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

.skill-status { flex-shrink: 0; }

.status-badge {
  display: inline-block;
  padding: 1px 10px;
  border-radius: var(--oaa-radius-full);
  font-size: var(--oaa-text-xs);
  font-weight: 500;
}

.status-active {
  background: rgba(34, 197, 94, 0.15);
  color: var(--oaa-green-500);
}

.status-ready {
  background: rgba(59, 130, 246, 0.15);
  color: var(--oaa-blue-400);
}

.status-inactive {
  background: rgba(148, 163, 184, 0.12);
  color: var(--oaa-color-disabled);
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

/* Search */
.skill-search {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  margin-bottom: var(--oaa-space-4);
}

.search-icon { font-size: 0.9rem; opacity: 0.5; }

.search-input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--oaa-color-primary);
  font-family: inherit;
  font-size: var(--oaa-text-sm);
  outline: none;
}

.search-input::placeholder { color: var(--oaa-color-disabled); }

.search-clear {
  background: none;
  border: none;
  color: var(--oaa-color-disabled);
  cursor: pointer;
  font-size: 0.8rem;
  padding: 2px 6px;
  border-radius: var(--oaa-radius-sm);
}

.search-clear:hover { color: var(--oaa-color-primary); background: var(--oaa-bg-surface-hover); }

/* Skill card expanded state */
.skill-card { cursor: pointer; }

.skill-card.expanded {
  border-color: var(--oaa-primary);
  background: var(--oaa-primary-light);
}

.skill-current-tag {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--oaa-green-500);
  background: rgba(34, 197, 94, 0.15);
  padding: 0 6px;
  border-radius: var(--oaa-radius-sm);
  margin-left: var(--oaa-space-1);
  vertical-align: middle;
}

/* Detail panel */
.skill-detail-panel {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-default);
  border-radius: var(--oaa-radius-lg);
  margin: var(--oaa-space-1) 0 var(--oaa-space-2);
  padding: var(--oaa-space-4);
  animation: fadeIn 0.2s ease;
}

.detail-tabs {
  display: flex;
  gap: var(--oaa-space-1);
  background: var(--oaa-bg-app);
  border-radius: var(--oaa-radius-md);
  padding: 3px;
  margin-bottom: var(--oaa-space-3);
}

.detail-tab-btn {
  flex: 1;
  padding: var(--oaa-space-1) var(--oaa-space-3);
  border: none;
  border-radius: var(--oaa-radius-sm);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-xs);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--oaa-transition-fast), color var(--oaa-transition-fast);
}

.detail-tab-btn.active {
  background: var(--oaa-bg-surface);
  color: var(--oaa-color-primary);
}

.detail-tab-btn:hover { color: var(--oaa-color-primary); }

.detail-content {
  max-height: 320px;
  overflow-y: auto;
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  line-height: 1.55;
}

.detail-desc { margin-bottom: var(--oaa-space-3); }

.detail-section { margin-top: var(--oaa-space-3); }

.detail-section h4 {
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: var(--oaa-space-1);
}

.detail-tool-item {
  display: flex;
  flex-direction: column;
  padding: var(--oaa-space-1) 0;
  border-bottom: 1px solid var(--oaa-border-subtle);
}

.tool-name { font-weight: 600; color: var(--oaa-color-primary); }

.tool-desc { font-size: var(--oaa-text-xs); color: var(--oaa-color-muted); }

.detail-knowledge-item {
  margin: var(--oaa-space-1) 0;
}

.detail-knowledge-item summary {
  cursor: pointer;
  font-weight: 500;
  color: var(--oaa-color-secondary);
  font-size: var(--oaa-text-xs);
}

.knowledge-body {
  margin-top: var(--oaa-space-1);
  padding: var(--oaa-space-2);
  background: var(--oaa-bg-app);
  border-radius: var(--oaa-radius-sm);
  font-size: var(--oaa-text-xs);
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.detail-markdown {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: var(--oaa-text-xs);
  line-height: 1.5;
  color: var(--oaa-color-secondary);
}

.detail-empty { color: var(--oaa-color-disabled); font-style: italic; }

.detail-actions {
  margin-top: var(--oaa-space-3);
  padding-top: var(--oaa-space-3);
  border-top: 1px solid var(--oaa-border-subtle);
  display: flex;
  justify-content: flex-end;
}

.current-skill-badge {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-green-500);
  font-weight: 500;
}

.no-results {
  text-align: center;
  padding: var(--oaa-space-8);
  color: var(--oaa-color-disabled);
  font-size: var(--oaa-text-sm);
}

/* Evolution stats dashboard */
.evo-stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--oaa-space-3);
  margin-bottom: var(--oaa-space-6);
}

.evo-stat-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-4);
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}

.stat-value {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  color: var(--oaa-primary);
}

.stat-label {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
}

/* Usage ranking */
.evo-usage-section { margin-bottom: var(--oaa-space-6); }

.evo-usage-section h3,
.evo-section h3 {
  font-size: var(--oaa-text-sm);
  font-weight: 600;
  color: var(--oaa-color-secondary);
  margin-bottom: var(--oaa-space-2);
}

.usage-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}

.usage-row {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-1) var(--oaa-space-2);
  background: var(--oaa-bg-surface);
  border-radius: var(--oaa-radius-sm);
  font-size: var(--oaa-text-xs);
}

.usage-rank {
  width: 20px;
  font-weight: 600;
  color: var(--oaa-color-muted);
  text-align: center;
}

.usage-name {
  width: 100px;
  font-weight: 500;
  color: var(--oaa-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.usage-bar-bg {
  flex: 1;
  height: 6px;
  background: var(--oaa-bg-app);
  border-radius: var(--oaa-radius-full);
  overflow: hidden;
}

.usage-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--oaa-primary), var(--oaa-blue-400));
  border-radius: var(--oaa-radius-full);
  transition: width 0.4s ease;
}

.usage-count {
  width: 40px;
  text-align: right;
  color: var(--oaa-color-muted);
  font-variant-numeric: tabular-nums;
}

/* Crystallized */
.crystallized-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--oaa-space-2);
  margin-bottom: var(--oaa-space-6);
}

.crystallized-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-1);
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
  padding: var(--oaa-space-1) var(--oaa-space-3);
  font-size: var(--oaa-text-xs);
}

.cryst-icon { font-size: 0.85rem; }
.cryst-name { font-weight: 500; color: var(--oaa-color-primary); }
.cryst-date { color: var(--oaa-color-disabled); }
</style>

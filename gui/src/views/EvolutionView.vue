<template>
  <div class="view-container">
    <div class="view-header">
      <h2>进化工厂</h2>
      <p class="view-subtitle">自我改进提案管理、执行与回滚</p>
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
        <span v-if="tab.id === 'pending' && pendingCount > 0" class="tab-badge">{{ pendingCount }}</span>
      </button>
    </div>

    <!-- ================================ -->
    <!-- 待处理提案 -->
    <!-- ================================ -->
    <div v-if="activeTab === 'pending'" key="pending" class="tab-content">
      <div v-if="loading" class="loading-row">
        <span class="loading-spinner"></span>
        <span>加载提案列表...</span>
      </div>

      <div v-else-if="pendingProposals.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
            <polyline points="22 4 12 14.01 9 11.01"/>
          </svg>
        </div>
        <p class="empty-text">暂无待处理提案</p>
        <p class="empty-hint">空闲巡检会自动生成改进提案，届时将显示在此处</p>
      </div>

      <div v-else class="proposal-list">
        <div v-for="prop in pendingProposals" :key="prop.id" class="oaa-card proposal-card">
          <div class="proposal-header">
            <span :class="['oaa-badge', typeBadgeClass(prop.type)]">{{ typeLabel(prop.type) }}</span>
            <span class="proposal-title">{{ prop.title }}</span>
            <span class="proposal-id">{{ prop.id }}</span>
          </div>

          <div class="proposal-body">
            <div v-if="prop.problem" class="proposal-field">
              <span class="field-label">问题</span>
              <p class="field-value">{{ prop.problem }}</p>
            </div>
            <div v-if="prop.benefit" class="proposal-field">
              <span class="field-label">收益</span>
              <p class="field-value benefit">{{ prop.benefit }}</p>
            </div>
            <div class="proposal-field">
              <span class="field-label">操作步骤</span>
              <ol class="action-list">
                <li v-for="(action, i) in prop.actions" :key="i" class="action-item">
                  <code class="action-tool">{{ action.tool }}</code>
                  <span v-if="action.description" class="action-desc">{{ action.description }}</span>
                  <div v-if="action.verify" class="action-verify">验证: {{ action.verify.description || action.verify.tool }}</div>
                </li>
              </ol>
            </div>
          </div>

          <div class="proposal-footer">
            <div class="footer-actions">
              <button class="oaa-btn oaa-btn--primary oaa-btn--sm" @click="approveProposal(prop.id)" :disabled="executing === prop.id">
                {{ executing === prop.id ? '执行中...' : '批准执行' }}
              </button>
              <button class="oaa-btn oaa-btn--secondary oaa-btn--sm" @click="ignoreProposal(prop.id, false)" :disabled="executing === prop.id">
                忽略本次
              </button>
              <button class="oaa-btn oaa-btn--ghost oaa-btn--sm ignore-forever" @click="ignoreProposal(prop.id, true)" :disabled="executing === prop.id">
                彻底忽略
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================ -->
    <!-- 执行历史 -->
    <!-- ================================ -->
    <div v-if="activeTab === 'history'" key="history" class="tab-content">
      <div v-if="loading" class="loading-row">
        <span class="loading-spinner"></span>
        <span>加载执行历史...</span>
      </div>

      <div v-else-if="historyProposals.length === 0" class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
        </div>
        <p class="empty-text">暂无执行历史</p>
        <p class="empty-hint">批准的提案执行后将在此处记录结果</p>
      </div>

      <div v-else class="proposal-list">
        <div v-for="prop in historyProposals" :key="prop.id" class="oaa-card history-card">
          <div class="history-header">
            <span :class="['oaa-badge', statusBadgeClass(prop.status)]">{{ statusLabel(prop.status) }}</span>
            <span class="history-title">{{ prop.title }}</span>
            <span class="history-date">{{ formatDate(prop.executed_at || prop.created_at) }}</span>
          </div>

          <div v-if="prop.result" class="history-result">
            <details>
              <summary class="result-summary">查看执行结果</summary>
              <pre class="result-json">{{ formatResult(prop.result) }}</pre>
            </details>
          </div>
          <div v-if="prop.error" class="history-error">
            <span class="error-label">错误:</span>
            <code>{{ prop.error }}</code>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================ -->
    <!-- 统计 -->
    <!-- ================================ -->
    <div v-if="activeTab === 'stats'" key="stats" class="tab-content">
      <div v-if="statsLoading" class="loading-row">
        <span class="loading-spinner"></span>
        <span>加载统计数据...</span>
      </div>

      <template v-else-if="statsData">
        <!-- Stats cards -->
        <div class="stats-cards">
          <div v-for="card in statsCards" :key="card.label" class="stat-card" :style="{ borderTopColor: card.color }">
            <span class="stat-value" :style="{ color: card.color }">{{ card.value }}</span>
            <span class="stat-label">{{ card.label }}</span>
          </div>
        </div>

        <!-- Charts row -->
        <div class="charts-row">
          <!-- Donut chart: type distribution -->
          <div class="chart-card">
            <h3 class="chart-title">提案类型分布</h3>
            <div v-if="donutSegments.length === 0" class="chart-empty">暂无数据</div>
            <div v-else class="donut-wrapper">
              <svg width="200" height="200" viewBox="0 0 200 200">
                <circle cx="100" cy="100" r="60" fill="none" stroke="var(--oaa-bg-input)" stroke-width="28"/>
                <circle v-for="seg in donutSegments" :key="seg.type"
                  cx="100" cy="100" r="60" fill="none"
                  :stroke="seg.color" stroke-width="28"
                  :stroke-dasharray="`${seg.dash} ${seg.gap}`"
                  :stroke-dashoffset="seg.offset"
                  transform="rotate(-90 100 100)"
                  style="transition: stroke-dasharray 0.5s;"
                />
              </svg>
              <div class="donut-legend">
                <div v-for="seg in donutSegments" :key="seg.type" class="legend-item">
                  <span class="legend-dot" :style="{ background: seg.color }"></span>
                  <span class="legend-label">{{ seg.label }}</span>
                  <span class="legend-count">{{ seg.count }}</span>
                  <span class="legend-pct">{{ (seg.pct * 100).toFixed(0) }}%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Bar chart: daily trend -->
          <div class="chart-card">
            <h3 class="chart-title">执行趋势</h3>
            <div v-if="barData.length === 0" class="chart-empty">暂无执行记录</div>
            <div v-else class="bar-wrapper">
              <div class="bar-chart">
                <svg :width="barData.length * 40 + 20" height="160" :viewBox="`0 0 ${barData.length * 40 + 20} 160`">
                  <g v-for="(b, i) in barData" :key="b.date">
                    <rect :x="i * 40 + 10" :y="148 - b.h" :width="12" :height="b.h" fill="var(--oaa-green-400)" rx="2" opacity="0.85" />
                    <rect v-if="b.fail > 0" :x="i * 40 + 10" :y="148 - (b.fail / barMaxTotal) * 120" :width="12" :height="(b.fail / barMaxTotal) * 120" fill="var(--oaa-red-400)" rx="2" opacity="0.85" />
                    <text :x="i * 40 + 16" y="156" text-anchor="middle" font-size="9" fill="var(--oaa-color-disabled)">{{ b.date }}</text>
                  </g>
                </svg>
              </div>
              <div class="bar-legend">
                <span class="bar-legend-item"><span class="bar-dot" style="background:var(--oaa-green-400)"></span>成功</span>
                <span class="bar-legend-item"><span class="bar-dot" style="background:var(--oaa-red-400)"></span>失败</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Proactivity metrics -->
        <div v-if="metricsData" class="metrics-section">
          <h3 class="section-title">主动性度量</h3>
          <div class="stats-cards">
            <div class="stat-card" style="border-top-color: var(--oaa-blue-400)">
              <span class="stat-value" style="color: var(--oaa-blue-400)">{{ metricsData.tool_metrics?.total_tool_calls ?? 0 }}</span>
              <span class="stat-label">工具调用总次数</span>
            </div>
            <div class="stat-card" style="border-top-color: var(--oaa-green-400)">
              <span class="stat-value" style="color: var(--oaa-green-400)">{{ ((metricsData.proactivity_ratio ?? 1) * 100).toFixed(0) }}%</span>
              <span class="stat-label">主动性比率</span>
            </div>
            <div class="stat-card" style="border-top-color: var(--oaa-yellow-400)">
              <span class="stat-value" style="color: var(--oaa-yellow-400)">{{ metricsData.tool_metrics?.active_repairs ?? 0 }}</span>
              <span class="stat-label">主动修复次数</span>
            </div>
            <div class="stat-card" style="border-top-color: var(--oaa-purple-400)">
              <span class="stat-value" style="color: var(--oaa-purple-400)">{{ metricsData.llm_metrics?.total_calls ?? 0 }}</span>
              <span class="stat-label">LLM 调用次数</span>
            </div>
          </div>
          <div class="charts-row">
            <div class="chart-card">
              <h3 class="chart-title">决策分布</h3>
              <div v-if="decisionBreakdown.length === 0" class="chart-empty">暂无数据</div>
              <div v-else class="bar-wrapper">
                <div class="bar-chart" style="height:120px">
                  <svg :width="decisionBreakdown.length * 80 + 20" height="120" :viewBox="`0 0 ${decisionBreakdown.length * 80 + 20} 120`">
                    <g v-for="(d, i) in decisionBreakdown" :key="d.label">
                      <rect :x="i * 80 + 20" :y="119 - d.h" :width="24" :height="d.h" :fill="d.color" rx="2" opacity="0.85" />
                      <text :x="i * 80 + 32" y="116" text-anchor="middle" font-size="10" fill="var(--oaa-color-disabled)">{{ d.label }}</text>
                      <text :x="i * 80 + 32" :y="119 - d.h - 4" text-anchor="middle" font-size="10" :fill="d.color">{{ d.value }}</text>
                    </g>
                  </svg>
                </div>
              </div>
            </div>
            <div class="chart-card">
              <h3 class="chart-title">工具成功/失败率</h3>
              <div v-if="toolBreakdown.length === 0" class="chart-empty">暂无数据</div>
              <div v-else class="ranking-list">
                <div v-for="(item, i) in toolBreakdown" :key="item.name" class="ranking-item">
                  <span class="ranking-idx">{{ i + 1 }}</span>
                  <span class="ranking-name">{{ item.name }}</span>
                  <span class="ranking-bar-bg">
                    <span class="ranking-bar-fill" :style="{ width: item.pct + '%', background: item.fail > 0 ? 'var(--oaa-yellow-400)' : 'var(--oaa-green-400)' }"></span>
                  </span>
                  <span class="ranking-count">{{ item.ok }}/{{ item.total }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-if="metricsData.llm_metrics?.by_model" class="info-card" style="margin-top: 0">
            <h3 class="chart-title">LLM 模型统计</h3>
            <div class="crystal-list">
              <div v-for="(count, model) in metricsData.llm_metrics.by_model" :key="model" class="crystal-item">
                <span class="crystal-icon">&#129302;</span>
                <span class="crystal-name" style="font-family: monospace; font-size: 12px">{{ model }}</span>
                <span class="crystal-date">{{ count }} 次 | avg {{ metricsData.llm_metrics.avg_duration_ms }}ms</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Bottom info section -->
        <div class="bottom-info-row">
          <div class="info-card">
            <h3 class="chart-title">技能使用排行</h3>
            <div v-if="skillRanking.length === 0" class="chart-empty">暂无数据</div>
            <div v-else class="ranking-list">
              <div v-for="(item, i) in skillRanking" :key="item.name" class="ranking-item">
                <span class="ranking-idx" :class="{ gold: i === 0, silver: i === 1, bronze: i === 2 }">{{ i + 1 }}</span>
                <span class="ranking-name">{{ item.name }}</span>
                <span class="ranking-bar-bg">
                  <span class="ranking-bar-fill" :style="{ width: (item.count / skillRanking[0].count * 100) + '%' }"></span>
                </span>
                <span class="ranking-count">{{ item.count }} 次</span>
              </div>
            </div>
          </div>
          <div class="info-card">
            <h3 class="chart-title">已固化技能</h3>
            <div v-if="crystallizedList.length === 0" class="chart-empty">暂无固化技能</div>
            <div v-else class="crystal-list">
              <div v-for="c in crystallizedList" :key="c.name" class="crystal-item">
                <span class="crystal-icon">&#10024;</span>
                <span class="crystal-name">{{ c.name }}</span>
                <span class="crystal-date">{{ c.created ? c.created.slice(0, 10) : '' }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div v-else class="empty-state">
        <div class="empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
            <path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>
          </svg>
        </div>
        <p class="empty-text">暂无统计数据</p>
        <p class="empty-hint">执行进化工厂操作后将在此处展示统计信息</p>
      </div>
    </div>

    <!-- Toast notification -->
    <div v-if="toast.show" :class="['toast', toast.type]">
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { sendRequest, proposalCompleted } = useWebSocket()

const activeTab = ref('pending')
const loading = ref(true)
const executing = ref('')
const proposals = ref<any[]>([])
const toast = ref({ show: false, type: 'success', message: '' })

const tabs = [
  { id: 'pending', icon: '📋', label: '待处理提案' },
  { id: 'history', icon: '📜', label: '执行历史' },
  { id: 'stats', icon: '📊', label: '统计' },
]

const pendingProposals = computed(() =>
  proposals.value.filter(p => p.status === 'pending')
)

const historyProposals = computed(() =>
  proposals.value.filter(p => p.status !== 'pending')
)

const pendingCount = computed(() => pendingProposals.value.length)

// ---- Statistics tab ----
const statsLoading = ref(true)
const statsData = ref<any>(null)

// --- Proactivity metrics ---
const metricsData = ref<any>(null)
const decisionBreakdown = computed(() => {
  const m = metricsData.value?.tool_metrics
  if (!m) return []
  const auto = m.auto || 0; const confirmed = m.confirmed || 0; const denied = m.denied || 0
  const max = Math.max(auto, confirmed, denied, 1)
  return [
    { label: '自动', value: auto, h: (auto / max) * 100, color: 'var(--oaa-green-400)' },
    { label: '确认', value: confirmed, h: (confirmed / max) * 100, color: 'var(--oaa-blue-400)' },
    { label: '拒绝', value: denied, h: (denied / max) * 100, color: 'var(--oaa-red-400)' },
  ]
})
const toolBreakdown = computed(() => {
  const b = metricsData.value?.tool_metrics?.breakdown
  if (!b) return []
  return Object.entries(b)
    .map(([name, s]: [string, any]) => ({
      name,
      ok: s.success || 0,
      fail: s.failure || 0,
      total: (s.success || 0) + (s.failure || 0),
      pct: (s.success || 0) + (s.failure || 0) > 0
        ? ((s.success || 0) / ((s.success || 0) + (s.failure || 0)) * 100) : 0,
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 8)
})

async function loadMetrics() {
  try {
    const resp = await sendRequest('get_metrics')
    if (resp?.ok) {
      metricsData.value = resp
    }
  } catch { /* ignore - metrics may not be available */ }
}

const typeColors: Record<string, string> = {
  tool_fix: '#ef4444',
  install_dep: '#f59e0b',
  sop_optimize: '#a78bfa',
  skill_crystallize: '#22c55e',
  config_change: '#60a5fa',
}
const typeLabelsAll: Record<string, string> = {
  tool_fix: '工具修复',
  install_dep: '安装依赖',
  sop_optimize: 'SOP 优化',
  skill_crystallize: '技能固化',
  config_change: '配置变更',
}

const statsCards = computed(() => {
  const s = statsData.value?.proposal_summary
  if (!s) return []
  return [
    { label: '总提案数', value: s.total, color: 'var(--oaa-blue-400)' },
    { label: '成功率', value: s.success_rate + '%', color: s.success_rate >= 80 ? 'var(--oaa-green-400)' : 'var(--oaa-amber-400)' },
    { label: '待处理', value: s.pending, color: s.pending > 0 ? 'var(--oaa-amber-400)' : 'var(--oaa-color-muted)' },
    { label: '回滚次数', value: s.rolled_back, color: s.rolled_back > 0 ? 'var(--oaa-red-400)' : 'var(--oaa-color-muted)' },
  ]
})

// Donut chart
const donutSegments = computed(() => {
  const dist = statsData.value?.type_distribution || {}
  const entries = Object.entries(dist) as [string, number][]
  const total = entries.reduce((s, [, v]) => s + v, 0)
  if (total === 0) return []
  const r = 60, circ = 2 * Math.PI * r
  let offset = 0
  return entries.map(([type, count]) => {
    const pct = count / total
    const dash = pct * circ
    const gap = circ - dash
    const seg = { type, count, pct, label: typeLabelsAll[type] || type, color: typeColors[type] || '#888', dash, gap, offset: -offset }
    offset += dash
    return seg
  })
})

// Bar chart
const barData = computed(() => {
  const trend = statsData.value?.daily_trend || []
  if (trend.length === 0) return []
  const maxVal = Math.max(...trend.map((d: any) => d.total), 1)
  return trend.map((d: any) => ({
    date: d.date,
    total: d.total,
    success: d.success,
    fail: d.fail,
    h: Math.max((d.total / maxVal) * 120, 4),
  }))
})
const barMaxTotal = computed(() => {
  const trend = statsData.value?.daily_trend || []
  return Math.max(...trend.map((d: any) => d.total), 1)
})

// skill ranking
const skillRanking = computed(() => statsData.value?.evolution?.skill_ranking || [])
const crystallizedList = computed(() => statsData.value?.evolution?.crystallized || [])

async function loadStats() {
  statsLoading.value = true
  try {
    const resp = await sendRequest('get_evolution_stats')
    if (resp.ok) {
      statsData.value = resp
    }
  } catch (_e) { /* ignore */ }
  statsLoading.value = false
}

// Tab change -> lazy load stats
// Auto-refresh proposal list when background task completes
watch(proposalCompleted, () => { loadProposals(); loadStats(); loadMetrics() })

watch(activeTab, (tab) => {
  if (tab === 'stats') {
    if (!statsData.value) loadStats()
    loadMetrics()
  }
})

const typeLabels: Record<string, string> = {
  tool_fix: '工具修复',
  install_dep: '安装依赖',
  sop_optimize: 'SOP 优化',
  skill_crystallize: '技能固化',
  config_change: '配置变更',
}

function typeLabel(type: string): string {
  return typeLabels[type] || type
}

function typeBadgeClass(type: string): string {
  const map: Record<string, string> = {
    tool_fix: 'oaa-badge--error',
    install_dep: 'oaa-badge--warning',
    sop_optimize: 'oaa-badge--accent',
    skill_crystallize: 'oaa-badge--success',
    config_change: 'oaa-badge--count',
  }
  return map[type] || 'oaa-badge--count'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    done: '已完成',
    failed: '失败',
    pending: '待处理',
    running: '执行中',
    approved: '已批准',
    ignored_once: '已忽略',
    ignored_forever: '永久忽略',
    rolled_back: '已回滚',
  }
  return map[status] || status
}

function statusBadgeClass(status: string): string {
  const map: Record<string, string> = {
    done: 'oaa-badge--success',
    failed: 'oaa-badge--error',
    running: 'oaa-badge--warning',
    ignored_once: 'oaa-badge--count',
    ignored_forever: 'oaa-badge--count',
    rolled_back: 'oaa-badge--warning',
  }
  return map[status] || 'oaa-badge--count'
}

function formatDate(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatResult(resultStr: string): string {
  if (!resultStr) return '(空)'
  try {
    const parsed = JSON.parse(resultStr)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return resultStr
  }
}

function showToast(message: string, type: 'success' | 'error' = 'success') {
  toast.value = { show: true, type, message }
  setTimeout(() => { toast.value.show = false }, 3000)
}

async function loadProposals() {
  loading.value = true
  try {
    const resp = await sendRequest('list_proposals')
    if (resp.ok) {
      proposals.value = (resp.proposals || []).sort((a: any, b: any) => (b.created_at || 0) - (a.created_at || 0))
    } else {
      showToast(resp.error || '加载失败', 'error')
    }
  } catch (e: any) {
    showToast('加载提案失败: ' + (e.message || e), 'error')
  }
  loading.value = false
}

async function approveProposal(id: string) {
  executing.value = id
  try {
    const resp = await sendRequest('proposal_approve', { id })
    if (resp.ok) {
      showToast(`提案 ${id.slice(0, 20)} 执行完毕 (${resp.proposal_status})`)
      await loadProposals()
    } else {
      showToast(resp.error || '执行失败', 'error')
      await loadProposals()
    }
  } catch (e: any) {
    showToast('执行出错: ' + (e.message || e), 'error')
  }
  executing.value = ''
}

async function ignoreProposal(id: string, permanent: boolean) {
  try {
    const resp = await sendRequest('proposal_ignore', { id, permanent })
    if (resp.ok) {
      showToast(permanent ? '已彻底忽略' : '已忽略本次')
      await loadProposals()
    } else {
      showToast(resp.error || '忽略失败', 'error')
    }
  } catch (e: any) {
    showToast('忽略出错: ' + (e.message || e), 'error')
  }
}

onMounted(() => {
  loadProposals()
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

/* Tab bar */
.tab-bar {
  display: flex;
  gap: var(--oaa-space-1);
  margin-bottom: var(--oaa-space-6);
  border-bottom: 1px solid var(--oaa-border-subtle);
  padding-bottom: var(--oaa-space-2);
}
.tab-btn {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-2) var(--oaa-space-4);
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-family: inherit;
  font-size: var(--oaa-text-sm);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--oaa-transition-fast);
  position: relative;
}
.tab-btn:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--oaa-color-primary);
}
.tab-btn.active {
  background: var(--oaa-primary-light);
  color: var(--oaa-primary);
}
.tab-icon {
  font-size: var(--oaa-text-base);
}
.tab-badge {
  background: var(--oaa-primary);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: var(--oaa-radius-full);
  min-width: 18px;
  text-align: center;
}

/* Loading */
.loading-row {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-10) 0;
  justify-content: center;
  color: var(--oaa-color-muted);
  font-size: var(--oaa-text-sm);
}
.loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--oaa-border-default);
  border-top-color: var(--oaa-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--oaa-space-12) 0;
  color: var(--oaa-color-muted);
}
.empty-text {
  font-size: var(--oaa-text-lg);
  margin-top: var(--oaa-space-4);
  color: var(--oaa-color-secondary);
}
.empty-hint {
  font-size: var(--oaa-text-sm);
  margin-top: var(--oaa-space-2);
}

/* Proposal cards */
.proposal-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-4);
}

.proposal-card {
  overflow: hidden;
}
.proposal-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-4) var(--oaa-space-5);
  padding-bottom: 0;
  flex-wrap: wrap;
}
.proposal-title {
  font-size: var(--oaa-text-base);
  font-weight: 600;
  color: var(--oaa-color-primary);
  flex: 1;
}
.proposal-id {
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
}

.proposal-body {
  padding: var(--oaa-space-4) var(--oaa-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-3);
}
.proposal-field {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}
.field-label {
  font-size: var(--oaa-text-xs);
  font-weight: 600;
  color: var(--oaa-color-disabled);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.field-value {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  line-height: 1.5;
}
.field-value.benefit {
  color: var(--oaa-green-400);
}

.action-list {
  margin: 0;
  padding-left: var(--oaa-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
}
.action-item {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-secondary);
  line-height: 1.4;
}
.action-tool {
  background: var(--oaa-bg-input);
  color: var(--oaa-primary);
  padding: 1px 6px;
  border-radius: var(--oaa-radius-sm);
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
}
.action-desc {
  margin-left: var(--oaa-space-2);
}
.action-verify {
  margin-top: 2px;
  font-size: var(--oaa-text-xs);
  color: var(--oaa-green-400);
  opacity: 0.7;
}

.proposal-footer {
  padding: var(--oaa-space-3) var(--oaa-space-5);
  border-top: 1px solid var(--oaa-border-subtle);
}
.footer-actions {
  display: flex;
  gap: var(--oaa-space-2);
}
.ignore-forever {
  margin-left: auto;
  color: var(--oaa-color-disabled);
}
.ignore-forever:hover:not(:disabled) {
  color: var(--oaa-red-400);
  background: rgba(239, 68, 68, 0.1);
}

/* History cards */
.history-card {
  overflow: hidden;
}
.history-header {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-3) var(--oaa-space-5);
}
.history-title {
  font-size: var(--oaa-text-base);
  font-weight: 500;
  color: var(--oaa-color-primary);
  flex: 1;
}
.history-date {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
  font-family: var(--oaa-font-mono);
}
.history-result {
  padding: 0 var(--oaa-space-5) var(--oaa-space-3);
}
.result-summary {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-muted);
  cursor: pointer;
  user-select: none;
  padding: var(--oaa-space-1) 0;
}
.result-summary:hover {
  color: var(--oaa-color-secondary);
}
.result-json {
  margin-top: var(--oaa-space-2);
  background: var(--oaa-bg-input);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-md);
  padding: var(--oaa-space-3);
  font-family: var(--oaa-font-mono);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-secondary);
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.history-error {
  padding: 0 var(--oaa-space-5) var(--oaa-space-3);
  display: flex;
  align-items: baseline;
  gap: var(--oaa-space-2);
}
.error-label {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-red-400);
  font-weight: 600;
}
.history-error code {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-red-400);
  font-family: var(--oaa-font-mono);
  background: rgba(239, 68, 68, 0.1);
  padding: 1px 6px;
  border-radius: var(--oaa-radius-sm);
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
  z-index: 1000;
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

/* ============================== */
/* Statistics tab styles */
/* ============================== */
.stats-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--oaa-space-4);
  margin-bottom: var(--oaa-space-6);
}
.stat-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  border-top: 3px solid;
  padding: var(--oaa-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-1);
}
.stat-value {
  font-size: var(--oaa-text-2xl);
  font-weight: 700;
  line-height: 1;
}
.stat-label {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  font-weight: 500;
}

.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--oaa-space-4);
  margin-bottom: var(--oaa-space-4);
}
.chart-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-4);
}
.chart-title {
  font-size: var(--oaa-text-sm);
  font-weight: 600;
  color: var(--oaa-color-primary);
  margin: 0 0 var(--oaa-space-3) 0;
}
.chart-empty {
  font-size: var(--oaa-text-sm);
  color: var(--oaa-color-disabled);
  padding: var(--oaa-space-6) 0;
  text-align: center;
}

/* Donut chart */
.donut-wrapper {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-4);
}
.donut-legend {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
  flex: 1;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  font-size: var(--oaa-text-xs);
}
.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.legend-label {
  color: var(--oaa-color-primary);
  flex: 1;
}
.legend-count {
  color: var(--oaa-color-secondary);
  font-family: var(--oaa-font-mono);
}
.legend-pct {
  color: var(--oaa-color-muted);
  min-width: 32px;
  text-align: right;
}

/* Bar chart */
.bar-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.bar-chart {
  overflow-x: auto;
  width: 100%;
}
.bar-chart svg {
  display: block;
}
.bar-legend {
  display: flex;
  gap: var(--oaa-space-4);
  margin-top: var(--oaa-space-2);
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-secondary);
}
.bar-legend-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-1);
}
.bar-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

/* Bottom info section */
.bottom-info-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--oaa-space-4);
}
.info-card {
  background: var(--oaa-bg-surface);
  border: 1px solid var(--oaa-border-subtle);
  border-radius: var(--oaa-radius-lg);
  padding: var(--oaa-space-4);
}

/* Skill usage ranking */
.ranking-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
}
.ranking-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  font-size: var(--oaa-text-xs);
}
.ranking-idx {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--oaa-bg-input);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 10px;
  color: var(--oaa-color-muted);
  flex-shrink: 0;
}
.ranking-idx.gold { background: #fbbf24; color: #78350f; }
.ranking-idx.silver { background: #cbd5e1; color: #334155; }
.ranking-idx.bronze { background: #fb923c; color: #7c2d12; }
.ranking-name {
  color: var(--oaa-color-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ranking-bar-bg {
  flex: 1;
  height: 6px;
  background: var(--oaa-bg-input);
  border-radius: 3px;
  overflow: hidden;
}
.ranking-bar-fill {
  display: block;
  height: 100%;
  background: var(--oaa-primary);
  border-radius: 3px;
}
.ranking-count {
  color: var(--oaa-color-muted);
  min-width: 40px;
  text-align: right;
  font-family: var(--oaa-font-mono);
}

/* Crystallized skills */
.crystal-list {
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-2);
}
.crystal-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-2);
  font-size: var(--oaa-text-sm);
}
.crystal-icon {
  font-size: var(--oaa-text-base);
  flex-shrink: 0;
}
.crystal-name {
  color: var(--oaa-color-primary);
  flex: 1;
}
.crystal-date {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  font-family: var(--oaa-font-mono);
}

/* Proactivity metrics */
.metrics-section {
  margin-bottom: var(--oaa-space-6);
}
.section-title {
  font-size: var(--oaa-text-lg);
  font-weight: 600;
  margin-bottom: var(--oaa-space-3);
  color: var(--oaa-color-primary);
}
</style>

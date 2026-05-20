<template>
  <nav class="sidebar">
    <div class="sidebar-header">
      <span class="sidebar-logo">OAA</span>
      <span class="sidebar-version">v0.1</span>
    </div>

    <div class="sidebar-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        :class="['nav-item', { active: activeTab === item.id }]"
        @click="$emit('navigate', item.id)"
        :title="item.label"
      >
        <span class="nav-icon" v-html="item.icon"></span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </div>

    <div class="sidebar-footer">
      <!-- Agent status -->
      <div class="agent-status">
        <div :class="['phase-ring', phase]">
          <span :class="['status-core', phase]"></span>
        </div>
        <div class="agent-info">
          <span class="agent-name">二愣</span>
          <span :class="['agent-phase', phase]">{{ phaseLabel }}</span>
        </div>
      </div>

      <!-- Channel dots -->
      <div class="channel-mini-dots">
        <span
          v-for="(ch, name) in channelList"
          :key="name"
          :class="['ch-mini-dot', ch.online ? 'online' : 'offline']"
          :title="`${ch.label}: ${ch.online ? '在线' : '离线'}`"
        ></span>
      </div>

      <!-- Uptime -->
      <div class="uptime-row">
        <span class="uptime-label">运行</span>
        <span class="uptime-value">{{ formatUptime() }}</span>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'
import { useAgentStatus } from '../composables/useAgentStatus'

defineProps<{ activeTab: string }>()
defineEmits<{ navigate: [tab: string] }>()

const { sendRequest } = useWebSocket()
const { phase, phaseLabel, channels, formatUptime, connected } = useAgentStatus(sendRequest)

const channelList = computed(() => {
  const labels: Record<string, string> = { wechat: '微信', dingtalk: '钉钉', feishu: '飞书' }
  return Object.entries(channels.value).map(([name, ch]) => ({
    name,
    online: ch.online,
    label: labels[name] || name,
  }))
})

const navItems = [
  {
    id: 'chat',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>`,
    label: '对话',
  },
  {
    id: 'skills',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
    </svg>`,
    label: '技能',
  },
  {
    id: 'connections',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>`,
    label: '连接',
  },
  {
    id: 'tasks',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>
    </svg>`,
    label: '任务',
  },
  {
    id: 'evolution',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>
      <polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>
      <line x1="4" y1="4" x2="9" y2="9"/>
    </svg>`,
    label: '进化工厂',
  },
  {
    id: 'files',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>`,
    label: '文件',
  },
  {
    id: 'settings',
    icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>`,
    label: '设置',
  },
]
</script>

<style scoped>
.sidebar {
  width: var(--oaa-sidebar-width);
  background: rgba(8, 12, 22, 0.85);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  display: flex;
  flex-direction: column;
  user-select: none;
  border-right: 1px solid var(--oaa-glass-border);
  flex-shrink: 0;
  position: relative;
  z-index: 1;
}

/* Subtle top glow on sidebar */
.sidebar::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.3), transparent);
}

.sidebar-header {
  display: flex;
  align-items: baseline;
  gap: var(--oaa-space-2);
  padding: var(--oaa-space-6) var(--oaa-space-5) var(--oaa-space-5);
  border-bottom: 1px solid var(--oaa-glass-border);
}

.sidebar-logo {
  font-size: var(--oaa-text-xl);
  font-weight: 700;
  color: var(--oaa-primary);
  letter-spacing: 2px;
  background: linear-gradient(135deg, var(--oaa-blue-400), var(--oaa-blue-600));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.sidebar-version {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
}

.sidebar-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: var(--oaa-space-2);
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
  padding: var(--oaa-space-2) var(--oaa-space-3);
  border: none;
  border-radius: var(--oaa-radius-md);
  background: transparent;
  color: var(--oaa-color-secondary);
  font-family: inherit;
  font-size: var(--oaa-text-sm);
  cursor: pointer;
  text-align: left;
  transition:
    background var(--oaa-transition-fast),
    color var(--oaa-transition-fast),
    transform var(--oaa-transition-fast);
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--oaa-color-primary);
  transform: translateX(2px);
}

.nav-item.active {
  background: var(--oaa-primary-light);
  color: var(--oaa-primary);
  box-shadow: inset 3px 0 0 var(--oaa-primary);
}

.nav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  transition: transform var(--oaa-transition-fast);
}

.nav-item:hover .nav-icon {
  transform: scale(1.1);
}

.nav-label {
  font-weight: 500;
}

.sidebar-footer {
  padding: var(--oaa-space-4) var(--oaa-space-5);
  border-top: 1px solid var(--oaa-border-subtle);
  display: flex;
  flex-direction: column;
  gap: var(--oaa-space-3);
}

/* Agent status row */
.agent-status {
  display: flex;
  align-items: center;
  gap: var(--oaa-space-3);
}

/* Animated ring around the core dot */
.phase-ring {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid var(--oaa-border-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
  flex-shrink: 0;
}

.phase-ring.idle {
  border-color: rgba(34, 197, 94, 0.3);
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.15);
}

.phase-ring.thinking {
  border-color: rgba(59, 130, 246, 0.5);
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.25);
  animation: ringPulse 1.2s ease-in-out infinite;
}

.phase-ring.executing {
  border-color: rgba(234, 179, 8, 0.5);
  box-shadow: 0 0 12px rgba(234, 179, 8, 0.25);
  animation: ringSpin 2s linear infinite;
}

.phase-ring.responding {
  border-color: rgba(139, 92, 246, 0.5);
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.25);
  animation: ringPulse 0.8s ease-in-out infinite;
}

@keyframes ringPulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.08); opacity: 0.8; }
}

@keyframes ringSpin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Core dot inside the ring */
.status-core {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--oaa-success);
  transition: background 0.3s ease, box-shadow 0.3s ease;
}

.status-core.idle {
  background: var(--oaa-green-500);
  box-shadow: 0 0 6px var(--oaa-green-500);
}

.status-core.thinking {
  background: var(--oaa-blue-500);
  box-shadow: 0 0 8px var(--oaa-blue-500);
  animation: corePulse 0.6s ease-in-out infinite;
}

.status-core.executing {
  background: var(--oaa-amber-500);
  box-shadow: 0 0 8px var(--oaa-amber-500);
}

.status-core.responding {
  background: var(--oaa-purple-400);
  box-shadow: 0 0 8px var(--oaa-purple-400);
}

@keyframes corePulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.4); }
}

.agent-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.agent-name {
  font-size: var(--oaa-text-sm);
  font-weight: 600;
  color: var(--oaa-color-primary);
}

.agent-phase {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  transition: color 0.3s ease;
}

.agent-phase.thinking { color: var(--oaa-blue-400); }
.agent-phase.executing { color: var(--oaa-amber-500); }
.agent-phase.responding { color: var(--oaa-purple-400); }

/* Channel mini dots */
.channel-mini-dots {
  display: flex;
  gap: 6px;
  padding: 0 2px;
}

.ch-mini-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  transition: background 0.3s ease, box-shadow 0.3s ease;
}

.ch-mini-dot.online {
  background: var(--oaa-green-500);
  box-shadow: 0 0 4px rgba(34, 197, 94, 0.4);
}

.ch-mini-dot.offline {
  background: var(--oaa-color-disabled);
}

/* Uptime */
.uptime-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.uptime-label {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-disabled);
}

.uptime-value {
  font-size: var(--oaa-text-xs);
  color: var(--oaa-color-muted);
  font-family: var(--oaa-font-mono);
  font-variant-numeric: tabular-nums;
}
</style>

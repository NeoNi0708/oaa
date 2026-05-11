import { ref, onMounted, onUnmounted } from 'vue'
import type { MgmtResponse } from './useWebSocket'

export type AgentPhase = 'idle' | 'thinking' | 'executing' | 'responding'

export interface ChannelStatus {
  online: boolean
}

export interface AgentStatus {
  agent_state: AgentPhase
  agent_state_since: number
  channels: Record<string, ChannelStatus>
  chat_count: number
  uptime_sec: number
  timestamp: string
}

type SendRequestFn = (type: string, payload?: Record<string, unknown>) => Promise<MgmtResponse>

// Poll interval in ms
const POLL_MS = 2000

export function useAgentStatus(sendRequest: SendRequestFn) {
  const phase = ref<AgentPhase>('idle')
  const phaseLabel = ref('待命')
  const channels = ref<Record<string, ChannelStatus>>({})
  const chatCount = ref(0)
  const uptimeSec = ref(0)
  const connected = ref(false)
  const lastUpdate = ref('')

  let timer: ReturnType<typeof setInterval> | null = null
  let destroyed = false

  const phaseLabels: Record<AgentPhase, string> = {
    idle: '待命',
    thinking: '思考中',
    executing: '执行工具',
    responding: '回复中',
  }

  async function poll() {
    try {
      const resp = await sendRequest('get_status')
      if (resp.ok) {
        phase.value = (resp.agent_state as AgentPhase) || 'idle'
        phaseLabel.value = phaseLabels[phase.value] || '待命'
        channels.value = (resp.channels as Record<string, ChannelStatus>) || {}
        chatCount.value = (resp.chat_count as number) || 0
        uptimeSec.value = (resp.uptime_sec as number) || 0
        lastUpdate.value = (resp.timestamp as string) || ''
        connected.value = true
      }
    } catch {
      connected.value = false
    }
  }

  function formatUptime(): string {
    const s = uptimeSec.value
    if (s < 60) return `${s}s`
    if (s < 3600) return `${Math.floor(s / 60)}m`
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return `${h}h ${m}m`
  }

  function start() {
    if (destroyed) return
    poll() // immediate first poll
    timer = setInterval(poll, POLL_MS)
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(start)
  onUnmounted(() => {
    destroyed = true
    stop()
  })

  return {
    phase,
    phaseLabel,
    channels,
    chatCount,
    uptimeSec,
    connected,
    lastUpdate,
    formatUptime,
    start,
    stop,
  }
}

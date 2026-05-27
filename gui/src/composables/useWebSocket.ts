import { ref, onMounted, onUnmounted } from 'vue'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  name?: string
  args?: string
  result?: string
  route?: 'local' | 'cloud'
}

export interface QRCodeData {
  url: string
  channel: string
  state: string
}

export interface ConfirmRequest {
  requestId: string
  operation: string
  details: string
}

export interface WorkEntry {
  id: number
  type: 'llm_output' | 'tool_call' | 'tool_result' | 'status' | 'error' | 'done'
  content: string
  name?: string
  args?: string
  result?: string
  step_id?: number
  phase?: string
  duration?: number
}

function _isErrorMessage(text: string): boolean {
  return /^模型调用失败/.test(text)
    || /^处理超时/.test(text)
    || text.includes('[系统错误]')
}

// Management response payload (same shape from all handlers)
export interface MgmtResponse {
  ok: boolean
  error?: string
  [key: string]: unknown   // handler-specific fields
}

interface PendingRequest {
  resolve: (value: MgmtResponse) => void
  reject: (reason: Error) => void
  timer: ReturnType<typeof setTimeout>
}

// 30s timeout for management requests
const REQUEST_TIMEOUT_MS = 30_000

export function useWebSocket() {
  const ws = ref<WebSocket | null>(null)
  const connected = ref(false)
  const messages = ref<ChatMessage[]>([])
  const streaming = ref(false)
  const streamingContent = ref('')
  const qrCode = ref<QRCodeData | null>(null)
  const statusText = ref('')
  const currentTool = ref<{ name: string; args: string } | null>(null)
  const confirmRequest = ref<ConfirmRequest | null>(null)
  const configUpdated = ref(0)  // incremented on config_updated push events
  const proposalCompleted = ref(0)  // incremented on proposal_completed push events
  const channelStatusChanged = ref(0)  // incremented on channel_disconnected push
  const tasksUpdated = ref(0)  // incremented on task_updated push events
  const proposalAdded = ref(0)  // incremented on proposal_added push events
  const workEntries = ref<WorkEntry[]>([])
  let _workEntryId = 0
  // When true, the next llm_output starts a fresh assistant bubble (set after tool_call/tool_result)
  let _bubbleClosed = false

  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let isDestroyed = false

  // Management request queue
  // eslint-disable-next-line prefer-const
  let pendingRequests = new Map<string, PendingRequest>()
  let _connectResolve: (() => void) | null = null
  let _connectPromise: Promise<void> | null = null

  function connect() {
    if (isDestroyed) return
    _connectPromise = new Promise((resolve) => { _connectResolve = resolve })
    try {
      ws.value = new WebSocket('ws://127.0.0.1:9765')
    } catch {
      _connectPromise = null
      scheduleReconnect()
      return
    }
    ws.value.onopen = () => {
      connected.value = true
      confirmRequest.value = null
      qrCode.value = null
      _connectResolve?.()
    }
    ws.value.onclose = () => {
      connected.value = false
      ws.value = null
      confirmRequest.value = null
      qrCode.value = null
      _connectPromise = null
      scheduleReconnect()
    }
    ws.value.onerror = () => {
      ws.value?.close()
    }
    ws.value.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        const p = data.payload || {}

        // Management responses — resolve pending promise.
        // Guarded by request_id match (UUID) so unknown _resp types are harmless.
        if (data.type && data.type.endsWith('_resp') && data.request_id) {
          const rid = data.request_id
          if (rid && pendingRequests.has(rid)) {
            const pr = pendingRequests.get(rid)!
            clearTimeout(pr.timer)
            pendingRequests.delete(rid)
            pr.resolve(p as MgmtResponse)
          }
          return
        }

        // Chat / streaming chunk
        switch (data.type) {
          case 'done': {
            streaming.value = false
            // 标记当前气泡已结束，下次 llm_output 从新气泡开始
            _bubbleClosed = true
            statusText.value = ''
            currentTool.value = null

            const finalContent = (p.content || streamingContent.value || '').trim()
            const finalRoute = (p.route || data.route) as 'local' | 'cloud' | undefined

            if (finalContent && !_isErrorMessage(finalContent)) {
              // If no streaming happened (no llm_output chunks), push to chat now
              if (streamingContent.value === '') {
                messages.value.push({ role: 'assistant', content: finalContent, route: finalRoute })
              } else {
                // Attach route to the last assistant message
                const lastMsg = messages.value[messages.value.length - 1]
                if (lastMsg && lastMsg.role === 'assistant') {
                  messages.value[messages.value.length - 1] = { ...lastMsg, route: finalRoute || lastMsg.route }
                }
              }
            } else if (_isErrorMessage(finalContent)) {
              // Error → work panel only, don't pollute chat
              workEntries.value.push({
                id: ++_workEntryId,
                type: 'error',
                content: finalContent,
              })
            }
            streamingContent.value = ''
            break
          }
          case 'llm_output': {
            streaming.value = true
            const chunk = p.content || ''
            streamingContent.value = (streamingContent.value || '') + chunk
            // Only show in chat area (not work panel) — stream into assistant bubble
            const msgs = messages.value
            if (_bubbleClosed && chunk.trim()) {
              // Tool call happened since last llm_output — start a new bubble
              msgs.push({ role: 'assistant', content: chunk })
              _bubbleClosed = false
            } else {
              if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
                msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], content: msgs[msgs.length - 1].content + chunk }
              } else if (chunk.trim()) {
                msgs.push({ role: 'assistant', content: chunk })
              }
            }
            break
          }
          case 'status': {
            statusText.value = p.content || ''
            workEntries.value.push({
              id: ++_workEntryId,
              type: 'status',
              content: p.content || '',
              step_id: p.step_id,
            })
            break
          }
          case 'tool_call': {
            _bubbleClosed = true
            currentTool.value = { name: p.name || '', args: JSON.stringify(p.args || {}) }
            workEntries.value.push({
              id: ++_workEntryId,
              type: 'tool_call',
              content: p.name || '',
              name: p.name,
              args: JSON.stringify(p.args || {}),
              step_id: p.step_id,
              phase: p.phase || 'plan',
            })
            break
          }
          case 'tool_result': {
            _bubbleClosed = true
            currentTool.value = null
            workEntries.value.push({
              id: ++_workEntryId,
              type: 'tool_result',
              content: '',
              name: p.name || '',
              result: typeof p.result === 'string' ? p.result.slice(0, 200) : JSON.stringify(p.result || {}).slice(0, 200),
              step_id: p.step_id,
              phase: p.phase || 'result',
              duration: p.duration,
            })
            break
          }
          case 'qr_code': {
            if (p.url) {
              qrCode.value = { url: p.url, channel: p.channel || '', state: p.state || '' }
            }
            break
          }
          case 'confirm_request': {
            confirmRequest.value = {
              requestId: data.request_id || '',
              operation: p.operation || '',
              details: p.details || '',
            }
            break
          }
          case 'config_updated': {
            configUpdated.value++
            break
          }
          case 'proposal_completed': {
            proposalCompleted.value++
            break
          }
          case 'channel_disconnected': {
            channelStatusChanged.value++
            break
          }
          case 'task_updated': {
            tasksUpdated.value++
            break
          }
          case 'proposal_added': {
            proposalAdded.value++
            break
          }
        }
      } catch {
        // ignore parse errors
      }
    }
  }

  // ------------------------------------------------------------------
  // Management request API
  // ------------------------------------------------------------------

  async function waitForOpen(): Promise<void> {
    if (ws.value?.readyState === WebSocket.OPEN) return
    const timeoutMs = 10000
    if (_connectPromise) {
      await Promise.race([
        _connectPromise,
        new Promise(resolve => setTimeout(resolve, timeoutMs)),
      ])
    }
    if (ws.value?.readyState !== WebSocket.OPEN && !isDestroyed) {
      await Promise.race([
        new Promise<void>((resolve) => {
          const check = setInterval(() => {
            if (ws.value?.readyState === WebSocket.OPEN || isDestroyed) {
              clearInterval(check)
              resolve()
            }
          }, 100)
        }),
        new Promise(resolve => setTimeout(resolve, timeoutMs)),
      ])
    }
  }

  async function sendRequest(type: string, payload: Record<string, unknown> = {}): Promise<MgmtResponse> {
    await waitForOpen()
    return new Promise((resolve, reject) => {
      if (!ws.value || ws.value.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'))
        return
      }
      const requestId = crypto.randomUUID()
      const timer = setTimeout(() => {
        pendingRequests.delete(requestId)
        reject(new Error(`Request ${type} timed out after ${REQUEST_TIMEOUT_MS / 1000}s`))
      }, REQUEST_TIMEOUT_MS)
      pendingRequests.set(requestId, { resolve, reject, timer })
      ws.value.send(JSON.stringify({
        type,
        request_id: requestId,
        payload,
      }))
    })
  }

  function clearQRCode() {
    qrCode.value = null
  }

  function clearStatus() {
    statusText.value = ''
  }

  function clearWorkEntries() {
    workEntries.value = []
    _workEntryId = 0
  }

  /** List user preferences with optional enabled_only filter. */
  async function listPreferences(enabledOnly = false): Promise<MgmtResponse> {
    return sendRequest('list_preferences', { enabled_only: enabledOnly })
  }

  /** Create or update a user preference (source=user_override). */
  async function updatePreference(key: string, value: string, description = ''): Promise<MgmtResponse> {
    return sendRequest('update_preference', { key, value, description })
  }

  /** Delete a user preference by key. */
  async function deletePreference(key: string): Promise<MgmtResponse> {
    return sendRequest('delete_preference', { key })
  }

  function respondToConfirm(confirmed: boolean) {
    if (!ws.value || ws.value.readyState !== WebSocket.OPEN || !confirmRequest.value) return
    ws.value.send(JSON.stringify({
      type: 'confirm_response',
      request_id: confirmRequest.value.requestId,
      payload: { confirmed },
    }))
    confirmRequest.value = null
  }

  function scheduleReconnect() {
    if (isDestroyed) return
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => connect(), 3000)
  }

  function send(content: string, routeOverride?: 'auto' | 'local' | 'cloud') {
    if (!ws.value || ws.value.readyState !== WebSocket.OPEN) return
    // Clear work entries from previous task
    clearWorkEntries()
    // Reset bubble tracking for new conversation
    _bubbleClosed = false
    // Don't reset state if still processing — backend cancels old task on new message
    if (!streaming.value && !currentTool.value) {
      statusText.value = ''
    }
    // Clear accumulated streaming content so the new task starts fresh
    streamingContent.value = ''

    messages.value.push({ role: 'user', content })
    const payload: Record<string, unknown> = { content }
    if (routeOverride && routeOverride !== 'auto') {
      payload.route_override = routeOverride
    }
    ws.value.send(JSON.stringify({
      type: 'message',
      payload,
    }))
  }

  onMounted(() => connect())

  onUnmounted(() => {
    isDestroyed = true
    // Reject all pending requests
    for (const [rid, pr] of pendingRequests) {
      clearTimeout(pr.timer)
      pr.reject(new Error('WebSocket destroyed'))
      pendingRequests.delete(rid)
    }
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws.value?.close()
  })

  return {
    connected,
    messages,
    streaming,
    streamingContent,
    qrCode,
    statusText,
    currentTool,
    confirmRequest,
    configUpdated,
    proposalCompleted,
    channelStatusChanged,
    tasksUpdated,
    proposalAdded,
    workEntries,
    send,
    sendRequest,
    listPreferences,
    updatePreference,
    deletePreference,
    respondToConfirm,
    clearQRCode,
    clearStatus,
    clearWorkEntries,
  }
}

import { ref, onMounted, onUnmounted } from 'vue'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  name?: string
  args?: string
  result?: string
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

        // Management responses — resolve pending promise
        if (data.type && data.type.endsWith('_resp')) {
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
            const finalContent = p.content || streamingContent.value || ''
            if (finalContent) {
              messages.value.push({ role: 'assistant', content: finalContent })
            }
            streaming.value = false
            streamingContent.value = ''
            statusText.value = ''
            currentTool.value = null
            break
          }
          case 'llm_output': {
            streaming.value = true
            streamingContent.value = (streamingContent.value || '') + (p.content || '')
            break
          }
          case 'status': {
            statusText.value = p.content || ''
            break
          }
          case 'tool_call': {
            currentTool.value = { name: p.name || '', args: JSON.stringify(p.args || {}) }
            break
          }
          case 'tool_result': {
            currentTool.value = null
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

  function send(content: string) {
    if (!ws.value || ws.value.readyState !== WebSocket.OPEN) return
    // Don't reset state if still processing — backend cancels old task on new message
    if (!streaming.value && !currentTool.value) {
      statusText.value = ''
    }
    // Clear accumulated streaming content so the new task starts fresh
    streamingContent.value = ''

    messages.value.push({ role: 'user', content })
    ws.value.send(JSON.stringify({
      type: 'message',
      payload: { content },
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
    send,
    sendRequest,
    respondToConfirm,
    clearQRCode,
    clearStatus,
  }
}

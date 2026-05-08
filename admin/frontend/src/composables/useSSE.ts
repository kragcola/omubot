
export interface SSELogEntry {
  ts: string
  level: string
  channel: string
  message: string
}

// Module-level singleton
const logs = ref<SSELogEntry[]>([])
const connected = ref(false)
let eventSource: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let subscriberCount = 0

function _connect() {
  if (eventSource) return
  eventSource = new EventSource('/api/admin/events')
  connected.value = true

  eventSource.addEventListener('log', (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.entries) {
        logs.value = [...logs.value.slice(-200), ...data.entries]
      }
    } catch {}
  })

  eventSource.addEventListener('scheduler', () => {
    // Handled by scheduler store
  })

  eventSource.onerror = () => {
    connected.value = false
    eventSource?.close()
    eventSource = null
    reconnectTimer = setTimeout(_connect, 5000)
  }
}

function _disconnect() {
  eventSource?.close()
  eventSource = null
  connected.value = false
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

export function useSSE() {
  onMounted(() => {
    subscriberCount++
    if (subscriberCount === 1) _connect()
  })

  onUnmounted(() => {
    subscriberCount--
    if (subscriberCount <= 0) {
      subscriberCount = 0
      _disconnect()
    }
  })

  return { logs, connected }
}

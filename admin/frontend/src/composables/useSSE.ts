import { onMounted, onUnmounted, ref } from 'vue'

export interface SSELogEntry {
  ts: string
  level: string
  channel: string
  message: string
}

export interface SSEGroupMessage {
  type: 'group_message'
  group_id: string
  user_id: string
  ts: number
  is_bot: boolean
  presence_mode: string | null
  consumed: boolean
}

export interface SSEGroupActivityEntry {
  last_at?: number
  count_window?: number
  user_count_window?: number
}

export interface SSEGroupActivitySnapshot {
  ts: number
  window_seconds: number
  groups: Record<string, SSEGroupActivityEntry>
}

// Module-level singleton — one EventSource shared across all useSSE() callers
const logs = ref<SSELogEntry[]>([])
const connected = ref(false)
const eventBus = new EventTarget()
let eventSource: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let subscriberCount = 0

function _connect() {
  if (eventSource) return
  eventSource = new EventSource('/api/admin/events')
  connected.value = true

  eventSource.addEventListener('log', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data)
      if (data.entries) {
        logs.value = [...logs.value.slice(-200), ...data.entries]
      }
    } catch {}
  })

  eventSource.addEventListener('group_message', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as SSEGroupMessage
      eventBus.dispatchEvent(new CustomEvent<SSEGroupMessage>('group_message', { detail: data }))
    } catch {}
  })

  eventSource.addEventListener('group_activity', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as SSEGroupActivitySnapshot
      eventBus.dispatchEvent(new CustomEvent<SSEGroupActivitySnapshot>('group_activity', { detail: data }))
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

/**
 * Subscribe to inbound group_message events. Returns an unsubscribe function.
 *
 * The handler runs as long as some component on the page is calling useSSE()
 * and keeping the EventSource alive — call this from a view's setup() and
 * register the cleanup in onUnmounted.
 */
export function onGroupMessage(handler: (event: SSEGroupMessage) => void): () => void {
  const wrapper = (e: Event) => handler((e as CustomEvent<SSEGroupMessage>).detail)
  eventBus.addEventListener('group_message', wrapper)
  return () => eventBus.removeEventListener('group_message', wrapper)
}

/**
 * Subscribe to periodic group_activity snapshots used for reconciliation.
 *
 * Snapshots are server-authoritative — apply them to overwrite locally
 * incremented counters so they don't drift.
 */
export function onGroupActivity(handler: (snapshot: SSEGroupActivitySnapshot) => void): () => void {
  const wrapper = (e: Event) => handler((e as CustomEvent<SSEGroupActivitySnapshot>).detail)
  eventBus.addEventListener('group_activity', wrapper)
  return () => eventBus.removeEventListener('group_activity', wrapper)
}

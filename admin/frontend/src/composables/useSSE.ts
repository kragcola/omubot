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

export interface SSEBlockTraceEvent {
  type: 'block_trace_recorded'
  request_id: string
  count: number
  accepted: number
  trimmed: number
  rejected: number
  shadow_only: number
  ts: number
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

  eventSource.addEventListener('cache_pipelines', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data)
      eventBus.dispatchEvent(new CustomEvent('cache_pipelines', { detail: data }))
    } catch {}
  })

  eventSource.addEventListener('block_trace', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as SSEBlockTraceEvent
      eventBus.dispatchEvent(new CustomEvent<SSEBlockTraceEvent>('block_trace', { detail: data }))
    } catch {}
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

export interface SSECachePipelineSample {
  ts: string
  task: string
  hit_pct: number | null
  hit_tokens: number
  miss_tokens: number
}

export interface SSECachePipelineRecent {
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
  samples: SSECachePipelineSample[]
}

export interface SSECachePipelinePayload {
  overall: {
    calls: number
    hit_tokens: number
    miss_tokens: number
    hit_pct: number | null
    recent?: SSECachePipelineRecent
  }
  pipelines: Array<{
    key: string
    label: string
    tasks: string[]
    calls: number
    hit_tokens: number
    miss_tokens: number
    hit_pct: number | null
    per_task: Array<{
      task: string
      calls: number
      hit_tokens: number
      miss_tokens: number
      hit_pct: number | null
    }>
    recent?: SSECachePipelineRecent
  }>
}

/**
 * Subscribe to periodic cache_pipelines snapshots for the dashboard panel.
 *
 * Pushed by the server every ~10 s so the cache-hit panel stays live
 * without any client-side polling timer.
 */
export function onCachePipelines(handler: (payload: SSECachePipelinePayload) => void): () => void {
  const wrapper = (e: Event) => handler((e as CustomEvent<SSECachePipelinePayload>).detail)
  eventBus.addEventListener('cache_pipelines', wrapper)
  return () => eventBus.removeEventListener('cache_pipelines', wrapper)
}

/**
 * Subscribe to block_trace events fired whenever the BudgetManager records a
 * batch of traces or a provider runs in shadow mode. Carries only counts —
 * the BlockTraceView re-fetches /alignment, /stats, /recent on receipt.
 */
export function onBlockTrace(handler: (event: SSEBlockTraceEvent) => void): () => void {
  const wrapper = (e: Event) => handler((e as CustomEvent<SSEBlockTraceEvent>).detail)
  eventBus.addEventListener('block_trace', wrapper)
  return () => eventBus.removeEventListener('block_trace', wrapper)
}

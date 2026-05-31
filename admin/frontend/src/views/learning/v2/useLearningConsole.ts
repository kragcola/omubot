import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type {
  LearningStageKey,
  LearningItem,
  LearningItemsResponse,
  StageStripItem,
} from '../types'

export type ViewTab = 'dashboard' | 'pipeline' | 'settings'

export interface PipelineStages {
  candidate: StageStripItem
  review: StageStripItem
  approved: StageStripItem
  hits: StageStripItem
  archived: StageStripItem
}

export interface ScheduleTask {
  enabled: boolean
  last_run: string | null
  status: 'idle' | 'disabled' | 'active'
  interval_minutes?: number
}

export interface Schedules {
  slang_extract: ScheduleTask
  style_extract: ScheduleTask
  consolidator: ScheduleTask
  affection_scoring: ScheduleTask
}

export interface PipelineSettings {
  autopilot: { enabled: boolean; aggressiveness: 'conservative' | 'standard' | 'aggressive'; concurrency?: number }
  slang: Record<string, unknown>
  style: { extract_enabled: boolean; extract_interval_minutes: number }
  consolidator: { auto_enabled: boolean; interval_minutes: number }
  affection: { scoring_enabled: boolean }
}

export interface ActivityEvent {
  time: string
  message: string
  type: 'extract' | 'review' | 'hit' | 'archive' | 'approve'
}

export interface TrendPoint {
  date: string
  candidate: number
  approved: number
  hits: number
}

async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`/api/admin${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`)
  return res.json()
}

export function useLearningConsole() {
  const route = useRoute()
  const router = useRouter()

  const activeView = computed<ViewTab>({
    get: () => (route.query.view as ViewTab) || 'dashboard',
    set: (v) => router.replace({ query: { ...route.query, view: v } }),
  })

  // --- Dashboard ---
  const stages = ref<PipelineStages | null>(null)
  const schedules = ref<Schedules | null>(null)
  const trend = ref<TrendPoint[]>([])
  const activity = ref<ActivityEvent[]>([])
  const autopilotStatus = ref<{ enabled: boolean; aggressiveness: string }>({ enabled: false, aggressiveness: 'standard' })
  const dashboardLoading = ref(false)

  async function fetchDashboard() {
    dashboardLoading.value = true
    try {
      const [pipelineRes, schedulesRes, trendRes, activityRes, settingsRes] = await Promise.allSettled([
        api<{ stages: PipelineStages }>('/learning/pipeline'),
        api<Schedules>('/learning/schedules'),
        api<{ points: TrendPoint[] }>('/learning/stats/trend?days=7'),
        api<{ events: ActivityEvent[] }>('/learning/activity?limit=20'),
        api<PipelineSettings>('/learning/settings'),
      ])
      if (pipelineRes.status === 'fulfilled') {
        stages.value = pipelineRes.value.stages
      }
      if (schedulesRes.status === 'fulfilled') schedules.value = schedulesRes.value
      if (trendRes.status === 'fulfilled') trend.value = trendRes.value.points
      if (activityRes.status === 'fulfilled') activity.value = activityRes.value.events
      if (settingsRes.status === 'fulfilled') {
        autopilotStatus.value = settingsRes.value.autopilot
      }
    } finally {
      dashboardLoading.value = false
    }
  }

  // --- Pipeline ---
  const activeStage = ref<LearningStageKey>('candidate')
  const activeNouns = ref<string[]>([])
  const activeSubStage = ref<'all' | 'unscanned' | 'ai_kept'>('all')
  const candidateSub = ref<{ unscanned: number; ai_rejected: number; ai_kept: number; all: number }>({ unscanned: 0, ai_rejected: 0, ai_kept: 0, all: 0 })
  const items = ref<LearningItem[]>([])
  const hasMore = ref(false)
  const nextCursor = ref('')
  const pipelineLoading = ref(false)
  const dateCounts = ref<Record<string, number>>({})

  async function fetchItems(append = false) {
    pipelineLoading.value = true
    try {
      const nounParam = activeNouns.value.length > 0 ? activeNouns.value.join(',') : 'all'
      const params = new URLSearchParams({
        stage: activeStage.value,
        noun: nounParam,
        sort: 'newest',
        limit: '30',
      })
      if (activeStage.value === 'candidate' && activeSubStage.value !== 'all') {
        params.set('sub_stage', activeSubStage.value)
      }
      if (append && nextCursor.value) params.set('cursor', nextCursor.value)
      const [itemsRes, pipelineRes] = await Promise.allSettled([
        api<LearningItemsResponse & { date_counts?: Record<string, number> }>(`/learning/items?${params}`),
        append ? Promise.resolve(null) : api<{ stages: PipelineStages; candidate_sub?: { unscanned: number; ai_rejected: number; ai_kept: number; all: number } }>('/learning/pipeline'),
      ])
      if (itemsRes.status === 'fulfilled') {
        const res = itemsRes.value
        items.value = append ? [...items.value, ...res.items] : res.items
        hasMore.value = res.has_more
        nextCursor.value = res.next_cursor ?? ''
        if (res.date_counts) dateCounts.value = res.date_counts
      }
      if (pipelineRes.status === 'fulfilled' && pipelineRes.value) {
        stages.value = pipelineRes.value.stages
        if (pipelineRes.value.candidate_sub) candidateSub.value = pipelineRes.value.candidate_sub
      }
    } finally {
      pipelineLoading.value = false
    }
  }

  watch(activeStage, () => fetchItems())
  watch(activeSubStage, () => fetchItems())

  // --- Settings ---
  const settings = ref<PipelineSettings | null>(null)
  const settingsLoading = ref(false)
  const settingsSaving = ref(false)

  async function fetchSettings() {
    settingsLoading.value = true
    try {
      settings.value = await api<PipelineSettings>('/learning/settings')
    } finally {
      settingsLoading.value = false
    }
  }

  async function saveSettings(payload: Partial<PipelineSettings>) {
    settingsSaving.value = true
    try {
      await api('/learning/settings', { method: 'POST', body: JSON.stringify(payload) })
      await fetchSettings()
    } finally {
      settingsSaving.value = false
    }
  }

  return {
    activeView,
    // dashboard
    stages,
    schedules,
    trend,
    activity,
    autopilotStatus,
    dashboardLoading,
    fetchDashboard,
    // pipeline
    activeStage,
    activeNouns,
    activeSubStage,
    candidateSub,
    items,
    hasMore,
    nextCursor,
    pipelineLoading,
    dateCounts,
    fetchItems,
    // settings
    settings,
    settingsLoading,
    settingsSaving,
    fetchSettings,
    saveSettings,
  }
}

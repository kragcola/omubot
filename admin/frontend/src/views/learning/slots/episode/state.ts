import { type InjectionKey, inject, ref } from 'vue'
import { api } from '../../../../api/client'
import type { LearningStageKey } from '../../types'

export interface EpisodeItem {
  episode_id: string
  group_id: string
  scope: string
  situation: string
  observed_context: string
  action_taken: string
  outcome_signal: string
  reflection: string
  linked_memory_ids: string[]
  confidence: number
  episode_state: string
  source: string
  decay_at: string
  last_used_at: string
  created_at: string
  updated_at: string
  disabled_by_admin: boolean
  cross_group_visible: boolean
  cross_group_enabled_by: string
  cross_group_enabled_at: string
  cross_group_enabled_for_groups: string[]
  cross_group_enabled_reason: string
}

export interface EpisodeStats {
  dry_run: number
  candidate: number
  approved: number
  enabled_for_prompt: number
  disabled: number
}

export interface EpisodeRevision {
  revision_id: string
  episode_id: string
  action: string
  actor: string
  prev_state: string
  new_state: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: string
}

export type EpisodeAction = 'approve' | 'disable' | 'restore'

export const EPISODE_STATE_LABEL: Record<string, string> = {
  dry_run: 'dry_run',
  candidate: '候选',
  approved: '已批准',
  enabled_for_prompt: '已注入',
  disabled: '已停用',
}

export const EPISODE_STATE_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  dry_run: 'default',
  candidate: 'info',
  approved: 'success',
  enabled_for_prompt: 'warning',
  disabled: 'error',
}

export const EPISODE_ACTION_LABEL: Record<EpisodeAction, string> = {
  approve: '批准',
  disable: '停用',
  restore: '恢复',
}

export function stageToEpisodeState(stage: LearningStageKey): string | null {
  switch (stage) {
    case 'candidate':
      return 'candidate'
    case 'review':
      return 'approved'
    case 'approved':
      return 'enabled_for_prompt'
    case 'archived':
      return 'disabled'
    case 'hits':
    default:
      return null
  }
}

export function createEpisodeConsole() {
  const loading = ref(false)
  const episodes = ref<EpisodeItem[]>([])
  const stats = ref<EpisodeStats>({
    dry_run: 0,
    candidate: 0,
    approved: 0,
    enabled_for_prompt: 0,
    disabled: 0,
  })
  const filterState = ref<string | null>(null)
  const filterGroup = ref('')

  const actionTarget = ref<EpisodeItem | null>(null)
  const actionType = ref<EpisodeAction>('approve')
  const actionReason = ref('')
  const actionSubmitting = ref(false)
  const detailTarget = ref<EpisodeItem | null>(null)
  const revisions = ref<EpisodeRevision[]>([])
  const revisionsLoading = ref(false)

  async function fetchStats() {
    try {
      const res = await api<{ ok: boolean, stats: EpisodeStats }>('/api/admin/episodes/stats')
      stats.value = res.stats || stats.value
    } catch {
      // swallow — stats are optional
    }
  }

  async function fetchEpisodes() {
    loading.value = true
    try {
      const params = new URLSearchParams()
      if (filterState.value) params.set('state', filterState.value)
      if (filterGroup.value.trim()) params.set('group_id', filterGroup.value.trim())
      params.set('limit', '100')
      const qs = params.toString()
      const res = await api<{ ok: boolean, episodes: EpisodeItem[] }>(
        `/api/admin/episodes${qs ? '?' + qs : ''}`,
      )
      episodes.value = (res.episodes || []).map(ep => ({
        ...ep,
        linked_memory_ids: Array.isArray(ep.linked_memory_ids) ? ep.linked_memory_ids : [],
        cross_group_enabled_for_groups: Array.isArray(ep.cross_group_enabled_for_groups)
          ? ep.cross_group_enabled_for_groups
          : [],
      }))
    } catch (e: any) {
      console.error('[episode fold-in] fetchEpisodes failed', e)
    } finally {
      loading.value = false
    }
  }

  function refresh() {
    void fetchStats()
    void fetchEpisodes()
  }

  function openActionDialog(ep: EpisodeItem, action: EpisodeAction) {
    actionTarget.value = ep
    actionType.value = action
    actionReason.value = ''
  }

  function closeActionDialog() {
    actionTarget.value = null
    actionReason.value = ''
  }

  async function submitAction() {
    if (!actionTarget.value) return
    const target = actionTarget.value
    const action = actionType.value
    actionSubmitting.value = true
    try {
      await api(`/api/admin/episodes/${target.episode_id}/${action}`, {
        method: 'POST',
        body: { reason: actionReason.value.trim() },
      })
      closeActionDialog()
      await fetchStats()
      await fetchEpisodes()
      if (detailTarget.value && detailTarget.value.episode_id === target.episode_id) {
        await openDetail(target.episode_id)
      }
    } catch (e: any) {
      console.error('[episode fold-in] submitAction failed', e)
    } finally {
      actionSubmitting.value = false
    }
  }

  async function openDetail(episodeId: string) {
    const ep = episodes.value.find(e => e.episode_id === episodeId)
    if (!ep) return
    detailTarget.value = ep
    revisions.value = []
    revisionsLoading.value = true
    try {
      const res = await api<{ ok: boolean, revisions: EpisodeRevision[] }>(
        `/api/admin/episodes/${episodeId}/revisions?limit=100`,
      )
      revisions.value = res.revisions || []
    } catch {
      revisions.value = []
    } finally {
      revisionsLoading.value = false
    }
  }

  function closeDetail() {
    detailTarget.value = null
  }

  return {
    loading,
    episodes,
    stats,
    filterState,
    filterGroup,
    actionTarget,
    actionType,
    actionReason,
    actionSubmitting,
    detailTarget,
    revisions,
    revisionsLoading,
    fetchStats,
    fetchEpisodes,
    refresh,
    openActionDialog,
    closeActionDialog,
    submitAction,
    openDetail,
    closeDetail,
  }
}

export type EpisodeConsole = ReturnType<typeof createEpisodeConsole>

export const EPISODE_CONSOLE_KEY: InjectionKey<EpisodeConsole> = Symbol('episode-console')

export function useEpisodeConsoleInject(): EpisodeConsole {
  const value = inject(EPISODE_CONSOLE_KEY)
  if (!value) {
    throw new Error('EpisodeFoldInProvider must wrap any episode fold-in slot consumer')
  }
  return value
}

export function decayHint(decayAt: string): string {
  if (!decayAt) return '—'
  const t = new Date(decayAt).getTime()
  if (Number.isNaN(t)) return decayAt
  const diff = t - Date.now()
  if (diff <= 0) return '已过期'
  const hours = Math.round(diff / 3600000)
  if (hours < 24) return `约 ${hours} 小时`
  const days = Math.round(hours / 24)
  return `约 ${days} 天`
}

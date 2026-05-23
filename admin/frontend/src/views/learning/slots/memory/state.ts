import { type InjectionKey, computed, inject, ref } from 'vue'
import { api } from '../../../../api/client'
import type { LearningStageKey } from '../../types'

export interface Candidate {
  candidate_id: string
  run_id: string
  domain: string
  scope: string
  group_id: string
  source_message_pks: number[]
  payload: Record<string, any>
  confidence: number
  state: string
  decision_reason: string
  decided_by: string
  decided_at: number
  normalizer_cluster_id: string
  created_at: number
}

export interface Revision {
  revision_id: string
  candidate_id: string
  action: string
  actor: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: number
  meta: Record<string, any>
}

export const DOMAIN_LABEL: Record<string, string> = {
  fact: '事实 fact',
  slang: '黑话 slang',
  style: '风格 style',
  episode: '经验 episode',
  graph_relation: '图谱 graph_relation',
}

export const STATE_LABEL: Record<string, string> = {
  dry_run: 'dry_run',
  queued: '已入队 queued',
  approved: '已批准 approved',
  rejected: '已拒绝 rejected',
}

export const STATE_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  dry_run: 'default',
  queued: 'info',
  approved: 'success',
  rejected: 'error',
}

export const DOMAIN_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  fact: 'info',
  slang: 'success',
  style: 'warning',
  episode: 'default',
  graph_relation: 'error',
}

export const EPISODE_FIELDS: { key: string, label: string }[] = [
  { key: 'situation', label: '场景 situation' },
  { key: 'observed_context', label: '观察上下文 observed_context' },
  { key: 'action_taken', label: '采取行动 action_taken' },
  { key: 'outcome_signal', label: '结果信号 outcome_signal' },
  { key: 'reflection', label: '反思 reflection' },
]

export function stageToCandidateState(stage: LearningStageKey): string {
  switch (stage) {
    case 'candidate':
      return 'dry_run'
    case 'review':
      return 'queued'
    case 'approved':
      return 'approved'
    case 'archived':
      return 'rejected'
    case 'hits':
    default:
      return 'all'
  }
}

export function timeText(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  if (Number.isNaN(d.getTime())) return String(ts)
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

export function createMemoryConsole() {
  const loading = ref(false)
  const candidates = ref<Candidate[]>([])
  const filterDomain = ref<string>('all')
  const filterState = ref<string>('all')
  const filterGroup = ref('')

  const detailTarget = ref<Candidate | null>(null)
  const revisions = ref<Revision[]>([])
  const revisionsLoading = ref(false)

  const editing = ref(false)
  const editPayload = ref<Record<string, string>>({})
  const editReason = ref('')
  const editSubmitting = ref(false)

  const decideTarget = ref<Candidate | null>(null)
  const decideAction = ref<'approved' | 'rejected'>('approved')
  const decideReason = ref('')
  const decideSubmitting = ref(false)

  const filteredCandidates = computed(() => {
    return candidates.value.filter((c) => {
      if (filterDomain.value !== 'all' && c.domain !== filterDomain.value) return false
      if (filterGroup.value.trim() && c.group_id !== filterGroup.value.trim()) return false
      return true
    })
  })

  const stats = computed(() => {
    const out = { total: 0, dry_run: 0, queued: 0, approved: 0, rejected: 0, episode: 0 }
    for (const c of candidates.value) {
      out.total += 1
      if (c.state in out) (out as any)[c.state] += 1
      if (c.domain === 'episode') out.episode += 1
    }
    return out
  })

  const canEdit = computed(() => {
    if (!detailTarget.value) return false
    if (detailTarget.value.domain !== 'episode') return false
    return ['dry_run', 'queued'].includes(detailTarget.value.state)
  })

  async function fetchCandidates() {
    loading.value = true
    try {
      const params = new URLSearchParams()
      if (filterState.value !== 'all') params.set('state', filterState.value)
      params.set('limit', '200')
      const qs = params.toString()
      const res = await api<{ ok: boolean, data: Candidate[], count: number }>(
        `/api/admin/memory_consolidator/candidates${qs ? '?' + qs : ''}`,
      )
      candidates.value = (res.data || []).map(c => ({
        ...c,
        source_message_pks: Array.isArray(c.source_message_pks) ? c.source_message_pks : [],
        payload: c.payload && typeof c.payload === 'object' ? c.payload : {},
      }))
    } catch (e: any) {
      console.error('[memory fold-in] fetchCandidates failed', e)
    } finally {
      loading.value = false
    }
  }

  async function openDetail(candidateId: string) {
    const c = candidates.value.find(x => x.candidate_id === candidateId)
    if (!c) return
    detailTarget.value = c
    editPayload.value = {}
    editing.value = false
    revisions.value = []
    revisionsLoading.value = true
    try {
      const res = await api<{ ok: boolean, data: Revision[], count: number }>(
        `/api/admin/memory_consolidator/candidates/${candidateId}/revisions?limit=100`,
      )
      revisions.value = res.data || []
    } catch {
      revisions.value = []
    } finally {
      revisionsLoading.value = false
    }
  }

  function closeDetail() {
    detailTarget.value = null
    editPayload.value = {}
    editing.value = false
  }

  function startEdit() {
    if (!detailTarget.value) return
    if (detailTarget.value.domain !== 'episode') return
    if (!['dry_run', 'queued'].includes(detailTarget.value.state)) return
    const draft: Record<string, string> = {}
    for (const field of EPISODE_FIELDS) {
      draft[field.key] = String(detailTarget.value.payload[field.key] ?? '')
    }
    editPayload.value = draft
    editReason.value = ''
    editing.value = true
  }

  function cancelEdit() {
    editing.value = false
    editPayload.value = {}
    editReason.value = ''
  }

  async function submitEdit() {
    if (!detailTarget.value) return
    const target = detailTarget.value
    editSubmitting.value = true
    try {
      const res = await api<{ ok: boolean, data: Candidate }>(
        `/api/admin/memory_consolidator/candidates/${target.candidate_id}/payload`,
        {
          method: 'PATCH',
          body: {
            payload: { ...editPayload.value },
            actor: 'admin',
            reason: editReason.value.trim(),
          },
        },
      )
      const idx = candidates.value.findIndex(c => c.candidate_id === target.candidate_id)
      if (idx >= 0 && res.data) {
        candidates.value[idx] = res.data
        detailTarget.value = res.data
      }
      editing.value = false
      try {
        const revRes = await api<{ ok: boolean, data: Revision[] }>(
          `/api/admin/memory_consolidator/candidates/${target.candidate_id}/revisions?limit=100`,
        )
        revisions.value = revRes.data || []
      } catch {
        // swallow — revisions are best-effort
      }
    } catch (e: any) {
      console.error('[memory fold-in] submitEdit failed', e)
    } finally {
      editSubmitting.value = false
    }
  }

  function openDecide(c: Candidate, action: 'approved' | 'rejected') {
    decideTarget.value = c
    decideAction.value = action
    decideReason.value = ''
  }

  function closeDecide() {
    decideTarget.value = null
    decideReason.value = ''
  }

  async function submitDecide() {
    if (!decideTarget.value) return
    const target = decideTarget.value
    const action = decideAction.value
    decideSubmitting.value = true
    try {
      await api<{ ok: boolean, data: any }>(
        `/api/admin/memory_consolidator/candidates/${target.candidate_id}/decide`,
        {
          method: 'POST',
          body: {
            state: action,
            decided_by: 'admin',
            reason: decideReason.value.trim(),
          },
        },
      )
      closeDecide()
      await fetchCandidates()
      if (detailTarget.value && detailTarget.value.candidate_id === target.candidate_id) {
        const refreshed = candidates.value.find(c => c.candidate_id === target.candidate_id)
        if (refreshed) detailTarget.value = refreshed
      }
    } catch (e: any) {
      console.error('[memory fold-in] submitDecide failed', e)
    } finally {
      decideSubmitting.value = false
    }
  }

  return {
    loading,
    candidates,
    filterDomain,
    filterState,
    filterGroup,
    filteredCandidates,
    stats,
    detailTarget,
    revisions,
    revisionsLoading,
    editing,
    editPayload,
    editReason,
    editSubmitting,
    decideTarget,
    decideAction,
    decideReason,
    decideSubmitting,
    canEdit,
    fetchCandidates,
    openDetail,
    closeDetail,
    startEdit,
    cancelEdit,
    submitEdit,
    openDecide,
    closeDecide,
    submitDecide,
  }
}

export type MemoryConsole = ReturnType<typeof createMemoryConsole>

export const MEMORY_CONSOLE_KEY: InjectionKey<MemoryConsole> = Symbol('memory-console')

export function useMemoryConsoleInject(): MemoryConsole {
  const value = inject(MEMORY_CONSOLE_KEY)
  if (!value) {
    throw new Error('MemoryFoldInProvider must wrap any memory fold-in slot consumer')
  }
  return value
}

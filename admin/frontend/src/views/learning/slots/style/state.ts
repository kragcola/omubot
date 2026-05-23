import { type InjectionKey, inject, ref } from 'vue'
import { api } from '../../../../api/client'
import type { LearningStageKey } from '../../types'

export type StyleStatus = 'pending' | 'approved' | 'rejected' | 'muted'
export type StyleScope = 'group' | 'global'
export type OutputPolicy = 'allow_use' | 'transform' | 'observe_only'

export interface StyleSummary {
  total: number
  pending: number
  approved: number
  rejected: number
  muted: number
  feedback_count: number
  profile_count: number
  enabled_profile_count: number
}

export interface NormalizationInfo {
  cluster_id: string
  item_id?: string
  canonical_text?: string
  normalized_key?: string
  method?: string
  score?: number
  auto_merged?: boolean
  features?: Record<string, any>
}

export interface NormalizerItem {
  item_id: string
  raw_text: string
  count: number
  last_seen_at: string
}

export interface NormalizerRevision {
  revision_id: string
  action: string
  item_id?: string
  created_at: string
}

export interface NormalizerClusterDetail {
  cluster?: Record<string, any>
  items: NormalizerItem[]
  revisions: NormalizerRevision[]
}

export interface StyleExpression {
  expression_id: string
  situation: string
  style: string
  scope: StyleScope
  group_id: string
  status: StyleStatus
  confidence: number
  count: number
  risk_tags: string[]
  output_policy: OutputPolicy
  updated_at: string
  normalization?: NormalizationInfo | null
  meta?: Record<string, any>
}

export interface StyleProfile {
  profile_id: string
  scope: StyleScope
  group_id: string
  version: number
  status: 'draft' | 'enabled' | 'disabled'
  content: string
  risk_notes: string[]
  created_at: string
}

export interface StyleFeedback {
  feedback_id: string
  target_type: string
  group_id: string
  rating: 'positive' | 'negative' | 'neutral'
  source: string
  raw_text: string
  context: string
  created_at: string
}

export interface StyleExtractGroupResult {
  group_id: string
  scanned: number
  text_scanned: number
  raw_scanned: number
  batches: number
  backlog_raw: number
  backlog_text: number
  has_more: boolean
  extracted: number
  filtered: number
  saved: number
  approved: number
  pending: number
  expression_ids: string[]
  error?: string
}

export interface StyleExtractResult {
  ok: boolean
  error?: string
  groups: string[]
  scope: StyleScope
  scanned: number
  text_scanned: number
  raw_scanned: number
  backlog_raw: number
  backlog_text: number
  has_more: boolean
  batch_limit: number
  max_batches: number
  target_text_rows: number
  extracted: number
  filtered: number
  saved: number
  approved: number
  pending: number
  expression_ids: string[]
  per_group: StyleExtractGroupResult[]
}

export function stageToStyleStatus(stage: LearningStageKey): StyleStatus | 'archived' | 'all' {
  switch (stage) {
    case 'candidate':
    case 'review':
      return 'pending'
    case 'approved':
      return 'approved'
    case 'archived':
      return 'archived'
    case 'hits':
    default:
      return 'all'
  }
}

export function createStyleConsole() {
  const loading = ref(false)
  const actionLoading = ref(false)

  const groupId = ref('')
  const stageStatusFilter = ref<StyleStatus | 'archived' | 'all'>('pending')
  const scopeFilter = ref<StyleScope | ''>('')
  const sortMode = ref<'default' | 'time'>('default')

  const summary = ref<StyleSummary>({
    total: 0,
    pending: 0,
    approved: 0,
    rejected: 0,
    muted: 0,
    feedback_count: 0,
    profile_count: 0,
    enabled_profile_count: 0,
  })
  const expressions = ref<StyleExpression[]>([])
  const profiles = ref<StyleProfile[]>([])
  const feedback = ref<StyleFeedback[]>([])
  const lastExtractResult = ref<StyleExtractResult | null>(null)
  const normalizerDetails = ref<Record<string, NormalizerClusterDetail>>({})

  function statusForRequest(): StyleStatus | '' | 'archived' {
    if (stageStatusFilter.value === 'all') return ''
    return stageStatusFilter.value
  }

  async function loadAll() {
    loading.value = true
    try {
      const reqStatus = statusForRequest()
      const expressionStatus: StyleStatus | '' = reqStatus === 'archived' ? '' : reqStatus
      const [summaryResp, expressionResp, profileResp, feedbackResp] = await Promise.all([
        api<StyleSummary>('/api/admin/style/summary'),
        api<{ expressions: StyleExpression[] }>('/api/admin/style/expressions', {
          params: {
            status: expressionStatus,
            scope: scopeFilter.value,
            group_id: groupId.value.trim(),
            sort: sortMode.value,
            limit: 80,
          },
        }),
        api<{ profiles: StyleProfile[] }>('/api/admin/style/profiles', {
          params: {
            group_id: groupId.value.trim(),
            sort: sortMode.value,
            limit: 20,
          },
        }),
        api<{ feedback: StyleFeedback[] }>('/api/admin/style/feedback', {
          params: {
            group_id: groupId.value.trim(),
            sort: sortMode.value,
            limit: 30,
          },
        }),
      ])
      summary.value = summaryResp
      let expr = expressionResp.expressions || []
      if (reqStatus === 'archived') {
        expr = expr.filter(item => item.status === 'rejected' || item.status === 'muted')
      }
      expressions.value = expr
      profiles.value = profileResp.profiles || []
      feedback.value = feedbackResp.feedback || []
    } catch (error) {
      console.error('[style fold-in] loadAll failed', error)
    } finally {
      loading.value = false
    }
  }

  async function setStatus(item: StyleExpression, status: StyleStatus) {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/style/expressions/${item.expression_id}/status`,
        { method: 'POST', body: { status } },
      )
      if (!resp.ok) throw new Error(resp.error || '操作失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function sendFeedback(item: StyleExpression, rating: 'positive' | 'negative') {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/style/expressions/${item.expression_id}/feedback`,
        { method: 'POST', body: { rating } },
      )
      if (!resp.ok) throw new Error(resp.error || '操作失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function loadNormalizerDetail(info?: NormalizationInfo | null) {
    if (!info?.cluster_id || normalizerDetails.value[info.cluster_id]) return
    try {
      const data = await api<NormalizerClusterDetail>(
        `/api/admin/learning-normalizer/clusters/${info.cluster_id}/items`,
      )
      normalizerDetails.value = {
        ...normalizerDetails.value,
        [info.cluster_id]: data,
      }
    } catch (error) {
      console.error('[style fold-in] loadNormalizerDetail failed', error)
    }
  }

  function normalizerDetail(info?: NormalizationInfo | null) {
    return info?.cluster_id ? normalizerDetails.value[info.cluster_id] : undefined
  }

  async function lockNormalizerCluster(item: StyleExpression) {
    const info = item.normalization
    if (!info?.cluster_id) return
    const canonical = window.prompt('锁定代表写法', info.canonical_text || item.situation)
    if (!canonical) return
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/learning-normalizer/clusters/${info.cluster_id}/lock`,
        { method: 'POST', body: { canonical_text: canonical, reason: 'style fold-in lock' } },
      )
      if (!resp.ok) throw new Error(resp.error || '锁定失败')
      delete normalizerDetails.value[info.cluster_id]
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function splitNormalizerItem(item: StyleExpression) {
    const info = item.normalization
    if (!info?.item_id || !window.confirm('确认把当前写法拆出归一化簇？')) return
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/learning-normalizer/items/${info.item_id}/split`,
        { method: 'POST', body: { reason: 'style fold-in split' } },
      )
      if (!resp.ok) throw new Error(resp.error || '拆分失败')
      if (info.cluster_id) delete normalizerDetails.value[info.cluster_id]
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function undoNormalizerAutoMerge(item: StyleExpression) {
    const info = item.normalization
    await loadNormalizerDetail(info)
    const detail = normalizerDetail(info)
    const revision = detail?.revisions.find(entry => entry.action === 'auto_merge' && entry.item_id === info?.item_id)
      || detail?.revisions.find(entry => entry.action === 'auto_merge')
    if (!info?.cluster_id || !revision || !window.confirm('确认撤销最近一次自动归并？')) return
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/learning-normalizer/revisions/${revision.revision_id}/undo`,
        { method: 'POST' },
      )
      if (!resp.ok) throw new Error(resp.error || '撤销失败')
      delete normalizerDetails.value[info.cluster_id]
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function runExtract() {
    actionLoading.value = true
    try {
      const resp = await api<StyleExtractResult>('/api/admin/style/extract/run', {
        method: 'POST',
        body: {
          group_id: groupId.value.trim(),
          scope: scopeFilter.value || 'group',
          auto_approve: false,
        },
      })
      lastExtractResult.value = resp
      if (!resp.ok) throw new Error(resp.error || '抽取失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function generateProfile() {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>('/api/admin/style/profiles/generate', {
        method: 'POST',
        body: {
          group_id: groupId.value.trim(),
          scope: scopeFilter.value || 'group',
          enable: true,
        },
      })
      if (!resp.ok) throw new Error(resp.error || '生成失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function disableProfile(profile: StyleProfile) {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/style/profiles/${profile.profile_id}/disable`,
        { method: 'POST', body: {} },
      )
      if (!resp.ok) throw new Error(resp.error || '禁用失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function enableProfile(profile: StyleProfile) {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>(
        `/api/admin/style/profiles/${profile.profile_id}/enable`,
        { method: 'POST', body: {} },
      )
      if (!resp.ok) throw new Error(resp.error || '启用失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  async function rollbackProfile(profile: StyleProfile) {
    actionLoading.value = true
    try {
      const resp = await api<{ ok: boolean, error?: string }>('/api/admin/style/profiles/rollback', {
        method: 'POST',
        body: {
          scope: profile.scope,
          group_id: profile.group_id,
        },
      })
      if (!resp.ok) throw new Error(resp.error || '回滚失败')
      await loadAll()
    } finally {
      actionLoading.value = false
    }
  }

  return {
    loading,
    actionLoading,
    groupId,
    stageStatusFilter,
    scopeFilter,
    sortMode,
    summary,
    expressions,
    profiles,
    feedback,
    lastExtractResult,
    normalizerDetails,
    loadAll,
    setStatus,
    sendFeedback,
    runExtract,
    generateProfile,
    enableProfile,
    disableProfile,
    rollbackProfile,
    lockNormalizerCluster,
    splitNormalizerItem,
    undoNormalizerAutoMerge,
    loadNormalizerDetail,
    normalizerDetail,
  }
}

export type StyleConsole = ReturnType<typeof createStyleConsole>

export const STYLE_CONSOLE_KEY: InjectionKey<StyleConsole> = Symbol('style-console')

export function useStyleConsoleInject(): StyleConsole {
  const value = inject(STYLE_CONSOLE_KEY)
  if (!value) {
    throw new Error('StyleFoldInProvider must wrap any style fold-in slot consumer')
  }
  return value
}

/**
 * Slang console state machine.
 *
 * Lifted out of SlangView.vue so the same logic can power /slang and the
 * /learning?noun=slang fold-in slots without duplicating refs / handlers.
 */

import { useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { api } from '../../../api/client'
import { statusLabel } from '../helpers/badges'
import {
  DEFAULT_SLANG_SETTINGS,
  mergeSettings as mergeSlangSettings,
} from '../helpers/formatters'
import type {
  SlangCreateDraft,
  SlangDriftReview,
  SlangObservation,
  SlangPendingCandidate,
  SlangQueueMode,
  SlangRevision,
  SlangSettings,
  SlangStats,
  SlangSummary,
  SlangTerm,
} from '../helpers/types'

export type SlangQuickStatusAction = 'approve' | 'mute' | 'expire'
export type SlangAiReviewAction = 'human-approve' | 'deny' | 'return-candidate'
export type SlangBulkAction = 'approve' | 'mute' | 'expire' | 'delete_observations'
export type SlangDriftAction = 'accept' | 'reject' | 'alias' | 'mute'

export interface UseSlangConsoleOptions {
  initialQueueMode?: SlangQueueMode
}

export function useSlangConsole(options: UseSlangConsoleOptions = {}) {
  const message = useMessage()

  const summary = ref<SlangSummary>({
    candidate_count: 0,
    candidate_unreviewed_count: 0,
    approved_count: 0,
    muted_count: 0,
    expired_count: 0,
    pending_count: 0,
    drift_count: 0,
    ai_review_count: 0,
    ai_pending_review_count: 0,
    under_observation_count: 0,
    ai_rejected_count: 0,
    human_reviewed_count: 0,
    eligible_backlog_count: 0,
    today_hits: 0,
    group_count: 0,
    last_extracted_at: '',
    last_daily_ai_review_at: '',
    latest_run_status: '',
  })
  const stats = ref<SlangStats | null>(null)
  const terms = ref<SlangTerm[]>([])
  const total = ref(0)
  const pendingCandidates = ref<SlangPendingCandidate[]>([])
  const pendingTotal = ref(0)
  const driftReviews = ref<SlangDriftReview[]>([])
  const driftTotal = ref(0)
  const groups = ref<string[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const extracting = ref(false)
  const scanningGlobal = ref(false)
  const bulkLoading = ref(false)
  const driftBacklogLoading = ref(false)
  const savingSettings = ref(false)
  const settingsDrawerVisible = ref(false)
  const runningAiReview = ref(false)
  const createDrawerVisible = ref(false)
  const creatingTerm = ref(false)
  const page = ref(1)

  const searchText = ref('')
  const groupFilter = ref('')
  const queueMode = ref<SlangQueueMode>(options.initialQueueMode ?? 'candidate')
  const minConfidence = ref('')
  const sortBy = ref<'updated_desc' | 'confidence_desc' | 'usage_desc' | 'updated_asc'>('updated_desc')
  const selectedTermIds = ref<string[]>([])

  const settings = ref<SlangSettings>({ ...DEFAULT_SLANG_SETTINGS })
  const allowlistText = ref('')
  const stoplistText = ref('')
  const pageSize = computed(() => Math.max(10, Math.min(200, Number(settings.value.bulk_page_size || 50))))
  const createDraft = ref<SlangCreateDraft>({
    term: '',
    meaning: '',
    aliases: '',
    scope: 'group',
    group_id: '',
    confidence: 0.8,
    status: 'approved',
    repeat_policy: 'understand_only',
    notes: '',
    evidence: '',
  })

  const drawerVisible = ref(false)
  const detailLoading = ref(false)
  const detailTerm = ref<SlangTerm | null>(null)
  const observations = ref<SlangObservation[]>([])
  const revisions = ref<SlangRevision[]>([])
  const editAliases = ref('')
  const mergeTargetId = ref('')
  const mergeSearchText = ref('')
  const mergeLoading = ref(false)
  const mergeCandidates = ref<SlangTerm[]>([])

  const displayTotal = computed(() => queueMode.value === 'drift' ? driftTotal.value : total.value)
  const pendingCount = computed(() =>
    summary.value.candidate_unreviewed_count
    + summary.value.under_observation_count
    + summary.value.drift_count,
  )
  const pageCount = computed(() => Math.max(1, Math.ceil(displayTotal.value / pageSize.value)))
  const mergeOptions = computed(() => mergeCandidates.value
    .filter(term => term.term_id !== detailTerm.value?.term_id)
    .map(term => ({
      label: `${term.term} · ${term.scope === 'global' ? '全局' : `群 ${term.group_id}`} · ${statusLabel(term.status)}`,
      value: term.term_id,
    })))

  watch([searchText, groupFilter, queueMode, minConfidence, sortBy], () => {
    page.value = 1
    selectedTermIds.value = []
    void loadTerms(true)
    void loadDriftReviews()
    void loadPending()
  })

  watch(page, () => {
    if (queueMode.value === 'drift') {
      void loadDriftReviews()
      return
    }
    void loadTerms(true)
  })

  function buildParams() {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (groupFilter.value) params.group_id = groupFilter.value
    if (queueMode.value === 'candidate') {
      params.review_filter = 'pending_all'
    }
    if (queueMode.value === 'approved') params.status = 'approved'
    if (queueMode.value === 'ai_rejected') {
      params.status = 'muted'
      params.review_filter = 'ai_rejected_only'
    }
    if (queueMode.value === 'pending_human_review') {
      params.status = 'approved'
      params.review_filter = 'needs_human_review'
    }
    if (queueMode.value === 'archived') {
      params.review_filter = 'archived_only'
    }
    if (searchText.value.trim()) params.search = searchText.value.trim()
    if (minConfidence.value) params.min_confidence = Number(minConfidence.value)
    if (sortBy.value && sortBy.value !== 'updated_desc') params.sort_by = sortBy.value
    return params
  }

  async function loadAll(silent = false) {
    if (silent) refreshing.value = true
    else loading.value = true
    try {
      await Promise.all([
        loadSummary(),
        loadGroups(),
        loadSettings(),
        loadTerms(true),
        loadDriftReviews(),
        loadStats(),
        loadPending(),
      ])
    } finally {
      loading.value = false
      refreshing.value = false
    }
  }

  async function loadSummary() {
    const data = await api('/api/admin/slang/summary')
    summary.value = { ...summary.value, ...data }
  }

  async function loadGroups() {
    const data = await api('/api/admin/slang/groups')
    groups.value = data.groups || []
  }

  async function loadStats() {
    const data = await api('/api/admin/slang/stats')
    stats.value = data
  }

  async function loadPending() {
    const data = await api('/api/admin/slang/pending', {
      params: {
        group_id: groupFilter.value || undefined,
        search: searchText.value.trim() || undefined,
        page_size: 6,
      },
    })
    pendingCandidates.value = data.pending || []
    pendingTotal.value = Number(data.total || 0)
  }

  async function loadDriftReviews() {
    const data = await api('/api/admin/slang/drift', {
      params: {
        status: 'open',
        group_id: groupFilter.value || undefined,
        search: searchText.value.trim() || undefined,
        page: queueMode.value === 'drift' ? page.value : 1,
        page_size: queueMode.value === 'drift' ? pageSize.value : 4,
      },
    })
    driftReviews.value = data.reviews || []
    driftTotal.value = Number(data.total || 0)
  }

  async function loadSettings() {
    const data = await api('/api/admin/slang/settings')
    settings.value = mergeSlangSettings(data.settings || {}, settings.value)
    allowlistText.value = settings.value.group_allowlist.join('\n')
    stoplistText.value = settings.value.stoplist.join('\n')
  }

  async function loadTerms(silent = false) {
    if (queueMode.value === 'drift') {
      terms.value = []
      selectedTermIds.value = []
      return
    }
    if (!silent) loading.value = true
    try {
      const data = await api('/api/admin/slang/terms', { params: buildParams() })
      terms.value = data.terms || []
      total.value = Number(data.total || 0)
      selectedTermIds.value = selectedTermIds.value.filter(id => terms.value.some(term => term.term_id === id))
    } catch (error) {
      console.error('Failed to load slang terms:', error)
      message.error('黑话列表加载失败')
    } finally {
      if (!silent) loading.value = false
    }
  }

  async function runExtract() {
    extracting.value = true
    try {
      const data = await api('/api/admin/slang/extract/run', {
        method: 'POST',
        body: {
          group_id: groupFilter.value || undefined,
          limit: settings.value.extraction_batch_limit,
        },
      })
      if (!data.ok) {
        message.error(data.error || '手动抽取失败')
        return
      }
      message.success(`已扫描 ${data.scanned || 0} 条消息，抽取 ${data.extracted || 0} 个，进入审核 ${data.candidates || 0} 个`)
      await loadAll(true)
    } catch (error) {
      console.error('Failed to run slang extraction:', error)
      message.error('手动抽取失败')
    } finally {
      extracting.value = false
    }
  }

  async function runForceAiReview() {
    if (runningAiReview.value) return
    runningAiReview.value = true
    try {
      const data = await api('/api/admin/slang/ai-review/run', {
        method: 'POST',
        body: {},
      })
      if (!data.ok) {
        message.error(data.error || 'AI 清池启动失败')
        return
      }
      message.success('AI 清池已启动，进度见下方面板')
      setTimeout(() => loadSummary(), 5000)
    } catch (error: any) {
      const msg = error?.data?.error || error?.message || 'AI 清池启动失败'
      message.error(msg)
    } finally {
      runningAiReview.value = false
    }
  }

  async function runGlobalScan() {
    scanningGlobal.value = true
    try {
      const data = await api('/api/admin/slang/global/scan', {
        method: 'POST',
        body: { min_groups: settings.value.global_promote_min_groups },
      })
      if (!data.ok) {
        message.error(data.error || '跨群候选扫描失败')
        return
      }
      message.success(`已生成 ${data.created || 0} 个跨群候选，跳过 ${data.skipped || 0} 个重复项`)
      queueMode.value = 'candidate'
      await loadAll(true)
    } catch (error) {
      console.error('Failed to scan global slang candidates:', error)
      message.error('跨群候选扫描失败')
    } finally {
      scanningGlobal.value = false
    }
  }

  function resetCreateDraft() {
    createDraft.value = {
      term: '',
      meaning: '',
      aliases: '',
      scope: 'group',
      group_id: groupFilter.value || '',
      confidence: 0.8,
      status: 'approved',
      repeat_policy: settings.value.repeat_policy,
      notes: '',
      evidence: '',
    }
  }

  function openCreateDrawer() {
    resetCreateDraft()
    createDrawerVisible.value = true
  }

  async function saveCreateTerm() {
    const draft = createDraft.value
    if (!draft.term.trim()) {
      message.warning('请填写黑话词')
      return
    }
    if (draft.scope === 'group' && !draft.group_id.trim()) {
      message.warning('群内黑话需要填写群号')
      return
    }
    creatingTerm.value = true
    try {
      const data = await api('/api/admin/slang/terms/create', {
        method: 'POST',
        body: {
          term: draft.term.trim(),
          meaning: draft.meaning.trim(),
          aliases: draft.aliases.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
          scope: draft.scope,
          group_id: draft.scope === 'global' ? '' : draft.group_id.trim(),
          confidence: draft.confidence,
          status: draft.status,
          repeat_policy: draft.repeat_policy,
          notes: draft.notes.trim(),
          evidence: draft.evidence.trim(),
        },
      })
      if (!data.ok) {
        message.error(data.error || '创建失败')
        return
      }
      message.success('黑话已创建')
      createDrawerVisible.value = false
      queueMode.value = draft.status === 'candidate'
        ? 'candidate'
        : draft.status === 'approved'
          ? 'approved'
          : 'all'
      groupFilter.value = draft.scope === 'group' ? draft.group_id.trim() : ''
      await Promise.all([loadSummary(), loadStats(), loadGroups(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to create slang term:', error)
      message.error('创建失败')
    } finally {
      creatingTerm.value = false
    }
  }

  async function quickStatus(term: SlangTerm, action: SlangQuickStatusAction) {
    try {
      const data = await api(`/api/admin/slang/terms/${term.term_id}/${action}`, { method: 'POST' })
      if (!data.ok) {
        message.error(data.error || '状态更新失败')
        return
      }
      message.success(action === 'approve' ? '已批准' : action === 'mute' ? '已静音' : '已过期')
      await Promise.all([loadSummary(), loadTerms(true)])
      if (detailTerm.value?.term_id === term.term_id) {
        detailTerm.value = data.term || detailTerm.value
        await loadRevisions(term.term_id)
      }
    } catch (error) {
      console.error('Failed to update slang status:', error)
      message.error('状态更新失败')
    }
  }

  async function reviewAiTerm(term: SlangTerm, action: SlangAiReviewAction) {
    const label = {
      'human-approve': '真实通过',
      deny: '否决并静音',
      'return-candidate': '退回候选',
    }[action]
    if (action !== 'human-approve' && !window.confirm(`确认${label}“${term.term}”？`)) return
    try {
      const data = await api(`/api/admin/slang/terms/${term.term_id}/${action}`, { method: 'POST' })
      if (!data.ok) {
        message.error(data.error || `${label}失败`)
        return
      }
      message.success(`${label}完成`)
      if (detailTerm.value?.term_id === term.term_id) {
        detailTerm.value = data.term || detailTerm.value
        await loadRevisions(term.term_id)
      }
      await Promise.all([loadSummary(), loadStats(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to review AI slang term:', error)
      message.error(`${label}失败`)
    }
  }

  async function openDetail(term: SlangTerm) {
    drawerVisible.value = true
    detailLoading.value = true
    detailTerm.value = { ...term }
    editAliases.value = term.aliases.join('\n')
    observations.value = []
    revisions.value = []
    mergeTargetId.value = ''
    mergeSearchText.value = ''
    mergeCandidates.value = terms.value.filter(item => item.term_id !== term.term_id)
    try {
      const data = await api(`/api/admin/slang/terms/${term.term_id}`)
      detailTerm.value = data.term || detailTerm.value
      editAliases.value = detailTerm.value?.aliases.join('\n') || ''
      observations.value = data.observations || []
      await loadRevisions(term.term_id)
      await loadMergeCandidates()
    } catch (error) {
      console.error('Failed to load slang detail:', error)
      message.error('黑话详情加载失败')
    } finally {
      detailLoading.value = false
    }
  }

  async function loadRevisions(termId: string) {
    try {
      const data = await api(`/api/admin/slang/terms/${termId}/revisions`)
      revisions.value = data.revisions || []
    } catch (error) {
      console.error('Failed to load slang revisions:', error)
      revisions.value = []
    }
  }

  async function loadMergeCandidates(query = '') {
    mergeLoading.value = true
    try {
      const data = await api('/api/admin/slang/terms', {
        params: {
          search: query || undefined,
          page_size: 20,
        },
      })
      mergeCandidates.value = data.terms || []
    } catch (error) {
      console.error('Failed to load merge candidates:', error)
    } finally {
      mergeLoading.value = false
    }
  }

  async function saveDetail() {
    if (!detailTerm.value) return
    try {
      const data = await api(`/api/admin/slang/terms/${detailTerm.value.term_id}`, {
        method: 'POST',
        body: {
          term: detailTerm.value.term,
          meaning: detailTerm.value.meaning,
          aliases: editAliases.value.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
          scope: detailTerm.value.scope,
          group_id: detailTerm.value.group_id,
          confidence: detailTerm.value.confidence,
          status: detailTerm.value.status,
          repeat_policy: detailTerm.value.repeat_policy,
          notes: detailTerm.value.notes,
        },
      })
      if (!data.ok) {
        message.error(data.error || '保存失败')
        return
      }
      message.success('已保存')
      detailTerm.value = data.term || detailTerm.value
      if (detailTerm.value) await loadRevisions(detailTerm.value.term_id)
      await Promise.all([loadSummary(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to save slang detail:', error)
      message.error('保存失败')
    }
  }

  async function recomputeConfidence() {
    if (!detailTerm.value) return
    try {
      const data = await api(`/api/admin/slang/terms/${detailTerm.value.term_id}/recompute-confidence`, { method: 'POST' })
      if (!data.ok) {
        message.error(data.error || '置信度重算失败')
        return
      }
      detailTerm.value = data.term || detailTerm.value
      if (detailTerm.value) await loadRevisions(detailTerm.value.term_id)
      message.success('置信度已重算')
      await Promise.all([loadStats(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to recompute slang confidence:', error)
      message.error('置信度重算失败')
    }
  }

  async function mergeCurrentIntoTarget() {
    if (!detailTerm.value || !mergeTargetId.value) return
    if (!window.confirm('确认把当前词条合并到目标词条？当前词条会标记为已过期。')) return
    try {
      const data = await api('/api/admin/slang/terms/merge', {
        method: 'POST',
        body: {
          target_id: mergeTargetId.value,
          source_ids: [detailTerm.value.term_id],
        },
      })
      if (!data.ok) {
        message.error(data.error || '合并失败')
        return
      }
      detailTerm.value = data.term || detailTerm.value
      mergeTargetId.value = ''
      if (detailTerm.value) await loadRevisions(detailTerm.value.term_id)
      message.success('已合并到主词条')
      await Promise.all([loadSummary(), loadStats(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to merge slang terms:', error)
      message.error('合并失败')
    }
  }

  async function saveSettings() {
    const times = settings.value.daily_ai_review_times || []
    if (times.some((t: string) => !/^\d{2}:\d{2}$/.test(t))) {
      message.warning('AI 清池时段请使用 HH:MM 格式')
      return
    }
    savingSettings.value = true
    try {
      const payload = {
        ...settings.value,
        group_allowlist: allowlistText.value.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
        stoplist: stoplistText.value.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
      }
      const data = await api('/api/admin/slang/settings', {
        method: 'POST',
        body: { settings: payload },
      })
      if (!data.ok) {
        message.error(data.error || '保存设置失败')
        return
      }
      settings.value = mergeSlangSettings(data.settings || {}, payload)
      allowlistText.value = settings.value.group_allowlist.join('\n')
      stoplistText.value = settings.value.stoplist.join('\n')
      message.success('设置已保存')
      await Promise.all([loadStats(), loadTerms(true), loadPending(), loadDriftReviews()])
    } catch (error) {
      console.error('Failed to save slang settings:', error)
      message.error('保存设置失败')
    } finally {
      savingSettings.value = false
    }
  }

  function resetFilters() {
    searchText.value = ''
    groupFilter.value = ''
    queueMode.value = 'candidate'
    minConfidence.value = ''
    sortBy.value = 'updated_desc'
  }

  function setQueueMode(value: SlangQueueMode) {
    if (queueMode.value === value) return
    queueMode.value = value
  }

  async function runBulkAction(action: SlangBulkAction) {
    if (!selectedTermIds.value.length) return
    const label = {
      approve: '批量批准',
      mute: '批量静音',
      expire: '批量过期',
      delete_observations: '删除观察记录',
    }[action]
    if (!window.confirm(`确认${label} ${selectedTermIds.value.length} 个词条？`)) return
    bulkLoading.value = true
    try {
      const data = await api('/api/admin/slang/terms/bulk', {
        method: 'POST',
        body: {
          action,
          term_ids: selectedTermIds.value,
        },
      })
      if (!data.ok) {
        message.error(data.error || '批量操作失败')
        return
      }
      message.success(`${label}完成`)
      selectedTermIds.value = []
      await Promise.all([loadSummary(), loadStats(), loadTerms(true)])
    } catch (error) {
      console.error('Failed to run slang bulk action:', error)
      message.error('批量操作失败')
    } finally {
      bulkLoading.value = false
    }
  }

  async function handleDriftAction(drift: SlangDriftReview, action: SlangDriftAction) {
    const label = {
      accept: '采纳新释义',
      reject: '保留旧释义',
      alias: '转成别名',
      mute: '静音词条',
    }[action]
    if (action !== 'accept' && !window.confirm(`确认${label}“${drift.term}”？`)) return
    try {
      const data = await api(`/api/admin/slang/drift/${drift.drift_id}/${action}`, { method: 'POST' })
      if (!data.ok) {
        message.error(data.error || `${label}失败`)
        return
      }
      message.success(`${label}完成`)
      await Promise.all([loadSummary(), loadDriftReviews(), loadTerms(true), loadStats()])
      if (detailTerm.value?.term_id === drift.term_id) {
        const detail = await api(`/api/admin/slang/terms/${drift.term_id}`)
        detailTerm.value = detail.term || detailTerm.value
        observations.value = detail.observations || observations.value
        await loadRevisions(drift.term_id)
      }
    } catch (error) {
      console.error('Failed to handle slang drift review:', error)
      message.error(`${label}失败`)
    }
  }

  async function processDriftBacklog() {
    if (driftBacklogLoading.value) return
    driftBacklogLoading.value = true
    try {
      const data = await api('/api/admin/slang/drift/process-backlog', { method: 'POST', body: { limit: 200 } })
      if (!data.ok) {
        message.error(data.error || 'AI 复核失败')
        return
      }
      const r = data.result || {}
      const dismissed = Number(r.dismissed || 0)
      const aliased = Number(r.aliased || 0)
      const kept = Number(r.kept_real_drift || 0)
      const errors = Number(r.errors || 0)
      const skipped = Number(r.skipped || 0)
      const parts: string[] = []
      if (dismissed) parts.push(`同义归档 ${dismissed}`)
      if (aliased) parts.push(`并入别名 ${aliased}`)
      if (kept) parts.push(`保留真漂移 ${kept}`)
      if (skipped) parts.push(`已复核跳过 ${skipped}`)
      if (errors) parts.push(`异常 ${errors}`)
      message.success(parts.length ? `AI 复核完成 · ${parts.join('，')}` : 'AI 复核完成（无变化）')
      await Promise.all([loadSummary(), loadDriftReviews(), loadTerms(true), loadStats()])
    } catch (error: any) {
      console.error('Failed to process drift backlog:', error)
      message.error(error?.data?.error || error?.message || 'AI 复核失败')
    } finally {
      driftBacklogLoading.value = false
    }
  }

  return {
    summary,
    stats,
    terms,
    total,
    pendingCandidates,
    pendingTotal,
    driftReviews,
    driftTotal,
    groups,
    loading,
    refreshing,
    extracting,
    scanningGlobal,
    bulkLoading,
    driftBacklogLoading,
    savingSettings,
    settingsDrawerVisible,
    runningAiReview,
    createDrawerVisible,
    creatingTerm,
    page,
    searchText,
    groupFilter,
    queueMode,
    minConfidence,
    sortBy,
    selectedTermIds,
    settings,
    allowlistText,
    stoplistText,
    pageSize,
    createDraft,
    drawerVisible,
    detailLoading,
    detailTerm,
    observations,
    revisions,
    editAliases,
    mergeTargetId,
    mergeSearchText,
    mergeLoading,
    mergeCandidates,
    displayTotal,
    pendingCount,
    pageCount,
    mergeOptions,
    loadAll,
    loadSummary,
    loadGroups,
    loadStats,
    loadPending,
    loadDriftReviews,
    loadSettings,
    loadTerms,
    runExtract,
    runForceAiReview,
    runGlobalScan,
    openCreateDrawer,
    saveCreateTerm,
    quickStatus,
    reviewAiTerm,
    openDetail,
    saveDetail,
    recomputeConfidence,
    mergeCurrentIntoTarget,
    saveSettings,
    resetFilters,
    setQueueMode,
    runBulkAction,
    handleDriftAction,
    processDriftBacklog,
    loadMergeCandidates,
  }
}

export type SlangConsole = ReturnType<typeof useSlangConsole>

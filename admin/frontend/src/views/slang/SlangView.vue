<script setup lang="ts">
import {
  PricetagsOutline,
  RefreshOutline,
  SettingsOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import SlangStatsCards from './components/SlangStatsCards.vue'
import SlangBacklogProgress from './components/SlangBacklogProgress.vue'
import SlangExtractionProgress from './components/SlangExtractionProgress.vue'
import SlangCreateDrawer from './components/SlangCreateDrawer.vue'
import SlangDetailDrawer from './components/SlangDetailDrawer.vue'
import SlangQueueToolbar from './components/SlangQueueToolbar.vue'
import SlangSettingsDrawer from './components/SlangSettingsDrawer.vue'
import SlangSnapshotStrip from './components/SlangSnapshotStrip.vue'
import SlangSummaryBar from './components/SlangSummaryBar.vue'
import SlangTermList from './components/SlangTermList.vue'
import { statusLabel } from './helpers/badges'
import {
  DEFAULT_SLANG_SETTINGS,
  mergeSettings as mergeSlangSettings,
} from './helpers/formatters'
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
} from './helpers/types'

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
const queueMode = ref<SlangQueueMode>('candidate')
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
const slangCacheRevision = 'slang-console-v2_5-cache-recovery'

const displayTotal = computed(() => queueMode.value === 'drift' ? driftTotal.value : total.value)
const pendingCount = computed(() =>
  summary.value.candidate_unreviewed_count
  + summary.value.under_observation_count
  + summary.value.drift_count
)
const pageCount = computed(() => Math.max(1, Math.ceil(displayTotal.value / pageSize.value)))
const mergeOptions = computed(() => mergeCandidates.value
  .filter(term => term.term_id !== detailTerm.value?.term_id)
  .map(term => ({
    label: `${term.term} · ${term.scope === 'global' ? '全局' : `群 ${term.group_id}`} · ${statusLabel(term.status)}`,
    value: term.term_id,
  })))

onMounted(() => {
  void loadAll()
})

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

async function quickStatus(term: SlangTerm, action: 'approve' | 'mute' | 'expire') {
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

async function reviewAiTerm(term: SlangTerm, action: 'human-approve' | 'deny' | 'return-candidate') {
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

async function runBulkAction(action: 'approve' | 'mute' | 'expire' | 'delete_observations') {
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

async function handleDriftAction(drift: SlangDriftReview, action: 'accept' | 'reject' | 'alias' | 'mute') {
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
</script>

<template>
  <AppPage
    title="群内黑话"
    eyebrow="Slang Review"
    description="从群聊中学习候选黑话，人工审核后按群注入语境，帮助 Omubot 理解社群内部约定。"
  >
    <span class="slang-cache-revision" aria-hidden="true">
      {{ slangCacheRevision }}
    </span>

    <template #action>
      <NSpace align="center" :size="10">
        <NButton secondary size="small" :loading="refreshing" @click="loadAll(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <NButton secondary size="small" :loading="extracting" @click="runExtract">
          <template #icon>
            <NIcon :component="SparklesOutline" />
          </template>
          手动抽取
        </NButton>
        <NPopconfirm
          :positive-text="'确认执行'"
          :negative-text="'取消'"
          @positive-click="runForceAiReview"
        >
          <template #trigger>
            <NButton secondary size="small" :loading="runningAiReview">
              <template #icon>
                <NIcon :component="SparklesOutline" />
              </template>
              AI 清池
            </NButton>
          </template>
          将立即对所有启用的群跑一次 AI 审核（force=true，跳过当日去重），可能消耗较多 LLM 配额。是否继续？
        </NPopconfirm>
        <NButton secondary size="small" @click="openCreateDrawer">
          <template #icon>
            <NIcon :component="PricetagsOutline" />
          </template>
          新建黑话
        </NButton>
        <NButton quaternary size="small" @click="settingsDrawerVisible = true">
          <template #icon>
            <NIcon :component="SettingsOutline" />
          </template>
          设置
        </NButton>
      </NSpace>
    </template>

    <SlangSummaryBar :summary="summary" @switch-queue-mode="setQueueMode" />

    <SlangSnapshotStrip :summary="summary" />

    <div class="slang-main-layout">
      <div class="slang-main-layout__main">
        <SlangQueueToolbar
          v-model:search-text="searchText"
          v-model:group-filter="groupFilter"
          v-model:queue-mode="queueMode"
          v-model:min-confidence="minConfidence"
          v-model:sort-by="sortBy"
          :summary="summary"
          :groups="groups"
          :display-total="displayTotal"
          :scanning-global="scanningGlobal"
          @reset="resetFilters"
          @scan-global="runGlobalScan"
        />

        <NSkeleton v-if="loading" :repeat="8" text />

        <SlangTermList
          v-else
          v-model:page="page"
          v-model:selected-term-ids="selectedTermIds"
          :terms="terms"
          :drift-reviews="driftReviews"
          :queue-mode="queueMode"
          :page-count="pageCount"
          :bulk-loading="bulkLoading"
          :drift-backlog-loading="driftBacklogLoading"
          @open-detail="openDetail"
          @quick-status="quickStatus"
          @review-ai="reviewAiTerm"
          @drift-action="handleDriftAction"
          @bulk-action="runBulkAction"
          @drift-process-backlog="processDriftBacklog"
        />
      </div>

      <aside class="slang-main-layout__side">
        <SlangBacklogProgress :eligible-count="summary.eligible_backlog_count" @progress="loadSummary" />

        <SlangExtractionProgress />

        <SlangStatsCards
          v-if="!loading"
          :summary="summary"
          :stats="stats"
        />
      </aside>
    </div>

    <SlangCreateDrawer
      v-model:visible="createDrawerVisible"
      v-model:draft="createDraft"
      :creating-term="creatingTerm"
      @save="saveCreateTerm"
    />

    <SlangDetailDrawer
      v-model:visible="drawerVisible"
      v-model:detail-term="detailTerm"
      v-model:edit-aliases="editAliases"
      v-model:merge-target-id="mergeTargetId"
      v-model:merge-search-text="mergeSearchText"
      :detail-loading="detailLoading"
      :observations="observations"
      :revisions="revisions"
      :merge-options="mergeOptions"
      :merge-loading="mergeLoading"
      @save="saveDetail"
      @recompute-confidence="recomputeConfidence"
      @merge="mergeCurrentIntoTarget"
      @review-ai="reviewAiTerm"
      @search-merge="loadMergeCandidates"
    />

    <SlangSettingsDrawer
      v-model:visible="settingsDrawerVisible"
      v-model:settings="settings"
      v-model:allowlist-text="allowlistText"
      v-model:stoplist-text="stoplistText"
      :saving-settings="savingSettings"
      @save="saveSettings"
    />
  </AppPage>
</template>

<style scoped>
.slang-cache-revision {
  display: none;
}

.slang-main-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
  align-items: start;
}

.slang-main-layout__main {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.slang-main-layout__side {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 14px;
}

@media (max-width: 1100px) {
  .slang-main-layout {
    grid-template-columns: 1fr;
  }

  .slang-main-layout__side {
    position: static;
  }
}
</style>

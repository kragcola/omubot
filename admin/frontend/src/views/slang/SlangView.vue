<script setup lang="ts">
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  FlashOutline,
  PricetagsOutline,
  RefreshOutline,
  SearchOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppDrawerHeader from '../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import { recordSortOptions } from '../shared/sort'

type SlangStatus = 'candidate' | 'approved' | 'muted' | 'expired'
type RepeatPolicy = 'understand_only' | 'allow_rephrase' | 'allow_use'
type SlangQueueMode =
  | 'candidate'
  | 'candidate_ai_approved'
  | 'candidate_ai_rejected'
  | 'candidate_ai_unreviewed'
  | 'observe_more'
  | 'ai_review'
  | 'approved'
  | 'drift'
  | 'all'

interface SlangSummary {
  candidate_count: number
  candidate_total_count: number
  candidate_reviewed_count: number
  candidate_unreviewed_count: number
  candidate_review_approved_count: number
  candidate_review_rejected_count: number
  candidate_review_kept_count: number
  candidate_review_failed_count: number
  approved_count: number
  muted_count: number
  expired_count: number
  pending_count: number
  drift_count: number
  ai_review_count: number
  ai_pending_review_count: number
  today_hits: number
  group_count: number
  last_extracted_at: string
  last_daily_ai_review_at: string
  latest_run_status: string
}

interface SlangTerm {
  term_id: string
  term: string
  meaning: string
  aliases: string[]
  scope: 'group' | 'global'
  group_id: string
  confidence: number
  status: SlangStatus
  usage_count: number
  unique_user_count: number
  first_seen_at: string
  last_seen_at: string
  last_inferred_at?: string
  created_at?: string
  updated_at?: string
  source: string
  repeat_policy: RepeatPolicy
  notes: string
  meta?: Record<string, any>
  normalization?: NormalizationInfo | null
}

interface NormalizationInfo {
  cluster_id: string
  item_id?: string
  canonical_text?: string
  normalized_key?: string
  method?: string
  score?: number
  auto_merged?: boolean
  features?: Record<string, any>
}

interface NormalizerItem {
  item_id: string
  raw_text: string
  count: number
  last_seen_at: string
}

interface NormalizerRevision {
  revision_id: string
  action: string
  item_id?: string
  created_at: string
}

interface NormalizerClusterDetail {
  cluster?: Record<string, any>
  items: NormalizerItem[]
  revisions: NormalizerRevision[]
}

interface SlangObservation {
  observation_id: string
  group_id: string
  user_id: string
  message_id?: number
  raw_text: string
  context: string
  observed_at: string
  reason: string
}

interface SlangPendingCandidate {
  pending_id: string
  term: string
  meaning: string
  aliases: string[]
  group_id: string
  confidence: number
  count: number
  unique_user_count: number
  evidence: string
  reason: string
  repeat_policy: RepeatPolicy
  first_seen_at: string
  last_seen_at: string
  meta?: Record<string, any>
  normalization?: NormalizationInfo | null
}

interface SlangExtractionRun {
  run_id: string
  started_at: string
  finished_at?: string
  status: 'running' | 'success' | 'failed' | 'abandoned'
  group_count: number
  scanned_messages: number
  extracted_terms: number
  promoted_candidates: number
  error: string
  duration_ms: number
  meta?: Record<string, any>
}

interface SlangStatsTerm {
  term_id: string
  term: string
  meaning: string
  scope: 'group' | 'global'
  group_id: string
  status: SlangStatus
  confidence: number
  usage_count: number
  unique_user_count: number
  last_seen_at: string
}

interface SlangStats {
  popular_terms: SlangStatsTerm[]
  group_activity: Array<{
    group_id: string
    term_count: number
    approved_count: number
    usage_count: number
  }>
  recent_trend: Array<{
    date: string
    created: number
    observations: number
  }>
  review: {
    total_terms: number
    candidate_count: number
    reviewed_count: number
    approval_rate: number
  }
  injection: {
    approved_terms: number
    avg_confidence: number
    global_candidates: number
    global_approved: number
    observing_count: number
  }
}

interface SlangSettings {
  learning_enabled: boolean
  injection_enabled: boolean
  review_required: boolean
  max_injected_terms: number
  extract_interval_minutes: number
  candidate_min_count: number
  group_allowlist: string[]
  repeat_policy: RepeatPolicy
  extraction_batch_limit: number
  auto_promote_global_enabled: boolean
  global_promote_min_groups: number
  global_excluded_group_ids: string[]
  bulk_page_size: number
  stats_days: number
  stoplist: string[]
  max_prompt_chars: number
  daily_ai_review_enabled: boolean
  daily_ai_review_time: string
  daily_ai_review_search_enabled: boolean
  daily_ai_auto_approve_enabled: boolean
  daily_ai_auto_approve_min_confidence: number
  daily_ai_max_terms_per_group: number
  daily_ai_recent_message_limit: number
  drift_detection_enabled: boolean
  drift_min_confidence: number
  lookup_tool_enabled: boolean
  min_inject_confidence: number
  semantic_backend: 'ngram' | 'embedding'
}

interface SlangRevision {
  revision_id: string
  term_id: string
  action: string
  actor: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: string
  meta?: Record<string, any>
}

interface SlangDriftReview {
  drift_id: string
  term_id: string
  term: string
  group_id: string
  old_meaning: string
  new_meaning: string
  aliases: string[]
  evidence: string
  confidence: number
  reason: string
  status: 'open' | 'accepted' | 'rejected' | 'aliased' | 'muted'
  created_at: string
  updated_at: string
  meta?: Record<string, any>
}

interface SlangCreateDraft {
  term: string
  meaning: string
  aliases: string
  scope: 'group' | 'global'
  group_id: string
  confidence: number
  status: SlangStatus
  repeat_policy: RepeatPolicy
  notes: string
  evidence: string
}

const message = useMessage()

const summary = ref<SlangSummary>({
  candidate_count: 0,
  candidate_total_count: 0,
  candidate_reviewed_count: 0,
  candidate_unreviewed_count: 0,
  candidate_review_approved_count: 0,
  candidate_review_rejected_count: 0,
  candidate_review_kept_count: 0,
  candidate_review_failed_count: 0,
  approved_count: 0,
  muted_count: 0,
  expired_count: 0,
  pending_count: 0,
  drift_count: 0,
  ai_review_count: 0,
  ai_pending_review_count: 0,
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
const extractionRuns = ref<SlangExtractionRun[]>([])
const groups = ref<string[]>([])
const loading = ref(true)
const refreshing = ref(false)
const extracting = ref(false)
const reviewing = ref(false)
const scanningGlobal = ref(false)
const bulkLoading = ref(false)
const savingSettings = ref(false)
const showAdvancedOverview = ref(false)
const showAdvancedSettings = ref(false)
const createDrawerVisible = ref(false)
const creatingTerm = ref(false)
const page = ref(1)

const searchText = ref('')
const groupFilter = ref('')
const scopeFilter = ref<'group' | 'global' | ''>('')
const sortMode = ref<'default' | 'time'>('default')
const queueMode = ref<SlangQueueMode>('candidate')
const minConfidence = ref('')
const selectedTermIds = ref<string[]>([])

const defaultSlangSettings: SlangSettings = {
  learning_enabled: true,
  injection_enabled: true,
  review_required: true,
  max_injected_terms: 8,
  extract_interval_minutes: 30,
  candidate_min_count: 2,
  group_allowlist: [],
  repeat_policy: 'understand_only',
  extraction_batch_limit: 80,
  auto_promote_global_enabled: false,
  global_promote_min_groups: 3,
  global_excluded_group_ids: [],
  bulk_page_size: 50,
  stats_days: 14,
  stoplist: [],
  max_prompt_chars: 1200,
  daily_ai_review_enabled: true,
  daily_ai_review_time: '04:30',
  daily_ai_review_search_enabled: true,
  daily_ai_auto_approve_enabled: false,
  daily_ai_auto_approve_min_confidence: 0.82,
  daily_ai_max_terms_per_group: 5,
  daily_ai_recent_message_limit: 200,
  drift_detection_enabled: true,
  drift_min_confidence: 0.65,
  lookup_tool_enabled: true,
  min_inject_confidence: 0,
  semantic_backend: 'ngram',
}

const settings = ref<SlangSettings>({ ...defaultSlangSettings })
const allowlistText = ref('')
const globalExcludedText = ref('')
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
const normalizerDetails = ref<Record<string, NormalizerClusterDetail>>({})
const editAliases = ref('')
const mergeTargetId = ref('')
const mergeSearchText = ref('')
const mergeLoading = ref(false)
const mergeCandidates = ref<SlangTerm[]>([])
const slangCacheRevision = 'slang-console-v2_5-cache-recovery'

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '候选池', value: 'candidate' },
  { label: '已批准', value: 'approved' },
  { label: '已静音', value: 'muted' },
  { label: '已过期', value: 'expired' },
]

const confidenceOptions = [
  { label: '全部置信度', value: '' },
  { label: '≥ 0.3', value: '0.3' },
  { label: '≥ 0.6', value: '0.6' },
  { label: '≥ 0.8', value: '0.8' },
]

const scopeOptions = [
  { label: '全部作用域', value: '' },
  { label: '群内词条', value: 'group' },
  { label: '跨群候选', value: 'global' },
]

const repeatPolicyOptions = [
  { label: '仅理解，不主动复述', value: 'understand_only' },
  { label: '可自然改写解释', value: 'allow_rephrase' },
  { label: '可在合适语境使用', value: 'allow_use' },
]

const groupOptions = computed(() => [
  { label: '全部群', value: '' },
  ...groups.value.map(group => ({ label: `群 ${group}`, value: group })),
])
const totalQueueCount = computed(() => (
  summary.value.candidate_total_count
  + summary.value.approved_count
  + summary.value.muted_count
  + summary.value.expired_count
))
const queueOptions = computed(() => [
  {
    label: '待 AI 复核',
    value: 'candidate' as const,
    count: summary.value.candidate_unreviewed_count,
  },
  {
    label: 'AI 建议通过',
    value: 'candidate_ai_approved' as const,
    count: summary.value.candidate_review_approved_count,
  },
  {
    label: 'AI 未通过',
    value: 'candidate_ai_rejected' as const,
    count: summary.value.candidate_review_rejected_count,
  },
  {
    label: '观察不足',
    value: 'observe_more' as const,
    count: summary.value.candidate_review_kept_count,
  },
  {
    label: 'AI 审核',
    value: 'ai_review' as const,
    count: summary.value.ai_pending_review_count,
  },
  {
    label: '已批准',
    value: 'approved' as const,
    count: summary.value.approved_count,
  },
  {
    label: '语义漂移',
    value: 'drift' as const,
    count: summary.value.drift_count,
  },
  {
    label: '全部',
    value: 'all' as const,
    count: totalQueueCount.value,
  },
])

const displayTotal = computed(() => queueMode.value === 'drift' ? driftTotal.value : total.value)
const pageCount = computed(() => Math.max(1, Math.ceil(displayTotal.value / pageSize.value)))
const selectedCount = computed(() => selectedTermIds.value.length)
const pageSelectionChecked = computed(() => (
  terms.value.length > 0 && terms.value.every(term => selectedTermIds.value.includes(term.term_id))
))
const pageSelectionIndeterminate = computed(() => (
  terms.value.some(term => selectedTermIds.value.includes(term.term_id)) && !pageSelectionChecked.value
))
const mergeOptions = computed(() => mergeCandidates.value
  .filter(term => term.term_id !== detailTerm.value?.term_id)
  .map(term => ({
    label: `${term.term} · ${term.scope === 'global' ? '全局' : `群 ${term.group_id}`} · ${statusLabel(term.status)}`,
    value: term.term_id,
  })))

onMounted(() => {
  void loadAll()
})

watch([searchText, groupFilter, scopeFilter, queueMode, minConfidence, sortMode], () => {
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

function statusLabel(status: SlangStatus) {
  return {
    candidate: '候选',
    approved: '已批准',
    muted: '已静音',
    expired: '已过期',
  }[status]
}

function timeValue(value?: string) {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function latestTermTime(term: SlangTerm) {
  return Math.max(
    timeValue(term.updated_at),
    timeValue(term.last_seen_at),
    timeValue(term.last_inferred_at),
    timeValue(term.created_at),
    timeValue(term.first_seen_at),
  )
}

function sortLatestTerms(items: SlangTerm[]) {
  return [...items].sort((left, right) => (
    latestTermTime(right) - latestTermTime(left)
    || right.confidence - left.confidence
    || right.usage_count - left.usage_count
    || left.term.localeCompare(right.term, 'zh-CN')
  ))
}

function sortLatestPending(items: SlangPendingCandidate[]) {
  return [...items].sort((left, right) => (
    timeValue(right.last_seen_at) - timeValue(left.last_seen_at)
    || right.count - left.count
    || right.confidence - left.confidence
    || left.term.localeCompare(right.term, 'zh-CN')
  ))
}

function sortLatestDrift(items: SlangDriftReview[]) {
  return [...items].sort((left, right) => (
    timeValue(right.updated_at) - timeValue(left.updated_at)
    || timeValue(right.created_at) - timeValue(left.created_at)
    || right.confidence - left.confidence
    || left.term.localeCompare(right.term, 'zh-CN')
  ))
}

function sortLatestRuns(items: SlangExtractionRun[]) {
  return [...items].sort((left, right) => (
    timeValue(right.started_at) - timeValue(left.started_at)
    || timeValue(right.finished_at) - timeValue(left.finished_at)
  ))
}

function sortLatestRevisions(items: SlangRevision[]) {
  return [...items].sort((left, right) => timeValue(right.created_at) - timeValue(left.created_at))
}

function sortLatestObservations(items: SlangObservation[]) {
  return [...items].sort((left, right) => timeValue(right.observed_at) - timeValue(left.observed_at))
}

function statusType(status: SlangStatus) {
  return {
    candidate: 'warning',
    approved: 'success',
    muted: 'default',
    expired: 'error',
  }[status] as 'default' | 'success' | 'warning' | 'error'
}

function driftStatusLabel(status: SlangDriftReview['status']) {
  return {
    open: '待处理',
    accepted: '已采纳',
    rejected: '已保留旧义',
    aliased: '已转别名',
    muted: '已静音',
  }[status] || status
}

function driftSemanticLabel(drift: SlangDriftReview) {
  const verdict = String(drift.meta?.drift_semantic_verdict || '').trim()
  return {
    same_meaning: '同义改写',
    alias_candidate: '别名候选',
    real_drift: '确认漂移',
    unclear: '证据不足',
  }[verdict] || ''
}

function driftSemanticType(drift: SlangDriftReview) {
  const verdict = String(drift.meta?.drift_semantic_verdict || '').trim()
  return ({
    same_meaning: 'success',
    alias_candidate: 'info',
    real_drift: 'warning',
    unclear: 'default',
  }[verdict] || 'default') as 'default' | 'success' | 'warning' | 'error' | 'info'
}

function driftSemanticReason(drift: SlangDriftReview) {
  return String(
    drift.meta?.drift_semantic_reason
    || drift.meta?.drift_semantic_error
    || '',
  ).trim()
}

function revisionActionLabel(action: string) {
  const labels: Record<string, string> = {
    create_term: '创建词条',
    update_term: '编辑词条',
    candidate_update: '候选更新',
    ai_auto_review: 'AI 通过',
    human_approve: '人工确认',
    human_deny: '人工否决',
    return_to_candidate: '退回候选',
    merge_terms: '合并词条',
    merge_source_expired: '合并归档',
    drift_detected: '发现漂移',
    drift_suppressed: '漂移门控忽略',
    drift_alias_candidate: '漂移转别名候选',
    drift_accept: '采纳新义',
    drift_reject: '保留旧义',
    drift_alias: '转成别名',
    drift_mute: '静音漂移',
    'set_status:approved': '批准',
    'set_status:muted': '静音',
    'set_status:expired': '过期',
    'set_status:candidate': '改为候选',
  }
  return labels[action] || action
}

function policyLabel(policy: RepeatPolicy) {
  return repeatPolicyOptions.find(option => option.value === policy)?.label || '仅理解，不主动复述'
}

function formatTime(value?: string) {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 16)
}

function confidenceText(value: number) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

function numberSetting(value: unknown, fallback: number) {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

function mergeSettings(
  incoming: Partial<SlangSettings> = {},
  fallback: Partial<SlangSettings> = settings.value,
): SlangSettings {
  const merged = { ...defaultSlangSettings, ...fallback, ...incoming }
  return {
    ...merged,
    group_allowlist: Array.isArray(merged.group_allowlist) ? merged.group_allowlist : [],
    global_excluded_group_ids: Array.isArray(merged.global_excluded_group_ids) ? merged.global_excluded_group_ids : [],
    stoplist: Array.isArray(merged.stoplist) ? merged.stoplist : [],
    daily_ai_review_time: merged.daily_ai_review_time || defaultSlangSettings.daily_ai_review_time,
    max_injected_terms: numberSetting(merged.max_injected_terms, defaultSlangSettings.max_injected_terms),
    extract_interval_minutes: numberSetting(merged.extract_interval_minutes, defaultSlangSettings.extract_interval_minutes),
    candidate_min_count: numberSetting(merged.candidate_min_count, defaultSlangSettings.candidate_min_count),
    extraction_batch_limit: numberSetting(merged.extraction_batch_limit, defaultSlangSettings.extraction_batch_limit),
    global_promote_min_groups: numberSetting(merged.global_promote_min_groups, defaultSlangSettings.global_promote_min_groups),
    bulk_page_size: numberSetting(merged.bulk_page_size, defaultSlangSettings.bulk_page_size),
    stats_days: numberSetting(merged.stats_days, defaultSlangSettings.stats_days),
    max_prompt_chars: numberSetting(merged.max_prompt_chars, defaultSlangSettings.max_prompt_chars),
    daily_ai_auto_approve_min_confidence: numberSetting(
      merged.daily_ai_auto_approve_min_confidence,
      defaultSlangSettings.daily_ai_auto_approve_min_confidence,
    ),
    daily_ai_max_terms_per_group: numberSetting(
      merged.daily_ai_max_terms_per_group,
      defaultSlangSettings.daily_ai_max_terms_per_group,
    ),
    daily_ai_recent_message_limit: numberSetting(
      merged.daily_ai_recent_message_limit,
      defaultSlangSettings.daily_ai_recent_message_limit,
    ),
    drift_min_confidence: numberSetting(merged.drift_min_confidence, defaultSlangSettings.drift_min_confidence),
    min_inject_confidence: numberSetting(merged.min_inject_confidence, defaultSlangSettings.min_inject_confidence),
    semantic_backend: merged.semantic_backend === 'embedding' ? 'embedding' : 'ngram',
  }
}

function isAiApproved(term?: SlangTerm | null) {
  return Boolean(term?.status === 'approved' && (term.source === 'ai_auto_review' || term.meta?.ai_approved === true))
}

function isHumanReviewed(term?: SlangTerm | null) {
  return Boolean(term?.meta?.human_reviewed === true)
}

function needsHumanReview(term?: SlangTerm | null) {
  return Boolean(isAiApproved(term) && term?.status === 'approved' && !isHumanReviewed(term))
}

function candidateReviewApproved(term?: SlangTerm | null) {
  return Boolean(term?.status === 'candidate' && term?.meta?.candidate_review_approved === true)
}

function candidateReviewRejected(term?: SlangTerm | null) {
  return Boolean(
    term?.meta?.ai_rejected === true
    || term?.meta?.candidate_review_state === 'rejected',
  )
}

function candidateReviewObserving(term?: SlangTerm | null) {
  return Boolean(
    term?.status === 'candidate'
    && (term?.meta?.review_decision === 'observe_more'
      || term?.meta?.candidate_review_state === 'observing'
      || term?.meta?.candidate_review_state === 'kept'),
  )
}

function candidateReviewReason(term?: SlangTerm | null) {
  if (term?.meta?.revived_from_ai_reject === true) {
    return '因复现重新审理'
  }
  if (term?.meta?.review_decision === 'returned_to_candidate') {
    return '已退回候选'
  }
  if (term?.meta?.review_decision === 'denied') {
    const reason = String(
      term?.meta?.candidate_review_reason
      || term?.meta?.ai_reason
      || term?.meta?.review_reason
      || '',
    ).trim()
    const progress = rejectedReobserveProgress(term)
    return `已否决并静音${reason ? `：${reason}` : ''}${progress ? `；${progress}` : ''}`
  }
  return String(term?.meta?.candidate_review_reason || '').trim()
}

function rejectedReobserveProgress(term?: SlangTerm | null) {
  const meta = term?.meta || {}
  const count = Number(meta.rejected_reobserve_count || 0)
  const users = Number(meta.rejected_reobserve_user_count || (Array.isArray(meta.rejected_reobserve_users) ? meta.rejected_reobserve_users.length : 0))
  if (!count && !users) return ''
  const countThreshold = Number(meta.rejected_reobserve_threshold_count || 3)
  const userThreshold = Number(meta.rejected_reobserve_threshold_users || 2)
  return `复现 ${count}/${countThreshold}，人 ${users}/${userThreshold}`
}

function normalizationLabel(info?: NormalizationInfo | null) {
  if (!info?.cluster_id) return ''
  const method = info.method === 'new_cluster' ? '新簇' : info.method || '归一化'
  const score = Number(info.score || 0)
  const scoreText = score > 0 ? ` · ${Math.round(score * 100)}%` : ''
  return `${method}${scoreText}`
}

function canReturnToCandidate(term?: SlangTerm | null) {
  return Boolean(
    term
    && (
      candidateReviewApproved(term)
      || candidateReviewRejected(term)
      || term?.meta?.review_decision === 'denied'
      || term?.status === 'expired'
    ),
  )
}

function runKindLabel(run: SlangExtractionRun) {
  return run.meta?.kind === 'daily_ai_review' ? 'AI 复核' : '抽取'
}

function runReviewedCount(run: SlangExtractionRun) {
  const meta = run.meta || {}
  return Number(meta.candidate_reviewed || 0) + Number(meta.pending_reviewed || 0)
}

function runApprovedCount(run: SlangExtractionRun) {
  const meta = run.meta || {}
  return Number(meta.ai_approved || 0) + Number(meta.pending_approved || 0)
}

function runRejectedCount(run: SlangExtractionRun) {
  const meta = run.meta || {}
  return Number(meta.candidate_rejected || 0) + Number(meta.pending_rejected || 0)
}

function runKeptCount(run: SlangExtractionRun) {
  const meta = run.meta || {}
  return Number(meta.candidate_kept || 0) + Number(meta.semantic_kept || 0) + Number(meta.pending_kept || 0)
}

function runStatusType(status: SlangExtractionRun['status']) {
  if (status === 'failed') return 'error'
  if (status === 'running' || status === 'abandoned') return 'warning'
  return 'success'
}

function pendingSemanticLabel(item: SlangPendingCandidate) {
  const meta = item.meta || {}
  if (meta.semantic_failed) return 'AI 复核失败'
  if (meta.semantic_no_info) return meta.semantic_force_review ? '全量已审：信息不足' : '已审：信息不足'
  if (meta.semantic_review) return meta.semantic_force_review ? '全量已审' : '已审'
  return '观察中'
}

function pendingSemanticType(item: SlangPendingCandidate) {
  const meta = item.meta || {}
  if (meta.semantic_failed) return 'error'
  if (meta.semantic_no_info || meta.semantic_review) return 'info'
  return 'warning'
}

function formatSearchQueries(term?: SlangTerm | null) {
  const value = term?.meta?.search_queries
  if (Array.isArray(value)) return value.join(' / ')
  return typeof value === 'string' ? value : ''
}

function buildParams() {
  const params: Record<string, any> = {
    page: page.value,
    page_size: pageSize.value,
    sort: sortMode.value,
  }
  if (groupFilter.value) params.group_id = groupFilter.value
  if (scopeFilter.value) params.scope = scopeFilter.value
  if (queueMode.value === 'candidate') params.review_filter = 'candidate_ai_unreviewed'
  if (queueMode.value === 'candidate_ai_approved') params.review_filter = 'candidate_ai_approved'
  if (queueMode.value === 'candidate_ai_rejected') params.review_filter = 'ai_rejected'
  if (queueMode.value === 'candidate_ai_unreviewed') params.review_filter = 'candidate_ai_unreviewed'
  if (queueMode.value === 'observe_more') params.review_filter = 'observe_more'
  if (queueMode.value === 'approved') params.status = 'approved'
  if (queueMode.value === 'ai_review') {
    params.status = 'approved'
    params.review_filter = 'needs_human_review'
  }
  if (searchText.value.trim()) params.search = searchText.value.trim()
  if (minConfidence.value) params.min_confidence = Number(minConfidence.value)
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
      loadExtractionRuns(),
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
  pendingCandidates.value = sortLatestPending(data.pending || [])
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
  driftReviews.value = sortLatestDrift(data.reviews || [])
  driftTotal.value = Number(data.total || 0)
}

async function loadExtractionRuns() {
  const data = await api('/api/admin/slang/extract/runs', { params: { limit: 5 } })
  extractionRuns.value = sortLatestRuns(data.runs || [])
}

async function loadSettings() {
  const data = await api('/api/admin/slang/settings')
  settings.value = mergeSettings(data.settings || {})
  allowlistText.value = settings.value.group_allowlist.join('\n')
  globalExcludedText.value = settings.value.global_excluded_group_ids.join('\n')
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

async function runAiReview(
  reviewCandidates = false,
  rerunReviewedCandidates = false,
  reviewAllPending = reviewCandidates,
) {
  const confirmText = rerunReviewedCandidates
    ? '确认重跑已审候选？这会重新调用模型并按新规则归档旧结果。'
    : '确认执行全量 AI 复核？这会扫描现有候选词条并调用模型。'
  if (
    reviewCandidates
    && !window.confirm(confirmText)
  ) return
  reviewing.value = true
  try {
    const data = await api('/api/admin/slang/review/run', {
      method: 'POST',
      body: {
        force: true,
        review_candidates: reviewCandidates,
        review_all_pending: reviewAllPending,
        rerun_reviewed_candidates: rerunReviewedCandidates,
      },
    })
    if (!data.ok) {
      message.error(data.error || 'AI 复核失败')
      return
    }
    const reviewed = Number(data.candidate_reviewed || 0) + Number(data.pending_reviewed || 0)
    const approved = Number(data.ai_approved || 0) + Number(data.pending_approved || 0)
    const rejected = Number(data.candidate_rejected || 0) + Number(data.pending_rejected || 0)
    const kept = Number(data.candidate_kept || 0) + Number(data.semantic_kept || 0) + Number(data.pending_kept || 0)
    const prefix = rerunReviewedCandidates
      ? '重跑已审完成'
      : reviewCandidates
        ? '全量 AI 复核完成'
        : 'AI 复核完成'
    message.success(`${prefix}：审查 ${reviewed} 条，通过 ${approved} 条，未通过 ${rejected} 条，观察 ${kept} 条`)
    await loadAll(true)
  } catch (error) {
    console.error('Failed to run slang AI review:', error)
    message.error('AI 复核失败')
  } finally {
    reviewing.value = false
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
    scopeFilter.value = 'global'
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
    scopeFilter.value = draft.scope
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
    observations.value = sortLatestObservations(data.observations || [])
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
    revisions.value = sortLatestRevisions(data.revisions || [])
  } catch (error) {
    console.error('Failed to load slang revisions:', error)
    revisions.value = []
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
    console.error('Failed to load normalizer cluster:', error)
  }
}

function normalizerDetail(info?: NormalizationInfo | null) {
  return info?.cluster_id ? normalizerDetails.value[info.cluster_id] : undefined
}

function normalizerAutoMergeRevision(term: SlangTerm) {
  const info = term.normalization
  const detail = normalizerDetail(info)
  return detail?.revisions.find(entry => entry.action === 'auto_merge' && entry.item_id === info?.item_id)
    || detail?.revisions.find(entry => entry.action === 'auto_merge')
}

function canUndoNormalizerAutoMerge(term: SlangTerm) {
  if (!term.normalization?.cluster_id) return false
  const detail = normalizerDetail(term.normalization)
  return !detail || Boolean(normalizerAutoMergeRevision(term))
}

async function lockNormalizerCluster(term: SlangTerm) {
  const info = term.normalization
  if (!info?.cluster_id) return
  const canonical = window.prompt('锁定代表写法', info.canonical_text || term.term)
  if (!canonical) return
  try {
    const data = await api(`/api/admin/learning-normalizer/clusters/${info.cluster_id}/lock`, {
      method: 'POST',
      body: { canonical_text: canonical, reason: 'slang console lock' },
    })
    if (!data.ok) {
      message.error(data.error || '锁定失败')
      return
    }
    delete normalizerDetails.value[info.cluster_id]
    message.success('代表写法已锁定')
    await loadTerms(true)
    if (detailTerm.value?.term_id === term.term_id) await openDetail(detailTerm.value)
  } catch (error) {
    console.error('Failed to lock normalizer cluster:', error)
    message.error('锁定代表写法失败')
  }
}

async function splitNormalizerItem(term: SlangTerm) {
  const info = term.normalization
  if (!info?.item_id || !window.confirm('确认把当前写法拆出归一化簇？')) return
  try {
    const data = await api(`/api/admin/learning-normalizer/items/${info.item_id}/split`, {
      method: 'POST',
      body: { reason: 'slang console split' },
    })
    if (!data.ok) {
      message.error(data.error || '拆分失败')
      return
    }
    if (info.cluster_id) delete normalizerDetails.value[info.cluster_id]
    message.success('已拆出变体')
    await loadTerms(true)
    if (detailTerm.value?.term_id === term.term_id) await openDetail(detailTerm.value)
  } catch (error) {
    console.error('Failed to split normalizer item:', error)
    message.error('拆出变体失败')
  }
}

async function undoNormalizerAutoMerge(term: SlangTerm) {
  const info = term.normalization
  await loadNormalizerDetail(info)
  const revision = normalizerAutoMergeRevision(term)
  if (!info?.cluster_id || !revision || !window.confirm('确认撤销最近一次自动归并？')) return
  try {
    const data = await api(`/api/admin/learning-normalizer/revisions/${revision.revision_id}/undo`, {
      method: 'POST',
    })
    if (!data.ok) {
      message.error(data.error || '撤销失败')
      return
    }
    delete normalizerDetails.value[info.cluster_id]
    message.success('已撤销自动归并')
    await loadTerms(true)
    if (detailTerm.value?.term_id === term.term_id) await openDetail(detailTerm.value)
  } catch (error) {
    console.error('Failed to undo normalizer auto merge:', error)
    message.error('撤销自动归并失败')
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
  if (!/^\d{2}:\d{2}$/.test(settings.value.daily_ai_review_time || '')) {
    message.warning('每日 AI 识别时间请使用 HH:MM 格式')
    return
  }
  savingSettings.value = true
  try {
    const payload = {
      ...settings.value,
      group_allowlist: allowlistText.value.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
      global_excluded_group_ids: globalExcludedText.value.split(/\n|,|，/).map(item => item.trim()).filter(Boolean),
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
    settings.value = mergeSettings(data.settings || {}, payload)
    allowlistText.value = settings.value.group_allowlist.join('\n')
    globalExcludedText.value = settings.value.global_excluded_group_ids.join('\n')
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
  scopeFilter.value = ''
  queueMode.value = 'candidate'
  minConfidence.value = ''
}

function setQueueMode(value: SlangQueueMode) {
  if (queueMode.value === value) return
  queueMode.value = value
}

function setPageSelection(checked: boolean) {
  const pageIds = terms.value.map(term => term.term_id)
  if (checked) {
    selectedTermIds.value = Array.from(new Set([...selectedTermIds.value, ...pageIds]))
    return
  }
  selectedTermIds.value = selectedTermIds.value.filter(id => !pageIds.includes(id))
}

function toggleTermSelection(termId: string, checked: boolean) {
  if (checked) {
    selectedTermIds.value = Array.from(new Set([...selectedTermIds.value, termId]))
    return
  }
  selectedTermIds.value = selectedTermIds.value.filter(id => id !== termId)
}

function handleTermSelectionUpdate(termId: string, checked: boolean) {
  toggleTermSelection(termId, checked)
}

function termSelectionHandler(termId: string) {
  return (checked: boolean) => handleTermSelectionUpdate(termId, checked)
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
      <NSpace align="center" :size="12">
        <NButton secondary :loading="refreshing" @click="loadAll(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <NButton secondary @click="openCreateDrawer">
          <template #icon>
            <NIcon :component="PricetagsOutline" />
          </template>
          新建黑话
        </NButton>
        <NButton type="primary" secondary :loading="extracting" @click="runExtract">
          <template #icon>
            <NIcon :component="SparklesOutline" />
          </template>
          手动抽取
        </NButton>
        <NButton type="primary" secondary :loading="reviewing" @click="runAiReview(true)">
          <template #icon>
            <NIcon :component="FlashOutline" />
          </template>
          全量 AI 复核
        </NButton>
        <NButton secondary :loading="reviewing" @click="runAiReview(true, true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          重跑已审
        </NButton>
      </NSpace>
    </template>

    <div class="slang-metric-grid">
      <MetricCard
        title="待 AI 复核"
        :value="summary.candidate_unreviewed_count"
        :hint="`候选共 ${summary.candidate_total_count} 条，AI 已审 ${summary.candidate_reviewed_count} 条`"
        :icon="PricetagsOutline"
        accent="warning"
      />
      <MetricCard
        title="AI 未通过"
        :value="summary.candidate_review_rejected_count"
        hint="已静音归档，可人工恢复"
        :icon="AlertCircleOutline"
        accent="warning"
      />
      <MetricCard title="观察不足" :value="summary.candidate_review_kept_count" hint="不催人工，等下个阈值再审" :icon="TimeOutline" accent="info" />
      <MetricCard title="AI 通过" :value="summary.ai_review_count" :hint="`${summary.ai_pending_review_count} 条待人工复核`" :icon="SparklesOutline" accent="primary" />
      <MetricCard title="已批准" :value="summary.approved_count" hint="可进入当前群动态语境" :icon="CheckmarkCircleOutline" accent="success" />
      <MetricCard title="观察中" :value="summary.pending_count" hint="未达到最小出现次数，暂不打扰审核" :icon="TimeOutline" accent="info" />
    </div>

    <AppCard bordered embedded class="slang-advanced-strip">
      <div class="slang-advanced-strip__copy">
        <strong>高级概览</strong>
        <span>热门排行和学习设置默认折叠，运行记录保持可见，避免你来回找入口。</span>
      </div>
      <NSpace align="center" :size="8">
        <NTag round size="small">
          今日命中 {{ summary.today_hits }}
        </NTag>
        <NTag round size="small">
          最近抽取 {{ summary.last_extracted_at || '--' }}
        </NTag>
        <NButton secondary size="small" @click="showAdvancedOverview = !showAdvancedOverview">
          {{ showAdvancedOverview ? '收起概览' : '展开概览' }}
        </NButton>
      </NSpace>
    </AppCard>

    <AppCard bordered embedded class="slang-run-panel">
      <div class="slang-stat-card__head">
        <span>最近抽取记录</span>
        <NTag
          round
          size="small"
          :type="summary.latest_run_status === 'failed' ? 'error' : summary.latest_run_status === 'abandoned' ? 'warning' : 'default'"
        >
          {{ summary.latest_run_status || '未运行' }}
        </NTag>
      </div>
        <div v-if="extractionRuns.length" class="slang-run-list">
        <div v-for="run in extractionRuns.slice(0, 4)" :key="run.run_id" class="slang-run-row">
          <div>
            <strong>{{ formatTime(run.started_at) }} · {{ runKindLabel(run) }}</strong>
            <span v-if="run.meta?.kind === 'daily_ai_review'">
              复核 {{ runReviewedCount(run) }} 条 / 通过 {{ runApprovedCount(run) }} 条 / 未通过 {{ runRejectedCount(run) }} 条 / 观察 {{ runKeptCount(run) }} 条
            </span>
            <span v-else>{{ run.scanned_messages }} 条 / {{ run.extracted_terms }} 个抽取 / {{ run.promoted_candidates }} 个入库</span>
          </div>
          <NTag :type="runStatusType(run.status)" round size="small">
            {{ run.status }}
          </NTag>
        </div>
      </div>
      <EmptyState v-else compact title="暂无抽取记录" description="手动抽取或后台抽取后会记录运行结果。" :icon="SparklesOutline" />
    </AppCard>

    <div v-if="showAdvancedOverview" class="slang-stats-grid">
      <AppCard bordered embedded class="slang-stat-card">
        <div class="slang-stat-card__head">
          <span>热门黑话</span>
          <NTag round size="small">
            {{ stats?.review.total_terms || 0 }} 总词条
          </NTag>
        </div>
        <div v-if="stats?.popular_terms.length" class="slang-rank-list">
          <div v-for="item in stats.popular_terms.slice(0, 5)" :key="item.term_id" class="slang-rank-row">
            <strong>{{ item.term }}</strong>
            <span>{{ item.usage_count }} 次 · {{ confidenceText(item.confidence) }}</span>
          </div>
        </div>
        <EmptyState v-else compact title="还没有排行" description="批准或命中词条后，这里会显示高频黑话。" :icon="PricetagsOutline" />
      </AppCard>

      <AppCard bordered embedded class="slang-stat-card">
        <div class="slang-stat-card__head">
          <span>群活跃排行</span>
          <NTag round size="small">
            {{ summary.group_count }} 个群
          </NTag>
        </div>
        <div v-if="stats?.group_activity.length" class="slang-rank-list">
          <div v-for="item in stats.group_activity.slice(0, 5)" :key="item.group_id" class="slang-rank-row">
            <strong>群 {{ item.group_id }}</strong>
            <span>{{ item.usage_count }} 次 · {{ item.approved_count }}/{{ item.term_count }} 已批准</span>
          </div>
        </div>
        <EmptyState v-else compact title="暂无群活跃数据" description="黑话命中后会自动形成排行。" :icon="FlashOutline" />
      </AppCard>

    </div>

    <div class="slang-control-strip">
      <div class="slang-control-strip__segments" role="tablist" aria-label="黑话审核队列">
        <button
          v-for="option in queueOptions"
          :key="option.value"
          class="slang-segment-button"
          :class="{ 'slang-segment-button--active': queueMode === option.value }"
          type="button"
          role="tab"
          :aria-selected="queueMode === option.value"
          @click="setQueueMode(option.value)"
        >
          <span>{{ option.label }}</span>
          <strong>{{ option.count }}</strong>
        </button>
      </div>

      <div class="slang-control-strip__filters">
        <NInput v-model:value="searchText" class="slang-filter-control slang-filter-control--search" clearable placeholder="搜索词、释义或别名">
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>
        <NSelect v-model:value="groupFilter" class="slang-filter-control" :options="groupOptions" />
        <NSelect v-model:value="scopeFilter" class="slang-filter-control slang-filter-control--compact" :options="scopeOptions" />
        <NSelect v-model:value="minConfidence" class="slang-filter-control slang-filter-control--compact" :options="confidenceOptions" />
        <NSelect v-model:value="sortMode" class="slang-filter-control slang-filter-control--compact" :options="recordSortOptions" />
      </div>

      <div class="slang-control-strip__actions">
        <NButton secondary class="slang-soft-action" @click="resetFilters">
          重置
        </NButton>
        <NButton secondary class="slang-soft-action" :loading="scanningGlobal" @click="runGlobalScan">
          跨群扫描
        </NButton>
        <NTag round size="small" class="slang-total-tag">
          {{ displayTotal }} 条记录
        </NTag>
      </div>
    </div>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <div class="slang-layout" :class="{ 'slang-layout--compact': !showAdvancedSettings }">
        <AppCard bordered elevated class="slang-list-panel">
          <div class="slang-panel-head">
            <div>
              <p class="slang-eyebrow">
                Review Queue
              </p>
              <h3 class="slang-title">
                黑话候选与词表
              </h3>
            </div>
            <NPagination v-if="pageCount > 1" v-model:page="page" :page-count="pageCount" :page-slot="5" size="small" />
          </div>

          <div v-if="queueMode !== 'drift' && terms.length" class="slang-bulk-bar">
            <NCheckbox
              :checked="pageSelectionChecked"
              :indeterminate="pageSelectionIndeterminate"
              @update:checked="setPageSelection"
            >
              选择本页
            </NCheckbox>
            <span>{{ selectedCount }} 个已选</span>
            <NSpace :size="8">
              <NButton size="small" secondary type="success" :disabled="!selectedCount" :loading="bulkLoading" @click="runBulkAction('approve')">
                批量批准
              </NButton>
              <NButton size="small" secondary :disabled="!selectedCount" :loading="bulkLoading" @click="runBulkAction('mute')">
                批量静音
              </NButton>
              <NButton size="small" secondary type="error" :disabled="!selectedCount" :loading="bulkLoading" @click="runBulkAction('expire')">
                批量过期
              </NButton>
              <NButton size="small" secondary :disabled="!selectedCount" :loading="bulkLoading" @click="runBulkAction('delete_observations')">
                删除观察
              </NButton>
            </NSpace>
          </div>

          <template v-if="queueMode === 'drift'">
            <EmptyState
              v-if="driftReviews.length === 0"
              compact
              title="没有待处理漂移"
              description="当已批准词条出现冲突新释义时，会先进入这里等待治理。"
              :icon="AlertCircleOutline"
            />

            <div v-else class="slang-drift-list">
              <AppCard
                v-for="drift in driftReviews"
                :key="drift.drift_id"
                bordered
                embedded
                class="slang-drift-card"
              >
                <div class="slang-drift-card__main">
                  <div class="slang-term-card__head">
                    <div class="slang-term-card__copy">
                      <strong>{{ drift.term }}</strong>
                      <span>群 {{ drift.group_id || '全局' }} · {{ confidenceText(drift.confidence) }} · {{ driftStatusLabel(drift.status) }}</span>
                    </div>
                    <NSpace align="center" size="small">
                      <NTag v-if="driftSemanticLabel(drift)" round size="small" :type="driftSemanticType(drift)">
                        {{ driftSemanticLabel(drift) }}
                      </NTag>
                      <NTag round size="small" type="warning">
                        语义漂移
                      </NTag>
                    </NSpace>
                  </div>
                  <div class="slang-drift-compare">
                    <div>
                      <span>现有释义</span>
                      <p>{{ drift.old_meaning || '未记录' }}</p>
                    </div>
                    <div>
                      <span>新证据释义</span>
                      <p>{{ drift.new_meaning || '未记录' }}</p>
                    </div>
                  </div>
                  <p class="slang-drift-evidence">
                    {{ drift.evidence || drift.reason || '暂无证据文本' }}
                  </p>
                  <p v-if="driftSemanticReason(drift)" class="slang-drift-evidence">
                    语义门控：{{ driftSemanticReason(drift) }}
                  </p>
                </div>
                <div class="slang-term-card__actions">
                  <NButton size="small" type="success" secondary @click="handleDriftAction(drift, 'accept')">
                    采纳新义
                  </NButton>
                  <NButton size="small" secondary @click="handleDriftAction(drift, 'reject')">
                    保留旧义
                  </NButton>
                  <NButton size="small" secondary @click="handleDriftAction(drift, 'alias')">
                    转成别名
                  </NButton>
                  <NButton size="small" secondary type="error" @click="handleDriftAction(drift, 'mute')">
                    静音
                  </NButton>
                </div>
              </AppCard>
            </div>
          </template>

          <EmptyState
            v-else-if="terms.length === 0"
            compact
            title="没有匹配的黑话记录"
            description="可以换一组筛选条件，或点击“手动抽取”从近期消息里生成候选。"
            :icon="PricetagsOutline"
          />

          <div v-else class="slang-term-list">
            <AppCard
              v-for="term in terms"
              :key="term.term_id"
              bordered
              embedded
              interactive
              class="slang-term-card"
              @click="openDetail(term)"
            >
              <NCheckbox
                :checked="selectedTermIds.includes(term.term_id)"
                @click.stop
                @update:checked="termSelectionHandler(term.term_id)"
              />
              <div class="slang-term-card__main">
                <div class="slang-term-card__head">
                  <div class="slang-term-card__copy">
                    <strong>{{ term.term }}</strong>
                    <span>{{ term.meaning || '释义待补充' }}</span>
                  </div>
                  <div class="slang-term-card__tags">
                    <NTag :type="statusType(term.status)" round size="small">
                      {{ statusLabel(term.status) }}
                    </NTag>
                    <NTag v-if="term.scope === 'global'" round size="small" type="info">
                      跨群候选
                    </NTag>
                    <NTag v-if="isAiApproved(term)" round size="small" type="info">
                      AI 通过
                    </NTag>
                    <NTag v-else-if="candidateReviewApproved(term)" round size="small" type="info">
                      AI 建议通过
                    </NTag>
                    <NTag v-else-if="candidateReviewRejected(term)" round size="small" type="warning">
                      AI 未通过
                    </NTag>
                    <NTag v-else-if="candidateReviewObserving(term)" round size="small" type="info">
                      观察不足
                    </NTag>
                    <NTag v-if="needsHumanReview(term)" round size="small" type="warning">
                      待人工复核
                    </NTag>
                    <NTag v-else-if="isHumanReviewed(term)" round size="small" type="success">
                      人工确认
                    </NTag>
                    <NTag round size="small">
                      {{ confidenceText(term.confidence) }}
                    </NTag>
                  </div>
                </div>
                <div class="slang-term-card__meta">
                  <span>群 {{ term.scope === 'global' ? '全局' : term.group_id }}</span>
                  <span>命中 {{ term.usage_count }}</span>
                  <span>{{ term.unique_user_count }} 人使用</span>
                  <span>最近 {{ formatTime(term.last_seen_at) }}</span>
                  <span v-if="term.normalization?.cluster_id">归一化 {{ normalizationLabel(term.normalization) }}</span>
                </div>
                <div v-if="term.normalization?.cluster_id" class="slang-normalization-row">
                  <NTag size="small" round>
                    簇 {{ term.normalization.cluster_id.slice(-6) }}
                  </NTag>
                  <span>代表：{{ term.normalization.canonical_text || term.term }}</span>
                  <span v-if="term.normalization.auto_merged">自动归并</span>
                </div>
                <p v-if="candidateReviewReason(term)" class="slang-term-card__review-note">
                  AI 复核：{{ candidateReviewReason(term) }}
                </p>
                <div v-if="term.aliases.length" class="slang-alias-row">
                  <NTag v-for="alias in term.aliases.slice(0, 5)" :key="alias" size="small" round>
                    {{ alias }}
                  </NTag>
                </div>
              </div>
              <div class="slang-term-card__actions">
                <NButton v-if="needsHumanReview(term)" size="small" type="success" secondary @click.stop="reviewAiTerm(term, 'human-approve')">
                  真实通过
                </NButton>
                <NButton v-if="needsHumanReview(term)" size="small" secondary type="error" @click.stop="reviewAiTerm(term, 'deny')">
                  否决
                </NButton>
                <NButton v-if="term.status !== 'approved'" size="small" type="success" secondary @click.stop="quickStatus(term, 'approve')">
                  批准
                </NButton>
                <NButton v-if="term.status !== 'muted'" size="small" secondary @click.stop="quickStatus(term, 'mute')">
                  静音
                </NButton>
                <NButton v-if="term.status !== 'expired'" size="small" secondary type="error" @click.stop="quickStatus(term, 'expire')">
                  过期
                </NButton>
                <NButton v-if="canReturnToCandidate(term)" size="small" secondary @click.stop="reviewAiTerm(term, 'return-candidate')">
                  退回候选
                </NButton>
              </div>
            </AppCard>
          </div>

          <div v-if="pageCount > 1" class="slang-pagination-bottom">
            <NPagination v-model:page="page" :page-count="pageCount" :page-slot="7" />
          </div>
        </AppCard>

        <AppCard bordered elevated class="slang-settings-panel">
          <div class="slang-panel-head">
            <div>
              <p class="slang-eyebrow">
                Advanced Settings
              </p>
              <h3 class="slang-title">
                学习与注入
              </h3>
            </div>
            <NButton tertiary size="small" @click="showAdvancedSettings = !showAdvancedSettings">
              {{ showAdvancedSettings ? '收起' : '展开' }}
            </NButton>
          </div>
          <p v-if="!showAdvancedSettings" class="slang-settings-collapsed-note">
            设置、漂移治理、观察中候选和自动学习参数都保留在这里，默认折叠以减少首屏干扰。
          </p>

          <template v-if="showAdvancedSettings">
            <div class="slang-side-section slang-governance-section">
              <div class="slang-side-section__head">
                <strong>质量治理</strong>
                <NTag round size="small" :type="summary.drift_count ? 'warning' : 'success'">
                  {{ summary.drift_count }} 个漂移
                </NTag>
              </div>
              <p class="slang-side-note">
                已批准词条遇到冲突新释义时，先进入漂移队列；处理前不会覆盖 Prompt 中的主释义。
              </p>
              <div v-if="driftReviews.length" class="slang-pending-list">
                <div v-for="item in driftReviews.slice(0, 4)" :key="item.drift_id" class="slang-pending-row">
                  <div>
                    <strong>{{ item.term }}</strong>
                    <span>{{ item.new_meaning || item.reason || '等待处理' }}</span>
                  </div>
                  <NTag round size="small" type="warning">
                    {{ confidenceText(item.confidence) }}
                  </NTag>
                </div>
              </div>
              <NButton secondary block @click="setQueueMode('drift')">
                查看漂移队列
              </NButton>
            </div>

            <div class="slang-side-section">
              <div class="slang-side-section__head">
                <strong>观察中候选</strong>
                <NTag round size="small">
                  {{ pendingTotal }} 条
                </NTag>
              </div>
              <div v-if="pendingCandidates.length" class="slang-pending-list">
                <div v-for="item in pendingCandidates" :key="item.pending_id" class="slang-pending-row">
                  <div>
                    <strong>{{ item.term }}</strong>
                    <span>{{ item.meaning || item.evidence || '等待更多出现证据' }}</span>
                  </div>
                  <NSpace align="center" :size="6">
                    <NTag v-if="item.normalization?.cluster_id" round size="small" type="info">
                      {{ normalizationLabel(item.normalization) }}
                    </NTag>
                    <NTag round size="small" :type="pendingSemanticType(item)">
                      {{ pendingSemanticLabel(item) }}
                    </NTag>
                    <NTag round size="small" type="warning">
                      {{ item.count }} 次
                    </NTag>
                  </NSpace>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="暂无观察中候选"
                description="未达到最小出现次数的抽取结果会先停在这里。"
                :icon="TimeOutline"
              />
            </div>

            <div class="slang-settings-form">
            <label class="slang-switch-row">
              <span>
                <strong>启用学习</strong>
                <small>后台从群聊中抽取候选。</small>
              </span>
              <NSwitch v-model:value="settings.learning_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>启用注入</strong>
                <small>已批准黑话进入动态 Prompt。</small>
              </span>
              <NSwitch v-model:value="settings.injection_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>审核优先</strong>
                <small>候选不自动批准，保持安全兜底。</small>
              </span>
              <NSwitch v-model:value="settings.review_required" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>自动跨群提升</strong>
                <small>默认建议关闭；开启后后台抽取会生成 global 候选，仍需人工批准。</small>
              </span>
              <NSwitch v-model:value="settings.auto_promote_global_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>每日 AI 识别</strong>
                <small>每天定点扫描近期群聊，结合搜索判断疑似网络梗。</small>
              </span>
              <NSwitch v-model:value="settings.daily_ai_review_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>搜索辅助识别梗</strong>
                <small>复用 web_search；搜索失败时只入候选，不自动通过。</small>
              </span>
              <NSwitch v-model:value="settings.daily_ai_review_search_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>允许 AI 自动通过</strong>
                <small>高置信且有搜索证据时直接 approved，并标记待人工复核。</small>
              </span>
              <NSwitch v-model:value="settings.daily_ai_auto_approve_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>语义漂移检测</strong>
                <small>已批准词条遇到冲突新释义时进入治理队列，不直接覆盖。</small>
              </span>
              <NSwitch v-model:value="settings.drift_detection_enabled" />
            </label>
            <label class="slang-switch-row">
              <span>
                <strong>启用黑话查询工具</strong>
                <small>允许 LLM 按需查询更多已批准黑话，减少 Prompt 常驻长度。</small>
              </span>
              <NSwitch v-model:value="settings.lookup_tool_enabled" />
            </label>

            <div class="slang-settings-grid">
              <label>
                <span>每日 AI 识别时间</span>
                <NInput v-model:value="settings.daily_ai_review_time" placeholder="04:30" />
              </label>
              <label>
                <span>AI 通过最低置信度</span>
                <NInputNumber v-model:value="settings.daily_ai_auto_approve_min_confidence" :min="0" :max="1" :step="0.01" />
              </label>
              <label>
                <span>每日每群最多入库</span>
                <NInputNumber v-model:value="settings.daily_ai_max_terms_per_group" :min="1" :max="30" />
              </label>
              <label>
                <span>每日扫描消息数</span>
                <NInputNumber v-model:value="settings.daily_ai_recent_message_limit" :min="20" :max="1000" />
              </label>
              <label>
                <span>最大注入条数</span>
                <NInputNumber v-model:value="settings.max_injected_terms" :min="1" :max="30" />
              </label>
              <label>
                <span>抽取间隔（分钟）</span>
                <NInputNumber v-model:value="settings.extract_interval_minutes" :min="1" :max="1440" />
              </label>
              <label>
                <span>候选最小出现次数</span>
                <NInputNumber v-model:value="settings.candidate_min_count" :min="1" :max="50" />
              </label>
              <label>
                <span>单批扫描消息数</span>
                <NInputNumber v-model:value="settings.extraction_batch_limit" :min="10" :max="500" />
              </label>
              <label>
                <span>跨群提升最小群数</span>
                <NInputNumber v-model:value="settings.global_promote_min_groups" :min="2" :max="20" />
              </label>
              <label>
                <span>批量页大小</span>
                <NInputNumber v-model:value="settings.bulk_page_size" :min="10" :max="200" />
              </label>
              <label>
                <span>统计窗口（天）</span>
                <NInputNumber v-model:value="settings.stats_days" :min="1" :max="120" />
              </label>
              <label>
                <span>Prompt 最大字符</span>
                <NInputNumber v-model:value="settings.max_prompt_chars" :min="300" :max="6000" />
              </label>
              <label>
                <span>漂移最低置信度</span>
                <NInputNumber v-model:value="settings.drift_min_confidence" :min="0" :max="1" :step="0.01" />
              </label>
              <label>
                <span>注入最低置信度</span>
                <NInputNumber v-model:value="settings.min_inject_confidence" :min="0" :max="1" :step="0.01" />
              </label>
            </div>

            <label class="slang-settings-field">
              <span>默认复述策略</span>
              <NSelect v-model:value="settings.repeat_policy" :options="repeatPolicyOptions" />
            </label>

            <label class="slang-settings-field">
              <span>语义后端</span>
              <NSelect
                v-model:value="settings.semantic_backend"
                :options="[{ label: '轻量 ngram（默认）', value: 'ngram' }, { label: 'Embedding（v3.5 预留，未安装时降级）', value: 'embedding', disabled: true }]"
              />
            </label>

            <label class="slang-settings-field">
              <span>群白名单</span>
              <NInput
                v-model:value="allowlistText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="每行一个群号；留空表示所有群可学习"
              />
            </label>

            <label class="slang-settings-field">
              <span>封闭全局黑话的群</span>
              <NInput
                v-model:value="globalExcludedText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="每行一个群号；留空表示所有群默认可使用全局黑话"
              />
              <small>这些群只使用本群已批准黑话，不注入也不查询 global 黑话。</small>
            </label>

            <label class="slang-settings-field">
              <span>停用词 / 永不学习</span>
              <NInput
                v-model:value="stoplistText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="每行一个普通词、人名或作品名；命中后不会进入候选"
              />
            </label>

            <NButton type="primary" :loading="savingSettings" @click="saveSettings">
              保存设置
            </NButton>
            </div>
          </template>
        </AppCard>
      </div>
    </template>

    <NDrawer v-model:show="createDrawerVisible" :width="620">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Manual Slang"
            title="主动构建黑话"
            description="手动录入群内约定词、释义和使用策略；可直接批准进入 Prompt 注入候选。"
          />
        </template>

        <AppDrawerLayout>
          <AppPanelSection eyebrow="Term" title="词条信息">
            <div class="slang-detail-grid">
              <label>
                <span>黑话词</span>
                <NInput v-model:value="createDraft.term" placeholder="例如：猫饼" />
              </label>
              <label>
                <span>作用域</span>
                <NSelect
                  v-model:value="createDraft.scope"
                  :options="[{ label: '当前群', value: 'group' }, { label: '全局', value: 'global' }]"
                />
              </label>
              <label v-if="createDraft.scope === 'group'">
                <span>群号</span>
                <NInput v-model:value="createDraft.group_id" placeholder="输入群号；可用当前筛选群自动带入" />
              </label>
              <label>
                <span>状态</span>
                <NSelect v-model:value="createDraft.status" :options="statusOptions.filter(option => option.value)" />
              </label>
              <label>
                <span>置信度</span>
                <NInputNumber v-model:value="createDraft.confidence" :min="0" :max="1" :step="0.05" />
              </label>
              <label>
                <span>复述策略</span>
                <NSelect v-model:value="createDraft.repeat_policy" :options="repeatPolicyOptions" />
              </label>
              <label class="slang-detail-grid__full">
                <span>释义</span>
                <NInput
                  v-model:value="createDraft.meaning"
                  type="textarea"
                  :autosize="{ minRows: 3, maxRows: 6 }"
                  placeholder="说明这个词在群里的真实含义和适用场景"
                />
              </label>
              <label class="slang-detail-grid__full">
                <span>别名（每行一个）</span>
                <NInput
                  v-model:value="createDraft.aliases"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  placeholder="可选；例如缩写、同义写法、错别字梗"
                />
              </label>
            </div>
          </AppPanelSection>

          <AppPanelSection eyebrow="Context" title="示例与备注">
            <div class="slang-detail-grid">
              <label class="slang-detail-grid__full">
                <span>示例 / 证据</span>
                <NInput
                  v-model:value="createDraft.evidence"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  placeholder="可选；写一句典型群聊用法，方便后续审核追溯"
                />
              </label>
              <label class="slang-detail-grid__full">
                <span>备注</span>
                <NInput
                  v-model:value="createDraft.notes"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  placeholder="可选；记录来源、边界或维护说明"
                />
              </label>
            </div>
          </AppPanelSection>

          <template #footer>
            <NButton secondary @click="createDrawerVisible = false">
              取消
            </NButton>
            <NButton type="primary" :loading="creatingTerm" @click="saveCreateTerm">
              创建黑话
            </NButton>
          </template>
        </AppDrawerLayout>
      </NDrawerContent>
    </NDrawer>

    <NDrawer v-model:show="drawerVisible" :width="620">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Slang Detail"
            :title="detailTerm?.term || '黑话详情'"
            :description="detailTerm ? `群 ${detailTerm.group_id || '全局'} · ${statusLabel(detailTerm.status)}` : ''"
          >
            <template v-if="detailTerm" #aside>
              <NTag :type="statusType(detailTerm.status)" round size="small">
                {{ confidenceText(detailTerm.confidence) }}
              </NTag>
            </template>
          </AppDrawerHeader>
        </template>

        <NSkeleton v-if="detailLoading" :repeat="6" text />

        <AppDrawerLayout v-else-if="detailTerm">
          <AppPanelSection eyebrow="Editor" title="术语与释义">
            <div class="slang-detail-grid">
              <label>
                <span>术语</span>
                <NInput v-model:value="detailTerm.term" />
              </label>
              <label>
                <span>作用域</span>
                <NSelect v-model:value="detailTerm.scope" :options="[{ label: '当前群', value: 'group' }, { label: '全局', value: 'global' }]" />
              </label>
              <label v-if="detailTerm.scope === 'group'">
                <span>群号</span>
                <NInput v-model:value="detailTerm.group_id" />
              </label>
              <label>
                <span>状态</span>
                <NSelect v-model:value="detailTerm.status" :options="statusOptions.filter(option => option.value)" />
              </label>
              <label>
                <span>置信度</span>
                <NInputNumber v-model:value="detailTerm.confidence" :min="0" :max="1" :step="0.05" />
              </label>
              <label>
                <span>复述策略</span>
                <NSelect v-model:value="detailTerm.repeat_policy" :options="repeatPolicyOptions" />
              </label>
              <label class="slang-detail-grid__full">
                <span>释义</span>
                <NInput v-model:value="detailTerm.meaning" type="textarea" :autosize="{ minRows: 3, maxRows: 6 }" />
              </label>
              <label class="slang-detail-grid__full">
                <span>别名（每行一个）</span>
                <NInput v-model:value="editAliases" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" />
              </label>
              <label class="slang-detail-grid__full">
                <span>备注</span>
                <NInput v-model:value="detailTerm.notes" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" />
              </label>
            </div>
          </AppPanelSection>

          <AppPanelSection v-if="isAiApproved(detailTerm)" eyebrow="AI Review" title="AI 通过复核">
            <div class="slang-ai-review-box">
              <div class="slang-ai-review-box__head">
                <div>
                  <strong>{{ needsHumanReview(detailTerm) ? '待管理员真实通过或否决' : '已完成人工处理' }}</strong>
                  <span>AI 通过会立即参与 Prompt 注入，但仍建议管理员复核来源和释义。</span>
                </div>
                <NTag :type="needsHumanReview(detailTerm) ? 'warning' : 'success'" round size="small">
                  {{ needsHumanReview(detailTerm) ? '待复核' : '已处理' }}
                </NTag>
              </div>
              <div class="slang-ai-review-grid">
                <div>
                  <span>AI 理由</span>
                  <p>{{ detailTerm.meta?.ai_reason || detailTerm.meta?.reason || '未记录' }}</p>
                </div>
                <div>
                  <span>群内证据</span>
                  <p>{{ detailTerm.meta?.group_evidence || detailTerm.meta?.evidence || '未记录' }}</p>
                </div>
                <div class="slang-ai-review-grid__full">
                  <span>搜索查询</span>
                  <p>{{ formatSearchQueries(detailTerm) || '未记录' }}</p>
                </div>
                <div class="slang-ai-review-grid__full">
                  <span>搜索证据</span>
                  <p>{{ detailTerm.meta?.search_evidence || '没有可用搜索证据' }}</p>
                </div>
              </div>
              <NSpace :size="8">
                <template v-if="needsHumanReview(detailTerm)">
                  <NButton type="success" secondary @click="reviewAiTerm(detailTerm, 'human-approve')">
                    真实通过
                  </NButton>
                  <NButton secondary @click="reviewAiTerm(detailTerm, 'return-candidate')">
                    退回候选
                  </NButton>
                  <NButton type="error" secondary @click="reviewAiTerm(detailTerm, 'deny')">
                    否决并静音
                  </NButton>
                </template>
                <template v-else>
                  <NButton secondary @click="reviewAiTerm(detailTerm, 'return-candidate')">
                    退回候选
                  </NButton>
                </template>
              </NSpace>
            </div>
          </AppPanelSection>

          <AppPanelSection
            v-if="detailTerm.normalization?.cluster_id"
            eyebrow="Normalizer"
            title="归一化归类"
            @mouseenter="loadNormalizerDetail(detailTerm.normalization)"
          >
            <div class="slang-ai-review-box">
              <div class="slang-ai-review-box__head">
                <div>
                  <strong>{{ normalizationLabel(detailTerm.normalization) }}</strong>
                  <span>自动归类只影响候选合并和别名/变体提示，不会改写原始证据。</span>
                </div>
                <NTag round size="small" type="info">
                  簇 {{ detailTerm.normalization.cluster_id.slice(-6) }}
                </NTag>
              </div>
              <div class="slang-ai-review-grid">
                <div>
                  <span>代表写法</span>
                  <p>{{ detailTerm.normalization.canonical_text || detailTerm.term }}</p>
                </div>
                <div>
                  <span>归一 Key</span>
                  <p>{{ detailTerm.normalization.normalized_key || '--' }}</p>
                </div>
              </div>
              <div class="slang-normalization-actions">
                <NButton size="small" secondary @click="lockNormalizerCluster(detailTerm)">
                  锁定代表写法
                </NButton>
                <NButton size="small" secondary @click="splitNormalizerItem(detailTerm)">
                  拆出当前变体
                </NButton>
                <NButton
                  size="small"
                  secondary
                  :disabled="!canUndoNormalizerAutoMerge(detailTerm)"
                  @click="undoNormalizerAutoMerge(detailTerm)"
                >
                  撤销最近归并
                </NButton>
              </div>
            </div>
          </AppPanelSection>

          <AppPanelSection v-if="canReturnToCandidate(detailTerm)" eyebrow="Recovery" title="退回候选">
            <div class="slang-ai-review-box">
              <div class="slang-ai-review-box__head">
                <div>
                  <strong>将该词条恢复到候选池</strong>
                  <span>会清空当前 AI 复核痕迹，并把词条重新交回候选队列，方便再次观察或人工重审。</span>
                </div>
                <NTag round size="small" type="warning">
                  {{ candidateReviewReason(detailTerm) || '可恢复' }}
                </NTag>
              </div>
              <NSpace :size="8">
                <NButton secondary @click="reviewAiTerm(detailTerm, 'return-candidate')">
                  退回候选
                </NButton>
              </NSpace>
            </div>
          </AppPanelSection>

          <AppPanelSection eyebrow="Quality" title="合并与置信度">
            <div class="slang-quality-grid">
              <AppCard bordered embedded class="slang-quality-card">
                <div class="slang-quality-card__head">
                  <div>
                    <strong>置信度来源</strong>
                    <span>按出现次数、独立用户、LLM 估计、人工状态和近期活跃重算。</span>
                  </div>
                  <NButton size="small" secondary @click="recomputeConfidence">
                    重算
                  </NButton>
                </div>
                <div class="slang-signal-list">
                  <span>出现次数：{{ detailTerm.meta?.confidence_signals?.usage_count ?? '--' }}</span>
                  <span>独立用户：{{ detailTerm.meta?.confidence_signals?.unique_users ?? '--' }}</span>
                  <span>LLM：{{ detailTerm.meta?.confidence_signals?.llm ?? '--' }}</span>
                  <span>人工状态：{{ detailTerm.meta?.confidence_signals?.status ?? '--' }}</span>
                </div>
              </AppCard>

              <AppCard bordered embedded class="slang-quality-card">
                <div class="slang-quality-card__head">
                  <div>
                    <strong>合并重复项</strong>
                    <span>把当前词条合并到主词条，观察记录和别名会迁移过去。</span>
                  </div>
                </div>
                <NSelect
                  v-model:value="mergeTargetId"
                  v-model:search-value="mergeSearchText"
                  filterable
                  remote
                  clearable
                  :loading="mergeLoading"
                  :options="mergeOptions"
                  placeholder="搜索并选择主词条"
                  @search="loadMergeCandidates"
                />
                <NButton secondary type="warning" :disabled="!mergeTargetId" @click="mergeCurrentIntoTarget">
                  合并到主词条
                </NButton>
              </AppCard>
            </div>
          </AppPanelSection>

          <AppPanelSection eyebrow="History" title="修订记录 / 证据链">
            <EmptyState v-if="revisions.length === 0" compact title="暂无修订记录" description="人工编辑、AI 通过、合并和漂移治理会在这里留下前后快照。" :icon="TimeOutline" />
            <div v-else class="slang-revision-list">
              <div v-for="revision in revisions.slice(0, 8)" :key="revision.revision_id" class="slang-revision-row">
                <div class="slang-revision-row__head">
                  <strong>{{ revisionActionLabel(revision.action) }}</strong>
                  <span>{{ formatTime(revision.created_at) }} · {{ revision.actor || 'system' }}</span>
                </div>
                <p v-if="revision.reason">
                  {{ revision.reason }}
                </p>
                <div class="slang-revision-diff">
                  <span v-if="revision.before?.meaning !== revision.after?.meaning">
                    释义：{{ revision.before?.meaning || '空' }} → {{ revision.after?.meaning || '空' }}
                  </span>
                  <span v-if="revision.before?.status !== revision.after?.status">
                    状态：{{ revision.before?.status || '无' }} → {{ revision.after?.status || '无' }}
                  </span>
                  <span v-if="revision.before?.confidence !== revision.after?.confidence">
                    置信度：{{ confidenceText(revision.before?.confidence || 0) }} → {{ confidenceText(revision.after?.confidence || 0) }}
                  </span>
                </div>
              </div>
            </div>
          </AppPanelSection>

          <AppPanelSection eyebrow="Evidence" title="观察记录">
            <EmptyState v-if="observations.length === 0" compact title="暂无观察记录" description="后续命中或抽取会在这里留下证据。" :icon="SearchOutline" />
            <div v-else class="slang-observation-list">
              <div v-for="item in observations" :key="item.observation_id" class="slang-observation">
                <div class="slang-observation__meta">
                  <span>{{ formatTime(item.observed_at) }}</span>
                  <span>用户 {{ item.user_id || '--' }}</span>
                  <span>{{ item.reason || '观察' }}</span>
                </div>
                <p>{{ item.raw_text || item.context }}</p>
              </div>
            </div>
          </AppPanelSection>

          <template #footer>
            <NButton secondary @click="drawerVisible = false">
              关闭
            </NButton>
            <NButton type="primary" @click="saveDetail">
              保存修改
            </NButton>
          </template>
        </AppDrawerLayout>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.slang-metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(156px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.slang-cache-revision {
  display: none;
}

.slang-advanced-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding: 14px 16px;
}

.slang-advanced-strip__copy {
  display: grid;
  gap: 4px;
}

.slang-advanced-strip__copy strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.slang-advanced-strip__copy span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.slang-control-strip {
  display: grid;
  grid-template-columns: auto minmax(300px, 1fr) auto;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--om-border) 86%, rgb(var(--primary-color)));
  border-radius: 18px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--om-surface-solid) 94%, transparent), color-mix(in srgb, rgb(var(--primary-color)) 5%, var(--om-surface-solid))),
    var(--om-surface-2);
  box-shadow: 0 10px 28px rgba(23, 42, 48, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.48);
}

.slang-control-strip__segments,
.slang-control-strip__filters,
.slang-control-strip__actions {
  min-width: 0;
}

.slang-control-strip__segments {
  display: inline-grid;
  grid-template-columns: repeat(5, minmax(70px, 1fr));
  gap: 3px;
  padding: 3px;
  border: 1px solid color-mix(in srgb, var(--om-border) 82%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-segment-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 34px;
  gap: 7px;
  padding: 0 12px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.slang-segment-button:hover {
  color: var(--om-text-1);
  background: color-mix(in srgb, rgb(var(--primary-color)) 8%, transparent);
}

.slang-segment-button--active {
  color: var(--om-text-1);
  background: color-mix(in srgb, rgb(var(--primary-color)) 16%, var(--om-surface-solid));
  box-shadow: 0 6px 14px rgba(23, 42, 48, 0.08);
}

.slang-segment-button strong {
  min-width: 24px;
  padding: 2px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface-solid) 84%, transparent);
  color: inherit;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
}

.slang-control-strip__filters {
  display: grid;
  grid-template-columns: minmax(210px, 1.35fr) minmax(150px, 0.78fr) minmax(132px, 0.62fr) minmax(126px, 0.56fr);
  gap: 8px;
}

.slang-filter-control {
  min-width: 0;
}

.slang-filter-control :deep(.n-input),
.slang-filter-control :deep(.n-base-selection) {
  --n-height: 36px;
  border-radius: 999px;
}

.slang-filter-control :deep(.n-input__border),
.slang-filter-control :deep(.n-input__state-border),
.slang-filter-control :deep(.n-base-selection__border),
.slang-filter-control :deep(.n-base-selection__state-border) {
  border-radius: 999px;
}

.slang-control-strip__actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.slang-soft-action {
  border-radius: 999px;
}

.slang-total-tag {
  min-height: 30px;
  padding: 0 10px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 18px;
}

.slang-run-panel {
  margin-bottom: 18px;
  padding: 16px;
}

.slang-stat-card {
  min-height: 172px;
  padding: 16px;
}

.slang-stat-card__head,
.slang-side-section__head,
.slang-quality-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.slang-stat-card__head span,
.slang-side-section__head strong,
.slang-quality-card__head strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.slang-rank-list,
.slang-run-list,
.slang-pending-list,
.slang-signal-list {
  display: grid;
  gap: 10px;
}

.slang-rank-row,
.slang-run-row,
.slang-pending-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid color-mix(in srgb, var(--om-border) 72%, transparent);
}

.slang-rank-row:first-child,
.slang-run-row:first-child,
.slang-pending-row:first-child {
  border-top: 0;
}

.slang-rank-row strong,
.slang-run-row strong,
.slang-pending-row strong {
  color: var(--om-text-1);
}

.slang-rank-row span,
.slang-run-row span,
.slang-pending-row span,
.slang-quality-card__head span,
.slang-signal-list span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-run-row > div,
.slang-pending-row > div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.slang-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 18px;
  align-items: start;
}

.slang-layout--compact {
  grid-template-columns: 1fr;
}

.slang-list-panel,
.slang-settings-panel {
  padding: 20px;
}

.slang-settings-collapsed-note {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.slang-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 18px;
}

.slang-eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.slang-title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.slang-bulk-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: -4px 0 16px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 78%, transparent);
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-term-list {
  display: grid;
  gap: 12px;
}

.slang-drift-list {
  display: grid;
  gap: 12px;
}

.slang-drift-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  padding: 16px;
}

.slang-drift-card__main {
  min-width: 0;
}

.slang-drift-compare {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.slang-drift-compare > div {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.slang-drift-compare span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 700;
}

.slang-drift-compare p,
.slang-drift-evidence {
  margin: 6px 0 0;
  color: var(--om-text-1);
  line-height: 1.65;
}

.slang-drift-evidence {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-term-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 14px;
  padding: 16px;
}

.slang-term-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.slang-term-card__copy {
  display: grid;
  min-width: 0;
  gap: 6px;
}

.slang-term-card__copy strong {
  color: var(--om-text-1);
  font-size: 17px;
}

.slang-term-card__copy span,
.slang-term-card__meta,
.slang-settings-field span,
.slang-settings-grid span,
.slang-detail-grid span {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-term-card__tags,
.slang-alias-row,
.slang-term-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.slang-term-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}

.slang-normalization-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-normalization-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.slang-term-card__review-note {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.slang-alias-row {
  margin-top: 10px;
}

.slang-term-card__actions {
  align-content: flex-start;
  justify-content: flex-end;
}

.slang-pagination-bottom {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}

.slang-settings-form {
  display: grid;
  gap: 16px;
}

.slang-side-section {
  display: grid;
  gap: 12px;
  margin-bottom: 18px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 68%, transparent);
}

.slang-governance-section {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 24%, var(--om-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, rgb(var(--primary-color)) 8%, transparent), transparent),
    color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-side-note {
  margin: -4px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.slang-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-switch-row span {
  display: grid;
  gap: 4px;
}

.slang-switch-row strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.slang-switch-row small {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-settings-grid,
.slang-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-settings-grid label,
.slang-settings-field,
.slang-detail-grid label {
  display: grid;
  gap: 8px;
}

.slang-detail-grid__full {
  grid-column: 1 / -1;
}

.slang-quality-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-quality-card {
  display: grid;
  align-content: start;
  gap: 12px;
  padding: 14px;
}

.slang-ai-review-box {
  display: grid;
  gap: 14px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, rgb(var(--primary-color)) 28%, var(--om-border));
  border-radius: 16px;
  background: color-mix(in srgb, rgba(var(--primary-color), 0.12) 58%, var(--om-surface-solid));
}

.slang-ai-review-box__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.slang-ai-review-box__head > div {
  display: grid;
  gap: 4px;
}

.slang-ai-review-box strong {
  color: var(--om-text-1);
}

.slang-ai-review-box span,
.slang-ai-review-box p {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-ai-review-box p {
  margin: 4px 0 0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.slang-ai-review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-ai-review-grid__full {
  grid-column: 1 / -1;
}

.slang-signal-list {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.slang-observation-list {
  display: grid;
  gap: 10px;
}

.slang-revision-list {
  display: grid;
  gap: 10px;
}

.slang-revision-row {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-revision-row__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.slang-revision-row__head strong {
  color: var(--om-text-1);
}

.slang-revision-row__head span,
.slang-revision-row p,
.slang-revision-diff span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-revision-row p {
  margin: 0;
  line-height: 1.6;
}

.slang-revision-diff {
  display: grid;
  gap: 4px;
}

.slang-observation {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-observation__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-observation p {
  margin: 8px 0 0;
  color: var(--om-text-1);
  line-height: 1.65;
}

@media (max-width: 1180px) {
  .slang-control-strip {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .slang-control-strip__segments {
    grid-column: 1 / -1;
    width: 100%;
  }

  .slang-layout {
    grid-template-columns: 1fr;
  }

  .slang-stats-grid {
    grid-template-columns: 1fr;
  }

  .slang-metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .slang-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-control-strip {
    grid-template-columns: 1fr;
  }

  .slang-control-strip__filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-control-strip__actions {
    justify-content: flex-start;
  }

  .slang-term-card {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .slang-drift-card {
    grid-template-columns: 1fr;
  }

  .slang-term-card__actions {
    grid-column: 2;
    justify-content: flex-start;
  }

  .slang-quality-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .slang-metric-grid,
  .slang-stats-grid,
  .slang-settings-grid,
  .slang-detail-grid,
  .slang-ai-review-grid,
  .slang-drift-compare {
    grid-template-columns: 1fr;
  }

  .slang-term-card__head {
    flex-direction: column;
  }

  .slang-control-strip {
    padding: 8px;
    border-radius: 16px;
  }

  .slang-control-strip__segments,
  .slang-control-strip__filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-control-strip__actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-total-tag {
    justify-content: center;
    grid-column: 1 / -1;
  }
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NSelect, NTag, NText } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import StyleManagement from './StyleManagement.vue'
import AutoLearningManagement from './AutoLearningManagement.vue'
import ObservationManagement from './ObservationManagement.vue'
import MemoryIdentityEpisodeManagement from './MemoryIdentityEpisodeManagement.vue'
import MattersManagement from './MattersManagement.vue'
import type {
  MemoryApplyRequest,
  MemoryCardMetadataView,
  MemoryCardQueryView,
  MemoryCardClearRequest,
  MemoryCardClassificationRequest,
  MemoryCandidatePageView,
  MemoryCandidateView,
  MemoryCorrectionRequest,
  DomainLearningFailurePageView,
  DomainLearningFailureView,
  DomainLearningRetryRequest,
  DomainLearningRetryView,
  MemoryDisableFactRequest,
  MemoryFactPageView,
  MemoryFactView,
  MemoryHotFactView,
  MemoryHotPreviewView,
  MemoryResolveConflictRequest,
  MemoryReviewRequest,
  MemoryRetrievalDiagnosticRequest,
  MemoryRetrievalDiagnosticView,
  ExtractionRunDiagnosticPageView,
  ExtractionRunDiagnosticView,
  DomainLearningCandidateView,
  JsonValue,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const PAGE_SIZE = 32

const statusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '待审核', value: 'pending' },
  { label: '冲突待处理', value: 'conflict_pending' },
  { label: '已批准', value: 'approved' },
  { label: '已应用', value: 'applied' },
  { label: '已拒绝', value: 'rejected' },
  { label: '已撤回', value: 'withdrawn' },
  { label: '已跳过', value: 'skipped' },
]

const groupIdInput = ref('')
const statusFilter = ref('all')
const loadedGroupId = ref('')
const loadedStatus = ref('all')
type ReviewCandidateView = MemoryCandidatePageView['items'][number]

const candidates = shallowRef<ReviewCandidateView[]>([])
const domainFailures = ref<DomainLearningFailureView[]>([])
const extractionRuns = ref<ExtractionRunDiagnosticView[]>([])
const domainReviewReasons = reactive<Record<string, string>>({})
const domainRetryReasons = reactive<Record<string, string>>({})
const nextCursor = ref<string | null>(null)
const nextFailureCursor = ref<string | null>(null)
const nextRunCursor = ref<string | null>(null)
const loading = ref(false)
const failureLoading = ref(false)
const runLoading = ref(false)
const loadError = ref('')
const failureLoadError = ref('')
const runLoadError = ref('')
const failureActionError = ref('')
const actionError = ref('')
const notice = ref('')
const busyCandidateId = ref('')
const busyFailureId = ref('')
const hotGroupIdInput = ref('')
const hotSubjectIdInput = ref('')
const hotGroupId = ref('')
const hotSubjectId = ref('')
const hotFacts = ref<MemoryHotFactView[]>([])
const hotTruncated = ref(false)
const hotLoading = ref(false)
const hotError = ref('')
const hotQueried = ref(false)
const factGroupIdInput = ref('')
const factSubjectIdInput = ref('')
const loadedFactGroupId = ref('')
const loadedFactSubjectId = ref('')
const facts = ref<MemoryFactView[]>([])
const nextFactCursor = ref<string | null>(null)
const factsLoading = ref(false)
const factsLoadError = ref('')
const factsActionError = ref('')
const factsNotice = ref('')
const busyFactId = ref('')
const disableConfirmFactId = ref('')
const correctionDrafts = reactive<Record<string, { sourceId: string; value: string; verified: boolean }>>({})
const conflictCandidateId = ref('')
const conflictTargetFact = ref<MemoryFactView | null>(null)
const conflictSourceVerified = ref(false)
const conflictLoading = ref(false)
const conflictError = ref('')
const retrievalGroupId = ref('')
const retrievalSubjectId = ref('')
const retrievalQuery = ref('')
const retrievalLoading = ref(false)
const retrievalError = ref('')
const retrievalResult = ref<MemoryRetrievalDiagnosticView | null>(null)

let disposed = false
let loadSequence = 0
let loadController: AbortController | undefined
let failureSequence = 0
let failureController: AbortController | undefined
let runSequence = 0
let runController: AbortController | undefined
let hotSequence = 0
let hotController: AbortController | undefined
let factSequence = 0
let factController: AbortController | undefined
let conflictSequence = 0
let conflictController: AbortController | undefined
let retrievalSequence = 0
let retrievalController: AbortController | undefined
let busyCandidateEpoch = 0
let busyFactEpoch = 0

const queryIsDirty = computed(() => loadedGroupId.value !== groupIdInput.value.trim() || loadedStatus.value !== statusFilter.value)
const canSearch = computed(() => sessionState.adminAuthenticated && Boolean(groupIdInput.value.trim()) && !loading.value && !busyCandidateId.value && !busyFailureId.value)
const hasQueried = computed(() => Boolean(loadedGroupId.value))
const canLoadMore = computed(() => Boolean(nextCursor.value) && !loading.value && !busyCandidateId.value && !queryIsDirty.value)
const canLoadMoreFailures = computed(() => Boolean(nextFailureCursor.value)
  && !failureLoading.value && !busyFailureId.value && !queryIsDirty.value)
const canLoadMoreRuns = computed(() => Boolean(nextRunCursor.value)
  && !runLoading.value && !queryIsDirty.value)
const failureEmptyDescription = computed(() => nextFailureCursor.value
  ? '当前页没有仍可见的逐域提取失败；仍有更多诊断，可继续加载。'
  : '当前群没有仍可见的逐域提取失败')
const runEmptyDescription = computed(() => nextRunCursor.value
  ? '当前页没有仍缺少回执的终止提取记录；仍有更多诊断，可继续加载。'
  : '当前群没有仍缺少回执的终止提取记录')
const canRetryFailures = computed(() => sessionState.adminAuthenticated
  && Boolean(loadedGroupId.value) && !queryIsDirty.value && !loading.value
  && !failureLoading.value && !busyCandidateId.value && !busyFailureId.value)
const factQueryIsDirty = computed(() => loadedFactGroupId.value !== factGroupIdInput.value.trim()
  || loadedFactSubjectId.value !== factSubjectIdInput.value.trim())
const canSearchFacts = computed(() => sessionState.adminAuthenticated && Boolean(factGroupIdInput.value.trim())
  && !factsLoading.value && !busyFactId.value)
const canLoadMoreFacts = computed(() => Boolean(nextFactCursor.value) && !factsLoading.value
  && !busyFactId.value && !factQueryIsDirty.value)
const hasQueriedFacts = computed(() => Boolean(loadedFactGroupId.value))
const selectedConflictCandidate = computed<MemoryCandidateView | null>(() => {
  for (const candidate of candidates.value) {
    if (candidate.kind === 'fact' && candidate.candidate_id === conflictCandidateId.value) return candidate
  }
  return null
})

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待审核',
    conflict_pending: '冲突待处理',
    approved: '已批准，尚未应用',
    applied: '已应用',
    rejected: '已拒绝',
    withdrawn: '已撤回',
    skipped: '已跳过',
  }
  return labels[status] ?? status
}

function statusTagType(status: string): 'warning' | 'success' | 'error' | 'default' {
  if (status === 'pending' || status === 'conflict_pending') return 'warning'
  if (status === 'approved' || status === 'applied') return 'success'
  if (status === 'rejected' || status === 'withdrawn') return 'error'
  return 'default'
}

function reviewCandidateStatusLabel(candidate: ReviewCandidateView): string {
  if (candidate.kind !== 'fact' && (candidate.status === 'rejected' || candidate.status === 'withdrawn')) {
    return statusLabel(candidate.status)
  }
  if (candidate.kind !== 'fact' && candidate.application_status === 'applied') return '已应用'
  if (candidate.kind !== 'fact' && candidate.application_status === 'disabled') return '已停用'
  return statusLabel(candidate.status)
}

function episodeStateLabel(state: DomainLearningCandidateView['episode_state']): string {
  if (state === 'dry_run') return '试运行'
  if (state === 'candidate') return '待审核候选'
  if (state === 'approved') return '已批准'
  if (state === 'enabled_for_prompt') return '已应用到提示词'
  if (state === 'disabled') return '已停用'
  return '非事件域'
}

function domainLabel(candidate: DomainLearningCandidateView): string {
  if (candidate.kind === 'slang') return '俚语'
  if (candidate.kind === 'style') return '表达风格'
  return '事件经历'
}

function socialCaptureStatusLabel(status: NonNullable<DomainLearningCandidateView['social']>['capture_status']): string {
  const labels: Record<typeof status, string> = {
    disabled: '未启用',
    pending: '等待捕获',
    captured: '已记录',
    invalidated: '来源已撤权',
    failed: '捕获失败',
  }
  return labels[status]
}

function socialStoryStatusLabel(
  status: NonNullable<DomainLearningCandidateView['social']>['story_status'],
  captureStatus?: NonNullable<DomainLearningCandidateView['social']>['capture_status'],
): string {
  if (captureStatus === 'invalidated') {
    return status === 'committed'
      ? '历史通用剧情已保留（不再作为当前共同经历引用）'
      : '已停止（来源已撤权）'
  }
  const labels: Record<typeof status, string> = {
    disabled: '未启用',
    waiting_for_capture: '等待 Social 捕获',
    pending: '等待提交',
    committed: '已提交通用剧情',
  }
  return labels[status]
}

function socialStatusTagType(status: NonNullable<DomainLearningCandidateView['social']>['capture_status']): 'warning' | 'success' | 'error' | 'default' {
  if (status === 'captured') return 'success'
  if (status === 'invalidated') return 'error'
  if (status === 'pending' || status === 'failed') return 'warning'
  return 'default'
}

function socialStoryTagType(status: NonNullable<DomainLearningCandidateView['social']>['story_status']): 'warning' | 'success' | 'error' | 'default' {
  if (status === 'committed') return 'success'
  if (status === 'pending' || status === 'waiting_for_capture') return 'warning'
  return 'default'
}

function socialErrorReason(code: string): string {
  const labels: Record<string, string> = {
    story_main_missing: '当前群没有可用主线',
    story_main_ambiguous: '当前群主线不唯一',
    social_evidence_disabled: 'Social 证据功能未启用或群未加入允许范围',
    social_experience_invalidated: '来源权限已撤销',
  }
  return labels[code] ?? `服务端错误码：${code}`
}

function socialProgressNotice(social: DomainLearningCandidateView['social']): string {
  if (!social) return 'N7 Social 状态未返回，尚不能确认共同经历是否完成。'
  const capture = socialCaptureStatusLabel(social.capture_status)
  const story = socialStoryStatusLabel(social.story_status, social.capture_status)
  const reason = social.error_code ? ` 阻塞原因：${socialErrorReason(social.error_code)}。` : ''
  return `N7 Social 捕获：${capture}；Story：${story}。${reason}`
}

function episodeApplicationLabel(status: DomainLearningCandidateView['application_status']): string {
  if (status === 'applied') return '已应用'
  if (status === 'disabled') return '已停用'
  return '尚未应用'
}

function failureDomainLabel(domain: DomainLearningFailureView['domain']): string {
  if (domain === 'fact') return '事实'
  if (domain === 'slang') return '俚语'
  if (domain === 'style') return '表达风格'
  return '事件经历'
}

function failureCodeLabel(code: DomainLearningFailureView['error_code'] | DomainLearningRetryView['error_code']): string {
  if (code === 'slang_key_collision') return '词语或别名与现有候选冲突'
  if (code === 'slang_stoplisted') return '词语已被当前范围的停用表禁止'
  if (code === 'incomplete_after_deadline') return '封存期限已过，仍缺少该域的完成回执'
  return '没有可显示的错误码'
}

function extractionRunStatusLabel(status: ExtractionRunDiagnosticView['status']): string {
  if (status === 'cancelled') return '已取消'
  if (status === 'unknown') return '结果未知'
  return '进程中断'
}

function extractionRunStageLabel(stage: ExtractionRunDiagnosticView['stage']): string {
  if (stage === 'extracting') return '提取中'
  if (stage === 'fact') return '事实域'
  if (stage === 'slang') return '俚语域'
  if (stage === 'style') return '表达风格域'
  return '事件经历域'
}

function jsonText(value: JsonValue | undefined): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value === null || value === undefined) return '未记录'
  if (Array.isArray(value)) return value.map((item) => jsonText(item)).join('、') || '无'
  return Object.values(value).map((item) => jsonText(item)).join('、') || '无'
}

function domainValueFields(candidate: DomainLearningCandidateView): Array<{ label: string; value: string }> {
  const value = candidate.value
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return []
  if (candidate.kind === 'slang') {
    return [
      { label: '词语', value: jsonText(value.term) },
      { label: '含义', value: jsonText(value.meaning) },
      { label: '别名', value: jsonText(value.aliases) },
    ]
  }
  if (candidate.kind === 'style') {
    return [
      { label: '情境', value: jsonText(value.situation) },
      { label: '表达方式', value: jsonText(value.style) },
      { label: '输出策略', value: jsonText(value.output_policy) },
      { label: '风险标记', value: jsonText(value.risk_tags) },
    ]
  }
  return [
    { label: '情境', value: jsonText(value.situation) },
    { label: '观察到的上下文', value: jsonText(value.observed_context) },
    { label: '采取的行动', value: jsonText(value.action_taken) },
    { label: '结果信号', value: jsonText(value.outcome_signal) },
    { label: '复盘', value: jsonText(value.reflection) },
  ]
}

function setDomainReviewReason(candidateId: string, reason: string): void {
  domainReviewReasons[candidateId] = reason
}

function actionLabel(action: MemoryCandidateView['action']): string {
  const labels: Record<MemoryCandidateView['action'], string> = {
    add: '新增事实',
    reinforce: '强化现有事实',
    supersede: '替换事实',
    skip: '跳过',
  }
  return labels[action]
}

function suggestionReasonLabel(reason: MemoryCandidateView['suggestion_reason']): string {
  const labels: Record<NonNullable<MemoryCandidateView['suggestion_reason']>, string> = {
    stable_preference: '稳定偏好',
    time_bounded_plan: '有时限的计划',
    communication_boundary: '沟通边界',
    explicit_correction: '明确纠正',
  }
  return reason === null ? '未记录提议理由' : labels[reason]
}

function formatTime(timestamp: number | null): string {
  if (timestamp === null || !Number.isFinite(timestamp)) return '未记录'
  const date = new Date(timestamp * 1000)
  return Number.isNaN(date.getTime())
    ? '未记录'
    : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

function formatValidity(start: number | null, end: number | null): string {
  if (start === null && end === null) return '未限定'
  return `${start === null ? '起点未限定' : formatTime(start)} 至 ${end === null ? '未限定' : formatTime(end)}`
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function memoryErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const labels: Record<string, string> = {
      candidate_not_found: '候选已不存在，请重新读取列表。',
      candidate_not_reviewable: '候选状态已变化，请重新读取后再操作。',
      candidate_not_conflict_pending: '候选冲突状态已变化，请重新读取列表。',
      candidate_not_resolvable: '该冲突类型不能通过此流程解决。',
      candidate_not_approved: '只有已批准的候选可以应用。',
      candidate_not_applicable: '候选状态已变化，不能重复应用。',
      domain_learning_candidate_not_found: '多域候选已不存在或不属于当前群。',
      domain_learning_candidate_not_reviewable: '多域候选状态已变化，请重新读取后再操作。',
      invalid_domain_learning_reason: '请填写本次人工审核理由。',
      domain_learning_failure_revision_conflict: '失败记录修订已变化，请重新读取列表后再试。',
      domain_learning_retry_decision_mismatch: '封存决策与当前失败记录不匹配，请重新读取列表。',
      domain_learning_retry_source_unavailable: '封存决策已过期或不可用，无法重试。',
      source_revision_conflict: '来源修订已变化，请重新读取失败记录。',
      idempotency_conflict: '该失败已按另一条审核理由处理，请重新读取列表。',
      invalid_episode_transition: '事件候选状态已变化，请重新读取。',
      conflict_pending: '候选仍有未解决冲突，服务端拒绝了批准。',
      fact_not_found: '当前事实已不存在或不再可见。',
      fact_not_active: '事实状态已变化，请重新读取列表。',
      stale_memory_card_fact: '当前事实或来源已变化，请重新读取分类。',
      fact_not_current: '目标事实已不再当前有效，请重新读取。',
      correction_stale: '所选来源早于目标事实，请核对最新来源。',
      source_revoked: '来源权限已撤销，服务端已拒绝本次操作。',
      target_suppressed: '目标事实已有待处理纠正，请重新读取。',
      duplicate_active_fact: '同一事实已存在，请重新读取当前事实。',
      invalid_memory_cursor: '分页游标已失效，请重新查询。',
      invalid_memory_page_limit: '候选页长度无效，请重新查询。',
      invalid_memory_status: '候选状态筛选无效，请重新选择。',
      revision_conflict: '事实或候选版本已变化，页面会重读当前状态；请核对后再操作。',
    }
    return labels[error.code] ?? apiErrorMessage(error)
  }
  return apiErrorMessage(error)
}

function isCurrentLoad(sequence: number, epoch: number): boolean {
  return !disposed
    && sequence === loadSequence
    && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentFailureRequest(sequence: number, epoch: number): boolean {
  return !disposed
    && sequence === failureSequence
    && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentRunRequest(sequence: number, epoch: number): boolean {
  return !disposed
    && sequence === runSequence
    && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentMutation(epoch: number): boolean {
  return !disposed && isCurrentAdminEpoch(epoch) && sessionState.adminAuthenticated
}

function isCurrentHotRequest(sequence: number, epoch: number): boolean {
  return !disposed
    && sequence === hotSequence
    && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentFactRequest(sequence: number, epoch: number): boolean {
  return !disposed && sequence === factSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentConflictRequest(sequence: number, epoch: number): boolean {
  return !disposed && sequence === conflictSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function isCurrentRetrievalRequest(sequence: number, epoch: number): boolean {
  return !disposed && sequence === retrievalSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}

function requestUrl(after: string | null): string {
  const params = new URLSearchParams({ group_id: loadedGroupId.value, limit: String(PAGE_SIZE) })
  if (after) params.set('after', after)
  if (loadedStatus.value !== 'all') params.set('status', loadedStatus.value)
  return `/api/admin/memory/candidates?${params.toString()}`
}

function failureRequestUrl(after: string | null): string {
  const params = new URLSearchParams({ group_id: loadedGroupId.value, limit: String(PAGE_SIZE) })
  if (after) params.set('after', after)
  return `/api/admin/memory/domain-failures?${params.toString()}`
}

function extractionRunRequestUrl(after: string | null): string {
  const params = new URLSearchParams({ group_id: loadedGroupId.value, limit: String(PAGE_SIZE) })
  if (after) params.set('after', after)
  return `/api/admin/memory/extraction-run-diagnostics?${params.toString()}`
}

function clearDomainReviewReasons(): void {
  for (const candidateId of Object.keys(domainReviewReasons)) delete domainReviewReasons[candidateId]
}

function isRetryableDomainFailure(failure: DomainLearningFailureView): boolean {
  return failure.status === 'failed'
    && failure.domain === 'slang'
    && (failure.error_code === 'slang_key_collision' || failure.error_code === 'slang_stoplisted')
}

function canRetryDomainFailure(failure: DomainLearningFailureView): boolean {
  return canRetryFailures.value
    && isRetryableDomainFailure(failure)
    && Boolean(domainRetryReasons[failure.result_id]?.trim())
}

async function readPage(after: string | null, replace: boolean): Promise<void> {
  if (!loadedGroupId.value || !sessionState.adminAuthenticated || loading.value) return

  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++loadSequence
  loading.value = true
  loadError.value = ''

  try {
    const page = await apiRequest<MemoryCandidatePageView>(requestUrl(after), { signal: controller.signal })
    if (!isCurrentLoad(sequence, epoch)) return
    if (replace) {
      candidates.value = page.items
    } else {
      const knownIds = new Set(candidates.value.map((candidate) => candidate.candidate_id))
      candidates.value = [...candidates.value, ...page.items.filter((candidate) => !knownIds.has(candidate.candidate_id))]
    }
    nextCursor.value = page.next_cursor
  } catch (error: unknown) {
    if (!isCurrentLoad(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    loadError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

async function readDomainFailures(after: string | null, replace: boolean): Promise<void> {
  if (!loadedGroupId.value || !sessionState.adminAuthenticated) return

  failureController?.abort()
  const controller = new AbortController()
  failureController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++failureSequence
  failureLoading.value = true
  failureLoadError.value = ''

  try {
    const page = await apiRequest<DomainLearningFailurePageView>(failureRequestUrl(after), {
      signal: controller.signal,
    })
    if (!isCurrentFailureRequest(sequence, epoch)) return
    if (replace) {
      domainFailures.value = page.items
    } else {
      const knownIds = new Set(domainFailures.value.map((failure) => failure.result_id))
      domainFailures.value = [
        ...domainFailures.value,
        ...page.items.filter((failure) => !knownIds.has(failure.result_id)),
      ]
    }
    nextFailureCursor.value = page.next_cursor
  } catch (error: unknown) {
    if (!isCurrentFailureRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    failureLoadError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === failureSequence) failureLoading.value = false
  }
}

async function readExtractionRunDiagnostics(after: string | null, replace: boolean): Promise<void> {
  if (!loadedGroupId.value || !sessionState.adminAuthenticated) return

  runController?.abort()
  const controller = new AbortController()
  runController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++runSequence
  runLoading.value = true
  runLoadError.value = ''

  try {
    const page = await apiRequest<ExtractionRunDiagnosticPageView>(extractionRunRequestUrl(after), {
      signal: controller.signal,
    })
    if (!isCurrentRunRequest(sequence, epoch)) return
    if (replace) {
      extractionRuns.value = page.items
    } else {
      const knownIds = new Set(extractionRuns.value.map((run) => run.run_id))
      extractionRuns.value = [
        ...extractionRuns.value,
        ...page.items.filter((run) => !knownIds.has(run.run_id)),
      ]
    }
    nextRunCursor.value = page.next_cursor
  } catch (error: unknown) {
    if (!isCurrentRunRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    runLoadError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === runSequence) runLoading.value = false
  }
}

async function retryDomainFailure(failure: DomainLearningFailureView): Promise<void> {
  if (!canRetryDomainFailure(failure)) return
  const reason = domainRetryReasons[failure.result_id]?.trim()
  if (!reason) return
  const epoch = currentAdminEpoch()
  busyFailureId.value = failure.result_id
  failureActionError.value = ''
  notice.value = ''
  try {
    const body: DomainLearningRetryRequest = {
      group_id: loadedGroupId.value,
      result_id: failure.result_id,
      failure_revision: failure.failure_revision,
      reason,
    }
    const result = await apiRequest<DomainLearningRetryView>(
      '/api/admin/memory/domain-failures/retry',
      { method: 'POST', adminMutation: true, body },
    )
    if (!isCurrentMutation(epoch)) return
    if (result.status === 'candidates') {
      delete domainRetryReasons[failure.result_id]
      notice.value = `已从封存决策重试俚语域，生成 ${result.candidate_count} 条待审核候选。`
    } else {
      failureActionError.value = `重试仍失败：${failureCodeLabel(result.error_code)}。失败记录未变更。`
    }
    await Promise.all([readPage(null, true), readDomainFailures(null, true)])
  } catch (error: unknown) {
    if (!isCurrentMutation(epoch)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    failureActionError.value = memoryErrorMessage(error)
    if (isApiError(error) && error.status === 409) {
      await Promise.all([readPage(null, true), readDomainFailures(null, true)])
    }
  } finally {
    if (isCurrentMutation(epoch) && busyFailureId.value === failure.result_id) {
      busyFailureId.value = ''
    }
  }
}

async function searchCandidates(): Promise<void> {
  const exactGroupId = groupIdInput.value.trim()
  if (!exactGroupId) {
    loadError.value = '请填写准确的群 ID。'
    return
  }
  if (!sessionState.adminAuthenticated || loading.value || busyCandidateId.value || busyFailureId.value) return

  loadSequence += 1
  loadController?.abort()
  failureSequence += 1
  failureController?.abort()
  runSequence += 1
  runController?.abort()
  loadedGroupId.value = exactGroupId
  loadedStatus.value = statusFilter.value
  candidates.value = []
  domainFailures.value = []
  extractionRuns.value = []
  failureActionError.value = ''
  clearDomainReviewReasons()
  for (const resultId of Object.keys(domainRetryReasons)) delete domainRetryReasons[resultId]
  nextCursor.value = null
  nextFailureCursor.value = null
  nextRunCursor.value = null
  loadError.value = ''
  failureLoadError.value = ''
  runLoadError.value = ''
  actionError.value = ''
  notice.value = ''
  await Promise.all([
    readPage(null, true),
    readDomainFailures(null, true),
    readExtractionRunDiagnostics(null, true),
  ])
}

async function loadMore(): Promise<void> {
  if (!canLoadMore.value || !nextCursor.value) return
  await readPage(nextCursor.value, false)
}

async function loadMoreDomainFailures(): Promise<void> {
  if (!canLoadMoreFailures.value || !nextFailureCursor.value) return
  await readDomainFailures(nextFailureCursor.value, false)
}

async function loadMoreExtractionRuns(): Promise<void> {
  if (!canLoadMoreRuns.value || !nextRunCursor.value) return
  await readExtractionRunDiagnostics(nextRunCursor.value, false)
}

function factsRequestUrl(after: string | null): string {
  const params = new URLSearchParams({ group_id: loadedFactGroupId.value, limit: String(PAGE_SIZE) })
  if (after) params.set('after', after)
  if (loadedFactSubjectId.value) params.set('subject_id', loadedFactSubjectId.value)
  return `/api/admin/memory/facts?${params.toString()}`
}

async function readFactsPage(after: string | null, replace: boolean): Promise<void> {
  if (!loadedFactGroupId.value || !sessionState.adminAuthenticated || factsLoading.value) return

  factController?.abort()
  const controller = new AbortController()
  factController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++factSequence
  factsLoading.value = true
  factsLoadError.value = ''
  try {
    const page = await apiRequest<MemoryFactPageView>(factsRequestUrl(after), { signal: controller.signal })
    if (!isCurrentFactRequest(sequence, epoch)) return
    if (replace) {
      facts.value = page.items
    } else {
      const knownIds = new Set(facts.value.map((fact) => fact.fact_id))
      facts.value = [...facts.value, ...page.items.filter((fact) => !knownIds.has(fact.fact_id))]
    }
    nextFactCursor.value = page.next_cursor
  } catch (error: unknown) {
    if (!isCurrentFactRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    factsLoadError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === factSequence) factsLoading.value = false
  }
}

async function searchFacts(): Promise<void> {
  const exactGroupId = factGroupIdInput.value.trim()
  const exactSubjectId = factSubjectIdInput.value.trim()
  if (!exactGroupId) {
    factsLoadError.value = '请填写准确的群 ID。'
    return
  }
  if (!sessionState.adminAuthenticated || factsLoading.value || busyFactId.value) return

  factSequence += 1
  factController?.abort()
  loadedFactGroupId.value = exactGroupId
  loadedFactSubjectId.value = exactSubjectId
  facts.value = []
  nextFactCursor.value = null
  factsLoadError.value = ''
  factsActionError.value = ''
  factsNotice.value = ''
  disableConfirmFactId.value = ''
  await readFactsPage(null, true)
}

async function loadMoreFacts(): Promise<void> {
  if (!canLoadMoreFacts.value || !nextFactCursor.value) return
  await readFactsPage(nextFactCursor.value, false)
}

async function refreshFactsAndCandidates(epoch: number): Promise<void> {
  await readFactsPage(null, true)
  if (isCurrentMutation(epoch)
    && loadedGroupId.value === loadedFactGroupId.value
    && !queryIsDirty.value
    && !loading.value) {
    await readPage(null, true)
  }
}

function correctionDraft(fact: MemoryFactView): { sourceId: string; value: string; verified: boolean } {
  const current = correctionDrafts[fact.fact_id]
  if (current) return current
  const draft = { sourceId: '', value: '', verified: false }
  correctionDrafts[fact.fact_id] = draft
  return draft
}

function toggleCorrection(fact: MemoryFactView): void {
  if (correctionDrafts[fact.fact_id]) delete correctionDrafts[fact.fact_id]
  else correctionDraft(fact)
}

function canCreateCorrection(fact: MemoryFactView): boolean {
  const draft = correctionDrafts[fact.fact_id]
  return sessionState.adminAuthenticated && !factQueryIsDirty.value && !factsLoading.value
    && !busyFactId.value && fact.status === 'active' && Boolean(draft?.verified)
    && fact.source_ids.includes(draft?.sourceId ?? '') && Boolean(draft?.value.trim())
}

function replaceCandidate(updated: ReviewCandidateView): void {
  candidates.value = candidates.value.map((candidate) => candidate.candidate_id === updated.candidate_id ? updated : candidate)
}

function canReview(candidate: ReviewCandidateView): boolean {
  return sessionState.adminAuthenticated
    && !queryIsDirty.value
    && !loading.value
    && !busyCandidateId.value
    && (candidate.status === 'pending' || candidate.status === 'conflict_pending')
    && (candidate.kind === 'fact' || Boolean(domainReviewReasons[candidate.candidate_id]?.trim()))
}

function canApply(candidate: ReviewCandidateView): boolean {
  return sessionState.adminAuthenticated
    && !queryIsDirty.value
    && !loading.value
    && !busyCandidateId.value
    && candidate.status === 'approved'
    && (candidate.kind === 'fact' || candidate.application_status === 'not_applied')
}

function canRetrySocial(candidate: ReviewCandidateView): boolean {
  return sessionState.adminAuthenticated
    && Boolean(loadedGroupId.value)
    && !queryIsDirty.value
    && !loading.value
    && !busyCandidateId.value
    && candidate.kind === 'episode'
    && candidate.application_status === 'applied'
    && Boolean(candidate.social?.retryable)
}

function handleMutationFailure(error: unknown, epoch: number): boolean {
  if (!isCurrentMutation(epoch) || isAbortError(error)) return false
  if (isApiError(error) && error.status === 401) {
    expireAdminSession()
    return false
  }
  actionError.value = memoryErrorMessage(error)
  notice.value = '操作结果未确认或候选版本已变化，正在重读列表；请核对最新状态后再操作。'
  return true
}

async function reviewCandidate(candidate: ReviewCandidateView, decision: MemoryReviewRequest['decision']): Promise<void> {
  if (!canReview(candidate)) return
  if (candidate.kind !== 'fact' && decision === 'withdrawn') return
  if (candidate.kind === 'fact' && decision === 'candidate') return
  if (decision === 'approved' && candidate.status === 'conflict_pending') return

  const epoch = currentAdminEpoch()
  busyCandidateId.value = candidate.candidate_id
  busyCandidateEpoch = epoch
  actionError.value = ''
  notice.value = ''
  let reconcile = false
  try {
    const body: MemoryReviewRequest = candidate.kind === 'fact'
      ? {
        group_id: loadedGroupId.value,
        candidate_id: candidate.candidate_id,
        expected_revision: candidate.candidate_revision,
        kind: 'fact',
        decision,
      }
      : {
        group_id: loadedGroupId.value,
        candidate_id: candidate.candidate_id,
        expected_revision: candidate.candidate_revision,
        kind: candidate.kind,
        decision,
        reason: domainReviewReasons[candidate.candidate_id]?.trim() ?? '',
      }
    const updated = await apiRequest<ReviewCandidateView>('/api/admin/memory/review', {
      method: 'POST',
      adminMutation: true,
      body,
    })
    if (!isCurrentMutation(epoch)) return
    replaceCandidate(updated)
    if (candidate.kind !== 'fact') delete domainReviewReasons[candidate.candidate_id]
    if (decision === 'candidate') notice.value = '已人工确认该事件候选进入审核阶段；它仍未批准，也未应用。'
    else if (decision === 'approved') notice.value = '已批准。候选仍未应用；请检查后单独选择“应用”。'
    else if (decision === 'rejected') notice.value = '候选已拒绝。'
    else notice.value = '候选已撤回。'
  } catch (error: unknown) {
    reconcile = handleMutationFailure(error, epoch)
  } finally {
    if (busyCandidateId.value === candidate.candidate_id && busyCandidateEpoch === epoch) {
      busyCandidateId.value = ''
      busyCandidateEpoch = 0
    }
  }

  if (reconcile && isCurrentMutation(epoch)) await readPage(null, true)
}

async function applyCandidate(candidate: ReviewCandidateView): Promise<void> {
  if (!canApply(candidate)) return

  const epoch = currentAdminEpoch()
  busyCandidateId.value = candidate.candidate_id
  busyCandidateEpoch = epoch
  actionError.value = ''
  notice.value = ''
  let reconcile = false
  try {
    const body: MemoryApplyRequest = {
      group_id: loadedGroupId.value,
      candidate_id: candidate.candidate_id,
      expected_revision: candidate.candidate_revision,
      kind: candidate.kind,
    }
    const updated = await apiRequest<ReviewCandidateView>('/api/admin/memory/apply', {
      method: 'POST',
      adminMutation: true,
      body,
    })
    if (!isCurrentMutation(epoch)) return
    replaceCandidate(updated)
    if (candidate.kind === 'fact') {
      notice.value = '事实已应用。来源、有效期与群授权仍由服务端控制。'
    } else if (candidate.kind === 'episode' && updated.kind === 'episode') {
      notice.value = `N6 事件经历${episodeApplicationLabel(updated.application_status)}；${socialProgressNotice(updated.social)}`
    } else {
      notice.value = '多域候选已应用。'
    }
  } catch (error: unknown) {
    reconcile = handleMutationFailure(error, epoch)
  } finally {
    if (busyCandidateId.value === candidate.candidate_id && busyCandidateEpoch === epoch) {
      busyCandidateId.value = ''
      busyCandidateEpoch = 0
    }
  }

  if (reconcile && isCurrentMutation(epoch)) await readPage(null, true)
}

async function retrySocialExperience(candidate: ReviewCandidateView): Promise<void> {
  if (!canRetrySocial(candidate) || candidate.kind !== 'episode') return

  const epoch = currentAdminEpoch()
  busyCandidateId.value = candidate.candidate_id
  busyCandidateEpoch = epoch
  actionError.value = ''
  notice.value = ''
  let reconcile = false
  try {
    const updated = await apiRequest<DomainLearningCandidateView>('/api/admin/memory/social/retry', {
      method: 'POST',
      adminMutation: true,
      body: {
        group_id: loadedGroupId.value,
        candidate_id: candidate.candidate_id,
      },
    })
    if (!isCurrentMutation(epoch)) return
    replaceCandidate(updated)
    notice.value = `N6 事件经历仍为${episodeApplicationLabel(updated.application_status)}；${socialProgressNotice(updated.social)}`
  } catch (error: unknown) {
    reconcile = handleMutationFailure(error, epoch)
  } finally {
    if (busyCandidateId.value === candidate.candidate_id && busyCandidateEpoch === epoch) {
      busyCandidateId.value = ''
      busyCandidateEpoch = 0
    }
  }

  if (reconcile && isCurrentMutation(epoch)) await readPage(null, true)
}

async function searchHotFacts(): Promise<void> {
  const exactGroupId = hotGroupIdInput.value.trim()
  const exactSubjectId = hotSubjectIdInput.value.trim()
  if (!exactGroupId || !exactSubjectId) {
    hotError.value = '请填写准确的群 ID 和当前发言者主体 ID。'
    return
  }
  if (!sessionState.adminAuthenticated || hotLoading.value) return

  hotController?.abort()
  const controller = new AbortController()
  hotController = controller
  const sequence = ++hotSequence
  const epoch = currentAdminEpoch()
  hotGroupId.value = exactGroupId
  hotSubjectId.value = exactSubjectId
  hotFacts.value = []
  hotTruncated.value = false
  hotQueried.value = true
  hotError.value = ''
  hotLoading.value = true

  const params = new URLSearchParams({ group_id: exactGroupId, subject_id: exactSubjectId })
  try {
    const preview = await apiRequest<MemoryHotPreviewView>(`/api/admin/memory/hot?${params.toString()}`, {
      signal: controller.signal,
    })
    if (!isCurrentHotRequest(sequence, epoch)) return
    hotFacts.value = preview.items
    hotTruncated.value = preview.truncated
  } catch (error: unknown) {
    if (!isCurrentHotRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    hotError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === hotSequence) hotLoading.value = false
  }
}

function candidateBusy(candidate: ReviewCandidateView): boolean {
  return busyCandidateId.value === candidate.candidate_id
}

function factBusy(fact: MemoryFactView): boolean {
  return busyFactId.value === fact.fact_id
}

function isCurrentConflictMutation(epoch: number): boolean {
  return !disposed && isCurrentAdminEpoch(epoch) && sessionState.adminAuthenticated
}

async function submitCorrection(fact: MemoryFactView): Promise<void> {
  if (!canCreateCorrection(fact)) return
  const draft = correctionDraft(fact)
  const epoch = currentAdminEpoch()
  busyFactId.value = fact.fact_id
  busyFactEpoch = epoch
  factsActionError.value = ''
  factsNotice.value = ''
  let reconcile = false
  try {
    const body = {
      group_id: loadedFactGroupId.value,
      fact_id: fact.fact_id,
      expected_fact_revision: fact.fact_revision,
      source_id: draft.sourceId,
      subject_id: fact.subject_id,
      predicate: fact.predicate,
      value: draft.value.trim(),
    } satisfies MemoryCorrectionRequest
    const created = await apiRequest<MemoryCandidateView>('/api/admin/memory/correction', {
      method: 'POST',
      adminMutation: true,
      body,
    })
    if (!isCurrentMutation(epoch)) return
    delete correctionDrafts[fact.fact_id]
    if (loadedGroupId.value === loadedFactGroupId.value
      && (loadedStatus.value === 'all' || loadedStatus.value === created.status)) {
      candidates.value = [created, ...candidates.value.filter((item) => item.candidate_id !== created.candidate_id)]
    }
    factsNotice.value = `已建立纠错候选（${created.status}）。请在候选区按冲突核验、批准和应用分步处理。`
  } catch (error: unknown) {
    if (!isCurrentMutation(epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    factsActionError.value = memoryErrorMessage(error)
    factsNotice.value = '已保留纠错草稿；正在重读当前事实与候选状态。'
    reconcile = true
  } finally {
    if (busyFactId.value === fact.fact_id && busyFactEpoch === epoch) {
      busyFactId.value = ''
      busyFactEpoch = 0
    }
  }

  if (reconcile && isCurrentMutation(epoch)) {
    await refreshFactsAndCandidates(epoch)
  }
}

function requestDisableFact(fact: MemoryFactView): void {
  if (!sessionState.adminAuthenticated || factQueryIsDirty.value || factsLoading.value || busyFactId.value) return
  disableConfirmFactId.value = fact.fact_id
}

function cancelDisableFact(): void {
  disableConfirmFactId.value = ''
}

async function confirmDisableFact(fact: MemoryFactView): Promise<void> {
  if (disableConfirmFactId.value !== fact.fact_id || fact.status !== 'active'
    || !sessionState.adminAuthenticated || factQueryIsDirty.value || factsLoading.value || busyFactId.value) return

  const epoch = currentAdminEpoch()
  busyFactId.value = fact.fact_id
  busyFactEpoch = epoch
  factsActionError.value = ''
  factsNotice.value = ''
  disableConfirmFactId.value = ''
  let reconcile = false
  try {
    const body = {
      group_id: loadedFactGroupId.value,
      fact_id: fact.fact_id,
      expected_revision: fact.fact_revision,
      reason: 'admin_disabled',
    } satisfies MemoryDisableFactRequest
    const disabled = await apiRequest<MemoryFactView>('/api/admin/memory/disable', {
      method: 'POST',
      adminMutation: true,
      body,
    })
    if (!isCurrentMutation(epoch)) return
    facts.value = facts.value.filter((item) => item.fact_id !== disabled.fact_id)
    delete correctionDrafts[fact.fact_id]
    factsNotice.value = '事实已停用，并从当前有效事实列表移除；针对它的待处理候选由服务端撤回。'
    if (loadedGroupId.value === loadedFactGroupId.value && !queryIsDirty.value && !loading.value) {
      await readPage(null, true)
    }
  } catch (error: unknown) {
    if (!isCurrentMutation(epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    factsActionError.value = memoryErrorMessage(error)
    factsNotice.value = '操作结果未确认；正在重读当前事实与候选状态，请核对后再操作。'
    reconcile = true
  } finally {
    if (busyFactId.value === fact.fact_id && busyFactEpoch === epoch) {
      busyFactId.value = ''
      busyFactEpoch = 0
    }
  }
  if (reconcile && isCurrentMutation(epoch)) await refreshFactsAndCandidates(epoch)
}

async function inspectConflictTarget(candidate: MemoryCandidateView): Promise<void> {
  if (!canReview(candidate) || candidate.status !== 'conflict_pending'
    || candidate.action !== 'supersede' || !candidate.target_fact_id) return
  conflictController?.abort()
  const controller = new AbortController()
  conflictController = controller
  const sequence = ++conflictSequence
  const epoch = currentAdminEpoch()
  conflictCandidateId.value = candidate.candidate_id
  conflictTargetFact.value = null
  conflictSourceVerified.value = false
  conflictError.value = ''
  conflictLoading.value = true
  const params = new URLSearchParams({ group_id: loadedGroupId.value })
  try {
    const fact = await apiRequest<MemoryFactView>(
      `/api/admin/memory/facts/${encodeURIComponent(candidate.target_fact_id)}?${params.toString()}`,
      { signal: controller.signal },
    )
    if (!isCurrentConflictRequest(sequence, epoch)) return
    if (fact.fact_id !== candidate.target_fact_id || fact.status !== 'active') {
      conflictError.value = '当前目标与候选记录不一致，请重新读取候选。'
      return
    }
    conflictTargetFact.value = fact
  } catch (error: unknown) {
    if (!isCurrentConflictRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    conflictError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === conflictSequence) conflictLoading.value = false
  }
}

function cancelConflictResolution(): void {
  conflictSequence += 1
  conflictController?.abort()
  conflictController = undefined
  conflictCandidateId.value = ''
  conflictTargetFact.value = null
  conflictSourceVerified.value = false
  conflictLoading.value = false
  conflictError.value = ''
}

function canResolveConflict(candidate: MemoryCandidateView): boolean {
  return canReview(candidate) && candidate.status === 'conflict_pending'
    && candidate.action === 'supersede'
    && candidate.candidate_id === conflictCandidateId.value
    && conflictTargetFact.value?.fact_id === candidate.target_fact_id
    && candidate.source_ids.length > 0
    && conflictSourceVerified.value && !conflictLoading.value
}

async function resolveConflict(candidate: MemoryCandidateView): Promise<void> {
  if (!canResolveConflict(candidate)) return
  const epoch = currentAdminEpoch()
  busyCandidateId.value = candidate.candidate_id
  busyCandidateEpoch = epoch
  actionError.value = ''
  notice.value = ''
  let reconcile = false
  try {
    const body = {
      group_id: loadedGroupId.value,
      candidate_id: candidate.candidate_id,
      expected_revision: candidate.candidate_revision,
      expected_target_revision: conflictTargetFact.value?.fact_revision ?? 0,
      reason: 'verified_source',
    } satisfies MemoryResolveConflictRequest
    const updated = await apiRequest<MemoryCandidateView>('/api/admin/memory/resolve-conflict', {
      method: 'POST',
      adminMutation: true,
      body,
    })
    if (!isCurrentMutation(epoch)) return
    replaceCandidate(updated)
    conflictCandidateId.value = ''
    conflictTargetFact.value = null
    conflictSourceVerified.value = false
    notice.value = '冲突已核验并移入待审核；随后仍需单独批准，再单独应用。'
  } catch (error: unknown) {
    reconcile = handleMutationFailure(error, epoch)
    if (reconcile) {
      conflictTargetFact.value = null
      conflictSourceVerified.value = false
    }
  } finally {
    if (busyCandidateId.value === candidate.candidate_id && busyCandidateEpoch === epoch) {
      busyCandidateId.value = ''
      busyCandidateEpoch = 0
    }
  }
  if (reconcile && isCurrentMutation(epoch)) await readPage(null, true)
}

async function runRetrievalDiagnostic(): Promise<void> {
  const groupId = retrievalGroupId.value.trim()
  if (!groupId) {
    retrievalError.value = '请填写准确的群 ID。'
    return
  }
  if (!sessionState.adminAuthenticated || retrievalLoading.value) return
  retrievalController?.abort()
  const controller = new AbortController()
  retrievalController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++retrievalSequence
  retrievalLoading.value = true
  retrievalError.value = ''
  retrievalResult.value = null
  try {
    const body = {
      group_id: groupId,
      query: retrievalQuery.value,
      subject_id: retrievalSubjectId.value.trim() || null,
    } satisfies MemoryRetrievalDiagnosticRequest
    retrievalResult.value = await apiRequest<MemoryRetrievalDiagnosticView>(
      '/api/admin/memory/retrieval-diagnostic',
      { method: 'POST', adminMutation: true, body, signal: controller.signal },
    )
    if (!isCurrentRetrievalRequest(sequence, epoch)) retrievalResult.value = null
  } catch (error: unknown) {
    if (!isCurrentRetrievalRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    retrievalError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === retrievalSequence) retrievalLoading.value = false
  }
}

type CardCategory = NonNullable<MemoryCardMetadataView['category']>
const cardCategoryOptions = [
  { label: '偏好', value: 'preference' }, { label: '边界', value: 'boundary' },
  { label: '关系', value: 'relationship' }, { label: '事件', value: 'event' },
  { label: '承诺', value: 'promise' }, { label: '事实', value: 'fact' },
  { label: '当前状态', value: 'status' },
]
const cardFilterOptions = [{ label: '全部分类', value: 'all' }, ...cardCategoryOptions]
const cardGroupInput = ref('')
const cardQueryInput = ref('')
const cardFilter = ref('all')
const cardFactIdInput = ref('')
const loadedCardGroup = ref('')
const cardPage = shallowRef<MemoryCardQueryView | null>(null)
const cardMetadata = shallowRef<MemoryCardMetadataView | null>(null)
const cardCategoryDraft = ref<CardCategory>('fact')
const cardBusy = ref(false)
const cardError = ref('')
const cardNotice = ref('')
let cardSequence = 0
let cardController: AbortController | undefined

function clearCardState(): void {
  cardSequence += 1
  cardController?.abort()
  cardController = undefined
  cardBusy.value = false
  cardPage.value = null
  cardMetadata.value = null
  loadedCardGroup.value = ''
  cardCategoryDraft.value = 'fact'
  cardError.value = ''
  cardNotice.value = ''
}
watch([cardGroupInput, cardQueryInput, cardFilter], clearCardState, { flush: 'sync' })
watch(() => sessionState.adminAuthenticated, authenticated => {
  if (!authenticated) clearCardState()
}, { flush: 'sync' })
watch(cardFactIdInput, () => {
  cardSequence += 1
  cardController?.abort()
  cardBusy.value = false
  cardMetadata.value = null
  cardCategoryDraft.value = 'fact'
  cardNotice.value = ''
}, { flush: 'sync' })

function isCurrentCardRequest(sequence: number, epoch: number): boolean {
  return !disposed && sequence === cardSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated
}
const canWriteCard = computed(() => sessionState.adminAuthenticated && !cardBusy.value
  && cardMetadata.value !== null && loadedCardGroup.value === cardGroupInput.value.trim()
  && cardMetadata.value.fact.fact_id === cardFactIdInput.value.trim())

async function loadCards(factId?: string): Promise<void> {
  if (!sessionState.adminAuthenticated || cardBusy.value) return
  const groupId = cardGroupInput.value.trim()
  if (!groupId) { cardError.value = '请填写准确的群 ID。'; return }
  if (factId !== undefined) cardFactIdInput.value = factId
  const target = factId === undefined ? null : factId.trim()
  if (target === '') { cardError.value = '请填写已应用事实 ID。'; return }
  cardController?.abort()
  const controller = new AbortController()
  cardController = controller
  const sequence = ++cardSequence
  const epoch = currentAdminEpoch()
  cardBusy.value = true
  cardError.value = ''
  cardNotice.value = ''
  cardMetadata.value = null
  if (target === null) cardPage.value = null
  loadedCardGroup.value = groupId
  const params = new URLSearchParams({ group_id: groupId })
  try {
    if (target === null) {
      params.set('query', cardQueryInput.value)
      params.set('limit', String(PAGE_SIZE))
      if (cardFilter.value !== 'all') params.set('category', cardFilter.value)
      const page = await apiRequest<MemoryCardQueryView>(`/api/admin/memory/cards?${params}`, {
        signal: controller.signal,
      })
      if (isCurrentCardRequest(sequence, epoch)) cardPage.value = page
    } else {
      const metadata = await apiRequest<MemoryCardMetadataView>(
        `/api/admin/memory/cards/${encodeURIComponent(target)}?${params}`, { signal: controller.signal },
      )
      if (!isCurrentCardRequest(sequence, epoch)) return
      cardMetadata.value = metadata
      cardCategoryDraft.value = metadata.category ?? 'fact'
    }
  } catch (error: unknown) {
    if (!isCurrentCardRequest(sequence, epoch) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) { expireAdminSession(); return }
    cardError.value = memoryErrorMessage(error)
  } finally {
    if (sequence === cardSequence) cardBusy.value = false
  }
}

async function mutateCard(clear: boolean): Promise<void> {
  if (!canWriteCard.value || cardMetadata.value === null) return
  const metadata = cardMetadata.value
  const groupId = loadedCardGroup.value
  const factId = metadata.fact.fact_id
  const controller = new AbortController()
  cardController = controller
  const sequence = ++cardSequence
  const epoch = currentAdminEpoch()
  cardBusy.value = true
  cardError.value = ''
  cardNotice.value = ''
  const identity = {
    group_id: groupId, fact_id: factId,
    expected_fact_revision: metadata.fact.fact_revision,
    expected_classification_revision: metadata.classification_revision,
  } satisfies MemoryCardClearRequest
  const body = clear ? identity : {
    ...identity, category: cardCategoryDraft.value,
  } satisfies MemoryCardClassificationRequest
  try {
    await apiRequest(`/api/admin/memory/cards/${clear ? 'clear' : 'classify'}`, {
      method: 'POST', adminMutation: true, body, signal: controller.signal,
    })
    if (!isCurrentCardRequest(sequence, epoch)) return
    // A committed mutation is followed by a fresh owner snapshot, including clear tombstones.
    cardPage.value = null
    cardMetadata.value = null
    const params = new URLSearchParams({ group_id: groupId })
    const current = await apiRequest<MemoryCardMetadataView>(
      `/api/admin/memory/cards/${encodeURIComponent(factId)}?${params}`, { signal: controller.signal },
    )
    if (!isCurrentCardRequest(sequence, epoch)) return
    cardMetadata.value = current
    cardCategoryDraft.value = current.category ?? 'fact'
    cardNotice.value = clear ? '分类已清除，事实正文未修改。' : '分类已保存，事实正文未修改。'
  } catch (error: unknown) {
    if (!isCurrentCardRequest(sequence, epoch) || isAbortError(error)) return
    cardMetadata.value = null
    cardPage.value = null
    if (isApiError(error) && error.status === 401) { expireAdminSession(); return }
    cardError.value = `${memoryErrorMessage(error)} 请重新读取事实分类后再操作。`
  } finally {
    if (sequence === cardSequence) cardBusy.value = false
  }
}

onMounted(() => {
  disposed = false
})

onBeforeUnmount(() => {
  clearCardState()
  disposed = true
  loadSequence += 1
  loadController?.abort()
  loadController = undefined
  failureSequence += 1
  failureController?.abort()
  failureController = undefined
  runSequence += 1
  runController?.abort()
  runController = undefined
  hotSequence += 1
  hotController?.abort()
  hotController = undefined
  factSequence += 1
  factController?.abort()
  factController = undefined
  conflictSequence += 1
  conflictController?.abort()
  conflictController = undefined
  retrievalSequence += 1
  retrievalController?.abort()
  retrievalController = undefined
  busyFailureId.value = ''
})

watch(() => sessionState.adminAuthenticated, (authenticated) => {
  if (authenticated) return
  loadSequence += 1
  loadController?.abort()
  loadController = undefined
  failureSequence += 1
  failureController?.abort()
  failureController = undefined
  runSequence += 1
  runController?.abort()
  runController = undefined
  hotSequence += 1
  hotController?.abort()
  hotController = undefined
  factSequence += 1
  factController?.abort()
  factController = undefined
  conflictSequence += 1
  conflictController?.abort()
  conflictController = undefined
  retrievalSequence += 1
  retrievalController?.abort()
  retrievalController = undefined
  busyFailureId.value = ''

  candidates.value = []
  domainFailures.value = []
  extractionRuns.value = []
  clearDomainReviewReasons()
  for (const resultId of Object.keys(domainRetryReasons)) delete domainRetryReasons[resultId]
  nextCursor.value = null
  nextFailureCursor.value = null
  nextRunCursor.value = null
  loadedGroupId.value = ''
  groupIdInput.value = ''
  loadedStatus.value = 'all'
  statusFilter.value = 'all'
  hotGroupIdInput.value = ''
  hotSubjectIdInput.value = ''
  hotFacts.value = []
  hotTruncated.value = false
  hotQueried.value = false
  hotGroupId.value = ''
  hotSubjectId.value = ''
  facts.value = []
  nextFactCursor.value = null
  loadedFactGroupId.value = ''
  loadedFactSubjectId.value = ''
  factGroupIdInput.value = ''
  factSubjectIdInput.value = ''
  for (const factId of Object.keys(correctionDrafts)) delete correctionDrafts[factId]
  disableConfirmFactId.value = ''
  cancelConflictResolution()
  retrievalResult.value = null
  retrievalGroupId.value = ''
  retrievalSubjectId.value = ''
  retrievalQuery.value = ''

  loading.value = false
  failureLoading.value = false
  runLoading.value = false
  hotLoading.value = false
  factsLoading.value = false
  conflictLoading.value = false
  retrievalLoading.value = false
  busyCandidateId.value = ''
  busyCandidateEpoch = 0
  busyFactId.value = ''
  busyFactEpoch = 0
  loadError.value = ''
  failureLoadError.value = ''
  runLoadError.value = ''
  failureActionError.value = ''
  actionError.value = ''
  notice.value = ''
  hotError.value = ''
  factsLoadError.value = ''
  factsActionError.value = ''
  factsNotice.value = ''
  retrievalError.value = ''
})
</script>

<template>
  <section class="memory-view" aria-label="记忆候选审核">
    <StyleManagement />
    <AutoLearningManagement />
    <ObservationManagement />
    <MemoryIdentityEpisodeManagement />
    <MattersManagement />
    <n-alert class="notice" type="info" :show-icon="true">
      候选列表统一显示事实、俚语、表达风格和事件经历，只展示结构化候选与来源身份，不回显 Archive 原文。批准和应用是分开的人工操作；episode 的 dry_run 还需先人工确认进入审核阶段。热事实快照只列出输入主体在该群内、获权且当前有效的已应用事实，不是完整聊天上下文。查询需要同源管理员会话和对应群的 <code>memory.retrieve</code>；建立纠错候选需要 <code>memory.learn</code>，审核需要 <code>memory.review</code>，停用或应用需要 <code>memory.apply</code>。服务端逐次复核权限、scope 与候选版本。
    </n-alert>

    <n-card class="page-card" title="记忆卡片分类" :bordered="false">
      <n-text depth="3">管理已应用事实的七类分类；正文纠错和停用仍使用下方事实流程。列表按当前来源与分类时限筛选，未分类事实可按事实 ID 读取。</n-text>
      <div class="form-grid">
        <label class="form-field">群 ID<n-input v-model:value="cardGroupInput" maxlength="64" :input-props="{ 'aria-label': '卡片的准确群 ID' }" placeholder="准确的群 ID" /></label>
        <label class="form-field">查询词<n-input v-model:value="cardQueryInput" maxlength="8192" :input-props="{ 'aria-label': '卡片查询词' }" placeholder="中文词面查询" /></label>
        <label class="form-field">分类<n-select v-model:value="cardFilter" aria-label="卡片分类筛选" :options="cardFilterOptions" /></label>
        <n-button :loading="cardBusy" :disabled="!sessionState.adminAuthenticated || cardBusy" @click="loadCards()">查询卡片</n-button>
      </div>
      <n-alert v-if="cardError" type="error" class="notice" role="alert">{{ cardError }}</n-alert>
      <n-alert v-if="cardNotice" type="success" class="notice" role="status">{{ cardNotice }}</n-alert>
      <template v-if="cardPage">
        <n-text depth="3">当前可用 {{ cardPage.total_active }} 条，匹配 {{ cardPage.matched_active }} 条，显示前 {{ cardPage.items.length }} 条。</n-text>
        <div v-for="card in cardPage.items" :key="card.fact.fact_id" class="memory-fact">
          <n-text>{{ card.fact.value }}</n-text>
          <n-tag>{{ cardCategoryOptions.find(option => option.value === card.category)?.label }}</n-tag>
          <n-button :disabled="cardBusy" @click="loadCards(card.fact.fact_id)">读取分类</n-button>
        </div>
        <n-empty v-if="cardPage.items.length === 0" description="当前查询没有可用卡片" />
      </template>
      <div class="form-grid">
        <label class="form-field">已应用事实 ID<n-input v-model:value="cardFactIdInput" maxlength="128" :input-props="{ 'aria-label': '已应用事实 ID' }" placeholder="从事实列表复制 ID" /></label>
        <n-button :disabled="!sessionState.adminAuthenticated || cardBusy" @click="loadCards(cardFactIdInput)">读取事实分类</n-button>
      </div>
      <div v-if="cardMetadata" class="memory-fact">
        <n-text>{{ cardMetadata.fact.value }}</n-text>
        <n-text depth="3">{{ cardMetadata.fact.fact_id }} · 事实版本 {{ cardMetadata.fact.fact_revision }} · 分类版本 {{ cardMetadata.classification_revision }}</n-text>
        <n-text depth="3">当前分类：{{ cardMetadata.category ?? '未分类' }}</n-text>
        <label class="form-field">分类草稿<n-select v-model:value="cardCategoryDraft" aria-label="卡片分类草稿" :options="cardCategoryOptions" :disabled="cardBusy" /></label>
        <n-button :disabled="!canWriteCard" @click="mutateCard(false)">保存分类</n-button>
        <n-button :disabled="!canWriteCard || cardMetadata.category === null" @click="mutateCard(true)">清除分类</n-button>
      </div>
    </n-card>

    <n-alert v-if="loadError" class="notice" type="error" :show-icon="true" role="alert">
      {{ loadError }}
    </n-alert>
    <n-alert v-if="actionError" class="notice" type="error" :show-icon="true" role="alert">
      {{ actionError }}
    </n-alert>
    <n-alert v-if="notice" class="notice" type="success" :show-icon="true" role="status" aria-live="polite">
      {{ notice }}
    </n-alert>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">精确范围</p>
          <h2>查询结构化候选</h2>
          <p class="subtle-text">输入一个完整群 ID。查询使用精确匹配，不支持通配或模糊搜索。</p>
        </div>
      </div>
      <form class="memory-query-form" @submit.prevent="searchCandidates">
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="memory-group-input">准确群 ID</label>
            <n-input
              id="memory-group"
              v-model:value="groupIdInput"
              :input-props="{ id: 'memory-group-input', 'aria-label': '准确群 ID' }"
              :disabled="loading || Boolean(busyCandidateId)"
              maxlength="64"
              autocomplete="off"
              placeholder="填写完整群 ID"
            />
            <p class="form-hint">群 ID 会按原值查询；只清除首尾空白，不会改写内部字符。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="memory-status-filter">候选状态</label>
            <n-select
              id="memory-status-filter"
              v-model:value="statusFilter"
              :options="statusOptions"
              :disabled="loading || Boolean(busyCandidateId)"
              aria-label="候选状态"
            />
            <p class="form-hint">每次查询最多读取 {{ PAGE_SIZE }} 条，可继续加载后续候选。</p>
          </div>
        </div>
        <div class="button-row">
          <n-button type="primary" attr-type="submit" :loading="loading" :disabled="!canSearch">
            查询候选
          </n-button>
          <n-text v-if="queryIsDirty && hasQueried" depth="3" role="status" aria-live="polite">
            查询条件已更改；重新查询前，当前列表中的操作已停用。
          </n-text>
        </div>
      </form>
    </n-card>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">当前有效事实</p>
          <h2>事实纠错与停用</h2>
          <p class="subtle-text">只读取同群、当前有效且来源仍获权的已应用事实。纠错必须选择并核验一个现有来源身份；建立候选不会立即改变事实，停用会让事实退出后续检索。</p>
        </div>
        <n-tag v-if="hasQueriedFacts" size="small" round>群 {{ loadedFactGroupId }}</n-tag>
      </div>

      <n-alert v-if="factsLoadError" class="notice" type="error" :show-icon="true" role="alert">
        {{ factsLoadError }}
      </n-alert>
      <n-alert v-if="factsActionError" class="notice" type="error" :show-icon="true" role="alert">
        {{ factsActionError }}
      </n-alert>
      <n-alert v-if="factsNotice" class="notice" type="success" :show-icon="true" role="status" aria-live="polite">
        {{ factsNotice }}
      </n-alert>

      <form class="memory-query-form" @submit.prevent="searchFacts">
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="active-facts-group-input">准确群 ID</label>
            <n-input
              id="active-facts-group"
              v-model:value="factGroupIdInput"
              :input-props="{ id: 'active-facts-group-input', 'aria-label': '当前事实的准确群 ID' }"
              :disabled="factsLoading || Boolean(busyFactId)"
              maxlength="64"
              autocomplete="off"
              placeholder="填写完整群 ID"
            />
          </div>
          <div class="form-field">
            <label class="field-label" for="active-facts-subject-input">主体 ID（可选）</label>
            <n-input
              id="active-facts-subject"
              v-model:value="factSubjectIdInput"
              :input-props="{ id: 'active-facts-subject-input', 'aria-label': '当前事实主体 ID，可选筛选' }"
              :disabled="factsLoading || Boolean(busyFactId)"
              maxlength="64"
              autocomplete="off"
              placeholder="留空读取该群所有可见主体"
            />
          </div>
        </div>
        <div class="button-row">
          <n-button type="primary" attr-type="submit" :loading="factsLoading" :disabled="!canSearchFacts">
            查询当前事实
          </n-button>
          <n-text v-if="factQueryIsDirty && hasQueriedFacts" depth="3" role="status" aria-live="polite">
            查询条件已更改；重新查询前，当前列表中的纠错和停用操作已停用。
          </n-text>
        </div>
      </form>

      <div v-if="factsLoading && !facts.length" class="empty-state" role="status" aria-live="polite">
        正在读取当前有效事实…
      </div>
      <n-empty v-else-if="hasQueriedFacts && !facts.length && !factsLoading && !factsLoadError" description="当前群与筛选主体没有可见的已应用事实" />
      <n-empty v-else-if="!hasQueriedFacts" description="填写准确群 ID 后查询当前有效事实" />

      <div v-else class="memory-candidates">
        <n-card v-for="fact in facts" :key="fact.fact_id" class="memory-candidate page-card" :bordered="false">
          <div class="section-heading">
            <div>
              <p class="eyebrow">已应用事实 · 修订 {{ fact.fact_revision }}</p>
              <h3>{{ fact.subject_id }} · {{ fact.predicate }}</h3>
            </div>
            <n-tag type="success" size="small" round>当前有效</n-tag>
          </div>
          <div class="memory-fact"><strong>{{ fact.value }}</strong></div>
          <dl class="memory-metadata">
            <div><dt>事实 ID</dt><dd><code>{{ fact.fact_id }}</code></dd></div>
            <div><dt>应用时间</dt><dd>{{ formatTime(fact.applied_at) }}</dd></div>
            <div><dt>观察时间</dt><dd>{{ formatTime(fact.observed_at) }}</dd></div>
            <div><dt>事实有效期</dt><dd>{{ formatValidity(fact.valid_from, fact.valid_to) }}</dd></div>
          </dl>
          <details class="memory-sources">
            <summary>来源身份（{{ fact.source_ids.length }}）</summary>
            <ul v-if="fact.source_ids.length" class="memory-source-list">
              <li v-for="sourceId in fact.source_ids" :key="sourceId"><code>{{ sourceId }}</code></li>
            </ul>
            <p v-else class="form-hint">当前没有可见来源身份。</p>
          </details>

          <div class="memory-actions">
            <n-button
              :disabled="factQueryIsDirty || factsLoading || Boolean(busyFactId) || fact.status !== 'active'"
              @click="toggleCorrection(fact)"
            >{{ correctionDrafts[fact.fact_id] ? '收起纠错表单' : '建立纠错候选' }}</n-button>
            <n-button
              type="error"
              :disabled="factQueryIsDirty || factsLoading || Boolean(busyFactId) || fact.status !== 'active'"
              @click="requestDisableFact(fact)"
            >停用事实</n-button>
          </div>

          <form v-if="correctionDrafts[fact.fact_id]" class="correction-form" @submit.prevent="void submitCorrection(fact)">
            <h4>从已核对来源建立纠错候选</h4>
            <p class="form-hint">主体和谓词固定为当前事实；纠错候选会进入冲突核验与审核流程，不会直接替换现有事实。</p>
            <div class="form-field">
              <label class="field-label" :for="`correction-source-${fact.fact_id}`">明确选择纠错来源</label>
              <n-select
                :id="`correction-source-${fact.fact_id}`"
                v-model:value="correctionDraft(fact).sourceId"
                :options="fact.source_ids.map((sourceId) => ({ label: sourceId, value: sourceId }))"
                :disabled="factBusy(fact)"
                placeholder="选择一个来源身份 ID"
                aria-label="选择显式核验的纠错来源身份"
              />
            </div>
            <div class="form-field">
              <label class="field-label" :for="`correction-value-${fact.fact_id}`">纠正后的结构化值</label>
              <n-input
                :id="`correction-value-${fact.fact_id}`"
                v-model:value="correctionDraft(fact).value"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 4 }"
                maxlength="256"
                :disabled="factBusy(fact)"
                aria-label="纠正后的结构化事实值"
              />
            </div>
            <n-checkbox
              v-model:checked="correctionDraft(fact).verified"
              :disabled="!correctionDraft(fact).sourceId || factBusy(fact)"
            >我已核对所选来源身份支持这一纠正值</n-checkbox>
            <div class="memory-actions">
              <n-button
                type="primary"
                attr-type="submit"
                :loading="factBusy(fact)"
                :disabled="!canCreateCorrection(fact)"
              >创建待审核纠错候选</n-button>
              <n-button :disabled="factBusy(fact)" @click="toggleCorrection(fact)">取消</n-button>
            </div>
          </form>

          <div v-if="disableConfirmFactId === fact.fact_id" class="disable-confirm" role="group" aria-label="确认停用事实">
            <n-text>停用后该事实不再参与记忆检索；该事实目标下的待处理候选会由服务端撤回。</n-text>
            <div class="memory-actions">
              <n-button type="error" :loading="factBusy(fact)" :disabled="factBusy(fact)" @click="void confirmDisableFact(fact)">
                确认停用事实
              </n-button>
              <n-button :disabled="factBusy(fact)" @click="cancelDisableFact">取消</n-button>
            </div>
          </div>
        </n-card>
      </div>

      <div v-if="hasQueriedFacts && (facts.length || nextFactCursor)" class="memory-pager">
        <n-text depth="3">已显示 {{ facts.length }} 条当前事实</n-text>
        <n-button :loading="factsLoading" :disabled="!canLoadMoreFacts" @click="void loadMoreFacts">
          {{ nextFactCursor ? '加载更多' : '已到列表末尾' }}
        </n-button>
      </div>
    </n-card>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">只返回原因码</p>
          <h2>检索路径诊断</h2>
          <p class="subtle-text">使用本页临时查询词读取检索诊断，只显示命中计数、预算状态和固定原因码，不返回事实正文。结果按 web-admin 在该群的权限计算，不代表 QQ Bot 某次对话的完整上下文。</p>
        </div>
      </div>
      <n-alert v-if="retrievalError" class="notice" type="error" :show-icon="true" role="alert">
        {{ retrievalError }}
      </n-alert>
      <form class="memory-query-form" @submit.prevent="void runRetrievalDiagnostic">
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="retrieval-group-input">准确群 ID</label>
            <n-input
              id="retrieval-group"
              v-model:value="retrievalGroupId"
              :input-props="{ id: 'retrieval-group-input', 'aria-label': '检索诊断的准确群 ID' }"
              :disabled="retrievalLoading"
              maxlength="64"
              autocomplete="off"
              placeholder="填写完整群 ID"
            />
          </div>
          <div class="form-field">
            <label class="field-label" for="retrieval-subject-input">当前发言者主体 ID（可选）</label>
            <n-input
              id="retrieval-subject"
              v-model:value="retrievalSubjectId"
              :input-props="{ id: 'retrieval-subject-input', 'aria-label': '检索诊断的主体 ID，可选' }"
              :disabled="retrievalLoading"
              maxlength="64"
              autocomplete="off"
              placeholder="留空不预览热事实"
            />
          </div>
        </div>
        <div class="form-field">
          <label class="field-label" for="retrieval-query-input">临时查询词</label>
          <n-input
            id="retrieval-query"
            v-model:value="retrievalQuery"
            :input-props="{ id: 'retrieval-query-input', 'aria-label': '用于诊断的临时查询词' }"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="1024"
            :disabled="retrievalLoading"
            placeholder="输入当前要核对的检索词"
          />
        </div>
        <div class="button-row">
          <n-button type="primary" attr-type="submit" :loading="retrievalLoading"
            :disabled="!sessionState.adminAuthenticated || retrievalLoading || !retrievalGroupId.trim()">
            读取原因码
          </n-button>
        </div>
      </form>
      <div v-if="retrievalResult" class="retrieval-result" role="status" aria-live="polite">
        <dl class="memory-metadata">
          <div><dt>热事实命中数</dt><dd>{{ retrievalResult.hot_hit_count }}</dd></div>
          <div><dt>冷检索命中数</dt><dd>{{ retrievalResult.cold_hit_count }}</dd></div>
          <div><dt>冷检索状态</dt><dd>{{ retrievalResult.cold_pack_state }}</dd></div>
          <div><dt>事实/证据字符预算</dt><dd>{{ retrievalResult.total_budget_used }} / {{ retrievalResult.total_budget }}</dd></div>
        </dl>
        <p class="form-hint">这是内部事实与证据内容的字符预算，不是模型 token 数、模型成本或完整提示长度。</p>
        <div class="reason-codes">
          <p class="field-label">原因码</p>
          <n-tag v-for="reason in retrievalResult.reason_codes" :key="`reason-${reason}`" size="small" round>{{ reason }}</n-tag>
          <n-text v-if="!retrievalResult.reason_codes.length" depth="3">没有返回原因码。</n-text>
        </div>
        <div v-if="retrievalResult.omitted_reason_codes.length" class="reason-codes">
          <p class="field-label">未纳入原因码</p>
          <n-tag v-for="reason in retrievalResult.omitted_reason_codes" :key="`omitted-${reason}`" size="small" round type="warning">{{ reason }}</n-tag>
        </div>
      </div>
    </n-card>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">候选清单</p>
          <h2>审核与应用</h2>
          <p class="subtle-text">批准只改变候选状态；应用是单独操作。服务端会再次检查版本、来源和权限。</p>
        </div>
        <n-tag v-if="hasQueried" size="small" round>群 {{ loadedGroupId }}</n-tag>
      </div>

      <div v-if="loading && !candidates.length" class="empty-state" role="status" aria-live="polite">
        正在读取候选…
      </div>
      <n-empty v-else-if="hasQueried && !candidates.length && !loading && !loadError" description="当前群与筛选条件下没有候选" />
      <n-empty v-else-if="!hasQueried" description="填写准确群 ID 后查询候选" />

      <div v-else class="memory-candidates">
        <n-card
          v-for="candidate in candidates"
          :key="candidate.candidate_id"
          class="memory-candidate page-card"
          :bordered="false"
        >
          <template v-if="candidate.kind === 'fact'">
          <div class="section-heading">
            <div>
              <p class="eyebrow">{{ actionLabel(candidate.action) }} · 修订 {{ candidate.candidate_revision }}</p>
              <h3>候选事实</h3>
            </div>
            <n-tag :type="statusTagType(candidate.status)" size="small" round>
              {{ statusLabel(candidate.status) }}
            </n-tag>
          </div>

          <div class="memory-fact">
            <code>{{ candidate.subject_id }}</code>
            <span aria-hidden="true">·</span>
            <code>{{ candidate.predicate }}</code>
            <span aria-hidden="true">→</span>
            <strong>{{ candidate.value }}</strong>
          </div>

          <dl class="memory-metadata">
            <div>
              <dt>候选 ID</dt>
              <dd><code>{{ candidate.candidate_id }}</code></dd>
            </div>
            <div>
              <dt>观察时间</dt>
              <dd>{{ formatTime(candidate.observed_at) }}</dd>
            </div>
            <div>
              <dt>事实有效期</dt>
              <dd>{{ formatValidity(candidate.valid_from, candidate.valid_to) }}</dd>
            </div>
            <div>
              <dt>提议理由</dt>
              <dd>{{ suggestionReasonLabel(candidate.suggestion_reason) }}</dd>
            </div>
            <div v-if="candidate.target_fact_id">
              <dt>目标事实</dt>
              <dd><code>{{ candidate.target_fact_id }}</code></dd>
            </div>
            <div v-if="candidate.conflict_set_id">
              <dt>冲突集合</dt>
              <dd><code>{{ candidate.conflict_set_id }}</code></dd>
            </div>
            <div v-if="candidate.skip_reason">
              <dt>跳过原因</dt>
              <dd>{{ candidate.skip_reason }}</dd>
            </div>
          </dl>

          <details class="memory-sources">
            <summary>来源身份（{{ candidate.source_ids.length }}）</summary>
            <ul v-if="candidate.source_ids.length" class="memory-source-list">
              <li v-for="sourceId in candidate.source_ids" :key="sourceId"><code>{{ sourceId }}</code></li>
            </ul>
            <p v-else class="form-hint">当前没有可见来源身份。</p>
          </details>

          <div v-if="candidate.status === 'conflict_pending'" class="memory-actions">
            <n-button disabled aria-label="存在未解决冲突，不能批准此候选">存在冲突，不能批准</n-button>
            <n-button
              v-if="candidate.action === 'supersede' && candidate.target_fact_id"
              :loading="conflictLoading && conflictCandidateId === candidate.candidate_id"
              :disabled="!canReview(candidate)"
              :aria-label="`读取冲突候选 ${candidate.candidate_id} 的当前目标事实`"
              @click="void inspectConflictTarget(candidate)"
            >读取当前目标以核验</n-button>
            <n-text v-else depth="3">此冲突没有可读取的替换目标，不能通过本页解决。</n-text>
            <n-button
              type="error"
              :loading="candidateBusy(candidate)"
              :disabled="!canReview(candidate)"
              :aria-label="`拒绝候选 ${candidate.candidate_id}`"
              @click="void reviewCandidate(candidate, 'rejected')"
            >拒绝候选</n-button>
            <n-button
              type="warning"
              :loading="candidateBusy(candidate)"
              :disabled="!canReview(candidate)"
              :aria-label="`撤回候选 ${candidate.candidate_id}`"
              @click="void reviewCandidate(candidate, 'withdrawn')"
            >撤回候选</n-button>
          </div>
          <div v-else-if="candidate.status === 'pending'" class="memory-actions">
            <n-button
              type="primary"
              :loading="candidateBusy(candidate)"
              :disabled="!canReview(candidate)"
              :aria-label="`批准候选 ${candidate.candidate_id}，但不应用`"
              @click="void reviewCandidate(candidate, 'approved')"
            >批准，暂不应用</n-button>
            <n-button
              type="error"
              :loading="candidateBusy(candidate)"
              :disabled="!canReview(candidate)"
              :aria-label="`拒绝候选 ${candidate.candidate_id}`"
              @click="void reviewCandidate(candidate, 'rejected')"
            >拒绝候选</n-button>
            <n-button
              type="warning"
              :loading="candidateBusy(candidate)"
              :disabled="!canReview(candidate)"
              :aria-label="`撤回候选 ${candidate.candidate_id}`"
              @click="void reviewCandidate(candidate, 'withdrawn')"
            >撤回候选</n-button>
          </div>
          <div v-else-if="candidate.status === 'approved'" class="memory-actions">
            <n-text depth="3">已批准，尚未应用。</n-text>
            <n-button
              type="primary"
              :loading="candidateBusy(candidate)"
              :disabled="!canApply(candidate)"
              :aria-label="`单独应用候选 ${candidate.candidate_id}`"
              @click="void applyCandidate(candidate)"
            >单独应用事实</n-button>
          </div>
          </template>
          <template v-else>
            <div class="section-heading">
              <div>
                <p class="eyebrow">{{ domainLabel(candidate) }} · 修订 {{ candidate.candidate_revision }}</p>
                <h3>{{ domainLabel(candidate) }}候选</h3>
              </div>
              <div class="memory-actions">
                <n-tag :type="statusTagType(candidate.status)" size="small" round>
                  {{ reviewCandidateStatusLabel(candidate) }}
                </n-tag>
                <n-tag v-if="candidate.kind === 'episode'" size="small" round>
                  {{ episodeStateLabel(candidate.episode_state) }}
                </n-tag>
              </div>
            </div>

            <dl class="memory-metadata">
              <div v-for="field in domainValueFields(candidate)" :key="field.label">
                <dt>{{ field.label }}</dt>
                <dd>{{ field.value }}</dd>
              </div>
              <div>
                <dt>候选 ID</dt>
                <dd><code>{{ candidate.candidate_id }}</code></dd>
              </div>
              <div>
                <dt>创建时间</dt>
                <dd>{{ formatTime(candidate.created_at) }}</dd>
              </div>
              <div>
                <dt>应用状态</dt>
                <dd>{{ candidate.application_status === 'applied' ? '已应用' : candidate.application_status === 'disabled' ? '已停用' : '尚未应用' }}</dd>
              </div>
            </dl>

            <section
              v-if="candidate.kind === 'episode' && candidate.application_status !== 'not_applied'"
              class="memory-social-stage"
              aria-label="N6 与 N7 处理进度"
              aria-live="polite"
            >
              <p class="eyebrow">共同经历处理进度</p>
              <div class="memory-social-stages">
                <div class="memory-social-status">
                  <n-text depth="3">N6 DomainLearning</n-text>
                  <n-tag :type="candidate.application_status === 'applied' ? 'success' : 'default'" size="small" round>
                    {{ episodeApplicationLabel(candidate.application_status) }}
                  </n-tag>
                </div>
                <div class="memory-social-status">
                  <n-text depth="3">N7 Social 捕获</n-text>
                  <n-tag
                    v-if="candidate.social"
                    :type="socialStatusTagType(candidate.social.capture_status)"
                    size="small"
                    round
                  >{{ socialCaptureStatusLabel(candidate.social.capture_status) }}</n-tag>
                  <n-text v-else depth="3">状态未返回</n-text>
                </div>
                <div class="memory-social-status">
                  <n-text depth="3">Story 通用剧情</n-text>
                  <n-tag
                    v-if="candidate.social"
                    :type="socialStoryTagType(candidate.social.story_status)"
                    size="small"
                    round
                  >{{ socialStoryStatusLabel(candidate.social.story_status, candidate.social.capture_status) }}</n-tag>
                  <n-text v-else depth="3">状态未返回</n-text>
                </div>
              </div>
              <p v-if="candidate.social?.error_code" class="form-hint" role="status">
                受阻原因：{{ socialErrorReason(candidate.social.error_code) }}
              </p>
              <div v-if="candidate.social?.retryable" class="memory-actions">
                <n-button
                  type="primary"
                  :loading="candidateBusy(candidate)"
                  :disabled="!canRetrySocial(candidate)"
                  :aria-label="`重试事件经历 ${candidate.candidate_id} 的 Social 共同经历处理`"
                  @click="void retrySocialExperience(candidate)"
                >重试 N7 共同经历</n-button>
              </div>
            </section>

            <details class="memory-sources">
              <summary>来源身份（{{ candidate.source_ids.length }}）</summary>
              <ul v-if="candidate.source_ids.length" class="memory-source-list">
                <li v-for="sourceId in candidate.source_ids" :key="sourceId"><code>{{ sourceId }}</code></li>
              </ul>
              <p v-else class="form-hint">当前没有可见来源身份。</p>
            </details>

            <div v-if="candidate.status === 'pending'" class="domain-review-reason">
              <label class="field-label" :for="`domain-review-reason-${candidate.candidate_id}`">人工审核理由</label>
              <n-input
                :id="`domain-review-reason-${candidate.candidate_id}`"
                type="textarea"
                :value="domainReviewReasons[candidate.candidate_id] ?? ''"
                :disabled="candidateBusy(candidate) || loading || queryIsDirty"
                placeholder="写明本次审核判断；该理由只进入审核审计。"
                :autosize="{ minRows: 2, maxRows: 4 }"
                @update:value="(value) => setDomainReviewReason(candidate.candidate_id, value)"
              />
            </div>

            <div v-if="candidate.status === 'pending'" class="memory-actions">
              <n-button
                v-if="candidate.kind === 'episode' && candidate.episode_state === 'dry_run'"
                type="primary"
                :loading="candidateBusy(candidate)"
                :disabled="!canReview(candidate)"
                :aria-label="`确认事件候选 ${candidate.candidate_id} 进入审核阶段`"
                @click="void reviewCandidate(candidate, 'candidate')"
              >进入审核阶段</n-button>
              <n-button
                v-else
                type="primary"
                :loading="candidateBusy(candidate)"
                :disabled="!canReview(candidate)"
                :aria-label="`批准多域候选 ${candidate.candidate_id}，但不应用`"
                @click="void reviewCandidate(candidate, 'approved')"
              >批准，暂不应用</n-button>
              <n-button
                type="error"
                :loading="candidateBusy(candidate)"
                :disabled="!canReview(candidate)"
                :aria-label="`拒绝多域候选 ${candidate.candidate_id}`"
                @click="void reviewCandidate(candidate, 'rejected')"
              >拒绝候选</n-button>
            </div>
            <div v-else-if="candidate.status === 'approved' && candidate.application_status === 'not_applied'" class="memory-actions">
              <n-text depth="3">已批准，尚未应用。</n-text>
              <n-button
                type="primary"
                :loading="candidateBusy(candidate)"
                :disabled="!canApply(candidate)"
                :aria-label="`单独应用多域候选 ${candidate.candidate_id}`"
                @click="void applyCandidate(candidate)"
              >单独应用</n-button>
            </div>
            <div v-else-if="candidate.application_status === 'applied'" class="memory-actions">
              <n-text depth="3">已应用；服务端会拒绝重复应用。</n-text>
            </div>
          </template>
        </n-card>
      </div>

      <div v-if="hasQueried && (candidates.length || nextCursor)" class="memory-pager">
        <n-text depth="3">已显示 {{ candidates.length }} 条候选</n-text>
        <n-button :loading="loading" :disabled="!canLoadMore" @click="void loadMore">
          {{ nextCursor ? '加载更多' : '已到列表末尾' }}
        </n-button>
      </div>

      <n-card v-if="hasQueried" class="page-card domain-failure-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">多域处理诊断</p>
            <h2>逐域提取失败</h2>
            <p class="subtle-text">
              显示同群来源、失败域与稳定错误码，不包含 Archive 原文或抽取值。俚语失败可由管理员重用仍有效的封存决策显式重试，不会重新调用模型。
            </p>
          </div>
          <n-tag v-if="domainFailures.length" type="error" size="small" round>
            {{ domainFailures.length }} 条
          </n-tag>
        </div>
        <n-alert v-if="failureLoadError" class="notice" type="error" :show-icon="true" role="alert">
          {{ failureLoadError }}
        </n-alert>
        <n-alert v-if="failureActionError" class="notice" type="error" :show-icon="true" role="alert">
          {{ failureActionError }}
        </n-alert>
        <div v-if="failureLoading && !domainFailures.length" class="empty-state" role="status" aria-live="polite">
          正在读取逐域诊断…
        </div>
        <n-empty
          v-else-if="!domainFailures.length && !failureLoading && !failureLoadError"
          :description="failureEmptyDescription"
        />
        <div v-else class="domain-failure-list">
          <article v-for="failure in domainFailures" :key="failure.result_id" class="domain-failure-row">
            <div class="domain-failure-heading">
              <div class="button-row">
                <n-tag :type="failure.status === 'incomplete' ? 'warning' : 'error'" size="small">
                  {{ failure.status === 'incomplete' ? '未完成' : '失败' }}
                </n-tag>
                <n-tag size="small">{{ failureDomainLabel(failure.domain) }}</n-tag>
              </div>
              <n-text depth="3">{{ formatTime(failure.created_at) }}</n-text>
            </div>
            <p>来源：<code>{{ failure.source_id }}</code></p>
            <p>
              错误码：<code>{{ failure.error_code }}</code>
              <span aria-hidden="true">·</span>
              {{ failureCodeLabel(failure.error_code) }}
            </p>
            <p>来源修订 {{ failure.source_revision }} · 失败记录修订 {{ failure.failure_revision }}</p>
            <template v-if="isRetryableDomainFailure(failure)">
              <label class="field-label" :for="`domain-retry-reason-${failure.result_id}`">本次重试理由</label>
              <n-input
                :id="`domain-retry-reason-${failure.result_id}`"
                :value="domainRetryReasons[failure.result_id] ?? ''"
                :disabled="!canRetryFailures"
                maxlength="256"
                placeholder="说明为何现在重试；该理由会进入审计记录。"
                @update:value="(value) => { domainRetryReasons[failure.result_id] = value }"
              />
            </template>
            <n-button
              v-if="isRetryableDomainFailure(failure)"
              class="failure-retry-button"
              type="primary"
              :loading="busyFailureId === failure.result_id"
              :disabled="!canRetryDomainFailure(failure)"
              :aria-label="`从封存决策重试俚语失败 ${failure.result_id}`"
              @click="void retryDomainFailure(failure)"
            >从封存决策重试俚语</n-button>
          </article>
        </div>
        <div v-if="domainFailures.length || nextFailureCursor" class="memory-pager">
          <n-text depth="3">已显示 {{ domainFailures.length }} 条逐域失败</n-text>
          <n-button
            :loading="failureLoading"
            :disabled="!canLoadMoreFailures"
            @click="void loadMoreDomainFailures"
          >
            {{ nextFailureCursor ? '加载更多失败' : '已到诊断末尾' }}
          </n-button>
        </div>
      </n-card>

      <n-card v-if="hasQueried" class="page-card extraction-run-diagnostic-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">提取流程状态</p>
            <h2>终止且仍缺少回执</h2>
            <p class="subtle-text">
              这里只显示当前来源修订仍缺一个或多个域回执的取消、结果未知或进程中断记录。受管恢复后可能补齐；这表示流程状态，不代表逐域失败，也不提供重试。
            </p>
          </div>
          <n-tag v-if="extractionRuns.length" type="warning" size="small" round>
            {{ extractionRuns.length }} 条
          </n-tag>
        </div>
        <n-alert v-if="runLoadError" class="notice" type="error" :show-icon="true" role="alert">
          {{ runLoadError }}
        </n-alert>
        <div v-if="runLoading && !extractionRuns.length" class="empty-state" role="status" aria-live="polite">
          正在读取提取流程状态…
        </div>
        <n-empty
          v-else-if="!extractionRuns.length && !runLoading && !runLoadError"
          :description="runEmptyDescription"
        />
        <div v-else-if="extractionRuns.length" class="domain-failure-list">
          <article v-for="run in extractionRuns" :key="run.run_id" class="domain-failure-row">
            <div class="domain-failure-heading">
              <div class="button-row">
                <n-tag type="warning" size="small">{{ extractionRunStatusLabel(run.status) }}</n-tag>
                <n-tag size="small">{{ extractionRunStageLabel(run.stage) }}</n-tag>
              </div>
              <n-text depth="3">{{ formatTime(run.finished_at ?? run.updated_at) }}</n-text>
            </div>
            <p>来源：<code>{{ run.source_id }}</code></p>
            <p>
              缺少回执的域：
              <n-tag v-for="domain in run.missing_domains" :key="`${run.run_id}-${domain}`" size="small">
                {{ failureDomainLabel(domain) }}
              </n-tag>
            </p>
            <p>来源修订 {{ run.source_revision }} · 错误码：<code>{{ run.error_code || '未记录' }}</code></p>
            <p>开始时间 {{ formatTime(run.started_at) }} · 最近更新 {{ formatTime(run.updated_at) }}</p>
          </article>
        </div>
        <div v-if="extractionRuns.length || nextRunCursor" class="memory-pager">
          <n-text depth="3">已显示 {{ extractionRuns.length }} 条终止流程诊断</n-text>
          <n-button
            :loading="runLoading"
            :disabled="!canLoadMoreRuns"
            @click="void loadMoreExtractionRuns"
          >
            {{ nextRunCursor ? '加载更多流程诊断' : '已到诊断末尾' }}
          </n-button>
        </div>
      </n-card>

      <div v-if="conflictCandidateId" class="conflict-review-panel" aria-label="冲突目标核验">
        <div class="section-heading">
          <div>
            <p class="eyebrow">人工核验</p>
            <h3>将冲突移入待审核</h3>
            <p class="subtle-text">此操作只记录对目标修订和来源身份的核验，并把候选转为待审核；不会批准或应用。</p>
          </div>
          <n-button quaternary @click="cancelConflictResolution">
            取消核验
          </n-button>
        </div>
        <n-alert v-if="conflictError" type="error" :show-icon="true" role="alert">{{ conflictError }}</n-alert>
        <div v-if="conflictLoading && !conflictTargetFact" class="empty-state" role="status" aria-live="polite">
          正在读取当前目标事实…
        </div>
        <template v-if="selectedConflictCandidate && conflictTargetFact">
          <div class="memory-fact">
            <code>{{ conflictTargetFact.subject_id }}</code>
            <span aria-hidden="true">·</span>
            <code>{{ conflictTargetFact.predicate }}</code>
            <span aria-hidden="true">→</span>
            <strong>{{ conflictTargetFact.value }}</strong>
          </div>
          <dl class="memory-metadata">
            <div><dt>目标事实 ID</dt><dd><code>{{ conflictTargetFact.fact_id }}</code></dd></div>
            <div><dt>当前修订</dt><dd>{{ conflictTargetFact.fact_revision }}</dd></div>
            <div><dt>当前有效期</dt><dd>{{ formatValidity(conflictTargetFact.valid_from, conflictTargetFact.valid_to) }}</dd></div>
          </dl>
          <p class="form-hint">请展开候选卡中的来源身份列表，逐一核对其身份。服务端会在本次操作中再次检查全部来源是否仍获权；不会读取或回显聊天原文。</p>
          <n-checkbox
            v-model:checked="conflictSourceVerified"
            :disabled="!conflictTargetFact || Boolean(busyCandidateId)"
          >我已核对当前目标事实及候选的全部来源身份</n-checkbox>
          <div class="memory-actions">
            <n-button
              type="warning"
              :loading="busyCandidateId === selectedConflictCandidate.candidate_id"
              :disabled="!canResolveConflict(selectedConflictCandidate)"
              @click="void resolveConflict(selectedConflictCandidate)"
            >核验冲突并移入待审核</n-button>
          </div>
        </template>
      </div>
    </n-card>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">只读快照</p>
          <h2>已应用热事实</h2>
          <p class="subtle-text">仅查询输入主体在指定群内当前有效且获权的已应用热事实；不包含候选或来源聊天原文，也不代表完整聊天上下文。</p>
        </div>
      </div>

      <n-alert v-if="hotError" class="notice" type="error" :show-icon="true" role="alert">
        {{ hotError }}
      </n-alert>

      <form class="memory-query-form" @submit.prevent="searchHotFacts">
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="hot-memory-group-input">准确群 ID</label>
            <n-input
              id="hot-memory-group"
              v-model:value="hotGroupIdInput"
              :input-props="{ id: 'hot-memory-group-input', 'aria-label': '已应用事实的准确群 ID' }"
              :disabled="hotLoading"
              maxlength="64"
              autocomplete="off"
              placeholder="填写完整群 ID"
            />
            <p class="form-hint">按完整群 ID 精确读取，不支持通配或模糊搜索。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="hot-memory-subject-input">主体 ID（当前发言者）</label>
            <n-input
              id="hot-memory-subject"
              v-model:value="hotSubjectIdInput"
              :input-props="{ id: 'hot-memory-subject-input', 'aria-label': '当前发言者主体 ID' }"
              :disabled="hotLoading"
              maxlength="64"
              autocomplete="off"
              placeholder="填写精确主体 ID"
            />
            <p class="form-hint">填写当前发言者对应的完整主体 ID；此查询只读取该主体。</p>
          </div>
        </div>
        <div class="button-row">
          <n-button
            type="primary"
            attr-type="submit"
            :loading="hotLoading"
            :disabled="!sessionState.adminAuthenticated || hotLoading || !hotGroupIdInput.trim() || !hotSubjectIdInput.trim()"
          >读取当前事实</n-button>
          <n-text v-if="hotQueried" depth="3">群 {{ hotGroupId }} · 主体 {{ hotSubjectId }}</n-text>
        </div>
      </form>

      <div v-if="hotLoading && !hotFacts.length" class="empty-state" role="status" aria-live="polite">
        正在读取已应用事实…
      </div>
      <n-empty v-else-if="hotQueried && !hotFacts.length && !hotError" description="该群与主体当前没有可见的已应用事实" />
      <n-empty v-else-if="!hotQueried" description="输入准确群 ID 和当前发言者主体 ID 后读取" />

      <template v-if="hotFacts.length">
        <n-alert v-if="hotTruncated" class="notice" type="warning" :show-icon="true">
          当前快照已截断，只显示服务端返回的部分事实。
        </n-alert>
        <div class="memory-candidates">
          <n-card v-for="fact in hotFacts" :key="fact.fact_id" class="memory-candidate page-card" :bordered="false">
            <div class="section-heading">
              <div>
                <p class="eyebrow">已应用事实 · 修订 {{ fact.fact_revision }}</p>
                <h3>当前主体事实</h3>
              </div>
              <n-tag type="success" size="small" round>当前快照</n-tag>
            </div>
            <div class="memory-fact">
              <code>{{ fact.subject_id }}</code>
              <span aria-hidden="true">·</span>
              <code>{{ fact.predicate }}</code>
              <span aria-hidden="true">→</span>
              <strong>{{ fact.value }}</strong>
            </div>
            <dl class="memory-metadata">
              <div>
                <dt>事实 ID</dt>
                <dd><code>{{ fact.fact_id }}</code></dd>
              </div>
              <div>
                <dt>应用时间</dt>
                <dd>{{ formatTime(fact.applied_at) }}</dd>
              </div>
              <div>
                <dt>观察时间</dt>
                <dd>{{ formatTime(fact.observed_at) }}</dd>
              </div>
              <div>
                <dt>事实有效期</dt>
                <dd>{{ formatValidity(fact.valid_from, fact.valid_to) }}</dd>
              </div>
            </dl>
            <details class="memory-sources">
              <summary>来源身份（{{ fact.source_ids.length }}）</summary>
              <ul v-if="fact.source_ids.length" class="memory-source-list">
                <li v-for="sourceId in fact.source_ids" :key="sourceId"><code>{{ sourceId }}</code></li>
              </ul>
              <p v-else class="form-hint">当前没有可见来源身份。</p>
            </details>
          </n-card>
        </div>
      </template>
    </n-card>
  </section>
</template>

<style scoped>
.memory-query-form {
  margin-top: 16px;
}

.memory-candidates {
  display: grid;
  gap: 16px;
  margin-top: 16px;
}

.memory-candidate {
  margin-top: 0;
  border-radius: var(--om-radius);
  background: var(--om-surface-soft);
  box-shadow: none;
}

.memory-candidate h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 720;
}

.memory-social-stage {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface);
}

.memory-social-stage > .eyebrow {
  margin: 0 0 8px;
}

.memory-social-stages,
.memory-social-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
}

.memory-social-stage .form-hint {
  margin: 8px 0 0;
}

.memory-social-stage .memory-actions {
  margin-top: 12px;
}

.domain-review-reason {
  margin-top: 16px;
}

.domain-failure-list {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.domain-failure-row {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface-soft);
}

.domain-failure-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.domain-failure-row p {
  margin: 10px 0 0;
}

.conflict-review-panel,
.correction-form,
.disable-confirm,
.retrieval-result {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius);
  background: var(--om-surface-soft);
}

.conflict-review-panel h3,
.correction-form h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 720;
}

.correction-form > .form-field {
  margin-top: 12px;
}

.correction-form > .n-checkbox {
  margin-top: 12px;
}

.disable-confirm {
  display: grid;
  gap: 8px;
}

.reason-codes {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.reason-codes .field-label {
  margin: 0;
}

.memory-fact {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin-top: 16px;
  overflow-wrap: anywhere;
}

.memory-fact strong {
  min-width: 0;
  padding: 8px 12px;
  overflow-wrap: anywhere;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface);
  font-weight: 680;
}

.memory-metadata {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.memory-metadata > div {
  min-width: 0;
}

.memory-metadata dt {
  color: var(--om-muted);
  font-size: 12px;
}

.memory-metadata dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.memory-sources {
  padding-top: 12px;
  border-top: 1px solid var(--om-border);
}

.memory-sources summary {
  color: var(--om-primary-dark);
  font-weight: 680;
}

.memory-source-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.memory-source-list li {
  max-width: 100%;
  padding: 4px 8px;
  overflow-wrap: anywhere;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface);
}

.memory-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}

.memory-pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
}

@media (max-width: 680px) {
  .memory-metadata {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

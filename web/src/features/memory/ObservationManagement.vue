<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NSelect, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type {
  JsonValue,
  ObservationJobPageView,
  ObservationJobView,
  ObservationPoolPageView,
  ObservationPoolView,
  ObservationReviewRequest,
  RevisionResponse,
  SlangGovernanceRevokeRequest,
  SlangGovernanceSuggestionPageView,
  SlangGovernanceSuggestionView,
  SlangReviewStatusView,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const verdicts: ObservationReviewRequest['verdict'][] = ['approved', 'rejected', 'kept', 'failed', 'cancelled']
const verdictOptions = [
  { label: '已检查：认可', value: 'approved' },
  { label: '已检查：拒绝', value: 'rejected' },
  { label: '已检查：保留待后续', value: 'kept' },
  { label: '已检查：标记失败', value: 'failed' },
  { label: '已检查：取消', value: 'cancelled' },
]
const groupInput = ref('')
const loadedGroup = ref('')
const observationPage = ref<ObservationPoolPageView | null>(null)
const jobPage = ref<ObservationJobPageView | null>(null)
const slangStatus = ref<SlangReviewStatusView | null>(null)
const governancePage = ref<SlangGovernanceSuggestionPageView | null>(null)
const reviewDrafts = reactive<Record<string, ObservationReviewRequest['verdict'] | ''>>({})
const busy = ref(false)
const jobsFresh = ref(false)
const governanceFresh = ref(false)
const error = ref('')
const actionError = ref('')
const notice = ref('')
let sequence = 0
let disposed = false

const loadedForCurrentGroup = computed(() => sessionState.adminAuthenticated && Boolean(loadedGroup.value)
  && groupInput.value.trim() === loadedGroup.value && observationPage.value !== null && jobPage.value !== null)
const canLoad = computed(() => sessionState.adminAuthenticated && !busy.value && Boolean(groupInput.value.trim()))
const canDisableSlang = computed(() => loadedForCurrentGroup.value && !busy.value && slangStatus.value?.enabled === true)

function clearLoadedState(clearGroupInput: boolean) {
  sequence += 1
  loadedGroup.value = ''
  observationPage.value = null
  jobPage.value = null
  slangStatus.value = null
  governancePage.value = null
  jobsFresh.value = false
  governanceFresh.value = false
  for (const jobId of Object.keys(reviewDrafts)) delete reviewDrafts[jobId]
  busy.value = false
  error.value = ''
  actionError.value = ''
  notice.value = ''
  if (clearGroupInput) groupInput.value = ''
}
watch(() => groupInput.value.trim(), target => {
  if (target !== loadedGroup.value) clearLoadedState(false)
})
watch(() => sessionState.generation, () => clearLoadedState(true))
watch(() => sessionState.adminAuthenticated, authenticated => { if (!authenticated) clearLoadedState(true) })
onBeforeUnmount(() => { disposed = true; clearLoadedState(true) })

function currentRequest(requestSequence: number, epoch: number, target: string): boolean {
  return !disposed && requestSequence === sequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && groupInput.value.trim() === target
}
function paramsFor(target: string, limit: number, after?: string | null): URLSearchParams {
  const params = new URLSearchParams({ group_id: target, limit: String(limit) })
  if (after) params.set('after', after)
  return params
}
function isConflict(cause: unknown): boolean {
  return isApiError(cause) && cause.status === 409
}
function handleUnauthorized(cause: unknown) {
  if (isApiError(cause) && cause.status === 401) {
    expireAdminSession()
    clearLoadedState(true)
    return true
  }
  return false
}

async function load() {
  if (!canLoad.value) return
  const target = groupInput.value.trim()
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  error.value = ''
  actionError.value = ''
  notice.value = ''
  try {
    const [nextObservations, nextJobs, nextStatus, nextGovernance] = await Promise.all([
      apiRequest<ObservationPoolPageView>(`/api/admin/memory/observations?${paramsFor(target, 64)}`),
      apiRequest<ObservationJobPageView>(`/api/admin/memory/observation-jobs?${paramsFor(target, 32)}`),
      apiRequest<SlangReviewStatusView>('/api/admin/memory/slang-review/status'),
      apiRequest<SlangGovernanceSuggestionPageView>(`/api/admin/memory/slang-governance?${paramsFor(target, 100)}`),
    ])
    if (!currentRequest(requestSequence, epoch, target)) return
    observationPage.value = nextObservations
    jobPage.value = nextJobs
    slangStatus.value = nextStatus
    governancePage.value = nextGovernance
    loadedGroup.value = target
    jobsFresh.value = true
    governanceFresh.value = true
    for (const jobId of Object.keys(reviewDrafts)) {
      if (!nextJobs.items.some(job => job.job_id === jobId)) delete reviewDrafts[jobId]
    }
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (!handleUnauthorized(cause)) error.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function loadMoreObservations() {
  const cursor = observationPage.value?.next_cursor
  if (!loadedForCurrentGroup.value || busy.value || !cursor) return
  const target = loadedGroup.value
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  error.value = ''
  try {
    const next = await apiRequest<ObservationPoolPageView>(`/api/admin/memory/observations?${paramsFor(target, 64, cursor)}`)
    if (!currentRequest(requestSequence, epoch, target)) return
    const items = new Map((observationPage.value?.items ?? []).map(item => [item.pool_key, item]))
    for (const item of next.items) items.set(item.pool_key, item)
    observationPage.value = { ...next, items: [...items.values()] }
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (!handleUnauthorized(cause)) error.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function loadMoreJobs() {
  const cursor = jobPage.value?.next_cursor
  if (!loadedForCurrentGroup.value || busy.value || !cursor) return
  const target = loadedGroup.value
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  error.value = ''
  try {
    const next = await apiRequest<ObservationJobPageView>(`/api/admin/memory/observation-jobs?${paramsFor(target, 32, cursor)}`)
    if (!currentRequest(requestSequence, epoch, target)) return
    const items = new Map((jobPage.value?.items ?? []).map(item => [item.job_id, item]))
    for (const item of next.items) items.set(item.job_id, item)
    jobPage.value = { ...next, items: [...items.values()] }
    jobsFresh.value = true
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (!handleUnauthorized(cause)) error.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

function setReviewDraft(jobId: string, value: string | number | null) {
  if (typeof value === 'string' && verdicts.includes(value as ObservationReviewRequest['verdict'])) {
    reviewDrafts[jobId] = value as ObservationReviewRequest['verdict']
  }
}
function reviewDraft(jobId: string): string | null {
  return reviewDrafts[jobId] || null
}
function canReviewJob(job: ObservationJobView): boolean {
  return loadedForCurrentGroup.value && !busy.value && jobsFresh.value
    && jobPage.value?.items.some(item => item.job_id === job.job_id && item.revision === job.revision) === true
    && Boolean(reviewDrafts[job.job_id])
}

async function reloadJobs(target: string, requestSequence: number, epoch: number): Promise<boolean> {
  const next = await apiRequest<ObservationJobPageView>(`/api/admin/memory/observation-jobs?${paramsFor(target, 32)}`)
  if (!currentRequest(requestSequence, epoch, target)) return false
  jobPage.value = next
  jobsFresh.value = true
  return true
}

async function submitReview(job: ObservationJobView) {
  if (!canReviewJob(job)) return
  const target = loadedGroup.value
  const verdict = reviewDrafts[job.job_id]
  if (!verdict) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  actionError.value = ''
  notice.value = ''
  try {
    const body: ObservationReviewRequest = {
      group_id: target, job_id: job.job_id, expected_revision: job.revision, verdict,
    }
    const saved = await apiRequest<ObservationJobView>('/api/admin/memory/observation-review', {
      method: 'POST', body, adminMutation: true,
    })
    if (!currentRequest(requestSequence, epoch, target)) return
    jobPage.value = jobPage.value ? {
      ...jobPage.value,
      items: jobPage.value.items.map(item => item.job_id === saved.job_id ? saved : item),
    } : jobPage.value
    delete reviewDrafts[job.job_id]
    notice.value = '服务端已更新人工检查点；这不批准候选，也不会应用数据。'
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (handleUnauthorized(cause)) return
    jobsFresh.value = false
    if (isConflict(cause)) {
      try {
        if (await reloadJobs(target, requestSequence, epoch)) {
          actionError.value = '任务版本已变化；已重新读取最新任务，所选人工结果仍保留。核对后可再次提交。'
        }
      } catch (reloadCause) {
        if (currentRequest(requestSequence, epoch, target)) actionError.value = `版本冲突，重新读取失败；人工结果仍保留。${apiErrorMessage(reloadCause)}`
      }
    } else actionError.value = `${apiErrorMessage(cause)} 人工结果仍保留；请重新读取后核对。`
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function disableSlangReview() {
  if (!canDisableSlang.value) return
  const target = loadedGroup.value
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  actionError.value = ''
  notice.value = ''
  try {
    const actual = await apiRequest<SlangReviewStatusView>('/api/admin/memory/slang-review/disable', {
      method: 'POST', adminMutation: true,
    })
    if (!currentRequest(requestSequence, epoch, target)) return
    slangStatus.value = actual
    notice.value = actual.enabled ? '服务端仍报告俚语自动审阅已启用，请核对运行状态。' : '已关闭本次运行的俚语自动审阅；保存设置与运行状态分别管理。'
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (!handleUnauthorized(cause)) actionError.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function reloadGovernance(target: string, requestSequence: number, epoch: number): Promise<SlangGovernanceSuggestionPageView | null> {
  const next = await apiRequest<SlangGovernanceSuggestionPageView>(`/api/admin/memory/slang-governance?${paramsFor(target, 100)}`)
  if (!currentRequest(requestSequence, epoch, target)) return null
  governancePage.value = next
  governanceFresh.value = true
  return next
}
function canRevokeSuggestion(item: SlangGovernanceSuggestionView): boolean {
  return loadedForCurrentGroup.value && governanceFresh.value && !busy.value
    && governancePage.value?.items.some(current => current.suggestion_id === item.suggestion_id
      && current.revision === item.revision) === true
}
async function revokeSuggestion(item: SlangGovernanceSuggestionView) {
  if (!canRevokeSuggestion(item)) return
  const target = loadedGroup.value
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  governanceFresh.value = false
  actionError.value = ''
  notice.value = ''
  try {
    const body: SlangGovernanceRevokeRequest = {
      group_id: target, suggestion_id: item.suggestion_id, expected_revision: item.revision,
    }
    const result = await apiRequest<RevisionResponse>('/api/admin/memory/slang-governance/revoke', {
      method: 'POST', body, adminMutation: true,
    })
    if (!currentRequest(requestSequence, epoch, target)) return
    const refreshed = await reloadGovernance(target, requestSequence, epoch)
    if (!currentRequest(requestSequence, epoch, target) || !refreshed) return
    if (refreshed.items.some(current => current.suggestion_id === item.suggestion_id)) {
      actionError.value = `撤销返回修订 ${result.revision}，但建议仍在当前来源结果中；请重新读取确认。`
      governanceFresh.value = false
    } else notice.value = `已撤销建议并重新读取来源（修订 ${result.revision}）；不会改写俚语候选。`
  } catch (cause) {
    if (!currentRequest(requestSequence, epoch, target)) return
    if (handleUnauthorized(cause)) return
    if (isConflict(cause)) {
      try {
        const refreshed = await reloadGovernance(target, requestSequence, epoch)
        if (currentRequest(requestSequence, epoch, target) && refreshed) actionError.value = '建议已变化；已重新读取当前来源，请核对后再操作。'
      } catch (reloadCause) {
        if (currentRequest(requestSequence, epoch, target)) actionError.value = `版本冲突，来源重读失败：${apiErrorMessage(reloadCause)}`
      }
    } else actionError.value = `${apiErrorMessage(cause)} 写入结果可能不确定；请先重新读取来源。`
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

function codeLabel(code: SlangGovernanceSuggestionView['code']): string {
  return code === 'real_drift_pending' ? '发现实际漂移，待检查' : '建议静音'
}
function typedDetail(value: JsonValue | undefined): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''
}
function sourceIds(sources: ObservationPoolView['sources']): string {
  return sources.map(source => `${source.source_id} · ${source.subject_id} · r${source.source_revision}`).join('、')
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="观察池与审阅检查点">
    <p class="form-hint">人工结果只记录 observation job 的检查点；不会批准候选，也不会应用数据。所有展示仅含结构化候选编号、来源身份与状态，不回显原始消息正文。</p>
    <form class="form-row" @submit.prevent="void load()">
      <label for="observation-group">群 ID</label>
      <n-input id="observation-group" v-model:value="groupInput" :disabled="busy" placeholder="输入已获授权的群 ID" />
      <n-button attr-type="submit" :disabled="!canLoad" :loading="busy">读取观察与治理状态</n-button>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false" role="alert">{{ error }}</n-alert>
    <n-alert v-if="actionError" type="error" :show-icon="false" role="alert">{{ actionError }}</n-alert>
    <n-alert v-if="notice" type="success" :show-icon="false">{{ notice }}</n-alert>

    <template v-if="loadedForCurrentGroup && slangStatus">
      <h3>俚语自动审阅运行状态</h3>
      <p>本次运行：<n-tag>{{ slangStatus.enabled ? '已启用' : '已关闭' }}</n-tag>
        · 允许群：{{ slangStatus.allowed_groups.join('、') || '未配置' }}</p>
      <p v-if="slangStatus.last_report" class="form-hint">本次进程最近报告：{{ Object.entries(slangStatus.last_report).map(([key, value]) => `${key} ${value}`).join(' · ') }}</p>
      <n-button :disabled="!canDisableSlang" :loading="busy" @click="void disableSlangReview()">仅关闭本次运行的自动审阅</n-button>
      <p class="form-hint">此处只提供运行时停用，不提供运行时启用。保存配置与本次运行状态相互独立。</p>
    </template>

    <template v-if="loadedForCurrentGroup && observationPage">
      <h3>观察池</h3>
      <n-empty v-if="!observationPage.items.length" description="当前群没有可见的观察池条目" />
      <article v-for="item in observationPage.items" :key="item.pool_key">
        <p><n-tag>{{ item.domain === 'slang' ? '俚语' : '表达风格' }}</n-tag>
          · {{ item.count }} 条观察 · {{ item.candidate_ids.length }} 个候选 · pool {{ item.pool_key }}</p>
        <p>语义检查点 {{ item.semantic_checkpoint }} · backlog 检查点 {{ item.backlog_checkpoint }}
          · {{ item.output_policy ?? '无输出策略' }} · {{ item.risk_tags.join('、') || '无风险标记' }}</p>
        <p class="form-hint">来源：{{ sourceIds(item.sources) || '无来源身份' }}</p>
      </article>
      <n-button v-if="observationPage.next_cursor" :disabled="busy" @click="void loadMoreObservations()">加载更多观察池</n-button>
    </template>

    <template v-if="loadedForCurrentGroup && jobPage">
      <h3>观察任务与人工结果</h3>
      <n-empty v-if="!jobPage.items.length" description="当前群没有可见的观察任务" />
      <article v-for="item in jobPage.items" :key="item.job_id">
        <p>任务 {{ item.job_id }} · {{ item.chain === 'semantic' ? '语义链' : 'backlog 链' }}
          · {{ item.status }} · 修订 {{ item.revision }}</p>
        <p>候选 {{ item.candidate.candidate_id }} (r{{ item.candidate.candidate_revision }})
          · 对象 {{ item.object_id }} (r{{ item.object_revision }}) · {{ item.count }}/{{ item.threshold }} · pool {{ item.pool_key }}</p>
        <p class="form-hint">来源：{{ sourceIds(item.sources) || '无来源身份' }}</p>
        <div class="form-row">
          <n-select :value="reviewDraft(item.job_id)" :options="verdictOptions" placeholder="选择人工检查结果"
            :disabled="busy || !jobsFresh || !loadedForCurrentGroup" @update:value="setReviewDraft(item.job_id, $event)" />
          <n-button :disabled="!canReviewJob(item)" :loading="busy" @click="void submitReview(item)">记录检查点</n-button>
        </div>
      </article>
      <n-button v-if="jobPage.next_cursor" :disabled="busy" @click="void loadMoreJobs()">加载更多任务</n-button>
    </template>

    <template v-if="loadedForCurrentGroup && governancePage">
      <h3>俚语治理建议</h3>
      <n-empty v-if="!governancePage.items.length" description="当前群没有当前可见的治理建议" />
      <article v-for="item in governancePage.items" :key="item.suggestion_id">
        <p>建议 {{ item.suggestion_id }} · 修订 {{ item.revision }} · {{ codeLabel(item.code) }}</p>
        <p v-if="typedDetail(item.details.term)">术语：{{ typedDetail(item.details.term) }}</p>
        <p v-if="typedDetail(item.details.proposed_meaning)">拟议释义：{{ typedDetail(item.details.proposed_meaning) }}</p>
        <p v-if="typedDetail(item.details.candidate)">候选：{{ typedDetail(item.details.candidate) }}</p>
        <p v-if="typedDetail(item.details.chain)">链：{{ typedDetail(item.details.chain) }}</p>
        <n-button :disabled="!canRevokeSuggestion(item)" :loading="busy" @click="void revokeSuggestion(item)">撤销此治理建议</n-button>
      </article>
    </template>
  </n-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NSelect, NTag } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type {
  MemoryMatterPageView,
  MemoryMatterProposalRequest,
  MemoryMatterReplaceRequest,
  MemoryMatterTransitionRequest,
  MemoryMatterView,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const groupInput = ref('')
const subjectFilter = ref('')
const stateFilter = ref('all')
const loadedQuery = ref('')
const page = shallowRef<MemoryMatterPageView | null>(null)
const selected = shallowRef<MemoryMatterView | null>(null)
const matterIdInput = ref('')
const busy = ref(false)
const needsReload = ref(false)
const error = ref('')
const notice = ref('')
const cancelReason = ref('user_cancelled')
const replacing = shallowRef<MemoryMatterView | null>(null)
const draft = reactive({ subject: '', source: '', sourceRevision: '', summary: '', condition: '', observed: '', due: '', expiry: '' })
let operationId = ''
let sequence = 0
let controller: AbortController | undefined
let disposed = false

const states: Record<MemoryMatterView['state'], string> = {
  candidate: '待审核候选', approved: '已批准，尚未应用', active: '有效事项',
  completed: '已完成', cancelled: '已取消', expired: '已过期',
}
const stateOptions = [
  { label: '全部状态', value: 'all' },
  ...Object.entries(states).map(([value, label]) => ({ value, label })),
]
const reasonOptions = [
  { label: '本人取消', value: 'user_cancelled' },
  { label: '条件已改变', value: 'condition_changed' },
  { label: '不再需要', value: 'no_longer_needed' },
  { label: '管理员取消', value: 'cancelled' },
]
const queryKey = computed(() => [groupInput.value.trim(), subjectFilter.value.trim(), stateFilter.value].join('\0'))
const loadedForCurrentQuery = computed(() => (page.value !== null || selected.value !== null) && loadedQuery.value === queryKey.value)
const canLoad = computed(() => sessionState.adminAuthenticated && !busy.value && Boolean(groupInput.value.trim()))
const canWrite = computed(() => sessionState.adminAuthenticated && loadedForCurrentQuery.value && !busy.value && !needsReload.value)
const canLoadMore = computed(() => canLoad.value && loadedForCurrentQuery.value && Boolean(page.value?.next_cursor))
const canRead = computed(() => canLoad.value && Boolean(matterIdInput.value.trim()))
const draftValid = computed(() => Boolean(draft.subject.trim() && draft.source.trim() && draft.summary.trim())
  && Number.isSafeInteger(Number(draft.sourceRevision)) && Number(draft.sourceRevision) >= 1
  && Boolean(draft.expiry.trim()) && Number.isFinite(Number(draft.expiry))
  && (!draft.observed.trim() || Number.isFinite(Number(draft.observed)))
  && (!draft.due.trim() || Number.isFinite(Number(draft.due)))
  && (!replacing.value || draft.subject.trim() === replacing.value.subject_id))
const canPropose = computed(() => canWrite.value && draftValid.value)

function clearDraft() {
  replacing.value = null
  operationId = ''
  Object.assign(draft, { subject: '', source: '', sourceRevision: '', summary: '', condition: '', observed: '', due: '', expiry: '' })
}
function clear(clearInputs = false) {
  sequence += 1
  controller?.abort()
  controller = undefined
  page.value = null
  selected.value = null
  loadedQuery.value = ''
  matterIdInput.value = ''
  busy.value = false
  needsReload.value = false
  error.value = ''
  notice.value = ''
  cancelReason.value = 'user_cancelled'
  clearDraft()
  if (clearInputs) {
    groupInput.value = ''
    subjectFilter.value = ''
    stateFilter.value = 'all'
  }
}
watch(queryKey, () => clear(), { flush: 'sync' })
watch(draft, () => { operationId = '' }, { flush: 'sync' })
watch(() => sessionState.generation, () => clear(true), { flush: 'sync' })
watch(() => sessionState.adminAuthenticated, authenticated => { if (!authenticated) clear(true) }, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; clear(true) })

function current(requestSequence: number, epoch: number, query: string): boolean {
  return !disposed && requestSequence === sequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && queryKey.value === query
}
function begin() {
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  error.value = ''
  notice.value = ''
  return { sequence: ++sequence, epoch: currentAdminEpoch(), query: queryKey.value, signal: controller.signal }
}
function failure(cause: unknown, mutation = false) {
  if (isApiError(cause) && cause.status === 401) {
    expireAdminSession()
    clear(true)
    return
  }
  if (mutation) needsReload.value = true
  error.value = apiErrorMessage(cause) + (mutation ? ' 请重新读取核对实际状态后再操作。' : '')
}
function params(after?: string | null) {
  const value = new URLSearchParams({ group_id: groupInput.value.trim(), limit: '32' })
  if (subjectFilter.value.trim()) value.set('subject_id', subjectFilter.value.trim())
  if (stateFilter.value !== 'all') value.set('state', stateFilter.value)
  if (after) value.set('after', after)
  return value
}
async function load(more = false) {
  if (!canLoad.value || (more && !canLoadMore.value)) return
  const request = begin()
  try {
    const result = await apiRequest<MemoryMatterPageView>(`/api/admin/memory/matters?${params(more ? page.value?.next_cursor : null)}`, { signal: request.signal })
    if (!current(request.sequence, request.epoch, request.query)) return
    const items = new Map((more ? page.value?.items ?? [] : []).map(item => [item.matter_id, item]))
    for (const item of result.items) items.set(item.matter_id, item)
    page.value = { ...result, items: [...items.values()] }
    loadedQuery.value = request.query
    if (!more) selected.value = null
    needsReload.value = false
  } catch (cause) {
    if (current(request.sequence, request.epoch, request.query)) failure(cause)
  } finally {
    if (current(request.sequence, request.epoch, request.query)) busy.value = false
  }
}
async function read(matterId = matterIdInput.value.trim()) {
  if (!canLoad.value || !matterId) return
  const request = begin()
  try {
    const result = await apiRequest<MemoryMatterView>(`/api/admin/memory/matters/${encodeURIComponent(matterId)}?group_id=${encodeURIComponent(groupInput.value.trim())}`, { signal: request.signal })
    if (!current(request.sequence, request.epoch, request.query)) return
    showSaved(result)
    loadedQuery.value = request.query
    clearDraft()
    needsReload.value = false
  } catch (cause) {
    if (current(request.sequence, request.epoch, request.query)) {
      selected.value = null
      failure(cause)
    }
  } finally {
    if (current(request.sequence, request.epoch, request.query)) busy.value = false
  }
}
function showSaved(result: MemoryMatterView) {
  selected.value = result
  matterIdInput.value = result.matter_id
  if (!page.value) return
  const matches = (!subjectFilter.value.trim() || result.subject_id === subjectFilter.value.trim())
    && (stateFilter.value === 'all' || result.state === stateFilter.value)
  const items = page.value.items.filter(item => item.matter_id !== result.matter_id)
  if (matches) items.unshift(result)
  page.value = { ...page.value, items }
}
async function save(path: string, body: MemoryMatterProposalRequest | MemoryMatterReplaceRequest | MemoryMatterTransitionRequest, message: string) {
  if (!canWrite.value) return
  const request = begin()
  try {
    const result = await apiRequest<MemoryMatterView>(path, { method: 'POST', body, adminMutation: true, signal: request.signal })
    if (!current(request.sequence, request.epoch, request.query)) return
    showSaved(result)
    if (path === '/api/admin/memory/matters/replace') {
      // The API returns the new candidate; omit the old row until its cancelled version is read.
      if (page.value) page.value = { ...page.value, items: page.value.items.filter(item => item.matter_id !== result.replaces_matter_id) }
      needsReload.value = true
    }
    clearDraft()
    notice.value = message
  } catch (cause) {
    if (current(request.sequence, request.epoch, request.query)) failure(cause, true)
  } finally {
    if (current(request.sequence, request.epoch, request.query)) busy.value = false
  }
}
async function propose() {
  if (!canPropose.value) return
  operationId ||= crypto.randomUUID()
  const body: MemoryMatterProposalRequest = {
    group_id: groupInput.value.trim(), operation_id: operationId,
    subject_id: draft.subject.trim(), source_id: draft.source.trim(),
    expected_source_revision: Number(draft.sourceRevision), summary: draft.summary.trim(),
    condition: draft.condition.trim() || null, observed_at: draft.observed.trim() ? Number(draft.observed) : null,
    due_at: draft.due.trim() ? Number(draft.due) : null, expires_at: Number(draft.expiry),
  }
  if (replacing.value) {
    const replacement: MemoryMatterReplaceRequest = { ...body, matter_id: replacing.value.matter_id, expected_revision: replacing.value.revision }
    await save('/api/admin/memory/matters/replace', replacement, '旧事项已纠正并取消，新事项仍是待审核候选。')
  } else await save('/api/admin/memory/matters/propose', body, '已创建待审核候选；批准与应用仍需分别操作。')
}
function canTransition(action: 'review' | 'apply' | 'complete' | 'cancel'): boolean {
  if (!canWrite.value || !selected.value) return false
  const state = selected.value.state
  if (action === 'review') return state === 'candidate'
  if (action === 'apply') return state === 'approved'
  if (action === 'complete') return state === 'active'
  return ['candidate', 'approved', 'active'].includes(state) && Boolean(cancelReason.value)
}
async function transition(action: 'review' | 'apply' | 'complete' | 'cancel', decision?: 'approved' | 'rejected') {
  if (!canTransition(action) || !selected.value) return
  const body: MemoryMatterTransitionRequest = {
    group_id: groupInput.value.trim(), matter_id: selected.value.matter_id,
    expected_revision: selected.value.revision,
    ...(action === 'review' ? { decision } : {}),
    ...(action === 'cancel' ? { reason: cancelReason.value } : {}),
  }
  await save(`/api/admin/memory/matters/${action}`, body, action === 'review'
    ? (decision === 'rejected' ? '候选已拒绝，实际状态为已取消。' : '已批准；尚未应用到后续聊天。')
    : action === 'apply' ? '事项已应用，只由后续合法入站承接。'
    : action === 'complete' ? '事项已标记完成。' : '事项已取消。')
}
function startReplacement() {
  if (!canWrite.value || selected.value?.state !== 'active') return
  const item = selected.value
  clearDraft()
  replacing.value = item
  Object.assign(draft, {
    subject: item.subject_id, source: item.source_id, sourceRevision: String(item.source_revision),
    summary: item.summary, condition: item.condition ?? '', observed: item.observed_at === null ? '' : String(item.observed_at),
    due: item.due_at === null ? '' : String(item.due_at), expiry: String(item.expires_at),
  })
}
function timeLabel(value: number | null): string {
  if (value === null) return '未知'
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? `${date.toISOString()}（${value} 秒）` : `${value} 秒`
}
function reasonLabel(reason: string): string {
  return ({ rejected: '审核拒绝', corrected: '已被纠正', completed: '已完成', source_revoked: '来源撤回',
    user_cancelled: '本人取消', condition_changed: '条件已改变', no_longer_needed: '不再需要', cancelled: '管理员取消' } as Record<string, string>)[reason] ?? reason
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="有期限的本人事项">
    <p class="form-hint">仅管理仍有持久来源资格的本人事项。先建立候选，再审核、应用；有效事项由后续合法入站承接，不会自动提醒或发送 QQ。来源失效或事项结束后停止使用。</p>
    <form class="matters-fields" @submit.prevent="void load()">
      <label for="matters-group">群 ID<n-input id="matters-group" v-model:value="groupInput" :disabled="busy" placeholder="已获授权的群 ID" /></label>
      <label for="matters-subject-filter">本人 ID 筛选（可留空）<n-input id="matters-subject-filter" v-model:value="subjectFilter" :disabled="busy" /></label>
      <label for="matters-state">状态<n-select id="matters-state" v-model:value="stateFilter" :options="stateOptions" :disabled="busy" /></label>
      <div class="matters-actions"><n-button attr-type="submit" :disabled="!canLoad" :loading="busy">读取事项</n-button></div>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
    <n-alert v-if="notice" type="success" :show-icon="false">{{ notice }}</n-alert>
    <n-alert v-if="needsReload" type="warning" :show-icon="false">当前列表或操作结果需要重新核对，请重新读取后再修改。</n-alert>
    <p v-if="busy" role="status">正在读取或保存事项…</p>
    <n-empty v-if="!page && !busy" description="输入群 ID 后读取事项；查询不会开启捕获权限" />
    <template v-if="page">
      <n-empty v-if="!page.items.length" :description="page.next_cursor ? '当前页没有仍可见的事项，可继续加载。' : '此筛选下暂无可见事项'" />
      <article v-for="item in page.items" :key="item.matter_id" class="matter-row">
        <div class="matters-actions"><strong>{{ item.summary }}</strong><n-tag size="small">{{ states[item.state] }}</n-tag></div>
        <p class="matter-content">条件：{{ item.condition ?? '无附加条件' }}</p>
        <p class="form-hint">本人 {{ item.subject_id }} · 修订 {{ item.revision }} · 原因 {{ reasonLabel(item.reason) || '无' }}</p>
        <n-button :disabled="!canLoad" @click="void read(item.matter_id)">读取详情及当前版本</n-button>
      </article>
      <n-button v-if="page.next_cursor" :disabled="!canLoadMore" @click="void load(true)">加载更多事项</n-button>
    </template>
    <form class="matters-actions" @submit.prevent="void read()">
      <label for="matters-id">事项 ID<n-input id="matters-id" v-model:value="matterIdInput" :disabled="busy" placeholder="可按 ID 核对操作结果" /></label>
      <n-button attr-type="submit" :disabled="!canRead">读取单条事项</n-button>
    </form>
    <section v-if="selected" class="matter-detail" aria-label="当前事项详情">
      <div class="matters-actions"><h3>{{ selected.summary }}</h3><n-tag>{{ states[selected.state] }}</n-tag></div>
      <p class="matter-content">完整条件：{{ selected.condition ?? '无附加条件' }}</p>
      <dl class="matter-metadata">
        <dt>事项 / 修订</dt><dd>{{ selected.matter_id }} / {{ selected.revision }}</dd>
        <dt>群 / 本人</dt><dd>{{ selected.scope.group_id }} / {{ selected.subject_id }}</dd>
        <dt>来源 / 版本</dt><dd>{{ selected.source_id }} / {{ selected.source_revision }}</dd>
        <dt>事件时点</dt><dd>{{ timeLabel(selected.observed_at) }}</dd>
        <dt>约定时点</dt><dd>{{ timeLabel(selected.due_at) }}</dd>
        <dt>使用截止</dt><dd>{{ timeLabel(selected.expires_at) }}</dd>
        <dt>状态原因</dt><dd>{{ reasonLabel(selected.reason) || '无' }}</dd>
        <dt>替代旧事项</dt><dd>{{ selected.replaces_matter_id || '无' }}</dd>
        <dt>登记者 / 时点</dt><dd>{{ selected.actor }} / {{ timeLabel(selected.created_at) }}</dd>
        <dt>最近更新</dt><dd>{{ timeLabel(selected.updated_at) }}</dd>
      </dl>
      <div class="matters-actions">
        <n-button :disabled="!canTransition('review')" @click="void transition('review', 'approved')">批准候选</n-button>
        <n-button :disabled="!canTransition('review')" @click="void transition('review', 'rejected')">拒绝候选</n-button>
        <n-button :disabled="!canTransition('apply')" @click="void transition('apply')">应用已批准事项</n-button>
        <n-button :disabled="!canTransition('complete')" @click="void transition('complete')">标记完成</n-button>
        <n-button :disabled="!canWrite || selected.state !== 'active'" @click="startReplacement">纠正为新候选</n-button>
      </div>
      <div class="matters-actions">
        <label for="matters-cancel-reason">取消原因<n-select id="matters-cancel-reason" v-model:value="cancelReason" :options="reasonOptions" :disabled="busy" /></label>
        <n-button :disabled="!canTransition('cancel')" @click="void transition('cancel')">取消此事项</n-button>
      </div>
    </section>
    <h3>{{ replacing ? '纠正有效事项：旧事项将取消，新事项重新审核' : '建立本人事项候选' }}</h3>
    <p class="form-hint">填写本人来源身份及实际版本。时间使用 Unix 秒；未知事件时点或约定时点留空，不用登记时间代替。条件完整保留，摘要及条件各最多 256 字。</p>
    <form class="matters-fields" @submit.prevent="void propose()">
      <label for="matters-subject">本人 ID<n-input id="matters-subject" v-model:value="draft.subject" :disabled="busy || Boolean(replacing)" :maxlength="64" /></label>
      <label for="matters-source">来源 ID<n-input id="matters-source" v-model:value="draft.source" :disabled="busy" :maxlength="128" /></label>
      <label for="matters-source-revision">来源版本<n-input id="matters-source-revision" v-model:value="draft.sourceRevision" :disabled="busy" placeholder="正整数" /></label>
      <label for="matters-observed">事件时点（可留空）<n-input id="matters-observed" v-model:value="draft.observed" :disabled="busy" placeholder="与来源记录的 Unix 秒一致" /></label>
      <label for="matters-due">约定时点（可留空）<n-input id="matters-due" v-model:value="draft.due" :disabled="busy" placeholder="Unix 秒" /></label>
      <label for="matters-expiry">使用截止（必填）<n-input id="matters-expiry" v-model:value="draft.expiry" :disabled="busy" placeholder="Unix 秒" /></label>
      <label for="matters-summary" class="matter-wide">事项摘要<n-input id="matters-summary" v-model:value="draft.summary" :disabled="busy" :maxlength="256" /></label>
      <label for="matters-condition" class="matter-wide">完整条件（可留空）<n-input id="matters-condition" v-model:value="draft.condition" :disabled="busy" :maxlength="256" placeholder="例如：场地确认后再排练" /></label>
      <div class="matters-actions matter-wide">
        <n-button attr-type="submit" :disabled="!canPropose" :loading="busy">{{ replacing ? '取消旧事项并建立纠正候选' : '建立待审核候选' }}</n-button>
        <n-button v-if="replacing" :disabled="busy" @click="clearDraft">退出纠正草稿</n-button>
      </div>
    </form>
  </n-card>
</template>

<style scoped>
.matters-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: 12px; margin: 16px 0; }
.matters-fields label, .matters-actions label { display: grid; gap: 4px; }
.matters-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.matters-actions label { flex: 1; min-width: min(100%, 240px); }
.matter-wide { grid-column: 1 / -1; }
.matter-row { border-top: 1px solid var(--om-border); padding: 12px 0; }
.matter-content, .matter-metadata dd, .matter-row strong { white-space: pre-wrap; overflow-wrap: anywhere; }
.matter-detail { border: 1px solid var(--om-border); border-radius: 8px; padding: 16px; margin: 16px 0; }
.matter-metadata { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 16px; }
.matter-metadata dt { color: var(--om-muted); }
.matter-metadata dd { margin: 0; }
.matter-detail h3 { margin: 0; }
</style>

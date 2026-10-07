<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NSelect, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type {
  JournalApprovalRequest, JournalDecisionRequest, JournalDeliveryPageView, JournalDeliveryView,
  JournalDraftView, JournalDryRunView, JournalFactualPreviewRequest, JournalFictionPreviewView,
  JournalHeadPageView, JournalPublicConsentPageView, JournalPublishRequest, JournalResolveRequest,
  JournalSelectionView, JournalStatusView,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const group = ref('')
const loadedGroup = ref('')
const heads = ref<JournalHeadPageView | null>(null)
const status = ref<JournalStatusView | null>(null)
const consents = ref<JournalPublicConsentPageView | null>(null)
const consentIds = ref<string[]>([])
const deliveries = ref<JournalDeliveryPageView | null>(null)
const decisions = ref<JournalSelectionView[]>([])
const selected = ref<JournalDraftView | null>(null)
const sourceEvent = ref('')
const body = ref('')
const dryRun = ref<JournalDryRunView | null>(null)
const approvalScope = ref<'dry_run' | 'live'>('dry_run')
const publishMode = ref<'dry_run' | 'live'>('dry_run')
const resolutionId = ref('')
const resolutionOutcome = ref<'published' | 'failed' | null>(null)
const resolutionReceipt = ref('')
const busy = ref(false)
const needsReload = ref(false)
const draftCurrent = ref(false)
const error = ref('')
const notice = ref('')
let sequence = 0
let disposed = false
let controller: AbortController | null = null

function clearResolution() {
  resolutionId.value = ''; resolutionOutcome.value = null; resolutionReceipt.value = ''
}
function clearDraft() {
  selected.value = null; sourceEvent.value = ''; body.value = ''; dryRun.value = null
  draftCurrent.value = false; approvalScope.value = 'dry_run'; publishMode.value = 'dry_run'
}
function clearData() {
  sequence += 1; controller?.abort(); controller = null
  heads.value = null; status.value = null; consents.value = null; deliveries.value = null
  consentIds.value = []; decisions.value = []; loadedGroup.value = ''; busy.value = false
  needsReload.value = false; error.value = ''; notice.value = ''; clearDraft(); clearResolution()
}
watch(group, clearData, { flush: 'sync' })
watch(() => sessionState.generation, clearData, { flush: 'sync' })
watch(() => sessionState.adminAuthenticated, value => { if (!value) clearData() }, { flush: 'sync' })
watch(body, () => { dryRun.value = null }, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; clearData() })

const ready = computed(() => sessionState.adminAuthenticated && !busy.value && !needsReload.value
  && heads.value !== null && status.value !== null && loadedGroup.value === group.value.trim())
const dirty = computed(() => selected.value !== null && body.value !== selected.value.body)
const editable = computed(() => ready.value && (!selected.value
  || (draftCurrent.value && selected.value.content_kind === 'fiction' && selected.value.is_tip
    && ['pending_review', 'rejected'].includes(selected.value.state))))
const canCreate = computed(() => ready.value && status.value?.fiction_available === true && !selected.value
  && Boolean(sourceEvent.value.trim()) && Boolean(body.value.trim()))
const canRevise = computed(() => editable.value && selected.value !== null && dirty.value && Boolean(body.value.trim()))
const canReview = computed(() => ready.value && draftCurrent.value && selected.value?.is_tip === true
  && selected.value.state === 'pending_review' && !dirty.value)
const canApprove = computed(() => canReview.value && (approvalScope.value === 'dry_run' || status.value?.live_available === true))
const approved = computed(() => {
  const draft = selected.value, review = draft?.review
  return ready.value && draftCurrent.value && draft?.is_tip === true && draft.state === 'approved'
    && !dirty.value && review?.decision === 'approve' && review.body_hash === draft.body_hash
    && review.source_hash === draft.source_hash && review.content_hash === draft.content_hash
})
const canDryRun = computed(() => approved.value)
const selectedDelivery = computed(() => deliveries.value?.items.find(item =>
  item.draft_id === selected.value?.draft_id && item.mode === publishMode.value))
const canPublish = computed(() => approved.value && selected.value?.review?.approval_scope === publishMode.value
  && (publishMode.value === 'dry_run' || status.value?.live_available === true) && !selectedDelivery.value)
const canPreviewFiction = computed(() => ready.value && status.value?.fiction_available === true)
const canPreviewFactual = computed(() => ready.value && status.value?.archive_available === true
  && consentIds.value.length >= 1 && consentIds.value.length <= 2
  && consentIds.value.every(id => consents.value?.items.some(item => item.source_id === id && item.current)))
const resolving = computed(() => deliveries.value?.items.find(item => item.delivery_id === resolutionId.value))
const canResolve = computed(() => ready.value && resolving.value?.state === 'unknown'
  && resolutionOutcome.value !== null && (resolutionOutcome.value === 'failed' || Boolean(resolutionReceipt.value.trim())))
const modeOptions = computed(() => [
  { label: '本地演练（dry_run，无外发）', value: 'dry_run' },
  { label: '真实发布（live）', value: 'live', disabled: status.value?.live_available !== true },
])
const outcomeOptions = [
  { label: '已人工确认发布成功', value: 'published' },
  { label: '已人工确认失败', value: 'failed' },
]
const templateLabels: Record<string, string> = {
  social_public_event_solo_v1: '公开活动（单人同意）',
  social_public_event_duo_v1: '公开活动（双人同意）',
  attendance_solo_v1: '到场记录（单人同意）',
  attendance_duo_v1: '到场记录（双人同意）',
  milestone_solo_v1: '共同里程碑（单人同意）',
  milestone_duo_v1: '共同里程碑（双人同意）',
}

async function request<T>(operation: (signal: AbortSignal) => Promise<T>, apply: (value: T) => void) {
  controller?.abort(); controller = new AbortController()
  const own = ++sequence, epoch = currentAdminEpoch(), scope = group.value.trim()
  const current = () => !disposed && own === sequence && scope === group.value.trim()
    && isCurrentAdminEpoch(epoch) && sessionState.adminAuthenticated
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await operation(controller.signal)
    if (current()) apply(value)
  } catch (cause) {
    if (!current() || (cause instanceof Error && cause.name === 'AbortError')) return
    if (isApiError(cause) && cause.status === 403 && cause.code === 'story_scope_denied') {
      if (status.value) status.value.fiction_available = false
      error.value = '虚构来源当前不可用，需要运行版本的 Worldbook 和 Story 群授权。事实模板仍可单独使用。'
      return
    }
    if (isApiError(cause) && (cause.status === 401 || cause.status === 403)
      && cause.code !== 'journal_live_gate_closed') {
      clearData()
      if (cause.status === 401) expireAdminSession()
      error.value = cause.status === 401 ? '管理员会话已失效。' : `当前群的 Journal 操作授权不可用（${cause.code}）。`
      return
    }
    needsReload.value = true; draftCurrent.value = false; dryRun.value = null
    if (isApiError(cause) && cause.code === 'journal_live_gate_closed') {
      if (status.value) status.value.live_available = false
      error.value = '真实发布门已关闭；正文已保留，请重新读取实际状态。'
    } else if (isApiError(cause) && ['journal_source_expired', 'journal_source_changed', 'journal_source_unavailable',
      'journal_public_consent_unavailable'].includes(cause.code)) {
      error.value = `来源已失效或不可用（${cause.code}）；正文已保留，旧审批不能继续使用。请重新读取来源与草稿。`
    } else if (isApiError(cause) && cause.status === 409) {
      error.value = `状态或版本冲突（${cause.code}）；编辑正文已保留，请重新读取并核对最新修订。`
    } else if (isApiError(cause) && cause.code === 'invalid_journal_cursor') {
      error.value = '分页游标已失效；编辑正文已保留，请刷新状态回到第一页。'
    } else {
      error.value = `${apiErrorMessage(cause)} 请重新读取状态与投递记录；页面不会自动重发。`
    }
  } finally { if (current()) busy.value = false }
}

async function load() {
  if (!sessionState.adminAuthenticated || busy.value || !group.value.trim()) return
  const query = `group_id=${encodeURIComponent(group.value.trim())}&limit=64`
  await request(signal => Promise.all([
    apiRequest<JournalHeadPageView>(`/api/admin/journal?${query}`, { signal }),
    apiRequest<JournalStatusView>(`/api/admin/journal/status?${query}`, { signal }),
    apiRequest<JournalPublicConsentPageView>(`/api/admin/journal/consents?${query}`, { signal }),
    apiRequest<JournalDeliveryPageView>(`/api/admin/journal/deliveries?${query}`, { signal }),
  ]), ([page, currentStatus, currentConsents, currentDeliveries]) => {
    heads.value = page; status.value = currentStatus; consents.value = currentConsents; deliveries.value = currentDeliveries
    consentIds.value = consentIds.value.filter(id => currentConsents.items.some(item => item.source_id === id && item.current))
    loadedGroup.value = group.value.trim(); needsReload.value = false
    if (selected.value) draftCurrent.value = false
    if (!currentDeliveries.items.some(item => item.delivery_id === resolutionId.value && item.state === 'unknown')) clearResolution()
    notice.value = selected.value ? '状态已更新。读取最新修订会替换当前编辑正文；未读取前不能沿用旧审批。' : '当前群状态已读取。'
  })
}
async function loadMoreHeads() {
  const page = heads.value
  if (!ready.value || !page?.next_cursor) return
  const query = new URLSearchParams({ group_id: loadedGroup.value, limit: '64', cursor: page.next_cursor })
  await request(signal => apiRequest<JournalHeadPageView>(`/api/admin/journal?${query}`, { signal }), value => {
    const items = new Map(page.items.map(item => [item.root_id, item]))
    for (const item of value.items) items.set(item.root_id, item)
    heads.value = { ...value, items: [...items.values()] }
  })
}
async function loadMoreConsents() {
  const page = consents.value
  if (!ready.value || !page?.next_cursor) return
  const query = new URLSearchParams({ group_id: loadedGroup.value, limit: '64', cursor: page.next_cursor })
  await request(signal => apiRequest<JournalPublicConsentPageView>(`/api/admin/journal/consents?${query}`, { signal }), value => {
    const items = new Map(page.items.map(item => [item.source_id, item]))
    for (const item of value.items) items.set(item.source_id, item)
    consents.value = { ...value, items: [...items.values()] }
    consentIds.value = consentIds.value.filter(id => consents.value?.items.some(item => item.source_id === id && item.current))
  })
}
async function loadMoreDeliveries() {
  const page = deliveries.value
  if (!ready.value || !page?.next_cursor) return
  const query = new URLSearchParams({ group_id: loadedGroup.value, limit: '64', cursor: page.next_cursor })
  await request(signal => apiRequest<JournalDeliveryPageView>(`/api/admin/journal/deliveries?${query}`, { signal }), value => {
    const items = new Map(page.items.map(item => [item.delivery_id, item]))
    for (const item of value.items) items.set(item.delivery_id, item)
    deliveries.value = { ...value, items: [...items.values()] }
    if (resolving.value?.state !== 'unknown') clearResolution()
  })
}
function applyDraft(value: JournalDraftView) {
  selected.value = value; body.value = value.body; sourceEvent.value = value.source_event_id; dryRun.value = null
  draftCurrent.value = true; approvalScope.value = value.review?.approval_scope ?? 'dry_run'
  publishMode.value = value.review?.approval_scope ?? 'dry_run'
  if (!heads.value) return
  const head = { draft_id: value.draft_id, root_id: value.root_id, revision: value.revision,
    source_event_id: value.source_event_id, source_hash: value.source_hash, body_hash: value.body_hash,
    content_hash: value.content_hash, state: value.state, created_at: value.created_at }
  if (heads.value.items.some(item => item.draft_id === value.draft_id)) {
    heads.value.items = heads.value.items.map(item => item.draft_id === value.draft_id ? head : item)
  } else {
    heads.value.items = [head, ...heads.value.items.filter(item => item.root_id !== value.root_id)]
  }
}
async function select(draftId: string) {
  if (!ready.value) return
  await request(signal => apiRequest<JournalDraftView>(
    `/api/admin/journal/${encodeURIComponent(draftId)}?group_id=${encodeURIComponent(loadedGroup.value)}`, { signal }), applyDraft)
}
function chooseConsent(id: string, checked: boolean) {
  if (!ready.value) return
  if (!checked) { consentIds.value = consentIds.value.filter(item => item !== id); return }
  if (consentIds.value.includes(id)) return
  if (consentIds.value.length === 2) { error.value = '每次事实预览最多选择两个同模板的公开同意引用。'; return }
  consentIds.value = [...consentIds.value, id]
}
async function previewFiction() {
  if (!canPreviewFiction.value) return
  await request(signal => apiRequest<JournalFictionPreviewView>('/api/admin/journal/preview-fiction', {
    method: 'POST', adminMutation: true, signal,
    body: { group_id: loadedGroup.value, operation_id: crypto.randomUUID() },
  }), value => {
    decisions.value = value.decisions
    if (value.draft) { applyDraft(value.draft); notice.value = 'Story 来源已生成虚构草稿，仍需审核。' }
    else notice.value = '当前没有可成稿的 Story 来源，未创建草稿。'
  })
}
async function previewFactual() {
  if (!canPreviewFactual.value) return
  const payload: JournalFactualPreviewRequest = { group_id: loadedGroup.value,
    consent_source_ids: [...consentIds.value], operation_id: crypto.randomUUID() }
  await request(signal => apiRequest<JournalDraftView>('/api/admin/journal/preview-factual', {
    method: 'POST', adminMutation: true, signal, body: payload,
  }), value => { applyDraft(value); notice.value = '已按公开同意引用生成固定事实模板，正文不可改写，仍需审核。' })
}
async function create() {
  if (!canCreate.value) return
  await request(signal => apiRequest<JournalDraftView>('/api/admin/journal', { method: 'POST', adminMutation: true, signal,
    body: { group_id: loadedGroup.value, source_event_id: sourceEvent.value.trim(), body: body.value,
      operation_id: crypto.randomUUID() } }), applyDraft)
}
async function act(kind: 'revise' | 'approve' | 'reject' | 'dry-run') {
  const draft = selected.value
  if (!draft || (kind === 'revise' ? !canRevise.value : kind === 'dry-run' ? !canDryRun.value
    : kind === 'approve' ? !canApprove.value : !canReview.value)) return
  const payload = { group_id: loadedGroup.value, draft_id: draft.draft_id,
    expected_body_hash: draft.body_hash, expected_source_hash: draft.source_hash }
  if (kind === 'dry-run') {
    await request(signal => apiRequest<JournalDryRunView>('/api/admin/journal/dry-run',
      { method: 'POST', adminMutation: true, signal, body: payload }), value => { dryRun.value = value })
  } else {
    const decision: JournalDecisionRequest = { ...payload, operation_id: crypto.randomUUID() }
    const approval: JournalApprovalRequest = { ...decision, approval_scope: approvalScope.value }
    await request(signal => apiRequest<JournalDraftView>(`/api/admin/journal/${kind}`, { method: 'POST', adminMutation: true, signal,
      body: kind === 'revise' ? { ...decision, body: body.value } : kind === 'approve' ? approval : decision }), value => {
      applyDraft(value)
      notice.value = kind === 'revise' ? '新修订已保存，需重新审批。' : kind === 'approve'
        ? `仅批准${value.review?.approval_scope === 'live' ? '真实发布' : '本地演练'}；尚未执行发布。` : '已拒绝该修订。'
    })
  }
}
function applyDelivery(value: JournalDeliveryView) {
  if (deliveries.value) deliveries.value.items = [value, ...deliveries.value.items.filter(item => item.delivery_id !== value.delivery_id)]
}
function deliveryLabel(value: JournalDeliveryView): string {
  const state = { dispatching: '处理中', unknown: '结果未知，需人工核销', published: '已完成', failed: '失败' }[value.state]
  return value.mode === 'dry_run' ? `本地演练（无外发）：${state}` : `真实发布：${state}`
}
async function publish() {
  const draft = selected.value
  if (!draft || !canPublish.value) return
  const payload: JournalPublishRequest = { group_id: loadedGroup.value, draft_id: draft.draft_id,
    expected_body_hash: draft.body_hash, expected_source_hash: draft.source_hash,
    expected_content_hash: draft.content_hash, operation_id: crypto.randomUUID(), mode: publishMode.value }
  await request(signal => apiRequest<JournalDeliveryView>('/api/admin/journal/publish', {
    method: 'POST', adminMutation: true, signal, body: payload,
  }), value => { applyDelivery(value); notice.value = deliveryLabel(value) })
}
function chooseResolution(value: JournalDeliveryView) {
  if (!ready.value || value.state !== 'unknown') return
  resolutionId.value = value.delivery_id; resolutionOutcome.value = null; resolutionReceipt.value = ''
}
async function resolve() {
  if (!canResolve.value || resolutionOutcome.value === null) return
  const payload: JournalResolveRequest = { group_id: loadedGroup.value, delivery_id: resolutionId.value,
    outcome: resolutionOutcome.value, receipt: resolutionReceipt.value.trim() }
  await request(signal => apiRequest<JournalDeliveryView>('/api/admin/journal/resolve', {
    method: 'POST', adminMutation: true, signal, body: payload,
  }), value => { applyDelivery(value); clearResolution(); notice.value = '已记录人工核销结果；本次操作不会发送正文。' })
}
</script>

<template>
  <section class="journal-view" aria-label="Journal 预览、审核与发布">
    <n-alert type="info">预览、审批和发布分别操作。本地演练（dry_run）不会外发；真实发布（live）需当前运行通道和账号门禁可用。事实模板只使用本人明确公开同意的来源引用。</n-alert>
    <n-alert v-if="error" type="error" role="alert">{{ error }}</n-alert>
    <n-alert v-if="notice" type="info" role="status">{{ notice }}</n-alert>
    <n-card class="page-card" :bordered="false">
      <h2>读取群日记状态</h2>
      <div class="form-field"><label for="journal-group">准确群 ID</label>
        <n-input v-model:value="group" :input-props="{ id: 'journal-group' }" maxlength="64" /></div>
      <n-button :disabled="!sessionState.adminAuthenticated || busy || !group.trim()" :loading="busy" @click="load">读取 / 刷新状态</n-button>
      <p class="subtle-text">需要运行版本开启 Journal、允许该群，以及 journal.manage 管理授权。事实日志独立于 Worldbook；虚构成稿另需 Story 来源。</p>
      <template v-if="status">
        <div class="button-row"><n-tag :type="status.live_available ? 'success' : 'warning'">真实发布：{{ status.live_available ? '当前可用' : '当前不可用' }}</n-tag>
          <n-tag>虚构来源：{{ status.fiction_available ? '可用' : '不可用' }}</n-tag><n-tag>事实来源：{{ status.archive_available ? '可用' : '不可用' }}</n-tag></div>
        <p class="subtle-text">运行允许发布：{{ status.allow_live_publish ? '是' : '否' }}；通道已核验：{{ status.wire_validated ? '是' : '否' }}；外部通道：{{ status.external_transport ? '是' : '否' }}。</p>
        <p v-if="status.storage_error" role="alert">来源存储不可用：{{ status.storage_error }}</p>
      </template>
      <p v-if="needsReload" role="status">状态已失效，请刷新。编辑正文保留，旧审批不能继续执行。</p>
    </n-card>
    <template v-if="heads">
      <n-card class="page-card" :bordered="false">
        <h2>从当前来源预览</h2>
        <n-button :disabled="!canPreviewFiction" @click="previewFiction">从 Story 预览虚构日记</n-button>
        <p class="subtle-text">优先从已提交的故事来源成稿；来源不可用时不会创建虚构事实。</p>
        <p v-for="decision in decisions" :key="decision.source_event_id" class="subtle-text">{{ decision.source_event_id }} · {{ decision.reason }}</p>
        <h3>公开同意引用</h3>
        <p class="subtle-text">只显示不含原文与作者身份的引用。按模板选择一至两个当前引用，服务端核验期限与授权后生成固定正文。</p>
        <n-empty v-if="!consents?.items.length" description="没有可读取的公开同意引用" />
        <div v-for="consent in consents?.items" :key="consent.source_id" class="consent-row">
          <n-checkbox :checked="consentIds.includes(consent.source_id)" :disabled="!ready || !consent.current"
            @update:checked="checked => chooseConsent(consent.source_id, checked)">
            {{ templateLabels[consent.template_id] }} · {{ consent.labels[consent.label_index] }} · {{ consent.source_id }}
          </n-checkbox>
          <span class="subtle-text">{{ consent.current ? '当前有效' : `不可用：${consent.code}` }} · 到期 {{ new Date(consent.expires_at * 1000).toLocaleString() }}</span>
        </div>
        <n-button v-if="consents?.next_cursor" :disabled="!ready" @click="loadMoreConsents">加载更多公开同意引用</n-button>
        <n-button :disabled="!canPreviewFactual" @click="previewFactual">预览固定事实模板</n-button>
      </n-card>
      <n-card class="page-card" :bordered="false">
        <h2>当前修订</h2>
        <n-empty v-if="!heads.items.length" description="当前群没有草稿" />
        <div v-for="head in heads.items" :key="head.draft_id" class="button-row">
          <n-button :disabled="!ready" @click="select(head.draft_id)">读取 revision {{ head.revision }}</n-button>
          <n-tag>{{ head.state }}</n-tag><span class="subtle-text">{{ head.source_event_id }}</span>
        </div>
        <n-button v-if="heads.next_cursor" :disabled="!ready" @click="loadMoreHeads">加载更多草稿</n-button>
        <p class="subtle-text">读取修订会替换编辑正文；冲突后可先复制保留的正文。</p>
        <n-button :disabled="!ready" @click="clearDraft">清空当前编辑 / 手工虚构草稿</n-button>
      </n-card>
      <n-card class="page-card" :bordered="false">
        <h2>{{ selected ? `审核 revision ${selected.revision}（${selected.content_kind === 'factual' ? '固定事实模板' : '虚构日记'}）` : '手工虚构草稿' }}</h2>
        <div class="form-field"><label for="journal-source">{{ selected?.content_kind === 'factual' ? '固定模板来源引用' : '已提交故事事件 ID' }}</label>
          <n-input v-model:value="sourceEvent" :readonly="Boolean(selected) || !ready" :input-props="{ id: 'journal-source' }" maxlength="128" /></div>
        <div class="form-field"><label for="journal-body">正文</label>
          <n-input v-model:value="body" type="textarea" :readonly="!editable" :input-props="{ id: 'journal-body' }" maxlength="280" />
          <p class="subtle-text">事实正文由固定模板生成，不可改写；虚构正文保存时带“虚构故事里，”标识，完整正文最多 280 字。修改正文须保存新修订并重新审批。</p></div>
        <template v-if="selected">
          <p class="fingerprint">正文指纹：{{ selected.body_hash }}</p><p class="fingerprint">来源指纹：{{ selected.source_hash }}</p><p class="fingerprint">内容指纹：{{ selected.content_hash }}</p>
          <p v-if="selected.review">审批用途：{{ selected.review.approval_scope === 'live' ? '真实发布' : '本地演练' }} · {{ selected.review.decision }}</p>
          <p v-if="!draftCurrent" role="status">旧修订状态已失效，请读取最新修订后重新核对。</p>
        </template>
        <p v-if="dirty" role="status">正文尚未保存，请先保存新修订再审核。</p>
        <div class="form-field" v-if="selected?.state === 'pending_review'"><label for="journal-approval-scope">审批用途</label>
          <n-select v-model:value="approvalScope" :options="modeOptions" :disabled="!canReview" :input-props="{ id: 'journal-approval-scope' }" /></div>
        <div class="button-row">
          <n-button v-if="!selected" type="primary" :disabled="!canCreate" @click="create">创建虚构草稿</n-button>
          <n-button v-if="selected?.content_kind === 'fiction'" :disabled="!canRevise" @click="act('revise')">保存新修订</n-button>
          <n-button v-if="selected" :disabled="!canApprove" @click="act('approve')">批准{{ approvalScope === 'live' ? '真实发布' : '本地演练' }}</n-button>
          <n-button v-if="selected" :disabled="!canReview" @click="act('reject')">拒绝</n-button>
          <n-button v-if="selected" :disabled="!canDryRun" @click="act('dry-run')">查看本地演练描述</n-button>
        </div>
        <n-alert v-if="dryRun" type="success" role="status">revision {{ dryRun.revision }} 的本地演练描述（dry_run）已核验，正文与审批指纹一致；未真实发布、无外发。</n-alert>
        <template v-if="selected?.state === 'approved'">
          <div class="form-field"><label for="journal-publish-mode">执行用途（须与审批一致）</label>
            <n-select v-model:value="publishMode" :options="modeOptions" :disabled="!approved" :input-props="{ id: 'journal-publish-mode' }" /></div>
          <n-button type="primary" :disabled="!canPublish" @click="publish">{{ publishMode === 'live' ? '执行真实发布' : '执行本地演练（无外发）' }}</n-button>
          <p v-if="selectedDelivery" role="status">{{ deliveryLabel(selectedDelivery) }}。已有投递记录，页面不会再次发送。</p>
        </template>
      </n-card>
      <n-card class="page-card" :bordered="false">
        <h2>投递记录与人工核销</h2>
        <p class="subtle-text">未知结果不能当作失败或自动重发。先在实际发布目标核实，再记录已确认结果；核销操作不会发送正文。</p>
        <n-empty v-if="!deliveries?.items.length" description="当前群没有投递记录" />
        <div v-for="delivery in deliveries?.items" :key="delivery.delivery_id" class="delivery-row">
          <p>{{ deliveryLabel(delivery) }}</p><p class="fingerprint">{{ delivery.delivery_id }} · 草稿 {{ delivery.draft_id }} · 指纹 {{ delivery.payload_hash }}</p>
          <p v-if="delivery.receipt || delivery.code" class="subtle-text">回执 {{ delivery.receipt || '无' }} · {{ delivery.code }}</p>
          <n-button v-if="delivery.state === 'unknown'" :disabled="!ready" @click="chooseResolution(delivery)">人工核销此记录</n-button>
        </div>
        <n-button v-if="deliveries?.next_cursor" :disabled="!ready" @click="loadMoreDeliveries">加载更多投递记录</n-button>
        <template v-if="resolving?.state === 'unknown'">
          <p class="fingerprint">核销记录：{{ resolutionId }}</p>
          <div class="form-field"><label for="journal-resolution">实际核实结果</label>
            <n-select v-model:value="resolutionOutcome" :options="outcomeOptions" :disabled="!ready" :input-props="{ id: 'journal-resolution' }" placeholder="请选择已确认的结果" /></div>
          <div class="form-field"><label for="journal-receipt">成功回执（确认成功时必填）</label>
            <n-input v-model:value="resolutionReceipt" :disabled="!ready" maxlength="128" :input-props="{ id: 'journal-receipt' }" /></div>
          <n-button :disabled="!canResolve" @click="resolve">记录人工核销结果</n-button>
        </template>
      </n-card>
    </template>
  </section>
</template>

<style scoped>
.journal-view { display: grid; gap: 16px; }
.form-field { display: grid; gap: 8px; margin-bottom: 16px; }
.button-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 16px; }
.consent-row, .delivery-row { display: grid; gap: 8px; margin-bottom: 16px; }
.fingerprint { overflow-wrap: anywhere; }
</style>

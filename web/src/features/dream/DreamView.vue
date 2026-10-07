<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NInput,
  NModal,
  NSelect,
  NTag,
  NText,
} from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type {
  DreamDecisionRequest,
  DreamProposalPageView,
  DreamProposalView,
  DreamProposeRequest,
  StoryArcCreateRequest,
  StoryArcListView,
  StoryArcView,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const PAGE_SIZE = 32

const groupIdInput = ref('')
const loadedGroupId = ref('')
const arcs = ref<StoryArcView[]>([])
const arcsLoaded = ref(false)
const selectedArcId = ref('')
const proposals = ref<DreamProposalView[]>([])
const nextCursor = ref<string | null>(null)
const loading = ref(false)
const loadError = ref('')
const proposalError = ref('')
const actionError = ref('')
const notice = ref('')
const arcIdInput = ref('')
const arcTitleInput = ref('')
const arcStageInput = ref('active')
const busyAction = ref('')
const decisionProposal = ref<DreamProposalView | null>(null)
const decisionKind = ref<'accept' | 'reject' | null>(null)

let disposed = false
let readSequence = 0
let readController: AbortController | undefined
let mutationSequence = 0
let mutationController: AbortController | undefined

const queryIsDirty = computed(() => loadedGroupId.value !== groupIdInput.value.trim())
const hasLoadedGroup = computed(() => Boolean(loadedGroupId.value))
const canSearch = computed(() => sessionState.adminAuthenticated
  && Boolean(groupIdInput.value.trim()) && !loading.value && !busyAction.value)
const activeArcs = computed(() => arcs.value.filter((arc) => arc.status === 'active'))
const currentArc = computed(() => activeArcs.value.find((arc) => arc.arc_id === selectedArcId.value) ?? null)
const hasMainArc = computed(() => arcs.value.some((arc) => arc.role === 'main'))
const arcOptions = computed(() => activeArcs.value.map((arc) => ({
  label: `${arc.title || arc.arc_id} · ${roleLabel(arc.role)} · ${arc.stage}`,
  value: arc.arc_id,
})))
const canLoadMore = computed(() => sessionState.adminAuthenticated && Boolean(nextCursor.value)
  && !loading.value && !busyAction.value && !queryIsDirty.value)
const canCreateArc = computed(() => sessionState.adminAuthenticated && hasLoadedGroup.value
  && !queryIsDirty.value && !loading.value && !busyAction.value
  && arcsLoaded.value
  && !hasMainArc.value
  && Boolean(arcIdInput.value.trim()) && Boolean(arcTitleInput.value.trim())
  && Boolean(arcStageInput.value.trim()))
const dreamModelReady = computed(() => sessionState.status?.mode === 'live')
const canGenerateDream = computed(() => sessionState.adminAuthenticated && dreamModelReady.value
  && Boolean(currentArc.value) && !queryIsDirty.value && !loading.value && !busyAction.value)
const canOpenDecision = computed(() => sessionState.adminAuthenticated && hasLoadedGroup.value
  && !queryIsDirty.value && !loading.value && !busyAction.value)

function roleLabel(role: string): string {
  const labels: Record<string, string> = { main: '主线', side: '支线', ambient: '背景' }
  return labels[role] ?? role
}

function arcStatusLabel(status: string): string {
  return status === 'active' ? '进行中' : status === 'closed' ? '已结束' : status
}

function proposalStatusLabel(proposal: DreamProposalView): string {
  if (proposal.decision_status === 'rejected') return '已拒绝'
  if (proposal.committed_event_id) return '管理员已接受并提交'
  if (proposal.decision_status === 'validated') return '管理员已接受 · 待提交重试'
  return '待管理员审核'
}

function proposalStatusType(proposal: DreamProposalView): 'warning' | 'success' | 'error' {
  if (proposal.decision_status === 'rejected') return 'error'
  if (proposal.committed_event_id) return 'success'
  return 'warning'
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function errorMessage(error: unknown): string {
  if (isApiError(error)) {
    const labels: Record<string, string> = {
      invalid_input: '输入不符合服务端合同，请核对群 ID、Arc ID、标题和阶段。',
      story_main_ambiguous: '该群已经存在主线 Arc，当前服务端不允许再创建另一条。',
    }
    return labels[error.code] ?? apiErrorMessage(error)
  }
  return apiErrorMessage(error)
}

function isCurrentRead(sequence: number, epoch: number, groupId: string): boolean {
  return !disposed && sequence === readSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && loadedGroupId.value === groupId
}

function isCurrentMutation(sequence: number, epoch: number, groupId: string): boolean {
  return !disposed && sequence === mutationSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && loadedGroupId.value === groupId
}

function assignProposals(items: DreamProposalView[], replace: boolean): void {
  if (replace) {
    proposals.value = items
    return
  }
  const knownIds = new Set(proposals.value.map((proposal) => proposal.proposal_id))
  proposals.value = [...proposals.value, ...items.filter((proposal) => !knownIds.has(proposal.proposal_id))]
}

function requestPageUrl(after: string | null): string {
  const params = new URLSearchParams({ group_id: loadedGroupId.value, limit: String(PAGE_SIZE) })
  if (after) params.set('after', after)
  return `/api/admin/dream/proposals?${params.toString()}`
}

async function searchGroup(): Promise<void> {
  const groupId = groupIdInput.value.trim()
  if (!groupId) {
    loadError.value = '请填写准确的群 ID。'
    return
  }
  if (!canSearch.value) return

  readController?.abort()
  const controller = new AbortController()
  readController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++readSequence
  loadedGroupId.value = groupId
  arcs.value = []
  arcsLoaded.value = false
  selectedArcId.value = ''
  proposals.value = []
  nextCursor.value = null
  loadError.value = ''
  proposalError.value = ''
  actionError.value = ''
  notice.value = ''
  loading.value = true

  const params = new URLSearchParams({ group_id: groupId })
  const [arcResult, proposalResult] = await Promise.allSettled([
    apiRequest<StoryArcListView>(`/api/admin/story/arcs?${params.toString()}`, { signal: controller.signal }),
    apiRequest<DreamProposalPageView>(requestPageUrl(null), { signal: controller.signal }),
  ])
  if (!isCurrentRead(sequence, epoch, groupId)) return

  if (arcResult.status === 'fulfilled') {
    arcs.value = arcResult.value.items
    arcsLoaded.value = true
    const preferred = activeArcs.value.find((arc) => arc.role === 'main') ?? activeArcs.value[0]
    selectedArcId.value = preferred?.arc_id ?? ''
  } else if (isApiError(arcResult.reason) && arcResult.reason.status === 401) {
    expireAdminSession()
    return
  } else if (!isAbortError(arcResult.reason)) {
    loadError.value = errorMessage(arcResult.reason)
  }

  if (proposalResult.status === 'fulfilled') {
    assignProposals(proposalResult.value.items, true)
    nextCursor.value = proposalResult.value.next_cursor
  } else if (isApiError(proposalResult.reason) && proposalResult.reason.status === 401) {
    expireAdminSession()
    return
  } else if (!isAbortError(proposalResult.reason)) {
    proposalError.value = errorMessage(proposalResult.reason)
  }
  loading.value = false
}

async function loadMore(): Promise<void> {
  if (!canLoadMore.value || !nextCursor.value) return
  readController?.abort()
  const controller = new AbortController()
  readController = controller
  const epoch = currentAdminEpoch()
  const sequence = ++readSequence
  const groupId = loadedGroupId.value
  loading.value = true
  proposalError.value = ''
  try {
    const page = await apiRequest<DreamProposalPageView>(requestPageUrl(nextCursor.value), { signal: controller.signal })
    if (!isCurrentRead(sequence, epoch, groupId)) return
    assignProposals(page.items, false)
    nextCursor.value = page.next_cursor
  } catch (error: unknown) {
    if (!isCurrentRead(sequence, epoch, groupId) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    proposalError.value = errorMessage(error)
  } finally {
    if (sequence === readSequence) loading.value = false
  }
}

function replaceArc(updated: StoryArcView): void {
  const known = arcs.value.some((arc) => arc.arc_id === updated.arc_id)
  arcs.value = known
    ? arcs.value.map((arc) => arc.arc_id === updated.arc_id ? updated : arc)
    : [updated, ...arcs.value]
}

function replaceProposal(updated: DreamProposalView): void {
  const known = proposals.value.some((proposal) => proposal.proposal_id === updated.proposal_id)
  proposals.value = known
    ? proposals.value.map((proposal) => proposal.proposal_id === updated.proposal_id ? updated : proposal)
    : [updated, ...proposals.value]
}

async function createArc(): Promise<void> {
  if (!canCreateArc.value) return
  const groupId = loadedGroupId.value
  const epoch = currentAdminEpoch()
  const sequence = ++mutationSequence
  mutationController?.abort()
  const controller = new AbortController()
  mutationController = controller
  busyAction.value = 'arc'
  actionError.value = ''
  notice.value = ''
  const body = {
    group_id: groupId,
    arc_id: arcIdInput.value.trim(),
    title: arcTitleInput.value.trim(),
    stage: arcStageInput.value.trim(),
  } satisfies StoryArcCreateRequest
  try {
    const created = await apiRequest<StoryArcView>('/api/admin/story/arcs', {
      method: 'POST', adminMutation: true, body, signal: controller.signal,
    })
    if (!isCurrentMutation(sequence, epoch, groupId)) return
    replaceArc(created)
    if (created.status === 'active') selectedArcId.value = created.arc_id
    arcIdInput.value = ''
    arcTitleInput.value = ''
    arcStageInput.value = 'active'
    notice.value = `Arc「${created.title || created.arc_id}」已创建。`
  } catch (error: unknown) {
    if (!isCurrentMutation(sequence, epoch, groupId) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    else actionError.value = errorMessage(error)
  } finally {
    if (sequence === mutationSequence) busyAction.value = ''
  }
}

async function generateDream(): Promise<void> {
  if (!canGenerateDream.value || !currentArc.value) return
  const groupId = loadedGroupId.value
  const epoch = currentAdminEpoch()
  const sequence = ++mutationSequence
  mutationController?.abort()
  const controller = new AbortController()
  mutationController = controller
  busyAction.value = 'dream'
  actionError.value = ''
  notice.value = ''
  const body = { group_id: groupId, arc_id: currentArc.value.arc_id } satisfies DreamProposeRequest
  try {
    const created = await apiRequest<DreamProposalView>('/api/admin/dream/propose', {
      method: 'POST', adminMutation: true, body, signal: controller.signal,
    })
    if (!isCurrentMutation(sequence, epoch, groupId)) return
    replaceProposal(created)
    notice.value = 'Dream 提案已生成，尚未接受或提交虚构事件。请检查摘要与完整 payload。'
  } catch (error: unknown) {
    if (!isCurrentMutation(sequence, epoch, groupId) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    else actionError.value = errorMessage(error)
  } finally {
    if (sequence === mutationSequence) busyAction.value = ''
  }
}

function askDecision(proposal: DreamProposalView, kind: 'accept' | 'reject'): void {
  if (!canOpenDecision.value || proposal.group_id !== loadedGroupId.value) return
  if (proposal.decision_status === 'rejected' || proposal.committed_event_id) return
  decisionProposal.value = proposal
  decisionKind.value = kind
}

function closeDecision(): void {
  if (busyAction.value) return
  decisionProposal.value = null
  decisionKind.value = null
}

async function confirmDecision(): Promise<void> {
  const proposal = decisionProposal.value
  const kind = decisionKind.value
  if (!proposal || !kind || !canOpenDecision.value) return
  const groupId = loadedGroupId.value
  const epoch = currentAdminEpoch()
  const sequence = ++mutationSequence
  mutationController?.abort()
  const controller = new AbortController()
  mutationController = controller
  busyAction.value = kind
  actionError.value = ''
  notice.value = ''
  const body = { group_id: groupId, proposal_id: proposal.proposal_id } satisfies DreamDecisionRequest
  try {
    const updated = await apiRequest<DreamProposalView>(`/api/admin/dream/${kind}`, {
      method: 'POST', adminMutation: true, body, signal: controller.signal,
    })
    if (!isCurrentMutation(sequence, epoch, groupId)) return
    replaceProposal(updated)
    if (kind === 'accept') {
      notice.value = updated.committed_event_id
        ? '已接受并提交虚构事件。'
        : '服务端返回机器校验通过，但尚未确认事件提交；请刷新并核对状态。'
    } else {
      notice.value = '提案已拒绝，未提交虚构事件。'
    }
    decisionProposal.value = null
    decisionKind.value = null
  } catch (error: unknown) {
    if (!isCurrentMutation(sequence, epoch, groupId) || isAbortError(error)) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    else actionError.value = errorMessage(error)
  } finally {
    if (sequence === mutationSequence) busyAction.value = ''
  }
}

onMounted(() => {
  disposed = false
})

function clearPageState(): void {
  readSequence += 1
  mutationSequence += 1
  readController?.abort()
  readController = undefined
  mutationController?.abort()
  mutationController = undefined
  groupIdInput.value = ''
  loadedGroupId.value = ''
  arcs.value = []
  arcsLoaded.value = false
  selectedArcId.value = ''
  proposals.value = []
  nextCursor.value = null
  arcIdInput.value = ''
  arcTitleInput.value = ''
  arcStageInput.value = 'active'
  decisionProposal.value = null
  decisionKind.value = null
  loading.value = false
  busyAction.value = ''
  loadError.value = ''
  proposalError.value = ''
  actionError.value = ''
  notice.value = ''
}

onBeforeUnmount(() => {
  disposed = true
  clearPageState()
})

watch(() => sessionState.adminAuthenticated, (authenticated) => {
  if (!authenticated) clearPageState()
})
</script>

<template>
  <section class="dream-view" aria-label="Dream 故事审核">
    <n-alert class="notice" type="info" :show-icon="true">
      Dream 内容是虚构提案。接受提案会把虚构事件提交到本实例的故事账本并改变故事状态，不会发送 QQ 消息；请先核对摘要和完整 payload。机器校验通过不代表管理员已经接受。
    </n-alert>
    <n-alert v-if="loadError" class="notice" type="error" :show-icon="true" role="alert">{{ loadError }}</n-alert>
    <n-alert v-if="proposalError" class="notice" type="error" :show-icon="true" role="alert">{{ proposalError }}</n-alert>
    <n-alert v-if="actionError" class="notice" type="error" :show-icon="true" role="alert">{{ actionError }}</n-alert>
    <n-alert v-if="notice" class="notice" type="success" :show-icon="true" role="status" aria-live="polite">{{ notice }}</n-alert>

    <n-card class="page-card" :bordered="false">
      <div class="section-heading">
        <div>
          <p class="eyebrow">精确范围</p>
          <h2>读取一个群的故事</h2>
          <p class="subtle-text">请输入完整群 ID。查询按该值精确读取 Arc 和提案，不支持通配搜索。</p>
        </div>
      </div>
      <form class="dream-query-form" @submit.prevent="searchGroup">
        <div class="form-field">
          <label class="field-label" for="dream-group-input">准确群 ID</label>
          <n-input
            id="dream-group"
            v-model:value="groupIdInput"
            :input-props="{ id: 'dream-group-input', 'aria-label': '准确群 ID' }"
            maxlength="64"
            autocomplete="off"
            :disabled="loading || Boolean(busyAction)"
            placeholder="填写完整群 ID"
          />
          <p class="form-hint">只清除首尾空白，群 ID 内部字符按原值发送。</p>
        </div>
        <div class="button-row">
          <n-button type="primary" attr-type="submit" :loading="loading" :disabled="!canSearch">读取故事</n-button>
          <n-tag v-if="hasLoadedGroup" size="small" round>群 {{ loadedGroupId }}</n-tag>
          <n-text v-if="queryIsDirty && hasLoadedGroup" depth="3" role="status" aria-live="polite">
            群 ID 已更改；重新读取前，当前列表的操作已停用。
          </n-text>
        </div>
      </form>
    </n-card>

    <template v-if="hasLoadedGroup">
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">当前 Arc</p>
            <h2>故事主线</h2>
            <p class="subtle-text">选择一个进行中的 Arc 作为当前上下文；Dream 生成只会针对该 Arc。</p>
          </div>
          <n-tag v-if="currentArc" size="small" round>{{ roleLabel(currentArc.role) }}</n-tag>
        </div>

        <div v-if="activeArcs.length" class="arc-layout">
          <div class="form-field">
            <label class="field-label" for="dream-current-arc">当前 Arc</label>
            <n-select
              id="dream-current-arc"
              v-model:value="selectedArcId"
              :options="arcOptions"
              :disabled="loading || Boolean(busyAction) || queryIsDirty"
              aria-label="选择当前 Arc"
            />
          </div>
          <div v-if="currentArc" class="arc-summary">
            <strong>{{ currentArc.title || currentArc.arc_id }}</strong>
            <span><code>{{ currentArc.arc_id }}</code></span>
            <span>阶段：{{ currentArc.stage }} · 修订：{{ currentArc.revision }}</span>
          </div>
        </div>
        <n-empty v-else description="该群还没有进行中的 Arc" />
        <p v-if="!dreamModelReady" class="form-hint model-hint">
          当前离线隔离模式没有可用的真实 Dream 模型，因此“生成 Dream”默认停用；可先建立 Arc、查看、接受或拒绝已有提案。接受只写入本实例的虚构故事账本，不调用模型或 QQ。实时模式下，服务端仍会检查模型配置和群权限。
        </p>
        <div class="button-row dream-actions">
          <n-button type="primary" :disabled="!canGenerateDream" :loading="busyAction === 'dream'" @click="void generateDream()">
            生成 Dream
          </n-button>
          <n-text v-if="dreamModelReady" depth="3">实时模式可提交生成请求；服务端按模型配置、Dream 功能门和群授权检查。</n-text>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Arc 管理</p>
            <h2>创建主线 Arc</h2>
            <p class="subtle-text">当前接口只创建当前群的主线 Arc；新建只登记故事容器，不会生成虚构事件。</p>
          </div>
        </div>
        <n-alert v-if="hasMainArc" class="notice" type="info" :show-icon="true">
          该群已经存在主线 Arc；服务端只允许一个主线，因此不能再次创建。
        </n-alert>
        <form class="arc-create-form" @submit.prevent="createArc">
          <div class="form-field">
            <label class="field-label" for="dream-arc-id">Arc ID</label>
            <n-input id="dream-arc-id" v-model:value="arcIdInput" maxlength="128" autocomplete="off" :disabled="loading || Boolean(busyAction)" />
          </div>
          <div class="form-field">
            <label class="field-label" for="dream-arc-title">标题</label>
            <n-input id="dream-arc-title" v-model:value="arcTitleInput" maxlength="200" :disabled="loading || Boolean(busyAction)" />
          </div>
          <div class="form-field">
            <label class="field-label" for="dream-arc-stage">阶段</label>
            <n-input id="dream-arc-stage" v-model:value="arcStageInput" maxlength="64" :disabled="loading || Boolean(busyAction)" />
          </div>
          <div class="button-row arc-create-action">
          <n-button type="primary" attr-type="submit" :disabled="!canCreateArc" :loading="busyAction === 'arc'">创建主线 Arc</n-button>
          </div>
        </form>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">人工审核</p>
            <h2>Dream 提案</h2>
            <p class="subtle-text">每页最多 {{ PAGE_SIZE }} 条；pending、validated 和 rejected 均保留供核对。</p>
          </div>
          <n-tag v-if="proposals.length" size="small" round>{{ proposals.length }} 条已载入</n-tag>
        </div>

        <n-empty v-if="!loading && !proposals.length && !proposalError" description="该群还没有已保存的 Dream 提案" />
        <div class="proposal-list">
          <article v-for="proposal in proposals" :key="proposal.proposal_id" class="proposal-card">
            <div class="proposal-heading">
              <div>
                <h3>{{ proposal.summary || proposal.kind }}</h3>
                <p class="proposal-meta">
                  <code>{{ proposal.proposal_id }}</code> · Arc <code>{{ proposal.target_arc_id }}</code> · {{ formatTime(proposal.created_at) }}
                </p>
              </div>
              <n-tag :type="proposalStatusType(proposal)" size="small" round>{{ proposalStatusLabel(proposal) }}</n-tag>
            </div>
            <p class="proposal-kind">类型：{{ proposal.kind }} · 目标 Arc 修订：{{ proposal.target_arc_revision }}</p>
            <div class="payload-block">
              <span class="field-label">完整 payload</span>
              <pre>{{ JSON.stringify(proposal.payload, null, 2) }}</pre>
            </div>
            <p v-if="proposal.reason" class="proposal-reason">服务端说明：{{ proposal.reason }}</p>
            <p v-if="proposal.committed_event_id" class="proposal-meta">
              已提交事件 <code>{{ proposal.committed_event_id }}</code> · {{ proposal.committed_at ? formatTime(proposal.committed_at) : '提交时间未知' }}
            </p>
            <div v-if="proposal.decision_status !== 'rejected' && !proposal.committed_event_id" class="button-row proposal-actions">
              <n-button
                type="primary"
                :disabled="!canOpenDecision || proposal.group_id !== loadedGroupId"
                :loading="busyAction === 'accept' && decisionProposal?.proposal_id === proposal.proposal_id"
                @click="askDecision(proposal, 'accept')"
              >
                接受并提交虚构事件
              </n-button>
              <n-button
                type="error"
                secondary
                :disabled="!canOpenDecision || proposal.group_id !== loadedGroupId"
                :loading="busyAction === 'reject' && decisionProposal?.proposal_id === proposal.proposal_id"
                @click="askDecision(proposal, 'reject')"
              >拒绝提案</n-button>
            </div>
          </article>
        </div>

        <div v-if="nextCursor" class="pager-row">
          <n-button secondary :loading="loading" :disabled="!canLoadMore" @click="void loadMore()">加载更多提案</n-button>
          <n-text depth="3">列表按服务端游标继续读取，不会一次载入全部历史。</n-text>
        </div>
      </n-card>
    </template>

    <n-modal :show="Boolean(decisionProposal && decisionKind)" :mask-closable="!busyAction" @update:show="(show) => { if (!show) closeDecision() }">
      <n-card class="decision-modal" :bordered="false" role="dialog" aria-modal="true">
        <template #header>
          <div class="section-heading">
            <h2>{{ decisionKind === 'accept' ? '确认接受 Dream 提案' : '确认拒绝 Dream 提案' }}</h2>
          </div>
        </template>
        <p v-if="decisionKind === 'accept'" class="decision-warning">接受会提交虚构事件并改变故事状态。请确认已经检查摘要和 payload。</p>
        <p v-else>拒绝后该提案不会提交为故事事件。</p>
        <p v-if="decisionProposal" class="decision-summary">{{ decisionProposal.summary }}</p>
        <div class="button-row modal-actions">
          <n-button :disabled="Boolean(busyAction)" @click="closeDecision">取消</n-button>
          <n-button
            :type="decisionKind === 'accept' ? 'primary' : 'error'"
            :loading="Boolean(busyAction)"
            @click="void confirmDecision()"
          >
            {{ decisionKind === 'accept' ? '确认接受并提交' : '确认拒绝' }}
          </n-button>
        </div>
      </n-card>
    </n-modal>
  </section>
</template>

<style scoped>
.dream-view {
  display: grid;
  gap: 16px;
}

.page-card {
  border-radius: var(--om-radius);
  box-shadow: var(--om-shadow);
}

.dream-query-form,
.arc-create-form {
  display: grid;
  gap: 16px;
}

.dream-query-form .form-field {
  max-width: 480px;
}

.arc-create-form {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: end;
}

.arc-create-action {
  grid-column: 1 / -1;
}

.form-field {
  display: grid;
  min-width: 0;
  gap: 8px;
}

.field-label {
  color: var(--om-muted);
  font-size: 12px;
  font-weight: 700;
}

.subtle-text,
.form-hint,
.proposal-meta,
.proposal-kind {
  color: var(--om-muted);
  font-size: 13px;
}

.form-hint {
  margin: 0;
}

.model-hint {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface-soft);
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}

.arc-layout {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) minmax(240px, 1.4fr);
  align-items: end;
  gap: 16px;
}

.arc-summary {
  display: grid;
  min-width: 0;
  gap: 4px;
  padding: 12px 16px;
  overflow-wrap: anywhere;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface-soft);
}

.dream-actions {
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.proposal-list {
  display: grid;
  gap: 12px;
}

.proposal-card {
  min-width: 0;
  padding: 16px;
  overflow-wrap: anywhere;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius);
  background: var(--om-surface-soft);
}

.proposal-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.proposal-heading h3,
.proposal-meta,
.proposal-kind,
.proposal-reason {
  margin: 0;
}

.proposal-heading h3 {
  font-size: 16px;
  font-weight: 720;
}

.proposal-meta {
  margin-top: 4px;
}

.proposal-kind {
  margin-top: 8px;
}

.payload-block {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.payload-block pre {
  max-height: 420px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  background: var(--om-surface);
  font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
}

.proposal-reason {
  margin-top: 12px;
  color: var(--om-muted);
}

.proposal-actions {
  padding-top: 12px;
  border-top: 1px solid var(--om-border);
}

.pager-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
}

.decision-modal {
  width: min(560px, calc(100vw - 32px));
  border-radius: var(--om-radius);
}

.decision-modal h2 {
  margin: 0;
  font-size: 18px;
}

.decision-warning {
  padding: 12px;
  border: 1px solid var(--om-warning);
  border-radius: var(--om-radius-sm);
  background: var(--om-warning-soft);
}

.decision-summary {
  overflow-wrap: anywhere;
  font-weight: 650;
}

.modal-actions {
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .arc-create-form,
  .arc-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .proposal-heading {
    flex-direction: column;
  }
}
</style>

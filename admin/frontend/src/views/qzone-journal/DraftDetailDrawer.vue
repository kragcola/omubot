<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  EyeOutline,
  FlaskOutline,
  HelpCircleOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  approveQzoneDraft,
  confirmNotPublishedQzoneDraft,
  confirmPublishedQzoneDraft,
  dryRunQzoneDraft,
  extractApiError,
  fetchQzoneDraft,
  fetchQzoneDraftAudit,
  fetchQzoneDraftRevisions,
  recomposeQzoneDraft,
  rejectQzoneDraft,
} from '../../api/qzoneJournal'
import AppDrawerHeader from '../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../components/common/AppDrawerLayout.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import DraftContextPanels from './DraftContextPanels.vue'
import DraftHistoryPanels from './DraftHistoryPanels.vue'
import type {
  QzoneDraft,
  QzoneDraftAuditResponse,
  QzoneDraftRevision,
  QzoneDryRunDescriptor,
} from './types'
import { statusBadge, statusLabel } from './types'

const props = defineProps<{
  show: boolean
  draftId: string | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  refreshed: []
  select: [draftId: string]
}>()

const message = useMessage()

const loading = ref(false)
const loadError = ref('')
const actionBusy = ref(false)
const draft = ref<QzoneDraft | null>(null)
const audit = ref<QzoneDraftAuditResponse | null>(null)
const revisions = ref<QzoneDraftRevision[]>([])
const reviewNote = ref('')
const resolutionNote = ref('')
const externalPostId = ref('')
const operatorGuidance = ref('')
const dryRunResult = ref<QzoneDryRunDescriptor | null>(null)
const activeTab = ref<'context' | 'history'>('context')
let detailRequestGeneration = 0
let actionGeneration = 0

const drawerOpen = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value),
})

const status = computed(() => draft.value?.status ?? '')
const isPending = computed(() => status.value === 'pending_review')
const isRejected = computed(() => status.value === 'rejected')
const isApproved = computed(() => status.value === 'approved')
const isUnknown = computed(() => status.value === 'unknown')
/**
 * True when loaded lineage history proves the viewed row has no successor.
 * Fail-closed: empty revisions means not actionable (API always returns the
 * current row on normal load; [] is unexpected, not "presume tip").
 */
const isLineageTip = computed(() => {
  const id = draft.value?.draft_id || props.draftId
  if (!id) return false
  if (!revisions.value.length) {
    return false
  }
  return !revisions.value.some((item) => item.supersedes_draft_id === id)
})
const canRecompose = computed(
  () =>
    (status.value === 'pending_review' || status.value === 'rejected')
    && isLineageTip.value,
)
/** Dry-run only when approved and current lineage tip (fail-closed). */
const canDryRun = computed(() => isApproved.value && isLineageTip.value)
/** Manual resolve only when unknown and current lineage tip (fail-closed). */
const canResolve = computed(() => isUnknown.value && isLineageTip.value)
const isReadOnly = computed(() =>
  ['published', 'failed', 'dispatching'].includes(status.value),
)
const actionBoundary = computed(() => {
  if (!isLineageTip.value) {
    return {
      status: 'default' as const,
      icon: EyeOutline,
      title: '历史版本，只读',
      description: '该版本已有后继修订，仅用于核对正文、溯源与审计记录。',
    }
  }
  if (isPending.value) {
    return {
      status: 'warning' as const,
      icon: TimeOutline,
      title: '等待人工审核',
      description: '可通过、拒绝，或给出措辞指导后生成新的 append-only 修订。',
    }
  }
  if (isRejected.value) {
    return {
      status: 'error' as const,
      icon: AlertCircleOutline,
      title: '已拒绝，可重新修订',
      description: '原版本保持不可变；修订会生成新的队列 tip。',
    }
  }
  if (isApproved.value) {
    return {
      status: 'info' as const,
      icon: FlaskOutline,
      title: '审核已通过，可执行 Dry-run',
      description: '模拟只返回消毒后的线缆描述符，不会触发真实发布。',
    }
  }
  if (isUnknown.value) {
    return {
      status: 'warning' as const,
      icon: HelpCircleOutline,
      title: '投递状态未知，等待人工处置',
      description: '核实外部结果后，填写备注并确认已发布或未发布。',
    }
  }
  if (status.value === 'published') {
    return {
      status: 'success' as const,
      icon: CheckmarkCircleOutline,
      title: '发布结果已确认',
      description: '当前记录已闭环，仅可查看。',
    }
  }
  if (status.value === 'failed') {
    return {
      status: 'error' as const,
      icon: AlertCircleOutline,
      title: '投递失败，只读',
      description: '请结合错误码与审计记录核对失败原因。',
    }
  }
  return {
    status: 'info' as const,
    icon: TimeOutline,
    title: '状态处理中，只读',
    description: '当前状态不允许人工变更，请等待状态机完成。',
  }
})
const actionBoundaryClass = computed(
  () => `qzone-drawer__boundary--${actionBoundary.value.status}`,
)

const rejectEnabled = computed(() => reviewNote.value.trim().length > 0)
const resolutionEnabled = computed(() => resolutionNote.value.trim().length > 0)

const review = computed(() => draft.value?.review ?? null)

const provenanceRows = computed(() => {
  const provenance = review.value?.provenance
  if (!provenance || typeof provenance !== 'object') return [] as Array<{ key: string; value: string }>
  // Flatten top-level provenance; nest public_projection as a dedicated block below.
  return Object.entries(provenance)
    .filter(([key]) => key !== 'public_projection')
    .map(([key, value]) => ({
      key,
      value: formatValue(value),
    }))
})

/** Safe public projection metadata only (schema_version 2 factual drafts). */
const publicProjectionRows = computed(() => {
  const projection = review.value?.provenance?.public_projection
  if (!projection || typeof projection !== 'object') {
    return [] as Array<{ key: string; value: string }>
  }
  const preferred = [
    'schema_version',
    'policy_id',
    'public_template_id',
    'source_event_hash',
    'applied_claim_classes',
    'aliases',
  ] as const
  const rows: Array<{ key: string; value: string }> = []
  for (const key of preferred) {
    if (projection[key] !== undefined) {
      rows.push({ key, value: formatValue(projection[key]) })
    }
  }
  return rows
})

const dryRunRows = computed(() => {
  const result = dryRunResult.value
  if (!result) return [] as Array<{ key: string; value: string }>
  const preferred = [
    'profile_id',
    'validated',
    'method',
    'host',
    'path',
    'field_names',
    'content_chars',
    'content_sha256',
    'follow_redirects',
  ]
  return preferred
    .filter(key => result[key] !== undefined)
    .map(key => ({ key, value: formatValue(result[key]) }))
})

function formatValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value || '—'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

async function loadDetail() {
  const id = props.draftId
  if (!id || !props.show) return
  const requestGeneration = ++detailRequestGeneration
  loading.value = true
  loadError.value = ''
  try {
    const [detail, auditRes, revisionsRes] = await Promise.all([
      fetchQzoneDraft(id),
      fetchQzoneDraftAudit(id),
      fetchQzoneDraftRevisions(id),
    ])
    if (
      requestGeneration !== detailRequestGeneration
      || !props.show
      || props.draftId !== id
    ) return
    draft.value = detail
    audit.value = auditRes
    revisions.value = revisionsRes.revisions ?? []
  } catch (error) {
    if (
      requestGeneration !== detailRequestGeneration
      || !props.show
      || props.draftId !== id
    ) return
    loadError.value = extractApiError(error, '草稿详情加载失败')
    draft.value = null
    audit.value = null
    revisions.value = []
  } finally {
    if (requestGeneration === detailRequestGeneration) {
      loading.value = false
    }
  }
}

function isCurrentAction(actionDraftId: string, actionGen: number): boolean {
  return (
    props.show
    && props.draftId === actionDraftId
    && actionGen === actionGeneration
  )
}

async function afterActionSuccess(
  successText: string,
  actionDraftId: string,
  actionGen: number,
) {
  if (!isCurrentAction(actionDraftId, actionGen)) return
  message.success(successText)
  await loadDetail()
  if (!isCurrentAction(actionDraftId, actionGen)) return
  emit('refreshed')
}

async function onApprove() {
  if (!props.draftId || actionBusy.value || !isLineageTip.value || !isPending.value) {
    return
  }
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    await approveQzoneDraft(actionDraftId, reviewNote.value)
    if (!isCurrentAction(actionDraftId, actionGen)) return
    reviewNote.value = ''
    await afterActionSuccess('已通过审核', actionDraftId, actionGen)
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, '通过失败，请检查草稿状态后重试'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

async function onReject() {
  if (
    !props.draftId
    || actionBusy.value
    || !rejectEnabled.value
    || !isLineageTip.value
    || !isPending.value
  ) {
    return
  }
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    await rejectQzoneDraft(actionDraftId, reviewNote.value)
    if (!isCurrentAction(actionDraftId, actionGen)) return
    reviewNote.value = ''
    await afterActionSuccess('已拒绝草稿', actionDraftId, actionGen)
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, '拒绝失败，请填写审核备注后重试'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

async function onDryRun() {
  if (!props.draftId || actionBusy.value || !canDryRun.value) return
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    const result = await dryRunQzoneDraft(actionDraftId)
    if (!isCurrentAction(actionDraftId, actionGen)) return
    dryRunResult.value = result
    message.success('Dry-run 完成（仅描述符，未真实发布）')
    await loadDetail()
    if (!isCurrentAction(actionDraftId, actionGen)) return
    emit('refreshed')
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, 'Dry-run 失败，请确认草稿为已通过且门禁允许模拟'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

async function onConfirmPublished() {
  if (
    !props.draftId
    || actionBusy.value
    || !resolutionEnabled.value
    || !canResolve.value
  ) {
    return
  }
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    await confirmPublishedQzoneDraft(
      actionDraftId,
      resolutionNote.value,
      externalPostId.value,
    )
    if (!isCurrentAction(actionDraftId, actionGen)) return
    resolutionNote.value = ''
    externalPostId.value = ''
    await afterActionSuccess('已确认发布', actionDraftId, actionGen)
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, '确认发布失败，请填写处置备注后重试'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

async function onConfirmNotPublished() {
  if (
    !props.draftId
    || actionBusy.value
    || !resolutionEnabled.value
    || !canResolve.value
  ) {
    return
  }
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    await confirmNotPublishedQzoneDraft(actionDraftId, resolutionNote.value)
    if (!isCurrentAction(actionDraftId, actionGen)) return
    resolutionNote.value = ''
    externalPostId.value = ''
    await afterActionSuccess('已确认未发布', actionDraftId, actionGen)
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, '确认未发布失败，请填写处置备注后重试'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

async function onRecompose() {
  if (!props.draftId || actionBusy.value || !canRecompose.value) return
  const actionDraftId = props.draftId
  const actionGen = ++actionGeneration
  actionBusy.value = true
  try {
    const created = await recomposeQzoneDraft(actionDraftId, operatorGuidance.value)
    if (!isCurrentAction(actionDraftId, actionGen)) return
    operatorGuidance.value = ''
    const newDraftId = created.draft_id
    message.success('已修订并重新入队')
    // Parent owns selection; switch drawer to the new tip before refresh.
    emit('select', newDraftId)
    emit('refreshed')
  } catch (error) {
    if (!isCurrentAction(actionDraftId, actionGen)) return
    message.error(extractApiError(error, '修订失败，请确认状态后重试'))
  } finally {
    if (actionGen === actionGeneration) {
      actionBusy.value = false
    }
  }
}

watch(
  () => [props.show, props.draftId] as const,
  ([visible, id]) => {
    // Invalidate in-flight detail/action UI writes on switch or close.
    actionGeneration += 1
    actionBusy.value = false
    if (visible && id) {
      activeTab.value = 'context'
      reviewNote.value = ''
      resolutionNote.value = ''
      externalPostId.value = ''
      operatorGuidance.value = ''
      dryRunResult.value = null
      void loadDetail()
    }
    if (!visible) {
      detailRequestGeneration += 1
      loading.value = false
      draft.value = null
      audit.value = null
      revisions.value = []
      loadError.value = ''
      dryRunResult.value = null
      operatorGuidance.value = ''
    }
  },
)
</script>

<template>
  <NDrawer
    v-model:show="drawerOpen"
    width="min(560px, 100vw)"
    display-directive="show"
  >
    <NDrawerContent closable :native-scrollbar="false">
      <template #header>
        <AppDrawerHeader
          eyebrow="Draft Review"
          :title="draft?.draft_id || draftId || '草稿详情'"
          :description="draft ? `来源 ${draft.source} · 事件日 ${draft.event_date}` : '加载草稿与审计记录'"
        >
          <template v-if="draft" #aside>
            <StateBadge
              :status="statusBadge(draft.status)"
              :label="statusLabel(draft.status)"
            />
          </template>
        </AppDrawerHeader>
      </template>

      <NSkeleton v-if="loading" :repeat="8" text />

      <EmptyState
        v-else-if="loadError"
        title="详情加载失败"
        :description="loadError"
        compact
      >
        <NButton type="primary" secondary @click="loadDetail">
          重试
        </NButton>
      </EmptyState>

      <AppDrawerLayout v-else-if="draft" class="qzone-drawer">
        <div
          class="qzone-drawer__boundary"
          :class="actionBoundaryClass"
          role="status"
          aria-live="polite"
        >
          <span class="qzone-drawer__boundary-icon" aria-hidden="true">
            <NIcon :component="actionBoundary.icon" :size="20" />
          </span>
          <span class="qzone-drawer__boundary-copy">
            <small>当前动作边界</small>
            <strong>{{ actionBoundary.title }}</strong>
            <span>{{ actionBoundary.description }}</span>
          </span>
          <StateBadge
            :status="actionBoundary.status"
            :label="isLineageTip ? '当前版本' : '历史版本'"
            compact
          />
        </div>

        <NTabs v-model:value="activeTab" type="line" class="qzone-drawer__tabs">
          <NTabPane name="context" tab="草稿与来源">
            <DraftContextPanels
              :draft="draft"
              :review="review"
              :provenance-rows="provenanceRows"
              :public-projection-rows="publicProjectionRows"
              :dry-run-rows="dryRunRows"
            />
          </NTabPane>

          <NTabPane name="history" tab="版本与审计">
            <DraftHistoryPanels :revisions="revisions" :audit="audit" />
          </NTabPane>
        </NTabs>

        <template #footer>
          <div class="qzone-drawer__footer">
            <div class="qzone-drawer__footer-copy">
              <strong>{{ actionBoundary.title }}</strong>
              <span>{{ actionBoundary.description }}</span>
            </div>

            <!-- Actions only on lineage tip; historical supersedes keep immutable status. -->
            <template v-if="isPending && isLineageTip">
            <div class="qzone-drawer__actions-block">
              <label class="qzone-drawer__field">
                <span>审核备注（拒绝时必填）</span>
                <NInput
                  v-model:value="reviewNote"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                  maxlength="500"
                  show-count
                  placeholder="可选：通过备注；拒绝时请说明原因"
                  :disabled="actionBusy"
                  aria-label="审核备注"
                />
              </label>
              <div class="qzone-drawer__footer-row">
                <NButton
                  type="error"
                  secondary
                  :disabled="!rejectEnabled || actionBusy"
                  :loading="actionBusy"
                  @click="onReject"
                >
                  拒绝
                </NButton>
                <NButton
                  type="primary"
                  :disabled="actionBusy"
                  :loading="actionBusy"
                  @click="onApprove"
                >
                  通过
                </NButton>
              </div>
              <label class="qzone-drawer__field">
                <span>修订指导（可选，仅措辞，不作事实来源）</span>
                <NInput
                  v-model:value="operatorGuidance"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 3 }"
                  maxlength="500"
                  show-count
                  placeholder="可选：提示语气或删减方向，不会写入 provenance"
                  :disabled="actionBusy"
                  aria-label="修订指导"
                />
              </label>
              <div class="qzone-drawer__footer-row">
                <NButton
                  secondary
                  :disabled="actionBusy || !canRecompose"
                  :loading="actionBusy"
                  @click="onRecompose"
                >
                  修订并重新入队
                </NButton>
              </div>
            </div>
            </template>

            <template v-else-if="isRejected && isLineageTip">
            <div class="qzone-drawer__actions-block">
              <label class="qzone-drawer__field">
                <span>修订指导（可选，仅措辞，不作事实来源）</span>
                <NInput
                  v-model:value="operatorGuidance"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 3 }"
                  maxlength="500"
                  show-count
                  placeholder="可选：提示语气或删减方向，不会写入 provenance"
                  :disabled="actionBusy"
                  aria-label="修订指导"
                />
              </label>
              <div class="qzone-drawer__footer-row">
                <NButton
                  type="primary"
                  :disabled="actionBusy || !canRecompose"
                  :loading="actionBusy"
                  @click="onRecompose"
                >
                  修订并重新入队
                </NButton>
              </div>
            </div>
            </template>

            <template v-else-if="canDryRun">
              <div class="qzone-drawer__footer-row">
                <NButton
                  type="primary"
                  :disabled="actionBusy || !canDryRun"
                  :loading="actionBusy"
                  @click="onDryRun"
                >
                  Dry-run 模拟
                </NButton>
              </div>
            </template>

            <template v-else-if="canResolve">
            <div class="qzone-drawer__actions-block">
              <label class="qzone-drawer__field">
                <span>处置备注（必填）</span>
                <NInput
                  v-model:value="resolutionNote"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                  maxlength="500"
                  show-count
                  placeholder="说明为何确认已发布或未发布"
                  :disabled="actionBusy"
                  aria-label="处置备注"
                />
              </label>
              <label class="qzone-drawer__field">
                <span>外部帖子 ID（可选）</span>
                <NInput
                  v-model:value="externalPostId"
                  maxlength="120"
                  placeholder="仅确认已发布时可选填写"
                  :disabled="actionBusy"
                  aria-label="外部帖子 ID"
                />
              </label>
              <div class="qzone-drawer__footer-row">
                <NButton
                  secondary
                  :disabled="!resolutionEnabled || actionBusy || !canResolve"
                  :loading="actionBusy"
                  @click="onConfirmNotPublished"
                >
                  确认未发布
                </NButton>
                <NButton
                  type="primary"
                  :disabled="!resolutionEnabled || actionBusy || !canResolve"
                  :loading="actionBusy"
                  @click="onConfirmPublished"
                >
                  确认已发布
                </NButton>
              </div>
            </div>
            </template>

            <template v-else>
              <p class="qzone-drawer__readonly">
                当前状态为「{{ statusLabel(status) }}」，仅可查看，不可变更。
                <template v-if="!isLineageTip">
                  请切换到版本历史中的最新 tip 继续操作。
                </template>
                <template v-else-if="isReadOnly">
                  所有状态变更均由既有状态机负责。
                </template>
              </p>
            </template>
          </div>
        </template>
      </AppDrawerLayout>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.qzone-drawer {
  gap: 16px;
}

.qzone-drawer__boundary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.qzone-drawer__boundary-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid currentColor;
  border-radius: 8px;
  background: var(--om-surface);
  color: var(--om-info);
}

.qzone-drawer__boundary-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.qzone-drawer__boundary-copy small {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.qzone-drawer__boundary-copy strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.qzone-drawer__boundary-copy > span {
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.qzone-drawer__boundary--success {
  border-color: color-mix(in srgb, var(--om-success) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-success) 8%, var(--om-surface-2));
}

.qzone-drawer__boundary--success .qzone-drawer__boundary-icon {
  color: var(--om-success);
}

.qzone-drawer__boundary--warning {
  border-color: color-mix(in srgb, var(--om-warning) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 8%, var(--om-surface-2));
}

.qzone-drawer__boundary--warning .qzone-drawer__boundary-icon {
  color: var(--om-warning);
}

.qzone-drawer__boundary--error {
  border-color: color-mix(in srgb, var(--om-danger) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 8%, var(--om-surface-2));
}

.qzone-drawer__boundary--error .qzone-drawer__boundary-icon {
  color: var(--om-danger);
}

.qzone-drawer__boundary--info .qzone-drawer__boundary-icon {
  color: var(--om-info);
}

.qzone-drawer__tabs {
  min-width: 0;
}

.qzone-drawer__actions-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.qzone-drawer__footer {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.qzone-drawer__footer-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.qzone-drawer__footer-copy strong {
  color: var(--om-text-1);
  font-size: 13px;
}

.qzone-drawer__footer-copy span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
}

.qzone-drawer__field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
}

.qzone-drawer__footer-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.qzone-drawer__readonly {
  margin: 0;
  width: 100%;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.55;
  text-align: left;
}

@media (max-width: 640px) {
  .qzone-drawer__boundary {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .qzone-drawer__boundary > :last-child {
    grid-column: 2;
  }

  .qzone-drawer__footer-row {
    flex-direction: column;
  }

  .qzone-drawer__footer-row :deep(.n-button) {
    width: 100%;
  }
}
</style>

<script setup lang="ts">
import { useMessage } from 'naive-ui'

import {
  fetchToolApprovalContext,
  approveToolCall,
  fetchReconciliationContext,
  reconcileToolCall,
  fetchMemoryCandidateContext,
  decideMemoryCandidate,
  type AgentRuntimeOperatorCredentials,
  type DecisionContext,
  type DecisionReceipt,
} from '../../../api/agentRuntime'

type GovernanceKind = 'approval' | 'reconciliation' | 'memory'

const props = defineProps<{
  show: boolean
  kind: GovernanceKind
  resourceId: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  refresh: []
}>()

const message = useMessage()
const context = ref<DecisionContext | null>(null)
const loading = ref(false)
const submitting = ref(false)
const actionAvailability = ref<'available' | 'actions_unavailable'>('available')
const staleContext = ref(false)
const operatorId = ref('')
const operatorCredential = ref('')
const operatorAuthenticationFailed = ref(false)
const contextEpoch = ref(0)

const form = reactive({
  approvalRef: '',
  reconciliationDecision: 'confirmed_succeeded' as 'confirmed_succeeded' | 'confirmed_not_applied',
  evidenceRef: '',
  operatorNote: '',
  externalId: '',
  memoryDecision: 'reject' as 'approve' | 'reject',
  reasonCode: '',
})

const readOnly = computed(() => actionAvailability.value === 'actions_unavailable')
const hasOperatorCredentials = computed(() => (
  operatorId.value.trim().length > 0 && operatorCredential.value.length > 0
))
const title = computed(() => ({
  approval: '记录工具批准',
  reconciliation: '核对外部结果',
  memory: '裁决记忆候选',
})[props.kind])
const previewEntries = computed(() => (
  Object.entries(context.value?.preview ?? {}).filter(([key]) => key !== 'conflict_ids')
))
const previewConflictIds = computed(() => context.value?.preview?.conflict_ids ?? [])
const canSubmit = computed(() => {
  if (!context.value || !hasOperatorCredentials.value || readOnly.value || submitting.value) return false
  if (props.kind === 'approval') return form.approvalRef.trim().length > 0
  if (props.kind === 'reconciliation') {
    return form.evidenceRef.trim().length > 0 && form.operatorNote.trim().length > 0
  }
  return form.reasonCode.trim().length > 0
})

function close() {
  emit('update:show', false)
}

function clearDecisionContext() {
  context.value = null
}

function currentOperatorCredentials(): AgentRuntimeOperatorCredentials | null {
  if (!hasOperatorCredentials.value) return null
  return {
    operatorId: operatorId.value.trim(),
    credential: operatorCredential.value,
  }
}

function resetForm() {
  form.approvalRef = ''
  form.reconciliationDecision = 'confirmed_succeeded'
  form.evidenceRef = ''
  form.operatorNote = ''
  form.externalId = ''
  form.memoryDecision = 'reject'
  form.reasonCode = ''
  operatorId.value = ''
  operatorCredential.value = ''
  operatorAuthenticationFailed.value = false
  context.value = null
  staleContext.value = false
  actionAvailability.value = 'available'
}

function statusOf(error: unknown) {
  const candidate = error as {
    status?: number
    statusCode?: number
    response?: { status?: number }
  }
  return Number(candidate?.response?.status ?? candidate?.statusCode ?? candidate?.status ?? 0)
}

async function loadContext() {
  if (!props.resourceId) return
  const credentials = currentOperatorCredentials()
  if (!credentials) {
    clearDecisionContext()
    return
  }
  const epoch = contextEpoch.value
  loading.value = true
  operatorAuthenticationFailed.value = false
  try {
    const loaded = await fetchDecisionContext(credentials)
    if (epoch !== contextEpoch.value) return
    context.value = loaded
    actionAvailability.value = 'available'
  }
  catch (error) {
    if (epoch !== contextEpoch.value) return
    clearDecisionContext()
    if (statusOf(error) === 503) {
      actionAvailability.value = 'actions_unavailable'
      message.warning('操作来源不可用，当前已切换为只读模式')
    }
    else if (statusOf(error) === 401) {
      operatorAuthenticationFailed.value = true
      message.error('操作员凭据无效或无法访问该资源')
    }
    else {
      message.error('裁决上下文加载失败')
    }
  }
  finally {
    if (epoch === contextEpoch.value) loading.value = false
  }
}

async function refreshAfterDecision(receipt: DecisionReceipt) {
  if (receipt.exact_retry) message.info('该裁决已经记录，无需重复提交')
  else message.success('裁决已记录')
  emit('refresh')
  close()
}

async function submitApproval(credentials: AgentRuntimeOperatorCredentials) {
  if (!context.value) return
  const receipt = await approveToolCall(props.resourceId, {
    expected_token: context.value.expected_token,
    approval_ref: form.approvalRef.trim(),
  }, credentials)
  await refreshAfterDecision(receipt)
}

async function submitReconciliation(credentials: AgentRuntimeOperatorCredentials) {
  if (!context.value) return
  const receipt = await reconcileToolCall(props.resourceId, {
    expected_token: context.value.expected_token,
    decision: form.reconciliationDecision,
    evidence_ref: form.evidenceRef.trim(),
    operator_note: form.operatorNote.trim(),
    external_id: form.externalId.trim() || null,
  }, credentials)
  await refreshAfterDecision(receipt)
}

async function submitMemoryDecision(credentials: AgentRuntimeOperatorCredentials) {
  if (!context.value) return
  const receipt = await decideMemoryCandidate(props.resourceId, {
    expected_token: context.value.expected_token,
    decision: form.memoryDecision,
    reason_code: form.reasonCode.trim(),
    operator_note: form.operatorNote.trim() || null,
    occurred_at: new Date().toISOString(),
  }, credentials)
  await refreshAfterDecision(receipt)
}

async function submit() {
  const credentials = currentOperatorCredentials()
  if (!canSubmit.value || !credentials) return
  submitting.value = true
  staleContext.value = false
  try {
    if (props.kind === 'approval') await submitApproval(credentials)
    else if (props.kind === 'reconciliation') await submitReconciliation(credentials)
    else await submitMemoryDecision(credentials)
  }
  catch (error) {
    const status = statusOf(error)
    if (status === 409) {
      staleContext.value = true
      message.warning('裁决上下文已过期，正在重新获取')
      await loadContext()
    }
    else if (status === 503) {
      actionAvailability.value = 'actions_unavailable'
      message.warning('操作来源不可用，当前已切换为只读模式')
    }
    else if (status === 401) {
      clearDecisionContext()
      operatorAuthenticationFailed.value = true
      message.error('操作员凭据无效或无法访问该资源')
    }
    else {
      message.error('裁决记录失败')
    }
  }
  finally {
    submitting.value = false
  }
}

async function fetchDecisionContext(
  credentials: AgentRuntimeOperatorCredentials,
): Promise<DecisionContext> {
  if (props.kind === 'approval') {
    return fetchToolApprovalContext(props.resourceId, credentials)
  }
  if (props.kind === 'reconciliation') {
    return fetchReconciliationContext(props.resourceId, credentials)
  }
  return fetchMemoryCandidateContext(props.resourceId, credentials)
}

watch(
  [operatorId, operatorCredential],
  () => {
    contextEpoch.value += 1
    clearDecisionContext()
    staleContext.value = false
    actionAvailability.value = 'available'
    operatorAuthenticationFailed.value = false
    loading.value = false
  },
)

watch(
  () => [props.show, props.kind, props.resourceId] as const,
  ([show]) => {
    if (!show) return
    contextEpoch.value += 1
    resetForm()
    void loadContext()
  },
)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    class="governance-modal"
    :title="title"
    :mask-closable="!submitting"
    @update:show="emit('update:show', $event)"
  >
    <NSpin :show="loading">
      <NForm label-placement="top" class="governance-credentials">
        <NFormItem label="操作员 ID" required>
          <NInput
            v-model:value="operatorId"
            autocomplete="off"
            :disabled="submitting"
          />
        </NFormItem>
        <NFormItem label="操作凭据" required>
          <NInput
            v-model:value="operatorCredential"
            type="password"
            autocomplete="off"
            :disabled="submitting"
          />
        </NFormItem>
        <div class="governance-credential-actions">
          <NButton
            type="primary"
            :disabled="!hasOperatorCredentials || submitting"
            @click="loadContext"
          >
            加载裁决上下文
          </NButton>
        </div>
      </NForm>

      <NAlert
        v-if="operatorAuthenticationFailed"
        type="error"
        :show-icon="true"
        class="governance-alert"
      >
        操作员凭据无效或无法访问该资源。
      </NAlert>
      <NAlert v-if="readOnly" type="warning" :show-icon="true" class="governance-alert">
        当前操作服务未挂载，本页仅保留只读预览。
      </NAlert>
      <NAlert v-if="staleContext" type="warning" :show-icon="true" class="governance-alert">
        上下文已失效，已重新获取最新状态，请再次核对。
      </NAlert>

      <AppPanelSection
        v-if="context"
        title="Server-owned context"
        :description="`${context.resource.kind} · ${context.state}`"
      >
        <dl class="governance-preview">
          <div v-for="([key, value]) in previewEntries" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ Array.isArray(value) ? value.join(', ') : value }}</dd>
          </div>
          <div v-if="kind === 'memory'">
            <dt>conflict_ids</dt>
            <dd>{{ previewConflictIds.length ? previewConflictIds.join(', ') : '无' }}</dd>
          </div>
        </dl>
        <p class="governance-digest">{{ context.preview_digest }}</p>
      </AppPanelSection>

      <NForm v-if="context" :model="form" label-placement="top" class="governance-form">
        <template v-if="kind === 'approval'">
          <NFormItem label="批准凭据引用" required>
            <NInput v-model:value="form.approvalRef" maxlength="240" :disabled="readOnly" />
          </NFormItem>
        </template>

        <template v-else-if="kind === 'reconciliation'">
          <NFormItem label="核对结果" required>
            <NSelect
              v-model:value="form.reconciliationDecision"
              :disabled="readOnly"
              :options="[
                { label: '确认已生效', value: 'confirmed_succeeded' },
                { label: '确认未生效', value: 'confirmed_not_applied' },
              ]"
            />
          </NFormItem>
          <NFormItem label="外部证据引用" required>
            <NInput v-model:value="form.evidenceRef" maxlength="240" :disabled="readOnly" />
          </NFormItem>
          <NFormItem label="核对说明" required>
            <NInput
              v-model:value="form.operatorNote"
              type="textarea"
              maxlength="500"
              :disabled="readOnly"
            />
          </NFormItem>
          <NFormItem label="外部记录 ID">
            <NInput v-model:value="form.externalId" maxlength="240" :disabled="readOnly" />
          </NFormItem>
        </template>

        <template v-else>
          <NFormItem label="裁决" required>
            <NSelect
              v-model:value="form.memoryDecision"
              :disabled="readOnly"
              :options="[
                { label: '批准', value: 'approve' },
                { label: '拒绝', value: 'reject' },
              ]"
            />
          </NFormItem>
          <NFormItem label="原因代码" required>
            <NInput v-model:value="form.reasonCode" maxlength="120" :disabled="readOnly" />
          </NFormItem>
          <NFormItem label="裁决说明">
            <NInput
              v-model:value="form.operatorNote"
              type="textarea"
              maxlength="500"
              :disabled="readOnly"
            />
          </NFormItem>
        </template>
      </NForm>
    </NSpin>

    <template #footer>
      <div class="governance-footer">
        <NButton @click="close">取消</NButton>
        <NButton type="primary" :disabled="!canSubmit" :loading="submitting" @click="submit">
          确认记录
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
:global(.governance-modal) {
  display: flex;
  max-height: calc(100vh - 32px);
  flex-direction: column;
  width: min(640px, calc(100vw - 32px));
}

:global(.governance-modal .n-card-content) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.governance-alert + .governance-alert,
.governance-form,
.governance-alert,
.governance-credential-actions {
  margin-top: 16px;
}

.governance-credential-actions {
  display: flex;
  justify-content: flex-end;
}

.governance-preview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.governance-preview > div {
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.governance-preview dt {
  color: var(--om-text-3);
  font-size: 12px;
}

.governance-preview dd {
  margin: 4px 0 0;
  color: var(--om-text-1);
  overflow-wrap: anywhere;
}

.governance-digest {
  margin: 16px 0 0;
  color: var(--om-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.governance-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 640px) {
  .governance-preview {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

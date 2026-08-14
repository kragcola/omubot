<script setup lang="ts">
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  EyeOutline,
  LayersOutline,
  PulseOutline,
  RefreshOutline,
  ShieldCheckmarkOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { NButton, NIcon, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useWindowSize } from '@vueuse/core'

import {
  fetchActivationReadiness,
  fetchDarkReadiness,
  fetchMemoryCandidate,
  fetchMemoryCandidates,
  fetchMemoryConflicts,
  fetchMemoryObservations,
  fetchMemorySummary,
  fetchRollbackReadiness,
  fetchRuntimeEvents,
  fetchRuntimeRun,
  fetchRuntimeRuns,
  fetchRuntimeSummary,
  fetchRuntimeToolCalls,
  classifyReadinessGate,
  type ActivationReadiness,
  type DarkReadiness,
  type MemoryCandidate,
  type MemoryConflict,
  type MemoryObservation,
  type MemorySummary,
  type RollbackReadiness,
  type RuntimeEvent,
  type RuntimeRun,
  type RuntimeSummary,
  type RuntimeToolCall,
} from '../../api/agentRuntime'
import GovernanceDecisionDrawer from './components/GovernanceDecisionDrawer.vue'
import WorldbookGovernancePanel from './components/WorldbookGovernancePanel.vue'

type GovernanceKind = 'approval' | 'reconciliation' | 'memory'

const message = useMessage()
const { width: viewportWidth } = useWindowSize()
const runDrawerWidth = computed(() => Math.min(640, viewportWidth.value))
const candidateDrawerWidth = computed(() => Math.min(560, viewportWidth.value))
const activeTab = ref('runtime')
const loading = ref(false)
const lastError = ref('')
const refreshedAt = ref('')

const runtimeSummary = ref<RuntimeSummary | null>(null)
const memorySummary = ref<MemorySummary | null>(null)
const darkReadiness = ref<DarkReadiness | null>(null)
const activationReadiness = ref<ActivationReadiness | null>(null)
const rollbackReadiness = ref<RollbackReadiness | null>(null)

const runs = ref<RuntimeRun[]>([])
const runStatus = ref<string | null>(null)
const runNextCursor = ref<string | null>(null)
const toolCalls = ref<RuntimeToolCall[]>([])

const observations = ref<MemoryObservation[]>([])
const candidates = ref<MemoryCandidate[]>([])
const conflicts = ref<MemoryConflict[]>([])
const candidateProjection = ref<string | null>(null)
const candidateOperation = ref<string | null>(null)
const candidateNextCursor = ref<string | null>(null)

const runDrawerOpen = ref(false)
const selectedRun = ref<RuntimeRun | null>(null)
const selectedRunTools = ref<RuntimeToolCall[]>([])
const selectedRunEvents = ref<RuntimeEvent[]>([])
const runDetailLoading = ref(false)

const candidateDrawerOpen = ref(false)
const selectedCandidate = ref<MemoryCandidate | null>(null)
const candidateDetailLoading = ref(false)

const governanceOpen = ref(false)
const governanceKind = ref<GovernanceKind>('approval')
const governanceResourceId = ref('')

const runStatusOptions = [
  'queued',
  'running',
  'waiting_approval',
  'waiting_retry',
  'waiting_external',
  'succeeded',
  'failed',
  'cancelled',
].map(value => ({ label: statusLabel(value), value }))

const projectionOptions = ['card', 'slang', 'style', 'episode', 'graph_relation']
  .map(value => ({ label: projectionLabel(value), value }))
const operationOptions = ['create', 'reinforce', 'supersede', 'expire']
  .map(value => ({ label: operationLabel(value), value }))

const runtimeAvailable = computed(() => runtimeSummary.value?.available === true)
const memoryAvailable = computed(() => memorySummary.value?.available === true)
const darkReady = computed(() => darkReadiness.value?.status === 'ready')
const blockers = computed(() => activationReadiness.value?.blocking_gates ?? [])
const activationGateEntries = computed(() => Object.entries(activationReadiness.value?.gates ?? {}))
const rollbackGateEntries = computed(() => Object.entries(rollbackReadiness.value?.gates ?? {}))
const approvalCall = computed(() => selectedRunTools.value.find(call => call.status === 'approval_pending') ?? null)
const reconciliationCall = computed(() => selectedRunTools.value.find(call => call.status === 'unknown') ?? null)

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    waiting_approval: '待批准',
    waiting_retry: '待重试',
    waiting_external: '待外部确认',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    proposed: '已提议',
    denied: '已拒绝',
    approval_pending: '待批准',
    ready: '就绪',
    claimed: '已领取',
    dispatching: '派发中',
    failed_retryable: '可重试失败',
    failed_terminal: '终止失败',
    unknown: '结果未知',
    unreviewed: '未评审',
    review_queued: '待评审',
    approved: '已批准',
    rejected: '已拒绝',
  }
  return labels[value] ?? (value || '未知')
}

function projectionLabel(value: string) {
  const labels: Record<string, string> = {
    card: '记忆卡片',
    slang: '黑话',
    style: '措辞风格',
    episode: '情节',
    graph_relation: '关系图谱',
  }
  return labels[value] ?? value
}

function operationLabel(value: string) {
  const labels: Record<string, string> = {
    create: '新建',
    reinforce: '强化',
    supersede: '替代',
    expire: '过期',
  }
  return labels[value] ?? value
}

function statusType(value: string): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (['succeeded', 'approved', 'ready'].includes(value)) return 'success'
  if (['failed', 'failed_terminal', 'rejected', 'cancelled'].includes(value)) return 'error'
  if (['running', 'claimed', 'dispatching'].includes(value)) return 'info'
  if (value.startsWith('waiting_') || ['approval_pending', 'unknown', 'review_queued'].includes(value)) return 'warning'
  return 'default'
}

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function reasonLabel(reason: string | undefined) {
  if (!reason) return '未挂载'
  if (reason === 'source_unavailable') return '来源未挂载'
  if (reason === 'query_failed') return '完整性查询失败'
  return reason.startsWith('query_failed:') ? '查询失败' : reason
}

function sourceLabel(name: 'runtime' | 'memory' | 'worldbook') {
  return ({
    runtime: 'Runtime ledger',
    memory: 'Memory governance',
    worldbook: 'Worldbook governance',
  })[name]
}

function gateLabel(name: string) {
  return name.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ')
}

function gateReason(status: string, reason: string) {
  if (status === 'not_assessed') return '未评估'
  if (reason === 'unknown') return '没有 attestation evidence'
  if (reason === 'attestation_failed') return '证明来源失败'
  if (reason === 'attestation_invalid') return '证明格式无效'
  return reason || '未提供原因'
}

function renderStatus(value: string) {
  return h(NTag, { size: 'small', type: statusType(value), bordered: false }, () => statusLabel(value))
}

const runColumns: DataTableColumns<RuntimeRun> = [
  { title: 'Run', key: 'run_id', minWidth: 210, ellipsis: { tooltip: true } },
  { title: '触发', key: 'trigger_type', width: 110 },
  { title: '状态', key: 'status', width: 120, render: row => renderStatus(row.status) },
  { title: '注册代次', key: 'registry_generation', width: 100 },
  { title: '更新时间', key: 'updated_at', width: 180, render: row => formatTime(row.updated_at) },
  {
    title: '',
    key: 'actions',
    width: 64,
    render(row) {
      return h(
        NButton,
        {
          quaternary: true,
          circle: true,
          class: 'table-icon-button',
          'aria-label': `查看运行 ${row.run_id}`,
          onClick: () => inspectRun(row.run_id),
        },
        { icon: () => h(NIcon, { component: EyeOutline }) },
      )
    },
  },
]

const toolColumns: DataTableColumns<RuntimeToolCall> = [
  { title: 'Tool', key: 'tool_name', minWidth: 170, ellipsis: { tooltip: true } },
  { title: '效果', key: 'effect', width: 150, ellipsis: { tooltip: true } },
  { title: '状态', key: 'status', width: 120, render: row => renderStatus(row.status) },
  { title: '尝试', key: 'attempt_count', width: 72 },
  { title: '更新时间', key: 'updated_at', width: 180, render: row => formatTime(row.updated_at) },
]

const candidateColumns: DataTableColumns<MemoryCandidate> = [
  { title: 'Candidate', key: 'candidate_id', minWidth: 210, ellipsis: { tooltip: true } },
  { title: '投影', key: 'projection_kind', width: 120, render: row => projectionLabel(row.projection_kind) },
  { title: '操作', key: 'operation', width: 90, render: row => operationLabel(row.operation) },
  { title: '状态', key: 'fold_status', width: 110, render: row => renderStatus(row.fold_status) },
  { title: '冲突', key: 'conflict_count', width: 72 },
  { title: '生成时间', key: 'produced_at', width: 180, render: row => formatTime(row.produced_at) },
  {
    title: '',
    key: 'actions',
    width: 64,
    render(row) {
      return h(
        NButton,
        {
          quaternary: true,
          circle: true,
          class: 'table-icon-button',
          'aria-label': `查看候选 ${row.candidate_id}`,
          onClick: () => inspectCandidate(row.candidate_id),
        },
        { icon: () => h(NIcon, { component: EyeOutline }) },
      )
    },
  },
]

const observationColumns: DataTableColumns<MemoryObservation> = [
  { title: 'Observation', key: 'observation_id', minWidth: 210, ellipsis: { tooltip: true } },
  { title: '来源', key: 'source_kind', width: 140 },
  { title: '生产器', key: 'producer_kind', width: 110 },
  { title: '证据数', key: 'evidence_count', width: 80 },
  { title: '观测时间', key: 'observed_at', width: 180, render: row => formatTime(row.observed_at) },
]

const conflictColumns: DataTableColumns<MemoryConflict> = [
  { title: 'Conflict', key: 'conflict_id', minWidth: 210, ellipsis: { tooltip: true } },
  { title: '类型', key: 'kind', width: 160 },
  { title: '观测', key: 'observation_count', width: 72 },
  { title: '候选', key: 'candidate_count', width: 72 },
  { title: '发现时间', key: 'detected_at', width: 180, render: row => formatTime(row.detected_at) },
]

async function loadRuns(append = false) {
  const page = await fetchRuntimeRuns({
    limit: 20,
    cursor: append ? runNextCursor.value ?? undefined : undefined,
    status: runStatus.value ?? undefined,
  })
  runs.value = append ? [...runs.value, ...page.items] : page.items
  runNextCursor.value = page.next_cursor
}

async function loadCandidates(append = false) {
  const page = await fetchMemoryCandidates({
    limit: 20,
    cursor: append ? candidateNextCursor.value ?? undefined : undefined,
    projection_kind: candidateProjection.value ?? undefined,
    operation: candidateOperation.value ?? undefined,
  })
  candidates.value = append ? [...candidates.value, ...page.items] : page.items
  candidateNextCursor.value = page.next_cursor
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  lastError.value = ''
  try {
    const [
      runtimeValue,
      memoryValue,
      darkValue,
      activationValue,
      rollbackValue,
      toolsValue,
      observationsValue,
      conflictsValue,
    ] = await Promise.all([
      fetchRuntimeSummary(),
      fetchMemorySummary(),
      fetchDarkReadiness(),
      fetchActivationReadiness(),
      fetchRollbackReadiness(),
      fetchRuntimeToolCalls({ limit: 20 }),
      fetchMemoryObservations({ limit: 20 }),
      fetchMemoryConflicts({ limit: 20 }),
      loadRuns(),
      loadCandidates(),
    ])
    runtimeSummary.value = runtimeValue
    memorySummary.value = memoryValue
    darkReadiness.value = darkValue
    activationReadiness.value = activationValue
    rollbackReadiness.value = rollbackValue
    toolCalls.value = toolsValue.items
    observations.value = observationsValue.items
    conflicts.value = conflictsValue.items
    refreshedAt.value = new Date().toISOString()
  }
  catch (error) {
    lastError.value = error instanceof Error ? error.message : '请求失败'
    message.error('治理状态刷新失败')
  }
  finally {
    loading.value = false
  }
}

async function inspectRun(runId: string) {
  runDrawerOpen.value = true
  runDetailLoading.value = true
  try {
    const [detail, tools, events] = await Promise.all([
      fetchRuntimeRun(runId),
      fetchRuntimeToolCalls({ limit: 100, run_id: runId }),
      fetchRuntimeEvents(runId, { limit: 100 }),
    ])
    selectedRun.value = detail.item
    selectedRunTools.value = tools.items
    selectedRunEvents.value = events.items
  }
  catch {
    message.error('运行详情加载失败')
  }
  finally {
    runDetailLoading.value = false
  }
}

async function inspectCandidate(candidateId: string) {
  candidateDrawerOpen.value = true
  candidateDetailLoading.value = true
  try {
    selectedCandidate.value = (await fetchMemoryCandidate(candidateId)).item
  }
  catch {
    message.error('候选详情加载失败')
  }
  finally {
    candidateDetailLoading.value = false
  }
}

function openGovernance(kind: GovernanceKind, resourceId: string | undefined) {
  if (!resourceId) return
  runDrawerOpen.value = false
  candidateDrawerOpen.value = false
  governanceKind.value = kind
  governanceResourceId.value = resourceId
  governanceOpen.value = true
}

async function refreshGovernance() {
  await refresh()
  if (runDrawerOpen.value && selectedRun.value) await inspectRun(selectedRun.value.run_id)
  if (candidateDrawerOpen.value && selectedCandidate.value) {
    await inspectCandidate(selectedCandidate.value.candidate_id)
  }
}

watch(runStatus, () => { void loadRuns() })
watch([candidateProjection, candidateOperation], () => { void loadCandidates() })
onMounted(() => { void refresh() })
</script>

<template>
  <AppPage
    title="Agent Runtime"
    description="暗态运行、记忆治理与上线门槛"
    eyebrow="Runtime Governance"
  >
    <template #title-suffix>
      <NTag :type="darkReady ? 'success' : 'warning'" :bordered="false">
        {{ darkReady ? '暗态就绪' : '未就绪' }}
      </NTag>
    </template>
    <template #action>
      <NTooltip>
        <template #trigger>
          <NButton
            class="runtime-refresh-button"
            circle
            quaternary
            :loading="loading"
            aria-label="刷新治理状态"
            @click="refresh"
          >
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
          </NButton>
        </template>
        刷新
      </NTooltip>
    </template>

    <div class="runtime-metrics">
      <MetricCard
        title="Runtime Runs"
        :value="runtimeSummary?.counts?.runs ?? '—'"
        :hint="runtimeAvailable ? `事件 ${runtimeSummary?.counts?.events ?? 0}` : reasonLabel(runtimeSummary?.reason)"
        :icon="PulseOutline"
        :accent="runtimeAvailable ? 'success' : 'warning'"
      />
      <MetricCard
        title="Tool Calls"
        :value="runtimeSummary?.counts?.tool_calls ?? '—'"
        hint="受治理调用记录"
        :icon="ShieldCheckmarkOutline"
        accent="info"
      />
      <MetricCard
        title="Memory Candidates"
        :value="memorySummary?.candidate_count ?? '—'"
        :hint="memoryAvailable ? `冲突 ${memorySummary?.conflict_count ?? 0}` : reasonLabel(memorySummary?.reason)"
        :icon="LayersOutline"
        :accent="memoryAvailable ? 'primary' : 'warning'"
      />
      <MetricCard
        title="Activation Gates"
        :value="blockers.length"
        hint="生产激活仍未授权"
        :icon="AlertCircleOutline"
        accent="warning"
      />
    </div>

    <p v-if="refreshedAt" class="runtime-refreshed" aria-live="polite">
      最近刷新 {{ formatTime(refreshedAt) }}
    </p>
    <NAlert v-if="lastError" type="error" :show-icon="true" class="runtime-alert">
      {{ lastError }}
    </NAlert>

    <NTabs v-model:value="activeTab" type="segment" :animated="false" class="runtime-tabs">
      <NTabPane name="runtime" tab="运行记录">
        <PageToolbar>
          <template #left>
            <NSelect
              v-model:value="runStatus"
              class="runtime-filter"
              :options="runStatusOptions"
              clearable
              placeholder="全部状态"
              aria-label="筛选运行状态"
            />
          </template>
        </PageToolbar>

        <AppPanelSection class="runtime-section" title="Runs" description="受治理运行快照">
          <template v-if="runtimeAvailable">
            <NDataTable
              :columns="runColumns"
              :data="runs"
              :loading="loading"
              :row-key="(row: RuntimeRun) => row.run_id"
              :scroll-x="920"
            />
            <div v-if="runNextCursor" class="runtime-load-more">
              <NButton secondary :loading="loading" @click="loadRuns(true)">
                加载更多
              </NButton>
            </div>
          </template>
          <EmptyState
            v-else
            compact
            :icon="TimeOutline"
            title="Runtime 来源未挂载"
            :description="reasonLabel(runtimeSummary?.reason)"
          />
        </AppPanelSection>

        <AppPanelSection class="runtime-section" title="Tool Calls" description="最近受治理调用">
          <NDataTable
            v-if="runtimeAvailable"
            :columns="toolColumns"
            :data="toolCalls"
            :loading="loading"
            :row-key="(row: RuntimeToolCall) => row.call_id"
            :scroll-x="800"
          />
          <EmptyState
            v-else
            compact
            :icon="ShieldCheckmarkOutline"
            title="调用来源不可用"
            :description="reasonLabel(runtimeSummary?.reason)"
          />
        </AppPanelSection>
      </NTabPane>

      <NTabPane name="memory" tab="记忆治理">
        <PageToolbar>
          <template #left>
            <NSelect
              v-model:value="candidateProjection"
              class="runtime-filter"
              :options="projectionOptions"
              clearable
              placeholder="全部投影"
              aria-label="筛选投影类型"
            />
            <NSelect
              v-model:value="candidateOperation"
              class="runtime-filter"
              :options="operationOptions"
              clearable
              placeholder="全部操作"
              aria-label="筛选治理操作"
            />
          </template>
        </PageToolbar>

        <AppPanelSection class="runtime-section" title="Candidates" description="记忆投影候选">
          <template v-if="memoryAvailable">
            <NDataTable
              :columns="candidateColumns"
              :data="candidates"
              :loading="loading"
              :row-key="(row: MemoryCandidate) => row.candidate_id"
              :scroll-x="900"
            />
            <div v-if="candidateNextCursor" class="runtime-load-more">
              <NButton secondary :loading="loading" @click="loadCandidates(true)">
                加载更多
              </NButton>
            </div>
          </template>
          <EmptyState
            v-else
            compact
            :icon="LayersOutline"
            title="Memory governance 来源未挂载"
            :description="reasonLabel(memorySummary?.reason)"
          />
        </AppPanelSection>

        <div class="runtime-two-column">
          <AppPanelSection title="Observations" description="不可变观测索引">
            <NDataTable
              v-if="memoryAvailable"
              :columns="observationColumns"
              :data="observations"
              :loading="loading"
              :row-key="(row: MemoryObservation) => row.observation_id"
              :scroll-x="760"
            />
            <EmptyState v-else compact title="观测来源不可用" />
          </AppPanelSection>
          <AppPanelSection title="Conflicts" description="显式冲突索引">
            <NDataTable
              v-if="memoryAvailable"
              :columns="conflictColumns"
              :data="conflicts"
              :loading="loading"
              :row-key="(row: MemoryConflict) => row.conflict_id"
              :scroll-x="720"
            />
            <EmptyState v-else compact title="冲突来源不可用" />
          </AppPanelSection>
        </div>
      </NTabPane>

      <NTabPane name="worldbook" tab="世界书治理">
        <WorldbookGovernancePanel />
      </NTabPane>

      <NTabPane name="readiness" tab="上线门槛">
        <div class="runtime-two-column">
          <AppPanelSection title="Dark Readiness" description="显式来源完整性">
            <div class="readiness-list">
              <div v-for="name in (['runtime', 'memory', 'worldbook'] as const)" :key="name" class="readiness-row">
                <div>
                  <strong>{{ sourceLabel(name) }}</strong>
                  <span>Schema {{ darkReadiness?.sources[name].schema_version ?? '—' }}</span>
                </div>
                <NTag
                  :type="darkReadiness?.sources[name].available ? 'success' : 'warning'"
                  :bordered="false"
                >
                  {{ darkReadiness?.sources[name].available ? '完整' : reasonLabel(darkReadiness?.sources[name].reason) }}
                </NTag>
              </div>
            </div>
          </AppPanelSection>

          <AppPanelSection title="Rollback Readiness" description="attested rollback gates">
            <div class="readiness-list">
              <div v-for="([name, gate]) in rollbackGateEntries" :key="name" class="readiness-row">
                <div>
                  <strong>{{ gateLabel(name) }}</strong>
                  <span>{{ gateReason(gate.status, gate.reason) }}</span>
                  <span>Evidence {{ formatTime(gate.evidence_at) }}</span>
                </div>
                <NTag :type="classifyReadinessGate(gate.status).tone" :bordered="false">
                  {{ classifyReadinessGate(gate.status).label }}
                </NTag>
              </div>
            </div>
          </AppPanelSection>
        </div>

        <AppPanelSection class="runtime-section" title="Activation Gates" description="生产激活证明项">
          <div v-if="activationGateEntries.length" class="blocker-grid">
            <div v-for="([name, gate]) in activationGateEntries" :key="name" class="blocker-item">
              <div>
                <strong>{{ gateLabel(name) }}</strong>
                <span>{{ gateReason(gate.status, gate.reason) }}</span>
                <span>Evidence {{ formatTime(gate.evidence_at) }}</span>
              </div>
              <NTag :type="classifyReadinessGate(gate.status).tone" :bordered="false">
                {{ classifyReadinessGate(gate.status).label }}
              </NTag>
            </div>
          </div>
          <EmptyState v-else compact :icon="AlertCircleOutline" title="没有可用的 attestation" />
        </AppPanelSection>
      </NTabPane>
    </NTabs>

    <NDrawer v-model:show="runDrawerOpen" :width="runDrawerWidth">
      <AppDrawerLayout>
        <template #header>
          <AppDrawerHeader title="运行详情" :subtitle="selectedRun?.run_id ?? '—'" />
        </template>
        <NSpin :show="runDetailLoading">
          <div v-if="selectedRun" class="drawer-stack">
            <AppPanelSection title="Snapshot">
              <dl class="detail-grid">
                <div><dt>状态</dt><dd><component :is="renderStatus(selectedRun.status)" /></dd></div>
                <div><dt>触发</dt><dd>{{ selectedRun.trigger_type }}</dd></div>
                <div><dt>创建</dt><dd>{{ formatTime(selectedRun.created_at) }}</dd></div>
                <div><dt>更新</dt><dd>{{ formatTime(selectedRun.updated_at) }}</dd></div>
              </dl>
            </AppPanelSection>
            <AppPanelSection title="Operator actions" description="读取 server-owned context 后记录裁决">
              <div class="governance-actions">
                <NButton
                  class="governance-action"
                  secondary
                  aria-label="记录工具批准"
                  :disabled="!approvalCall"
                  @click="openGovernance('approval', approvalCall?.call_id)"
                >
                  <template #icon><NIcon :component="CheckmarkCircleOutline" /></template>
                  记录批准
                </NButton>
                <NButton
                  class="governance-action"
                  secondary
                  aria-label="核对外部结果"
                  :disabled="!reconciliationCall"
                  @click="openGovernance('reconciliation', reconciliationCall?.call_id)"
                >
                  <template #icon><NIcon :component="ShieldCheckmarkOutline" /></template>
                  核对结果
                </NButton>
              </div>
            </AppPanelSection>
            <AppPanelSection title="Tool Calls">
              <NDataTable :columns="toolColumns" :data="selectedRunTools" :scroll-x="760" />
            </AppPanelSection>
            <AppPanelSection title="Events">
              <ol class="event-list">
                <li v-for="event in selectedRunEvents" :key="event.event_id">
                  <span>{{ formatTime(event.event_at) }}</span>
                  <strong>{{ event.event_type }}</strong>
                  <code>{{ event.from_status || '∅' }} → {{ event.to_status || '∅' }}</code>
                </li>
              </ol>
            </AppPanelSection>
          </div>
          <EmptyState v-else compact title="运行记录不存在" />
        </NSpin>
      </AppDrawerLayout>
    </NDrawer>

    <NDrawer v-model:show="candidateDrawerOpen" :width="candidateDrawerWidth">
      <AppDrawerLayout>
        <template #header>
          <AppDrawerHeader title="候选详情" :subtitle="selectedCandidate?.candidate_id ?? '—'" />
        </template>
        <NSpin :show="candidateDetailLoading">
          <AppPanelSection v-if="selectedCandidate" title="Fold State">
            <dl class="detail-grid">
              <div><dt>状态</dt><dd><component :is="renderStatus(selectedCandidate.fold_status)" /></dd></div>
              <div><dt>投影</dt><dd>{{ projectionLabel(selectedCandidate.projection_kind) }}</dd></div>
              <div><dt>操作</dt><dd>{{ operationLabel(selectedCandidate.operation) }}</dd></div>
              <div><dt>事件</dt><dd>{{ selectedCandidate.promotion_event_count }}</dd></div>
              <div><dt>已解决冲突</dt><dd>{{ selectedCandidate.resolved_conflict_count ?? 0 }}</dd></div>
              <div><dt>未解决冲突</dt><dd>{{ selectedCandidate.unresolved_conflict_count ?? 0 }}</dd></div>
            </dl>
            <div class="governance-actions governance-actions--candidate">
              <NButton
                class="governance-action"
                secondary
                aria-label="裁决记忆候选"
                @click="openGovernance('memory', selectedCandidate?.candidate_id)"
              >
                <template #icon><NIcon :component="LayersOutline" /></template>
                记录裁决
              </NButton>
            </div>
          </AppPanelSection>
          <EmptyState v-else compact title="候选记录不存在" />
        </NSpin>
      </AppDrawerLayout>
    </NDrawer>

    <GovernanceDecisionDrawer
      v-model:show="governanceOpen"
      :kind="governanceKind"
      :resource-id="governanceResourceId"
      @refresh="refreshGovernance"
    />
  </AppPage>
</template>

<style scoped>
.runtime-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

:deep(.om-page__hero),
:deep(.om-page__surface-wrap),
:deep(.om-page__surface),
.runtime-metrics > *,
.runtime-tabs,
.runtime-tabs :deep(.n-tab-pane),
.runtime-section,
.runtime-two-column > * {
  min-width: 0;
}

.runtime-refreshed {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  text-align: right;
}

.runtime-alert,
.runtime-tabs,
.runtime-section {
  margin-top: 24px;
}

.runtime-filter {
  width: 200px;
}

.runtime-refresh-button,
:deep(.table-icon-button) {
  width: 44px;
  height: 44px;
}

.runtime-load-more {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.governance-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.governance-actions--candidate {
  margin-top: 16px;
}

.governance-action {
  min-width: 44px;
  min-height: 44px;
}

.runtime-two-column {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.readiness-list {
  display: grid;
  gap: 8px;
}

.readiness-row {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
}

.readiness-row > div {
  display: grid;
  gap: 4px;
}

.readiness-row span {
  color: var(--om-text-2);
  font-size: 13px;
}

.readiness-ok {
  color: var(--om-success);
}

.readiness-warn {
  color: var(--om-warning);
}

.blocker-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.blocker-item {
  display: flex;
  min-width: 0;
  min-height: 52px;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-warning);
}

.blocker-item > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.blocker-item span {
  color: var(--om-text-2);
  font-size: 12px;
}

.blocker-item span {
  overflow-wrap: anywhere;
}

.drawer-stack {
  display: grid;
  gap: 16px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.detail-grid > div {
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.detail-grid dt {
  margin-bottom: 4px;
  color: var(--om-text-3);
  font-size: 12px;
}

.detail-grid dd {
  margin: 0;
  color: var(--om-text-1);
  overflow-wrap: anywhere;
}

.event-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.event-list li {
  display: grid;
  grid-template-columns: 160px minmax(120px, 1fr) minmax(150px, 1fr);
  gap: 12px;
  align-items: center;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.event-list span {
  color: var(--om-text-3);
  font-size: 12px;
}

.event-list code {
  color: var(--om-text-2);
  overflow-wrap: anywhere;
}

@media (max-width: 1280px) {
  .runtime-metrics,
  .blocker-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .runtime-two-column {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .runtime-metrics,
  .blocker-grid,
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .runtime-filter {
    width: min(100%, 280px);
  }

  .event-list li {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

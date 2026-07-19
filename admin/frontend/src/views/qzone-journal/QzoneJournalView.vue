<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import { NButton } from 'naive-ui'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  DocumentTextOutline,
  FunnelOutline,
  HelpCircleOutline,
  RefreshOutline,
  TimeOutline,
} from '@vicons/ionicons5'

import {
  extractApiError,
  fetchQzoneDrafts,
  fetchQzoneHealth,
} from '../../api/qzoneJournal'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import DraftDetailDrawer from './DraftDetailDrawer.vue'
import type {
  QzoneDraft,
  QzoneDraftStatus,
  QzoneHealthResponse,
  QzoneStatusBadge,
} from './types'
import {
  QZONE_DRAFT_STATUSES,
  QZONE_SELECTION_REASONS,
  QZONE_SELECTION_REASON_LABELS,
  QZONE_STATUS_LABELS,
  statusBadge,
  statusLabel,
} from './types'

const PAGE_SIZE = 20

const loading = ref(false)
const healthLoading = ref(false)
const listError = ref('')
const healthError = ref('')
const health = ref<QzoneHealthResponse | null>(null)
const drafts = ref<QzoneDraft[]>([])
const total = ref(0)
const offset = ref(0)
const hasMore = ref(false)
const statusFilter = ref<QzoneDraftStatus | null>(null)

const drawerOpen = ref(false)
const selectedDraftId = ref<string | null>(null)
let healthRequestGeneration = 0
let draftRequestGeneration = 0

const page = computed({
  get: () => Math.floor(offset.value / PAGE_SIZE) + 1,
  set: (value: number) => {
    offset.value = Math.max(0, (value - 1) * PAGE_SIZE)
  },
})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const counts = computed(() => health.value?.counts ?? null)
const attentionCount = computed(() => {
  if (!counts.value) return '—'
  return counts.value.pending_review + counts.value.unknown
})
const attentionHint = computed(() => {
  if (!counts.value) return '等待健康检查'
  return `待审核 ${counts.value.pending_review} · 状态未知 ${counts.value.unknown}`
})
const gate = computed(() => health.value?.live_publish_gate ?? null)
const gateReady = computed(() => Boolean(gate.value?.ready))
const gateReasons = computed(() => gate.value?.reasons ?? [])
const gatePhase = computed<'loading' | 'error' | 'ready' | 'blocked'>(() => {
  if (healthError.value) return 'error'
  if (healthLoading.value && !health.value) return 'loading'
  if (gateReady.value) return 'ready'
  if (health.value) return 'blocked'
  return 'loading'
})
const gateClass = computed(() => {
  switch (gatePhase.value) {
    case 'ready':
      return 'qzone-gate--ready'
    case 'error':
      return 'qzone-gate--error'
    case 'loading':
      return 'qzone-gate--loading'
    default:
      return 'qzone-gate--blocked'
  }
})
const gateIcon = computed(() => {
  switch (gatePhase.value) {
    case 'ready':
      return CheckmarkCircleOutline
    case 'loading':
      return TimeOutline
    default:
      return AlertCircleOutline
  }
})
const gateTitle = computed(() => {
  switch (gatePhase.value) {
    case 'error':
      return '运行门状态暂不可用'
    case 'loading':
      return '正在检查门禁'
    case 'ready':
      return '真实发布门禁已就绪'
    default:
      return '真实发布仍被锁定'
  }
})
const gateDescription = computed(() => {
  switch (gatePhase.value) {
    case 'error':
      return healthError.value
    case 'loading':
      return '正在读取健康检查与线缆门禁。本控制台不提供真实发布按钮。'
    case 'ready':
      return '健康检查显示门禁通过。本控制台仍不提供真实发布按钮；任何 live publish 须走受控运维路径。'
    default:
      return '当前内置线缆配置与运行配置禁止真实发布。以下原因不含任何凭证。'
  }
})
const gateBadgeStatus = computed<QzoneStatusBadge>(() => {
  switch (gatePhase.value) {
    case 'ready':
      return 'success'
    case 'error':
      return 'error'
    case 'blocked':
      return 'warning'
    default:
      return 'info'
  }
})
const gateBadgeLabel = computed(() => {
  switch (gatePhase.value) {
    case 'ready':
      return '门禁就绪'
    case 'error':
      return '状态不可用'
    case 'blocked':
      return '保持锁定'
    default:
      return '检查中'
  }
})
const selectionSummary = computed(() => health.value?.selection_summary ?? {
  scope: 'process_lifetime',
  total: 0,
  accepted: 0,
  rejected: 0,
  acceptance_rate: 0,
})
const selectionRows = computed(() => QZONE_SELECTION_REASONS
  .map(reason => ({
    reason,
    label: QZONE_SELECTION_REASON_LABELS[reason],
    count: Number(health.value?.selection_decisions?.[reason] ?? 0),
  }))
  .filter(row => row.count > 0)
  .sort((left, right) => right.count - left.count))
const selectionRateLabel = computed(() => {
  const rate = Math.max(0, Math.min(1, selectionSummary.value.acceptance_rate || 0))
  return `${Math.round(rate * 100)}%`
})

const statusOptions = computed<SelectOption[]>(() =>
  QZONE_DRAFT_STATUSES.map(status => ({
    label: QZONE_STATUS_LABELS[status],
    value: status,
  })),
)

const isGloballyEmpty = computed(
  () => !loading.value && !listError.value && total.value === 0 && !statusFilter.value,
)
const isFilteredEmpty = computed(
  () => !loading.value && !listError.value && total.value === 0 && Boolean(statusFilter.value),
)

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\+00:00$/, ' UTC').replace(/Z$/, ' UTC')
}

function previewContent(content: string): string {
  const text = (content || '').replace(/\s+/g, ' ').trim()
  if (text.length <= 80) return text || '（空正文）'
  return `${text.slice(0, 80)}…`
}

function openDraft(draftId: string) {
  selectedDraftId.value = draftId
  drawerOpen.value = true
}

function onSelectDraft(draftId: string) {
  selectedDraftId.value = draftId
}

const columns = computed<DataTableColumns<QzoneDraft>>(() => [
  {
    title: '草稿',
    key: 'content',
    minWidth: 280,
    ellipsis: { tooltip: true },
    render(row) {
      return h('div', { class: 'qzone-table__draft' }, [
        h('span', { class: 'qzone-table__content' }, previewContent(row.content)),
        h('span', { class: 'qzone-table__id' }, row.draft_id),
      ])
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render(row) {
      return h(StateBadge, {
        status: statusBadge(row.status),
        label: statusLabel(row.status),
        compact: true,
      })
    },
  },
  {
    title: '来源',
    key: 'source',
    width: 140,
    ellipsis: { tooltip: true },
  },
  {
    title: '事件日',
    key: 'event_date',
    width: 120,
  },
  {
    title: '更新时间',
    key: 'updated_at',
    width: 180,
    render(row) {
      return formatTime(row.updated_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 96,
    render(row) {
      return h(
        NButton,
        {
          size: 'small',
          secondary: true,
          type: 'primary',
          onClick: () => openDraft(row.draft_id),
        },
        { default: () => '查看' },
      )
    },
  },
])

async function loadHealth() {
  const requestGeneration = ++healthRequestGeneration
  healthLoading.value = true
  healthError.value = ''
  try {
    const response = await fetchQzoneHealth()
    if (requestGeneration !== healthRequestGeneration) return
    health.value = response
  } catch (error) {
    if (requestGeneration !== healthRequestGeneration) return
    healthError.value = extractApiError(error, '健康状态加载失败')
    // Drop stale gate reasons/meta so a failed refresh cannot paint old lock state.
    health.value = null
  } finally {
    if (requestGeneration === healthRequestGeneration) {
      healthLoading.value = false
    }
  }
}

async function loadDrafts() {
  const requestGeneration = ++draftRequestGeneration
  const requestedStatus = statusFilter.value
  const requestedOffset = offset.value
  loading.value = true
  listError.value = ''
  try {
    const res = await fetchQzoneDrafts({
      status: requestedStatus,
      limit: PAGE_SIZE,
      offset: requestedOffset,
    })
    if (requestGeneration !== draftRequestGeneration) return
    drafts.value = res.drafts ?? []
    total.value = Number(res.total ?? 0)
    hasMore.value = Boolean(res.has_more)
    if (typeof res.offset === 'number') offset.value = res.offset
  } catch (error) {
    if (requestGeneration !== draftRequestGeneration) return
    listError.value = extractApiError(error, '草稿列表加载失败')
    drafts.value = []
    total.value = 0
    hasMore.value = false
  } finally {
    if (requestGeneration === draftRequestGeneration) {
      loading.value = false
    }
  }
}

async function refreshAll() {
  await Promise.all([loadHealth(), loadDrafts()])
}

async function onDrawerRefreshed() {
  await refreshAll()
}

function onStatusFilterUpdate(value: QzoneDraftStatus | null) {
  statusFilter.value = value
  offset.value = 0
}

function onPageUpdate(value: number) {
  page.value = value
}

watch([statusFilter, offset], () => {
  void loadDrafts()
})

onMounted(() => {
  void refreshAll()
})
</script>

<template>
  <AppPage
    class="qzone-page"
    title="空间日志"
    eyebrow="QZone Journal"
    description="人工审核队列：审阅草稿、模拟投递、处置未知状态。真实发布入口不在此控制台。"
  >
    <template #action>
      <NButton
        secondary
        size="small"
        :loading="loading || healthLoading"
        aria-label="刷新空间日志"
        title="刷新"
        @click="refreshAll"
      >
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <div class="qzone-view">
      <AppPanelSection
        class="qzone-gate"
        :class="gateClass"
        eyebrow="运行门 · Live Publish Gate"
        :title="gateTitle"
        :description="gateDescription"
        role="status"
        aria-live="polite"
        :aria-busy="gatePhase === 'loading'"
      >
        <template #aside>
          <div class="qzone-gate__state">
            <span class="qzone-gate__icon" aria-hidden="true">
              <NIcon :component="gateIcon" :size="20" />
            </span>
            <StateBadge
              :status="gateBadgeStatus"
              :label="gateBadgeLabel"
              compact
            />
          </div>
        </template>

        <ul
          v-if="gatePhase === 'blocked' && gateReasons.length"
          class="qzone-gate__reasons"
        >
          <li
            v-for="reason in gateReasons"
            :key="reason.code"
            class="qzone-gate__reason"
          >
            <code class="qzone-gate__code">{{ reason.code }}</code>
            <span>{{ reason.message }}</span>
          </li>
        </ul>

        <div v-if="health && gatePhase !== 'error'" class="qzone-gate__meta">
          <span class="qzone-gate__meta-item">
            <small>profile</small>
            <strong>{{ health.wire_profile_id || '—' }}</strong>
          </span>
          <span class="qzone-gate__meta-item">
            <small>validated</small>
            <strong>{{ health.profile_validated ? '是' : '否' }}</strong>
          </span>
          <span class="qzone-gate__meta-item">
            <small>dry-run</small>
            <strong>{{ health.dry_run ? '开启' : '关闭' }}</strong>
          </span>
          <span class="qzone-gate__meta-item">
            <small>live_allowed</small>
            <strong>{{ health.live_allowed ? '是' : '否' }}</strong>
          </span>
        </div>

        <NButton
          v-if="gatePhase === 'error'"
          size="small"
          secondary
          class="qzone-gate__retry"
          @click="loadHealth"
        >
          重试健康检查
        </NButton>
      </AppPanelSection>

      <div class="qzone-metrics">
        <MetricCard
          title="待处理"
          :value="attentionCount"
          :hint="attentionHint"
          :icon="AlertCircleOutline"
          accent="warning"
        />
        <MetricCard
          title="状态未知"
          :value="counts?.unknown ?? '—'"
          hint="unknown · 需人工处置"
          :icon="HelpCircleOutline"
          accent="warning"
        />
        <MetricCard
          title="已通过"
          :value="counts?.approved ?? '—'"
          hint="approved · 可 dry-run"
          :icon="CheckmarkCircleOutline"
          accent="info"
        />
        <MetricCard
          title="已发布"
          :value="counts?.published ?? '—'"
          hint="published · 已确认远端记录"
          :icon="DocumentTextOutline"
          accent="success"
        />
      </div>

      <PageToolbar class="qzone-toolbar">
        <template #left>
          <label class="qzone-toolbar__filter">
            <span class="qzone-toolbar__label">状态筛选</span>
            <NSelect
              :value="statusFilter"
              :options="statusOptions"
              clearable
              placeholder="全部状态"
              class="qzone-toolbar__select"
              aria-label="按状态筛选草稿"
              @update:value="onStatusFilterUpdate"
            />
          </label>
          <span class="qzone-toolbar__count" aria-live="polite">
            共 {{ total }} 条
            <template v-if="hasMore"> · 还有更多</template>
          </span>
        </template>
        <template #right>
          <NButton
            secondary
            size="small"
            :loading="loading"
            aria-label="刷新草稿列表"
            @click="loadDrafts"
          >
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
            刷新列表
          </NButton>
        </template>
      </PageToolbar>

      <AppPanelSection
        eyebrow="Review Queue"
        title="草稿列表"
        description="服务端分页，每页 20 条。请使用「查看」打开详情与操作。"
      >
        <template #aside>
          <StateBadge
            :status="attentionCount === 0 ? 'success' : 'warning'"
            :label="attentionCount === 0 ? '队列已清' : `待处理 ${attentionCount}`"
            compact
          />
        </template>

        <NSkeleton v-if="loading && drafts.length === 0" :repeat="6" text />

        <EmptyState
          v-else-if="listError"
          title="列表加载失败"
          :description="listError"
          :icon="AlertCircleOutline"
        >
          <NButton type="primary" secondary @click="loadDrafts">
            重试
          </NButton>
        </EmptyState>

        <EmptyState
          v-else-if="isGloballyEmpty"
          title="暂无空间日志草稿"
          description="事件成稿后会出现在待审队列。当前库中没有任何草稿。"
          :icon="DocumentTextOutline"
        />

        <EmptyState
          v-else-if="isFilteredEmpty"
          title="当前筛选无结果"
          description="换一个状态，或清空筛选查看全部草稿。"
          :icon="DocumentTextOutline"
          compact
        >
          <NButton secondary @click="onStatusFilterUpdate(null)">
            清空筛选
          </NButton>
        </EmptyState>

        <template v-else>
          <NDataTable
            :columns="columns"
            :data="drafts"
            :loading="loading"
            :bordered="false"
            size="small"
            :row-key="(row: QzoneDraft) => row.draft_id"
            :scroll-x="900"
          />

          <div v-if="total > PAGE_SIZE" class="qzone-pagination">
            <NPagination
              :page="page"
              :page-count="pageCount"
              :page-slot="7"
              show-quick-jumper
              aria-label="草稿列表分页"
              @update:page="onPageUpdate"
            />
          </div>
        </template>
      </AppPanelSection>

      <AppPanelSection
        class="qzone-selection"
        eyebrow="Selection Trace"
        title="选材诊断"
        description="当前 Bot 进程内的闭集选材结果，重启后重新计数。"
      >
        <template #aside>
          <StateBadge status="info" label="本次进程" compact />
        </template>

        <EmptyState
          v-if="healthError"
          title="选材诊断暂不可用"
          :description="healthError"
          :icon="AlertCircleOutline"
          compact
        >
          <NButton secondary size="small" @click="loadHealth">
            重试健康检查
          </NButton>
        </EmptyState>

        <NSkeleton v-else-if="!health" :repeat="4" text />

        <template v-else>
          <dl class="qzone-selection__summary">
            <div class="qzone-selection__summary-item">
              <dt>已评估</dt>
              <dd>{{ selectionSummary.total }}</dd>
            </div>
            <div class="qzone-selection__summary-item">
              <dt>已成稿</dt>
              <dd>{{ selectionSummary.accepted }}</dd>
            </div>
            <div class="qzone-selection__summary-item">
              <dt>已拒绝</dt>
              <dd>{{ selectionSummary.rejected }}</dd>
            </div>
            <div class="qzone-selection__summary-item">
              <dt>通过率</dt>
              <dd>{{ selectionRateLabel }}</dd>
            </div>
          </dl>

          <div class="qzone-selection__budget">
            <span>单次草稿上限 {{ health.max_drafts_per_tick }}</span>
            <span>当日草稿预算 {{ health.max_drafts_per_day }}</span>
          </div>

          <EmptyState
            v-if="selectionRows.length === 0"
            title="暂无选材决策"
            description="当前进程尚未评估空间日志候选。"
            :icon="FunnelOutline"
            compact
          />

          <ul v-else class="qzone-selection__list">
            <li
              v-for="row in selectionRows"
              :key="row.reason"
              class="qzone-selection__row"
            >
              <span class="qzone-selection__reason">
                <strong>{{ row.label }}</strong>
                <code>{{ row.reason }}</code>
              </span>
              <span class="qzone-selection__count">{{ row.count }}</span>
            </li>
          </ul>
        </template>
      </AppPanelSection>
    </div>

    <DraftDetailDrawer
      v-model:show="drawerOpen"
      :draft-id="selectedDraftId"
      @select="onSelectDraft"
      @refreshed="onDrawerRefreshed"
    />
  </AppPage>
</template>

<style scoped>
.qzone-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.qzone-page :deep(.om-page__surface) {
  min-width: 0;
}

.qzone-gate {
  min-width: 0;
  box-sizing: border-box;
  background: var(--om-surface-2);
}

.qzone-gate--blocked {
  border-color: color-mix(in srgb, var(--om-warning) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 8%, var(--om-surface-2));
}

.qzone-gate--ready {
  border-color: color-mix(in srgb, var(--om-success) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-success) 8%, var(--om-surface-2));
}

.qzone-gate--loading {
  border-color: color-mix(in srgb, var(--om-info) 35%, var(--om-border));
  background: color-mix(in srgb, var(--om-info) 8%, var(--om-surface-2));
}

.qzone-gate--error {
  border-color: color-mix(in srgb, var(--om-danger) 40%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 8%, var(--om-surface-2));
}

.qzone-gate__state {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.qzone-gate__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface);
  color: rgb(var(--primary-color));
}

.qzone-gate__reasons {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.qzone-gate__reason {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 12px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.5;
}

.qzone-gate__code {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  padding: 4px 8px;
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
}

.qzone-gate__meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  font-size: 12px;
}

.qzone-gate__meta-item {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
}

.qzone-gate__meta-item small {
  overflow-wrap: anywhere;
  color: var(--om-text-3);
  font-size: 11px;
}

.qzone-gate__meta-item strong {
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 13px;
}

.qzone-gate__retry {
  align-self: flex-start;
}

.qzone-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.qzone-selection {
  min-width: 0;
  box-sizing: border-box;
}

.qzone-selection__summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 0 0 16px;
  padding: 0 0 16px;
  border-bottom: 1px solid var(--om-border);
}

.qzone-selection__summary-item {
  min-width: 0;
}

.qzone-selection__summary-item dt {
  color: var(--om-text-3);
  font-size: 12px;
}

.qzone-selection__summary-item dd {
  margin: 4px 0 0;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
}

.qzone-selection__budget {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 8px;
  color: var(--om-text-3);
  font-size: 12px;
}

.qzone-selection__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.qzone-selection__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--om-border);
}

.qzone-selection__reason {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  color: var(--om-text-1);
}

.qzone-selection__reason strong {
  font-size: 13px;
}

.qzone-selection__reason code {
  max-width: 100%;
  overflow-wrap: anywhere;
  color: var(--om-text-3);
  font-size: 11px;
}

.qzone-selection__count {
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.qzone-toolbar {
  margin: 0;
}

.qzone-toolbar__filter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.qzone-toolbar__label {
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
}

.qzone-toolbar__select {
  width: 180px;
}

.qzone-toolbar__count {
  color: var(--om-text-3);
  font-size: 12px;
}

.qzone-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.qzone-table__draft {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.qzone-table__content {
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.5;
}

.qzone-table__id {
  overflow: hidden;
  color: var(--om-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (min-width: 1440px) {
  .qzone-view {
    gap: 24px;
  }

  .qzone-metrics {
    gap: 16px;
  }
}

@media (max-width: 1280px) {
  .qzone-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .qzone-gate__meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .qzone-toolbar :deep(.page-toolbar__side) {
    width: 100%;
    justify-content: space-between;
  }

  .qzone-selection__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .qzone-metrics {
    grid-template-columns: 1fr;
  }

  .qzone-selection__summary {
    grid-template-columns: 1fr;
  }

  .qzone-gate__meta {
    grid-template-columns: 1fr;
  }

  .qzone-toolbar__filter {
    width: 100%;
  }

  .qzone-toolbar__select {
    min-width: 0;
    flex: 1;
    width: 100%;
  }

  .qzone-pagination {
    justify-content: center;
  }
}
</style>

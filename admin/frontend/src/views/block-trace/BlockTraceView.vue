<script setup lang="ts">
import {
  RefreshOutline,
  TrashOutline,
  AnalyticsOutline,
  FunnelOutline,
} from '@vicons/ionicons5'
import { useMessage, NTag } from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import { onBlockTrace, useSSE } from '../../composables/useSSE'

interface TraceItem {
  trace_id: string
  request_id: string
  task: string
  source: string
  provider: string
  candidate_id: string
  decision: string
  hit_reason: string
  evidence_refs: string[]
  token_estimate: number
  char_count: number
  position: string
  label: string
  priority: number
  decay_state: string
  budget_reason: string
  metadata: Record<string, any>
  created_at: string
}

interface Stats {
  total: number
  by_decision: Record<string, number>
  by_source: Record<string, number>
  by_position: Record<string, number>
}

interface AlignmentRow {
  source: string
  provider: number
  plugin: number
  accepted: number
  trimmed: number
  rejected: number
  shadow_only: number
}

interface Alignment {
  ok: boolean
  sample_size: number
  mode: 'active' | 'plugin_only' | 'shadow_or_overlap' | 'empty'
  by_source: AlignmentRow[]
}

const DECISION_TAG: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  accepted: 'success',
  trimmed: 'warning',
  rejected: 'error',
  shadow_only: 'default',
}

const msg = useMessage()
const loading = ref(false)
const traces = ref<TraceItem[]>([])
const stats = ref<Stats | null>(null)
const alignment = ref<Alignment | null>(null)
const sourceFilter = ref<string | null>(null)
const requestFilter = ref('')
const pruning = ref(false)

const MODE_LABEL: Record<Alignment['mode'], string> = {
  active: 'Active（Provider 主导）',
  plugin_only: 'Plugin only（Provider 未注册）',
  shadow_or_overlap: 'Shadow / Overlap（双跑中）',
  empty: '尚无 trace',
}

const MODE_TAG: Record<Alignment['mode'], 'success' | 'warning' | 'default' | 'error'> = {
  active: 'success',
  plugin_only: 'warning',
  shadow_or_overlap: 'warning',
  empty: 'default',
}

const sourceOptions = computed<SelectOption[]>(() => {
  if (!stats.value) return []
  return Object.keys(stats.value.by_source).map(s => ({ label: s, value: s }))
})

const filteredTraces = computed(() => {
  let result = traces.value
  if (sourceFilter.value) {
    result = result.filter(t => t.source === sourceFilter.value)
  }
  if (requestFilter.value) {
    result = result.filter(t => t.request_id.includes(requestFilter.value))
  }
  return result
})

const groupedTraces = computed(() => {
  const groups: Record<string, TraceItem[]> = {}
  for (const t of filteredTraces.value) {
    if (!groups[t.request_id]) groups[t.request_id] = []
    groups[t.request_id].push(t)
  }
  return Object.entries(groups).sort(
    (a, b) => (b[1][0]?.created_at ?? '').localeCompare(a[1][0]?.created_at ?? ''),
  )
})

const columns: DataTableColumns<TraceItem> = [
  { title: 'Source', key: 'source', width: 100 },
  { title: 'Label', key: 'label', width: 140 },
  { title: 'Position', key: 'position', width: 90 },
  { title: 'Priority', key: 'priority', width: 80, sorter: 'default' },
  { title: 'Chars', key: 'char_count', width: 80, sorter: 'default' },
  {
    title: 'Decision',
    key: 'decision',
    width: 100,
    render(row) {
      return h(NTag, { type: DECISION_TAG[row.decision] ?? 'default', size: 'small' }, () => row.decision)
    },
  },
  { title: 'Budget Reason', key: 'budget_reason', ellipsis: { tooltip: true } },
  { title: 'Time', key: 'created_at', width: 160 },
]

async function fetchData() {
  loading.value = true
  try {
    const [traceRes, statsRes, alignRes] = await Promise.all([
      api<{ ok: boolean; traces: TraceItem[] }>('/api/admin/block-trace/recent?limit=200'),
      api<{ ok: boolean } & Stats>('/api/admin/block-trace/stats'),
      api<Alignment>('/api/admin/block-trace/alignment?limit=500'),
    ])
    if (traceRes.ok) traces.value = traceRes.traces
    if (statsRes.ok) stats.value = { total: statsRes.total, by_decision: statsRes.by_decision, by_source: statsRes.by_source, by_position: statsRes.by_position }
    if (alignRes.ok) alignment.value = alignRes
  } catch (e: any) {
    msg.error(e?.message ?? 'Failed to load traces')
  } finally {
    loading.value = false
  }
}

async function handlePrune() {
  pruning.value = true
  try {
    const res = await api<{ ok: boolean; deleted: number }>('/api/admin/block-trace/prune', { method: 'POST', params: { keep_days: 7 } })
    if (res.ok) {
      msg.success(`Pruned ${res.deleted} old traces`)
      await fetchData()
    }
  } catch (e: any) {
    msg.error(e?.message ?? 'Prune failed')
  } finally {
    pruning.value = false
  }
}

// Keep the shared EventSource alive while this view is mounted.
useSSE()

// Debounce SSE-triggered refreshes so a burst of block_trace events
// (shadow + budget for the same request) only triggers one fetch.
let refreshTimer: ReturnType<typeof setTimeout> | null = null
function scheduleRefresh() {
  if (refreshTimer) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    fetchData()
  }, 400)
}

let unsubscribeBlockTrace: (() => void) | null = null

onMounted(() => {
  fetchData()
  unsubscribeBlockTrace = onBlockTrace(scheduleRefresh)
})

onUnmounted(() => {
  unsubscribeBlockTrace?.()
  unsubscribeBlockTrace = null
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
})
</script>

<template>
  <AppPage
    title="BlockTrace"
    eyebrow="Block Trace"
    description="Prompt block 预算仲裁与追踪。"
  >
    <template #action>
      <NButton secondary size="small" :loading="loading" @click="fetchData">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <div class="bt-metrics">
      <MetricCard
        title="Total Traces"
        :value="stats?.total ?? 0"
      />
      <MetricCard
        title="Accepted"
        :value="stats?.by_decision?.accepted ?? 0"
        accent="success"
      />
      <MetricCard
        title="Trimmed"
        :value="stats?.by_decision?.trimmed ?? 0"
        accent="warning"
      />
      <MetricCard
        title="Rejected"
        :value="stats?.by_decision?.rejected ?? 0"
        accent="warning"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="requestFilter"
          placeholder="Filter by request_id..."
          clearable
          class="bt-toolbar__request"
        />
        <NSelect
          v-model:value="sourceFilter"
          :options="sourceOptions"
          placeholder="Source"
          clearable
          class="bt-toolbar__source"
        />
      </template>
      <template #right>
        <NButton
          size="small"
          type="warning"
          :loading="pruning"
          @click="handlePrune"
        >
          <template #icon>
            <NIcon :component="TrashOutline" />
          </template>
          Prune (7d)
        </NButton>
      </template>
    </PageToolbar>

    <AppPanelSection
      v-if="alignment"
      eyebrow="Alignment"
      title="Provider / Plugin Alignment"
      :description="`基于最近 ${alignment.sample_size} 条 trace`"
      class="bt-alignment-panel"
    >
      <template #aside>
        <NTag :type="MODE_TAG[alignment.mode]" size="small" :bordered="false" round>
          {{ MODE_LABEL[alignment.mode] }}
        </NTag>
      </template>

      <NDataTable
        :columns="[
          { title: 'Source', key: 'source', width: 120 },
          { title: 'Provider', key: 'provider', width: 100 },
          { title: 'Plugin', key: 'plugin', width: 100 },
          { title: 'Accepted', key: 'accepted', width: 100 },
          { title: 'Trimmed', key: 'trimmed', width: 100 },
          { title: 'Rejected', key: 'rejected', width: 100 },
          { title: 'Shadow', key: 'shadow_only', width: 100 },
        ]"
        :data="alignment.by_source"
        :bordered="false"
        :pagination="false"
        size="small"
        :row-key="(row: AlignmentRow) => row.source"
      />
    </AppPanelSection>

    <EmptyState
      v-if="!loading && traces.length === 0"
      :icon="AnalyticsOutline"
      title="尚无 Trace 记录"
      description="BlockTraceBus 已就绪，等待首次 LLM 调用后自动写入 trace。"
    />

    <div v-else class="bt-request-groups">
      <AppPanelSection
        v-for="[reqId, items] in groupedTraces"
        :key="reqId"
        class="bt-request-card"
      >
        <template #aside>
          <span class="bt-request-time">{{ items[0]?.created_at ?? '' }}</span>
        </template>

        <div class="bt-request-header">
          <NIcon :component="FunnelOutline" :size="16" />
          <code class="bt-request-id">{{ reqId }}</code>
          <NTag size="tiny" round :bordered="false">
            {{ items.length }} blocks
          </NTag>
        </div>

        <NDataTable
          :columns="columns"
          :data="items"
          :bordered="false"
          :pagination="false"
          size="small"
          :row-key="(row: TraceItem) => row.trace_id"
          max-height="320"
        />
      </AppPanelSection>
    </div>
  </AppPage>
</template>

<style scoped>
.bt-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.bt-toolbar__request {
  width: 260px;
}

.bt-toolbar__source {
  width: 160px;
}

.bt-alignment-panel {
  margin-bottom: 12px;
}

.bt-request-groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bt-request-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 13px;
}

.bt-request-id {
  color: var(--om-text-2);
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
}

.bt-request-time {
  color: var(--om-text-3);
  font-size: 12px;
}
</style>

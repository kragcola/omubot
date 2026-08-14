<script setup lang="ts">
import { EyeOutline, LibraryOutline, RefreshOutline } from '@vicons/ionicons5'
import { NButton, NIcon, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

import {
  fetchWorldbookGovernanceDetail,
  fetchWorldbookGovernanceList,
  fetchWorldbookGovernanceSummary,
  type WorldbookGovernanceProposal,
  type WorldbookGovernanceSummary,
} from '../../../api/agentRuntime'

const message = useMessage()
const loading = ref(false)
const summary = ref<WorldbookGovernanceSummary | null>(null)
const proposals = ref<WorldbookGovernanceProposal[]>([])
const nextCursor = ref<string | null>(null)
const sourceKind = ref<string | null>(null)
const status = ref<string | null>(null)
const selected = ref<WorldbookGovernanceProposal | null>(null)
const detailOpen = ref(false)

const available = computed(() => summary.value?.available === true)
const sourceOptions = [
  { label: 'Social evidence', value: 'social_evidence' },
  { label: 'Schedule', value: 'schedule' },
]
const statusOptions = ['pending', 'approved', 'rejected', 'committed']
  .map(value => ({ label: statusLabel(value), value }))

function statusLabel(value: string) {
  return ({
    pending: '待裁决',
    approved: '已批准',
    rejected: '已拒绝',
    committed: '已提交',
  } as Record<string, string>)[value] ?? value
}

function statusType(value: string): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (value === 'committed') return 'success'
  if (value === 'rejected') return 'error'
  if (value === 'approved') return 'info'
  return value === 'pending' ? 'warning' : 'default'
}

function formatTime(value: string | undefined) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

const columns: DataTableColumns<WorldbookGovernanceProposal> = [
  { title: 'Proposal', key: 'proposal_id', minWidth: 220, ellipsis: { tooltip: true } },
  { title: '世界', key: 'world_id', minWidth: 150, ellipsis: { tooltip: true } },
  { title: '来源', key: 'source_kind', width: 150 },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: row => h(NTag, { type: statusType(row.status), bordered: false }, () => statusLabel(row.status)),
  },
  { title: '发生时间', key: 'occurred_at', width: 180, render: row => formatTime(row.occurred_at) },
  {
    title: '',
    key: 'actions',
    width: 64,
    render: row => h(
      NButton,
      {
        quaternary: true,
        circle: true,
        class: 'worldbook-inspect',
        'aria-label': `查看提议 ${row.proposal_id}`,
        onClick: () => inspect(row.proposal_id),
      },
      { icon: () => h(NIcon, { component: EyeOutline }) },
    ),
  },
]

async function loadList(append = false) {
  const result = await fetchWorldbookGovernanceList({
    limit: 50,
    cursor: append ? nextCursor.value ?? undefined : undefined,
    world_id: undefined,
    source_kind: sourceKind.value ?? undefined,
    status: status.value ?? undefined,
  })
  proposals.value = append ? [...proposals.value, ...result.items] : result.items
  nextCursor.value = result.next_cursor
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    const [summaryValue] = await Promise.all([
      fetchWorldbookGovernanceSummary(),
      loadList(),
    ])
    summary.value = summaryValue
  }
  catch {
    message.error('Worldbook 治理记录加载失败')
  }
  finally {
    loading.value = false
  }
}

async function inspect(proposalId: string) {
  loading.value = true
  try {
    selected.value = (await fetchWorldbookGovernanceDetail(proposalId)).item
    detailOpen.value = true
  }
  catch {
    message.error('Worldbook 提议详情加载失败')
  }
  finally {
    loading.value = false
  }
}

watch([sourceKind, status], () => { void loadList() })
onMounted(() => { void refresh() })
</script>

<template>
  <div class="worldbook-stack">
    <PageToolbar>
      <template #left>
        <NTag type="info" :bordered="false">只读</NTag>
        <NSelect
          v-model:value="sourceKind"
          class="worldbook-filter"
          :options="sourceOptions"
          clearable
          placeholder="全部来源"
          aria-label="筛选世界书来源"
        />
        <NSelect
          v-model:value="status"
          class="worldbook-filter"
          :options="statusOptions"
          clearable
          placeholder="全部状态"
          aria-label="筛选世界书状态"
        />
      </template>
      <template #right>
        <NButton circle quaternary :loading="loading" aria-label="刷新世界书治理记录" @click="refresh">
          <template #icon><NIcon :component="RefreshOutline" /></template>
        </NButton>
      </template>
    </PageToolbar>

    <div class="worldbook-metrics">
      <MetricCard title="提议" :value="summary?.proposal_count ?? '—'" hint="治理事实总量" :icon="LibraryOutline" accent="primary" />
      <MetricCard title="待裁决" :value="summary?.pending_count ?? '—'" hint="尚无 operator decision" :icon="LibraryOutline" accent="warning" />
      <MetricCard title="已批准" :value="summary?.approved_count ?? '—'" hint="等待 reducer receipt" :icon="LibraryOutline" accent="info" />
      <MetricCard title="已提交" :value="summary?.committed_count ?? '—'" hint="verifier receipt 已存在" :icon="LibraryOutline" accent="success" />
    </div>

    <AppPanelSection title="Governed proposals" description="bounded / redacted / GET-only">
      <NDataTable
        v-if="available"
        :columns="columns"
        :data="proposals"
        :loading="loading"
        :row-key="(row: WorldbookGovernanceProposal) => row.proposal_id"
        :scroll-x="980"
      />
      <EmptyState
        v-else
        compact
        :icon="LibraryOutline"
        title="Worldbook governance 来源未挂载"
        description="当前没有显式、已打开的只读来源"
      />
      <div v-if="nextCursor" class="worldbook-more">
        <NButton secondary :loading="loading" @click="loadList(true)">加载更多</NButton>
      </div>
    </AppPanelSection>

    <NDrawer v-model:show="detailOpen" :width="560">
      <AppDrawerLayout>
        <template #header>
          <AppDrawerHeader title="Worldbook 提议" :subtitle="selected?.proposal_id ?? '—'" />
        </template>
        <AppPanelSection v-if="selected" title="Redacted record">
          <dl class="worldbook-detail">
            <div><dt>状态</dt><dd>{{ statusLabel(selected.status) }}</dd></div>
            <div><dt>世界</dt><dd>{{ selected.world_id }}</dd></div>
            <div><dt>来源</dt><dd>{{ selected.source_kind }}</dd></div>
            <div><dt>事件</dt><dd>{{ selected.event_id }}</dd></div>
            <div><dt>证据数</dt><dd>{{ selected.provenance.evidence_count }}</dd></div>
            <div><dt>时间基准</dt><dd>{{ selected.provenance.time_basis }}</dd></div>
            <div><dt>发生时间</dt><dd>{{ formatTime(selected.occurred_at) }}</dd></div>
            <div><dt>提议时间</dt><dd>{{ formatTime(selected.proposed_at) }}</dd></div>
          </dl>
        </AppPanelSection>
        <EmptyState v-else compact title="提议记录不存在" />
      </AppDrawerLayout>
    </NDrawer>
  </div>
</template>

<style scoped>
.worldbook-stack {
  display: grid;
  gap: 16px;
}

.worldbook-filter {
  width: 200px;
}

.worldbook-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

:deep(.worldbook-inspect) {
  width: 44px;
  height: 44px;
}

.worldbook-more {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.worldbook-detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.worldbook-detail > div {
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.worldbook-detail dt {
  color: var(--om-text-3);
  font-size: 12px;
}

.worldbook-detail dd {
  margin: 4px 0 0;
  color: var(--om-text-1);
  overflow-wrap: anywhere;
}

@media (max-width: 1280px) {
  .worldbook-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .worldbook-metrics,
  .worldbook-detail {
    grid-template-columns: minmax(0, 1fr);
  }

  .worldbook-filter {
    width: min(100%, 280px);
  }
}
</style>

<script setup lang="ts">
import { BulbOutline } from '@vicons/ionicons5'
import type { DataTableColumns } from 'naive-ui'
import EmptyState from '../../../../components/common/EmptyState.vue'
import {
  type Candidate,
  DOMAIN_LABEL,
  DOMAIN_TAG_TYPE,
  STATE_LABEL,
  STATE_TAG_TYPE,
  timeText,
  useMemoryConsoleInject,
} from './state'

const console_ = useMemoryConsoleInject()
const { filteredCandidates, loading, openDecide, openDetail } = console_

const columns = computed<DataTableColumns<Candidate>>(() => [
  {
    title: '摘要',
    key: 'summary',
    minWidth: 240,
    ellipsis: { tooltip: true },
    render: (row) => {
      const anchor = row.payload?.situation
        || row.payload?.term
        || row.payload?.expression
        || row.payload?.subject
        || row.payload?.subject_node
        || '(空 payload)'
      return h(
        'a',
        {
          style: 'color: var(--om-text-1); cursor: pointer; font-weight: 500',
          onClick: () => openDetail(row.candidate_id),
        },
        String(anchor),
      )
    },
  },
  {
    title: '域',
    key: 'domain',
    width: 120,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: DOMAIN_TAG_TYPE[row.domain] || 'default' },
      () => DOMAIN_LABEL[row.domain] || row.domain,
    ),
  },
  {
    title: '状态',
    key: 'state',
    width: 110,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: STATE_TAG_TYPE[row.state] || 'default' },
      () => STATE_LABEL[row.state] || row.state,
    ),
  },
  { title: '群', key: 'group_id', width: 110, ellipsis: { tooltip: true } },
  {
    title: '置信',
    key: 'confidence',
    width: 80,
    render: row => `${Math.round(row.confidence * 100)}%`,
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 160,
    render: row => timeText(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    fixed: 'right',
    render: (row) => {
      const NButton = resolveComponent('NButton') as any
      const NSpace = resolveComponent('NSpace') as any
      const btns: any[] = []
      if (row.state === 'dry_run') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'success',
          quaternary: true,
          onClick: () => openDecide(row, 'approved'),
        }, () => '批准'))
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'error',
          quaternary: true,
          onClick: () => openDecide(row, 'rejected'),
        }, () => '拒绝'))
      }
      btns.push(h(NButton, {
        size: 'tiny',
        quaternary: true,
        onClick: () => openDetail(row.candidate_id),
      }, () => '详情'))
      return h(NSpace, { size: 4 }, () => btns)
    },
  },
])
</script>

<template>
  <section class="memory-fold-main">
    <NDataTable
      v-if="filteredCandidates.length > 0"
      :columns="columns"
      :data="filteredCandidates"
      :loading="loading"
      :bordered="false"
      size="small"
      :scroll-x="1200"
      :pagination="{ pageSize: 20 }"
      :row-key="(row: Candidate) => row.candidate_id"
    />
    <EmptyState
      v-else
      title="暂无候选"
      description="Phase C 还未跑出本筛选条件下的候选；可在筛选项放宽后重试。"
      :icon="BulbOutline"
    />
  </section>
</template>

<style scoped>
.memory-fold-main {
  display: grid;
  gap: 12px;
}
</style>

<script setup lang="ts">
import { h } from 'vue'
import { OpenOutline } from '@vicons/ionicons5'
import { NButton, NIcon, NSpace, NTag, NTooltip } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { LearningItem } from '../types'

const props = withDefaults(defineProps<{
  items: LearningItem[]
  loading?: boolean
  hasMore?: boolean
  loadingMore?: boolean
}>(), {
  loading: false,
  hasMore: false,
  loadingMore: false,
})

const emit = defineEmits<{
  openDetail: [item: LearningItem]
  reviewItem: [item: LearningItem]
  loadMore: []
}>()

const columns = computed<DataTableColumns<LearningItem>>(() => [
  {
    title: '类型',
    key: 'kind_label',
    width: 82,
    render: row => h(NTag, { size: 'small', bordered: false }, { default: () => row.kind_label }),
  },
  {
    title: '内容',
    key: 'content',
    minWidth: 260,
    ellipsis: true,
    render: row => h(NTooltip, { trigger: 'hover', placement: 'top-start' }, {
      trigger: () => h('span', { class: 'learning-table__content' }, row.content || '--'),
      default: () => row.content_full || row.content || '--',
    }),
  },
  {
    title: '来源群',
    key: 'group_id',
    width: 92,
    render: row => h('span', { class: 'learning-table__muted' }, row.group_id || '--'),
  },
  {
    title: '时间',
    key: 'created_at',
    width: 136,
    render: row => h('span', { class: 'learning-table__muted' }, formatTime(row.created_at)),
  },
  {
    title: '状态',
    key: 'status_label',
    width: 96,
    render: row => h(NTag, {
      size: 'small',
      bordered: false,
      type: tagType(row.status),
    }, { default: () => row.status_label || row.status }),
  },
  {
    title: '置信',
    key: 'confidence',
    width: 88,
    render: row => h('span', { class: 'learning-table__metric' }, formatConfidence(row.confidence)),
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: row => h(NSpace, { size: 4, wrap: false }, {
      default: () => [
        row.review_drawer
          ? h(NButton, {
              size: 'small',
              secondary: true,
              onClick: () => emit('reviewItem', row),
            }, { default: () => '审核' })
          : null,
        h(NButton, {
          size: 'small',
          quaternary: true,
          onClick: () => emit('openDetail', row),
        }, {
          icon: () => h(NIcon, { component: OpenOutline }),
          default: () => '详情',
        }),
      ],
    }),
  },
])

function tagType(status: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  if (['hit', 'approved', 'enabled_for_prompt', 'active'].includes(status)) return 'success'
  if (['pending', 'candidate', 'dry_run', 'queued'].includes(status)) return 'warning'
  if (['muted', 'expired', 'rejected', 'disabled'].includes(status)) return 'error'
  return 'info'
}

function formatConfidence(value: number | null): string {
  if (value === null || Number.isNaN(Number(value))) return '--'
  return `${Math.round(Number(value) * 100)}%`
}

function formatTime(value: string): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
</script>

<template>
  <div class="learning-table">
    <NDataTable
      v-if="items.length || loading"
      :columns="columns"
      :data="items"
      :loading="loading"
      :pagination="false"
      :row-key="(row) => row.id"
      size="small"
      striped
    />
    <EmptyState
      v-else
      compact
      title="没有匹配项"
      description="当前筛选下没有学习条目。"
    />
    <div v-if="hasMore" class="learning-table__footer">
      <NButton :loading="loadingMore" @click="emit('loadMore')">
        加载更多
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.learning-table {
  display: grid;
  gap: 12px;
}

.learning-table__content {
  display: inline-block;
  overflow: hidden;
  max-width: 100%;
  color: var(--om-text-1);
  text-overflow: ellipsis;
  vertical-align: bottom;
  white-space: nowrap;
}

.learning-table__muted {
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-table__metric {
  color: var(--om-text-2);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}

.learning-table__footer {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}
</style>

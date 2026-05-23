<script setup lang="ts">
import {
  BulbOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  ReloadOutline,
} from '@vicons/ionicons5'
import type { DataTableColumns } from 'naive-ui'
import EmptyState from '../../../../components/common/EmptyState.vue'
import {
  EPISODE_STATE_LABEL,
  EPISODE_STATE_TAG_TYPE,
  type EpisodeItem,
  decayHint,
  useEpisodeConsoleInject,
} from './state'

const console_ = useEpisodeConsoleInject()
const { episodes, loading, openActionDialog, openDetail } = console_

const columns = computed<DataTableColumns<EpisodeItem>>(() => [
  {
    title: '场景',
    key: 'situation',
    minWidth: 240,
    ellipsis: { tooltip: true },
    render: row => h(
      'a',
      {
        style: 'color: var(--om-text-1); cursor: pointer; font-weight: 500',
        onClick: () => openDetail(row.episode_id),
      },
      row.situation || '(未填写)',
    ),
  },
  {
    title: '状态',
    key: 'episode_state',
    width: 120,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: EPISODE_STATE_TAG_TYPE[row.episode_state] || 'default' },
      () => EPISODE_STATE_LABEL[row.episode_state] || row.episode_state,
    ),
  },
  { title: '群', key: 'group_id', width: 120, ellipsis: { tooltip: true } },
  {
    title: '置信度',
    key: 'confidence',
    width: 80,
    render: row => `${Math.round(row.confidence * 100)}%`,
  },
  {
    title: '衰减',
    key: 'decay_at',
    width: 110,
    render: row => h(
      'span',
      { style: row.decay_at ? '' : 'color: var(--om-text-3); font-size: 12px' },
      decayHint(row.decay_at),
    ),
  },
  { title: '更新', key: 'updated_at', width: 160 },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    fixed: 'right',
    render: (row) => {
      const NButton = resolveComponent('NButton') as any
      const NIcon = resolveComponent('NIcon') as any
      const NSpace = resolveComponent('NSpace') as any
      const btns: any[] = []
      if (row.episode_state === 'candidate') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'success',
          quaternary: true,
          onClick: () => openActionDialog(row, 'approve'),
        }, {
          default: () => '批准',
          icon: () => h(NIcon, { component: CheckmarkCircleOutline }),
        }))
      }
      if (row.episode_state !== 'disabled') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'error',
          quaternary: true,
          onClick: () => openActionDialog(row, 'disable'),
        }, {
          default: () => '停用',
          icon: () => h(NIcon, { component: CloseCircleOutline }),
        }))
      }
      if (row.episode_state === 'disabled') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'info',
          quaternary: true,
          onClick: () => openActionDialog(row, 'restore'),
        }, {
          default: () => '恢复',
          icon: () => h(NIcon, { component: ReloadOutline }),
        }))
      }
      btns.push(h(NButton, {
        size: 'tiny',
        quaternary: true,
        onClick: () => openDetail(row.episode_id),
      }, () => '详情'))
      return h(NSpace, { size: 4 }, () => btns)
    },
  },
])
</script>

<template>
  <section class="episode-fold-main">
    <NDataTable
      v-if="episodes.length > 0"
      :columns="columns"
      :data="episodes"
      :loading="loading"
      :bordered="false"
      size="small"
      :scroll-x="1200"
      :pagination="{ pageSize: 20 }"
      :row-key="(row: EpisodeItem) => row.episode_id"
    />
    <EmptyState
      v-else
      title="暂无经验反思"
      description="Bot 完成对话后由 Consolidator 写入；置信度达 0.6 后自动晋升 candidate。"
      :icon="BulbOutline"
    />
  </section>
</template>

<style scoped>
.episode-fold-main {
  display: grid;
  gap: 12px;
}
</style>

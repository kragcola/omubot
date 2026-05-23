<script setup lang="ts">
import { ChevronForwardOutline, OpenOutline, TimeOutline } from '@vicons/ionicons5'
import EmptyState from '../../../components/common/EmptyState.vue'
import type { LearningItem } from '../types'

withDefaults(defineProps<{
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

type StatusTone = 'success' | 'pending' | 'rejected' | 'neutral'

function statusTone(status: string): StatusTone {
  if (['hit', 'approved', 'enabled_for_prompt', 'active'].includes(status)) return 'success'
  if (['pending', 'candidate', 'dry_run', 'queued'].includes(status)) return 'pending'
  if (['muted', 'expired', 'rejected', 'disabled'].includes(status)) return 'rejected'
  return 'neutral'
}

function statusTagType(status: string) {
  const tone = statusTone(status)
  if (tone === 'success') return 'success'
  if (tone === 'pending') return 'warning'
  if (tone === 'rejected') return 'error'
  return 'default'
}

function formatConfidence(value: number | null): string {
  if (value === null || Number.isNaN(Number(value))) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

function formatTime(value: string): string {
  if (!value) return '—'
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
  <div class="learning-list">
    <div v-if="loading && !items.length" class="learning-list__loading">
      <NSkeleton v-for="i in 6" :key="i" :height="64" />
    </div>

    <div v-else-if="items.length" class="learning-list__rows">
      <article
        v-for="item in items"
        :key="item.id"
        class="learning-row"
        :class="`learning-row--${statusTone(item.status)}`"
        @click="emit('openDetail', item)"
      >
        <span class="learning-row__stripe" />
        <div class="learning-row__head">
          <NTag
            class="learning-row__kind"
            size="small"
            :bordered="false"
          >
            {{ item.kind_label }}
          </NTag>
          <h4 class="learning-row__title">
            {{ item.content || '—' }}
          </h4>
        </div>

        <div class="learning-row__meta">
          <span v-if="item.group_id" class="learning-row__chip">
            <span class="learning-row__chip-key">群</span>
            <span class="learning-row__chip-val">{{ item.group_id }}</span>
          </span>
          <span class="learning-row__chip">
            <NIcon :component="TimeOutline" :size="13" />
            {{ formatTime(item.created_at) }}
          </span>
          <span v-if="item.confidence !== null" class="learning-row__chip learning-row__chip--mono">
            置信 {{ formatConfidence(item.confidence) }}
          </span>
        </div>

        <div class="learning-row__tail">
          <NTag
            size="small"
            round
            :bordered="false"
            :type="statusTagType(item.status)"
          >
            {{ item.status_label || item.status }}
          </NTag>
          <div class="learning-row__actions" @click.stop>
            <NButton
              v-if="item.review_drawer"
              size="tiny"
              secondary
              @click="emit('reviewItem', item)"
            >
              审核
            </NButton>
            <NButton
              size="tiny"
              quaternary
              @click="emit('openDetail', item)"
            >
              <template #icon>
                <NIcon :component="OpenOutline" />
              </template>
              详情
            </NButton>
          </div>
          <NIcon class="learning-row__chevron" :component="ChevronForwardOutline" :size="16" />
        </div>
      </article>
    </div>

    <EmptyState
      v-else
      compact
      title="没有匹配项"
      description="当前筛选下没有学习条目。"
    />

    <div v-if="hasMore" class="learning-list__footer">
      <NButton :loading="loadingMore" @click="emit('loadMore')">
        加载更多
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.learning-list {
  display: grid;
  gap: 12px;
}

.learning-list__loading {
  display: grid;
  gap: 8px;
}

.learning-list__rows {
  display: grid;
  gap: 6px;
}

.learning-row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: auto auto;
  grid-template-areas:
    'head tail'
    'meta tail';
  column-gap: 16px;
  row-gap: 6px;
  align-items: center;
  padding: 12px 16px 12px 22px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-solid);
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease, transform 0.16s ease;
}

.learning-row:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 60%, var(--om-surface-solid));
}

.learning-row:hover .learning-row__chevron {
  color: var(--om-text-2);
  transform: translateX(2px);
}

.learning-row__stripe {
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 8px;
  width: 3px;
  border-radius: 2px;
  background: var(--om-text-3);
  opacity: 0.55;
}

.learning-row--success .learning-row__stripe {
  background: var(--om-success);
  opacity: 0.85;
}

.learning-row--pending .learning-row__stripe {
  background: var(--om-warning);
  opacity: 0.85;
}

.learning-row--rejected .learning-row__stripe {
  background: var(--om-danger);
  opacity: 0.7;
}

.learning-row__head {
  grid-area: head;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.learning-row__kind {
  flex-shrink: 0;
  background: color-mix(in srgb, var(--om-text-2) 10%, transparent) !important;
  color: var(--om-text-2) !important;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.learning-row__title {
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.005em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.learning-row__meta {
  grid-area: meta;
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-row__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.learning-row__chip--mono {
  font-variant-numeric: tabular-nums;
}

.learning-row__chip-key {
  color: var(--om-text-3);
  opacity: 0.78;
}

.learning-row__chip-val {
  color: var(--om-text-2);
  font-variant-numeric: tabular-nums;
}

.learning-row__tail {
  grid-area: tail;
  display: flex;
  align-items: center;
  gap: 12px;
}

.learning-row__actions {
  display: flex;
  gap: 4px;
}

.learning-row__chevron {
  flex-shrink: 0;
  color: var(--om-text-3);
  transition: color 0.16s ease, transform 0.16s ease;
}

.learning-list__footer {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}

@media (max-width: 720px) {
  .learning-row {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'head'
      'meta'
      'tail';
    row-gap: 8px;
  }

  .learning-row__tail {
    justify-content: space-between;
  }
}
</style>

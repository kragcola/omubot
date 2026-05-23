<script setup lang="ts">
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

function formatConfidence(value: number | null): string {
  if (value === null || Number.isNaN(Number(value))) return '——'
  return `${Math.round(Number(value) * 100)}`
}

function formatTime(value: string): string {
  if (!value) return '——'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date).replace(/\//g, '-')
}

function shortGroup(value: string): string {
  if (!value) return '——'
  if (value.length <= 6) return value
  return `…${value.slice(-5)}`
}

function statusGlyph(tone: StatusTone): string {
  if (tone === 'success') return '✓'
  if (tone === 'pending') return '·'
  if (tone === 'rejected') return '✕'
  return '·'
}
</script>

<template>
  <div class="lt">
    <div v-if="loading && !items.length" class="lt-loading">
      <NSkeleton v-for="i in 18" :key="i" :height="26" :sharp="true" />
    </div>

    <template v-else-if="items.length">
      <header class="lt-head">
        <span class="lt-col lt-col--dot" />
        <span class="lt-col lt-col--kind">类型</span>
        <span class="lt-col lt-col--title">条目</span>
        <span class="lt-col lt-col--group">群</span>
        <span class="lt-col lt-col--time">时间</span>
        <span class="lt-col lt-col--conf">置信</span>
        <span class="lt-col lt-col--status">状态</span>
        <span class="lt-col lt-col--act" />
      </header>

      <div class="lt-body">
        <button
          v-for="item in items"
          :key="item.id"
          type="button"
          class="lt-row"
          :class="`lt-row--${statusTone(item.status)}`"
          @click="emit('openDetail', item)"
        >
          <span
            class="lt-col lt-col--dot"
            :title="item.status_label || item.status"
          >{{ statusGlyph(statusTone(item.status)) }}</span>
          <span class="lt-col lt-col--kind">{{ item.kind_label }}</span>
          <span class="lt-col lt-col--title" :title="item.content_full || item.content">
            {{ item.content || '——' }}
          </span>
          <span class="lt-col lt-col--group" :title="item.group_id">{{ shortGroup(item.group_id) }}</span>
          <span class="lt-col lt-col--time">{{ formatTime(item.created_at) }}</span>
          <span class="lt-col lt-col--conf">{{ formatConfidence(item.confidence) }}</span>
          <span class="lt-col lt-col--status">{{ item.status_label || item.status }}</span>
          <span class="lt-col lt-col--act" @click.stop>
            <button
              v-if="item.review_drawer"
              type="button"
              class="lt-act"
              @click="emit('reviewItem', item)"
            >审</button>
            <button
              type="button"
              class="lt-act"
              @click="emit('openDetail', item)"
            >详</button>
          </span>
        </button>
      </div>
    </template>

    <EmptyState
      v-else
      compact
      title="没有匹配项"
      description="当前筛选下没有学习条目。"
    />

    <div v-if="hasMore" class="lt-footer">
      <NButton size="tiny" :loading="loadingMore" @click="emit('loadMore')">加载更多</NButton>
    </div>
  </div>
</template>

<style scoped>
.lt {
  display: grid;
  gap: 0;
  font-feature-settings: 'tnum' 1;
}

.lt-loading {
  display: grid;
  gap: 1px;
}

.lt-head,
.lt-row {
  display: grid;
  grid-template-columns:
    20px              /* dot */
    44px              /* kind */
    minmax(0, 1fr)    /* title */
    72px              /* group */
    72px              /* time */
    36px              /* conf */
    52px              /* status */
    auto;             /* act */
  align-items: center;
  column-gap: 12px;
  padding: 0 12px;
  font-size: 12px;
  line-height: 1;
}

.lt-head {
  position: sticky;
  top: 0;
  z-index: 1;
  height: 24px;
  color: var(--om-text-3);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  background: color-mix(in srgb, var(--om-surface-2) 70%, var(--om-surface-solid));
  border-bottom: 1px solid var(--om-border);
}

.lt-body {
  display: grid;
  gap: 0;
}

.lt-row {
  position: relative;
  height: 26px;
  border: 0;
  border-bottom: 1px solid color-mix(in srgb, var(--om-border) 55%, transparent);
  background: transparent;
  color: var(--om-text-1);
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.lt-row:hover {
  background: color-mix(in srgb, var(--om-surface-2) 60%, transparent);
}

.lt-row:focus-visible {
  outline: none;
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
}

.lt-col {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.lt-col--dot {
  justify-self: center;
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: var(--om-text-3);
}

.lt-row--success .lt-col--dot { color: var(--om-success); }
.lt-row--pending .lt-col--dot { color: var(--om-warning); }
.lt-row--rejected .lt-col--dot { color: var(--om-danger); }

.lt-col--kind {
  color: var(--om-text-3);
  font-size: 11px;
  letter-spacing: 0.02em;
}

.lt-col--title {
  color: var(--om-text-1);
  font-weight: 500;
}

.lt-col--group,
.lt-col--time,
.lt-col--conf {
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.lt-col--conf::after {
  content: '%';
  margin-left: 1px;
  color: var(--om-text-3);
  opacity: 0.55;
}

.lt-col--status {
  color: var(--om-text-2);
  font-size: 11px;
  text-align: right;
}

.lt-row--success .lt-col--status { color: var(--om-success); }
.lt-row--pending .lt-col--status { color: var(--om-warning); }
.lt-row--rejected .lt-col--status { color: var(--om-danger); }

.lt-col--act {
  display: inline-flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.lt-row:hover .lt-col--act,
.lt-row:focus-visible .lt-col--act {
  opacity: 1;
}

.lt-act {
  height: 18px;
  padding: 0 6px;
  border: 1px solid color-mix(in srgb, var(--om-border-strong) 70%, transparent);
  border-radius: 3px;
  background: var(--om-surface-solid);
  color: var(--om-text-2);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.1s ease, color 0.1s ease;
}

.lt-act:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
}

.lt-footer {
  display: flex;
  justify-content: center;
  padding: 8px 0 0;
}

@media (max-width: 720px) {
  .lt-head,
  .lt-row {
    grid-template-columns: 20px 44px minmax(0, 1fr) 72px 36px;
    column-gap: 8px;
  }
  .lt-col--time,
  .lt-col--status,
  .lt-col--act {
    display: none;
  }
}
</style>

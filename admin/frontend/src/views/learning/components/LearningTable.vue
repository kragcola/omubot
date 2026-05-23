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

function formatConfidence(value: number | null): string | null {
  if (value === null || Number.isNaN(Number(value))) return null
  return `${Math.round(Number(value) * 100)}%`
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
  if (value.length <= 8) return value
  return `…${value.slice(-6)}`
}
</script>

<template>
  <div class="lt">
    <div v-if="loading && !items.length" class="lt-list lt-list--loading">
      <div v-for="i in 8" :key="i" class="lt-skeleton">
        <NSkeleton :width="36" :height="14" :sharp="false" />
        <NSkeleton :height="14" :sharp="false" style="flex: 1; margin: 0 12px" />
        <NSkeleton :width="80" :height="12" :sharp="false" />
      </div>
    </div>

    <template v-else-if="items.length">
      <div class="lt-list">
        <div
          v-for="item in items"
          :key="item.id"
          class="lt-row"
          :class="`lt-row--${statusTone(item.status)}`"
          tabindex="0"
          role="button"
          @click="emit('openDetail', item)"
          @keydown.enter.prevent="emit('openDetail', item)"
          @keydown.space.prevent="emit('openDetail', item)"
        >
          <span class="lt-row__kind">{{ item.kind_label }}</span>
          <span class="lt-row__content" :title="item.content_full || item.content">
            {{ item.content || '——' }}
          </span>
          <span class="lt-row__group" :title="item.group_id">
            {{ shortGroup(item.group_id) }}
          </span>
          <span class="lt-row__time">{{ formatTime(item.created_at) }}</span>
          <span
            v-if="formatConfidence(item.confidence)"
            class="lt-row__conf"
          >
            {{ formatConfidence(item.confidence) }}
          </span>
          <span v-else class="lt-row__conf lt-row__conf--empty">——</span>
          <span class="lt-row__status">{{ item.status_label || item.status }}</span>
          <span class="lt-row__actions" @click.stop>
            <button
              v-if="item.review_drawer"
              type="button"
              class="lt-row__act"
              @click="emit('reviewItem', item)"
            >审核</button>
            <button
              type="button"
              class="lt-row__act lt-row__act--ghost"
              @click="emit('openDetail', item)"
            >详情</button>
          </span>
        </div>
      </div>
    </template>

    <EmptyState
      v-else
      compact
      title="没有匹配项"
      description="当前筛选下没有学习条目。"
    />

    <div v-if="hasMore" class="lt-footer">
      <NButton size="small" :loading="loadingMore" @click="emit('loadMore')">加载更多</NButton>
    </div>
  </div>
</template>

<style scoped>
.lt {
  display: grid;
  gap: 12px;
  font-feature-settings: 'tnum' 1;
}

.lt-list {
  display: grid;
  gap: 8px;
}

.lt-skeleton {
  display: flex;
  align-items: center;
  padding: 10px 14px 10px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
}

.lt-row {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto 50px auto auto;
  align-items: center;
  gap: 12px;
  padding: 10px 14px 10px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  color: var(--om-text-1);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.lt-row::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.lt-row--success::before { background: var(--om-success); opacity: 1; }
.lt-row--pending::before { background: var(--om-warning); opacity: 1; }
.lt-row--rejected::before { background: var(--om-danger); opacity: 0.85; }

.lt-row:hover,
.lt-row:focus-visible {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  outline: none;
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.lt-row__kind {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--om-text-3) 14%, transparent);
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.lt-row__content {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: break-word;
}

.lt-row__group {
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  max-width: 9ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lt-row__time {
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.lt-row__conf {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 2px;
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
  color: var(--om-text-2);
  font-size: 10.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 36px;
  text-align: center;
}

.lt-row__conf--empty {
  background: transparent;
  color: var(--om-text-3);
  font-weight: 400;
}

.lt-row__status {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--om-text-3);
  white-space: nowrap;
}

.lt-row--success .lt-row__status { color: var(--om-success); }
.lt-row--pending .lt-row__status { color: var(--om-warning); }
.lt-row--rejected .lt-row__status { color: var(--om-danger); }

.lt-row__actions {
  display: inline-flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.lt-row:hover .lt-row__actions,
.lt-row:focus-visible .lt-row__actions,
.lt-row:focus-within .lt-row__actions {
  opacity: 1;
}

.lt-row__act {
  height: 22px;
  padding: 0 9px;
  border: 1px solid color-mix(in srgb, var(--om-border-strong) 70%, transparent);
  border-radius: 4px;
  background: var(--om-surface-solid);
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}

.lt-row__act:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
  border-color: var(--om-border-strong);
}

.lt-row__act--ghost {
  background: transparent;
  color: var(--om-text-3);
}

.lt-row__act--ghost:hover {
  background: color-mix(in srgb, var(--om-surface-2) 60%, transparent);
  color: var(--om-text-2);
}

.lt-footer {
  display: flex;
  justify-content: center;
  padding: 4px 0 0;
}

@media (max-width: 1100px) {
  .lt-row {
    grid-template-columns: auto minmax(0, 1fr) auto 50px auto auto;
  }
  .lt-row__group {
    display: none;
  }
}

@media (max-width: 720px) {
  .lt-row {
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    gap: 8px;
  }
  .lt-row__time,
  .lt-row__conf {
    display: none;
  }
  .lt-row__actions {
    opacity: 1;
  }
}
</style>

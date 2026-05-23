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
    <div v-if="loading && !items.length" class="lt-grid lt-grid--loading">
      <div v-for="i in 8" :key="i" class="lt-skeleton">
        <NSkeleton :width="56" :height="14" :sharp="false" />
        <NSkeleton :height="16" :sharp="false" style="margin-top: 10px" />
        <NSkeleton :width="180" :height="14" :sharp="false" style="margin-top: 6px" />
        <NSkeleton :width="120" :height="12" :sharp="false" style="margin-top: 14px" />
      </div>
    </div>

    <template v-else-if="items.length">
      <div class="lt-grid">
        <article
          v-for="item in items"
          :key="item.id"
          class="lt-card"
          :class="`lt-card--${statusTone(item.status)}`"
          tabindex="0"
          role="button"
          @click="emit('openDetail', item)"
          @keydown.enter.prevent="emit('openDetail', item)"
          @keydown.space.prevent="emit('openDetail', item)"
        >
          <header class="lt-card__head">
            <span class="lt-card__kind">{{ item.kind_label }}</span>
            <span class="lt-card__status">{{ item.status_label || item.status }}</span>
          </header>

          <p class="lt-card__title" :title="item.content_full || item.content">
            {{ item.content || '——' }}
          </p>

          <footer class="lt-card__foot">
            <span class="lt-card__meta lt-card__meta--group" :title="item.group_id">
              {{ shortGroup(item.group_id) }}
            </span>
            <span class="lt-card__meta lt-card__meta--time">{{ formatTime(item.created_at) }}</span>
            <span
              v-if="formatConfidence(item.confidence)"
              class="lt-card__meta lt-card__meta--conf"
            >
              {{ formatConfidence(item.confidence) }}
            </span>
            <span class="lt-card__actions" @click.stop>
              <button
                v-if="item.review_drawer"
                type="button"
                class="lt-card__act"
                @click="emit('reviewItem', item)"
              >审核</button>
              <button
                type="button"
                class="lt-card__act lt-card__act--ghost"
                @click="emit('openDetail', item)"
              >详情</button>
            </span>
          </footer>
        </article>
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

.lt-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
}

.lt-skeleton {
  display: block;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  min-height: 120px;
}

.lt-card {
  position: relative;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 8px;
  padding: 12px 14px;
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

.lt-card::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.lt-card--success::before { background: var(--om-success); opacity: 1; }
.lt-card--pending::before { background: var(--om-warning); opacity: 1; }
.lt-card--rejected::before { background: var(--om-danger); opacity: 0.85; }

.lt-card:hover,
.lt-card:focus-visible {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  outline: none;
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.lt-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 18px;
}

.lt-card__kind {
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
}

.lt-card__status {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--om-text-3);
}

.lt-card--success .lt-card__status { color: var(--om-success); }
.lt-card--pending .lt-card__status { color: var(--om-warning); }
.lt-card--rejected .lt-card__status { color: var(--om-danger); }

.lt-card__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 13.5px;
  font-weight: 500;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.lt-card__foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 6px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 70%, transparent);
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  min-height: 26px;
}

.lt-card__meta {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lt-card__meta--group {
  max-width: 9ch;
}

.lt-card__meta--time {
  color: var(--om-text-3);
}

.lt-card__meta--conf {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 2px;
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
  color: var(--om-text-2);
  font-size: 10.5px;
  font-weight: 600;
}

.lt-card__actions {
  margin-left: auto;
  display: inline-flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.lt-card:hover .lt-card__actions,
.lt-card:focus-visible .lt-card__actions,
.lt-card:focus-within .lt-card__actions {
  opacity: 1;
}

.lt-card__act {
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

.lt-card__act:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
  border-color: var(--om-border-strong);
}

.lt-card__act--ghost {
  background: transparent;
  color: var(--om-text-3);
}

.lt-card__act--ghost:hover {
  background: color-mix(in srgb, var(--om-surface-2) 60%, transparent);
  color: var(--om-text-2);
}

.lt-footer {
  display: flex;
  justify-content: center;
  padding: 4px 0 0;
}

@media (max-width: 720px) {
  .lt-grid {
    grid-template-columns: 1fr;
  }
  .lt-card__actions {
    opacity: 1;
  }
}
</style>

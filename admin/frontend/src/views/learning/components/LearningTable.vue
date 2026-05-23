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
  if (value.length <= 6) return value
  return `…${value.slice(-5)}`
}

function preview(item: LearningItem): string {
  const text = item.content_full || item.content || ''
  if (!text) return '暂无内容预览'
  if (text === item.content) return text
  return text
}
</script>

<template>
  <div class="lg">
    <div v-if="loading && !items.length" class="lg-grid">
      <NSkeleton v-for="i in 12" :key="i" :height="124" :sharp="false" :border-radius="10" />
    </div>

    <div v-else-if="items.length" class="lg-grid">
      <article
        v-for="item in items"
        :key="item.id"
        class="card"
        :class="`card--${statusTone(item.status)}`"
        tabindex="0"
        @click="emit('openDetail', item)"
        @keyup.enter="emit('openDetail', item)"
      >
        <header class="card-top">
          <span class="card-kind">{{ item.kind_label }}</span>
          <span class="card-meta-inline">
            <span class="card-time">{{ formatTime(item.created_at) }}</span>
            <span v-if="item.confidence !== null" class="card-conf">{{ formatConfidence(item.confidence) }}</span>
          </span>
        </header>

        <h4 class="card-title" :title="item.content">{{ item.content || '——' }}</h4>

        <p class="card-body" :title="preview(item)">{{ preview(item) }}</p>

        <footer class="card-bottom" @click.stop>
          <span class="card-group" :title="item.group_id">
            <span class="card-group-key">群</span>
            <span class="card-group-val">{{ shortGroup(item.group_id) }}</span>
          </span>
          <span class="card-status">{{ item.status_label || item.status }}</span>
          <span class="card-acts">
            <button
              v-if="item.review_drawer"
              type="button"
              class="card-act card-act--primary"
              @click="emit('reviewItem', item)"
            >审核</button>
            <button
              type="button"
              class="card-act"
              @click="emit('openDetail', item)"
            >详情</button>
          </span>
        </footer>
      </article>
    </div>

    <EmptyState
      v-else
      compact
      title="没有匹配项"
      description="当前筛选下没有学习条目。"
    />

    <div v-if="hasMore" class="lg-footer">
      <NButton size="small" :loading="loadingMore" @click="emit('loadMore')">加载更多</NButton>
    </div>
  </div>
</template>

<style scoped>
.lg {
  display: grid;
  gap: 12px;
  font-feature-settings: 'tnum' 1;
}

.lg-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 10px;
}

.card {
  position: relative;
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: 6px;
  min-height: 124px;
  padding: 10px 12px 10px 16px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  cursor: pointer;
  text-align: left;
  overflow: hidden;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.card::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 6px;
  width: 3px;
  border-radius: 2px;
  background: var(--om-text-3);
  opacity: 0.5;
  transition: opacity 0.16s ease, background-color 0.16s ease;
}

.card--success::before { background: var(--om-success); opacity: 0.85; }
.card--pending::before { background: var(--om-warning); opacity: 0.85; }
.card--rejected::before { background: var(--om-danger); opacity: 0.7; }

.card:hover,
.card:focus-visible {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 50%, var(--om-surface-solid));
  box-shadow: 0 1px 0 0 color-mix(in srgb, var(--om-border) 60%, transparent);
  outline: none;
}

.card:hover::before,
.card:focus-visible::before {
  opacity: 1;
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 18px;
}

.card-kind {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--om-text-2) 10%, transparent);
  color: var(--om-text-2);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.card-meta-inline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.card-conf {
  color: var(--om-text-2);
  font-weight: 500;
}

.card-title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.005em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-body {
  margin: 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-word;
}

.card-bottom {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 65%, transparent);
  font-size: 11px;
}

.card-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--om-text-3);
  font-variant-numeric: tabular-nums;
}

.card-group-key {
  opacity: 0.7;
}

.card-group-val {
  color: var(--om-text-2);
}

.card-status {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--om-text-3) 12%, transparent);
  color: var(--om-text-2);
  font-size: 10.5px;
  font-weight: 500;
}

.card--success .card-status {
  background: color-mix(in srgb, var(--om-success) 14%, transparent);
  color: var(--om-success);
}
.card--pending .card-status {
  background: color-mix(in srgb, var(--om-warning) 14%, transparent);
  color: var(--om-warning);
}
.card--rejected .card-status {
  background: color-mix(in srgb, var(--om-danger) 14%, transparent);
  color: var(--om-danger);
}

.card-acts {
  display: inline-flex;
  gap: 4px;
  opacity: 0;
  transform: translateX(4px);
  transition: opacity 0.14s ease, transform 0.14s ease;
}

.card:hover .card-acts,
.card:focus-visible .card-acts {
  opacity: 1;
  transform: translateX(0);
}

.card-act {
  height: 20px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--om-border-strong) 70%, transparent);
  border-radius: 4px;
  background: var(--om-surface-solid);
  color: var(--om-text-2);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.1s ease, color 0.1s ease, border-color 0.1s ease;
}

.card-act:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
}

.card-act--primary {
  border-color: color-mix(in srgb, var(--om-info) 50%, transparent);
  color: var(--om-info);
}

.card-act--primary:hover {
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
  color: var(--om-info);
}

.lg-footer {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}

@media (max-width: 720px) {
  .lg-grid {
    grid-template-columns: 1fr;
  }
  .card-acts {
    opacity: 1;
    transform: none;
  }
}
</style>

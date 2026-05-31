<script setup lang="ts">
import { computed } from 'vue'
import type { ActivityEvent } from '../useLearningConsole'

const props = defineProps<{ events: ActivityEvent[] }>()

function formatTime(ts: string): string {
  if (!ts) return ''
  if (ts.length >= 16) return ts.slice(11, 16)
  return ts
}

function formatDate(ts: string): string {
  if (!ts || ts.length < 10) return ''
  const today = new Date().toISOString().slice(0, 10)
  const d = ts.slice(0, 10)
  if (d === today) return '今天'
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  if (d === yesterday) return '昨天'
  return d.slice(5)
}

const typeConfig: Record<string, { label: string; color: string }> = {
  extract: { label: '提取', color: 'var(--om-info)' },
  approve: { label: '通过', color: 'var(--om-success)' },
  review: { label: '审核', color: 'var(--om-warning)' },
  hit: { label: '命中', color: 'rgb(var(--primary-color))' },
  archive: { label: '归档', color: 'var(--om-text-3)' },
}

interface GroupedEvents {
  date: string
  items: ActivityEvent[]
}

const grouped = computed<GroupedEvents[]>(() => {
  const map: Record<string, ActivityEvent[]> = {}
  for (const ev of props.events) {
    const d = formatDate(ev.time)
    if (!map[d]) map[d] = []
    map[d].push(ev)
  }
  return Object.entries(map).map(([date, items]) => ({ date, items }))
})
</script>

<template>
  <AppCard bordered elevated class="feed">
    <div class="feed__header">
      <p class="feed__title">最近活动</p>
      <span v-if="events.length" class="feed__count">{{ events.length }} 条</span>
    </div>
    <div v-if="events.length > 0" class="feed__list">
      <div v-for="group in grouped" :key="group.date" class="feed__group">
        <div class="feed__date">{{ group.date }}</div>
        <div v-for="(ev, i) in group.items" :key="i" class="feed__row">
          <span class="feed__time">{{ formatTime(ev.time) }}</span>
          <span
            class="feed__badge"
            :style="{ '--badge-color': typeConfig[ev.type]?.color || 'var(--om-text-3)' }"
          >
            {{ typeConfig[ev.type]?.label || ev.type }}
          </span>
          <span class="feed__msg">{{ ev.message }}</span>
        </div>
      </div>
    </div>
    <div v-else class="feed__empty">
      <p>暂无活动记录</p>
      <small>词条提取、审核、命中等事件将在此显示</small>
    </div>
  </AppCard>
</template>

<style scoped>
.feed {
  padding: 0;
}

.feed__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 0;
}

.feed__title {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.feed__count {
  font-size: 11px;
  color: var(--om-text-3);
}

.feed__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
  padding: 12px 20px 16px;
}

.feed__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.feed__date {
  font-size: 11px;
  font-weight: 600;
  color: var(--om-text-3);
  letter-spacing: 0.04em;
  padding: 8px 0 4px;
}

.feed__group:first-child .feed__date {
  padding-top: 0;
}

.feed__row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
  transition: background 0.12s;
}

.feed__row:hover {
  background: var(--om-fill);
}

.feed__time {
  flex-shrink: 0;
  width: 38px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--om-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.feed__badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--badge-color);
  background: color-mix(in srgb, var(--badge-color) 10%, transparent);
}

.feed__msg {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--om-text-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feed__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 20px;
  text-align: center;
}

.feed__empty p {
  margin: 0;
  color: var(--om-text-3);
  font-size: 13px;
}

.feed__empty small {
  color: var(--om-text-3);
  font-size: 12px;
  opacity: 0.7;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import type { TrendPoint } from '../useLearningConsole'

const props = defineProps<{ points: TrendPoint[] }>()

const hasData = computed(() => props.points.some(p => p.candidate > 0 || p.approved > 0 || p.hits > 0))
const todayCandidate = computed(() => props.points.length ? props.points[props.points.length - 1].candidate : 0)
const todayApproved = computed(() => props.points.length ? props.points[props.points.length - 1].approved : 0)
const todayHits = computed(() => props.points.length ? props.points[props.points.length - 1].hits : 0)

const maxVal = computed(() => {
  let m = 1
  for (const p of props.points) m = Math.max(m, p.candidate, p.approved, p.hits)
  return m
})

interface BarGroup {
  label: string
  bars: { color: string; pct: number; value: number }[]
}

const barGroups = computed<BarGroup[]>(() => {
  return props.points.map(p => ({
    label: p.date.slice(5),
    bars: [
      { color: 'var(--om-info)', pct: p.candidate / maxVal.value, value: p.candidate },
      { color: 'var(--om-success)', pct: p.approved / maxVal.value, value: p.approved },
      { color: 'rgb(var(--primary-color))', pct: p.hits / maxVal.value, value: p.hits },
    ],
  }))
})

// Text alternative for screen readers (data-table fallback in sentence form)
const chartSummary = computed(() => {
  if (!hasData.value) return '7 日趋势：暂无数据'
  const days = props.points
    .map(p => `${p.date.slice(5)} 候选 ${p.candidate}、生效 ${p.approved}、命中 ${p.hits}`)
    .join('；')
  return `7 日趋势柱状图，按候选、生效、命中三项统计。${days}。`
})
</script>

<template>
  <AppCard bordered elevated class="trend-chart">
    <div class="trend-chart__header">
      <p class="trend-chart__title">7 日趋势</p>
      <div v-if="hasData" class="trend-chart__summary">
        <span class="trend-chart__stat trend-chart__stat--candidate">
          <span class="trend-chart__stat-dot" />
          候选 +{{ todayCandidate }}
        </span>
        <span class="trend-chart__stat trend-chart__stat--approved">
          <span class="trend-chart__stat-dot" />
          生效 +{{ todayApproved }}
        </span>
        <span class="trend-chart__stat trend-chart__stat--hits">
          <span class="trend-chart__stat-dot" />
          命中 {{ todayHits }}
        </span>
      </div>
    </div>
    <div v-if="hasData" class="trend-chart__body">
      <div class="trend-chart__plot" role="img" :aria-label="chartSummary">
        <div
          v-for="group in barGroups"
          :key="group.label"
          class="trend-chart__group"
          aria-hidden="true"
        >
          <div class="trend-chart__bars">
            <div
              v-for="(bar, i) in group.bars"
              :key="i"
              class="trend-chart__bar"
              :style="{ height: `${Math.max(bar.pct * 100, bar.value > 0 ? 2 : 0)}%`, background: bar.color }"
              :title="`${bar.value}`"
            />
          </div>
          <div class="trend-chart__label">{{ group.label }}</div>
        </div>
      </div>
    </div>
    <div v-else class="trend-chart__empty">
      <p>暂无趋势数据</p>
    </div>
  </AppCard>
</template>

<style scoped>
.trend-chart {
  padding: 20px;
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
}

.trend-chart__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-shrink: 0;
}

.trend-chart__title {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.trend-chart__summary {
  display: flex;
  gap: 16px;
}

.trend-chart__stat {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.trend-chart__stat--candidate { color: var(--om-info); }
.trend-chart__stat--approved { color: var(--om-success); }
.trend-chart__stat--hits { color: rgb(var(--primary-color)); }

.trend-chart__stat-dot {
  width: 8px;
  height: 3px;
  border-radius: 2px;
  background: currentColor;
}

.trend-chart__body {
  flex: 1;
  display: flex;
  min-height: 200px;
}

.trend-chart__plot {
  flex: 1;
  display: flex;
  align-items: stretch;
  gap: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--om-border);
}

.trend-chart__group {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.trend-chart__bars {
  flex: 1;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 3px;
  padding-bottom: 6px;
}

.trend-chart__bar {
  flex: 1;
  min-width: 4px;
  max-width: 14px;
  border-radius: 3px 3px 0 0;
  transition: opacity 0.15s ease;
  min-height: 0;
}

.trend-chart__bar:hover {
  opacity: 0.8;
}

.trend-chart__label {
  text-align: center;
  margin-top: 6px;
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.trend-chart__empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.trend-chart__empty p {
  margin: 0;
  color: var(--om-text-3);
  font-size: 13px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'

import { cacheHitColor, formatHitPct } from '../../system/helpers/formatters'

export interface BarItem {
  // Short label shown beneath the bar. Kept ≤ 4 chars by callers
  // (Chinese task display names) so it never wraps under a column.
  label: string
  value: number | null
  // Optional secondary text used by tooltip — call count, source task, etc.
  badge?: string | null
  highlight?: boolean
  // Pure layout slot. Renders an empty grid cell so adjacent cards keep
  // matching bar widths even when their bar counts differ.
  placeholder?: boolean
}

interface Props {
  bars: BarItem[]
  axisLabel?: string
  emptyText?: string
  height?: number
}

const props = withDefaults(defineProps<Props>(), {
  axisLabel: '',
  emptyText: '暂无数据',
  height: 64,
})

const isEmpty = computed(() => {
  if (props.bars.length === 0) return true
  return props.bars.every(b => b.placeholder)
})

function barHeightPct(value: number | null | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(100, value * 100))
}

function tooltipText(bar: BarItem): string {
  const pct = formatHitPct(bar.value)
  return bar.badge ? `${bar.label}: ${pct} (${bar.badge})` : `${bar.label}: ${pct}`
}
</script>

<template>
  <figure class="bar-column-group">
    <figcaption v-if="axisLabel" class="bar-column-group__caption">
      {{ axisLabel }}
    </figcaption>
    <div
      v-if="isEmpty"
      class="bar-column-group__empty"
      :style="{ height: `${height + 18}px` }"
    >
      {{ emptyText }}
    </div>
    <div
      v-else
      class="bar-column-group__grid"
      :style="{
        gridTemplateColumns: `repeat(${bars.length}, minmax(0, 1fr))`,
      }"
    >
      <div
        v-for="(bar, i) in bars"
        :key="i"
        class="bar-column-group__col"
        :class="{ 'bar-column-group__col--placeholder': bar.placeholder }"
      >
        <div
          class="bar-column-group__track"
          :style="{ height: `${height}px` }"
          :title="bar.placeholder ? '' : tooltipText(bar)"
        >
          <div
            v-if="!bar.placeholder"
            class="bar-column-group__fill"
            :class="{
              'bar-column-group__fill--null': bar.value === null,
              'bar-column-group__fill--highlight': bar.highlight,
            }"
            :style="{
              height: `${barHeightPct(bar.value)}%`,
              background: bar.value === null ? undefined : cacheHitColor(bar.value),
            }"
          />
        </div>
        <span
          class="bar-column-group__label"
          :class="{ 'bar-column-group__label--highlight': bar.highlight }"
        >
          {{ bar.placeholder ? '' : bar.label }}
        </span>
      </div>
    </div>
  </figure>
</template>

<style scoped>
.bar-column-group {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.bar-column-group__caption {
  font-size: 11px;
  color: var(--om-text-3);
  letter-spacing: 0.02em;
}

.bar-column-group__grid {
  display: grid;
  column-gap: 4px;
  align-items: end;
}

.bar-column-group__col {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
  min-width: 0;
}

.bar-column-group__track {
  position: relative;
  background: rgba(49, 108, 114, 0.06);
  border-radius: 3px;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
}

.bar-column-group__fill {
  width: 100%;
  border-radius: 3px;
  transition: height 200ms ease;
  min-height: 2px;
}

.bar-column-group__fill--null {
  height: 2px !important;
  background: rgba(49, 108, 114, 0.18);
}

.bar-column-group__fill--highlight {
  outline: 1px solid rgba(255, 255, 255, 0.55);
  outline-offset: -1px;
}

.bar-column-group__col--placeholder .bar-column-group__track {
  background: transparent;
}

.bar-column-group__label {
  font-size: 10px;
  line-height: 1.2;
  color: var(--om-text-3);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-height: 12px;
}

.bar-column-group__label--highlight {
  color: var(--om-text-1);
  font-weight: 600;
}

.bar-column-group__empty {
  font-size: 11px;
  color: var(--om-text-3);
  background: rgba(49, 108, 114, 0.04);
  border-radius: 6px;
  padding: 12px 8px;
  text-align: center;
  line-height: 1.4;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>

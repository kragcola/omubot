<script setup lang="ts">
const props = withDefaults(defineProps<{
  values: number[]
  labels?: string[]
  height?: number
  color?: string
  fillOpacity?: number
  showLastLabel?: boolean
  min?: number
  max?: number
}>(), {
  labels: () => [],
  height: 72,
  color: 'rgb(var(--primary-color))',
  fillOpacity: 0.14,
  showLastLabel: true,
  min: undefined,
  max: undefined,
})

const width = 600
const padX = 4
const padTop = 6
const padBottom = 18

const bounds = computed(() => {
  const vals = props.values
  if (vals.length === 0) return { min: 0, max: 1 }
  const minValue = props.min ?? Math.min(...vals, 0)
  const maxValue = props.max ?? Math.max(...vals, 1)
  return {
    min: minValue,
    max: maxValue === minValue ? minValue + 1 : maxValue,
  }
})

const innerW = width - padX * 2
const innerH = computed(() => props.height - padTop - padBottom)

const points = computed(() => {
  const vals = props.values
  if (vals.length === 0) return [] as Array<{ x: number, y: number, value: number }>
  const step = vals.length > 1 ? innerW / (vals.length - 1) : 0
  return vals.map((v, i) => {
    const clamped = Math.max(bounds.value.min, Math.min(bounds.value.max, v))
    const pct = (clamped - bounds.value.min) / (bounds.value.max - bounds.value.min)
    return {
      x: padX + (vals.length > 1 ? i * step : innerW / 2),
      y: padTop + innerH.value - pct * innerH.value,
      value: v,
    }
  })
})

const linePath = computed(() => {
  if (points.value.length === 0) return ''
  return points.value.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ')
})

const areaPath = computed(() => {
  if (points.value.length === 0) return ''
  const base = padTop + innerH.value
  const first = points.value[0]
  const last = points.value[points.value.length - 1]
  const inner = points.value.map(p => `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ')
  return `M ${first.x.toFixed(2)} ${base} ${inner} L ${last.x.toFixed(2)} ${base} Z`
})

const ticks = computed(() => {
  if (props.labels.length === 0) return []
  const step = Math.ceil(props.labels.length / 6)
  return props.labels
    .map((label, i) => ({ label, i }))
    .filter(({ i }) => i === 0 || i === props.labels.length - 1 || i % step === 0)
})

const total = computed(() => props.values.reduce((a, b) => a + b, 0))
const peak = computed(() => {
  if (props.values.length === 0) return null
  let maxVal = -Infinity
  let maxIdx = 0
  props.values.forEach((v, i) => {
    if (v > maxVal) {
      maxVal = v
      maxIdx = i
    }
  })
  return {
    value: maxVal,
    label: props.labels[maxIdx] ?? String(maxIdx),
  }
})
</script>

<template>
  <div class="sparkline" :style="{ height: `${height}px` }">
    <svg
      v-if="values.length > 0"
      class="sparkline__svg"
      :viewBox="`0 0 ${width} ${height}`"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient :id="`sparkline-grad-${color}`" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="color" :stop-opacity="fillOpacity * 2" />
          <stop offset="100%" :stop-color="color" :stop-opacity="0" />
        </linearGradient>
      </defs>
      <path :d="areaPath" :fill="`url(#sparkline-grad-${color})`" />
      <path
        :d="linePath"
        fill="none"
        :stroke="color"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
        vector-effect="non-scaling-stroke"
      />
      <circle
        v-if="points.length > 0"
        :cx="points[points.length - 1].x"
        :cy="points[points.length - 1].y"
        r="2.8"
        :fill="color"
      />
      <g class="sparkline__ticks">
        <text
          v-for="t in ticks"
          :key="t.label"
          :x="padX + (values.length > 1 ? (t.i * innerW) / (values.length - 1) : innerW / 2)"
          :y="height - 4"
          text-anchor="middle"
        >
          {{ t.label }}
        </text>
      </g>
    </svg>
    <div v-else class="sparkline__empty">
      暂无数据
    </div>
    <div v-if="showLastLabel && values.length > 0" class="sparkline__stats">
      <span>累计 <strong>{{ total }}</strong></span>
      <span v-if="peak">峰值 <strong>{{ peak.value }}</strong> @ {{ peak.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.sparkline {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  width: 100%;
}

.sparkline__svg {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
}

.sparkline__ticks text {
  fill: var(--om-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9px;
}

.sparkline__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--om-text-3);
  font-size: 12px;
}

.sparkline__stats {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.sparkline__stats strong {
  color: var(--om-text-1);
  font-weight: 600;
  margin: 0 2px;
}
</style>

<script setup lang="ts">
import { NButton, NProgress, NTag } from 'naive-ui'
import { computed } from 'vue'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import StateBadge from '../../../components/common/StateBadge.vue'
import { cacheHitColor, formatHitPct } from '../../system/helpers/formatters'

export interface CachePipelineTaskMetric {
  task: string
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
}

export interface CachePipelineGroup {
  key: 'core_chat' | 'slang' | 'learning' | 'memory_graph'
  label: string
  tasks: string[]
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
  per_task: CachePipelineTaskMetric[]
}

export interface CachePipelineOverall {
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
}

export interface CachePipelineData {
  period: 'day' | 'week' | 'month'
  generated_at: string
  overall: CachePipelineOverall
  pipelines: CachePipelineGroup[]
}

interface Props {
  data: CachePipelineData | null
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), { loading: false })

const emit = defineEmits<{
  (e: 'navigate', target: string): void
}>()

// Top-N chip rendering: at most 5 chips per pipeline. Sort by hit_pct DESC
// (None last), then calls DESC. Tasks with calls === 0 are excluded — they
// land in the "未触发" footnote so the chip strip stays signal-dense.
const CHIP_LIMIT = 5

interface ChipModel {
  task: string
  pct: number | null
  calls: number
  pctLabel: string
  lowSample: boolean
}

function rankPerTask(tasks: CachePipelineTaskMetric[]): {
  chips: ChipModel[]
  hidden: number
  untriggered: number
} {
  const triggered = tasks.filter(t => t.calls > 0)
  const untriggered = tasks.length - triggered.length

  triggered.sort((a, b) => {
    const aPct = a.hit_pct
    const bPct = b.hit_pct
    if (aPct === null && bPct === null) return b.calls - a.calls
    if (aPct === null) return 1
    if (bPct === null) return -1
    if (bPct !== aPct) return bPct - aPct
    return b.calls - a.calls
  })

  const visible = triggered.slice(0, CHIP_LIMIT)
  const hidden = Math.max(0, triggered.length - CHIP_LIMIT)
  const chips: ChipModel[] = visible.map(t => ({
    task: t.task,
    pct: t.hit_pct,
    calls: t.calls,
    pctLabel: formatHitPct(t.hit_pct),
    lowSample: t.calls < 3,
  }))
  return { chips, hidden, untriggered }
}

function pctToPercent(pct: number | null): number {
  if (typeof pct !== 'number' || Number.isNaN(pct)) return 0
  return Math.max(0, Math.min(100, Math.round(pct * 100)))
}

function chipTagType(pct: number | null): 'success' | 'warning' | 'error' | 'default' {
  if (pct === null) return 'default'
  if (pct >= 0.85) return 'success'
  if (pct >= 0.40) return 'default'
  if (pct >= 0.20) return 'warning'
  return 'error'
}

const overall = computed(() => props.data?.overall ?? null)
const pipelines = computed(() => props.data?.pipelines ?? [])
const showLowSampleHint = computed(() => {
  const o = overall.value
  return !!o && o.calls > 0 && o.calls < 10
})
const isEmpty = computed(() => {
  const o = overall.value
  if (!o) return true
  return o.calls === 0
})

function onNavigate() {
  emit('navigate', '/system')
}
</script>

<template>
  <AppPanelSection eyebrow="CACHE" title="管线命中率">
    <template #aside>
      <StateBadge
        v-if="overall && overall.calls > 0"
        :status="overall.hit_pct !== null && overall.hit_pct >= 0.6 ? 'success' : 'default'"
        :label="`今日 ${overall.calls} 次 · ${formatHitPct(overall.hit_pct)}`"
        compact
      />
      <StateBadge
        v-else
        status="default"
        label="今日 0 次"
        compact
      />
    </template>

    <EmptyState
      v-if="isEmpty"
      title="今日尚无 cache 调用数据"
      description="等待第一次 LLM 调用后会在这里看到分管线命中率。"
    />

    <div v-else class="cache-pipeline-list">
      <p v-if="showLowSampleHint" class="cache-pipeline-hint">
        今日样本数较少（{{ overall?.calls ?? 0 }}），命中率仅供参考。
      </p>

      <div
        v-for="pipeline in pipelines"
        :key="pipeline.key"
        class="cache-pipeline-row"
      >
        <div class="cache-pipeline-row__head">
          <div class="cache-pipeline-row__title">
            <strong>{{ pipeline.label }}</strong>
            <span class="cache-pipeline-row__meta">{{ pipeline.calls }} 次</span>
          </div>
          <div class="cache-pipeline-row__metrics">
            <span class="cache-pipeline-row__pct">
              {{ formatHitPct(pipeline.hit_pct) }}
            </span>
            <NButton size="tiny" text @click="onNavigate">
              看明细
            </NButton>
          </div>
        </div>

        <NProgress
          type="line"
          :height="8"
          :percentage="pctToPercent(pipeline.hit_pct)"
          :show-indicator="false"
          :color="cacheHitColor(pipeline.hit_pct)"
          rail-color="rgba(49, 108, 114, 0.08)"
          :border-radius="6"
          :fill-border-radius="6"
        />

        <template v-if="pipeline.calls > 0">
          <div class="cache-pipeline-row__chips">
            <NTag
              v-for="chip in rankPerTask(pipeline.per_task).chips"
              :key="chip.task"
              size="tiny"
              round
              :type="chipTagType(chip.pct)"
            >
              {{ chip.task }} {{ chip.pctLabel }}{{ chip.lowSample ? '*' : '' }}
            </NTag>
            <NTag
              v-if="rankPerTask(pipeline.per_task).hidden > 0"
              size="tiny"
              round
              type="default"
            >
              +{{ rankPerTask(pipeline.per_task).hidden }}
            </NTag>
          </div>
          <p
            v-if="rankPerTask(pipeline.per_task).untriggered > 0"
            class="cache-pipeline-row__footnote"
          >
            {{ rankPerTask(pipeline.per_task).untriggered }} 个未触发任务
          </p>
        </template>
        <p
          v-else
          class="cache-pipeline-row__footnote"
        >
          {{ pipeline.tasks.length }} 个未触发任务
        </p>
      </div>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.cache-pipeline-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.cache-pipeline-hint {
  margin: 0 0 -4px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
}

.cache-pipeline-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cache-pipeline-row__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.cache-pipeline-row__title {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.cache-pipeline-row__title strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.cache-pipeline-row__meta {
  color: var(--om-text-3);
  font-size: 11px;
}

.cache-pipeline-row__metrics {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
}

.cache-pipeline-row__pct {
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.cache-pipeline-row__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cache-pipeline-row__footnote {
  margin: 0;
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
}
</style>

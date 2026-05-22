<script setup lang="ts">
import { NButton } from 'naive-ui'
import { computed } from 'vue'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import StateBadge from '../../../components/common/StateBadge.vue'
import { cacheHitColor, formatHitPct } from '../../system/helpers/formatters'
import {
  type CachePipelineData,
  type CachePipelineGroup,
  type CachePipelineTaskMetric,
  taskLabelZh,
} from '../types'
import BarColumnGroup, { type BarItem } from './BarColumnGroup.vue'

interface Props {
  data: CachePipelineData | null
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), { loading: false })

const emit = defineEmits<{
  (e: 'navigate', target: string): void
}>()

const RECENT_LIMIT = 5

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

// Pad all task-bar groups to the global max task count (across all 4
// pipelines — currently 6 from `learning`). Without this, the
// `core_chat`/`slang`/`memory_graph` 4-task cards render fat bars while
// `learning`'s 6-task card renders narrow ones, breaking the "4 cards
// look identical" requirement.
const maxTaskCount = computed(() => {
  const counts = pipelines.value.map(p => p.per_task.length)
  return counts.length > 0 ? Math.max(...counts) : 0
})

function badgeStatus(pct: number | null | undefined): 'success' | 'warning' | 'error' | 'default' {
  if (typeof pct !== 'number' || Number.isNaN(pct)) return 'default'
  if (pct >= 0.85) return 'success'
  if (pct >= 0.40) return 'default'
  if (pct >= 0.20) return 'warning'
  return 'error'
}

function taskBars(p: CachePipelineGroup): BarItem[] {
  const bars: BarItem[] = p.per_task.map((t: CachePipelineTaskMetric) => ({
    label: taskLabelZh(t.task),
    value: t.hit_pct,
    badge: t.calls > 0 ? `${t.calls} 次` : '未触发',
  }))
  // Pad with placeholder slots so widths match across pipelines.
  while (bars.length < maxTaskCount.value) {
    bars.push({ label: '', value: null, placeholder: true })
  }
  return bars
}

function recentBars(p: CachePipelineGroup): BarItem[] {
  const samples = p.recent?.samples ?? []
  // Backend returns newest-first; reverse so newest renders rightmost.
  const reversed = samples.slice().reverse()
  const bars: BarItem[] = reversed.map((s, i) => ({
    label: `-${reversed.length - i}`,
    value: s.hit_pct,
    badge: taskLabelZh(s.task),
    highlight: i === reversed.length - 1,
  }))
  // Always pad to RECENT_LIMIT so all 4 cards have the same column count.
  while (bars.length < RECENT_LIMIT) {
    bars.push({ label: '', value: null, placeholder: true })
  }
  return bars
}

function recentLabel(p: CachePipelineGroup): string {
  const calls = p.recent?.calls ?? 0
  return calls > 0 ? `近 ${calls} 次` : '近 0 次'
}

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
      <StateBadge
        v-if="overall?.recent && overall.recent.calls > 0"
        :status="badgeStatus(overall.recent.hit_pct)"
        :label="`近 ${overall.recent.calls} 次 · ${formatHitPct(overall.recent.hit_pct)}`"
        compact
      />
    </template>

    <EmptyState
      v-if="isEmpty"
      title="今日尚无 cache 调用数据"
      description="等待第一次 LLM 调用后会在这里看到分管线命中率。"
    />

    <div v-else class="cache-pipeline">
      <p v-if="showLowSampleHint" class="cache-pipeline__hint">
        今日样本数较少（{{ overall?.calls ?? 0 }}），命中率仅供参考。
      </p>

      <div class="cache-pipeline__grid">
        <article
          v-for="pipeline in pipelines"
          :key="pipeline.key"
          class="cache-pipeline-card"
        >
          <header class="cache-pipeline-card__head">
            <strong class="cache-pipeline-card__title">{{ pipeline.label }}</strong>
            <span class="cache-pipeline-card__meta">{{ pipeline.calls }} 次</span>
          </header>

          <dl class="cache-pipeline-card__rates">
            <div class="cache-pipeline-card__rate">
              <dt title="本周期内的总命中率（hit / (hit + miss)）">周期总</dt>
              <dd :style="{ color: cacheHitColor(pipeline.hit_pct) }">
                {{ formatHitPct(pipeline.hit_pct) }}
              </dd>
            </div>
            <div class="cache-pipeline-card__rate">
              <dt title="本周期内最近 N 次调用的加权命中率">{{ recentLabel(pipeline) }}</dt>
              <dd :style="{ color: cacheHitColor(pipeline.recent?.hit_pct ?? null) }">
                {{ formatHitPct(pipeline.recent?.hit_pct ?? null) }}
              </dd>
            </div>
          </dl>

          <BarColumnGroup
            :bars="taskBars(pipeline)"
            axis-label="按任务"
          />
          <BarColumnGroup
            :bars="recentBars(pipeline)"
            axis-label="近 5 次"
          />

          <footer class="cache-pipeline-card__foot">
            <NButton size="tiny" text @click="onNavigate">
              看明细
            </NButton>
          </footer>
        </article>
      </div>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.cache-pipeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cache-pipeline__hint {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
}

.cache-pipeline__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 1100px) {
  .cache-pipeline__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .cache-pipeline__grid {
    grid-template-columns: 1fr;
  }
}

.cache-pipeline-card {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  transition: background 120ms ease, transform 120ms ease;
}

.cache-pipeline-card:hover {
  background: color-mix(in srgb, var(--om-surface-3) 60%, var(--om-surface-2));
  transform: translateY(-1px);
}

.cache-pipeline-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.cache-pipeline-card__title {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.cache-pipeline-card__meta {
  color: var(--om-text-3);
  font-size: 11px;
}

.cache-pipeline-card__rates {
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 12px;
}

.cache-pipeline-card__rate dt {
  font-size: 11px;
  color: var(--om-text-3);
  cursor: help;
  margin-bottom: 2px;
}

.cache-pipeline-card__rate dd {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.cache-pipeline-card__foot {
  margin-top: auto;
  display: flex;
  justify-content: flex-end;
}
</style>

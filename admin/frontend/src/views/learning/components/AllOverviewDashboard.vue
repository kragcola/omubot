<script setup lang="ts">
import EmptyState from '../../../components/common/EmptyState.vue'
import type {
  LearningItem,
  LearningNounKey,
  LearningStageKey,
  StageStripItem,
} from '../types'

const props = defineProps<{
  stages: StageStripItem[]
  items: LearningItem[]
  loading?: boolean
  asOf: string
  nounLabels: Record<LearningNounKey, string>
  activeStage: LearningStageKey
}>()

const emit = defineEmits<{
  selectNoun: [noun: LearningNounKey]
  selectStage: [noun: LearningNounKey, stage: LearningStageKey]
  openItem: [item: LearningItem]
}>()

const nounOrder: LearningNounKey[] = ['slang', 'style', 'episode', 'memory', 'fact', 'graph_relation']

const stageOrder: LearningStageKey[] = ['candidate', 'review', 'approved', 'hits', 'archived']

interface KpiTile {
  key: string
  label: string
  value: number
  hint: string
  tone: 'info' | 'warn' | 'success' | 'neutral'
}

interface NounModule {
  key: LearningNounKey
  label: string
  total: number
  byStage: Record<LearningStageKey, number>
  pendingReview: number
  topStage: LearningStageKey
  topStageValue: number
  recent: LearningItem | null
  isEmpty: boolean
}

type NounChipTone = 'info' | 'success' | 'warn' | 'neutral'

interface FeedRow {
  id: string
  noun: LearningNounKey
  nounLabel: string
  nounTone: NounChipTone
  title: string
  statusLabel: string
  group: string
  time: string
  conf: number | null
  tone: 'success' | 'pending' | 'rejected' | 'neutral'
}

function statusTone(status: string): 'success' | 'pending' | 'rejected' | 'neutral' {
  if (['hit', 'approved', 'enabled_for_prompt', 'active'].includes(status)) return 'success'
  if (['pending', 'candidate', 'dry_run', 'queued'].includes(status)) return 'pending'
  if (['muted', 'expired', 'rejected', 'disabled'].includes(status)) return 'rejected'
  return 'neutral'
}

function safeNum(value: number | null | undefined): number {
  return typeof value === 'number' ? value : 0
}

const nounToneMap: Record<LearningNounKey, NounChipTone> = {
  slang: 'neutral',
  style: 'neutral',
  episode: 'info',
  memory: 'success',
  fact: 'warn',
  graph_relation: 'neutral',
}

function nounToneOf(noun: LearningNounKey): NounChipTone {
  return nounToneMap[noun] ?? 'neutral'
}

function formatRelativeTime(value: string): string {
  if (!value) return '——'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = Date.now()
  const diff = now - date.getTime()
  if (diff < 0) return '刚刚'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return '刚刚'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day === 1) return '昨天'
  if (day < 7) return `${day} 天前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  }).format(date).replace(/\//g, '-')
}

function shortGroup(value: string): string {
  if (!value) return '——'
  if (value.length <= 6) return value
  return `…${value.slice(-5)}`
}

function formatCount(value: number): string {
  if (value < 1000) return String(value)
  if (value < 10000) return `${(value / 1000).toFixed(1)}k`
  return `${Math.round(value / 1000)}k`
}

const stageLabel: Record<LearningStageKey, string> = {
  candidate: '候选',
  review: '待审',
  approved: '入库',
  hits: '命中',
  archived: '归档',
}

const stageOf = computed(() => {
  const map = {} as Record<LearningStageKey, StageStripItem | undefined>
  for (const stage of props.stages) {
    map[stage.key] = stage
  }
  return map
})

const kpiTiles = computed<KpiTile[]>(() => {
  const cand = stageOf.value.candidate
  const rev = stageOf.value.review
  const app = stageOf.value.approved
  const hit = stageOf.value.hits
  return [
    {
      key: 'candidate',
      label: '候选池',
      value: cand?.total ?? 0,
      hint: '抽取产物 · 待 AI 初筛',
      tone: 'neutral',
    },
    {
      key: 'review',
      label: '待人工审核',
      value: rev?.total ?? 0,
      hint: rev?.total ? '需要拍板的条目' : '当前队列已清空',
      tone: rev?.total ? 'warn' : 'neutral',
    },
    {
      key: 'approved',
      label: '已入库',
      value: app?.total ?? 0,
      hint: '可注入 prompt 的条目',
      tone: 'success',
    },
    {
      key: 'hits',
      label: '今日命中',
      value: hit?.total ?? 0,
      hint: '今日进入 prompt 的观测',
      tone: 'info',
    },
  ]
})

const nounModules = computed<NounModule[]>(() => {
  const recentByNoun = new Map<LearningNounKey, LearningItem>()
  for (const item of props.items) {
    if (!recentByNoun.has(item.noun)) {
      recentByNoun.set(item.noun, item)
    }
  }
  return nounOrder.map((noun) => {
    const byStage = {} as Record<LearningStageKey, number>
    let total = 0
    let topStage: LearningStageKey = 'candidate'
    let topStageValue = -1
    for (const stage of stageOrder) {
      const value = safeNum(stageOf.value[stage]?.byNoun?.[noun] ?? 0)
      byStage[stage] = value
      total += value
      if (value > topStageValue) {
        topStageValue = value
        topStage = stage
      }
    }
    return {
      key: noun,
      label: props.nounLabels[noun],
      total,
      byStage,
      pendingReview: byStage.review,
      topStage,
      topStageValue: Math.max(topStageValue, 0),
      recent: recentByNoun.get(noun) ?? null,
      isEmpty: total === 0,
    }
  })
})

const feedRows = computed<FeedRow[]>(() => {
  return props.items.slice(0, 12).map(item => ({
    id: item.id,
    noun: item.noun,
    nounLabel: props.nounLabels[item.noun] || item.kind_label || item.noun,
    nounTone: nounToneOf(item.noun),
    title: item.content || '——',
    statusLabel: item.status_label || item.status || '',
    group: shortGroup(item.group_id),
    time: formatRelativeTime(item.created_at),
    conf: item.confidence,
    tone: statusTone(item.status),
  }))
})

function maxStageValue(mod: NounModule): number {
  let max = 0
  for (const stage of stageOrder) {
    if (mod.byStage[stage] > max) max = mod.byStage[stage]
  }
  return max
}
</script>

<template>
  <div class="ov">
    <section class="ov-kpi">
      <article
        v-for="tile in kpiTiles"
        :key="tile.key"
        class="ov-kpi__tile"
        :class="`ov-kpi__tile--${tile.tone}`"
      >
        <span class="ov-kpi__label">{{ tile.label }}</span>
        <strong class="ov-kpi__value">
          {{ loading ? '——' : tile.value.toLocaleString() }}
        </strong>
        <span class="ov-kpi__hint">{{ tile.hint }}</span>
      </article>
    </section>

    <section class="ov-modules">
      <header class="ov-section-head">
        <span class="ov-eyebrow">Modules</span>
        <h3>各模块概览</h3>
        <span class="ov-meta">点击模块进入对应管线</span>
      </header>

      <div class="ov-modules__grid">
        <article
          v-for="mod in nounModules"
          :key="mod.key"
          class="mod"
          :class="{ 'mod--empty': mod.isEmpty }"
          tabindex="0"
          @click="emit('selectNoun', mod.key)"
          @keyup.enter="emit('selectNoun', mod.key)"
        >
          <header class="mod-head">
            <span class="mod-noun">{{ mod.label }}</span>
            <strong class="mod-total">{{ formatCount(mod.total) }}</strong>
          </header>

          <div class="mod-stages">
            <button
              v-for="stage in stageOrder"
              :key="stage"
              type="button"
              class="mod-stage"
              :class="{
                'mod-stage--zero': mod.byStage[stage] === 0,
                'mod-stage--top': mod.topStageValue > 0 && stage === mod.topStage,
              }"
              :title="`${stageLabel[stage]} · ${mod.byStage[stage]}`"
              @click.stop="emit('selectStage', mod.key, stage)"
            >
              <span class="mod-stage-bar">
                <span
                  class="mod-stage-fill"
                  :style="{
                    height:
                      maxStageValue(mod) > 0
                        ? `${Math.min(100, Math.max(8, (mod.byStage[stage] / maxStageValue(mod)) * 100))}%`
                        : '0%',
                  }"
                />
              </span>
              <span class="mod-stage-num">{{ formatCount(mod.byStage[stage]) }}</span>
              <span class="mod-stage-name">{{ stageLabel[stage] }}</span>
            </button>
          </div>

          <footer class="mod-foot">
            <span v-if="mod.pendingReview > 0" class="mod-tag mod-tag--warn">
              {{ mod.pendingReview }} 待审
            </span>
            <span v-else-if="mod.byStage.approved > 0" class="mod-tag mod-tag--ok">
              已入库 {{ formatCount(mod.byStage.approved) }}
            </span>
            <span v-else class="mod-tag mod-tag--mute">暂无积压</span>

            <span v-if="mod.recent" class="mod-recent" :title="mod.recent.content_full || mod.recent.content">
              最近 · {{ mod.recent.content || '——' }}
            </span>
            <span v-else class="mod-recent mod-recent--mute">尚无样本</span>
          </footer>
        </article>
      </div>
    </section>

    <section class="ov-feed">
      <header class="ov-section-head">
        <span class="ov-eyebrow">Live Feed</span>
        <h3>信息速递</h3>
        <span class="ov-meta">
          最近 {{ feedRows.length }} 条 · {{ asOf || '尚未同步' }}
        </span>
      </header>

      <div v-if="loading && !feedRows.length" class="ov-feed__loading">
        <NSkeleton v-for="i in 6" :key="i" :height="40" />
      </div>

      <ol v-else-if="feedRows.length" class="ov-feed__list">
        <li
          v-for="row in feedRows"
          :key="row.id"
          class="feed-row"
          :class="`feed-row--${row.tone}`"
          @click="emit('openItem', props.items.find(it => it.id === row.id)!)"
        >
          <span class="feed-dot" :title="row.statusLabel" />
          <div class="feed-main">
            <div class="feed-title" :title="row.title">{{ row.title }}</div>
            <div class="feed-meta">
              <span class="feed-meta__status">{{ row.statusLabel || '——' }}</span>
              <span class="feed-meta__sep">·</span>
              <span class="feed-meta__group">群 {{ row.group }}</span>
              <template v-if="row.conf !== null">
                <span class="feed-meta__sep">·</span>
                <span class="feed-meta__conf">{{ Math.round(row.conf * 100) }}%</span>
              </template>
            </div>
          </div>
          <div class="feed-side">
            <span class="feed-noun" :class="`feed-noun--${row.nounTone}`">{{ row.nounLabel }}</span>
            <span class="feed-time">{{ row.time }}</span>
          </div>
        </li>
      </ol>

      <EmptyState
        v-else
        compact
        title="暂无最新条目"
        description="抽取产物或审核结果会在这里实时滚动。"
      />
    </section>
  </div>
</template>

<style scoped>
.ov {
  display: grid;
  gap: 12px;
  font-feature-settings: 'tnum' 1;
}

.ov-section-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}

.ov-eyebrow {
  color: var(--om-text-3);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.ov-section-head h3 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.005em;
}

.ov-meta {
  margin-left: auto;
  color: var(--om-text-3);
  font-size: 11px;
}

/* ---------- KPI 速览 ---------- */
.ov-kpi {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.ov-kpi__tile {
  position: relative;
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  overflow: hidden;
}

.ov-kpi__tile::before {
  content: '';
  position: absolute;
  inset: 0;
  border-left: 3px solid var(--om-text-3);
  opacity: 0.4;
  pointer-events: none;
}

.ov-kpi__tile--info::before { border-color: var(--om-info); opacity: 0.85; }
.ov-kpi__tile--warn::before { border-color: var(--om-warning); opacity: 0.85; }
.ov-kpi__tile--success::before { border-color: var(--om-success); opacity: 0.85; }

.ov-kpi__label {
  color: var(--om-text-3);
  font-size: 11px;
  letter-spacing: 0.04em;
}

.ov-kpi__value {
  color: var(--om-text-1);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}

.ov-kpi__hint {
  color: var(--om-text-3);
  font-size: 11px;
}

/* ---------- 模块格子 ---------- */
.ov-modules__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.mod {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease, transform 0.16s ease;
}

.mod:hover,
.mod:focus-visible {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 50%, var(--om-surface-solid));
  outline: none;
}

.mod--empty {
  opacity: 0.65;
}

.mod-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.mod-noun {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.005em;
}

.mod-total {
  color: var(--om-text-2);
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.mod-stages {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
}

.mod-stage {
  display: grid;
  grid-template-rows: 36px auto auto;
  gap: 2px;
  padding: 4px 0 2px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  text-align: center;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.mod-stage:hover {
  background: color-mix(in srgb, var(--om-surface-2) 70%, transparent);
}

.mod-stage-bar {
  position: relative;
  display: block;
  width: 100%;
  height: 36px;
  background: color-mix(in srgb, var(--om-border) 50%, transparent);
  border-radius: 2px;
  overflow: hidden;
}

.mod-stage-fill {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--om-text-3);
  opacity: 0.7;
  border-radius: 2px;
  transition: height 0.2s ease;
}

.mod-stage--top .mod-stage-fill {
  background: var(--om-info);
  opacity: 1;
}

.mod-stage--zero .mod-stage-fill {
  background: transparent;
}

.mod-stage-num {
  color: var(--om-text-2);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.mod-stage--zero .mod-stage-num {
  color: var(--om-text-3);
  opacity: 0.55;
}

.mod-stage-name {
  color: var(--om-text-3);
  font-size: 10px;
  letter-spacing: 0.02em;
  line-height: 1.2;
}

.mod-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 60%, transparent);
  min-height: 22px;
}

.mod-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.mod-tag--warn {
  background: color-mix(in srgb, var(--om-warning) 14%, transparent);
  color: var(--om-warning);
}

.mod-tag--ok {
  background: color-mix(in srgb, var(--om-success) 12%, transparent);
  color: var(--om-success);
}

.mod-tag--mute {
  background: color-mix(in srgb, var(--om-text-3) 10%, transparent);
  color: var(--om-text-3);
}

.mod-recent {
  flex: 1;
  min-width: 0;
  color: var(--om-text-2);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mod-recent--mute {
  color: var(--om-text-3);
  opacity: 0.7;
  font-style: italic;
}

/* ---------- 信息速递 ---------- */
.ov-feed__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  overflow: hidden;
}

.ov-feed__loading {
  display: grid;
  gap: 4px;
}

.feed-row {
  position: relative;
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  align-items: center;
  column-gap: 12px;
  min-height: 52px;
  padding: 10px 14px;
  border-bottom: 1px solid color-mix(in srgb, var(--om-border) 55%, transparent);
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.feed-row:last-child {
  border-bottom: 0;
}

.feed-row:hover {
  background: color-mix(in srgb, var(--om-surface-2) 55%, transparent);
}

.feed-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--om-text-3);
  opacity: 0.55;
}

.feed-row--success .feed-dot { background: var(--om-success); opacity: 1; }
.feed-row--pending .feed-dot { background: var(--om-warning); opacity: 1; }
.feed-row--rejected .feed-dot { background: var(--om-danger); opacity: 1; }

.feed-main {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.feed-title {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feed-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.2;
}

.feed-meta__status {
  font-variant-numeric: tabular-nums;
}

.feed-row--success .feed-meta__status { color: var(--om-success); }
.feed-row--pending .feed-meta__status { color: var(--om-warning); }
.feed-row--rejected .feed-meta__status { color: var(--om-danger); }

.feed-meta__sep {
  opacity: 0.5;
}

.feed-meta__group,
.feed-meta__conf {
  font-variant-numeric: tabular-nums;
}

.feed-side {
  display: grid;
  justify-items: end;
  gap: 4px;
}

.feed-noun {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
  letter-spacing: 0.01em;
  background: color-mix(in srgb, var(--om-text-3) 12%, transparent);
  color: var(--om-text-2);
}

.feed-noun--info {
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
  color: var(--om-info);
}

.feed-noun--success {
  background: color-mix(in srgb, var(--om-success) 12%, transparent);
  color: var(--om-success);
}

.feed-noun--warn {
  background: color-mix(in srgb, var(--om-warning) 14%, transparent);
  color: var(--om-warning);
}

.feed-time {
  color: var(--om-text-3);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

@media (max-width: 1100px) {
  .ov-modules__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ov-kpi {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .ov-modules__grid,
  .ov-kpi {
    grid-template-columns: 1fr;
  }
  .feed-row {
    grid-template-columns: 8px minmax(0, 1fr) auto;
  }
  .feed-noun {
    display: none;
  }
}
</style>

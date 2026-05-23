<script setup lang="ts">
import { ChevronForwardOutline } from '@vicons/ionicons5'
import type { LearningNounKey, LearningStageKey, StageStripItem } from '../types'

const props = withDefaults(defineProps<{
  stages: StageStripItem[]
  activeStage: LearningStageKey
  loading?: boolean
}>(), {
  loading: false,
})

const emit = defineEmits<{
  select: [stage: LearningStageKey]
}>()

const nounLabels: Record<LearningNounKey, string> = {
  slang: '黑话',
  style: '风格',
  episode: '经验',
  memory: '记忆',
  fact: '事实',
  graph_relation: '关系',
}

const nounOrder: LearningNounKey[] = [
  'slang',
  'style',
  'episode',
  'memory',
  'fact',
  'graph_relation',
]

function formatCount(value: number | null): string {
  return value === null ? '--' : String(value)
}

function splitSummary(stage: StageStripItem): string {
  const parts = nounOrder
    .map(noun => ({ noun, value: stage.byNoun[noun] }))
    .filter(item => item.value !== null && Number(item.value) > 0)
    .slice(0, 3)
    .map(item => `${nounLabels[item.noun]} ${item.value}`)
  return parts.length ? parts.join(' · ') : '暂无拆分'
}

function handleSelect(stage: StageStripItem) {
  if (stage.key === props.activeStage) return
  emit('select', stage.key)
}
</script>

<template>
  <div class="stage-strip" aria-label="学习管道阶段">
    <template v-for="(stage, index) in stages" :key="stage.key">
      <NTooltip trigger="hover" placement="bottom">
        <template #trigger>
          <button
            type="button"
            class="stage-card"
            :class="{
              'stage-card--active': stage.key === activeStage,
              'stage-card--empty': !loading && stage.total === 0,
            }"
            :aria-current="stage.key === activeStage ? 'step' : undefined"
            :disabled="stage.key === activeStage"
            @click="handleSelect(stage)"
          >
            <span class="stage-card__eyebrow">{{ stage.eyebrow }}</span>
            <span class="stage-card__main">
              <span class="stage-card__label">{{ stage.label }}</span>
              <span class="stage-card__count">{{ loading ? '...' : formatCount(stage.total) }}</span>
            </span>
            <span class="stage-card__summary">{{ loading ? '读取中' : splitSummary(stage) }}</span>
          </button>
        </template>
        <div class="stage-tooltip">
          <strong>{{ stage.label }}</strong>
          <span>{{ stage.description }}</span>
          <div class="stage-tooltip__grid">
            <span v-for="noun in nounOrder" :key="noun">
              {{ nounLabels[noun] }} {{ formatCount(stage.byNoun[noun]) }}
            </span>
          </div>
        </div>
      </NTooltip>
      <span v-if="index < stages.length - 1" class="stage-strip__arrow" aria-hidden="true">
        <NIcon :component="ChevronForwardOutline" :size="18" />
      </span>
    </template>
  </div>
</template>

<style scoped>
.stage-strip {
  display: flex;
  align-items: stretch;
  gap: 12px;
  overflow-x: auto;
  padding: 4px 0 12px;
}

.stage-card {
  display: flex;
  flex: 1 0 168px;
  min-width: 168px;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  min-height: 88px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 160ms ease,
    background-color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}

.stage-card:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  box-shadow: var(--om-shadow-sm);
}

.stage-card:disabled {
  cursor: default;
}

.stage-card--active {
  border: 2px solid rgb(var(--primary-color));
  box-shadow: inset 0 -4px 0 var(--om-info);
  background: rgba(var(--primary-color), 0.08);
}

.stage-card--empty {
  opacity: 0.62;
}

.stage-card__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.stage-card__main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.stage-card__label {
  overflow: hidden;
  font-size: 14px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-card__count {
  flex-shrink: 0;
  color: var(--om-text-1);
  font-variant-numeric: tabular-nums;
  font-size: 24px;
  font-weight: 700;
}

.stage-card__summary {
  overflow: hidden;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-strip__arrow {
  display: inline-flex;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  color: var(--om-text-3);
}

.stage-tooltip {
  display: grid;
  gap: 8px;
  max-width: 280px;
  color: var(--om-text-1);
}

.stage-tooltip strong {
  font-size: 14px;
}

.stage-tooltip span {
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.stage-tooltip__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 12px;
  padding-top: 4px;
}

@media (max-width: 760px) {
  .stage-strip {
    align-items: stretch;
  }

  .stage-card {
    flex-basis: 176px;
  }
}
</style>

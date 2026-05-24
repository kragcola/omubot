<script setup lang="ts">
import {
  AppsOutline,
  BookmarkOutline,
  BulbOutline,
  ChatbubbleEllipsesOutline,
  CubeOutline,
  GitNetworkOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import type { Component } from 'vue'
import type { LearningNounFilter, LearningNounKey } from '../types'

const props = defineProps<{
  options: { label: string, value: LearningNounFilter }[]
  active: LearningNounFilter
  total: number
  byNoun: Record<LearningNounKey, number | null>
  loading?: boolean
}>()

const emit = defineEmits<{
  select: [value: LearningNounFilter]
}>()

const ICONS: Record<LearningNounFilter, Component> = {
  all: AppsOutline,
  slang: ChatbubbleEllipsesOutline,
  style: SparklesOutline,
  episode: BulbOutline,
  memory: BookmarkOutline,
  fact: CubeOutline,
  graph_relation: GitNetworkOutline,
}

const EYEBROWS: Record<LearningNounFilter, string> = {
  all: 'ALL',
  slang: 'SLANG',
  style: 'STYLE',
  episode: 'EPISODE',
  memory: 'MEMORY',
  fact: 'FACT',
  graph_relation: 'RELATION',
}

function countFor(value: LearningNounFilter): number | null {
  if (props.loading) return null
  if (value === 'all') return props.total
  return props.byNoun[value]
}

function formatCount(value: number | null): string {
  if (value === null) return '—'
  if (value >= 10000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

function isEmpty(value: LearningNounFilter): boolean {
  if (props.loading) return false
  const c = countFor(value)
  return c === null || c === 0
}

function handleSelect(value: LearningNounFilter) {
  if (value === props.active) return
  emit('select', value)
}
</script>

<template>
  <nav class="noun-switcher" role="tablist" aria-label="学习管道词条">
    <span class="noun-switcher__eyebrow" aria-hidden="true">学习词条主轴</span>
    <div class="noun-switcher__rail">
      <button
        v-for="opt in options"
        :key="opt.value"
        type="button"
        role="tab"
        :aria-selected="opt.value === active"
        class="noun-switcher__tab"
        :class="{
          'noun-switcher__tab--active': opt.value === active,
          'noun-switcher__tab--empty': isEmpty(opt.value),
          'noun-switcher__tab--all': opt.value === 'all',
        }"
        @click="handleSelect(opt.value)"
      >
        <span class="noun-switcher__head">
          <span class="noun-switcher__icon">
            <NIcon :component="ICONS[opt.value]" :size="16" />
          </span>
          <span class="noun-switcher__tag">{{ EYEBROWS[opt.value] }}</span>
        </span>
        <span class="noun-switcher__body">
          <span class="noun-switcher__label">{{ opt.label }}</span>
          <span class="noun-switcher__count">{{ formatCount(countFor(opt.value)) }}</span>
        </span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
.noun-switcher {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 14px 0;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.noun-switcher__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.noun-switcher__rail {
  display: flex;
  align-items: stretch;
  gap: 4px;
  margin: 0 -4px;
  padding: 0 4px;
  overflow-x: auto;
  scrollbar-width: none;
}

.noun-switcher__rail::-webkit-scrollbar {
  display: none;
}

.noun-switcher__tab {
  position: relative;
  display: inline-flex;
  flex: 1 1 0;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  min-width: 124px;
  height: 76px;
  padding: 10px 14px 14px;
  border: 1px solid transparent;
  border-bottom: 3px solid transparent;
  border-radius: 12px 12px 0 0;
  background: transparent;
  color: var(--om-text-2);
  text-align: left;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    border-color 160ms ease,
    color 160ms ease,
    box-shadow 160ms ease;
}

.noun-switcher__tab:hover:not(.noun-switcher__tab--active) {
  background: color-mix(in srgb, var(--om-surface-solid) 60%, transparent);
  color: var(--om-text-1);
}

.noun-switcher__tab--active {
  border-color: var(--om-border);
  border-bottom-color: rgb(var(--primary-color));
  background: var(--om-surface-solid);
  color: var(--om-text-1);
  box-shadow: var(--om-shadow-sm);
}

.noun-switcher__tab--all::after {
  position: absolute;
  top: 14px;
  right: -3px;
  bottom: 14px;
  width: 1px;
  background: var(--om-border);
  content: '';
}

.noun-switcher__tab--all {
  flex-grow: 0;
  flex-basis: 132px;
  margin-right: 6px;
}

.noun-switcher__tab--all.noun-switcher__tab--active::after {
  display: none;
}

.noun-switcher__tab--empty:not(.noun-switcher__tab--active) {
  opacity: 0.6;
}

.noun-switcher__head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.noun-switcher__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: currentColor;
}

.noun-switcher__tag {
  color: var(--om-text-3);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.noun-switcher__tab--active .noun-switcher__tag {
  color: rgb(var(--primary-color));
}

.noun-switcher__body {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.noun-switcher__label {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.noun-switcher__count {
  color: var(--om-text-2);
  font-variant-numeric: tabular-nums;
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
}

.noun-switcher__tab--active .noun-switcher__count {
  color: rgb(var(--primary-color));
}

@media (max-width: 880px) {
  .noun-switcher__rail {
    flex-wrap: nowrap;
  }

  .noun-switcher__tab {
    flex: 0 0 132px;
  }

  .noun-switcher__tab--all::after {
    display: none;
  }
}
</style>

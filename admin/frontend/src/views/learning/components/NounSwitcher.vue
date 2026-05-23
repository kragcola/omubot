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
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="tab"
      :aria-selected="opt.value === active"
      class="noun-switcher__item"
      :class="{
        'noun-switcher__item--active': opt.value === active,
        'noun-switcher__item--empty': isEmpty(opt.value),
        'noun-switcher__item--all': opt.value === 'all',
      }"
      @click="handleSelect(opt.value)"
    >
      <span class="noun-switcher__icon">
        <NIcon :component="ICONS[opt.value]" :size="16" />
      </span>
      <span class="noun-switcher__label">{{ opt.label }}</span>
      <span class="noun-switcher__count">{{ formatCount(countFor(opt.value)) }}</span>
    </button>
  </nav>
</template>

<style scoped>
.noun-switcher {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 6px;
  padding: 6px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.noun-switcher__item {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--om-text-2);
  cursor: pointer;
  transition:
    background-color 160ms ease,
    border-color 160ms ease,
    color 160ms ease,
    transform 160ms ease,
    box-shadow 160ms ease;
}

.noun-switcher__item:hover:not(.noun-switcher__item--active) {
  border-color: var(--om-border);
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
  color: var(--om-text-1);
  transform: translateY(-1px);
}

.noun-switcher__item--active {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 28%, transparent);
  background: var(--om-surface-solid);
  color: rgb(var(--primary-color));
  box-shadow: var(--om-shadow-sm), inset 0 -2px 0 rgb(var(--primary-color));
}

.noun-switcher__item--all::after {
  position: absolute;
  top: 8px;
  right: -1px;
  bottom: 8px;
  width: 1px;
  background: var(--om-border);
  content: '';
}

.noun-switcher__item--all {
  margin-right: 6px;
  padding-right: 18px;
}

.noun-switcher__item--empty:not(.noun-switcher__item--active) {
  opacity: 0.62;
}

.noun-switcher__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: currentColor;
}

.noun-switcher__label {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.noun-switcher__count {
  min-width: 28px;
  padding: 1px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-text-3) 14%, transparent);
  color: var(--om-text-2);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
  font-weight: 600;
  text-align: center;
  line-height: 1.5;
  transition:
    background-color 160ms ease,
    color 160ms ease;
}

.noun-switcher__item--active .noun-switcher__count {
  background: color-mix(in srgb, rgb(var(--primary-color)) 16%, transparent);
  color: rgb(var(--primary-color));
}

@media (max-width: 880px) {
  .noun-switcher {
    overflow-x: auto;
    flex-wrap: nowrap;
  }

  .noun-switcher__item--all::after {
    display: none;
  }
}
</style>

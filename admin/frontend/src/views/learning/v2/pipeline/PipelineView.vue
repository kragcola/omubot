<script setup lang="ts">
import { inject, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NEmpty, NIcon } from 'naive-ui'
import { ChevronForwardOutline } from '@vicons/ionicons5'
import type { useLearningConsole } from '../useLearningConsole'
import type { LearningStageKey, LearningItem, StageStripItem } from '../../types'
import ItemRow from './ItemRow.vue'

const router = useRouter()
const console = inject<ReturnType<typeof useLearningConsole>>('learningConsole')!

const nounOptions = [
  { key: 'slang', label: '黑话' },
  { key: 'style', label: '表达' },
  { key: 'episode', label: '情节' },
  { key: 'memory', label: '记忆' },
  { key: 'fact', label: '事实' },
  { key: 'graph_relation', label: '关系' },
]

function toggleNoun(key: string) {
  const current = console.activeNouns.value
  const idx = current.indexOf(key)
  if (idx >= 0) {
    console.activeNouns.value = current.filter(n => n !== key)
  } else {
    console.activeNouns.value = [...current, key]
  }
}

watch(() => console.activeNouns.value, () => console.fetchItems(), { deep: true })

const stageDefinitions: { key: LearningStageKey; eyebrow: string; label: string; description: string }[] = [
  { key: 'candidate', eyebrow: 'STAGE 1', label: '候选', description: '从群聊中提取的新词条，等待审核' },
  { key: 'review', eyebrow: 'STAGE 2', label: '待审', description: 'AI 或人工审核中的词条' },
  { key: 'approved', eyebrow: 'STAGE 3', label: '已生效', description: '已进入动态 Prompt 的词条' },
  { key: 'hits', eyebrow: 'STAGE 4', label: '命中', description: '在对话中被实际使用的词条' },
  { key: 'archived', eyebrow: 'STAGE 5', label: '归档', description: '已过期或被拒绝的词条' },
]

const stageStripItems = computed<StageStripItem[]>(() => {
  return stageDefinitions.map(def => {
    const stageData = console.stages.value?.[def.key] as any
    return {
      key: def.key,
      eyebrow: def.eyebrow,
      label: def.label,
      description: def.description,
      total: stageData?.total ?? 0,
      byNoun: stageData?.byNoun ?? stageData?.by_noun ?? {},
    } as StageStripItem
  })
})

function setStage(key: LearningStageKey) {
  if (console.activeStage.value === key) return
  console.activeStage.value = key
}

const activeStageTotal = computed(() => {
  const stageData = console.stages.value?.[console.activeStage.value] as any
  return stageData?.total ?? 0
})

function handleItemAction(item: LearningItem, action: string) {
  if (action === 'detail') {
    const query: Record<string, string> = {
      view: 'pipeline',
      id: item.id,
    }
    if (item.noun) query.noun = item.noun
    router.push({ path: '/learning', query })
    return
  }
  performItemAction(item, action)
}

async function performItemAction(item: LearningItem, action: string) {
  const noun = item.noun
  const id = item.id
  let url = ''

  if (noun === 'slang') {
    const termId = id.replace(/^slang-(?:hit-)?/, '')
    if (action === 'approve') url = `/api/admin/slang/terms/${termId}/approve`
    else if (action === 'reject') url = `/api/admin/slang/terms/${termId}/mute`
    else if (action === 'archive') url = `/api/admin/slang/terms/${termId}/expire`
    else if (action === 'restore') url = `/api/admin/slang/terms/${termId}/return-candidate`
  } else if (noun === 'style') {
    const exprId = id.replace(/^style-/, '')
    if (action === 'approve') url = `/api/admin/style/expressions/${exprId}/status`
    else if (action === 'reject') url = `/api/admin/style/expressions/${exprId}/status`
    else if (action === 'archive') url = `/api/admin/style/expressions/${exprId}/status`
    else if (action === 'restore') url = `/api/admin/style/expressions/${exprId}/status`
  } else if (noun === 'episode') {
    const epId = id.replace(/^episode-/, '')
    if (action === 'approve') url = `/api/admin/episodes/${epId}/approve`
    else if (action === 'reject') url = `/api/admin/episodes/${epId}/disable`
    else if (action === 'archive') url = `/api/admin/episodes/${epId}/disable`
    else if (action === 'restore') url = `/api/admin/episodes/${epId}/restore`
  }

  if (!url) return

  const body: Record<string, string> = {}
  if (noun === 'style') {
    if (action === 'approve') body.status = 'approved'
    else if (action === 'reject') body.status = 'rejected'
    else if (action === 'archive') body.status = 'muted'
    else if (action === 'restore') body.status = 'pending'
    body.actor = 'admin'
  }

  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  console.fetchItems()
}

interface TimeGroup {
  label: string
  dateKey: string
  items: LearningItem[]
}

const timeGroups = computed<TimeGroup[]>(() => {
  const now = new Date()
  const todayStr = now.toISOString().slice(0, 10)
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const yesterdayStr = yesterday.toISOString().slice(0, 10)

  const groups: Record<string, { dateKey: string; items: LearningItem[] }> = {}
  for (const item of console.items.value) {
    const dateStr = item.created_at?.slice(0, 10) ?? ''
    let label: string
    if (dateStr === todayStr) label = '今天'
    else if (dateStr === yesterdayStr) label = '昨天'
    else label = dateStr || '未知'
    if (!groups[label]) groups[label] = { dateKey: dateStr, items: [] }
    groups[label].items.push(item)
  }

  const order = ['今天', '昨天']
  const sorted = Object.entries(groups).sort(([a], [b]) => {
    if (a === '未知') return 1
    if (b === '未知') return -1
    const ai = order.indexOf(a)
    const bi = order.indexOf(b)
    if (ai !== -1 && bi !== -1) return ai - bi
    if (ai !== -1) return -1
    if (bi !== -1) return 1
    return b.localeCompare(a)
  })
  return sorted.map(([label, group]) => ({ label, dateKey: group.dateKey, items: group.items }))
})
</script>

<template>
  <div class="pipeline">
    <!-- Stage strip — card-based, matches old learning page -->
    <div class="pipeline__stage-strip" aria-label="学习管道阶段">
      <template v-for="(stage, index) in stageStripItems" :key="stage.key">
        <button
          type="button"
          class="stage-card"
          :class="{
            'stage-card--active': stage.key === console.activeStage.value,
            'stage-card--empty': stage.total === 0,
          }"
          :disabled="stage.key === console.activeStage.value"
          @click="setStage(stage.key)"
        >
          <span class="stage-card__eyebrow">{{ stage.eyebrow }}</span>
          <span class="stage-card__main">
            <span class="stage-card__label">{{ stage.label }}</span>
            <span v-if="stage.key === 'candidate' && console.candidateSub.value.all > 0" class="stage-card__count-split">
              <span class="stage-card__count">{{ console.candidateSub.value.unscanned }}</span>
              <span class="stage-card__count-sep">/</span>
              <span class="stage-card__count stage-card__count--info">{{ console.candidateSub.value.ai_kept }}</span>
            </span>
            <span v-else class="stage-card__count">{{ stage.total }}</span>
          </span>
          <span class="stage-card__desc">{{ stage.description }}</span>
        </button>
        <span v-if="index < stageStripItems.length - 1" class="pipeline__arrow" aria-hidden="true">
          <NIcon :component="ChevronForwardOutline" :size="18" />
        </span>
      </template>
    </div>

    <!-- Noun filter -->
    <div class="pipeline__filter">
      <span class="pipeline__filter-label">筛选</span>
      <div class="pipeline__filter-chips">
        <button
          v-for="opt in nounOptions"
          :key="opt.key"
          class="pipeline__filter-chip"
          :class="{ 'pipeline__filter-chip--active': console.activeNouns.value.includes(opt.key) }"
          @click="toggleNoun(opt.key)"
        >
          <span class="pipeline__filter-check">{{ console.activeNouns.value.includes(opt.key) ? '✓' : '' }}</span>
          {{ opt.label }}
        </button>
      </div>
      <button
        v-if="console.activeNouns.value.length > 0"
        class="pipeline__filter-reset"
        @click="console.activeNouns.value = []"
      >
        清除筛选
      </button>
    </div>

    <!-- Content -->
    <div v-if="console.items.value.length > 0" class="pipeline__content">
      <div v-for="group in timeGroups" :key="group.label" class="pipeline__group">
        <div class="pipeline__group-header">
          <span class="pipeline__group-label">{{ group.label }}</span>
          <span class="pipeline__group-count">{{ console.dateCounts.value[group.dateKey] ?? group.items.length }} 条</span>
        </div>
        <div class="pipeline__group-body">
          <ItemRow
            v-for="item in group.items"
            :key="item.id"
            :item="item"
            :stage="console.activeStage.value"
            @action="handleItemAction"
          />
        </div>
      </div>

      <div v-if="console.hasMore.value" class="pipeline__load-more">
        <NButton
          size="small"
          secondary
          :loading="console.pipelineLoading.value"
          @click="console.fetchItems(true)"
        >
          加载更多
        </NButton>
      </div>
    </div>

    <div v-else-if="!console.pipelineLoading.value" class="pipeline__empty">
      <NEmpty description="当前阶段暂无词条" />
    </div>

    <div v-else class="pipeline__loading">
      加载中…
    </div>
  </div>
</template>

<style scoped>
.pipeline {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 16px;
  border-radius: 16px;
  background: var(--om-surface-2);
  min-width: 0;
  overflow: hidden;
}

/* Stage strip — card-based flow visualization */
.pipeline__stage-strip {
  display: flex;
  align-items: stretch;
  gap: 12px;
  overflow-x: auto;
  padding: 4px 0 12px;
}

.stage-card {
  display: flex;
  flex: 1 0 148px;
  min-width: 148px;
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

.stage-card__desc {
  overflow: hidden;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-card__count-split {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stage-card__count-sep {
  font-size: 20px;
  font-weight: 300;
  color: var(--om-text-3);
}

.stage-card__count--info {
  color: var(--om-info);
}

.pipeline__arrow {
  display: inline-flex;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  color: var(--om-text-3);
}

/* Noun filter */
.pipeline__filter {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pipeline__filter-label {
  flex-shrink: 0;
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.pipeline__filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pipeline__filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 1.5px solid var(--om-border);
  border-radius: 6px;
  background: transparent;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.pipeline__filter-chip:hover {
  border-color: var(--om-border-strong);
  background: var(--om-fill);
  color: var(--om-text-1);
}

.pipeline__filter-check {
  display: inline-block;
  width: 14px;
  font-size: 11px;
  color: rgb(var(--primary-color));
  font-weight: 700;
}

.pipeline__filter-chip--active {
  border-color: rgb(var(--primary-color));
  background: rgba(var(--primary-color), 0.06);
  color: rgb(var(--primary-color));
  font-weight: 600;
}

.pipeline__filter-reset {
  flex-shrink: 0;
  padding: 0;
  border: none;
  background: none;
  color: var(--om-text-3);
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
  transition: color 0.15s;
}

.pipeline__filter-reset:hover {
  color: var(--om-text-1);
}

/* Content */
.pipeline__content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.pipeline__group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pipeline__group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.pipeline__group-label {
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.pipeline__group-count {
  color: var(--om-text-3);
  font-size: 11px;
}

.pipeline__group-body {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.pipeline__load-more {
  display: flex;
  justify-content: center;
  padding: 12px 0;
}

.pipeline__empty {
  padding: 60px 0;
}

.pipeline__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: var(--om-text-3);
  font-size: 13px;
}

@media (max-width: 760px) {
  .pipeline__stage-strip {
    align-items: stretch;
  }

  .stage-card {
    flex-basis: 140px;
    min-width: 140px;
  }
}
</style>

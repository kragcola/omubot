<script setup lang="ts">
import { ref, computed } from 'vue'
import { NButton, NTag, NIcon } from 'naive-ui'
import { ChevronDownOutline, ChevronForwardOutline } from '@vicons/ionicons5'
import type { LearningItem, LearningStageKey } from '../../types'

const props = defineProps<{
  item: LearningItem
  stage: LearningStageKey
}>()

const emit = defineEmits<{
  (e: 'action', item: LearningItem, action: string): void
}>()

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
}

const statusTone = computed(() => {
  const s = props.item.status
  if (s === 'approved') return 'success'
  if (s === 'candidate') return 'pending'
  if (s === 'archived' || s === 'hits') return 'neutral'
  return 'warning'
})

const aiStatusTag = computed(() => {
  return props.item.tags?.find(t => t.key === 'ai_status') || null
})

const aiStatusClass = computed(() => {
  const v = aiStatusTag.value?.value
  if (v === 'unscanned') return 'item-row__ai--unscanned'
  if (v === 'ai_kept') return 'item-row__ai--kept'
  if (v === 'ai_approved') return 'item-row__ai--approved'
  if (v === 'ai_rejected') return 'item-row__ai--rejected'
  return ''
})

const confidenceText = computed(() => {
  const c = props.item.confidence
  if (c == null) return ''
  return `${(c * 100).toFixed(0)}%`
})

const groupLabel = computed(() => {
  const g = props.item.group_id
  if (!g) return '全局'
  return `群${g.slice(-4)}`
})

const timeLabel = computed(() => {
  const t = props.item.created_at
  if (!t) return ''
  return t.slice(11, 16)
})

interface StageAction {
  label: string
  type: 'success' | 'error' | 'warning' | 'info' | 'default'
  action: string
}

const actions = computed<StageAction[]>(() => {
  switch (props.stage) {
    case 'candidate':
      return [
        { label: '通过', type: 'success', action: 'approve' },
        { label: '拒绝', type: 'error', action: 'reject' },
      ]
    case 'review':
      return [
        { label: '通过', type: 'success', action: 'approve' },
        { label: '退回', type: 'warning', action: 'return' },
      ]
    case 'approved':
      return [
        { label: '归档', type: 'warning', action: 'archive' },
      ]
    case 'hits':
      return [
        { label: '详情', type: 'info', action: 'detail' },
      ]
    case 'archived':
      return [
        { label: '恢复', type: 'success', action: 'restore' },
      ]
    default:
      return []
  }
})
</script>

<template>
  <div
    class="item-row"
    :class="[`item-row--${statusTone}`, { 'item-row--expanded': expanded }]"
    @click="toggle"
  >
    <div class="item-row__main">
      <span class="item-row__noun">{{ item.kind_label }}</span>
      <span v-if="aiStatusTag" class="item-row__ai" :class="aiStatusClass">{{ aiStatusTag.label }}</span>
      <strong class="item-row__term">{{ item.content }}</strong>
      <span v-if="item.content_full && item.content_full !== item.content" class="item-row__meaning">
        {{ item.content_full }}
      </span>
      <span class="item-row__spacer" />
      <button class="item-row__config" @click.stop="emit('action', item, 'detail')">
        <span class="item-row__config-icon">
          <NIcon :component="ChevronForwardOutline" :size="12" />
        </span>
        <span class="item-row__config-text">配置</span>
      </button>
      <span class="item-row__meta">{{ groupLabel }} · {{ timeLabel }}</span>
      <span v-if="confidenceText" class="item-row__conf">{{ confidenceText }}</span>
      <div class="item-row__actions" @click.stop>
        <NButton
          v-for="act in actions"
          :key="act.action"
          size="tiny"
          :type="act.type"
          secondary
          @click="emit('action', item, act.action)"
        >
          {{ act.label }}
        </NButton>
      </div>
      <button
        class="item-row__expand-btn"
        :class="{ 'item-row__expand-btn--open': expanded }"
        :aria-label="expanded ? '收起详情' : '展开详情'"
        :aria-expanded="expanded"
        @click.stop="toggle"
      >
        <NIcon :component="ChevronDownOutline" :size="14" />
      </button>
    </div>

    <div v-if="expanded" class="item-row__detail">
      <div v-if="item.content_full" class="item-row__detail-field">
        <span class="item-row__detail-label">完整内容</span>
        <span class="item-row__detail-value">{{ item.content_full }}</span>
      </div>
      <div class="item-row__detail-field">
        <span class="item-row__detail-label">类型</span>
        <NTag size="small" :bordered="false" round>{{ item.kind_label }}</NTag>
      </div>
      <div class="item-row__detail-field">
        <span class="item-row__detail-label">进入时间</span>
        <span class="item-row__detail-value">{{ item.created_at }}</span>
      </div>
      <div v-if="item.tags?.length" class="item-row__detail-field">
        <span class="item-row__detail-label">标签</span>
        <span class="item-row__detail-tags">
          <NTag v-for="tag in item.tags" :key="tag.key" size="small" :bordered="false" round>
            {{ tag.label }}
          </NTag>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.item-row {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 10px 14px 10px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  cursor: pointer;
  overflow: hidden;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.item-row::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.item-row--success::before { background: var(--om-success); opacity: 1; }
.item-row--pending::before { background: var(--om-warning); opacity: 1; }
.item-row--warning::before { background: var(--om-warning); opacity: 0.85; }
.item-row--neutral::before { background: var(--om-text-3); opacity: 0.35; }

.item-row:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.item-row__main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 28px;
  min-width: 0;
  overflow: hidden;
}

.item-row__noun {
  flex-shrink: 0;
  padding: 3px 10px;
  border: 1px solid var(--om-border-strong);
  border-radius: 6px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.item-row__ai {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.item-row__ai--unscanned {
  background: var(--om-fill);
  color: var(--om-text-3);
  border: 1px solid var(--om-border);
}

.item-row__ai--kept {
  background: color-mix(in srgb, var(--om-info) 10%, transparent);
  color: var(--om-info);
  border: 1px solid color-mix(in srgb, var(--om-info) 25%, transparent);
}

.item-row__ai--approved {
  background: color-mix(in srgb, var(--om-success) 10%, transparent);
  color: var(--om-success);
  border: 1px solid color-mix(in srgb, var(--om-success) 25%, transparent);
}

.item-row__ai--rejected {
  background: color-mix(in srgb, var(--om-warning) 12%, transparent);
  color: var(--om-warning);
  border: 1px solid color-mix(in srgb, var(--om-warning) 30%, transparent);
}

.item-row__term {
  color: var(--om-info);
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}

.item-row__meaning {
  overflow: hidden;
  color: var(--om-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.item-row__spacer {
  flex: 1;
}

.item-row__meta {
  flex-shrink: 0;
  width: 90px;
  text-align: right;
  color: var(--om-text-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.item-row__conf {
  flex-shrink: 0;
  width: 36px;
  text-align: center;
  font-size: 11px;
  color: var(--om-text-2);
  background: var(--om-fill);
  padding: 2px 7px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}

.item-row__config {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 12px 3px 6px;
  border: 1.5px solid color-mix(in srgb, var(--om-info) 32%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
  color: var(--om-info);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  white-space: nowrap;
}

.item-row__actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  width: 100px;
  justify-content: flex-end;
}

.item-row__config-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--om-info);
  color: #fff;
}

.item-row__config-text {
  line-height: 1;
}

.item-row__config:hover {
  border-color: var(--om-info);
  background: var(--om-info);
  color: #fff;
}

.item-row__config:hover .item-row__config-icon {
  background: rgba(255, 255, 255, 0.3);
}

.item-row__expand-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--om-text-3);
  cursor: pointer;
  transition: transform 0.2s ease, color 0.15s ease;
}

.item-row__expand-btn:hover {
  color: var(--om-text-1);
}

.item-row__expand-btn--open {
  transform: rotate(180deg);
}

.item-row__detail {
  margin-top: 10px;
  padding: 12px 14px;
  background: var(--om-fill);
  border-radius: 8px;
  display: grid;
  gap: 10px;
}

.item-row__detail-field {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
}

.item-row__detail-label {
  flex-shrink: 0;
  width: 64px;
  font-size: 12px;
  color: var(--om-text-3);
  font-weight: 500;
}

.item-row__detail-value {
  color: var(--om-text-1);
  line-height: 1.5;
}

.item-row__detail-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
</style>

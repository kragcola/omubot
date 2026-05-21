<script setup lang="ts">
import { driftStatusLabel } from '../helpers/badges'
import { confidenceText } from '../helpers/formatters'
import type { SlangDriftReview } from '../helpers/types'

defineProps<{
  drift: SlangDriftReview
}>()

const emit = defineEmits<{
  (e: 'action', drift: SlangDriftReview, action: 'accept' | 'reject' | 'alias' | 'mute'): void
}>()
</script>

<template>
  <div class="slang-drift-row">
    <header class="slang-drift-row__head">
      <div class="slang-drift-row__id">
        <strong class="slang-drift-row__term">{{ drift.term }}</strong>
        <NTag round size="tiny" type="warning">语义漂移</NTag>
        <span class="slang-drift-row__meta">
          {{ drift.group_id ? `群 ${drift.group_id}` : '全局' }}
          <span class="slang-drift-row__sep">·</span>
          {{ confidenceText(drift.confidence) }}
          <span class="slang-drift-row__sep">·</span>
          {{ driftStatusLabel(drift.status) }}
        </span>
      </div>
      <div class="slang-drift-row__actions">
        <NTooltip placement="top">
          <template #trigger>
            <NButton size="tiny" secondary @click="emit('action', drift, 'reject')">
              保留旧义
            </NButton>
          </template>
          忽略新证据，词条释义维持现状（左侧"现有释义"）。
        </NTooltip>
        <NTooltip placement="top">
          <template #trigger>
            <NButton size="tiny" type="success" secondary @click="emit('action', drift, 'accept')">
              采纳新义
            </NButton>
          </template>
          用新证据释义覆盖现有释义（右侧"新证据释义"），并合并别名。
        </NTooltip>
        <NTooltip placement="top">
          <template #trigger>
            <NButton size="tiny" secondary @click="emit('action', drift, 'alias')">
              转成别名
            </NButton>
          </template>
          认为这只是同义说法，把新写法并入词条 alias 列表，不改主释义。
        </NTooltip>
        <NTooltip placement="top">
          <template #trigger>
            <NButton size="tiny" secondary type="error" @click="emit('action', drift, 'mute')">
              静音
            </NButton>
          </template>
          整个词条静音：以后不再注入到 prompt，也不再触发漂移检测。
        </NTooltip>
      </div>
    </header>

    <div class="slang-drift-row__compare">
      <div class="slang-drift-row__pane">
        <span class="slang-drift-row__label">现有释义</span>
        <p>{{ drift.old_meaning || '未记录' }}</p>
      </div>
      <div class="slang-drift-row__pane slang-drift-row__pane--new">
        <span class="slang-drift-row__label">新证据释义</span>
        <p>{{ drift.new_meaning || '未记录' }}</p>
      </div>
    </div>

    <p v-if="drift.evidence || drift.reason" class="slang-drift-row__evidence">
      <span class="slang-drift-row__label">证据</span>
      {{ drift.evidence || drift.reason }}
    </p>
  </div>
</template>

<style scoped>
.slang-drift-row {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  transition: border-color 0.12s, box-shadow 0.12s;
}

.slang-drift-row:hover {
  border-color: color-mix(in srgb, var(--om-border-strong) 60%, transparent);
  box-shadow: 0 1px 4px color-mix(in srgb, var(--om-shadow-sm) 40%, transparent);
}

.slang-drift-row__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.slang-drift-row__id {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.slang-drift-row__term {
  color: var(--om-info);
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}

.slang-drift-row__meta {
  color: var(--om-text-3);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.slang-drift-row__sep {
  margin: 0 2px;
  color: var(--om-text-3);
}

.slang-drift-row__actions {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
}

.slang-drift-row__compare {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.slang-drift-row__pane {
  position: relative;
  padding: 8px 10px 8px 14px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.slang-drift-row__pane::before {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 6px;
  width: 2px;
  border-radius: 2px;
  background: var(--om-border-strong);
}

.slang-drift-row__pane--new::before {
  background: var(--om-warning);
}

.slang-drift-row__label {
  display: inline-block;
  margin-bottom: 2px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.slang-drift-row__pane p {
  margin: 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.55;
}

.slang-drift-row__evidence {
  margin: 0;
  padding: 6px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--om-surface-2) 60%, transparent);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}

.slang-drift-row__evidence .slang-drift-row__label {
  margin-right: 6px;
  margin-bottom: 0;
}

@media (max-width: 720px) {
  .slang-drift-row__head {
    flex-direction: column;
    align-items: flex-start;
  }

  .slang-drift-row__compare {
    grid-template-columns: 1fr;
  }
}
</style>

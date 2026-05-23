<script setup lang="ts">
import type { SlangQueueMode, SlangSummary } from '../helpers/types'

const props = defineProps<{
  summary: SlangSummary
  embedded?: boolean
}>()

const emit = defineEmits<{
  (e: 'switch-queue-mode', mode: SlangQueueMode): void
}>()

function onSwitchMode(mode: SlangQueueMode) {
  if (props.embedded) return
  emit('switch-queue-mode', mode)
}

function statusColor(status: string): string {
  if (status === 'success') return 'var(--om-success)'
  if (status === 'failed' || status === 'cancelled' || status === 'abandoned') return 'var(--om-danger)'
  if (status === 'running') return 'var(--om-info)'
  return 'var(--om-text-3)'
}

function statusText(status: string): string {
  const map: Record<string, string> = {
    success: '成功', failed: '失败', running: '运行中',
    cancelled: '已取消', abandoned: '已放弃',
  }
  return map[status] || status || '—'
}
</script>

<template>
  <div class="slang-summary-bar">
    <div class="slang-summary-bar__row">
      <component
        :is="props.embedded ? 'span' : 'button'"
        class="slang-summary-bar__count slang-summary-bar__count--warning"
        @click="onSwitchMode('candidate')"
      >
        <span class="slang-summary-bar__label">待清池</span>
        <strong>{{ summary.candidate_unreviewed_count + summary.under_observation_count + summary.drift_count }}</strong>
      </component>
      <component
        :is="props.embedded ? 'span' : 'button'"
        class="slang-summary-bar__count slang-summary-bar__count--success"
        @click="onSwitchMode('approved')"
      >
        <span class="slang-summary-bar__label">已批准</span>
        <strong>{{ summary.approved_count }}</strong>
      </component>
      <component
        :is="props.embedded ? 'span' : 'button'"
        class="slang-summary-bar__count slang-summary-bar__count--danger"
        @click="onSwitchMode('ai_rejected')"
      >
        <span class="slang-summary-bar__label">已否决</span>
        <strong>{{ summary.ai_rejected_count }}</strong>
      </component>

      <span class="slang-summary-bar__sep"></span>

      <span class="slang-summary-bar__pill">
        上次抽取 <strong>{{ summary.last_extracted_at?.replace('T', ' ').slice(0, 16) || '—' }}</strong>
        · <span :style="{ color: statusColor(summary.latest_run_status) }">{{ statusText(summary.latest_run_status) }}</span>
      </span>
      <span class="slang-summary-bar__pill">
        今日命中 <strong>{{ summary.today_hits }}</strong>
      </span>
      <span class="slang-summary-bar__pill">
        活跃群 <strong>{{ summary.group_count }}</strong>
      </span>
      <component
        v-if="summary.drift_count > 0"
        :is="props.embedded ? 'span' : 'button'"
        class="slang-summary-bar__pill slang-summary-bar__pill--warn"
        @click="onSwitchMode('drift')"
      >
        ⚠ {{ summary.drift_count }} 个漂移待处理
      </component>
    </div>
  </div>
</template>

<style scoped>
.slang-summary-bar {
  margin-bottom: 14px;
  padding: 8px 14px;
  border-radius: 10px;
  background: var(--om-surface-2);
}

.slang-summary-bar__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.slang-summary-bar__sep {
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--om-border);
}

.slang-summary-bar__count {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 4px 10px;
  border: none;
  border-radius: 6px;
  background: var(--om-surface-solid);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s;
}

.slang-summary-bar__count:hover {
  background: color-mix(in srgb, var(--om-surface-solid) 85%, var(--om-text-1));
}

span.slang-summary-bar__count {
  cursor: default;
}

span.slang-summary-bar__count:hover {
  background: var(--om-surface-solid);
}

.slang-summary-bar__count strong {
  font-weight: 700;
  font-size: 14px;
}

.slang-summary-bar__count--warning strong { color: var(--om-warning); }
.slang-summary-bar__count--primary strong { color: var(--om-info); }
.slang-summary-bar__count--success strong { color: var(--om-success); }
.slang-summary-bar__count--danger strong { color: var(--om-danger); }
.slang-summary-bar__count--muted strong { color: var(--om-text-3); }

.slang-summary-bar__label {
  color: var(--om-text-2);
  font-size: 12px;
}

.slang-summary-bar__pill {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-summary-bar__pill strong {
  color: var(--om-text-1);
  font-weight: 600;
}

.slang-summary-bar__pill--warn {
  padding: 2px 8px;
  border: none;
  border-radius: 4px;
  background: color-mix(in srgb, var(--om-danger) 12%, transparent);
  color: var(--om-danger);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.slang-summary-bar__pill--warn:hover {
  background: color-mix(in srgb, var(--om-danger) 20%, transparent);
}
</style>

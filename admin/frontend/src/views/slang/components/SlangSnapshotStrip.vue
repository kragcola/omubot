<script setup lang="ts">
import type { SlangSummary } from '../helpers/types'

defineProps<{
  summary: SlangSummary
}>()

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    success: '成功',
    failed: '失败',
    running: '运行中',
    cancelled: '已取消',
    abandoned: '已放弃',
  }
  return map[status] || status || '—'
}

function statusAccent(status: string): string {
  if (status === 'success') return 'var(--om-success)'
  if (status === 'failed' || status === 'cancelled' || status === 'abandoned') return 'var(--om-danger)'
  if (status === 'running') return 'var(--om-info)'
  return 'var(--om-text-3)'
}
</script>

<template>
  <div class="slang-snapshot-strip">
    <span class="slang-snapshot-strip__pill">
      <span class="slang-snapshot-strip__label">上次抽取</span>
      <span class="slang-snapshot-strip__value">{{ summary.last_extracted_at || '—' }}</span>
    </span>
    <span class="slang-snapshot-strip__pill">
      <span class="slang-snapshot-strip__label">运行状态</span>
      <span class="slang-snapshot-strip__value" :style="{ color: statusAccent(summary.latest_run_status) }">
        {{ statusLabel(summary.latest_run_status) }}
      </span>
    </span>
    <span class="slang-snapshot-strip__pill">
      <span class="slang-snapshot-strip__label">今日命中</span>
      <span class="slang-snapshot-strip__value">{{ summary.today_hits }}</span>
    </span>
    <span class="slang-snapshot-strip__pill">
      <span class="slang-snapshot-strip__label">活跃群</span>
      <span class="slang-snapshot-strip__value">{{ summary.group_count }}</span>
    </span>
  </div>
</template>

<style scoped>
.slang-snapshot-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.slang-snapshot-strip__pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--om-surface-2);
  font-size: 12px;
  line-height: 1.4;
}

.slang-snapshot-strip__label {
  color: var(--om-text-3);
}

.slang-snapshot-strip__value {
  color: var(--om-text-1);
  font-weight: 500;
}
</style>

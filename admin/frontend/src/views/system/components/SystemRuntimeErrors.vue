<script setup lang="ts">
import { computed } from 'vue'

import { ShieldCheckmarkOutline } from '@vicons/ionicons5'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { runtimeErrorLevelLabel, runtimeErrorLevelType } from '../helpers/badges'
import { formatTimestamp } from '../helpers/formatters'
import type { RuntimeErrorPayload } from '../helpers/types'

interface Props {
  runtimeErrors: RuntimeErrorPayload | null
}

const props = defineProps<Props>()

const runtimeErrorSummary = computed(() => props.runtimeErrors?.summary || null)
const runtimeIssueGroups = computed(() => props.runtimeErrors?.groups || [])
</script>

<template>
  <AppPanelSection
    class="system-runtime-errors"
    eyebrow="Runtime Signals"
    title="关键错误"
  >
    <template #aside>
      <NTag size="small" round :type="(runtimeErrorSummary?.errors || 0) > 0 ? 'error' : 'success'">
        {{ runtimeErrorSummary?.errors || 0 }} error
      </NTag>
      <NTag size="small" round :type="(runtimeErrorSummary?.warnings || 0) > 0 ? 'warning' : 'default'">
        {{ runtimeErrorSummary?.warnings || 0 }} warning
      </NTag>
    </template>

    <div class="system-runtime-errors__summary">
      <div>
        <span>滚动记录</span>
        <strong>{{ runtimeErrorSummary?.total || 0 }}</strong>
      </div>
      <div>
        <span>唯一问题</span>
        <strong>{{ runtimeErrorSummary?.unique || 0 }}</strong>
      </div>
      <div>
        <span>容量</span>
        <strong>{{ runtimeErrors?.max_events || 0 }}</strong>
      </div>
    </div>

    <div v-if="runtimeIssueGroups.length" class="system-runtime-errors__list">
      <div
        v-for="issue in runtimeIssueGroups"
        :key="issue.signature"
        class="system-runtime-error-row"
        :class="`system-runtime-error-row--${issue.level.toLowerCase()}`"
      >
        <div class="system-runtime-error-row__main">
          <div class="system-runtime-error-row__head">
            <NTag size="small" round :type="runtimeErrorLevelType(issue.level)">
              {{ runtimeErrorLevelLabel(issue.level) }}
            </NTag>
            <strong>{{ issue.channel || issue.logger || 'runtime' }}</strong>
            <span>{{ issue.count }} 次</span>
          </div>
          <p>{{ issue.message }}</p>
          <small>
            首次 {{ formatTimestamp(issue.first_seen_at) }} · 最近 {{ formatTimestamp(issue.last_seen_at) }}
          </small>
        </div>
      </div>
    </div>

    <EmptyState
      v-else
      compact
      title="最近没有关键错误"
      description="运行期 WARNING / ERROR / CRITICAL 会在这里自动聚合，便于先看摘要再进入日志页定位。"
      :icon="ShieldCheckmarkOutline"
    />
  </AppPanelSection>
</template>

<style scoped>
.system-runtime-errors {
  margin-bottom: 24px;
}

.system-runtime-errors__summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.system-runtime-errors__summary div {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.system-runtime-errors__summary span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-runtime-errors__summary strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 800;
}

.system-runtime-errors__list {
  display: grid;
  gap: 10px;
}

.system-runtime-error-row {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-runtime-error-row--warning {
  border-color: color-mix(in srgb, var(--om-warning) 30%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 9%, var(--om-surface));
}

.system-runtime-error-row--error,
.system-runtime-error-row--critical {
  border-color: color-mix(in srgb, var(--om-danger) 30%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 9%, var(--om-surface));
}

.system-runtime-error-row__main {
  min-width: 0;
}

.system-runtime-error-row__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.system-runtime-error-row__head strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.system-runtime-error-row__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.system-runtime-error-row p {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-runtime-error-row small {
  display: block;
  margin-top: 8px;
  color: var(--om-text-3);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .system-runtime-errors__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .system-runtime-errors__summary {
    grid-template-columns: 1fr;
  }
}
</style>

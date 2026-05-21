<script setup lang="ts">
import { TimeOutline } from '@vicons/ionicons5'
import EmptyState from '../../../components/common/EmptyState.vue'
import { confidenceText } from '../helpers/formatters'
import type { SlangDriftReview, SlangPendingCandidate, SlangSummary } from '../helpers/types'

defineProps<{
  summary: SlangSummary
  driftReviews: SlangDriftReview[]
  pendingCandidates: SlangPendingCandidate[]
  pendingTotal: number
}>()

const emit = defineEmits<{
  (e: 'switch-queue-mode', mode: 'drift'): void
}>()
</script>

<template>
  <div class="slang-side-section slang-governance-section">
    <div class="slang-side-section__head">
      <strong>质量治理</strong>
      <NTag round size="small" :type="summary.drift_count ? 'warning' : 'success'">
        {{ summary.drift_count }} 个漂移
      </NTag>
    </div>
    <p class="slang-side-note">
      已批准词条遇到冲突新释义时，先进入漂移队列；处理前不会覆盖 Prompt 中的主释义。
    </p>
    <div v-if="driftReviews.length" class="slang-pending-list">
      <div v-for="item in driftReviews.slice(0, 4)" :key="item.drift_id" class="slang-pending-row">
        <div>
          <strong>{{ item.term }}</strong>
          <span>{{ item.new_meaning || item.reason || '等待处理' }}</span>
        </div>
        <NTag round size="small" type="warning">
          {{ confidenceText(item.confidence) }}
        </NTag>
      </div>
    </div>
    <NButton secondary block @click="emit('switch-queue-mode', 'drift')">
      查看漂移队列
    </NButton>
  </div>

  <div class="slang-side-section">
    <div class="slang-side-section__head">
      <strong>观察中候选</strong>
      <NTag round size="small">
        {{ pendingTotal }} 条
      </NTag>
    </div>
    <div v-if="pendingCandidates.length" class="slang-pending-list">
      <div v-for="item in pendingCandidates" :key="item.pending_id" class="slang-pending-row">
        <div>
          <strong>{{ item.term }}</strong>
          <span>{{ item.meaning || item.evidence || '等待更多出现证据' }}</span>
        </div>
        <NTag round size="small" type="warning">
          {{ item.count }} 次
        </NTag>
      </div>
    </div>
    <EmptyState
      v-else
      compact
      title="暂无观察中候选"
      description="未达到最小出现次数的抽取结果会先停在这里。"
      :icon="TimeOutline"
    />
  </div>
</template>

<style scoped>
.slang-side-section {
  display: grid;
  gap: 12px;
  margin-bottom: 18px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 68%, transparent);
}

.slang-governance-section {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 24%, var(--om-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, rgb(var(--primary-color)) 8%, transparent), transparent),
    color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-side-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.slang-side-section__head strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.slang-side-note {
  margin: -4px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.slang-pending-list {
  display: grid;
  gap: 10px;
}

.slang-pending-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid color-mix(in srgb, var(--om-border) 72%, transparent);
}

.slang-pending-row:first-child {
  border-top: 0;
}

.slang-pending-row strong {
  color: var(--om-text-1);
}

.slang-pending-row span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-pending-row > div {
  display: grid;
  min-width: 0;
  gap: 4px;
}
</style>

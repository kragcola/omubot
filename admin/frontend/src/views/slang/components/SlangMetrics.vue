<script setup lang="ts">
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  PricetagsOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'

import MetricCard from '../../../components/common/MetricCard.vue'
import type { SlangSummary } from '../helpers/types'

defineProps<{
  summary: SlangSummary
}>()
</script>

<template>
  <div class="slang-metric-grid">
    <MetricCard
      title="待审核"
      :value="summary.candidate_count"
      hint="候选默认不注入 Prompt"
      :icon="PricetagsOutline"
      accent="warning"
    />
    <MetricCard
      title="AI 通过"
      :value="summary.ai_review_count"
      :hint="`${summary.ai_pending_review_count} 条待人工复核`"
      :icon="SparklesOutline"
      accent="primary"
    />
    <MetricCard
      title="已批准"
      :value="summary.approved_count"
      hint="可进入当前群动态语境"
      :icon="CheckmarkCircleOutline"
      accent="success"
    />
    <MetricCard
      title="观察中"
      :value="summary.pending_count"
      hint="未达到最小出现次数，暂不打扰审核"
      :icon="TimeOutline"
      accent="info"
    />
    <MetricCard
      title="语义漂移"
      :value="summary.drift_count"
      hint="新释义待治理，不会直接进入 Prompt"
      :icon="AlertCircleOutline"
      accent="warning"
    />
  </div>
</template>

<style scoped>
.slang-metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(156px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

@media (max-width: 1180px) {
  .slang-metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .slang-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .slang-metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  FlashOutline,
  PricetagsOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import type { PipelineStages } from '../useLearningConsole'

const props = defineProps<{ stages: PipelineStages | null }>()

const kpis = computed(() => {
  const s = props.stages
  if (!s) return []
  const candidate = s.candidate?.total ?? 0
  const review = s.review?.total ?? 0
  const approved = s.approved?.total ?? 0
  const hits = s.hits?.total ?? 0
  return [
    {
      title: '候选',
      value: candidate,
      hint: '待提取或待审核的新词条',
      icon: PricetagsOutline,
      accent: 'info' as const,
    },
    {
      title: '待审',
      value: review,
      hint: `${candidate + review} 条在审核管线中`,
      icon: TimeOutline,
      accent: 'warning' as const,
    },
    {
      title: '已生效',
      value: approved,
      hint: '已进入动态 Prompt 的词条',
      icon: CheckmarkCircleOutline,
      accent: 'success' as const,
    },
    {
      title: '命中',
      value: hits,
      hint: '在对话中被实际使用的词条',
      icon: FlashOutline,
      accent: 'primary' as const,
    },
    {
      title: '归档',
      value: s.archived?.total ?? 0,
      hint: '已过期或被拒绝的词条',
      icon: AlertCircleOutline,
      accent: 'warning' as const,
    },
  ]
})
</script>

<template>
  <div class="kpi-grid">
    <MetricCard
      v-for="kpi in kpis"
      :key="kpi.title"
      :title="kpi.title"
      :value="kpi.value"
      :hint="kpi.hint"
      :icon="kpi.icon"
      :accent="kpi.accent"
    />
  </div>
</template>

<style scoped>
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(156px, 1fr));
  gap: 16px;
}

@media (max-width: 1180px) {
  .kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .kpi-grid {
    grid-template-columns: 1fr;
  }
}
</style>

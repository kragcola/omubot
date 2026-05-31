<script setup lang="ts">
import { inject } from 'vue'
import type { useLearningConsole } from '../useLearningConsole'
import KpiRow from './KpiRow.vue'
import ScheduleStatus from './ScheduleStatus.vue'
import TrendChart from './TrendChart.vue'
import ActivityFeed from './ActivityFeed.vue'

const console = inject<ReturnType<typeof useLearningConsole>>('learningConsole')!
</script>

<template>
  <div class="dashboard">
    <KpiRow :stages="console.stages.value" />

    <div class="dashboard__mid">
      <TrendChart :points="console.trend.value" />
      <ScheduleStatus :schedules="console.schedules.value" :autopilot="console.autopilotStatus.value" />
    </div>

    <ActivityFeed :events="console.activity.value" />
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 16px;
  border-radius: 16px;
  background: var(--om-surface-2);
}

.dashboard__mid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 920px) {
  .dashboard__mid {
    grid-template-columns: 1fr;
  }
}
</style>

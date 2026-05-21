<script setup lang="ts">
import {
  FlashOutline,
  GitNetworkOutline,
  PulseOutline,
  SparklesOutline,
} from '@vicons/ionicons5'

import MetricCard from '../../../components/common/MetricCard.vue'
import { formatDuration } from '../helpers/formatters'
import type { HealthInfo, SystemInfo } from '../helpers/types'

interface Props {
  health: HealthInfo | null
  system: SystemInfo | null
}

defineProps<Props>()
</script>

<template>
  <div class="system-metric-grid">
    <MetricCard
      title="Bot 状态"
      :value="health?.bot === 'running' ? '运行中' : '异常'"
      hint="管理端后端主进程"
      :icon="PulseOutline"
      accent="success"
    />
    <MetricCard
      title="NapCat"
      :value="health?.napcat === 'connected' ? '已连接' : '断开'"
      hint="消息适配层连接状态"
      :icon="GitNetworkOutline"
      :accent="health?.napcat === 'connected' ? 'info' : 'warning'"
    />
    <MetricCard
      title="运行时长"
      :value="formatDuration(health?.uptime_seconds)"
      hint="基于 `/health` 返回的 uptime"
      :icon="FlashOutline"
      accent="primary"
    />
    <MetricCard
      title="活跃会话"
      :value="system?.active_sessions ?? '--'"
      hint="Short-term memory 当前会话数"
      :icon="SparklesOutline"
      accent="warning"
    />
  </div>
</template>

<style scoped>
.system-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

@media (max-width: 1100px) {
  .system-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .system-metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import MetricCard from '../../../../components/common/MetricCard.vue'
import { useEpisodeConsoleInject } from './state'

const console_ = useEpisodeConsoleInject()
const { stats } = console_
</script>

<template>
  <div class="episode-fold-side">
    <div class="episode-fold-side__metrics">
      <MetricCard title="dry_run" :value="stats.dry_run" hint="刚写入" />
      <MetricCard title="candidate" :value="stats.candidate" hint="待审" accent="info" />
      <MetricCard title="approved" :value="stats.approved" hint="已审核" accent="success" />
      <MetricCard title="enabled_for_prompt" :value="stats.enabled_for_prompt" hint="正在注入" accent="warning" />
      <MetricCard title="disabled" :value="stats.disabled" hint="已停用" />
    </div>

    <NAlert
      v-if="stats.enabled_for_prompt === 0"
      type="info"
      :bordered="false"
      class="episode-fold-side__alert"
    >
      <strong>Phase B 提示</strong> · enabled_for_prompt 状态推进按钮在 Phase B BlockTraceBus 落地前不可用。当前可执行：批准 / 停用 / 恢复。
    </NAlert>
  </div>
</template>

<style scoped>
.episode-fold-side {
  display: grid;
  gap: 12px;
}

.episode-fold-side__metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.episode-fold-side__alert {
  border-radius: 12px;
}
</style>

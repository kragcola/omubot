<script setup lang="ts">
import MetricCard from '../../../../components/common/MetricCard.vue'
import { useMemoryConsoleInject } from './state'

const console_ = useMemoryConsoleInject()
const { stats } = console_
</script>

<template>
  <div class="memory-fold-side">
    <header class="memory-fold-side__header">
      <span class="memory-fold-side__eyebrow">Consolidator Pipeline</span>
      <p class="memory-fold-side__hint">记忆整合候选流水（每日 dry-run，approve 后 promote 到生产）。</p>
    </header>
    <div class="memory-fold-side__metrics">
      <MetricCard title="总候选" :value="stats.total" hint="全部可见候选" />
      <MetricCard title="dry_run" :value="stats.dry_run" hint="刚生成" accent="info" />
      <MetricCard title="已批准" :value="stats.approved" hint="approve 后 promote" accent="success" />
      <MetricCard title="已拒绝" :value="stats.rejected" hint="不进生产存储" accent="warning" />
      <MetricCard title="episode" :value="stats.episode" hint="可走 promote 桥" />
    </div>
  </div>
</template>

<style scoped>
.memory-fold-side {
  display: grid;
  gap: 12px;
}

.memory-fold-side__header {
  display: grid;
  gap: 4px;
}

.memory-fold-side__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.memory-fold-side__hint {
  margin: 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.memory-fold-side__metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
</style>

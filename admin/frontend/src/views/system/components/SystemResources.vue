<script setup lang="ts">
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import { formatPercent, meterColor } from '../helpers/formatters'
import type { SystemInfo } from '../helpers/types'

interface Props {
  system: SystemInfo | null
}

defineProps<Props>()
</script>

<template>
  <AppPanelSection
    class="system-panel"
    eyebrow="Resource Pressure"
    title="系统资源"
  >
    <template #aside>
      <NTag size="small">
        PID {{ system?.process?.pid ?? '--' }}
      </NTag>
    </template>

    <div class="system-resource-list">
      <div class="system-resource">
        <div class="system-resource__head">
          <span>CPU</span>
          <strong>{{ formatPercent(system?.cpu_percent) }}%</strong>
        </div>
        <NProgress
          type="line"
          :percentage="formatPercent(system?.cpu_percent)"
          :height="10"
          :show-indicator="false"
          :color="meterColor(system?.cpu_percent)"
        />
      </div>

      <div class="system-resource">
        <div class="system-resource__head">
          <span>内存</span>
          <strong>
            {{ system?.memory ? `${system.memory.used_gb} / ${system.memory.total_gb} GB` : '--' }}
          </strong>
        </div>
        <NProgress
          type="line"
          :percentage="formatPercent(system?.memory?.percent)"
          :height="10"
          :show-indicator="false"
          :color="meterColor(system?.memory?.percent)"
        />
      </div>

      <div class="system-resource">
        <div class="system-resource__head">
          <span>磁盘</span>
          <strong>
            {{ system?.disk ? `${system.disk.used_gb} / ${system.disk.total_gb} GB` : '--' }}
          </strong>
        </div>
        <NProgress
          type="line"
          :percentage="formatPercent(system?.disk?.percent)"
          :height="10"
          :show-indicator="false"
          :color="meterColor(system?.disk?.percent)"
        />
      </div>
    </div>

    <div class="system-stats-grid">
      <div class="system-stat-card">
        <span class="system-stat-card__label">进程内存</span>
        <strong class="system-stat-card__value">
          {{ system?.process?.memory_mb != null ? `${Number(system.process.memory_mb).toFixed(1)} MB` : '--' }}
        </strong>
      </div>
      <div class="system-stat-card">
        <span class="system-stat-card__label">线程数</span>
        <strong class="system-stat-card__value">
          {{ system?.process?.threads ?? '--' }}
        </strong>
      </div>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.system-panel {
  min-height: 100%;
}

.system-resource-list {
  display: grid;
  gap: 16px;
}

.system-resource {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.system-resource__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-resource__head strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.system-stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.system-stat-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-stat-card__label {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-stat-card__value {
  display: block;
  margin-top: 10px;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

@media (max-width: 760px) {
  .system-stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>

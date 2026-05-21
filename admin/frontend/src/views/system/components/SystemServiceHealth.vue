<script setup lang="ts">
import { HardwareChipOutline } from '@vicons/ionicons5'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import {
  memorySemanticLastError,
  serviceMetaTags,
  serviceStatusLabel,
  serviceTagType,
} from '../helpers/badges'
import type { ServicesHealth } from '../helpers/types'

interface Props {
  servicesHealth: ServicesHealth | null
  attentionCount: number
}

defineProps<Props>()
</script>

<template>
  <AppPanelSection
    class="system-service-health"
    eyebrow="Service Health"
    title="服务级健康"
  >
    <template #aside>
      <NTag size="small" round :type="serviceTagType(servicesHealth?.overall_status || 'unknown')">
        {{ serviceStatusLabel(servicesHealth?.overall_status || 'unknown') }}
      </NTag>
      <NTag v-if="attentionCount" size="small" round type="warning">
        {{ attentionCount }} 项需关注
      </NTag>
    </template>

    <div v-if="servicesHealth?.services?.length" class="system-service-grid">
      <div
        v-for="service in servicesHealth.services"
        :key="service.id"
        class="system-service-card"
        :class="`system-service-card--${service.status}`"
      >
        <div class="system-service-card__head">
          <div>
            <strong>{{ service.label }}</strong>
            <span>{{ service.metric || service.id }}</span>
          </div>
          <NTag size="small" round :type="serviceTagType(service.status)">
            {{ serviceStatusLabel(service.status) }}
          </NTag>
        </div>
        <div v-if="serviceMetaTags(service).length" class="system-service-card__meta">
          <NTag
            v-for="tag in serviceMetaTags(service)"
            :key="tag"
            size="small"
            round
          >
            {{ tag }}
          </NTag>
        </div>
        <p>{{ service.detail }}</p>
        <small v-if="memorySemanticLastError(service)" class="system-service-card__note">
          最近语义错误：{{ memorySemanticLastError(service) }}
        </small>
      </div>
    </div>

    <EmptyState
      v-else
      compact
      title="服务健康尚未返回"
      description="系统资源可用，但服务级聚合接口暂时没有返回数据。"
      :icon="HardwareChipOutline"
    />
  </AppPanelSection>
</template>

<style scoped>
.system-service-health {
  margin-bottom: 24px;
}

.system-service-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.system-service-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.system-service-card--warning,
.system-service-card--degraded {
  border-color: color-mix(in srgb, var(--om-warning) 32%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 10%, var(--om-surface));
}

.system-service-card--error {
  border-color: color-mix(in srgb, var(--om-danger) 32%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 10%, var(--om-surface));
}

.system-service-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-service-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-service-card__head span {
  display: block;
  margin-top: 5px;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-service-card p {
  margin: 12px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-service-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.system-service-card__note {
  display: block;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .system-service-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .system-service-grid {
    grid-template-columns: 1fr;
  }
}
</style>

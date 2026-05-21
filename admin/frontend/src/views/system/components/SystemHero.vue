<script setup lang="ts">
import { computed } from 'vue'

import AppCard from '../../../components/common/AppCard.vue'
import type { HealthInfo, SystemInfo, VersionInfo } from '../helpers/types'

interface Props {
  health: HealthInfo | null
  system: SystemInfo | null
  version: VersionInfo | null
  lastLoadedAt: string
  error: boolean
}

const props = defineProps<Props>()

const heroTitle = computed(() => {
  if (props.error) return '系统接口暂不可用'
  if (props.version?.has_update) return '运行稳定，但有可用更新'
  return '系统运行面正常'
})

const heroDescription = computed(() => {
  if (props.error) return '后端没有返回任何有效状态，先到日志页查看进程是否仍在运行。'
  if (props.version?.has_update && props.version.latest_tag) {
    return `检测到可用更新 ${props.version.latest_tag}，建议在维护窗口评估升级。下面卡片聚合了进程资源、运行策略和深度排查入口。`
  }
  return '下面卡片聚合了进程资源、版本与运行策略，便于在维护窗口快速判断是否需要介入。'
})

const processMemoryDisplay = computed(() => {
  const value = props.system?.process?.memory_mb
  if (value == null) return '--'
  return `${Number(value).toFixed(1)} MB`
})
</script>

<template>
  <AppCard bordered elevated class="system-hero">
    <div class="system-hero__main">
      <p class="system-hero__eyebrow">
        Runtime Snapshot
      </p>
      <h2 class="system-hero__title">
        {{ heroTitle }}
      </h2>
      <p class="system-hero__description">
        {{ heroDescription }}
      </p>
      <div class="system-hero__chips">
        <NTag round size="small">
          版本 {{ version?.version || 'unknown' }}
        </NTag>
        <NTag v-if="version?.has_update && version?.latest_tag" round size="small" type="info">
          可升级到 {{ version.latest_tag }}
        </NTag>
        <NTag round size="small">
          {{ lastLoadedAt ? `更新于 ${lastLoadedAt}` : '等待数据' }}
        </NTag>
      </div>
    </div>

    <div class="system-hero__aside">
      <div class="system-hero__aside-card">
        <span class="system-hero__aside-label">进程基线</span>
        <strong class="system-hero__aside-value">
          PID {{ system?.process?.pid ?? '--' }}
        </strong>
        <span class="system-hero__aside-meta">
          内存 {{ processMemoryDisplay }} · 线程 {{ system?.process?.threads ?? '--' }}
        </span>
      </div>
      <div class="system-hero__aside-card">
        <span class="system-hero__aside-label">活跃会话</span>
        <strong class="system-hero__aside-value">
          {{ system?.active_sessions ?? '--' }}
        </strong>
        <span class="system-hero__aside-meta">
          Bot/NapCat 状态请到仪表盘查看
        </span>
      </div>
    </div>
  </AppCard>
</template>

<style scoped>
.system-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.95fr);
  gap: 18px;
  overflow: hidden;
  margin-bottom: 24px;
  padding: 24px;
  border-radius: 24px;
  background: var(--om-hero-gradient);
}

.system-hero__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.system-hero__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: clamp(24px, 2.6vw, 30px);
  line-height: 1.15;
  letter-spacing: -0.03em;
}

.system-hero__description {
  margin: 14px 0 0;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.75;
}

.system-hero__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.system-hero__aside {
  display: grid;
  gap: 14px;
}

.system-hero__aside-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 110px;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.42);
}

.dark .system-hero__aside-card {
  background: rgba(18, 29, 34, 0.48);
}

.system-hero__aside-label {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.system-hero__aside-value {
  margin-top: 10px;
  color: var(--om-text-1);
  font-size: 18px;
  line-height: 1.4;
}

.system-hero__aside-meta {
  margin-top: 8px;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .system-hero {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .system-hero {
    padding: 20px;
  }
}
</style>

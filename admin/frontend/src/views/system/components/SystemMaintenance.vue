<script setup lang="ts">
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import { alertSeverityLabel, alertTagType, serviceTagType } from '../helpers/badges'
import type {
  HealthAlert,
  HealthAlertPolicy,
  MaintenanceWindow,
  RestartNotice,
} from '../helpers/types'

interface Props {
  maintenanceWindow: MaintenanceWindow | null
  restartNotice: RestartNotice | null
  healthAlerts: HealthAlert[]
  alertPolicy: HealthAlertPolicy | null
}

defineProps<Props>()
</script>

<template>
  <AppPanelSection
    class="system-ops-card"
    eyebrow="Maintenance Window"
    title="运维建议"
  >
    <template #aside>
      <NTag
        size="small"
        round
        :type="serviceTagType(maintenanceWindow?.severity || 'info')"
      >
        {{ maintenanceWindow?.recommended ? '建议维护窗口' : '常规观察' }}
      </NTag>
      <NTag v-if="healthAlerts.length" size="small" round type="warning">
        {{ healthAlerts.length }} 条告警
      </NTag>
      <NTag v-if="alertPolicy?.suppressed_count" size="small" round>
        折叠 {{ alertPolicy.suppressed_count }} 条轻量提醒
      </NTag>
      <NTag v-if="maintenanceWindow?.restart_recommended" size="small" round type="info">
        建议重启验证
      </NTag>
    </template>

    <div class="system-ops-grid">
      <div class="system-ops-panel">
        <div class="system-ops-panel__intro">
          <strong>{{ maintenanceWindow?.title || '当前运行面整体稳定' }}</strong>
          <p>{{ maintenanceWindow?.summary || '当前没有明显服务告警，常规调整仍建议先备份再操作。' }}</p>
          <small>{{ maintenanceWindow?.window_hint || '优先选择群聊低峰与管理员可回看系统页的时段。' }}</small>
        </div>

        <div v-if="maintenanceWindow?.reasons?.length" class="system-ops-list">
          <div
            v-for="reason in maintenanceWindow.reasons"
            :key="reason"
            class="system-ops-list__item"
          >
            <span class="system-ops-list__dot" />
            <span>{{ reason }}</span>
          </div>
        </div>

        <div v-if="maintenanceWindow?.checklist?.length" class="system-ops-checklist">
          <strong>建议顺序</strong>
          <div
            v-for="item in maintenanceWindow.checklist"
            :key="item"
            class="system-ops-checklist__item"
          >
            <span class="system-ops-checklist__index" />
            <span>{{ item }}</span>
          </div>
        </div>
      </div>

      <div class="system-ops-panel system-ops-panel--restart">
        <div class="system-ops-panel__intro">
          <strong>{{ restartNotice?.title || '在线重启说明' }}</strong>
          <p>{{ restartNotice?.summary || '这个按钮只会重启当前 Bot 进程，让配置与运行态重新收敛；它不会重建镜像。' }}</p>
        </div>

        <div v-if="restartNotice?.impact?.length" class="system-ops-list">
          <div
            v-for="item in restartNotice.impact"
            :key="item"
            class="system-ops-list__item"
          >
            <span class="system-ops-list__dot system-ops-list__dot--impact" />
            <span>{{ item }}</span>
          </div>
        </div>

        <div v-if="restartNotice?.works_for?.length" class="system-ops-group">
          <small class="system-ops-group__title">适合在线重启</small>
          <div class="system-ops-list">
            <div
              v-for="item in restartNotice.works_for"
              :key="item"
              class="system-ops-list__item"
            >
              <span class="system-ops-list__dot system-ops-list__dot--fit" />
              <span>{{ item }}</span>
            </div>
          </div>
        </div>

        <div v-if="restartNotice?.needs_rebuild?.length" class="system-ops-group">
          <small class="system-ops-group__title">需要先重建镜像</small>
          <div class="system-ops-list">
            <div
              v-for="item in restartNotice.needs_rebuild"
              :key="item"
              class="system-ops-list__item"
            >
              <span class="system-ops-list__dot system-ops-list__dot--warning" />
              <span>{{ item }}</span>
            </div>
          </div>
        </div>

        <small class="system-ops-panel__hint">
          {{ restartNotice?.window_hint || '改配置可直接在线重启；改代码或依赖请先重建镜像。' }}
        </small>
      </div>
    </div>

    <div v-if="healthAlerts.length" class="system-alert-list">
      <div
        v-for="alert in healthAlerts"
        :key="alert.id"
        class="system-alert-row"
        :class="`system-alert-row--${alert.severity}`"
      >
        <div class="system-alert-row__main">
          <div class="system-alert-row__head">
            <NTag size="small" round :type="alertTagType(alert.severity)">
              {{ alertSeverityLabel(alert.severity) }}
            </NTag>
            <strong>{{ alert.title }}</strong>
            <span v-if="alert.metric">{{ alert.metric }}</span>
          </div>
          <p>{{ alert.detail }}</p>
          <small>{{ alert.action }}</small>
        </div>
      </div>
    </div>

    <div v-else-if="alertPolicy?.suppressed_count" class="system-ops-muted">
      当前没有达到阈值的顶部告警，但还有 {{ alertPolicy.suppressed_count }} 条轻量提醒保留在下方服务级健康卡中。
    </div>

    <p v-if="alertPolicy?.summary" class="system-ops-policy-note">
      {{ alertPolicy.summary }}
    </p>
  </AppPanelSection>
</template>

<style scoped>
.system-ops-card {
  display: grid;
  gap: 18px;
  margin-bottom: 24px;
}

.system-ops-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(280px, 0.85fr);
  gap: 16px;
}

.system-ops-panel {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-ops-panel--restart {
  background: linear-gradient(135deg, rgba(var(--primary-color), 0.12), rgba(var(--primary-color), 0.03));
}

.system-ops-panel__intro {
  display: grid;
  gap: 6px;
}

.system-ops-panel__intro strong,
.system-ops-checklist strong {
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-ops-panel__intro p,
.system-ops-panel__hint {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.system-ops-panel__hint,
.system-ops-panel__intro small {
  color: var(--om-text-3);
}

.system-ops-panel__intro small {
  font-size: 12px;
  line-height: 1.6;
}

.system-ops-list,
.system-ops-checklist,
.system-alert-list {
  display: grid;
  gap: 10px;
}

.system-ops-group {
  display: grid;
  gap: 10px;
}

.system-ops-group__title {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.system-ops-list__item,
.system-ops-checklist__item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.system-ops-list__dot,
.system-ops-checklist__index {
  width: 8px;
  height: 8px;
  margin-top: 7px;
  border-radius: 999px;
  background: var(--om-warning);
}

.system-ops-list__dot--impact {
  background: var(--om-primary);
}

.system-ops-list__dot--fit {
  background: var(--om-success);
}

.system-ops-list__dot--warning {
  background: var(--om-warning);
}

.system-ops-checklist__index {
  background: var(--om-success);
}

.system-alert-list {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.system-alert-row {
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-alert-row--error {
  border-color: color-mix(in srgb, var(--om-danger) 32%, var(--om-border));
}

.system-alert-row--warning {
  border-color: color-mix(in srgb, var(--om-warning) 32%, var(--om-border));
}

.system-alert-row__main {
  display: grid;
  gap: 8px;
}

.system-alert-row__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.system-alert-row__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.system-alert-row__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.system-alert-row p,
.system-alert-row small {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.system-alert-row small {
  color: var(--om-text-3);
}

.system-ops-muted,
.system-ops-policy-note {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.7;
}

.system-ops-muted {
  padding: 12px 14px;
  border: 1px dashed var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 28%, transparent);
}

@media (max-width: 1100px) {
  .system-alert-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .system-ops-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .system-alert-list {
    grid-template-columns: 1fr;
  }
}
</style>

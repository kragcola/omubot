<script setup lang="ts">
import { computed } from 'vue'
import type { Schedules } from '../useLearningConsole'

const props = defineProps<{
  schedules: Schedules | null
  autopilot: { enabled: boolean; aggressiveness: string }
}>()

const taskMeta: Record<string, { label: string; desc: string }> = {
  slang_extract: { label: '黑话提取', desc: '从群聊消息中识别新黑话' },
  style_extract: { label: '表达提取', desc: '提取语言风格与表达模式' },
  consolidator: { label: '记忆整合', desc: '合并重复记忆、清理过期条目' },
  affection_scoring: { label: '亲密度计分', desc: '根据互动频率更新亲密度' },
}

const aggressivenessLabel = computed(() => {
  const map: Record<string, string> = {
    conservative: '保守',
    standard: '标准',
    aggressive: '激进',
  }
  return map[props.autopilot.aggressiveness] || props.autopilot.aggressiveness
})

function formatLastRun(ts: string | null): string {
  if (!ts) return '从未执行'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24) return `${diffH} 小时前`
  return ts.slice(5, 16).replace('T', ' ')
}
</script>

<template>
  <AppCard bordered elevated class="sched">
    <!-- Autopilot banner -->
    <div class="sched__autopilot" :class="{ 'sched__autopilot--on': autopilot.enabled }">
      <div class="sched__autopilot-indicator">
        <span class="sched__autopilot-dot" />
        <span class="sched__autopilot-label">AI 托管</span>
      </div>
      <span v-if="autopilot.enabled" class="sched__autopilot-level">{{ aggressivenessLabel }}模式</span>
      <span v-else class="sched__autopilot-off">未启用</span>
    </div>

    <!-- Task list -->
    <p class="sched__title">定时任务</p>
    <div v-if="schedules" class="sched__list">
      <div
        v-for="(task, key) in schedules"
        :key="key"
        class="sched__task"
        :class="{ 'sched__task--disabled': task.status === 'disabled' }"
      >
        <div class="sched__task-head">
          <span class="sched__task-dot" :class="`sched__task-dot--${task.status}`" />
          <span class="sched__task-name">{{ taskMeta[key]?.label || key }}</span>
          <span class="sched__task-status">{{ task.status === 'disabled' ? '停用' : task.status }}</span>
        </div>
        <div class="sched__task-body">
          <span class="sched__task-desc">{{ taskMeta[key]?.desc || '' }}</span>
          <span class="sched__task-time">{{ formatLastRun(task.last_run) }}</span>
        </div>
      </div>
    </div>
    <div v-else class="sched__empty">加载中…</div>
  </AppCard>
</template>

<style scoped>
.sched {
  padding: 0;
  min-height: 200px;
  display: flex;
  flex-direction: column;
}

.sched__autopilot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--om-border);
  background: var(--om-fill);
  border-radius: 12px 12px 0 0;
}

.sched__autopilot--on {
  background: rgba(var(--primary-color), 0.06);
  border-bottom-color: rgba(var(--primary-color), 0.15);
}

.sched__autopilot-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sched__autopilot-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--om-text-3);
}

.sched__autopilot--on .sched__autopilot-dot {
  background: rgb(var(--primary-color));
  box-shadow: 0 0 6px rgba(var(--primary-color), 0.4);
}

.sched__autopilot-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--om-text-1);
}

.sched__autopilot-level {
  font-size: 12px;
  font-weight: 600;
  color: rgb(var(--primary-color));
}

.sched__autopilot-off {
  font-size: 12px;
  color: var(--om-text-3);
}

.sched__title {
  margin: 0;
  padding: 16px 20px 12px;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.sched__list {
  display: flex;
  flex-direction: column;
  padding: 0 20px 16px;
  gap: 10px;
}

.sched__task {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--om-fill);
  transition: opacity 0.15s;
}

.sched__task--disabled {
  opacity: 0.5;
}

.sched__task-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sched__task-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--om-text-3);
}

.sched__task-dot--active {
  background: var(--om-success);
}

.sched__task-dot--idle {
  background: var(--om-info);
}

.sched__task-dot--disabled {
  background: var(--om-text-3);
}

.sched__task-name {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--om-text-1);
}

.sched__task-status {
  font-size: 11px;
  font-weight: 500;
  color: var(--om-text-3);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.sched__task-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  padding-left: 15px;
}

.sched__task-desc {
  font-size: 12px;
  color: var(--om-text-3);
}

.sched__task-time {
  font-size: 11px;
  color: var(--om-text-3);
  font-variant-numeric: tabular-nums;
}

.sched__empty {
  padding: 20px;
  color: var(--om-text-3);
  font-size: 13px;
}
</style>

<script setup lang="ts">
import type { Component } from 'vue'

export type LogLevel = 'debug' | 'info' | 'success' | 'warning' | 'error'

export interface LogPanelLine {
  id?: string | number
  level?: LogLevel
  timestamp?: string
  channel?: string
  text: string
}

const props = withDefaults(defineProps<{
  lines: LogPanelLine[]
  title?: string
  subtitle?: string
  height?: number | string
  autoScroll?: boolean
  paused?: boolean
  empty?: string
  icon?: Component
}>(), {
  title: '',
  subtitle: '',
  height: 360,
  autoScroll: true,
  paused: false,
  empty: '暂无日志',
  icon: undefined,
})

const emit = defineEmits<{
  (e: 'pause'): void
  (e: 'clear'): void
  (e: 'resume'): void
}>()

const bodyRef = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)

const heightStyle = computed(() => (typeof props.height === 'number' ? `${props.height}px` : props.height))

function scrollToBottom() {
  const el = bodyRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = bodyRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom.value = distance < 24
}

watch(
  () => props.lines.length,
  () => {
    if (!props.autoScroll || !stickToBottom.value || props.paused) return
    nextTick(scrollToBottom)
  },
)

onMounted(() => nextTick(scrollToBottom))

defineExpose({ scrollToBottom })
</script>

<template>
  <section class="log-panel">
    <header v-if="title || subtitle || $slots.actions" class="log-panel__head">
      <div class="log-panel__copy">
        <div v-if="title" class="log-panel__title">
          <NIcon v-if="icon" :component="icon" :size="14" />
          <span>{{ title }}</span>
        </div>
        <p v-if="subtitle" class="log-panel__subtitle">
          {{ subtitle }}
        </p>
      </div>
      <div v-if="$slots.actions" class="log-panel__actions">
        <slot name="actions" />
      </div>
    </header>

    <div
      ref="bodyRef"
      class="log-panel__body cus-scroll"
      :style="{ height: heightStyle }"
      @scroll="onScroll"
    >
      <ul v-if="lines.length" class="log-panel__list">
        <li
          v-for="(line, idx) in lines"
          :key="line.id ?? idx"
          class="log-panel__line"
          :class="line.level ? `log-panel__line--${line.level}` : null"
        >
          <span v-if="line.timestamp" class="log-panel__ts">{{ line.timestamp }}</span>
          <span v-if="line.channel" class="log-panel__channel">{{ line.channel }}</span>
          <span class="log-panel__text">{{ line.text }}</span>
        </li>
      </ul>
      <div v-else class="log-panel__empty">
        {{ empty }}
      </div>
    </div>

    <footer v-if="$slots.footer || paused" class="log-panel__foot">
      <span v-if="paused" class="log-panel__paused">已暂停</span>
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.log-panel {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-solid);
  overflow: hidden;
}

.log-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--om-border);
  background: var(--om-surface-2);
}

.log-panel__copy {
  min-width: 0;
}

.log-panel__title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.log-panel__subtitle {
  margin: 4px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
}

.log-panel__actions {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}

.log-panel__body {
  flex: 1;
  min-height: 0;
  padding: 12px 16px;
  background: var(--om-surface-solid);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
}

.log-panel__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.log-panel__line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 2px 0;
  color: var(--om-text-1);
  word-break: break-word;
}

.log-panel__ts {
  flex-shrink: 0;
  color: var(--om-text-3);
  font-variant-numeric: tabular-nums;
}

.log-panel__channel {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 11px;
  letter-spacing: 0.02em;
}

.log-panel__text {
  min-width: 0;
  flex: 1;
}

.log-panel__line--debug {
  color: var(--om-text-3);
}

.log-panel__line--info {
  color: var(--om-text-1);
}

.log-panel__line--success {
  color: var(--om-success);
}

.log-panel__line--warning {
  color: var(--om-warning);
}

.log-panel__line--error {
  color: var(--om-danger);
}

.log-panel__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 80px;
  color: var(--om-text-3);
  font-family: inherit;
  font-size: 12px;
}

.log-panel__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-top: 1px solid var(--om-border);
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 12px;
}

.log-panel__paused {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-warning) 12%, transparent);
  color: var(--om-warning);
  font-weight: 600;
}
</style>

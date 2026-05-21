<script setup lang="ts">
import type { Component } from 'vue'

type StateStatus = 'success' | 'warning' | 'error' | 'info' | 'default'

const props = withDefaults(defineProps<{
  status?: StateStatus
  label: string
  icon?: Component
  compact?: boolean
}>(), {
  status: 'default',
  icon: undefined,
  compact: false,
})

const statusClass = computed(() => `state-badge--${props.status}`)
</script>

<template>
  <span class="state-badge" :class="[statusClass, { 'state-badge--compact': compact }]">
    <span v-if="icon" class="state-badge__icon">
      <NIcon :component="icon" :size="compact ? 11 : 13" />
    </span>
    <span v-else class="state-badge__dot" />
    <span class="state-badge__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.state-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--badge-border);
  border-radius: 999px;
  background: var(--badge-bg);
  color: var(--badge-text);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease;
}

.state-badge--compact {
  height: 20px;
  padding: 0 8px;
  font-size: 11px;
}

.state-badge__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--badge-icon);
}

.state-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--badge-icon);
}

.state-badge__label {
  letter-spacing: 0.02em;
}

.state-badge--success {
  --badge-bg: color-mix(in srgb, var(--om-success) 10%, transparent);
  --badge-border: color-mix(in srgb, var(--om-success) 30%, transparent);
  --badge-text: var(--om-success);
  --badge-icon: var(--om-success);
}

.state-badge--warning {
  --badge-bg: color-mix(in srgb, var(--om-warning) 10%, transparent);
  --badge-border: color-mix(in srgb, var(--om-warning) 30%, transparent);
  --badge-text: var(--om-warning);
  --badge-icon: var(--om-warning);
}

.state-badge--error {
  --badge-bg: color-mix(in srgb, var(--om-danger) 10%, transparent);
  --badge-border: color-mix(in srgb, var(--om-danger) 30%, transparent);
  --badge-text: var(--om-danger);
  --badge-icon: var(--om-danger);
}

.state-badge--info {
  --badge-bg: color-mix(in srgb, var(--om-info) 10%, transparent);
  --badge-border: color-mix(in srgb, var(--om-info) 30%, transparent);
  --badge-text: var(--om-info);
  --badge-icon: var(--om-info);
}

.state-badge--default {
  --badge-bg: var(--om-surface-2);
  --badge-border: var(--om-border);
  --badge-text: var(--om-text-2);
  --badge-icon: var(--om-text-3);
}
</style>

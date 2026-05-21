<script setup lang="ts">
export interface ConfigStatusItem {
  label: string
  value: string
  type: 'success' | 'warning' | 'error' | 'info'
}

defineProps<{
  items: ConfigStatusItem[]
}>()
</script>

<template>
  <div class="config-status-strip">
    <div
      v-for="item in items"
      :key="item.label"
      class="config-status-pill"
      :class="`config-status-pill--${item.type}`"
    >
      <span>{{ item.label }}</span>
      <strong>{{ item.value }}</strong>
    </div>
  </div>
</template>

<style scoped>
.config-status-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.config-status-pill {
  display: grid;
  gap: 4px;
  min-height: 64px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface);
  box-shadow: var(--om-shadow-sm);
}

.config-status-pill span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.config-status-pill strong {
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.config-status-pill--success {
  border-color: color-mix(in srgb, var(--om-success) 35%, var(--om-border));
}

.config-status-pill--warning {
  border-color: color-mix(in srgb, var(--om-warning) 38%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 6%, var(--om-surface));
}

.config-status-pill--error {
  border-color: color-mix(in srgb, var(--om-danger) 38%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 5%, var(--om-surface));
}

.config-status-pill--info {
  border-color: color-mix(in srgb, var(--om-info) 32%, var(--om-border));
}

@media (max-width: 1180px) {
  .config-status-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .config-status-strip {
    grid-template-columns: 1fr;
  }
}
</style>

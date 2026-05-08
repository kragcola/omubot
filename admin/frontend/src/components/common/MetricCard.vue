<script setup lang="ts">
import type { Component } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  value: string | number
  hint?: string
  icon?: Component
  accent?: 'primary' | 'success' | 'warning' | 'info'
}>(), {
  hint: '',
  icon: undefined,
  accent: 'primary',
})

const accentClass = computed(() => `metric-card--${props.accent}`)
</script>

<template>
  <AppCard bordered elevated class="metric-card" :class="accentClass">
    <div class="metric-card__head">
      <p class="metric-card__title">
        {{ title }}
      </p>
      <div v-if="icon" class="metric-card__icon">
        <NIcon :component="icon" :size="18" />
      </div>
    </div>
    <div class="metric-card__value">
      {{ value }}
    </div>
    <p v-if="hint" class="metric-card__hint">
      {{ hint }}
    </p>
    <slot />
  </AppCard>
</template>

<style scoped>
.metric-card {
  position: relative;
  overflow: hidden;
  min-height: 152px;
  padding: 20px;
}

.metric-card::before {
  position: absolute;
  inset: 0 auto auto 0;
  width: 100%;
  height: 4px;
  background: var(--metric-accent);
  content: '';
}

.metric-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.metric-card__title {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.metric-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: 1px solid color-mix(in srgb, var(--metric-accent) 30%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, var(--metric-accent) 12%, transparent);
  color: var(--metric-accent);
}

.metric-card__value {
  margin-top: 18px;
  color: var(--om-text-1);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.1;
}

.metric-card__hint {
  margin: 14px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.metric-card--primary {
  --metric-accent: rgb(var(--primary-color));
}

.metric-card--success {
  --metric-accent: var(--om-success);
}

.metric-card--warning {
  --metric-accent: var(--om-warning);
}

.metric-card--info {
  --metric-accent: var(--om-info);
}
</style>

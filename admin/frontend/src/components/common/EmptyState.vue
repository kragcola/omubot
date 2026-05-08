<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{
  title: string
  description?: string
  icon?: Component
  compact?: boolean
}>(), {
  description: '',
  icon: undefined,
  compact: false,
})
</script>

<template>
  <div class="empty-state" :class="{ 'empty-state--compact': compact }">
    <div class="empty-state__orb">
      <NIcon v-if="icon" :component="icon" :size="compact ? 18 : 22" />
      <span v-else class="empty-state__dot" />
    </div>
    <div class="empty-state__copy">
      <h3 class="empty-state__title">
        {{ title }}
      </h3>
      <p v-if="description" class="empty-state__description">
        {{ description }}
      </p>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 220px;
  padding: 24px;
  text-align: center;
}

.empty-state--compact {
  min-height: 150px;
  gap: 12px;
}

.empty-state__orb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: var(--om-surface-2);
  color: rgb(var(--primary-color));
}

.empty-state__dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: currentColor;
  box-shadow: 0 0 0 8px rgba(var(--primary-color), 0.12);
}

.empty-state__copy {
  max-width: 360px;
}

.empty-state__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 600;
}

.empty-state__description {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}
</style>

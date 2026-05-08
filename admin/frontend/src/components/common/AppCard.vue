<script setup lang="ts">
withDefaults(defineProps<{
  bordered?: boolean
  elevated?: boolean
  embedded?: boolean
  interactive?: boolean
}>(), {
  bordered: false,
  elevated: false,
  embedded: false,
  interactive: false,
})
</script>

<template>
  <div
    class="om-card"
    :class="{
      'om-card--bordered': bordered,
      'om-card--elevated': elevated,
      'om-card--embedded': embedded,
      'om-card--interactive': interactive,
    }"
  >
    <slot />
  </div>
</template>

<style scoped>
.om-card {
  position: relative;
  border-radius: 16px;
  background: var(--om-surface);
  color: var(--om-text-1);
  backdrop-filter: blur(18px);
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.om-card--bordered {
  border: 1px solid var(--om-border);
}

.om-card--elevated {
  box-shadow: var(--om-shadow-sm);
}

.om-card--embedded {
  background: var(--om-surface-2);
}

.om-card--interactive:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  box-shadow: var(--om-shadow-md);
}
</style>

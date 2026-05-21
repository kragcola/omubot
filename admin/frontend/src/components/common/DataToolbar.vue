<script setup lang="ts">
withDefaults(defineProps<{
  label?: string
  count?: number | string
  dense?: boolean
}>(), {
  label: '',
  count: undefined,
  dense: false,
})
</script>

<template>
  <div class="data-toolbar" :class="{ 'data-toolbar--dense': dense }">
    <div v-if="label || count !== undefined || $slots.summary" class="data-toolbar__summary">
      <slot name="summary">
        <span v-if="label" class="data-toolbar__label">{{ label }}</span>
        <span v-if="count !== undefined" class="data-toolbar__count">{{ count }}</span>
      </slot>
    </div>
    <div v-if="$slots.filters" class="data-toolbar__filters">
      <slot name="filters" />
    </div>
    <div v-if="$slots.actions" class="data-toolbar__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.data-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.data-toolbar--dense {
  padding: 8px 12px;
}

.data-toolbar__summary {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  margin-right: auto;
}

.data-toolbar__label {
  color: var(--om-text-3);
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.data-toolbar__count {
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.data-toolbar__filters {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.data-toolbar__actions {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

@media (max-width: 720px) {
  .data-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .data-toolbar__summary,
  .data-toolbar__filters,
  .data-toolbar__actions {
    margin: 0;
  }
}
</style>

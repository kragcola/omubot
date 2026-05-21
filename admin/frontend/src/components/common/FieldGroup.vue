<script setup lang="ts">
withDefaults(defineProps<{
  label?: string
  helper?: string
  required?: boolean
  inline?: boolean
}>(), {
  label: '',
  helper: '',
  required: false,
  inline: false,
})
</script>

<template>
  <div class="field-group" :class="{ 'field-group--inline': inline }">
    <div v-if="label || $slots.label" class="field-group__head">
      <span class="field-group__label">
        <slot name="label">{{ label }}</slot>
        <em v-if="required" class="field-group__required">*</em>
      </span>
      <span v-if="$slots.aside" class="field-group__aside">
        <slot name="aside" />
      </span>
    </div>
    <div class="field-group__control">
      <slot />
    </div>
    <p v-if="helper || $slots.helper" class="field-group__helper">
      <slot name="helper">{{ helper }}</slot>
    </p>
  </div>
</template>

<style scoped>
.field-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-group--inline {
  flex-direction: row;
  align-items: center;
  gap: 16px;
}

.field-group--inline .field-group__head {
  flex: 0 0 140px;
  margin-bottom: 0;
}

.field-group--inline .field-group__control {
  flex: 1;
  min-width: 0;
}

.field-group--inline .field-group__helper {
  flex-basis: 100%;
}

.field-group__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.field-group__label {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.field-group__required {
  margin-left: 4px;
  color: var(--om-danger);
  font-style: normal;
}

.field-group__aside {
  color: var(--om-text-3);
  font-size: 12px;
}

.field-group__control {
  min-width: 0;
}

.field-group__helper {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 720px) {
  .field-group--inline {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .field-group--inline .field-group__head {
    flex: none;
  }
}
</style>

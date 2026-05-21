<script setup lang="ts">
import ConfigField from './ConfigField.vue'
import type { ConfigFieldSchema } from './types'

const props = withDefaults(defineProps<{
  field: ConfigFieldSchema
  values: Record<string, any>
  originalValues: Record<string, any>
  secretMasks?: Record<string, string>
  errors?: Record<string, string>
  /** Nesting depth — 0 = top-level container, 1+ = nested object */
  depth?: number
}>(), {
  secretMasks: () => ({}),
  errors: () => ({}),
  depth: 0,
})

const emit = defineEmits<{
  (e: 'update', payload: { path: string, value: any }): void
  (e: 'revert', payload: { path: string }): void
}>()

const headLabel = computed(() => props.field.display_label || props.field.label)
const headDescription = computed(() => props.field.help || props.field.description)
const isCollapsible = computed(() => props.depth >= 1)
const isOpen = ref(true)

function toggle() {
  if (!isCollapsible.value) return
  isOpen.value = !isOpen.value
}
</script>

<template>
  <div
    class="config-object"
    :class="[
      `config-object--depth-${depth}`,
      isCollapsible ? 'config-object--collapsible' : '',
      isCollapsible && !isOpen ? 'config-object--collapsed' : '',
    ]"
  >
    <button
      v-if="headLabel || headDescription"
      type="button"
      class="config-object__head"
      :class="{ 'config-object__head--static': !isCollapsible }"
      :aria-expanded="isCollapsible ? isOpen : undefined"
      @click="toggle"
    >
      <span v-if="isCollapsible" class="config-object__caret" :class="{ 'config-object__caret--open': isOpen }" aria-hidden="true">
        ▶
      </span>
      <span class="config-object__head-text">
        <strong v-if="headLabel">{{ headLabel }}</strong>
        <span v-if="headDescription">{{ headDescription }}</span>
      </span>
    </button>

    <div v-show="!isCollapsible || isOpen" class="config-object__body">
      <ConfigField
        v-for="child in field.children || []"
        :key="child.path"
        :field="child"
        :values="values"
        :original-values="originalValues"
        :errors="errors"
        :secret-masks="secretMasks"
        :depth="depth + 1"
        @update="emit('update', $event)"
        @revert="emit('revert', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.config-object {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-object--depth-1,
.config-object--depth-2,
.config-object--depth-3 {
  padding-left: 14px;
  border-left: 2px solid color-mix(in srgb, var(--om-border) 80%, transparent);
}

.config-object__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 4px 0;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.config-object__head--static {
  cursor: default;
}

.config-object__caret {
  display: inline-block;
  flex: 0 0 auto;
  transform: rotate(0deg);
  color: var(--om-text-3);
  font-size: 9px;
  line-height: 1;
  transition: transform 0.16s ease;
  user-select: none;
}

.config-object__caret--open {
  transform: rotate(90deg);
}

.config-object__head-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.config-object__head-text strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.config-object__head-text span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.55;
}

.config-object__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-object--collapsible.config-object--collapsed > .config-object__body {
  display: none;
}
</style>

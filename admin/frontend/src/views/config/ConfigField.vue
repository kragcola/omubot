<script setup lang="ts">
import { ArrowUndoOutline } from '@vicons/ionicons5'

import ConfigJsonInput from './ConfigJsonInput.vue'
import ConfigKvField from './ConfigKvField.vue'
import ConfigListField from './ConfigListField.vue'
import ConfigObjectGroup from './ConfigObjectGroup.vue'
import ConfigSecretInput from './ConfigSecretInput.vue'
import type { ConfigFieldSchema } from './types'

const props = withDefaults(defineProps<{
  field: ConfigFieldSchema
  values: Record<string, any>
  originalValues: Record<string, any>
  secretMasks?: Record<string, string>
  errors?: Record<string, string>
  /** Container nesting depth — 0 means top of a section, 1+ means inside an object group */
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

const fieldValue = computed(() => getValueByPath(props.values, props.field.path))
const originalValue = computed(() => getValueByPath(props.originalValues, props.field.path))
const error = computed(() => props.errors[props.field.path] || '')
const hasError = computed(() => Boolean(error.value))

const changed = computed(() => !sameValue(fieldValue.value, originalValue.value))

const fieldTitle = computed(() => props.field.display_label || props.field.label)
const fieldDescription = computed(() => props.field.help || props.field.description)

const isCompactKind = computed(() => {
  const kind = props.field.kind
  return kind === 'switch' || kind === 'select' || kind === 'number'
})

const riskLabel = computed(() => {
  if (props.field.risk_level === 'danger') return '高风险'
  if (props.field.risk_level === 'careful') return '谨慎修改'
  return ''
})
const riskTagType = computed(() => props.field.risk_level === 'danger' ? 'error' : 'warning')

const restartLabel = computed(() => {
  if (props.field.restart_hint === 'required') return '需重启'
  if (props.field.restart_hint === 'recommended') return '建议重启'
  return ''
})

const selectOptions = computed(() =>
  (props.field.options || []).map(option => ({ label: String(option), value: option })) as any[],
)

const maskedSecret = computed(() => props.secretMasks[props.field.path] || '')

function getValueByPath(values: Record<string, any>, dottedPath: string): any {
  const segments = dottedPath.split('.')
  let node: any = values
  for (const segment of segments) {
    if (!node || typeof node !== 'object') return undefined
    node = node[segment]
  }
  return node
}

function sameValue(left: any, right: any): boolean {
  return JSON.stringify(left ?? null) === JSON.stringify(right ?? null)
}

function update(value: any) {
  emit('update', { path: props.field.path, value })
}

function revert() {
  emit('revert', { path: props.field.path })
}
</script>

<template>
  <ConfigObjectGroup
    v-if="field.kind === 'object'"
    :field="field"
    :values="values"
    :original-values="originalValues"
    :errors="errors"
    :secret-masks="secretMasks"
    :depth="depth"
    @update="emit('update', $event)"
    @revert="emit('revert', $event)"
  />

  <FieldGroup
    v-else
    class="config-field"
    :class="{
      'config-field--error': hasError,
      'config-field--changed': changed && !hasError,
      'config-field--inline': isCompactKind,
    }"
    :label="fieldTitle"
    :required="field.required"
    :inline="isCompactKind"
  >
    <template v-if="riskLabel || restartLabel || changed" #aside>
      <NTag v-if="riskLabel" round size="small" :type="riskTagType">
        {{ riskLabel }}
      </NTag>
      <NTag v-if="restartLabel" round size="small" type="info">
        {{ restartLabel }}
      </NTag>
      <NTag v-if="changed" round size="small" type="warning" class="config-field__changed-tag">
        已修改
      </NTag>
      <NButton
        v-if="changed"
        tertiary
        size="tiny"
        class="config-field__revert"
        @click="revert"
      >
        <template #icon>
          <NIcon :component="ArrowUndoOutline" />
        </template>
        撤销
      </NButton>
    </template>

    <template #helper>
      <span v-if="fieldDescription" class="config-field__help">{{ fieldDescription }}</span>
      <span v-if="field.recommended || field.example" class="config-field__hints">
        <span v-if="field.recommended" class="config-field__chip">推荐：{{ field.recommended }}</span>
        <span v-if="field.example" class="config-field__chip">示例：{{ field.example }}</span>
      </span>
      <span v-if="hasError" class="config-field__error-text">{{ error }}</span>
    </template>

    <NSwitch
      v-if="field.kind === 'switch'"
      :value="Boolean(fieldValue)"
      @update:value="update"
    />

    <NInputNumber
      v-else-if="field.kind === 'number'"
      :value="typeof fieldValue === 'number' ? fieldValue : 0"
      :step="field.number_type === 'int' ? 1 : 0.1"
      :precision="field.number_type === 'int' ? 0 : undefined"
      class="config-field__number"
      @update:value="(value: number | null) => update(value ?? 0)"
    />

    <NSelect
      v-else-if="field.kind === 'select'"
      :value="fieldValue as any"
      :options="selectOptions"
      :consistent-menu-width="false"
      class="config-field__select"
      @update:value="update"
    />

    <ConfigSecretInput
      v-else-if="field.kind === 'text' && field.secret"
      :model-value="typeof fieldValue === 'string' ? fieldValue : ''"
      :mask="maskedSecret"
      :placeholder="field.example ? `尚未填写，例如 ${field.example}` : '尚未填写。请在此粘贴密钥'"
      @update:model-value="update"
    />

    <NInput
      v-else-if="field.kind === 'text'"
      :value="typeof fieldValue === 'string' ? fieldValue : ''"
      type="text"
      @update:value="update"
    />

    <ConfigListField
      v-else-if="field.kind === 'list'"
      :field="field"
      :model-value="Array.isArray(fieldValue) ? fieldValue : []"
      @update:model-value="update"
    />

    <ConfigKvField
      v-else-if="field.kind === 'kv'"
      :field="field"
      :model-value="(fieldValue && typeof fieldValue === 'object' && !Array.isArray(fieldValue)) ? fieldValue : {}"
      @update:model-value="update"
    />

    <ConfigJsonInput
      v-else
      :model-value="fieldValue"
      @update:model-value="update"
    />
  </FieldGroup>
</template>

<style scoped>
.config-field {
  position: relative;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 76%, transparent);
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    box-shadow 0.16s ease;
}

.config-field--inline {
  /* In inline (label left / control right) layout, NSwitch can sit flush right */
  align-items: center;
}

.config-field--changed {
  border-color: color-mix(in srgb, var(--om-warning) 35%, var(--om-border));
  box-shadow: inset 3px 0 0 0 color-mix(in srgb, var(--om-warning) 70%, transparent);
}

.config-field--error {
  border-color: color-mix(in srgb, var(--om-danger) 45%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 6%, var(--om-surface));
  box-shadow: inset 3px 0 0 0 var(--om-danger);
}

.config-field--inline :deep(.field-group) {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 16px;
  row-gap: 8px;
  flex-direction: initial;
  align-items: center;
}

.config-field--inline :deep(.field-group__head) {
  flex: initial;
  grid-column: 1;
  grid-row: 1;
  margin-bottom: 0;
  min-width: 0;
}

.config-field--inline :deep(.field-group__control) {
  flex: initial;
  grid-column: 2;
  grid-row: 1;
  justify-self: end;
  min-width: 0;
}

.config-field--inline :deep(.field-group__helper) {
  grid-column: 1 / -1;
  grid-row: 2;
  flex-basis: auto;
}

@media (max-width: 720px) {
  .config-field--inline :deep(.field-group) {
    grid-template-columns: minmax(0, 1fr);
  }

  .config-field--inline :deep(.field-group__control) {
    grid-column: 1;
    grid-row: 2;
    justify-self: start;
  }

  .config-field--inline :deep(.field-group__helper) {
    grid-row: 3;
  }
}

.config-field :deep(.field-group__head) {
  flex-wrap: wrap;
  row-gap: 6px;
}

.config-field :deep(.field-group__aside) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.config-field :deep(.field-group__helper) {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-field__help {
  color: var(--om-text-3);
}

.config-field__hints {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.config-field__chip {
  padding: 2px 8px;
  border: 1px solid color-mix(in srgb, var(--om-info) 18%, var(--om-border));
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
}

.config-field__error-text {
  color: var(--om-danger);
  font-weight: 600;
}

.config-field__number,
.config-field__select {
  width: 100%;
  max-width: 320px;
}

.config-field__select {
  min-width: 180px;
}

.config-field--inline .config-field__number,
.config-field--inline .config-field__select {
  max-width: 240px;
}

.config-field__changed-tag {
  letter-spacing: 0.04em;
}

.config-field__revert {
  margin-left: 4px;
}
</style>

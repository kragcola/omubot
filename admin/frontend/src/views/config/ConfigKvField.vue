<script setup lang="ts">
import { AddOutline, RemoveOutline } from '@vicons/ionicons5'

import type { ConfigFieldSchema } from './types'

const props = defineProps<{
  field: ConfigFieldSchema
  modelValue: Record<string, any> | undefined
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, any>): void
}>()

const valueKind = computed(() => props.field.value_kind || 'text')
const isCompactValue = computed(() => valueKind.value === 'switch')

const selectOptions = computed(() =>
  (props.field.options || []).map(option => ({ label: String(option), value: option })) as any[],
)

const entries = computed<Array<[string, any]>>(() => {
  const value = props.modelValue
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value)
})

function getDefaultValue(kind: string): any {
  if (kind === 'switch') return false
  if (kind === 'number') return 0
  return ''
}

function cloneCurrent(): Record<string, any> {
  const value = props.modelValue
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return { ...value }
}

function emitNext(next: Record<string, any>) {
  emit('update:modelValue', next)
}

function addEntry() {
  const next = cloneCurrent()
  let key = 'new_key'
  let suffix = 1
  while (Object.prototype.hasOwnProperty.call(next, key)) {
    key = `new_key_${suffix}`
    suffix += 1
  }
  next[key] = getDefaultValue(valueKind.value)
  emitNext(next)
}

function removeEntry(key: string) {
  const next = cloneCurrent()
  delete next[key]
  emitNext(next)
}

function renameEntry(oldKey: string, rawNewKey: string) {
  const newKey = rawNewKey.trim()
  if (!newKey || newKey === oldKey) return
  const next = cloneCurrent()
  if (Object.prototype.hasOwnProperty.call(next, newKey)) return
  // Preserve key order: rebuild entries so the renamed entry stays in place.
  const rebuilt: Record<string, any> = {}
  for (const [k, v] of Object.entries(next)) {
    rebuilt[k === oldKey ? newKey : k] = v
  }
  emitNext(rebuilt)
}

function onKeyBlur(oldKey: string, event: FocusEvent) {
  const target = event.target as HTMLInputElement | null
  renameEntry(oldKey, target?.value ?? oldKey)
}

function updateValue(key: string, value: any) {
  const next = cloneCurrent()
  next[key] = value
  emitNext(next)
}
</script>

<template>
  <div class="config-kv">
    <div
      v-for="([entryKey, entryValue], index) in entries"
      :key="`${field.path}-${entryKey}-${index}`"
      class="config-kv__row"
      :class="{ 'config-kv__row--compact': isCompactValue }"
    >
      <NInput
        :value="entryKey"
        class="config-kv__key"
        @blur="(event: FocusEvent) => onKeyBlur(entryKey, event)"
      />
      <div class="config-kv__value" :class="{ 'config-kv__value--inline': isCompactValue }">
        <NSwitch
          v-if="valueKind === 'switch'"
          :value="Boolean(entryValue)"
          @update:value="(value: boolean) => updateValue(entryKey, value)"
        />
        <NInputNumber
          v-else-if="valueKind === 'number'"
          :value="typeof entryValue === 'number' ? entryValue : 0"
          class="config-kv__input"
          @update:value="(value: number | null) => updateValue(entryKey, value)"
        />
        <NSelect
          v-else-if="valueKind === 'select'"
          :value="entryValue as any"
          :options="selectOptions"
          class="config-kv__input"
          @update:value="(value: any) => updateValue(entryKey, value)"
        />
        <NInput
          v-else
          :value="String(entryValue ?? '')"
          class="config-kv__input"
          @update:value="(value: string) => updateValue(entryKey, value)"
        />
      </div>
      <NButton
        quaternary
        type="error"
        size="small"
        class="config-kv__remove"
        @click="removeEntry(entryKey)"
      >
        <template #icon>
          <NIcon :component="RemoveOutline" />
        </template>
      </NButton>
    </div>

    <NButton secondary size="small" class="config-kv__add" @click="addEntry">
      <template #icon>
        <NIcon :component="AddOutline" />
      </template>
      添加键值
    </NButton>
  </div>
</template>

<style scoped>
.config-kv {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-kv__row {
  display: grid;
  grid-template-columns: minmax(0, 200px) minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--om-border) 70%, transparent);
}

.config-kv__row:last-of-type {
  border-bottom: none;
}

.config-kv__row--compact {
  grid-template-columns: minmax(0, 200px) auto auto;
}

.config-kv__key,
.config-kv__input {
  width: 100%;
}

.config-kv__value {
  min-width: 0;
}

.config-kv__value--inline {
  display: inline-flex;
  align-items: center;
}

.config-kv__remove {
  flex: 0 0 auto;
  justify-self: end;
}

.config-kv__add {
  align-self: flex-start;
  margin-top: 4px;
}

@media (max-width: 720px) {
  .config-kv__row,
  .config-kv__row--compact {
    grid-template-columns: 1fr;
  }

  .config-kv__remove {
    justify-self: start;
  }
}
</style>

<script setup lang="ts">
import { AddOutline, RemoveOutline } from '@vicons/ionicons5'

import type { ConfigFieldSchema } from './types'

const props = defineProps<{
  field: ConfigFieldSchema
  modelValue: any[] | undefined
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: any[]): void
}>()

const items = computed<any[]>(() => Array.isArray(props.modelValue) ? props.modelValue : [])

const itemKind = computed(() => props.field.item_kind || 'text')
const isCompactItem = computed(() => itemKind.value === 'switch')

const selectOptions = computed(() =>
  (props.field.options || []).map(option => ({ label: String(option), value: option })) as any[],
)

function getDefaultValue(kind: string): any {
  if (kind === 'switch') return false
  if (kind === 'number') return 0
  return ''
}

function emitNext(next: any[]) {
  emit('update:modelValue', next)
}

function addItem() {
  emitNext([...items.value, getDefaultValue(itemKind.value)])
}

function removeItem(index: number) {
  const next = items.value.slice()
  next.splice(index, 1)
  emitNext(next)
}

function updateItem(index: number, value: any) {
  const next = items.value.slice()
  next[index] = value
  emitNext(next)
}
</script>

<template>
  <div class="config-list">
    <div
      v-for="(item, index) in items"
      :key="`${field.path}-${index}`"
      class="config-list__row"
      :class="{ 'config-list__row--compact': isCompactItem }"
    >
      <div class="config-list__control" :class="{ 'config-list__control--inline': isCompactItem }">
        <NSwitch
          v-if="itemKind === 'switch'"
          :value="Boolean(item)"
          @update:value="(value: boolean) => updateItem(index, value)"
        />
        <NInputNumber
          v-else-if="itemKind === 'number'"
          :value="typeof item === 'number' ? item : 0"
          class="config-list__input"
          @update:value="(value: number | null) => updateItem(index, value)"
        />
        <NSelect
          v-else-if="itemKind === 'select'"
          :value="item as any"
          :options="selectOptions"
          class="config-list__input"
          @update:value="(value: any) => updateItem(index, value)"
        />
        <NInput
          v-else
          :value="String(item ?? '')"
          class="config-list__input"
          @update:value="(value: string) => updateItem(index, value)"
        />
      </div>
      <NButton
        quaternary
        type="error"
        size="small"
        class="config-list__remove"
        @click="removeItem(index)"
      >
        <template #icon>
          <NIcon :component="RemoveOutline" />
        </template>
      </NButton>
    </div>

    <NButton secondary size="small" class="config-list__add" @click="addItem">
      <template #icon>
        <NIcon :component="AddOutline" />
      </template>
      添加一项
    </NButton>
  </div>
</template>

<style scoped>
.config-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-list__row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--om-border) 70%, transparent);
}

.config-list__row:last-of-type {
  border-bottom: none;
}

.config-list__control {
  flex: 1 1 auto;
  min-width: 0;
}

.config-list__control--inline {
  flex: 0 0 auto;
}

.config-list__input {
  width: 100%;
}

.config-list__remove {
  flex: 0 0 auto;
}

.config-list__add {
  align-self: flex-start;
  margin-top: 4px;
}
</style>

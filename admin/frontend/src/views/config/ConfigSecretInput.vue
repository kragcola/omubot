<script setup lang="ts">
import { CreateOutline } from '@vicons/ionicons5'

const props = withDefaults(defineProps<{
  modelValue: string
  mask?: string
  placeholder?: string
}>(), {
  mask: '',
  placeholder: '尚未填写。请在此粘贴 API Key',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const editing = ref(false)
const showMaskedView = computed(() => !editing.value && !!props.mask && !!props.modelValue && typeof props.modelValue === 'string')
</script>

<template>
  <div v-if="showMaskedView" class="config-secret">
    <NInput :value="mask" readonly />
    <NButton secondary @click="editing = true">
      <template #icon>
        <NIcon :component="CreateOutline" />
      </template>
      编辑
    </NButton>
  </div>
  <NInput
    v-else
    :value="modelValue"
    type="password"
    show-password-on="click"
    :placeholder="placeholder"
    clearable
    @update:value="(v: string) => emit('update:modelValue', v)"
  />
</template>

<style scoped>
.config-secret {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}
</style>

<script setup lang="ts">
const props = defineProps<{
  modelValue: any
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: any): void
}>()

const text = ref('')
const focused = ref(false)
const error = ref('')

watch(
  () => props.modelValue,
  (next) => {
    if (focused.value) return
    text.value = JSON.stringify(next ?? {}, null, 2)
    error.value = ''
  },
  { immediate: true },
)

function onFocus() {
  focused.value = true
}

function onBlur() {
  focused.value = false
  try {
    const parsed = JSON.parse(text.value || '{}')
    error.value = ''
    emit('update:modelValue', parsed)
  } catch (e) {
    console.error('Invalid JSON field:', e)
    error.value = 'JSON 格式错误，未应用到配置。'
  }
}
</script>

<template>
  <div class="config-json">
    <NInput
      v-model:value="text"
      type="textarea"
      :autosize="{ minRows: 4, maxRows: 12 }"
      class="config-json__input"
      @focus="onFocus"
      @blur="onBlur"
    />
    <p v-if="error" class="config-json__error">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.config-json {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-json__input:deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
}

.config-json__error {
  margin: 0;
  color: var(--om-danger);
  font-size: 12px;
  line-height: 1.5;
}
</style>

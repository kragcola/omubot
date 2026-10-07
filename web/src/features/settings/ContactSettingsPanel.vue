<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'

import type { ContactEndpointRules, ContactSettings } from '@/api/generated'

const props = defineProps<{ value: ContactSettings | null; disabled: boolean; baseRevision: number | null }>()
const emit = defineEmits<{ update: [value: ContactSettings | null] }>()
const kind = ref<'users' | 'groups'>('users')
const identity = ref('')
const windows = ref<Array<{ start: string; end: string }>>([{ start: '', end: '' }])
const sendInterval = ref<number | null>(null)
const sendLimit = ref<number | null>(null)
const decisionInterval = ref<number | null>(null)
const decisionLimit = ref<number | null>(null)
const error = ref('')
const kindOptions = [{ label: '用户', value: 'users' }, { label: '群', value: 'groups' }]
const rows = computed(() => Object.entries(props.value?.[kind.value] ?? {}))

function clock(minute: number): string {
  return `${Math.floor(minute / 60).toString().padStart(2, '0')}:${(minute % 60).toString().padStart(2, '0')}`
}

function minutes(value: string, end: boolean): number | null {
  if (end && value === '24:00') return 1440
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) return null
  const [hour, minute] = value.split(':').map(Number)
  return hour === undefined || minute === undefined ? null : hour * 60 + minute
}

function clearForm(): void {
  identity.value = ''
  windows.value = [{ start: '', end: '' }]
  sendInterval.value = null
  sendLimit.value = null
  decisionInterval.value = null
  decisionLimit.value = null
  error.value = ''
}

function edit(id: string, rules: ContactEndpointRules): void {
  identity.value = id
  windows.value = rules.windows.map(window => ({ start: clock(window.start_minute), end: clock(window.end_minute) }))
  sendInterval.value = rules.minimum_interval_seconds
  sendLimit.value = rules.day_limit
  decisionInterval.value = rules.decision_minimum_interval_seconds
  decisionLimit.value = rules.decision_day_limit
  error.value = ''
}

function enabled(value: boolean): void {
  emit('update', { ...(props.value ?? { users: {}, groups: {} }), enabled: value })
}

function saveEndpoint(): void {
  if (props.disabled) return
  const id = identity.value.trim()
  const parsed = windows.value.map(window => ({ start_minute: minutes(window.start, false), end_minute: minutes(window.end, true) }))
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || parsed.length < 1 || parsed.length > 16
    || parsed.some(window => window.start_minute === null || window.end_minute === null
      || window.start_minute >= window.end_minute)) {
    error.value = '请填写精确 ID 和完整的 HH:mm 时间窗；跨午夜请拆成两个窗口。'
    return
  }
  const complete = parsed as Array<{ start_minute: number; end_minute: number }>
  const ordered = [...complete].sort((left, right) => left.start_minute - right.start_minute)
  if (ordered.some((window, index) => index > 0 && (ordered[index - 1]?.end_minute ?? 0) > window.start_minute)
    || sendInterval.value === null || sendInterval.value <= 0
    || decisionInterval.value === null || decisionInterval.value <= 0
    || sendLimit.value === null || !Number.isInteger(sendLimit.value) || sendLimit.value < 1 || sendLimit.value > 180
    || decisionLimit.value === null || !Number.isInteger(decisionLimit.value) || decisionLimit.value < 1 || decisionLimit.value > 180) {
    error.value = '时间窗不能重叠，四项次数与间隔都必须明确填写。'
    return
  }
  const settings = props.value ?? { enabled: false, users: {}, groups: {} }
  if (settings[kind.value][id] === undefined && Object.keys(settings[kind.value]).length >= 128) {
    error.value = '此类对象最多配置 128 项。'
    return
  }
  const rules: ContactEndpointRules = {
    windows: complete, minimum_interval_seconds: sendInterval.value, day_limit: sendLimit.value,
    decision_minimum_interval_seconds: decisionInterval.value, decision_day_limit: decisionLimit.value,
  }
  emit('update', { ...settings, [kind.value]: { ...settings[kind.value], [id]: rules } })
  error.value = ''
}

function remove(id: string): void {
  if (props.disabled || !props.value) return
  const changed = { ...props.value[kind.value] }
  delete changed[id]
  emit('update', { ...props.value, [kind.value]: changed })
  clearForm()
}

watch(kind, clearForm)
watch(() => props.baseRevision, clearForm)
</script>

<template>
  <div class="form-field form-field-wide">
    <span class="field-label">主动联系时间与上限</span>
    <label class="toggle-row">
      <n-switch :value="value?.enabled ?? false" :disabled="disabled" @update:value="enabled" />
      <span><strong>允许在已审核窗口选择联系时机</strong><small>用户与群都必须有明确规则及独立联系授权。缺少任一规则时不联系，不自动填入频率数值。</small></span>
    </label>
    <p class="form-hint">每次有真实理由时才作一次 Thinker 判断。选择与实际首条发送分别计数；放弃、失败和未知结果不会退回已消耗的机会。用户上限合计所有群，群上限合计所有用户；原账号保护继续生效。</p>
    <n-alert v-if="error" type="error" class="notice">{{ error }}</n-alert>
    <div class="form-grid">
      <div class="form-field"><label class="field-label" for="contact-rule-kind">规则对象</label><n-select id="contact-rule-kind" v-model:value="kind" :options="kindOptions" :disabled="disabled" /></div>
      <div class="form-field"><label class="field-label" for="contact-rule-id">精确 ID</label><n-input id="contact-rule-id" v-model:value="identity" :disabled="disabled" :maxlength="64" /></div>
      <div class="form-field"><label class="field-label" for="contact-rule-send-gap">两次联系至少间隔（秒）</label><n-input-number id="contact-rule-send-gap" v-model:value="sendInterval" :disabled="disabled" /></div>
      <div class="form-field"><label class="field-label" for="contact-rule-send-cap">每天最多联系次数</label><n-input-number id="contact-rule-send-cap" v-model:value="sendLimit" :disabled="disabled" :min="1" :max="180" :precision="0" /></div>
      <div class="form-field"><label class="field-label" for="contact-rule-decision-gap">两次时机判断至少间隔（秒）</label><n-input-number id="contact-rule-decision-gap" v-model:value="decisionInterval" :disabled="disabled" /></div>
      <div class="form-field"><label class="field-label" for="contact-rule-decision-cap">每天最多时机判断次数</label><n-input-number id="contact-rule-decision-cap" v-model:value="decisionLimit" :disabled="disabled" :min="1" :max="180" :precision="0" /></div>
    </div>
    <div v-for="(window, index) in windows" :key="index" class="form-grid">
      <div class="form-field"><label class="field-label" :for="`contact-window-start-${index}`">允许开始（HH:mm）</label><n-input :id="`contact-window-start-${index}`" v-model:value="window.start" :disabled="disabled" /></div>
      <div class="form-field"><label class="field-label" :for="`contact-window-end-${index}`">允许结束（HH:mm）</label><n-input :id="`contact-window-end-${index}`" v-model:value="window.end" :disabled="disabled" /></div>
      <n-button :disabled="disabled || windows.length <= 1" @click="windows.splice(index, 1)">移除此时间窗</n-button>
    </div>
    <div class="button-row">
      <n-button :disabled="disabled || windows.length >= 16" @click="windows.push({ start: '', end: '' })">增加时间窗</n-button>
      <n-button :disabled="disabled" @click="saveEndpoint">加入配置草稿</n-button>
    </div>
    <p class="form-hint">按当前配置时区计算。24:00 只可作为结束；先加入草稿，再沿页面原保存、生效流程应用。开启总开关需要 Thinker 已配置。</p>
    <div v-for="[id, rules] in rows" :key="id" class="grant-row">
      <span>{{ id }} · {{ rules.windows.map(window => `${clock(window.start_minute)}–${clock(window.end_minute)}`).join('，') }} · 每天联系≤{{ rules.day_limit }}次，判断≤{{ rules.decision_day_limit }}次</span>
      <div class="button-row"><n-button :disabled="disabled" @click="edit(id, rules)">编辑</n-button><n-button :disabled="disabled" @click="remove(id)">从草稿移除</n-button></div>
    </div>
  </div>
</template>

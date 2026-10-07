<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { LearningAutoApplyReceiptPageView, LearningAutoApplyReceiptView, LearningAutoApplyRollbackRequest, LearningAutoApplyStatusView } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const group = ref('')
const loadedGroup = ref('')
const status = ref<LearningAutoApplyStatusView | null>(null)
const page = ref<LearningAutoApplyReceiptPageView | null>(null)
const busy = ref(false)
const error = ref('')
let sequence = 0
let disposed = false
const canWrite = computed(() => sessionState.adminAuthenticated && !busy.value
  && page.value !== null && group.value.trim() === loadedGroup.value)

function clear() {
  sequence += 1
  status.value = null
  page.value = null
  loadedGroup.value = ''
  error.value = ''
  busy.value = false
}
watch(() => sessionState.generation, clear)
watch(() => sessionState.adminAuthenticated, (authenticated) => { if (!authenticated) clear() })
onBeforeUnmount(() => { disposed = true; clear() })

async function load(more = false) {
  if (busy.value || !sessionState.adminAuthenticated) return
  const target = group.value.trim()
  if (!target || (more && target !== loadedGroup.value)) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  const after = more ? page.value?.next_cursor : null
  busy.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ group_id: target })
    if (after !== null && after !== undefined) params.set('after', String(after))
    const [nextStatus, nextPage] = await Promise.all([
      apiRequest<LearningAutoApplyStatusView>('/api/admin/memory/auto-apply/status'),
      apiRequest<LearningAutoApplyReceiptPageView>(`/api/admin/memory/auto-apply/receipts?${params}`),
    ])
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    status.value = nextStatus
    const receipts = new Map((more ? page.value?.items ?? [] : []).map(item => [item.receipt_id, item]))
    for (const item of nextPage.items) receipts.set(item.receipt_id, item)
    page.value = { ...nextPage, items: [...receipts.values()] }
    loadedGroup.value = target
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clear() }
    else error.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function mutate(item?: LearningAutoApplyReceiptView) {
  if (!canWrite.value) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  error.value = ''
  let applied = false
  try {
    if (item) {
      const body: LearningAutoApplyRollbackRequest = {
        group_id: loadedGroup.value, receipt_id: item.receipt_id,
        expected_object_revision: item.object_revision, reason: 'admin_disabled',
      }
      await apiRequest('/api/admin/memory/auto-apply/rollback', { method: 'POST', body, adminMutation: true })
    } else {
      const result = await apiRequest<LearningAutoApplyStatusView>('/api/admin/memory/auto-apply/disable', {
        method: 'POST', adminMutation: true,
      })
      if (!disposed && requestSequence === sequence && isCurrentAdminEpoch(epoch)) status.value = result
    }
    applied = true
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clear() }
    else error.value = apiErrorMessage(cause) + ' 请重新读取实际回执后再操作。'
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
  if (applied && !disposed && requestSequence === sequence && isCurrentAdminEpoch(epoch)) await load()
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="限定自动学习与实际回执">
    <p class="form-hint">默认关闭，仅处理已批准群范围内的少量本人偏好、非个人游戏术语和安全表达；其余候选保留人工审核。回执展示曾经实际应用的结果，不代表来源仍有效，也不修改固定人格。</p>
    <form class="form-row" @submit.prevent="void load()">
      <label for="auto-learning-group">群 ID</label>
      <n-input id="auto-learning-group" v-model:value="group" :disabled="busy" placeholder="输入已获审核授权的群 ID" />
      <n-button attr-type="submit" :loading="busy" :disabled="!sessionState.adminAuthenticated || busy || !group.trim()">读取自动学习管理</n-button>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false" role="alert">{{ error }}</n-alert>
    <template v-if="status && page">
      <p>本次运行：<n-tag>{{ status.enabled ? '自动审用已启用' : '自动审用已关闭' }}</n-tag></p>
      <n-button :disabled="!canWrite || !status.enabled" @click="void mutate()">停用本次运行的自动审用</n-button>
      <p class="form-hint">停用立即阻止后续自动应用，已有对象保留。持久停用需在配置中保存关闭开关；重启仍按保存配置运行。精确回退会停用原对象，若对象或来源已改变则拒绝，请重新核对。</p>
      <p v-if="status.last_report">最近来源批次：考虑 {{ status.last_report.considered }}，应用 {{ status.last_report.applied }}，恢复回执 {{ status.last_report.recovered }}，留人工 {{ status.last_report.kept }}；{{ status.last_report.errors.join('、') || '无失败代码' }}</p>
      <n-empty v-if="!page.items.length" description="该群尚无自动应用回执" />
      <article v-for="item in page.items" :key="item.receipt_id">
        <p><n-tag>{{ item.domain }}</n-tag> {{ item.state === 'applied' ? '曾实际应用' : '已回退' }} · 对象 {{ item.object_id }} · 版本 {{ item.object_revision }}</p>
        <p class="form-hint">候选 {{ item.candidate_id }} · 来源 {{ item.source_id }} · 策略 {{ item.policy_version }}</p>
        <n-button :disabled="!canWrite || item.state !== 'applied'" @click="void mutate(item)">精确回退此应用</n-button>
      </article>
      <n-button v-if="page.next_cursor !== null" :disabled="!canWrite" @click="void load(true)">继续读取回执</n-button>
    </template>
  </n-card>
</template>

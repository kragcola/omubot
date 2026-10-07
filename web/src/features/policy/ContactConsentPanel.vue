<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NInput, NSpace, NTag } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { ContactConsentResponse, ContactConsentView } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ updated: [] }>()
const snapshot = ref<ContactConsentView | null>(null)
const userId = ref('')
const groupId = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
let sequence = 0

function enabled(kind: 'user' | 'group', id: string): boolean {
  return snapshot.value?.consents.some(item => item.kind === kind && item.subject_id === id && item.enabled) ?? false
}
const userEnabled = computed(() => enabled('user', userId.value.trim()))
const groupEnabled = computed(() => enabled('group', groupId.value.trim()))
const configured = computed(() => {
  const settings = snapshot.value?.runtime_settings
  return settings?.enabled && settings.users[userId.value.trim()] !== undefined
    && settings.groups[groupId.value.trim()] !== undefined
})
const busy = computed(() => props.disabled || loading.value || saving.value)

async function load(): Promise<void> {
  const epoch = currentAdminEpoch()
  const current = ++sequence
  loading.value = true
  error.value = ''
  try {
    const result = await apiRequest<ContactConsentView>('/api/admin/policy/contact')
    if (!isCurrentAdminEpoch(epoch) || current !== sequence) return
    snapshot.value = result
  } catch (reason) {
    if (!isCurrentAdminEpoch(epoch) || current !== sequence) return
    if (isApiError(reason) && reason.status === 401) expireAdminSession()
    error.value = apiErrorMessage(reason)
  } finally {
    if (isCurrentAdminEpoch(epoch) && current === sequence) loading.value = false
  }
}

async function change(kind: 'user' | 'group', desired: boolean): Promise<void> {
  if (busy.value || !snapshot.value) return
  const subject = (kind === 'user' ? userId.value : groupId.value).trim()
  if (!subject || subject.length > 64 || /\s|\*/.test(subject)) {
    error.value = '请填写一个精确的用户或群 ID。'
    return
  }
  const epoch = currentAdminEpoch()
  const current = ++sequence
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await apiRequest<ContactConsentResponse>('/api/admin/policy/contact', {
      method: 'PUT', adminMutation: true, body: {
        expected_revision: snapshot.value.revision, kind, subject_id: subject, enabled: desired,
      },
    })
    if (!isCurrentAdminEpoch(epoch) || current !== sequence) return
    snapshot.value = {
      ...snapshot.value, revision: result.revision,
      consents: [...snapshot.value.consents.filter(item => !(item.kind === kind && item.subject_id === subject)), result.consent],
    }
    notice.value = desired ? '已保存此对象的联系开关；其余门仍须通过。' : '已关闭此对象的主动联系；旧候选不会自动恢复。'
    emit('updated')
  } catch (reason) {
    if (!isCurrentAdminEpoch(epoch) || current !== sequence) return
    if (isApiError(reason) && reason.status === 401) expireAdminSession()
    error.value = isApiError(reason) && reason.code === 'revision_conflict'
      ? '权限版本已变化，请刷新后重新审核；本次没有重试。' : apiErrorMessage(reason)
  } finally {
    if (isCurrentAdminEpoch(epoch) && current === sequence) saving.value = false
  }
}

watch(() => sessionState.adminAuthenticated, authenticated => {
  if (authenticated) return
  sequence += 1
  snapshot.value = null
  userId.value = ''
  groupId.value = ''
  loading.value = false
  saving.value = false
  error.value = ''
  notice.value = ''
})
onMounted(() => { void load() })
</script>

<template>
  <n-card class="page-card" :bordered="false">
    <div class="section-heading">
      <div>
        <p class="eyebrow">主动联系</p>
        <h2>逐用户、逐群开启</h2>
        <p class="subtle-text">自主闲聊或角色生活分享必须联系一个实际用户。群内消息会向其他成员可见，同一事件在群内合并。</p>
      </div>
      <n-button :loading="loading" :disabled="busy" @click="load">刷新开关</n-button>
    </div>
    <n-alert v-if="error" type="error" class="notice">{{ error }}</n-alert>
    <n-alert v-if="notice" type="info" class="notice">{{ notice }}</n-alert>
    <div class="form-grid">
      <div class="form-field">
        <label class="field-label" for="contact-user">实际联系用户 ID</label>
        <n-input id="contact-user" v-model:value="userId" :maxlength="64" :disabled="busy" />
        <n-checkbox :checked="userEnabled" :disabled="busy || !snapshot || !userId.trim()" @update:checked="value => change('user', value)">允许联系此用户</n-checkbox>
      </div>
      <div class="form-field">
        <label class="field-label" for="contact-group">消息可见群 ID</label>
        <n-input id="contact-group" v-model:value="groupId" :maxlength="64" :disabled="busy" />
        <n-checkbox :checked="groupEnabled" :disabled="busy || !snapshot || !groupId.trim()" @update:checked="value => change('group', value)">允许在此群主动联系</n-checkbox>
      </div>
    </div>
    <n-space>
      <n-tag :type="userEnabled && groupEnabled ? 'success' : 'default'">{{ userEnabled && groupEnabled ? '两级开关均开启' : '两级开关未齐备' }}</n-tag>
      <n-tag>{{ configured ? '运行版已有此对象的时间窗和上限' : '运行版尚未配置或启用此对象' }}</n-tag>
    </n-space>
    <p class="form-hint">时间窗、频率上限、固定人格、原消息与模型权限及账号保护仍独立检查。好感度、情绪、故事不能提高上限或替代任一级开关。用户开关按 Bot 与用户生效，可用于其已授权的群；群开关只作用于该群。</p>
    <p v-if="disabled" class="form-hint">请先保存或放弃常规权限草稿，避免共用版本发生冲突。</p>
    <div v-for="item in snapshot?.consents ?? []" :key="`${item.kind}:${item.subject_id}`" class="grant-row">
      <span>{{ item.kind === 'user' ? '用户' : '群' }} {{ item.subject_id }}</span>
      <n-tag :type="item.enabled ? 'success' : 'default'">{{ item.enabled ? '允许联系' : '已关闭' }}</n-tag>
    </div>
  </n-card>
</template>

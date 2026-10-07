<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NTag, NText } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import { currentSessionEpoch, expireAdminSession, isCurrentSessionEpoch, sessionState } from '@/app/session'
import type { OfflineChatResponse, PolicySnapshot } from '@/api/types'

interface ChatMessage {
  id: number
  kind: 'user' | 'bot'
  text: string
}

const groupId = ref('')
const userId = ref('')
const text = ref('')
const policy = ref<PolicySnapshot | null>(null)
const policyLoading = ref(false)
const policyError = ref('')
const sendError = ref('')
const isSending = ref(false)
const messages = ref<ChatMessage[]>([])
const lastRequestId = ref('')
const lastRequestState = ref<OfflineChatResponse['state'] | null>(null)

let viewEpoch = 0
let policySequence = 0
let sendSequence = 0
let messageSequence = 0

const isAdmin = computed(() => sessionState.adminAuthenticated)
const isLive = computed(() => policy.value?.mode === 'live')
const canSend = computed(() => {
  return Boolean(
    isAdmin.value &&
    policy.value?.mode === 'offline' &&
    !isSending.value &&
    groupId.value.trim() &&
    userId.value.trim() &&
    text.value.trim(),
  )
})

function modeLabel(mode: PolicySnapshot['mode']): string {
  return mode === 'live' ? '实时模式' : '离线隔离'
}

function modeTagType(mode: PolicySnapshot['mode']): 'success' | 'warning' {
  return mode === 'live' ? 'warning' : 'success'
}

function requestStateLabel(state: OfflineChatResponse['state']): string {
  const labels: Record<OfflineChatResponse['state'], string> = {
    accepted: '已接受',
    running: '处理中',
    queued: '排队中',
    succeeded: '已完成',
    cancelled_before_dispatch: '未发送',
    failed: '处理失败',
    denied: '未获授权',
    unknown: '状态未知',
    superseded: '已被替代',
  }
  return labels[state] ?? state
}

function requestStateTagType(state: OfflineChatResponse['state']): 'success' | 'warning' | 'error' | 'default' {
  if (state === 'succeeded') return 'success'
  if (state === 'accepted' || state === 'running' || state === 'queued') return 'warning'
  if (state === 'failed' || state === 'denied') return 'error'
  return 'default'
}

function clearAdminView(): void {
  viewEpoch += 1
  policySequence += 1
  sendSequence += 1
  policy.value = null
  policyLoading.value = false
  policyError.value = ''
  sendError.value = ''
  isSending.value = false
  messages.value = []
  groupId.value = ''
  userId.value = ''
  text.value = ''
  lastRequestId.value = ''
  lastRequestState.value = null
}

async function loadPolicy(): Promise<void> {
  if (!sessionState.adminAuthenticated) return
  const epoch = currentSessionEpoch()
  const localViewEpoch = viewEpoch
  const sequence = ++policySequence
  policyLoading.value = true
  policyError.value = ''
  try {
    const snapshot = await apiRequest<PolicySnapshot>('/api/admin/policy')
    if (!isCurrentSessionEpoch(epoch) || localViewEpoch !== viewEpoch || sequence !== policySequence) return
    policy.value = snapshot
  } catch (error: unknown) {
    if (!isCurrentSessionEpoch(epoch) || localViewEpoch !== viewEpoch || sequence !== policySequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    policyError.value = apiErrorMessage(error)
  } finally {
    if (sequence === policySequence) policyLoading.value = false
  }
}

async function sendOfflineChat(): Promise<void> {
  if (!canSend.value || !policy.value) return
  const epoch = currentSessionEpoch()
  const localViewEpoch = viewEpoch
  const sequence = ++sendSequence
  const submittedGroupId = groupId.value.trim()
  const submittedUserId = userId.value.trim()
  const submittedText = text.value.trim()
  isSending.value = true
  sendError.value = ''
  lastRequestId.value = ''
  lastRequestState.value = null
  try {
    const response = await apiRequest<OfflineChatResponse>('/api/admin/offline-chat', {
      method: 'POST',
      adminMutation: true,
      body: { group_id: submittedGroupId, user_id: submittedUserId, text: submittedText },
    })
    if (!isCurrentSessionEpoch(epoch) || localViewEpoch !== viewEpoch || sequence !== sendSequence) return
    lastRequestId.value = response.request_id
    lastRequestState.value = response.state
    messages.value = [
      ...messages.value,
      { id: ++messageSequence, kind: 'user', text: submittedText },
      ...response.replies.map((reply: string) => ({ id: ++messageSequence, kind: 'bot' as const, text: reply })),
    ]
    text.value = ''
  } catch (error: unknown) {
    if (!isCurrentSessionEpoch(epoch) || localViewEpoch !== viewEpoch || sequence !== sendSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    sendError.value = apiErrorMessage(error)
  } finally {
    if (sequence === sendSequence) isSending.value = false
  }
}

watch(
  () => sessionState.adminAuthenticated,
  (authenticated) => {
    if (!authenticated) {
      clearAdminView()
      return
    }
    void loadPolicy()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  viewEpoch += 1
  policySequence += 1
  sendSequence += 1
})
</script>

<template>
  <section class="chat-view" aria-labelledby="chat-view-heading">
    <n-card v-if="!isAdmin" class="page-card empty-card" :bordered="false">
      <div class="empty-heading">
        <p class="eyebrow">管理员工具</p>
        <h2 id="chat-view-heading">离线试聊</h2>
      </div>
      <n-empty description="管理员登录后才能使用试聊">
        <template #extra>
          <n-text depth="3">试聊只在本机管理会话中开放，输入内容不会发送到实时连接。</n-text>
        </template>
      </n-empty>
    </n-card>

    <template v-else>
      <div class="chat-toolbar">
        <div>
          <h2 id="chat-view-heading" class="chat-kicker">离线试聊</h2>
          <p class="chat-sync" aria-live="polite">
            {{ policyLoading ? '正在读取当前运行模式…' : '发送前会再次确认当前运行模式' }}
          </p>
        </div>
        <n-button
          secondary
          :loading="policyLoading"
          :disabled="policyLoading || isSending"
          aria-label="刷新运行模式和策略"
          @click="void loadPolicy()"
        >
          刷新模式
        </n-button>
      </div>

      <n-alert v-if="policyError" type="error" :show-icon="true" class="page-card" role="alert">
        {{ policyError }}
      </n-alert>
      <n-alert v-if="sendError" type="error" :show-icon="true" class="page-card" role="alert">
        {{ sendError }} 输入内容仍保留在表单中。
      </n-alert>
      <n-alert v-if="isLive" type="warning" :show-icon="true" class="page-card" role="status">
        当前是实时模式，离线试聊已禁用。切换到离线隔离后，才能在这里验证回复。
      </n-alert>

      <n-card class="page-card chat-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">离线请求</p>
            <h2>试聊一条消息</h2>
          </div>
          <n-tag v-if="policy" :type="modeTagType(policy.mode)" size="small" round>
            {{ modeLabel(policy.mode) }}
          </n-tag>
          <n-tag v-else type="default" size="small" round>模式未读取</n-tag>
        </div>
        <p class="chat-intro">使用模拟模型验证授权、对话和工具链，不调用配置中的真实模型或发送 QQ 消息。群和用户标识用于权限检查及会话隔离，执行记录会保存在本实例。</p>

        <div v-if="policy" class="policy-summary" aria-label="当前试聊策略">
          <div>
            <span>模型</span>
            <strong>{{ policy.model_config.model }}</strong>
          </div>
          <div>
            <span>配置</span>
            <strong>{{ policy.model_config.profile }}</strong>
          </div>
          <div>
            <span>策略版本</span>
            <strong>{{ policy.revision }}</strong>
          </div>
        </div>

        <form class="chat-form" @submit.prevent="void sendOfflineChat()">
          <div class="form-grid">
            <div class="chat-field">
              <label for="offline-group-id-input">群组 ID</label>
              <n-input
                id="offline-group-id"
                :input-props="{ id: 'offline-group-id-input', 'aria-label': '群组 ID' }"
                v-model:value="groupId"
                :disabled="isLive || policyLoading || isSending"
                autocomplete="off"
                placeholder="例如：test-group"
                aria-describedby="offline-group-help"
              />
              <span id="offline-group-help" class="field-help">默认留空；例如 test-group，须与权限页已保存的群 ID 一致。</span>
            </div>
            <div class="chat-field">
              <label for="offline-user-id-input">用户 ID</label>
              <n-input
                id="offline-user-id"
                :input-props="{ id: 'offline-user-id-input', 'aria-label': '用户 ID' }"
                v-model:value="userId"
                :disabled="isLive || policyLoading || isSending"
                autocomplete="off"
                placeholder="例如：test-user"
                aria-describedby="offline-user-help"
              />
              <span id="offline-user-help" class="field-help">默认留空；例如 test-user，须与该群已授权的用户 ID 一致。</span>
            </div>
          </div>
          <div class="chat-field chat-message-field">
            <label for="offline-message-input">消息内容</label>
            <n-input
              id="offline-message"
                :input-props="{ id: 'offline-message-input', 'aria-label': '消息内容' }"
              v-model:value="text"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 10 }"
              :disabled="isLive || policyLoading || isSending"
              maxlength="2000"
              show-count
              placeholder="输入要验证的消息"
              aria-describedby="offline-message-help"
            />
            <span id="offline-message-help" class="field-help">默认留空，最多 2000 字。可输入“你好”验证回复，或“现在的时间”验证时间工具；内容按纯文本显示。</span>
          </div>
          <div class="chat-submit-row">
            <n-text depth="3">{{ isSending ? '正在等待回复…' : '发送后不会自动重试。' }}</n-text>
            <n-button
              type="primary"
              attr-type="submit"
              :loading="isSending"
              :disabled="!canSend"
              aria-label="发送离线试聊消息"
            >
              发送试聊
            </n-button>
          </div>
        </form>
      </n-card>

      <n-card class="page-card chat-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">结果</p>
            <h2>对话记录</h2>
          </div>
          <n-tag v-if="lastRequestState" :type="requestStateTagType(lastRequestState)" size="small">
            {{ requestStateLabel(lastRequestState) }}
          </n-tag>
        </div>
        <p v-if="lastRequestId" class="request-meta" aria-live="polite">
          请求编号 <code>{{ lastRequestId }}</code>
        </p>
        <ol v-if="messages.length" class="chat-log" role="log" aria-live="polite" aria-label="试聊对话记录">
          <li v-for="message in messages" :key="message.id" class="chat-message" :class="{ 'from-user': message.kind === 'user' }">
            <div class="chat-meta">{{ message.kind === 'user' ? '本次输入' : '离线回复' }}</div>
            <p class="chat-body">{{ message.text }}</p>
          </li>
        </ol>
        <n-empty v-else description="发送一条消息后，回复会显示在这里" />
      </n-card>
    </template>
  </section>
</template>

<style scoped>
.chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.chat-kicker,
.chat-sync,
.chat-intro,
.request-meta {
  margin: 0;
}

.chat-kicker {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 720;
}

.chat-sync {
  margin-top: 2px;
  color: var(--om-muted);
  font-size: 12px;
}

.empty-card {
  padding: 18px 8px;
}

.chat-intro {
  max-width: 680px;
  margin-top: 8px;
  color: var(--om-muted);
  font-size: 13px;
}

.policy-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 20px;
}

.policy-summary > div {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 11px 13px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.policy-summary span,
.field-help {
  color: var(--om-muted);
  font-size: 12px;
}

.policy-summary strong {
  overflow-wrap: anywhere;
  font-size: 14px;
}

.chat-form {
  margin-top: 22px;
}

.chat-field {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.chat-field > label {
  color: var(--om-text);
  font-size: 13px;
  font-weight: 700;
}

.field-help {
  margin-top: -2px;
  line-height: 1.45;
}

.chat-message-field {
  margin-top: 17px;
}

.chat-submit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 18px;
}

.request-meta {
  margin-top: 10px;
  color: var(--om-muted);
  font-size: 12px;
}

.request-meta code {
  color: var(--om-primary-dark);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}

.chat-log {
  display: grid;
  gap: 10px;
  max-height: 430px;
  margin: 20px 0 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.chat-message {
  max-width: 82%;
  padding: 11px 14px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: 13px 13px 13px 4px;
}

.chat-message.from-user {
  justify-self: end;
  background: var(--om-primary-soft);
  border-radius: 13px 13px 4px 13px;
}

.chat-meta {
  margin-bottom: 3px;
  color: var(--om-muted);
  font-size: 11px;
  font-weight: 700;
}

.chat-body {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

:deep(.n-button) {
  min-height: 44px;
}

:deep(.n-input) {
  min-height: 44px;
}

@media (max-width: 620px) {
  .chat-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .chat-toolbar :deep(.n-button) {
    width: 100%;
  }

  .policy-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .chat-submit-row {
    align-items: stretch;
    flex-direction: column;
  }

  .chat-submit-row :deep(.n-button) {
    width: 100%;
  }

  .chat-message {
    max-width: 94%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .chat-view * {
    scroll-behavior: auto;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
</style>

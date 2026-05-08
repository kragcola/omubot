<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  PaperPlaneOutline,
  PeopleOutline,
  PersonOutline,
  PulseOutline,
  SparklesOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import { NButton, NIcon, NInput, NSelect, NTag, NText } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface ChatMsg {
  role: 'user' | 'bot'
  content: string
  ts: string
}

const simulate = ref<'private' | 'group'>('private')
const userId = ref('sandbox_user')
const groupId = ref('')
const inputMsg = ref('')
const messages = ref<ChatMsg[]>([])
const sending = ref(false)
const chatRef = ref<HTMLElement | null>(null)

const modeOptions = [
  { label: '模拟私聊', value: 'private' },
  { label: '模拟群聊', value: 'group' },
]

const conversationCount = computed(() => messages.value.length)
const lastSpeaker = computed(() => {
  const last = messages.value[messages.value.length - 1]
  if (!last) return '尚未开始'
  return last.role === 'user' ? '你' : 'Omubot'
})

const effectiveGroupId = computed(() =>
  simulate.value === 'group' ? (groupId.value.trim() || 'sandbox_group') : '',
)

let isComposing = false

async function send() {
  const msg = inputMsg.value.trim()
  if (!msg) return

  inputMsg.value = ''
  messages.value.push({ role: 'user', content: msg, ts: new Date().toLocaleTimeString() })
  scrollBottom()
  sending.value = true

  try {
    const data = await api('/api/admin/sandbox/chat', {
      method: 'POST',
      body: {
        message: msg,
        simulate: simulate.value,
        user_id: userId.value,
        group_id: simulate.value === 'group' ? effectiveGroupId.value : undefined,
      },
    })
    const reply = data.reply || data.error || '(无回复)'
    messages.value.push({ role: 'bot', content: reply, ts: new Date().toLocaleTimeString() })
  } catch (error) {
    console.error('Sandbox request failed:', error)
    messages.value.push({ role: 'bot', content: '请求失败', ts: new Date().toLocaleTimeString() })
  } finally {
    sending.value = false
    scrollBottom()
  }
}

function scrollBottom() {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

function clear() {
  messages.value = []
}

function onCompositionStart() {
  isComposing = true
}

function onCompositionEnd() {
  isComposing = false
}

function onEnter(event: KeyboardEvent) {
  if (isComposing || event.isComposing) return
  send()
}
</script>

<template>
  <AppPage
    title="沙盒"
    eyebrow="Conversation Sandbox"
    description="模拟私聊或群聊上下文，快速观察 Omubot 的回复链路、输入身份和输出节奏。"
  >
    <template #action>
      <div class="sandbox-hero-actions">
        <NTag round size="small" :type="simulate === 'group' ? 'warning' : 'info'">
          {{ simulate === 'group' ? '群聊模拟' : '私聊模拟' }}
        </NTag>
        <NTag round size="small" :type="sending ? 'warning' : 'success'">
          {{ sending ? '请求发送中' : '会话空闲' }}
        </NTag>
        <NButton secondary @click="clear">
          <template #icon>
            <NIcon :component="TrashOutline" />
          </template>
          清空会话
        </NButton>
      </div>
    </template>

    <div class="om-fill-page">
      <PageToolbar class="mb-16">
        <template #left>
          <NTag round size="small">
            当前用户 {{ userId }}
          </NTag>
          <NTag v-if="simulate === 'group'" round size="small" type="warning">
            群 {{ effectiveGroupId }}
          </NTag>
          <NTag round size="small" type="info">
            共 {{ conversationCount }} 条消息
          </NTag>
        </template>

        <template #right>
          <NText depth="3">
            最近发言：{{ lastSpeaker }}
          </NText>
        </template>
      </PageToolbar>

      <div class="sandbox-layout om-fill-page__body">
        <AppCard bordered elevated class="sandbox-chat om-fill-card">
        <div class="sandbox-chat__head">
          <div>
            <p class="sandbox-chat__eyebrow">
              Session Flow
            </p>
            <h3 class="sandbox-chat__title">
              对话流
            </h3>
          </div>

          <NTag round size="small" :type="sending ? 'warning' : 'default'">
            {{ sending ? '等待回复' : '可继续发送' }}
          </NTag>
        </div>

        <div ref="chatRef" class="sandbox-chat__body cus-scroll om-fill-scroll">
          <div v-if="messages.length > 0" class="sandbox-message-list">
            <div
              v-for="(message, index) in messages"
              :key="`${message.ts}-${index}`"
              class="sandbox-message"
              :class="message.role === 'user' ? 'sandbox-message--user' : 'sandbox-message--bot'"
            >
              <div class="sandbox-message__meta">
                <strong>{{ message.role === 'user' ? '你' : 'Omubot' }}</strong>
                <span>{{ message.ts }}</span>
              </div>

              <div class="sandbox-message__bubble">
                {{ message.content }}
              </div>
            </div>
          </div>

          <EmptyState
            v-else
            title="沙盒会话还没有开始"
            description="输入一条测试消息后，左侧会持续保留当前会话上下文，方便你观察多轮回复。"
            :icon="ChatbubbleEllipsesOutline"
          />
        </div>

        <AppCard bordered embedded class="sandbox-composer">
          <div class="sandbox-composer__head">
            <div>
              <p class="sandbox-chat__eyebrow">
                Composer
              </p>
              <h4 class="sandbox-composer__title">
                输入测试消息
              </h4>
            </div>
            <NText depth="3">
              Enter 发送，Shift+Enter 换行
            </NText>
          </div>

          <NInput
            v-model:value="inputMsg"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 8 }"
            placeholder="输入一条想让 Omubot 处理的消息"
            :disabled="sending"
            @keydown.enter.exact.prevent="onEnter"
            @compositionstart="onCompositionStart"
            @compositionend="onCompositionEnd"
          />

          <div class="sandbox-composer__actions">
            <NText depth="3" class="sandbox-composer__hint">
              {{ simulate === 'group' ? '群聊模式会启用隐私掩码并附带 Group ID。' : '私聊模式下仅带入 User ID。' }}
            </NText>
            <div class="sandbox-composer__buttons">
              <NButton secondary @click="clear">
                清空会话
              </NButton>
              <NButton type="primary" :loading="sending" :disabled="!inputMsg.trim()" @click="send">
                <template #icon>
                  <NIcon :component="PaperPlaneOutline" />
                </template>
                发送消息
              </NButton>
            </div>
          </div>
        </AppCard>
        </AppCard>

        <div class="sandbox-side">
          <AppCard bordered elevated class="sandbox-panel">
          <div class="sandbox-panel__head">
            <div>
              <p class="sandbox-panel__eyebrow">
                Context
              </p>
              <h3 class="sandbox-panel__title">
                会话参数
              </h3>
            </div>
            <NTag round size="small" type="info">
              sandbox_{{ userId }}
            </NTag>
          </div>

          <div class="sandbox-fields">
            <label class="sandbox-field">
              <span>模式</span>
              <NSelect v-model:value="simulate" :options="modeOptions" />
            </label>

            <label class="sandbox-field">
              <span>User ID</span>
              <NInput v-model:value="userId" placeholder="sandbox_user" />
            </label>

            <label v-if="simulate === 'group'" class="sandbox-field">
              <span>Group ID</span>
              <NInput v-model:value="groupId" placeholder="sandbox_group" />
            </label>
          </div>
          </AppCard>

          <AppCard bordered elevated class="sandbox-panel">
          <div class="sandbox-panel__head">
            <div>
              <p class="sandbox-panel__eyebrow">
                Runtime Notes
              </p>
              <h3 class="sandbox-panel__title">
                调试提示
              </h3>
            </div>
          </div>

          <div class="sandbox-notes">
            <div class="sandbox-note">
              <div class="sandbox-note__icon">
                <NIcon :component="simulate === 'group' ? PeopleOutline : PersonOutline" />
              </div>
              <div>
                <strong>{{ simulate === 'group' ? '群聊身份' : '私聊身份' }}</strong>
                <p>
                  {{ simulate === 'group' ? `请求会带入 ${effectiveGroupId} 作为群聊上下文。` : '请求会以单人会话方式发送，不附带群聊 ID。' }}
                </p>
              </div>
            </div>

            <div class="sandbox-note">
              <div class="sandbox-note__icon">
                <NIcon :component="PulseOutline" />
              </div>
              <div>
                <strong>当前链路</strong>
                <p>调用 `/api/admin/sandbox/chat`，并沿用实际 LLM chat 接口返回内容。</p>
              </div>
            </div>

            <div class="sandbox-note">
              <div class="sandbox-note__icon">
                <NIcon :component="SparklesOutline" />
              </div>
              <div>
                <strong>多轮观察</strong>
                <p>左侧消息流不会自动清空，适合连续测试同一身份下的多轮表现。</p>
              </div>
            </div>
          </div>
          </AppCard>
        </div>
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.sandbox-hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.sandbox-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) 320px;
  gap: 16px;
  min-height: 0;
}

.sandbox-chat {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 16px;
  min-height: 0;
  padding: 20px;
}

.sandbox-chat__head,
.sandbox-panel__head,
.sandbox-composer__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.sandbox-chat__eyebrow,
.sandbox-panel__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.sandbox-chat__title,
.sandbox-panel__title,
.sandbox-composer__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.sandbox-chat__body {
  min-height: 0;
  padding-right: 4px;
}

.sandbox-message-list {
  display: grid;
  gap: 14px;
}

.sandbox-message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sandbox-message--user {
  align-items: flex-end;
}

.sandbox-message--bot {
  align-items: flex-start;
}

.sandbox-message__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.sandbox-message__meta strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.sandbox-message__bubble {
  max-width: min(82%, 720px);
  padding: 14px 16px;
  border-radius: 18px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
}

.sandbox-message--user .sandbox-message__bubble {
  border-top-right-radius: 6px;
  background: rgba(var(--primary-color), 0.88);
  color: #fff;
  box-shadow: 0 12px 24px rgba(49, 108, 114, 0.18);
}

.sandbox-message--bot .sandbox-message__bubble {
  border: 1px solid var(--om-border);
  border-top-left-radius: 6px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
}

.sandbox-composer {
  display: grid;
  gap: 14px;
  padding: 18px;
  border-radius: 18px;
}

.sandbox-composer__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.sandbox-composer__hint {
  line-height: 1.7;
}

.sandbox-composer__buttons {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.sandbox-side {
  display: grid;
  align-content: start;
  gap: 16px;
  min-height: 0;
}

.sandbox-panel {
  display: grid;
  gap: 16px;
  padding: 20px;
}

.sandbox-fields {
  display: grid;
  gap: 14px;
}

.sandbox-field {
  display: grid;
  gap: 8px;
}

.sandbox-field span {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.sandbox-notes {
  display: grid;
  gap: 12px;
}

.sandbox-note {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 74%, transparent);
}

.sandbox-note__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: rgba(var(--primary-color), 0.12);
  color: rgb(var(--primary-color));
}

.sandbox-note strong {
  display: block;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.sandbox-note p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

@media (max-width: 1180px) {
  .sandbox-layout {
    grid-template-columns: 1fr;
  }

  .sandbox-side {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .sandbox-composer__actions,
  .sandbox-chat__head,
  .sandbox-panel__head,
  .sandbox-composer__head {
    flex-direction: column;
    align-items: stretch;
  }

  .sandbox-side {
    grid-template-columns: 1fr;
  }

  .sandbox-composer__buttons {
    justify-content: stretch;
  }

  .sandbox-message__bubble {
    max-width: 100%;
  }
}
</style>

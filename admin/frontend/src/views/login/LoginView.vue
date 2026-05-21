<script setup lang="ts">
import type { Component } from 'vue'

import {
  AlertCircleOutline,
  LockClosedOutline,
  PulseOutline,
  ShieldCheckmarkOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import AppCard from '../../components/common/AppCard.vue'
import TheLogo from '../../components/common/TheLogo.vue'
import { useAuthStore } from '../../stores/auth'

interface FeatureItem {
  title: string
  description: string
  icon: Component
}

const auth = useAuthStore()
const message = useMessage()

const inputRef = ref<{ focus: () => void } | null>(null)
const token = ref('')
const loading = ref(false)
const shaking = ref(false)
const capsLockOn = ref(false)
const failureCount = ref(0)
const cooldownLeft = ref(0)
const lastLoginAt = ref<string | null>(localStorage.getItem('admin:lastLoginAt'))

const COOLDOWN_THRESHOLD = 5
const COOLDOWN_SECONDS = 30

const features: FeatureItem[] = [
  {
    title: '运行监控',
    description: '统一查看实时日志、系统状态与 Bot 运行心跳。',
    icon: PulseOutline,
  },
  {
    title: '安全管控',
    description: '通过 Admin Token 控制敏感操作入口，隔离管理端写权限。',
    icon: ShieldCheckmarkOutline,
  },
  {
    title: '对话调优',
    description: '在人设、记忆、插件与沙盒之间快速来回切换。',
    icon: SparklesOutline,
  },
]

const isInsecureContext = computed(() => {
  if (typeof window === 'undefined') return false
  const { protocol, hostname } = window.location
  return protocol === 'http:' && !['localhost', '127.0.0.1', '::1'].includes(hostname)
})

const cooldownActive = computed(() => cooldownLeft.value > 0)

const submitLabel = computed(() => {
  if (cooldownActive.value) return `已锁定 (${cooldownLeft.value}s)`
  if (loading.value) return '正在验证…'
  return '登录并进入控制台'
})

let cooldownTimer: number | undefined

function triggerShake() {
  shaking.value = false
  requestAnimationFrame(() => {
    shaking.value = true
    window.setTimeout(() => { shaking.value = false }, 360)
  })
}

function startCooldown() {
  cooldownLeft.value = COOLDOWN_SECONDS
  cooldownTimer && window.clearInterval(cooldownTimer)
  cooldownTimer = window.setInterval(() => {
    cooldownLeft.value -= 1
    if (cooldownLeft.value <= 0) {
      cooldownLeft.value = 0
      cooldownTimer && window.clearInterval(cooldownTimer)
      cooldownTimer = undefined
      failureCount.value = 0
    }
  }, 1000)
}

function detectCapsLock(e: KeyboardEvent) {
  if (typeof e.getModifierState !== 'function') return
  capsLockOn.value = e.getModifierState('CapsLock')
}

async function handleLogin() {
  if (cooldownActive.value) {
    message.warning(`已锁定，请在 ${cooldownLeft.value} 秒后再试`)
    triggerShake()
    return
  }
  if (!token.value.trim()) {
    message.warning('请输入 Admin Token')
    triggerShake()
    return
  }

  loading.value = true
  try {
    const resp = await auth.login(token.value.trim())
    if (resp.ok) {
      const now = new Date().toLocaleString('zh-CN', { hour12: false })
      localStorage.setItem('admin:lastLoginAt', now)
      lastLoginAt.value = now
      failureCount.value = 0
      return
    }

    failureCount.value += 1
    triggerShake()

    if (resp.error === 'invalid_token') {
      message.error(`Token 无效（已尝试 ${failureCount.value}/${COOLDOWN_THRESHOLD}）`)
    } else {
      message.error('登录失败，请检查后端服务连通性')
    }

    if (failureCount.value >= COOLDOWN_THRESHOLD) {
      message.warning(`连续失败 ${COOLDOWN_THRESHOLD} 次，已锁定 ${COOLDOWN_SECONDS} 秒`)
      startCooldown()
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // Defer focus until next tick so NInput has fully mounted.
  nextTick(() => inputRef.value?.focus())
})

onBeforeUnmount(() => {
  cooldownTimer && window.clearInterval(cooldownTimer)
})
</script>
<template>
  <div class="login-shell">
    <div class="login-shell__glow login-shell__glow--left" aria-hidden="true" />
    <div class="login-shell__glow login-shell__glow--right" aria-hidden="true" />

    <div class="login-shell__grid">
      <section class="login-brand" aria-hidden="true">
        <div class="login-brand__mark">
          <TheLogo size="lg" />
          <div class="login-brand__mark-copy">
            <p class="login-brand__eyebrow">Omubot Runtime Console</p>
            <h1 class="login-brand__title">控制台登录</h1>
          </div>
        </div>

        <p class="login-brand__lead">
          统一管理 Bot 的运行状态、记忆数据、调度行为与实时调试入口。
          它是一块操作台，不只是一个配置页。
        </p>

        <div class="login-brand__chips">
          <span class="login-chip">Observability</span>
          <span class="login-chip">Memory Ops</span>
          <span class="login-chip">Sandbox</span>
        </div>

        <div class="login-feature-list">
          <div
            v-for="item in features"
            :key="item.title"
            class="login-feature"
          >
            <div class="login-feature__icon">
              <NIcon :component="item.icon" :size="18" />
            </div>
            <div>
              <h2 class="login-feature__title">{{ item.title }}</h2>
              <p class="login-feature__description">{{ item.description }}</p>
            </div>
          </div>
        </div>
      </section>

      <section class="login-panel">
        <AppCard
          bordered
          elevated
          class="login-card"
          :class="{ 'login-card--shake': shaking }"
        >
          <div class="login-card__head">
            <p class="login-card__eyebrow">Secure Access</p>
            <h2 class="login-card__title">进入 Omubot 控制台</h2>
            <p class="login-card__description">
              使用服务器环境变量 <code>ADMIN_TOKEN</code> 对应的值登录。
            </p>
          </div>

          <div
            v-if="isInsecureContext"
            class="login-warning"
            role="alert"
          >
            <NIcon :component="AlertCircleOutline" :size="16" />
            <span>当前为非 HTTPS 连接，Token 将以明文传输，仅建议在受信网络使用。</span>
          </div>

          <NForm class="login-form" @submit.prevent="handleLogin">
            <NFormItem label="Admin Token" :show-feedback="false">
              <NInput
                ref="inputRef"
                v-model:value="token"
                type="password"
                size="large"
                show-password-on="click"
                placeholder="输入 ADMIN_TOKEN"
                :input-props="{ autocomplete: 'current-password', spellcheck: 'false' }"
                :disabled="cooldownActive"
                @keyup.enter="handleLogin"
                @keydown="detectCapsLock"
                @keyup="detectCapsLock"
              />
            </NFormItem>

            <p
              v-if="capsLockOn"
              class="login-hint login-hint--warn"
              role="status"
            >
              <NIcon :component="AlertCircleOutline" :size="14" />
              <span>检测到 Caps Lock 已开启</span>
            </p>

            <NButton
              type="primary"
              size="large"
              :loading="loading"
              :disabled="cooldownActive || !token.trim()"
              block
              class="login-submit"
              @click="handleLogin"
            >
              <template #icon>
                <NIcon :component="LockClosedOutline" />
              </template>
              {{ submitLabel }}
            </NButton>
          </NForm>

          <div class="login-card__meta">
            <p
              v-if="lastLoginAt"
              class="login-card__last"
            >
              上次登录: <span>{{ lastLoginAt }}</span>
            </p>
            <div class="login-card__footer">
              <span class="login-card__footer-dot" aria-hidden="true" />
              建议仅在受信网络与本机运维场景下暴露管理端。
            </div>
          </div>
        </AppCard>
      </section>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(var(--primary-color), 0.22), transparent 32%),
    radial-gradient(circle at bottom right, rgba(var(--primary-color), 0.14), transparent 28%),
    linear-gradient(160deg, var(--om-bg) 0%, var(--om-bg-soft) 100%);
}

.login-shell__glow {
  position: absolute;
  border-radius: 999px;
  background: rgba(var(--primary-color), 0.12);
  filter: blur(70px);
  pointer-events: none;
}

.login-shell__glow--left { top: -120px; left: -90px; width: 320px; height: 320px; }
.login-shell__glow--right { right: -110px; bottom: -100px; width: 300px; height: 300px; }

.login-shell__grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(360px, 460px);
  gap: 32px;
  align-items: stretch;
  width: min(1120px, calc(100vw - 48px));
  min-height: 100%;
  margin: 0 auto;
  padding: 40px 0;
}

.login-brand {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 28px 8px;
}

.login-brand__mark {
  display: flex;
  align-items: center;
  gap: 20px;
}

.login-brand__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.login-brand__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: clamp(30px, 4vw, 48px);
  line-height: 1.04;
  letter-spacing: -0.04em;
}

.login-brand__lead {
  margin: 28px 0 0;
  max-width: 620px;
  color: var(--om-text-2);
  font-size: 16px;
  line-height: 1.85;
}

.login-brand__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 24px;
}

.login-chip {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--om-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface) 70%, transparent);
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.login-feature-list {
  display: grid;
  gap: 12px;
  margin-top: 32px;
}

.login-feature {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface) 70%, transparent);
  backdrop-filter: blur(12px);
}

.login-feature__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: rgba(var(--primary-color), 0.12);
  color: rgb(var(--primary-color));
}

.login-feature__title {
  margin: 2px 0 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 600;
}

.login-feature__description {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.login-panel {
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-card {
  width: 100%;
  padding: 32px;
  border-radius: 24px;
}

.login-card--shake {
  animation: login-card-shake 0.36s cubic-bezier(0.36, 0.07, 0.19, 0.97);
}

@keyframes login-card-shake {
  10%, 90% { transform: translateX(-2px); }
  20%, 80% { transform: translateX(4px); }
  30%, 50%, 70% { transform: translateX(-6px); }
  40%, 60% { transform: translateX(6px); }
}

.login-card__head {
  margin-bottom: 16px;
}

.login-card__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.login-card__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 28px;
  line-height: 1.08;
  letter-spacing: -0.03em;
}

.login-card__description {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.7;
}

.login-card__description code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.login-warning {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: 12px 0 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--om-warning) 35%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-warning) 10%, transparent);
  color: var(--om-warning);
  font-size: 12px;
  line-height: 1.6;
}

.login-form {
  margin-top: 16px;
}

.login-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.4;
}

.login-hint--warn {
  color: var(--om-warning);
}

.login-submit {
  margin-top: 16px;
  height: 48px;
}

.login-card__meta {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login-card__last {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.login-card__last span {
  color: var(--om-text-2);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.login-card__footer {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.login-card__footer-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: rgb(var(--primary-color));
  box-shadow: 0 0 0 6px rgba(var(--primary-color), 0.12);
}

@media (max-width: 980px) {
  .login-shell__grid {
    grid-template-columns: 1fr;
    width: min(560px, calc(100vw - 32px));
    gap: 24px;
    padding: 24px 0 32px;
  }
  .login-brand { padding: 12px 0 0; }
  .login-brand__lead { font-size: 14px; line-height: 1.7; margin-top: 20px; }
  .login-brand__chips { margin-top: 16px; }
  .login-feature-list { margin-top: 20px; }
  .login-panel { justify-content: stretch; }
}

@media (max-width: 640px) {
  .login-shell__grid {
    width: calc(100vw - 24px);
  }
  .login-brand__mark { align-items: flex-start; gap: 14px; }
  .login-card { padding: 24px 20px; border-radius: 20px; }
  .login-card__title { font-size: 24px; }
}

@media (prefers-reduced-motion: reduce) {
  .login-card--shake { animation: none; }
}
</style>


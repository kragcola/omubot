<script setup lang="ts">
import type { Component } from 'vue'

import {
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
const token = ref('')
const loading = ref(false)

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

async function handleLogin() {
  if (!token.value.trim()) {
    message.warning('请输入 Admin Token')
    return
  }

  loading.value = true
  try {
    const resp = await auth.login(token.value.trim())
    if (!resp.ok) {
      message.error('Token 无效')
    }
  } catch {
    message.error('登录失败，请检查后端服务')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-shell">
    <div class="login-shell__glow login-shell__glow--left" />
    <div class="login-shell__glow login-shell__glow--right" />

    <div class="login-shell__grid">
      <section class="login-brand">
        <div class="login-brand__mark">
          <TheLogo size="lg" />
          <div class="login-brand__mark-copy">
            <p class="login-brand__eyebrow">
              Omubot Runtime Console
            </p>
            <h1 class="login-brand__title">
              控制台登录
            </h1>
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
              <h2 class="login-feature__title">
                {{ item.title }}
              </h2>
              <p class="login-feature__description">
                {{ item.description }}
              </p>
            </div>
          </div>
        </div>
      </section>

      <section class="login-panel">
        <AppCard bordered elevated class="login-card">
          <div class="login-card__head">
            <p class="login-card__eyebrow">
              Secure Access
            </p>
            <h2 class="login-card__title">
              进入 Omubot 控制台
            </h2>
            <p class="login-card__description">
              使用服务器环境变量 `ADMIN_TOKEN` 对应的值登录。
            </p>
          </div>

          <NForm class="login-form" @submit.prevent="handleLogin">
            <NFormItem label="Admin Token">
              <NInput
                v-model:value="token"
                type="password"
                size="large"
                show-password-on="click"
                placeholder="输入 ADMIN_TOKEN"
                @keyup.enter="handleLogin"
              />
            </NFormItem>

            <NButton
              type="primary"
              size="large"
              :loading="loading"
              block
              class="login-submit"
              @click="handleLogin"
            >
              <template #icon>
                <NIcon :component="LockClosedOutline" />
              </template>
              登录并进入控制台
            </NButton>
          </NForm>

          <div class="login-card__footer">
            <span class="login-card__footer-dot" />
            建议仅在受信网络和本机运维场景下暴露管理端。
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

.login-shell__glow--left {
  top: -120px;
  left: -90px;
  width: 320px;
  height: 320px;
}

.login-shell__glow--right {
  right: -110px;
  bottom: -100px;
  width: 300px;
  height: 300px;
}

.login-shell__grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(360px, 460px);
  gap: 28px;
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
  padding: 28px 10px 28px 8px;
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
  gap: 10px;
  margin-top: 24px;
}

.login-chip {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--om-border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.34);
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.login-feature-list {
  display: grid;
  gap: 14px;
  margin-top: 34px;
}

.login-feature {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.28);
  backdrop-filter: blur(12px);
}

.login-feature__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 16px;
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
  padding: 28px;
  border-radius: 28px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(255, 255, 255, 0.86)),
    var(--om-surface);
}

.dark .login-card {
  background:
    linear-gradient(180deg, rgba(26, 38, 44, 0.94), rgba(26, 38, 44, 0.88)),
    var(--om-surface);
}

.login-card__head {
  margin-bottom: 20px;
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
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.7;
}

.login-form {
  margin-top: 18px;
}

.login-submit {
  margin-top: 8px;
  height: 48px;
}

.login-card__footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 20px;
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
    width: min(760px, calc(100vw - 32px));
    padding: 24px 0 32px;
  }

  .login-brand {
    padding: 12px 0 0;
  }

  .login-panel {
    justify-content: stretch;
  }
}

@media (max-width: 640px) {
  .login-shell__grid {
    width: calc(100vw - 24px);
    gap: 18px;
  }

  .login-brand__mark {
    align-items: flex-start;
  }

  .login-brand__lead {
    font-size: 14px;
  }

  .login-card {
    padding: 22px 18px;
    border-radius: 24px;
  }

  .login-card__title {
    font-size: 24px;
  }
}
</style>

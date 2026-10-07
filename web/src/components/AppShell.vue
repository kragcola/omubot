<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NMenu, NTag } from 'naive-ui'

import { navigation } from '@/app/router'
import { sessionState } from '@/app/session'
import AuthPanel from './AuthPanel.vue'

const route = useRoute()
const router = useRouter()
const navOpen = ref(false)

const activePath = computed(() => (route.path === '/' ? '/' : route.path))
const currentRoute = computed(() => route.meta as { label?: string; description?: string; requiresAdmin?: boolean })
const needsAdmin = computed(() => currentRoute.value.requiresAdmin === true)
const modeLabel = computed(() => {
  if (!sessionState.status) return '等待状态'
  return sessionState.status.mode === 'live' ? '实时模式' : '离线隔离'
})
const modeTagType = computed<'success' | 'warning' | 'default'>(() => {
  if (!sessionState.status) return 'default'
  return sessionState.status.mode === 'live' ? 'warning' : 'success'
})

async function navigate(path: string): Promise<void> {
  navOpen.value = false
  await router.push(path)
}

watch(() => route.path, () => {
  navOpen.value = false
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <n-button class="mobile-nav-button" quaternary circle aria-label="打开导航" @click="navOpen = !navOpen">
          <span aria-hidden="true">☰</span>
        </n-button>
        <div class="brand-mark" aria-hidden="true">O</div>
        <div>
          <div class="brand-name">OMUBOT</div>
          <div class="brand-caption">LOCAL CONTROL</div>
        </div>
      </div>
      <div class="topbar-status" aria-live="polite">
        <n-tag :type="modeTagType" size="small" round>{{ modeLabel }}</n-tag>
        <n-tag v-if="sessionState.adminAuthenticated" type="info" size="small" round>管理员</n-tag>
      </div>
    </header>
    <div class="app-body">
      <aside class="sidebar" :class="{ 'sidebar-open': navOpen }">
        <div class="sidebar-intro">
          <p class="eyebrow">实例控制台</p>
          <p class="sidebar-copy">把空白 Bot 配成可观察、可授权、可试聊的本地工作区。</p>
        </div>
        <n-menu :value="activePath" :options="navigation" :root-indent="16" @update:value="(key) => navigate(String(key))" />
        <div class="sidebar-foot">当前本机实例 · 离线优先</div>
      </aside>
      <main class="content-column">
        <div class="page-intro">
          <p class="eyebrow">{{ currentRoute.label }}</p>
          <h1>{{ currentRoute.label }}</h1>
          <p>{{ currentRoute.description }}</p>
        </div>
        <n-alert v-if="sessionState.status?.mode === 'offline'" class="notice" type="info" :show-icon="true">
          当前为离线隔离模式：配置和授权会保存到本实例，但对话使用模拟模型，不会进行真实模型对话或发送 QQ 消息；手动获取模型列表和验证真实模型会联网。
          QQ 接入页的主动连接会访问你填写的 NapCat WebUI，但不会自动发送 QQ 消息。模型配置保存后需重启；真实接入还需启动凭据、QQ 连接和以 --live 启动实例。
        </n-alert>
        <n-alert v-if="sessionState.status?.dev_web_bypass" class="notice" type="warning" :show-icon="true">
          开发模式：已跳过网页登录与管理请求鉴权。仅限本机离线开发，关闭启动开关即可恢复。
        </n-alert>
        <AuthPanel v-if="!sessionState.statusAuthenticated || (needsAdmin && !sessionState.adminAuthenticated)" />
        <router-view v-if="!needsAdmin || sessionState.adminAuthenticated" />
      </main>
    </div>
  </div>
</template>

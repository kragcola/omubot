<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NAlert, NButton, NCard, NInput, NSpace, NTabs, NTabPane, NTag, NText } from 'naive-ui'

import { apiErrorMessage } from '@/api/client'
import { loginAdmin, loginStatus, sessionState } from '@/app/session'

const route = useRoute()
const activeTab = ref(route.meta.requiresAdmin ? 'admin' : 'status')
const statusToken = ref('')
const adminToken = ref('')
const errorMessage = ref('')

watch(() => route.meta.requiresAdmin, (requiresAdmin) => {
  activeTab.value = requiresAdmin ? 'admin' : 'status'
})

async function submitStatus(): Promise<void> {
  if (!statusToken.value || sessionState.authBusy) return
  errorMessage.value = ''
  const token = statusToken.value
  statusToken.value = ''
  try {
    await loginStatus(token)
  } catch (error: unknown) {
    errorMessage.value = apiErrorMessage(error)
  }
}

async function submitAdmin(): Promise<void> {
  if (!adminToken.value || sessionState.authBusy) return
  errorMessage.value = ''
  const token = adminToken.value
  adminToken.value = ''
  try {
    await loginAdmin(token)
  } catch (error: unknown) {
    errorMessage.value = apiErrorMessage(error)
  }
}
</script>

<template>
  <n-card class="auth-card" :bordered="false">
    <div class="section-heading">
      <div>
        <p class="eyebrow">本机访问</p>
        <h2>登录控制台</h2>
        <n-text depth="3">只读状态和管理操作分别登录，凭据不会保存到页面。</n-text>
      </div>
      <n-tag v-if="sessionState.statusAuthenticated" type="success" size="small">状态已登录</n-tag>
    </div>
    <n-alert v-if="errorMessage" class="stack-gap" type="error" :show-icon="true">
      {{ errorMessage }}
    </n-alert>
    <n-alert v-if="sessionState.authNotice" class="stack-gap" type="warning" :show-icon="true">
      {{ sessionState.authNotice }}
    </n-alert>
    <n-tabs v-model:value="activeTab" type="segment" animated class="stack-gap">
      <n-tab-pane name="status" tab="只读状态">
        <form class="login-form" @submit.prevent="submitStatus">
          <label for="status-token">状态访问凭据</label>
          <n-input v-model:value="statusToken" type="password" show-password-on="click" :input-props="{ id: 'status-token', 'aria-label': '状态访问凭据', autocomplete: 'off' }" placeholder="需填写：本实例的 status 凭据" />
          <p class="form-hint">无通用默认值。使用本实例 credentials.toml 中的 status 值，仅用于查看状态。</p>
          <n-button type="primary" attr-type="submit" :loading="sessionState.authBusy === 'status'" :disabled="!statusToken || Boolean(sessionState.authBusy)">查看运行概览</n-button>
        </form>
      </n-tab-pane>
      <n-tab-pane name="admin" tab="管理员">
        <form class="login-form" @submit.prevent="submitAdmin">
          <label for="admin-token">管理员凭据</label>
          <n-input v-model:value="adminToken" type="password" show-password-on="click" :input-props="{ id: 'admin-token', 'aria-label': '管理员凭据', autocomplete: 'off' }" placeholder="需填写：本实例的 admin 凭据" />
          <p class="form-hint">无通用默认值。使用本实例 credentials.toml 中的 admin 值，可管理配置、授权和离线试聊；不是模型 API key。</p>
          <n-button type="primary" attr-type="submit" :loading="sessionState.authBusy === 'admin'" :disabled="!adminToken || Boolean(sessionState.authBusy)">进入管理区</n-button>
        </form>
      </n-tab-pane>
    </n-tabs>
    <n-space v-if="!sessionState.statusAuthenticated && !sessionState.adminAuthenticated" size="small">
      <n-text depth="3">需要凭据才能读取当前实例。</n-text>
    </n-space>
  </n-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NInput, NQrCode, NTag } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { NapcatConnectRequest, NapcatLogs, NapcatRuntime, NapcatStatus } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const DEFAULT_ENDPOINT = 'http://127.0.0.1:6100'

const endpoint = ref('')
const token = ref('')
const totpCode = ref('')
const status = ref<NapcatStatus | null>(null)
const runtime = ref<NapcatRuntime | null>(null)
const statusBusy = ref(false)
const statusError = ref('')
const actionBusy = ref<'connect' | 'disconnect' | 'start' | 'stop' | null>(null)
const actionError = ref('')
const actionMessage = ref('')
const logLines = ref<string[]>([])
const logDetail = ref('')
const logError = ref('')
const logSampling = ref(false)
const logBusy = ref(false)

let disposed = false
let viewEpoch = 0
let statusSequence = 0
let actionSequence = 0
let logSequence = 0
let statusTimer: ReturnType<typeof setTimeout> | undefined
let logTimer: ReturnType<typeof setTimeout> | undefined
let statusController: AbortController | undefined
let actionController: AbortController | undefined
let logController: AbortController | undefined

type CapturedResult<T> = { ok: true; value: T } | { ok: false; error: unknown }

function captureResult<T>(promise: Promise<T>): Promise<CapturedResult<T>> {
  return promise.then(
    (value) => ({ ok: true, value }),
    (error: unknown) => ({ ok: false, error }),
  )
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

const isAdmin = computed(() => sessionState.adminAuthenticated)
const qrValue = computed(() => {
  if (statusError.value || status.value?.connected !== true || status.value.logged_in === true) return ''
  return status.value.qr_url?.trim() ?? ''
})
const connectionLabel = computed(() => statusError.value ? '状态待确认（自动重试）' : status.value?.connected === true ? '已连接' : '未连接')
const connectionTagType = computed<'success' | 'default'>(() => !statusError.value && status.value?.connected === true ? 'success' : 'default')
const loginLabel = computed(() => {
  if (statusError.value) return '状态待确认'
  if (status.value?.logged_in === true) return 'QQ 已在线'
  if (status.value?.connected === true) return '等待 QQ 登录'
  return '未连接'
})
const loginTagType = computed<'success' | 'warning' | 'default'>(() => {
  if (statusError.value) return 'warning'
  if (status.value?.logged_in === true) return 'success'
  if (status.value?.connected === true) return 'warning'
  return 'default'
})
const availabilityLabel = computed(() => {
  if (statusError.value) return '状态待确认（正在重试）'
  if (status.value?.connected !== true) return '未连接'
  if (status.value.logged_in === true) return 'QQ 已在线'
  if (status.value.offline === true) return 'QQ 已掉线'
  return '等待 QQ 登录'
})
const phaseLabel = computed(() => status.value?.phase?.trim() || '未读取')
const runtimePhaseLabel = computed(() => {
  if (!runtime.value) return '未读取'
  if (!runtime.value.available) return '未配置'
  if (runtime.value.phase === 'running') return '运行中'
  if (runtime.value.phase === 'stopped') return '已停止'
  return '已停用'
})
const runtimeTagType = computed<'success' | 'warning' | 'default'>(() => {
  if (!runtime.value) return 'default'
  if (!runtime.value.available || runtime.value.phase === 'disabled') return 'warning'
  return runtime.value.phase === 'running' ? 'success' : 'default'
})
const runtimeOomLabel = computed(() => {
  if (!runtime.value) return '未读取'
  return runtime.value.oom_killed ? '曾因内存不足退出' : '未检测到内存不足退出'
})
const runtimeStartLabel = computed(() => runtime.value?.phase === 'running' ? '连接 NapCat' : '启动并连接 NapCat')
const logText = computed(() => logLines.value.join('\n'))
const canConnect = computed(() => Boolean(
  isAdmin.value &&
  actionBusy.value === null &&
  !statusBusy.value &&
  !logBusy.value &&
  token.value.trim(),
))
const canReadStatus = computed(() => Boolean(isAdmin.value && actionBusy.value === null && !statusBusy.value && !logBusy.value))
const canRefresh = computed(() => Boolean(
  isAdmin.value &&
  status.value?.connected === true &&
  status.value.logged_in !== true &&
  actionBusy.value === null &&
  !statusBusy.value &&
  !logBusy.value,
))
const canDisconnect = computed(() => Boolean(
  isAdmin.value &&
  actionBusy.value === null &&
  !statusBusy.value &&
  !logBusy.value,
))
const canStartRuntime = computed(() => Boolean(
  isAdmin.value &&
  runtime.value?.available === true &&
  (status.value?.connected !== true || runtime.value?.phase === 'stopped') &&
  actionBusy.value === null &&
  !statusBusy.value &&
  !logBusy.value,
))
const canStopRuntime = computed(() => Boolean(
  isAdmin.value &&
  runtime.value?.available === true &&
  runtime.value.phase === 'running' &&
  actionBusy.value === null &&
  !statusBusy.value &&
  !logBusy.value,
))
const canStartLogs = computed(() => Boolean(
  isAdmin.value &&
  status.value?.connected === true &&
  !logSampling.value &&
  !logBusy.value &&
  actionBusy.value === null,
))

function clearStatusTimer(): void {
  if (statusTimer !== undefined) {
    clearTimeout(statusTimer)
    statusTimer = undefined
  }
}

function clearLogTimer(): void {
  if (logTimer !== undefined) {
    clearTimeout(logTimer)
    logTimer = undefined
  }
}

function isCurrentRequest(epoch: number, localEpoch: number): boolean {
  return !disposed && sessionState.adminAuthenticated && isCurrentAdminEpoch(epoch) && localEpoch === viewEpoch
}

function clearAdminView(): void {
  viewEpoch += 1
  statusSequence += 1
  actionSequence += 1
  logSequence += 1
  clearStatusTimer()
  clearLogTimer()
  statusController?.abort()
  actionController?.abort()
  logController?.abort()
  statusController = undefined
  actionController = undefined
  logController = undefined
  endpoint.value = ''
  token.value = ''
  totpCode.value = ''
  status.value = null
  runtime.value = null
  statusBusy.value = false
  statusError.value = ''
  actionBusy.value = null
  actionError.value = ''
  actionMessage.value = ''
  logLines.value = []
  logDetail.value = ''
  logError.value = ''
  logSampling.value = false
  logBusy.value = false
}

function scheduleStatusPoll(delay = 3000): void {
  clearStatusTimer()
  if (disposed || !sessionState.adminAuthenticated) return
  statusTimer = setTimeout(() => {
    statusTimer = undefined
    void loadStatus()
  }, delay)
}

async function loadStatus(): Promise<void> {
  if (disposed || !sessionState.adminAuthenticated) return
  if (actionBusy.value !== null) {
    scheduleStatusPoll()
    return
  }
  if (logBusy.value) {
    scheduleStatusPoll()
    return
  }
  if (statusBusy.value) return

  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++statusSequence
  const controller = new AbortController()
  statusController = controller
  statusBusy.value = true
  try {
    const [result, runtimeResult] = await Promise.all([
      captureResult(apiRequest<NapcatStatus>('/api/admin/napcat/status', { signal: controller.signal })),
      captureResult(apiRequest<NapcatRuntime>('/api/admin/napcat/runtime', { signal: controller.signal })),
    ])
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== statusSequence) return
    if (!result.ok && isApiError(result.error) && result.error.status === 401) {
      expireAdminSession()
      return
    }
    if (!runtimeResult.ok && isApiError(runtimeResult.error) && runtimeResult.error.status === 401) {
      expireAdminSession()
      return
    }
    const errors: string[] = []
    if (result.ok) {
      status.value = result.value
    } else if (!isAbortError(result.error)) {
      actionMessage.value = ''
      stopLogSampling()
      errors.push(`QQ 状态读取失败：${apiErrorMessage(result.error)}`)
    }
    if (runtimeResult.ok) {
      runtime.value = runtimeResult.value
    } else if (!isAbortError(runtimeResult.error)) {
      errors.push(`独立实例状态读取失败：${apiErrorMessage(runtimeResult.error)}`)
    }
    statusError.value = errors.join(' ')
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== statusSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (isAbortError(error)) return
    actionMessage.value = ''
    clearStatusTimer()
    stopLogSampling()
    statusError.value = apiErrorMessage(error)
  } finally {
    if (statusController === controller) {
      statusController = undefined
      statusBusy.value = false
    }
    if (sequence === statusSequence && !disposed && sessionState.adminAuthenticated) scheduleStatusPoll(statusError.value ? 5000 : 3000)
  }
}

async function refreshNapcatStatus(): Promise<void> {
  if (!canRefresh.value) return
  clearStatusTimer()
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++statusSequence
  const controller = new AbortController()
  statusController = controller
  statusBusy.value = true
  statusError.value = ''
  actionMessage.value = ''
  try {
    const result = await apiRequest<NapcatStatus>('/api/admin/napcat/refresh', {
      method: 'POST',
      adminMutation: true,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== statusSequence) return
    status.value = result
    actionMessage.value = '二维码刷新请求已发送。'
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== statusSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    status.value = null
    clearStatusTimer()
    stopLogSampling()
    statusError.value = apiErrorMessage(error)
  } finally {
    if (statusController === controller) {
      statusController = undefined
      statusBusy.value = false
    }
    if (sequence === statusSequence && !statusError.value && !disposed && sessionState.adminAuthenticated) scheduleStatusPoll()
  }
}

async function startNapcat(): Promise<void> {
  if (!canStartRuntime.value) return
  clearStatusTimer()
  stopLogSampling()
  logLines.value = []
  logDetail.value = ''
  logError.value = ''
  status.value = null
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++actionSequence
  const controller = new AbortController()
  actionController = controller
  actionBusy.value = 'start'
  actionError.value = ''
  actionMessage.value = ''
  statusError.value = ''
  try {
    const result = await apiRequest<NapcatStatus>('/api/admin/napcat/start', {
      method: 'POST',
      adminMutation: true,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    status.value = result
    runtime.value = runtime.value
      ? { ...runtime.value, phase: 'running' }
      : { available: true, phase: 'running', oom_killed: false }
    token.value = ''
    totpCode.value = ''
    actionMessage.value = result.logged_in
      ? 'NapCat 已连接，QQ 已在线，无需再次扫码。'
      : result.qr_url
        ? 'NapCat 已连接，请用手机 QQ 扫描二维码确认登录。无需手动复制令牌。'
        : 'NapCat 已连接。无需手动复制令牌。'
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    actionError.value = apiErrorMessage(error)
  } finally {
    if (actionController === controller) {
      actionController = undefined
      actionBusy.value = null
    }
    if (sequence === actionSequence && !disposed && sessionState.adminAuthenticated) {
      scheduleStatusPoll(actionError.value ? 0 : 3000)
    }
  }
}

async function stopNapcat(): Promise<void> {
  if (!canStopRuntime.value) return
  clearStatusTimer()
  stopLogSampling()
  logLines.value = []
  logDetail.value = ''
  logError.value = ''
  status.value = null
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++actionSequence
  const controller = new AbortController()
  actionController = controller
  actionBusy.value = 'stop'
  actionError.value = ''
  actionMessage.value = ''
  statusError.value = ''
  try {
    const result = await apiRequest<NapcatRuntime>('/api/admin/napcat/stop', {
      method: 'POST',
      adminMutation: true,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    runtime.value = result
    token.value = ''
    totpCode.value = ''
    actionMessage.value = 'NapCat 实例已停止，QQ 已离线；登录数据仍保留。'
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    actionError.value = apiErrorMessage(error)
  } finally {
    if (actionController === controller) {
      actionController = undefined
      actionBusy.value = null
    }
    if (sequence === actionSequence && !disposed && sessionState.adminAuthenticated) {
      scheduleStatusPoll(actionError.value ? 0 : 3000)
    }
  }
}

async function connectNapcat(): Promise<void> {
  if (!canConnect.value) return
  clearStatusTimer()
  stopLogSampling()
  logLines.value = []
  logDetail.value = ''
  logError.value = ''
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++actionSequence
  const controller = new AbortController()
  actionController = controller
  const request: NapcatConnectRequest = {
    endpoint: endpoint.value.trim() || DEFAULT_ENDPOINT,
    token: token.value.trim(),
  }
  const totp = totpCode.value.trim()
  if (totp) request.totp_code = totp
  actionBusy.value = 'connect'
  actionError.value = ''
  actionMessage.value = ''
  statusError.value = ''
  try {
    const result = await apiRequest<NapcatStatus>('/api/admin/napcat/connect', {
      method: 'POST',
      adminMutation: true,
      body: request,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    status.value = result
    token.value = ''
    totpCode.value = ''
    actionMessage.value = result.qr_url
      ? '连接请求已接受，请使用手机 QQ 扫描二维码确认。'
      : 'NapCat 管理连接已建立。'
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    status.value = null
    clearStatusTimer()
    actionError.value = apiErrorMessage(error)
  } finally {
    if (actionController === controller) {
      actionController = undefined
      actionBusy.value = null
    }
    if (sequence === actionSequence && !actionError.value && !disposed && sessionState.adminAuthenticated) scheduleStatusPoll()
  }
}

async function disconnectNapcat(): Promise<void> {
  if (!canDisconnect.value) return
  clearStatusTimer()
  stopLogSampling()
  logLines.value = []
  logDetail.value = ''
  logError.value = ''
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++actionSequence
  const controller = new AbortController()
  actionController = controller
  actionBusy.value = 'disconnect'
  actionError.value = ''
  actionMessage.value = ''
  statusError.value = ''
  try {
    const result = await apiRequest<NapcatStatus>('/api/admin/napcat/disconnect', {
      method: 'POST',
      adminMutation: true,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    status.value = { ...result, connected: false, qr_url: '' }
    token.value = ''
    totpCode.value = ''
    actionMessage.value = '已断开 NapCat 管理连接；不会执行 QQ 登出。'
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== actionSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    status.value = null
    clearStatusTimer()
    actionError.value = apiErrorMessage(error)
  } finally {
    if (actionController === controller) {
      actionController = undefined
      actionBusy.value = null
    }
    if (sequence === actionSequence && !actionError.value && !disposed && sessionState.adminAuthenticated) scheduleStatusPoll()
  }
}

function scheduleLogSample(delay = 2000): void {
  clearLogTimer()
  if (disposed || !sessionState.adminAuthenticated || !logSampling.value) return
  logTimer = setTimeout(() => {
    logTimer = undefined
    void sampleLogs()
  }, delay)
}

async function sampleLogs(): Promise<void> {
  if (disposed || !sessionState.adminAuthenticated || !logSampling.value || logBusy.value) return
  if (statusBusy.value) {
    scheduleLogSample()
    return
  }
  const epoch = currentAdminEpoch()
  const localEpoch = viewEpoch
  const sequence = ++logSequence
  const controller = new AbortController()
  logController = controller
  logBusy.value = true
  logError.value = ''
  try {
    const result = await apiRequest<NapcatLogs>('/api/admin/napcat/logs', {
      adminMutation: true,
      signal: controller.signal,
    })
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== logSequence) return
    logLines.value = result.lines.slice(-200)
    logDetail.value = result.detail
  } catch (error: unknown) {
    if (!isCurrentRequest(epoch, localEpoch) || sequence !== logSequence) return
    if (isApiError(error) && error.status === 401) {
      expireAdminSession()
      return
    }
    if (error instanceof Error && error.name === 'AbortError') return
    logError.value = apiErrorMessage(error)
    logSampling.value = false
    clearLogTimer()
  } finally {
    if (logController === controller) {
      logController = undefined
      logBusy.value = false
    }
    if (sequence === logSequence && logSampling.value && !disposed && sessionState.adminAuthenticated) scheduleLogSample()
  }
}

function startLogSampling(): void {
  if (!canStartLogs.value) return
  logSampling.value = true
  logError.value = ''
  logDetail.value = ''
  scheduleLogSample()
}

function stopLogSampling(): void {
  logSampling.value = false
  clearLogTimer()
  logSequence += 1
  logController?.abort()
}

watch(
  () => sessionState.adminAuthenticated,
  (authenticated) => {
    if (!authenticated) {
      clearAdminView()
      return
    }
    void loadStatus()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disposed = true
  clearAdminView()
})
</script>

<template>
  <section class="napcat-view" aria-labelledby="napcat-heading">
    <n-card v-if="!isAdmin" class="page-card empty-card" :bordered="false">
      <div class="empty-heading">
        <p class="eyebrow">管理员工具</p>
        <h2 id="napcat-heading">QQ 接入</h2>
      </div>
      <p class="subtle-text">管理员登录后才能连接 NapCat WebUI。</p>
    </n-card>

    <template v-else>
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">独立接入</p>
            <h2 id="napcat-heading">QQ 接入</h2>
          </div>
          <div class="napcat-tags" aria-live="polite">
            <n-tag :type="connectionTagType" size="small" round>管理连接：{{ connectionLabel }}</n-tag>
            <n-tag :type="loginTagType" size="small" round>QQ：{{ loginLabel }}</n-tag>
          </div>
        </div>
        <p class="napcat-intro">这里连接的是独立开发的 NapCat WebUI，不是模型配置或 OneBot 端点。已配置的独立实例可在下方启动；管理连接与 QQ 在线状态分开显示。</p>

        <n-alert v-if="statusError" class="notice" type="error" :show-icon="true" role="alert">{{ statusError }}
        </n-alert>
        <n-alert v-if="actionError" class="notice" type="error" :show-icon="true" role="alert">{{ actionError }}
        </n-alert>
        <n-alert v-if="logError" class="notice" type="error" :show-icon="true" role="alert">{{ logError }}
        </n-alert>
        <n-alert v-if="actionMessage" class="notice" type="success" :show-icon="true" role="status">{{ actionMessage }}
        </n-alert>

        <div class="info-grid napcat-status-grid">
          <div class="info-block">
            <span class="label">QQ 连接状态</span>
            <strong class="value">{{ availabilityLabel }}</strong>
            <p class="subtle-text">阶段：{{ phaseLabel }}</p>
          </div>
          <div class="info-block">
            <span class="label">QQ 在线</span>
            <strong class="value">{{ loginLabel }}</strong>
            <p class="subtle-text">扫码确认后，在线状态会在手动刷新或轮询中更新。</p>
          </div>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">独立实例</p>
            <h2>NapCat 运行控制</h2>
          </div>
          <n-tag :type="runtimeTagType" size="small" round aria-live="polite">实例：{{ runtimePhaseLabel }}</n-tag>
        </div>
        <p class="napcat-copy">启动会自动连接 NapCat WebUI，并在连接成功后展示现有二维码或 QQ 状态；无需手动复制令牌。手动连接表单保留在下方的高级/备用区域。</p>
        <n-alert v-if="runtime && !runtime.available" class="notice" type="warning" :show-icon="true" role="status">
          独立 NapCat 实例尚未配置，启动和停止按钮已停用。你仍可在下方手动连接已经运行的 NapCat WebUI。
        </n-alert>
        <n-alert v-else-if="runtime?.phase === 'disabled'" class="notice" type="warning" :show-icon="true" role="status">
          独立 NapCat 实例当前已停用，请先完成运行配置；手动连接已经运行的 NapCat WebUI 仍可用。
        </n-alert>
        <div class="info-grid napcat-runtime-grid">
          <div class="info-block">
            <span class="label">实例状态</span>
            <strong class="value">{{ runtimePhaseLabel }}</strong>
            <p class="subtle-text">仅管理当前独立开发实例。</p>
          </div>
          <div class="info-block">
            <span class="label">运行保护</span>
            <strong class="value">{{ runtimeOomLabel }}</strong>
            <p class="subtle-text">停止实例会让 QQ 离线，但会保留登录数据。</p>
          </div>
        </div>
        <div class="button-row">
          <n-button type="primary" :loading="actionBusy === 'start'" :disabled="!canStartRuntime" @click="void startNapcat()">
            {{ runtimeStartLabel }}
          </n-button>
          <n-button type="error" secondary :loading="actionBusy === 'stop'" :disabled="!canStopRuntime" @click="void stopNapcat()">
            停止实例
          </n-button>
        </div>
        <p class="form-hint" role="status">启动或停止期间会暂停日志采样和状态轮询；操作完成后会自动重新读取运行状态。</p>
      </n-card>

      <n-card v-if="qrValue" class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">登录确认</p>
            <h2>用手机 QQ 扫码</h2>
          </div>
          <n-tag type="warning" size="small" round>等待确认</n-tag>
        </div>
        <div class="napcat-qr-layout">
          <div>
            <p class="napcat-copy">连接请求已返回二维码。请用手机 QQ 扫描并确认登录，再等待上方 QQ 状态变为在线。</p>
            <p class="form-hint">二维码只在当前管理员会话中展示；状态轮询不会替你执行登录操作。</p>
          </div>
          <div class="napcat-qr" aria-label="NapCat QQ 登录二维码">
            <n-qr-code :value="qrValue" :size="220" />
          </div>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">高级 / 备用</p>
            <h2>连接管理</h2>
          </div>
          <n-tag v-if="statusBusy" type="info" size="small" round>正在读取</n-tag>
        </div>
        <details>
          <summary>高级连接：手动填写地址与令牌（已连接时无需填写）</summary>
        <p class="napcat-copy">填写已经运行的 NapCat WebUI 地址和令牌。独立实例控制不可用时可使用这里的备用入口；地址留空时使用本机默认值，TOTP 仅在你的 NapCat 实例要求时填写。</p>
        <div class="form-grid">
          <div class="form-field form-field-wide">
            <label class="field-label" for="napcat-endpoint">NapCat WebUI 地址</label>
            <n-input
              :value="endpoint"
              :placeholder="DEFAULT_ENDPOINT"
              :disabled="actionBusy !== null || statusBusy"
              :input-props="{ id: 'napcat-endpoint', 'aria-label': 'NapCat WebUI 地址' }"
              @update:value="(value) => endpoint = value"
            />
            <p class="form-hint">默认：{{ DEFAULT_ENDPOINT }}。这是 NapCat 独立管理地址，不是模型 Endpoint 或 OneBot Endpoint；请填完整 HTTP(S) 地址。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="napcat-token">WebUI 令牌</label>
            <n-input
              :value="token"
              type="password"
              show-password-on="click"
              placeholder="填写 NapCat WebUI 令牌"
              :disabled="actionBusy !== null || statusBusy"
              :input-props="{ id: 'napcat-token', autocomplete: 'new-password', 'aria-label': 'NapCat WebUI 令牌' }"
              @update:value="(value) => token = value"
            />
            <p class="form-hint">无通用默认值，连接成功后会从本页清除；令牌不会写入模型或 OneBot 配置。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="napcat-totp">TOTP 验证码（可选）</label>
            <n-input
              :value="totpCode"
              placeholder="实例要求时填写"
              :disabled="actionBusy !== null || statusBusy"
              :input-props="{ id: 'napcat-totp', inputmode: 'numeric', autocomplete: 'one-time-code', 'aria-label': 'TOTP 验证码' }"
              @update:value="(value) => totpCode = value"
            />
            <p class="form-hint">默认留空。仅当 NapCat WebUI 开启二次验证时使用。</p>
          </div>
        </div>
        <div class="button-row">
          <n-button type="primary" :loading="actionBusy === 'connect'" :disabled="!canConnect" @click="void connectNapcat()">连接 NapCat</n-button>
        </div>
        </details>
        <div class="button-row">
          <n-button secondary :loading="statusBusy" :disabled="!canReadStatus" @click="void loadStatus()">重新读取状态</n-button>
          <n-button secondary :loading="statusBusy" :disabled="!canRefresh" @click="void refreshNapcatStatus()">刷新二维码</n-button>
          <n-button type="error" secondary :loading="actionBusy === 'disconnect'" :disabled="!canDisconnect" @click="void disconnectNapcat()">断开管理连接</n-button>
        </div>
        <p class="form-hint" role="status">状态成功读取后会每 3 秒按顺序轮询；“刷新二维码”会主动调用 NapCat 刷新接口。关闭此页不会断开服务端管理连接。</p>
      </n-card>



      <n-card class="page-card" :bordered="false">
        <div class="napcat-log-toolbar">
          <div>
            <p class="eyebrow">本次采样</p>
            <h2>NapCat 日志</h2>
          </div>
          <div class="napcat-tags">
            <n-tag v-if="logSampling" type="success" size="small" round>{{ logBusy ? '采样中' : '采样已开启' }}</n-tag>
            <n-tag v-else type="default" size="small" round>未采样</n-tag>
            <n-button size="small" type="primary" :disabled="!canStartLogs" @click="startLogSampling">开始采样</n-button>
            <n-button size="small" secondary :disabled="!logSampling" @click="stopLogSampling">停止采样</n-button>
          </div>
        </div>
        <p class="napcat-copy">显示最近一次采样，可能包含 NapCat 缓存；采样间隙可能遗漏。每次最多读取 2 秒的输出，开始后先等待 2 秒，再按顺序每 2 秒采样一次；最多显示 200 行。</p>
        <p v-if="logDetail" class="form-hint" role="status">{{ logDetail }}</p>
        <pre class="napcat-log" aria-live="polite">{{ logText || '尚未采样日志。' }}</pre>
      </n-card>
    </template>
  </section>
</template>

<style scoped>
.napcat-view {
  min-width: 0;
}

.napcat-intro,
.napcat-copy {
  max-width: 760px;
  margin: 12px 0 0;
  color: var(--om-muted);
}

.napcat-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.napcat-status-grid {
  margin-bottom: 0;
}

.napcat-qr-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: center;
  margin-top: 16px;
}

.napcat-qr {
  display: grid;
  padding: 16px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius);
}

.napcat-log-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.napcat-log-toolbar h2 {
  margin: 0;
  color: var(--om-text);
  font-size: 19px;
  font-weight: 740;
  letter-spacing: -.02em;
}

.napcat-log {
  min-height: 180px;
  max-height: 420px;
  margin: 16px 0 0;
  padding: 16px;
  overflow: auto;
  color: var(--om-text);
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.6 "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}

@media (max-width: 700px) {
  .napcat-tags {
    justify-content: flex-start;
  }

  .napcat-qr-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .napcat-qr {
    justify-self: start;
  }

  .napcat-log-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

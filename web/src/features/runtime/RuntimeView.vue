<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NModal, NSpace, NTag } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { CoreRestartResponse, CoreRuntime, HealthResponse } from '@/api/generated'
import { bootstrapSession, expireAdminSession, sessionState } from '@/app/session'
import { hasSettingsDraftChanges, settingsState } from '@/features/settings/store'
import { hasPolicyDraftChanges, policyState } from '@/features/policy/store'
import NativeReleasePanel from './NativeReleasePanel.vue'
import QQDeliveryPanel from './QQDeliveryPanel.vue'

type RestartPhase = 'idle' | 'submitting' | 'waiting' | 'unknown'
type RecoveryResult =
  | { status: 'recovered'; health: HealthResponse }
  | { status: 'timeout'; lastError: string }
  | { status: 'cancelled' }

const runtime = ref<CoreRuntime | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const healthDetail = ref('')
const runtimeStale = ref(false)
const confirmOpen = ref(false)
const restartPhase = ref<RestartPhase>('idle')

let requestSequence = 0
let runtimeController: AbortController | undefined
let restartController: AbortController | undefined

const configDraftDirty = computed(() => hasSettingsDraftChanges() || settingsState.advancedEdited)
const policyDraftDirty = computed(() => hasPolicyDraftChanges())
const reconnectHint = computed(() => sessionState.status?.dev_web_bypass
  ? '当前开发模式会自动恢复连接。' : '恢复后需重新登录。')
const editorBusy = computed(() => Boolean(
  settingsState.loading || settingsState.saving || policyState.loading || policyState.saving,
))
const hasUncommittedWork = computed(() => configDraftDirty.value || policyDraftDirty.value)
const isRestarting = computed(() => restartPhase.value === 'submitting' || restartPhase.value === 'waiting')
const canRestart = computed(() => Boolean(
  runtime.value?.managed
  && !runtime.value.pending
  && !isRestarting.value
  && !runtimeStale.value
  && !loading.value
  && !editorBusy.value
  && !hasUncommittedWork.value
  && sessionState.adminAuthenticated,
))

function isCurrent(sequence: number, sessionGeneration: number): boolean {
  return sequence === requestSequence
    && sessionGeneration === sessionState.generation
    && sessionState.adminAuthenticated
}

function cancelPending(): void {
  requestSequence += 1
  runtimeController?.abort()
  restartController?.abort()
  runtimeController = undefined
  restartController = undefined
  loading.value = false
}

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '未记录'
  const total = Math.floor(seconds)
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (days) return `${days} 天 ${hours} 小时`
  if (hours) return `${hours} 小时 ${minutes} 分钟`
  if (minutes) return `${minutes} 分钟 ${secs} 秒`
  return `${secs} 秒`
}

function runtimeStatusLabel(value: CoreRuntime): string {
  if (isRestarting.value) return '重启处理中'
  if (runtimeStale.value) return '状态未刷新'
  if (!value.managed) return '未托管'
  if (value.pending) return '重启处理中'
  if (value.last_restart_error) return '上次重启失败'
  if (value.restart_required) return '等待应用配置'
  return '运行中'
}

function runtimeStatusType(value: CoreRuntime): 'success' | 'warning' | 'error' | 'default' {
  if (isRestarting.value) return 'warning'
  if (runtimeStale.value) return 'warning'
  if (!value.managed) return 'default'
  if (value.last_restart_error) return 'error'
  if (value.pending || value.restart_required) return 'warning'
  return 'success'
}

function runtimeErrorMessage(cause: unknown): string {
  if (!isApiError(cause)) return '请求未能确认，可能已经执行；页面不会自动重试。'
  const labels: Record<string, string> = {
    runtime_busy: '当前有在途任务，重启会被拒绝；请等待核心空闲后再试。',
    runtime_pending: '已有一次核心重启正在处理中，请等待运行状态恢复。',
    runtime_stale: '本次运行标识或配置版本已变化，请刷新运行状态后再试。',
    runtime_unmanaged: '当前实例未由此管理进程托管，不能从 Web 重启。',
    runtime_config_invalid: '已保存配置无法用于启动核心，请先修正并保存后再试。',
    runtime_start_failed: '新配置启动失败，已恢复上一运行版本；请检查配置并刷新运行状态。',
  }
  return labels[cause.code] ?? apiErrorMessage(cause)
}

function restartErrorLabel(code: string): string {
  return code === 'runtime_start_failed'
    ? '新配置启动失败，已恢复上一运行版本。'
    : '上次核心重启未完成。'
}

async function loadRuntime(): Promise<void> {
  if (!sessionState.adminAuthenticated || loading.value || isRestarting.value) return
  runtimeController?.abort()
  const controller = new AbortController()
  const sequence = ++requestSequence
  const sessionGeneration = sessionState.generation
  runtimeController = controller
  loading.value = true
  error.value = ''
  try {
    const result = await apiRequest<CoreRuntime>('/api/admin/runtime', { signal: controller.signal })
    if (!isCurrent(sequence, sessionGeneration) || controller.signal.aborted) return
    runtime.value = result
    runtimeStale.value = false
    restartPhase.value = 'idle'
    notice.value = ''
    healthDetail.value = ''
  } catch (cause: unknown) {
    if (!isCurrent(sequence, sessionGeneration) || controller.signal.aborted) return
    if (isApiError(cause) && cause.status === 401) {
      expireAdminSession()
      return
    }
    runtimeStale.value = Boolean(runtime.value)
    error.value = runtimeErrorMessage(cause)
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      if (runtimeController === controller) runtimeController = undefined
    }
  }
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new Error('aborted'))
      return
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, milliseconds)
    const abort = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      reject(new Error('aborted'))
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

function now(): number {
  return typeof performance === 'undefined' ? Date.now() : performance.now()
}

async function readHealth(signal: AbortSignal, deadline: number): Promise<HealthResponse> {
  const controller = new AbortController()
  const forwardAbort = () => controller.abort()
  const remaining = Math.max(1, Math.min(1000, deadline - now()))
  const timer = setTimeout(() => controller.abort(), remaining)
  signal.addEventListener('abort', forwardAbort, { once: true })
  try {
    return await apiRequest<HealthResponse>('/health', { signal: controller.signal })
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', forwardAbort)
  }
}

async function waitForRecovery(
  previousGeneration: string,
  sequence: number,
  sessionGeneration: number,
  signal: AbortSignal,
): Promise<RecoveryResult> {
  let lastError = ''
  const deadline = now() + 60000
  while (now() < deadline) {
    const cycleStarted = now()
    if (!isCurrent(sequence, sessionGeneration) || signal.aborted) return { status: 'cancelled' }
    try {
      const health = await readHealth(signal, deadline)
      if (!isCurrent(sequence, sessionGeneration) || signal.aborted) return { status: 'cancelled' }
      if (health.ready && health.generation !== previousGeneration) return { status: 'recovered', health }
    } catch (cause: unknown) {
      if (signal.aborted) return { status: 'cancelled' }
      lastError = apiErrorMessage(cause)
    }
    const remaining = deadline - now()
    const nextPollIn = Math.min(1000 - (now() - cycleStarted), remaining)
    if (nextPollIn > 0) {
      try {
        await wait(nextPollIn, signal)
      } catch {
        return { status: 'cancelled' }
      }
    }
  }
  return { status: 'timeout', lastError }
}

function recovered(health: HealthResponse): void {
  const development = sessionState.status?.dev_web_bypass === true
  const message = development
    ? '核心已恢复，正在自动重新连接开发控制台并核对运行版本。'
    : `核心已恢复（运行标识 ${health.generation}）。Web 会话已失效，请重新登录后核对运行版本。`
  runtimeStale.value = false
  notice.value = message
  restartPhase.value = 'idle'
  expireAdminSession()
  sessionState.authNotice = message
  // The server alone decides whether a bypass is allowed. Standard sessions
  // remain logged out; the offline development console can reconnect itself.
  void bootstrapSession()
}

async function restartCore(): Promise<void> {
  if (!canRestart.value || !runtime.value) return
  confirmOpen.value = false
  runtimeController?.abort()
  const controller = new AbortController()
  const sequence = ++requestSequence
  const sessionGeneration = sessionState.generation
  const previousGeneration = runtime.value.generation
  restartController = controller
  restartPhase.value = 'submitting'
  error.value = ''
  notice.value = ''
  healthDetail.value = ''
  runtimeStale.value = true
  try {
    const postController = new AbortController()
    const forwardAbort = () => postController.abort()
    const postTimer = setTimeout(() => postController.abort(), 10000)
    controller.signal.addEventListener('abort', forwardAbort, { once: true })
    let response: CoreRestartResponse
    try {
      response = await apiRequest<CoreRestartResponse>('/api/admin/runtime/restart', {
        method: 'POST',
        adminMutation: true,
        signal: postController.signal,
        body: { expected_revision: runtime.value.revision, generation: previousGeneration },
      })
    } finally {
      clearTimeout(postTimer)
      controller.signal.removeEventListener('abort', forwardAbort)
    }
    if (!isCurrent(sequence, sessionGeneration) || controller.signal.aborted) return
    if (!response.accepted) {
      restartPhase.value = 'unknown'
      error.value = '服务没有确认重启已接受，未宣称成功；请刷新运行状态核对。'
      return
    }
    restartPhase.value = 'waiting'
    notice.value = '重启请求已接受，正在等待新的运行标识就绪（最多 60 秒）。'
    const recovery = await waitForRecovery(previousGeneration, sequence, sessionGeneration, controller.signal)
    if (recovery.status === 'cancelled') return
    if (recovery.status === 'timeout') {
      restartPhase.value = 'unknown'
      error.value = '未能在 60 秒内确认核心恢复，未宣称重启成功。请重新登录或刷新后核对运行版本。'
      healthDetail.value = recovery.lastError ? `最后一次健康检查：${recovery.lastError}` : ''
      return
    }
    recovered(recovery.health)
  } catch (cause: unknown) {
    if (!isCurrent(sequence, sessionGeneration) || controller.signal.aborted) return
    if (isApiError(cause)) {
      if (cause.status === 401) expireAdminSession()
      else {
        if (cause.code === 'runtime_stale' || cause.code === 'runtime_pending') runtimeStale.value = true
        restartPhase.value = 'idle'
        error.value = runtimeErrorMessage(cause)
      }
      return
    }
    restartPhase.value = 'waiting'
    notice.value = '重启请求未确认，正在仅检查健康状态；不会自动重试请求。'
    const recovery = await waitForRecovery(previousGeneration, sequence, sessionGeneration, controller.signal)
    if (recovery.status === 'cancelled') return
    if (recovery.status === 'recovered') {
      recovered(recovery.health)
      return
    }
    restartPhase.value = 'unknown'
    runtimeStale.value = true
    error.value = '重启请求结果未知，未宣称重启成功。请重新登录或刷新后核对运行版本。'
    healthDetail.value = recovery.lastError ? `最后一次健康检查：${recovery.lastError}` : ''
  } finally {
    if (sequence === requestSequence && restartController === controller) restartController = undefined
  }
}

function requestRestart(): void {
  if (canRestart.value) confirmOpen.value = true
}

watch(() => sessionState.generation, () => {
  cancelPending()
  runtime.value = null
  runtimeStale.value = false
  restartPhase.value = 'idle'
  if (sessionState.adminAuthenticated) void loadRuntime()
})

onMounted(() => {
  void loadRuntime()
})

onBeforeUnmount(() => {
  cancelPending()
})
</script>

<template>
  <section class="runtime-view" aria-labelledby="runtime-heading">
    <div class="runtime-toolbar">
      <div>
        <h2 id="runtime-heading" class="runtime-kicker">核心运行管理</h2>
        <p class="runtime-sync" aria-live="polite">
          {{ isRestarting ? '正在等待核心恢复…' : '读取当前核心运行标识与配置版本' }}
        </p>
      </div>
      <n-space align="center" size="small">
        <n-tag v-if="runtime" :type="runtimeStatusType(runtime)" size="small">{{ runtimeStatusLabel(runtime) }}
        </n-tag>
        <n-button secondary :loading="loading" :disabled="!sessionState.adminAuthenticated || loading || isRestarting" @click="void loadRuntime()">
          刷新运行状态
        </n-button>
      </n-space>
    </div>

    <n-alert v-if="error" class="notice" type="error" :show-icon="true">
      {{ error }}
      <span v-if="healthDetail" class="notice-detail">{{ healthDetail }}</span>
    </n-alert>
    <n-alert v-if="notice" class="notice" type="success" :show-icon="true">
      {{ notice }}
    </n-alert>
    <n-alert v-if="runtime && runtimeStale && !isRestarting" class="notice" type="warning" :show-icon="true">
      当前显示上一次成功读取的运行快照，可能已经过期；重启操作已禁用，请先成功刷新运行状态。
    </n-alert>

    <n-card v-if="loading && !runtime" class="page-card empty-card" :bordered="false">
      <n-empty description="正在读取核心运行状态…" />
    </n-card>
    <n-card v-else-if="!runtime" class="page-card empty-card" :bordered="false">
      <n-empty description="暂时没有可用的核心运行状态">
        <template #extra>
          <n-button secondary :disabled="!sessionState.adminAuthenticated" @click="void loadRuntime()">重新读取</n-button>
        </template>
      </n-empty>
    </n-card>

    <template v-else>
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">当前实例</p>
            <h2>运行快照</h2>
            <p class="subtle-text">核心会重新加载并应用配置；NapCat 容器保持不动。</p>
          </div>
          <n-tag :type="runtimeStatusType(runtime)" size="small">{{ runtimeStatusLabel(runtime) }}
          </n-tag>
        </div>
        <dl class="detail-list">
          <div>
            <dt>实例 ID</dt>
            <dd><code>{{ runtime.instance_id }}</code></dd>
          </div>
          <div>
            <dt>本次运行标识</dt>
            <dd><code>{{ runtime.generation }}</code></dd>
          </div>
          <div>
            <dt>运行时长</dt>
            <dd>{{ formatUptime(runtime.uptime_seconds) }}</dd>
          </div>
          <div>
            <dt>活跃会话 / 队列</dt>
            <dd>{{ runtime.active_sessions }} / {{ runtime.queue_depth }}</dd>
          </div>
          <div>
            <dt>已保存配置版本</dt>
            <dd><code>{{ runtime.revision }}</code></dd>
          </div>
          <div>
            <dt>运行配置版本</dt>
            <dd><code>{{ runtime.effective_revision }}</code></dd>
          </div>
        </dl>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">保存版本 → 运行版本</p>
            <h2>重启核心</h2>
            <p class="subtle-text">重启只应用已保存配置。{{ reconnectHint }}</p>
          </div>
          <n-tag v-if="runtime.restart_required" type="warning" size="small">有待重启配置
          </n-tag>
        </div>

        <n-alert v-if="!runtime.managed" class="notice" type="warning" :show-icon="true">
          当前实例没有由本 Web 管理的核心重启控制器，不能从此处重启。
        </n-alert>
        <n-alert v-else-if="runtime.pending" class="notice" type="info" :show-icon="true">
          已有核心重启正在处理中。请等待运行标识变化后刷新状态，不会重复提交。
        </n-alert>
        <n-alert v-if="runtime.restart_required" class="notice" type="info" :show-icon="true">
          已保存版本 {{ runtime.revision }} 尚未进入运行版本 {{ runtime.effective_revision }}；重启会尝试应用已保存版本。
        </n-alert>
        <n-alert v-if="runtime.last_restart_error" class="notice" type="error" :show-icon="true">
          {{ restartErrorLabel(runtime.last_restart_error) }} <code>{{ runtime.last_restart_error }}</code> 请核对已保存配置后刷新状态。
        </n-alert>
        <n-alert v-if="hasUncommittedWork" class="notice" type="warning" :show-icon="true">
          当前有未保存的模型/权限草稿或未应用的高级 JSON。为避免重启使 Web 会话失效时丢失草稿，重启已禁用；未保存草稿不会应用，请先保存或放弃草稿。
          <div class="runtime-links">
            <router-link v-if="configDraftDirty" class="runtime-entry" to="/settings">前往模型配置</router-link>
            <router-link v-if="policyDraftDirty" class="runtime-entry" to="/policy">前往权限策略</router-link>
          </div>
        </n-alert>
        <n-alert v-if="editorBusy" class="notice" type="info" :show-icon="true">
          配置或权限正在读取/保存，完成后才能重启核心。
        </n-alert>

        <div class="runtime-actions">
          <div>
            <strong>重启前请确认</strong>
            <p class="subtle-text">忙时服务会拒绝重启，不会中断在途任务；重启成功会清空核心内存中的短期会话历史，NapCat 容器不受影响。</p>
          </div>
          <n-button type="warning" :loading="isRestarting" :disabled="!canRestart" @click="requestRestart">
            {{ restartPhase === 'waiting' ? '等待核心恢复…' : runtime.restart_required ? '应用已保存配置并重启' : '重启核心' }}
          </n-button>
        </div>
      </n-card>
    </template>

    <QQDeliveryPanel />
    <NativeReleasePanel />

    <n-modal v-model:show="confirmOpen" preset="dialog" title="确认重启核心" positive-text="确认重启" negative-text="取消" :positive-button-props="{ disabled: !canRestart }" @positive-click="restartCore">
      <p>这会只应用已保存的配置并重新加载核心。内存短期会话历史会清空，NapCat 容器不会重启。{{ reconnectHint }}</p>
      <p v-if="runtime?.restart_required">当前已保存版本为 {{ runtime.revision }}，运行版本为 {{ runtime.effective_revision }}。</p>
    </n-modal>
  </section>
</template>

<style scoped>
.runtime-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.runtime-kicker,
.runtime-sync {
  margin: 0;
}

.runtime-kicker {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 720;
}

.runtime-sync {
  margin-top: 4px;
  color: var(--om-muted);
  font-size: 12px;
}

.notice-detail {
  display: block;
  margin-top: 4px;
  color: var(--om-muted);
  font-size: 12px;
}

.detail-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 24px;
  margin: 16px 0;
}

.detail-list > div {
  min-width: 0;
}

.detail-list dt {
  color: var(--om-muted);
  font-size: 12px;
}

.detail-list dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  color: var(--om-text);
  font-weight: 680;
}

.runtime-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.runtime-actions > div {
  min-width: 0;
}

.runtime-actions p {
  max-width: 700px;
  margin: 4px 0 0;
}

.runtime-actions .n-button {
  flex: 0 0 auto;
}

.runtime-links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
}

.runtime-entry {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}

.runtime-entry:hover {
  text-decoration: underline;
}

.runtime-view code {
  color: var(--om-primary-dark);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.runtime-view :deep(.n-modal-body) p {
  margin: 0 0 12px;
}

.runtime-view :deep(.n-modal-body) p:last-child {
  margin-bottom: 0;
}

@media (max-width: 620px) {
  .runtime-toolbar,
  .runtime-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .runtime-actions .n-button {
    width: 100%;
  }

  .detail-list {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NTag, NText } from 'naive-ui'

import { logoutAdmin, logoutStatus, refreshStatus, sessionState } from '@/app/session'
import type { ActionStatus, StatusSnapshot, TaskBinding } from '@/api/types'

const status = computed(() => sessionState.status)
const isRefreshing = computed(() => sessionState.statusLoading)

const actionTotal = computed(() => sumCounters(status.value?.actions))
const requestTotal = computed(() => sumCounters(status.value?.requests))
const taskBindings = computed(() => {
  const bindings = status.value?.model_config.task_bindings
  if (!bindings) return []
  return Object.entries(bindings).flatMap(([task, binding]) => {
    if (!binding) return []
    return [{ task, binding }]
  })
})

function sumCounters(counters: Record<string, number> | undefined): number {
  if (!counters) return 0
  return Object.values(counters).reduce((total, count) => total + count, 0)
}

function modeLabel(mode: StatusSnapshot['mode']): string {
  return mode === 'live' ? '实时模式' : '离线隔离'
}

function connectionLabel(connection: StatusSnapshot['connection']): string {
  const labels: Record<StatusSnapshot['connection'], string> = {
    connected: '已连接',
    disconnected: '已断开',
    isolated: '隔离中',
  }
  return labels[connection] ?? connection
}

function connectionTagType(connection: StatusSnapshot['connection']): 'success' | 'warning' | 'error' | 'default' {
  if (connection === 'connected') return 'success'
  if (connection === 'disconnected') return 'error'
  return 'warning'
}

function modeTagType(mode: StatusSnapshot['mode']): 'success' | 'warning' {
  return mode === 'live' ? 'warning' : 'success'
}

function actionTagType(state: string): 'success' | 'warning' | 'error' | 'default' {
  if (['succeeded', 'complete', 'sent'].includes(state)) return 'success'
  if (['accepted', 'running', 'queued', 'pending'].includes(state)) return 'warning'
  if (['failed', 'denied', 'cancelled'].includes(state)) return 'error'
  return 'default'
}

function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    'model.invoke': '模型调用',
    'model.schedule': '日程规划',
    'model.dream': 'Dream 提案',
    'message.reply': '发送回复',
    'tool.invoke:time.now': '查询时间',
    chat: '对话',
    reply: '回复',
    thinker: '思考任务',
    delivery: '发送',
    message: '消息',
  }
  return labels[action] ?? action
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    succeeded: '已完成', failed: '失败', denied: '未获授权', unknown: '结果未知',
    queued: '排队中', dispatching: '执行中', cancelled_before_dispatch: '发送前取消',
  }
  return labels[state] ?? state
}

function taskLabel(task: string): string {
  const labels: Record<string, string> = {
    reply: '回复',
    thinker: '思考',
    vision: '视觉',
  }
  return labels[task] ?? task
}

function modelLabel(binding: TaskBinding): string {
  return `${binding.profile} · ${binding.model}`
}

function revisionLabel(value: string | number): string {
  return String(value)
}

async function handleRefresh(): Promise<void> {
  await refreshStatus()
}

async function handleStatusLogout(): Promise<void> {
  await logoutStatus()
}

async function handleAdminLogout(): Promise<void> {
  await logoutAdmin()
}

function isActionStatus(value: ActionStatus): boolean {
  return Boolean(value.request_id || value.action || value.state)
}

onMounted(() => {
  if (!sessionState.statusAuthenticated || !sessionState.status) return
  void refreshStatus()
})
</script>

<template>
  <section class="status-view" aria-labelledby="status-view-heading">
    <div class="status-toolbar">
      <div>
        <h2 id="status-view-heading" class="status-kicker">运行状态</h2>
        <p class="status-sync" aria-live="polite">
          {{ isRefreshing ? '正在刷新当前实例…' : '数据来自当前本机实例' }}
        </p>
      </div>
      <div class="status-actions">
        <n-button
          secondary
          :loading="isRefreshing"
          :disabled="!sessionState.statusAuthenticated || isRefreshing"
          aria-label="刷新运行状态"
          @click="void handleRefresh()"
        >
          刷新状态
        </n-button>
        <n-button
          v-if="sessionState.statusAuthenticated"
          quaternary
          :loading="sessionState.authBusy === 'status-logout'"
          :disabled="Boolean(sessionState.authBusy)"
          @click="void handleStatusLogout()"
        >
          退出只读
        </n-button>
        <n-button
          v-if="sessionState.adminAuthenticated"
          quaternary
          :loading="sessionState.authBusy === 'admin-logout'"
          :disabled="Boolean(sessionState.authBusy)"
          @click="void handleAdminLogout()"
        >
          退出管理员
        </n-button>
      </div>
    </div>

    <n-alert v-if="sessionState.statusError" type="error" :show-icon="true" class="page-card">
      {{ sessionState.statusError }}
    </n-alert>

    <n-card v-if="!status" class="page-card empty-card" :bordered="false">
      <n-empty description="还没有可显示的运行状态">
        <template #extra>
          <n-text depth="3">登录只读状态后，当前实例的连接和任务信息会显示在这里。</n-text>
        </template>
      </n-empty>
    </n-card>

    <template v-else>
      <div class="metric-grid status-metrics" aria-label="运行指标">
        <n-card class="metric-card" :bordered="false">
          <div class="metric-label">队列等待</div>
          <div class="metric-value">{{ status.queue_depth }}</div>
          <div class="metric-detail">正在等待处理的请求</div>
        </n-card>
        <n-card class="metric-card" :bordered="false">
          <div class="metric-label">活动会话</div>
          <div class="metric-value">{{ status.active_sessions }}</div>
          <div class="metric-detail">当前保持的对话会话</div>
        </n-card>
        <n-card class="metric-card" :bordered="false">
          <div class="metric-label">请求总数</div>
          <div class="metric-value">{{ requestTotal }}</div>
          <div class="metric-detail">已记录的累计请求</div>
        </n-card>
        <n-card class="metric-card" :bordered="false">
          <div class="metric-label">动作总数</div>
          <div class="metric-value">{{ actionTotal }}</div>
          <div class="metric-detail">已记录的处理动作</div>
        </n-card>
      </div>

      <div class="info-grid status-info-grid">
        <n-card class="page-card status-card" :bordered="false">
          <div class="section-heading">
            <div>
              <p class="eyebrow">实例</p>
              <h2>运行实例</h2>
            </div>
            <n-tag :type="modeTagType(status.mode)" size="small" round>
              {{ modeLabel(status.mode) }}
            </n-tag>
          </div>
          <dl class="detail-list">
            <div>
              <dt>名称</dt>
              <dd>{{ status.instance.name }}</dd>
            </div>
            <div>
              <dt>实例 ID</dt>
              <dd><code>{{ status.instance.id }}</code></dd>
            </div>
            <div>
              <dt>Bot ID</dt>
              <dd><code>{{ status.instance.bot_id }}</code></dd>
            </div>
            <div>
              <dt>配置版本</dt>
              <dd><code>{{ revisionLabel(status.config_revision) }}</code></dd>
            </div>
            <div>
              <dt>策略版本</dt>
              <dd>{{ status.policy_revision }}</dd>
            </div>
          </dl>
        </n-card>

        <n-card class="page-card status-card" :bordered="false">
          <div class="section-heading">
            <div>
              <p class="eyebrow">连接</p>
              <h2>连接与运行</h2>
            </div>
            <n-tag :type="connectionTagType(status.connection)" size="small" round>
              {{ connectionLabel(status.connection) }}
            </n-tag>
          </div>
          <dl class="detail-list">
            <div>
              <dt>活动传输</dt>
              <dd>{{ status.active_transports }}</dd>
            </div>
            <div>
              <dt>发送 API</dt>
              <dd>{{ status.onebot_api_ready ? '反向 WS 已就绪' : '未验证' }}</dd>
              <p class="subtle-text">只验证接口和账号身份，真实发送仍受权限与运行模式控制</p>
            </div>
            <div>
              <dt>传输故障</dt>
              <dd>{{ status.transport_fault ? '需要关注' : '无' }}</dd>
            </div>
            <div>
              <dt>运行错误</dt>
              <dd>{{ status.runtime_errors }}</dd>
            </div>
            <div>
              <dt>存储错误</dt>
              <dd>{{ status.storage_errors }}</dd>
            </div>
            <div v-if="status.last_error" class="detail-wide detail-error">
              <dt>最近错误</dt>
              <dd>{{ status.last_error }}</dd>
            </div>
          </dl>
        </n-card>
      </div>

      <n-card class="page-card status-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">任务</p>
            <h2>模型与任务</h2>
          </div>
          <n-tag v-if="status.model_config.profile" type="info" size="small">
            {{ status.model_config.profile }}
          </n-tag>
        </div>
        <div class="model-summary">
          <div>
            <span class="detail-label">当前模型</span>
            <strong>{{ status.model_config.model }}</strong>
          </div>
          <div>
            <span class="detail-label">接口格式</span>
            <strong>{{ status.model_config.api_format }}</strong>
          </div>
          <div>
            <span class="detail-label">授权配置</span>
            <strong :title="status.model_config.policy_provider">{{ status.model_config.profile }}</strong>
          </div>
          <div>
            <span class="detail-label">模型并发</span>
            <strong>{{ status.model_budget.active }} 活动 / {{ status.model_budget.waiting }} 等待</strong>
          </div>
        </div>
        <ul v-if="taskBindings.length" class="task-list" aria-label="任务模型绑定">
          <li v-for="item in taskBindings" :key="item.task">
            <span>{{ taskLabel(item.task) }}</span>
            <span class="subtle">{{ modelLabel(item.binding) }}</span>
          </li>
        </ul>
        <p v-else class="empty-state">当前没有额外任务绑定。</p>
      </n-card>

      <n-card class="page-card status-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">审计</p>
            <h2>近期执行</h2>
          </div>
          <n-text depth="3">{{ status.recent.length }} 条</n-text>
        </div>
        <ol v-if="status.recent.some(isActionStatus)" class="execution-list" aria-label="近期执行记录">
          <li v-for="item in status.recent.filter(isActionStatus)" :key="item.key">
            <div class="execution-main">
              <div class="execution-title">
                <strong>{{ actionLabel(item.action) }}</strong>
                <n-tag :type="actionTagType(item.state)" size="small">
                  {{ stateLabel(item.state) }}
                </n-tag>
              </div>
              <p class="execution-meta">
                请求 <code>{{ item.request_id }}</code><span v-if="item.revision"> · 版本 {{ item.revision }}</span>
              </p>
            </div>
            <span v-if="item.code" class="execution-code">{{ item.code }}</span>
          </li>
        </ol>
        <n-empty v-else description="暂无近期执行记录" />
      </n-card>
    </template>
  </section>
</template>

<style scoped>
.status-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.status-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.status-kicker,
.status-sync {
  margin: 0;
}

.status-kicker {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 720;
}

.status-sync {
  margin-top: 2px;
  color: var(--om-muted);
  font-size: 12px;
}

.empty-card {
  padding: 18px 8px;
}

.status-card {
  min-width: 0;
}

.detail-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 18px;
  margin: 20px 0 0;
}

.detail-list > div {
  min-width: 0;
}

.detail-list dt,
.detail-label {
  color: var(--om-muted);
  font-size: 12px;
}

.detail-list dd {
  margin: 3px 0 0;
  overflow-wrap: anywhere;
  color: var(--om-text);
  font-weight: 680;
}

.detail-list code,
.execution-meta code {
  color: var(--om-primary-dark);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
}

.detail-wide {
  grid-column: 1 / -1;
}

.detail-error dd {
  color: var(--om-danger);
  font-weight: 600;
}

.model-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 20px;
}

.model-summary > div {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 12px 13px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.model-summary strong {
  overflow-wrap: anywhere;
  font-size: 14px;
}

.task-list,
.execution-list {
  display: grid;
  gap: 0;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.task-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 11px 0;
  border-top: 1px solid var(--om-border);
}

.task-list li > span:first-child {
  color: var(--om-text);
  font-weight: 700;
}

.task-list .subtle {
  overflow-wrap: anywhere;
  text-align: right;
}

.execution-list {
  border-top: 1px solid var(--om-border);
}

.execution-list li {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 13px 0;
  border-bottom: 1px solid var(--om-border);
}

.execution-main {
  min-width: 0;
}

.execution-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.execution-meta {
  margin: 4px 0 0;
  color: var(--om-muted);
  font-size: 12px;
}

.execution-code {
  max-width: 35%;
  overflow-wrap: anywhere;
  color: var(--om-muted);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  text-align: right;
}

:deep(.n-button) {
  min-height: 44px;
}

@media (max-width: 820px) {
  .model-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .status-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .status-actions {
    justify-content: flex-start;
    width: 100%;
  }

  .status-actions :deep(.n-button) {
    flex: 1 1 auto;
  }

  .detail-list,
  .model-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .task-list li,
  .execution-list li {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .task-list .subtle,
  .execution-code {
    max-width: 100%;
    text-align: left;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-view * {
    scroll-behavior: auto;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
</style>

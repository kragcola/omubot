<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NSpace, NTag, NText } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, refreshStatus, sessionState } from '@/app/session'
import type {
  AggregateHealthSnapshot,
  ContextObservationSnapshot,
  ContextPathObservation,
  ContextPathTotals,
  DiagnosticsSnapshot,
  GroupBoardView,
  ModelUsage,
  ParticipationDiagnostic,
  RequestTrace,
  TopicEdgeDiagnostic,
  TopicEdgeDiagnosticsSnapshot,
  TraceAction,
} from '@/api/generated'

const boardGroup = ref('')
const groupBoard = ref<GroupBoardView | null>(null)
const boardLoading = ref(false)
const boardError = ref('')
let boardSequence = 0
let boardController: AbortController | undefined
let boardExpiry: ReturnType<typeof setTimeout> | undefined

function clearGroupBoard(): void {
  boardSequence += 1
  boardController?.abort()
  boardController = undefined
  if (boardExpiry !== undefined) clearTimeout(boardExpiry)
  boardExpiry = undefined
  groupBoard.value = null
  boardLoading.value = false
  boardError.value = ''
}

async function loadGroupBoard(): Promise<void> {
  if (!sessionState.adminAuthenticated || !boardGroup.value.trim() || boardLoading.value) return
  clearGroupBoard()
  const group = boardGroup.value.trim()
  const sequence = boardSequence
  const epoch = currentAdminEpoch()
  const startedAt = performance.now()
  const controller = new AbortController()
  boardController = controller
  boardLoading.value = true
  try {
    const result = await apiRequest<GroupBoardView>(
      `/api/admin/diagnostics/group-state?${new URLSearchParams({ group_id: group })}`,
      { signal: controller.signal },
    )
    if (sequence !== boardSequence || controller.signal.aborted || !isCurrentAdminEpoch(epoch)
      || !sessionState.adminAuthenticated || group !== boardGroup.value.trim()) return
    if (result.expires_in_s !== null) {
      const remainingMs = result.expires_in_s * 1000 - (performance.now() - startedAt)
      if (remainingMs <= 0) {
        boardError.value = '快照内的记录已到保留期，请重新读取。'
        return
      }
      boardExpiry = setTimeout(() => {
        clearGroupBoard()
        boardError.value = '快照内的记录已到保留期，请重新读取。'
      }, remainingMs)
    }
    groupBoard.value = result
  } catch (cause: unknown) {
    if (sequence !== boardSequence || controller.signal.aborted) return
    if (isApiError(cause) && cause.status === 401) expireAdminSession()
    else boardError.value = apiErrorMessage(cause)
  } finally {
    if (sequence === boardSequence) {
      boardLoading.value = false
      if (boardController === controller) boardController = undefined
    }
  }
}

watch(boardGroup, clearGroupBoard)

const diagnostics = ref<DiagnosticsSnapshot | null>(null)
const isLoading = ref(false)
const error = ref('')
const stale = ref(false)
const topicEdges = ref<TopicEdgeDiagnostic[]>([])
const topicEdgeError = ref('')
const contextObservations = ref<ContextObservationSnapshot | null>(null)
const contextObservationError = ref('')
const contextObservationStale = ref(false)
const aggregateHealth = ref<AggregateHealthSnapshot | null>(null)
const healthError = ref('')
const healthStale = ref(false)
let requestGeneration = 0
let requestController: AbortController | undefined

function cancelPendingRequest(): void {
  requestGeneration += 1
  requestController?.abort()
  requestController = undefined
  isLoading.value = false
}

async function loadDiagnostics(): Promise<void> {
  if (!sessionState.statusAuthenticated || isLoading.value) return
  requestController?.abort()
  const controller = new AbortController()
  const generation = ++requestGeneration
  requestController = controller
  isLoading.value = true
  error.value = ''
  topicEdgeError.value = ''
  contextObservationError.value = ''
  healthError.value = ''
  const adminEpoch = currentAdminEpoch()
  const sessionGeneration = sessionState.generation
  try {
    const topicRequest = sessionState.adminAuthenticated
      ? apiRequest<TopicEdgeDiagnosticsSnapshot>('/api/admin/diagnostics/topic-edges', { signal: controller.signal })
      : Promise.resolve(null)
    const contextRequest = sessionState.adminAuthenticated
      ? apiRequest<ContextObservationSnapshot>('/api/admin/diagnostics/context-observations', { signal: controller.signal })
      : Promise.resolve(null)
    const healthRequest = sessionState.adminAuthenticated
      ? apiRequest<AggregateHealthSnapshot>('/api/admin/diagnostics/health', { signal: controller.signal })
      : Promise.resolve(null)
    const [diagnosticsResult, topicResult, contextResult, healthResult] = await Promise.allSettled([
      apiRequest<DiagnosticsSnapshot>('/api/diagnostics', { signal: controller.signal }),
      topicRequest,
      contextRequest,
      healthRequest,
    ])
    if (
      generation !== requestGeneration
      || sessionGeneration !== sessionState.generation
      || controller.signal.aborted
      || !sessionState.statusAuthenticated
    ) return

    if (diagnosticsResult.status === 'fulfilled') {
      diagnostics.value = diagnosticsResult.value
      stale.value = false
    } else {
      const cause = diagnosticsResult.reason as unknown
      if (isApiError(cause) && cause.status === 401) {
        sessionState.statusAuthenticated = false
        void refreshStatus()
      }
      error.value = apiErrorMessage(cause)
      stale.value = Boolean(diagnostics.value)
    }

    if (!isCurrentAdminEpoch(adminEpoch) || !sessionState.adminAuthenticated) return
    const adminResults = [topicResult, contextResult, healthResult]
    if (adminResults.some(result => result.status === 'rejected' && isApiError(result.reason) && result.reason.status === 401)) {
      topicEdges.value = []
      contextObservations.value = null
      contextObservationStale.value = false
      aggregateHealth.value = null
      healthStale.value = false
      expireAdminSession()
      return
    }
    if (topicResult.status === 'fulfilled') {
      topicEdges.value = topicResult.value?.topic_edges ?? []
      topicEdgeError.value = ''
    } else topicEdgeError.value = apiErrorMessage(topicResult.reason as unknown)

    if (healthResult.status === 'fulfilled') {
      aggregateHealth.value = healthResult.value
      healthStale.value = false
    } else {
      healthError.value = apiErrorMessage(healthResult.reason as unknown)
      healthStale.value = Boolean(aggregateHealth.value)
    }

    if (contextResult.status === 'fulfilled') {
      contextObservations.value = contextResult.value
      contextObservationStale.value = false
    } else {
      contextObservationError.value = apiErrorMessage(contextResult.reason as unknown)
      contextObservationStale.value = Boolean(contextObservations.value)
    }
  } catch (cause: unknown) {
    if (generation !== requestGeneration || controller.signal.aborted) return
    if (isApiError(cause) && cause.status === 401) {
      sessionState.statusAuthenticated = false
      void refreshStatus()
    }
    error.value = apiErrorMessage(cause)
    stale.value = Boolean(diagnostics.value)
  } finally {
    if (generation === requestGeneration) {
      isLoading.value = false
      if (requestController === controller) requestController = undefined
    }
  }
}

watch(
  () => [sessionState.statusAuthenticated, sessionState.adminAuthenticated, sessionState.generation] as const,
  ([authenticated]) => {
    cancelPendingRequest()
    clearGroupBoard()
    diagnostics.value = null
    topicEdges.value = []
    contextObservations.value = null
    contextObservationError.value = ''
    contextObservationStale.value = false
    aggregateHealth.value = null
    healthError.value = ''
    healthStale.value = false
    error.value = ''
    topicEdgeError.value = ''
    stale.value = false
    if (!authenticated) {
      return
    }
    void loadDiagnostics()
  },
)

onMounted(() => {
  if (sessionState.statusAuthenticated) void loadDiagnostics()
})

onBeforeUnmount(() => {
  clearGroupBoard()
  cancelPendingRequest()
})

function healthStateLabel(state: AggregateHealthSnapshot['state']): string {
  return { healthy: '已观测正常', degraded: '异常', unknown: '未知', disabled: '未启用', stopping: '停止中' }[state]
}

function healthComponentLabel(component: AggregateHealthSnapshot['components'][number]['component']): string {
  return { schedule: '日程生成', storylet: '剧情推进', memory: '记忆提取', rws_feedback: '意愿反馈', domain_revocation: '来源撤销清理', contact_hooks: '主动联系业务接入' }[component]
}

function healthReasonLabel(code: AggregateHealthSnapshot['components'][number]['code']): string {
  return { disabled: '功能未启用', no_report: '尚无实际运行报告', observed: '最近一轮已完成',
    owner_error: '最近一轮执行出错', report_failed: '最近报告包含失败', task_unavailable: '任务尚未建立',
    task_finished: '任务已结束', task_cancelled: '任务已取消', stopping: '正在停止任务' }[code]
}

const usageMetrics = computed(() => {
  const usage = diagnostics.value?.usage
  if (!usage) return []
  const reportedTokens = (value: number): string => usage.reported_calls > 0 ? formatCount(value) : '未上报'
  return [
    { label: '用量覆盖', value: `${formatCount(usage.reported_calls)} / ${formatCount(usage.model_calls)}`, detail: '已上报用量的模型动作 / 全部模型动作' },
    { label: '输入 token', value: reportedTokens(usage.input_tokens), detail: '服务商已上报输入量' },
    { label: '输出 token', value: reportedTokens(usage.output_tokens), detail: '服务商已上报输出量' },
    { label: '缓存读取 token', value: reportedTokens(usage.cached_input_tokens), detail: '已上报缓存读取量' },
    { label: '缓存写入 token', value: reportedTokens(usage.cache_write_tokens), detail: '已上报缓存写入量' },
  ]
})

const traces = computed(() => [
  ...(diagnostics.value?.traces.slice(0, 20) ?? []),
  ...(diagnostics.value?.background_traces?.slice(0, 20) ?? []),
])
const participation = computed(() => diagnostics.value?.participation?.slice(0, 64) ?? [])
const boundedTopicEdges = computed(() => topicEdges.value.slice(0, 64))
const recentContextObservations = computed(() => contextObservations.value?.recent.slice(0, 64) ?? [])
const contextRoles = ['context_main', 'temporal_trace', 'context_constrained', 'social_episode'] as const
const contextPathTotals = computed(() => contextRoles.map(role => ({
  role,
  ...contextObservations.value?.path_totals.find(item => item.role === role),
})))
const packStates = ['skip', 'empty', 'nonempty', 'omit_only', 'unknown'] as const

function observationCount(value: number | null | undefined): string {
  return value == null ? '未知' : formatCount(value)
}

function contextRoleLabel(role: ContextPathTotals['role']): string {
  return { context_main: '主上下文', temporal_trace: '时间线', context_constrained: '受限上下文', social_episode: '社交经历' }[role]
}

function contextPathLabel(path: ContextPathObservation): string {
  if (path.availability === 'missing') return '缺失'
  if (path.availability === 'unknown' || path.decision === null) return '未知'
  return { accepted: '接纳', trimmed: '裁剪', rejected: '拒绝' }[path.decision]
}

function packStateLabel(state: typeof packStates[number] | null): string {
  return state === null ? '未知' : { skip: '跳过', empty: '空包', nonempty: '非空包', omit_only: '仅省略', unknown: '未知' }[state]
}


function participationOutcomeLabel(outcome: ParticipationDiagnostic['outcome']): string {
  const labels = { force: '强触发', forbid: '禁止', gray: '灰区' }
  return labels[outcome]
}

function stageAOutcomeLabel(outcome: ParticipationDiagnostic['stage_a_outcome']): string {
  const labels = {
    not_run: '未运行',
    complete: '完成',
    hold: '暂缓',
    error: '失败',
    cancelled: '已取消',
  }
  return labels[outcome]
}

function participationModeLabel(mode: ParticipationDiagnostic['rws_mode']): string {
  const labels = { disabled: '关闭', shadow: '影子', primary: '主裁决' }
  return labels[mode]
}

function signalStatusLabel(status: ParticipationDiagnostic['signal_availability'][number]['status']): string {
  const labels = { available: '可用', missing: '缺失', disabled: '关闭' }
  return labels[status]
}

function participationScoreLabel(decision: ParticipationDiagnostic): string {
  return decision.rws_score == null ? '未评估' : decision.rws_score.toFixed(2)
}

function repeatShadowLabel(status: ParticipationDiagnostic['semantic_repeat_shadow']): string {
  const labels = {
    not_evaluated: '未评估',
    candidate: '相似候选',
    clear: '未发现',
    no_topic: '无可信话题',
    no_success_receipt: '无成功回执',
    history_denied: '历史权限不足',
    stream_skipped: '流式未评估',
    unavailable: '来源不可用',
  }
  return labels[status]
}

function topicEdgeKindLabel(kind: TopicEdgeDiagnostic['topic_edge_kind']): string {
  const labels = {
    root: '根边',
    reply: '回复边',
    bot_receipt: 'Bot 回执边',
    reframe: '重述边',
    unknown: '未知边',
    conflict: '冲突边',
    unknown_source_expired: '来源已过期',
  }
  return labels[kind]
}

function topicEdgeRouteLabel(route: TopicEdgeDiagnostic['mention_routes'][number]): string {
  const source = route.source_event_ids.length ? route.source_event_ids.join('、') : '无来源'
  const topics = route.topic_ids.length ? route.topic_ids.join('、') : '无话题'
  return `${route.target_id} → 来源 ${source} · 话题 ${topics}${route.truncated ? ' · 已截断' : ''}`
}

function formatCount(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatDuration(value: number | null): string {
  return value === null ? '未记录' : `${formatCount(value)} ms`
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    accepted: '已接收',
    running: '执行中',
    queued: '排队中',
    succeeded: '已完成',
    failed: '失败',
    denied: '未获授权',
    unknown: '结果未知',
    cancelled_before_dispatch: '发送前取消',
    superseded: '已被新轮次取代',
  }
  return (labels[state] ?? state) || '未知'
}

function stateTagType(state: string): 'success' | 'warning' | 'error' | 'default' {
  if (['succeeded', 'complete'].includes(state)) return 'success'
  if (['accepted', 'running', 'queued', 'pending'].includes(state)) return 'warning'
  if (['failed', 'denied', 'cancelled_before_dispatch'].includes(state)) return 'error'
  return 'default'
}

function taskLabel(task: string): string {
  const labels: Record<string, string> = { reply: '回复', thinker: 'Thinker', vision: '视觉', send: '发送', delivery: '发送' }
  return (labels[task] ?? task) || '未标注'
}

function usageValue(usage: ModelUsage | null, key: keyof ModelUsage): string {
  if (!usage) return '未上报'
  const value = usage[key]
  return typeof value === 'number' && Number.isFinite(value) ? formatCount(value) : '未上报'
}

function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    'model.invoke': '模型调用',
    'model.schedule': '日程规划',
    'model.dream': 'Dream 提案',
    'message.reply': '发送回复',
    'tool.invoke:time.now': '查询时间',
  }
  return (labels[action] ?? action) || '未标注动作'
}

function actionKey(action: TraceAction): string {
  return action.key || '未标注'
}

function reasonLabel(code: string): string {
  const labels: Record<string, string> = {
    accepted: '已接收',
    running: '执行中',
    queued: '排队中',
    succeeded: '已完成',
    complete: '已完成',
    sent: '已发送',
    upstream_timeout: '上游超时',
    upstream_auth: '上游认证失败',
    upstream_rate_limited: '上游限流',
    upstream_http: '上游 HTTP 错误',
    upstream_unavailable: '上游不可用',
    auth: '认证失败',
    rate_limited: '被限流',
    http: 'HTTP 错误',
    unavailable: '服务不可用',
    denied: '未获授权',
    revoked: '权限已撤销',
    invalid_protocol: '协议响应无效',
    output_limit: '输出超限',
    offline: '当前为离线模式',
    onebot_disconnected: 'OneBot 连接中断',
    onebot_not_ready: 'OneBot 尚未就绪',
    send_rejected: 'OneBot 拒绝发送',
    send_timeout: '发送超时',
    model_timeout: '模型请求超时',
    tool_timeout: '工具请求超时',
    invalid_input: '输入无效',
    stopping: '实例正在停止',
    superseded: '已被新轮次取代',
    unknown: '结果未知',
  }
  return (labels[code] ?? code) || '无原因码'
}

function requestReasonLabel(trace: RequestTrace): string {
  return !trace.code && trace.state === 'succeeded' ? reasonLabel('succeeded') : reasonLabel(trace.code)
}

function actionReasonLabel(action: TraceAction): string {
  return reasonLabel(action.code)
}
</script>

<template>
  <section class="diagnostics-view" aria-labelledby="diagnostics-heading">
    <div class="diagnostics-toolbar">
      <div>
        <h2 id="diagnostics-heading" class="diagnostics-kicker">用量诊断</h2>
        <p class="diagnostics-sync" aria-live="polite">
          {{ isLoading ? '正在读取当前实例…' : '手动刷新；不会自动高频轮询' }}
        </p>
      </div>
      <n-space align="center" size="small">
        <n-tag v-if="stale" type="warning" size="small">旧数据</n-tag>
        <n-button
          secondary
          :loading="isLoading"
          :disabled="!sessionState.statusAuthenticated || isLoading"
          aria-label="刷新用量诊断"
          @click="void loadDiagnostics()"
        >
          刷新诊断
        </n-button>
      </n-space>
    </div>

    <n-card v-if="sessionState.adminAuthenticated" class="page-card" title="群短期状态" :bordered="false">
      <n-space align="center">
        <n-input v-model:value="boardGroup" placeholder="群 ID" aria-label="群状态的群 ID" />
        <n-button :loading="boardLoading" :disabled="!boardGroup.trim() || boardLoading" @click="void loadGroupBoard()">读取群状态</n-button>
      </n-space>
      <n-alert v-if="boardError" type="warning" class="page-card" role="alert">{{ boardError }}</n-alert>
      <template v-if="groupBoard">
        <p>保留期 {{ groupBoard.retention_seconds }} 秒；当前保留 {{ groupBoard.retained_count }} 条，显示最近 {{ groupBoard.timeline.length }} 条。
          {{ groupBoard.window_truncated ? '窗口已截断。' : '' }}此窗口不代表全群历史。</p>
        <p>窗口内近 {{ groupBoard.message_frequency.window_seconds }} 秒：{{ groupBoard.message_frequency.count }} 条用户消息（{{ groupBoard.message_frequency.label }}）。</p>
        <p>最近活跃用户：{{ groupBoard.active_users.join('、') || '暂无' }}</p>
        <p>最近词面：{{ groupBoard.recent_topics.join('、') || '暂无' }}；词面仅来自获准的纯文字。</p>
        <p>最近提及 Bot：{{ groupBoard.recent_mentions.map(item => `${item.author_id}：${item.count} 次`).join('、') || '暂无' }}</p>
        <table class="data-table">
          <caption>已授权记录的来源和话题归属，不显示正文</caption>
          <thead><tr><th scope="col">作者 / 类型</th><th scope="col">消息 / 事件</th><th scope="col">话题</th><th scope="col">距读取时间</th></tr></thead>
          <tbody><tr v-for="(item, index) in groupBoard.timeline" :key="index">
            <td>{{ item.author_id }} / {{ item.role }}</td>
            <td>{{ item.message_id || '无消息 ID' }} / {{ item.event_id || '无事件 ID' }}</td>
            <td>{{ item.topic_id || '未归属' }} / {{ item.topic_edge_kind }}</td>
            <td>{{ item.age_s.toFixed(1) }} 秒前</td>
          </tr></tbody>
        </table>
      </template>
    </n-card>

    <n-card v-if="sessionState.adminAuthenticated" title="后台健康快照" size="small">
      <n-text depth="3">沿当前任务和最近实际报告读取；离线、未运行或未观测不代表外部服务健康。主动联系业务接入仅报告提交后的本地交接，不代表模型或收件效果。</n-text>
      <n-alert v-if="healthError" type="warning" :show-icon="false">{{ healthError }}{{ healthStale ? '；保留的是上次快照。' : '' }}</n-alert>
      <template v-if="aggregateHealth">
        <p>{{ healthStateLabel(aggregateHealth.state) }} · 读取时间 {{ aggregateHealth.observed_at }}</p>
        <div class="metric-grid">
          <div v-for="component in aggregateHealth.components" :key="component.component" class="metric-card">
            <strong>{{ healthComponentLabel(component.component) }}</strong>
            <p>{{ healthStateLabel(component.state) }} · {{ healthReasonLabel(component.code) }}</p>
            <n-text depth="3">{{ component.report_present ? '已有实际运行记录' : '尚无实际运行记录' }}<template v-if="component.failure_count != null"> · {{ component.component === 'contact_hooks' ? '累计交接失败数' : '报告失败数' }} {{ component.failure_count }}</template></n-text>
          </div>
        </div>
      </template>
      <n-empty v-else description="尚未读取健康快照" />
    </n-card>

    <n-alert v-if="error" type="error" :show-icon="true" class="page-card" role="alert">
      {{ error }}
      <span v-if="diagnostics" class="diagnostics-error-detail">上一次成功读取仍保留，并已标记为旧数据。</span>
    </n-alert>

    <n-card v-if="!sessionState.statusAuthenticated" class="page-card empty-card" :bordered="false">
      <n-empty description="登录只读状态后查看用量诊断">
        <template #extra>
          <n-text depth="3">诊断只读使用状态会话，不会显示消息正文或模型密钥。</n-text>
        </template>
      </n-empty>
    </n-card>

    <n-card v-else-if="isLoading && !diagnostics" class="page-card empty-card" :bordered="false">
      <n-empty description="正在读取用量诊断…" />
    </n-card>

    <template v-else-if="diagnostics">
      <div class="metric-grid diagnostics-metrics" aria-label="累计用量">
        <n-card v-for="metric in usageMetrics" :key="metric.label" class="metric-card" :bordered="false">
          <div class="metric-label">{{ metric.label }}</div>
          <div class="metric-value">{{ metric.value }}</div>
          <div class="metric-detail">{{ metric.detail }}</div>
        </n-card>
      </div>

      <n-card class="page-card diagnostics-note" :bordered="false">
        <p>累计 token 只汇总服务商明确返回的 usage。单次调用缺失 usage 时显示“未上报”，不会按 0 计入单次详情，也不会估算费用。</p>
      </n-card>

      <n-card v-if="sessionState.adminAuthenticated" class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">请求上下文观测</p>
            <h2>上下文计划与组包</h2>
            <p class="subtle-text">最多保留 64 次实际请求（含工具续答）的计数摘要；未知不计为零，计数不代表回复质量。</p>
          </div>
          <n-tag v-if="contextObservationStale" type="warning" size="small">旧观测数据</n-tag>
        </div>
        <n-alert v-if="contextObservationError" type="error" :show-icon="true" class="topic-edge-error" role="alert">
          上下文观测暂时无法读取：{{ contextObservationError }}
          <span v-if="contextObservations">上一次读取已标记为旧数据。</span>
        </n-alert>
        <n-empty v-if="contextObservations && !contextObservations.enabled" description="请求上下文观测未开启">
          <template #extra><n-text depth="3">需要在模型配置中开启并应用运行配置；关闭状态不会采集样本。</n-text></template>
        </n-empty>
        <template v-else-if="contextObservations?.enabled">
          <p class="subtle-text">保留样本 {{ observationCount(contextObservations.sample_count) }} 次；仅汇总当前保留窗口。</p>
          <div class="participation-signals" aria-label="组包状态计数">
            <span v-for="state in packStates" :key="state">{{ packStateLabel(state) }}：{{ observationCount(contextObservations.pack_state_counts[state]) }}</span>
          </div>
          <div class="context-table-wrap">
            <table class="context-table" aria-label="四条上下文路径计数">
              <thead><tr><th scope="col">路径</th><th scope="col">接纳</th><th scope="col">裁剪</th><th scope="col">拒绝</th><th scope="col">缺失</th><th scope="col">未知</th></tr></thead>
              <tbody><tr v-for="path in contextPathTotals" :key="path.role">
                <th scope="row">{{ contextRoleLabel(path.role) }}</th>
                <td>{{ observationCount(path.accepted) }}</td><td>{{ observationCount(path.trimmed) }}</td>
                <td>{{ observationCount(path.rejected) }}</td><td>{{ observationCount(path.missing) }}</td><td>{{ observationCount(path.unknown) }}</td>
              </tr></tbody>
            </table>
          </div>
          <div v-if="recentContextObservations.length" class="trace-list" aria-label="最近请求上下文摘要">
            <details v-for="(observation, index) in recentContextObservations" :key="index" class="trace-item">
              <summary>样本 {{ index + 1 }} · {{ packStateLabel(observation.pack?.state ?? null) }} · 最终预算{{ observation.budget.outcome === 'accepted' ? '接纳' : '拒绝' }}</summary>
              <dl class="trace-meta context-detail">
                <template v-if="observation.plan">
                  <div><dt>计划模式 / profile</dt><dd>{{ observation.plan.mode }} / {{ observation.plan.profile }}</dd></div>
                  <div><dt>计划预算</dt><dd>{{ observationCount(observation.plan.budget) }}</dd></div>
                  <div><dt>计划开关 / 身份需求</dt><dd>{{ observation.plan.enabled ? '开启' : '关闭' }} / {{ observation.plan.identity ? '需要' : '不需要' }}</dd></div>
                  <div><dt>类型上限（事实 / 卡片 / 文档 / 图）</dt><dd>{{ observationCount(observation.plan.caps.memory_fact) }} / {{ observationCount(observation.plan.caps.memory_card) }} / {{ observationCount(observation.plan.caps.document) }} / {{ observationCount(observation.plan.caps.graph_fact) }}</dd></div>
                  <div><dt>桶预算（记忆 / 文档 / 图）</dt><dd>{{ observationCount(observation.plan.buckets.memory) }} / {{ observationCount(observation.plan.buckets.doc) }} / {{ observationCount(observation.plan.buckets.graph) }}</dd></div>
                </template>
                <div v-else><dt>上下文计划</dt><dd>未知</dd></div>
                <template v-if="observation.pack">
                  <div><dt>热 / 冷 / 卡片条数</dt><dd>{{ observationCount(observation.pack.hot_count) }} / {{ observationCount(observation.pack.cold_count) }} / {{ observationCount(observation.pack.card_count) }}</dd></div>
                  <div><dt>文档 / 图 / 时间线条数</dt><dd>{{ observationCount(observation.pack.document_count) }} / {{ observationCount(observation.pack.graph_count) }} / {{ observationCount(observation.pack.temporal_count) }}</dd></div>
                  <div><dt>组包已用 / 总预算 / 冷预算</dt><dd>{{ observationCount(observation.pack.used_budget) }} / {{ observationCount(observation.pack.total_budget) }} / {{ observationCount(observation.pack.cold_used_budget) }}</dd></div>
                </template>
                <div v-else><dt>上下文组包</dt><dd>未知</dd></div>
                <div><dt>构建 / 最终 / 字符上限</dt><dd>{{ observationCount(observation.budget.built_characters) }} / {{ observationCount(observation.budget.final_characters) }} / {{ observationCount(observation.budget.character_limit) }}</dd></div>
                <div><dt>移除消息数</dt><dd>{{ observationCount(observation.budget.removed_messages) }}</dd></div>
              </dl>
              <div class="participation-signals context-detail" aria-label="单次上下文路径摘要">
                <span v-for="path in observation.paths" :key="path.role">{{ contextRoleLabel(path.role) }}：{{ contextPathLabel(path) }} · {{ observationCount(path.item_count) }} 条</span>
              </div>
            </details>
          </div>
          <n-empty v-else description="观测已开启，尚无保留样本" />
        </template>
        <n-empty v-else-if="!contextObservationError" description="尚未读取请求上下文观测" />
      </n-card>

      <n-card v-if="sessionState.adminAuthenticated" class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">话题边诊断</p>
            <h2>最近 {{ boundedTopicEdges.length }} 条话题边</h2>
            <p class="subtle-text">管理员只读视图显示同块/异块归属、来源父边、真实 @ 目标和 Bot 参与；不包含正文、提示词或密钥。</p>
          </div>
          <n-tag type="warning" size="small">管理员会话 · 最多 64 条</n-tag>
        </div>
        <n-alert v-if="topicEdgeError" type="error" :show-icon="true" class="topic-edge-error" role="alert">
          话题边暂时无法读取：{{ topicEdgeError }}
        </n-alert>
        <div v-if="boundedTopicEdges.length" class="topic-edge-list" aria-label="话题边诊断">
          <article
            v-for="edge in boundedTopicEdges"
            :key="`${edge.instance_id}:${edge.bot_id}:${edge.group_id}:${edge.event_id}`"
            class="topic-edge-item"
          >
            <div class="participation-heading">
              <strong>{{ edge.group_id }} · {{ edge.event_id }}</strong>
              <n-space size="small">
                <n-tag size="small">{{ topicEdgeKindLabel(edge.topic_edge_kind) }}</n-tag>
                <n-tag :type="edge.bot_involved ? 'success' : 'default'" size="small">
                  {{ edge.bot_involved ? 'Bot 已参与' : 'Bot 未参与' }}
                </n-tag>
              </n-space>
            </div>
            <dl class="trace-meta">
              <div><dt>实例 / Bot</dt><dd>{{ edge.instance_id }} / {{ edge.bot_id }}</dd></div>
              <div><dt>来源作者</dt><dd>{{ edge.author_id }}</dd></div>
              <div><dt>消息 ID</dt><dd>{{ edge.message_id || '无' }}</dd></div>
              <div><dt>话题 / 父边</dt><dd>{{ edge.topic_id ?? '无' }} / {{ edge.topic_parent_event_id ?? '无' }}</dd></div>
            </dl>
            <div class="topic-edge-targets" aria-label="真实 @ 目标">
              <strong>真实 @ 目标</strong>
              <span>{{ edge.mention_targets.length ? edge.mention_targets.join('、') : '无' }}</span>
            </div>
            <div v-if="edge.mention_routes.length" class="topic-edge-routes" aria-label="@ 来源路由">
              <strong>@ 来源路由</strong>
              <span v-for="route in edge.mention_routes" :key="`${route.target_id}:${route.source_event_ids.join(',')}`">
                {{ topicEdgeRouteLabel(route) }}
              </span>
            </div>
          </article>
        </div>
        <n-empty v-else description="暂无话题边诊断记录" />
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">参与裁决</p>
            <h2>最近 {{ participation.length }} 条决定</h2>
            <p class="subtle-text">只显示身份与规则摘要；决策分数不是概率，不包含消息正文、提示词或思维内容。</p>
          </div>
          <n-tag type="info" size="small">最多 64 条</n-tag>
        </div>
        <div v-if="participation.length" class="participation-list" aria-label="最近参与裁决">
          <article
            v-for="decision in participation"
            :key="`${decision.instance_id}:${decision.bot_id}:${decision.group_id}:${decision.event_id}`"
            class="participation-item"
          >
            <div class="participation-heading">
              <strong>{{ decision.group_id }} · {{ decision.event_id }}</strong>
              <n-tag :type="decision.outcome === 'force' ? 'success' : decision.outcome === 'forbid' ? 'error' : 'default'" size="small">
                {{ participationOutcomeLabel(decision.outcome) }}
              </n-tag>
            </div>
            <dl class="trace-meta">
              <div><dt>实例 / Bot</dt><dd>{{ decision.instance_id }} / {{ decision.bot_id }}</dd></div>
              <div><dt>原因</dt><dd><code>{{ decision.reason }}</code></dd></div>
              <div><dt>阶段 A</dt><dd>{{ stageAOutcomeLabel(decision.stage_a_outcome) }}</dd></div>
              <div v-if="decision.history_denied"><dt>历史上下文</dt><dd><code>history_denied</code></dd></div>
              <div><dt>RWS</dt><dd>{{ participationModeLabel(decision.rws_mode) }} · {{ participationScoreLabel(decision) }} / {{ decision.rws_threshold.toFixed(2) }}</dd></div>
              <div><dt>重复候选（仅观察）</dt><dd>{{ repeatShadowLabel(decision.semantic_repeat_shadow) }}<span v-if="decision.semantic_repeat_score != null"> · {{ decision.semantic_repeat_score.toFixed(2) }}</span></dd></div>
              <div><dt>熔断</dt><dd>{{ decision.loop_fuse_reason ?? '无' }}</dd></div>
            </dl>
            <div class="participation-signals" aria-label="RWS 信号可用性">
              <span v-for="signal in decision.signal_availability" :key="signal.name">
                {{ signal.name }}：{{ signalStatusLabel(signal.status) }}
              </span>
            </div>
          </article>
        </div>
        <n-empty v-else description="暂无参与裁决记录" />
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">请求追踪</p>
            <h2>最近对话与后台动作（{{ traces.length }} 组）</h2>
            <p class="subtle-text">每条请求可展开查看受控动作；页面不读取消息正文、提示词或密钥。</p>
          </div>
          <n-tag type="info" size="small">对话 / 后台各 20 组</n-tag>
        </div>

        <div v-if="traces.length" class="trace-list" aria-label="最近请求列表">
          <details v-for="trace in traces" :key="`${trace.origin ?? 'conversation'}:${trace.request_id}`" class="trace-item">
            <summary class="trace-summary">
              <span class="trace-main">
                <strong>{{ trace.request_id }}</strong>
                <n-tag :type="stateTagType(trace.state)" size="small">
                  {{ stateLabel(trace.state) }}
                </n-tag>
              </span>
              <span class="trace-reason">
                <span>{{ requestReasonLabel(trace) }}</span>
                <code v-if="trace.code">{{ trace.code }}</code>
              </span>
            </summary>
            <div class="trace-body">
              <dl class="trace-meta">
                <div>
                  <dt>状态</dt>
                  <dd>{{ stateLabel(trace.state) }}</dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{{ trace.origin === 'background' ? '后台模型动作组' : '对话请求' }}</dd>
                </div>
                <div>
                  <dt>原因</dt>
                  <dd>
                    <span>{{ requestReasonLabel(trace) }}</span>
                    <code v-if="trace.code" class="reason-code">{{ trace.code }}</code>
                  </dd>
                </div>
                <div>
                  <dt>动作数</dt>
                  <dd>{{ trace.actions.length }}</dd>
                </div>
              </dl>
              <div v-if="trace.actions.length" class="action-list" aria-label="请求动作">
                <div v-for="action in trace.actions" :key="action.key" class="action-card">
                  <div class="action-heading">
                    <strong>{{ actionLabel(action.action) }}</strong>
                    <n-tag :type="stateTagType(action.state)" size="small">
                      {{ stateLabel(action.state) }}
                    </n-tag>
                  </div>
                  <dl class="action-meta">
                    <div>
                      <dt>任务</dt>
                      <dd>{{ taskLabel(action.task) }}</dd>
                    </div>
                    <div>
                      <dt>动作ID</dt>
                      <dd>{{ actionKey(action) }}</dd>
                    </div>
                    <div v-if="action.action.startsWith('model.')">
                      <dt>调用模型</dt>
                      <dd>{{ action.model ?? '未记录' }}</dd>
                    </div>
                    <div v-if="action.action.startsWith('model.')">
                      <dt>配置与目标标识</dt>
                      <dd>{{ action.provider ?? '未记录' }}</dd>
                    </div>
                    <div>
                      <dt>状态</dt>
                      <dd>{{ stateLabel(action.state) }}</dd>
                    </div>
                    <div>
                      <dt>原因</dt>
                      <dd>
                        <span>{{ actionReasonLabel(action) }}</span>
                        <code v-if="action.code" class="reason-code">{{ action.code }}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>执行耗时</dt>
                      <dd>{{ formatDuration(action.elapsed_ms) }}</dd>
                    </div>
                    <div v-if="action.action === 'model.invoke'">
                      <dt>首个非空文本增量耗时</dt>
                      <dd>{{ formatDuration(action.first_delta_ms) }}</dd>
                    </div>
                  </dl>
                  <div class="action-usage" aria-label="动作 token 用量">
                    <span>输入 {{ usageValue(action.usage, 'input_tokens') }}</span>
                    <span>输出 {{ usageValue(action.usage, 'output_tokens') }}</span>
                    <span>缓存读取 {{ usageValue(action.usage, 'cached_input_tokens') }}</span>
                    <span>缓存写入 {{ usageValue(action.usage, 'cache_write_tokens') }}</span>
                  </div>
                </div>
              </div>
              <n-empty v-else description="这条请求没有记录动作" :show-icon="false" />
            </div>
          </details>
        </div>
        <n-empty v-else description="暂无请求诊断记录" />
      </n-card>
    </template>
  </section>
</template>

<style scoped>
.context-table-wrap { overflow-x: auto; margin-top: 16px; }
.context-table { width: 100%; border-collapse: collapse; text-align: left; }
.context-table th, .context-table td { padding: 8px; border-bottom: 1px solid var(--om-border); }
.context-detail { margin-top: 16px; }

.diagnostics-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.diagnostics-kicker,
.diagnostics-sync {
  margin: 0;
}

.diagnostics-kicker {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 720;
}

.diagnostics-sync {
  margin-top: 4px;
  color: var(--om-muted);
  font-size: 12px;
}

.empty-card {
  padding: 16px 8px;
}

.diagnostics-error-detail {
  display: block;
  margin-top: 4px;
  color: var(--om-muted);
  font-size: 12px;
}

.diagnostics-note p {
  margin: 0;
  color: var(--om-muted);
  font-size: 13px;
}

.participation-list {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}

.participation-item {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 12px 16px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.participation-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.participation-heading strong {
  overflow-wrap: anywhere;
}

.participation-signals {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: var(--om-muted);
  font-size: 12px;
}

.topic-edge-error {
  margin-top: 16px;
}

.topic-edge-list {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}

.topic-edge-item {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 12px 16px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.topic-edge-targets,
.topic-edge-routes {
  display: grid;
  gap: 4px;
  color: var(--om-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.topic-edge-targets strong,
.topic-edge-routes strong {
  color: var(--om-text);
}

.topic-edge-routes span + span {
  padding-top: 2px;
}

.trace-list {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}

.trace-item {
  min-width: 0;
  padding: 12px 16px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.trace-item[open] {
  background: var(--om-surface);
}

.trace-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
  cursor: pointer;
  list-style-position: inside;
}

.trace-main,
.action-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.trace-main strong,
.trace-reason,
.action-heading strong {
  overflow-wrap: anywhere;
}

.trace-reason {
  display: grid;
  gap: 4px;
  max-width: 44%;
  color: var(--om-muted);
  font-size: 12px;
  text-align: right;
}

.trace-reason code,
.reason-code {
  color: var(--om-muted);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  font-weight: 500;
}

.reason-code {
  display: block;
  margin-top: 4px;
}

.trace-body {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.trace-meta,
.action-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}

.trace-meta > div,
.action-meta > div {
  min-width: 0;
}

.trace-meta dt,
.action-meta dt {
  color: var(--om-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
}

.trace-meta dd,
.action-meta dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  color: var(--om-text);
  font-size: 13px;
  font-weight: 650;
}

.action-list {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}

.action-card {
  min-width: 0;
  padding: 12px;
  background: var(--om-surface);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.action-meta {
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin-top: 12px;
}

.action-usage {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
  color: var(--om-muted);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 11px;
}

@media (max-width: 700px) {
  .diagnostics-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .diagnostics-toolbar .n-space {
    width: 100%;
  }

  .diagnostics-toolbar .n-button {
    width: 100%;
  }

  .trace-summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .trace-reason {
    max-width: 100%;
    text-align: left;
  }

  .trace-meta,
  .action-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 420px) {
  .trace-meta,
  .action-meta {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

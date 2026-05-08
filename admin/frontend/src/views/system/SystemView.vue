<script setup lang="ts">
import {
  ArchiveOutline,
  FlashOutline,
  GitNetworkOutline,
  HardwareChipOutline,
  PulseOutline,
  RefreshOutline,
  ShieldCheckmarkOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'

interface HealthInfo {
  bot: string
  napcat: string
  uptime_seconds: number
}

interface SystemInfo {
  cpu_percent?: number | null
  active_sessions?: number | null
  restart_notice?: RestartNotice
  memory?: {
    total_gb: number
    used_gb: number
    percent: number
  }
  disk?: {
    total_gb: number
    used_gb: number
    percent: number
  }
  process?: {
    pid: number
    memory_mb: number
    threads: number
  }
}

interface VersionInfo {
  version: string
  summary?: string
  latest_tag?: string
  latest_name?: string
  latest_url?: string
  has_update?: boolean
}

interface HumanizerInfo {
  enabled: boolean
  min_delay?: number
  max_delay?: number
  char_delay?: number
}

interface TalkScheduleInfo {
  time_multiplier: number
}

interface ProviderProfile {
  name: string
  active: boolean
  api_format: string
  base_url: string
  model: string
  max_tokens?: number
  capabilities?: string[]
  api_key_mask?: string
  api_key_present?: boolean
  provider_kind?: string
  provider_mode?: string
  last_cache_hit_pct?: number | null
  last_prompt_cache_hit_tokens?: number
  last_prompt_cache_miss_tokens?: number
  last_reasoning_replay_tokens?: number
  last_payload_sanitized?: boolean
  last_usage?: Record<string, any>
  rate_limit?: ProviderRateLimit
}

interface ProviderOption {
  value: string
  label: string
}

interface ProviderRateLimit {
  profile: string
  status: 'ready' | 'cooldown' | string
  cooldown_remaining_seconds: number
  total_calls: number
  successes: number
  failures: number
  rate_limited: number
  blocked_calls: number
  consecutive_rate_limits: number
  last_task: string
  last_error: string
  last_success_at: number
  last_limited_at: number
  provider_kind?: string
  provider_mode?: string
  last_model?: string
  last_api_format?: string
  last_cache_hit_pct?: number | null
  last_prompt_cache_hit_tokens?: number
  last_prompt_cache_miss_tokens?: number
  last_reasoning_replay_tokens?: number
  last_payload_sanitized?: boolean
  last_usage?: Record<string, any>
}

type ProviderTaskKey = 'main' | 'thinker' | 'compact' | 'slang' | 'vision'

interface ProviderTaskProfile {
  task: ProviderTaskKey | string
  profile: string
  model: string
  api_format: string
}

interface ProvidersInfo {
  default_profile: string
  task_profiles?: ProviderTaskProfile[]
  profiles: ProviderProfile[]
  capability_options?: ProviderOption[]
  api_format_options?: ProviderOption[]
  rate_limits?: {
    profiles?: Record<string, ProviderRateLimit>
    tasks?: Record<string, ProviderRateLimit>
  }
}

type ProviderApiKeyMode = 'keep' | 'replace' | 'clear'

interface ProviderProfileDraft {
  name: string
  api_format: string
  base_url: string
  model: string
  max_tokens: number | null
  capabilities: string[]
  api_key_mask: string
  api_key_present: boolean
  api_key_mode: ProviderApiKeyMode
  api_key_input: string
}

interface ProviderTestResult {
  ok: boolean
  profile: string
  api_format?: string
  model?: string
  elapsed_ms?: number
  text_preview?: string
  error?: string
  provider_kind?: string
  provider_mode?: string
  payload_sanitized?: boolean
  reasoning_replay_tokens?: number
  usage_summary?: Record<string, any>
}

interface ProtocolCapability {
  key: string
  label: string
  status: 'ok' | 'configured' | 'unchecked' | 'failed' | string
  detail: string
}

interface ProtocolCompatibilityItem {
  key: string
  label: string
  napcat: 'supported' | 'compatible' | 'conditional' | 'manual' | 'unchecked' | string
  llonebot: 'supported' | 'compatible' | 'conditional' | 'manual' | 'unchecked' | string
  detail: string
}

interface ProtocolConnectionSummary {
  current_status: 'connected' | 'disconnected' | 'unknown' | string
  connected_bots: number
  self_ids: string[]
  changed_at: number
  last_seen_at: number
  disconnected_since: number
  last_recovery_seconds?: number | null
  last_error?: string
  event_count: number
}

interface ProtocolConnectionEvent {
  event_id: string
  kind: 'state' | 'error' | string
  status: string
  previous_status: string
  connected_bots: number
  self_ids: string[]
  source: string
  error?: string
  recovery_seconds?: number | null
  occurred_at: number
}

interface ProtocolConnectionPayload {
  summary: ProtocolConnectionSummary
  events: ProtocolConnectionEvent[]
  max_items: number
}

interface ProtocolHealth {
  adapter: string
  api_url: string
  connected_bots: number
  checked_at: number
  trace_summary?: ProtocolTraceSummary
  capabilities: ProtocolCapability[]
  compatibility?: ProtocolCompatibilityItem[]
  connection?: ProtocolConnectionSummary
}

interface ProtocolTraceSummary {
  total: number
  ok: number
  failed: number
  pending: number
  avg_elapsed_ms: number
  wrapped_bots?: number
  last_error?: string
}

interface ProtocolTrace {
  trace_id: string
  action: string
  status: string
  elapsed_ms: number
  started_at: number
  error?: string
}

interface ProtocolTracePayload {
  summary: ProtocolTraceSummary
  traces: ProtocolTrace[]
  max_items: number
}

interface ServiceHealthItem {
  id: string
  label: string
  status: 'ok' | 'warning' | 'error' | 'unknown' | string
  detail: string
  metric?: string
  meta?: Record<string, any>
}

interface HealthAlert {
  id: string
  source: string
  severity: 'error' | 'warning' | 'info' | string
  title: string
  detail: string
  metric?: string
  action: string
  maintenance_window: boolean
}

interface MaintenanceWindow {
  recommended: boolean
  severity: 'error' | 'warning' | 'info' | string
  title: string
  summary: string
  window_hint: string
  reasons: string[]
  checklist: string[]
  restart_recommended: boolean
}

interface RestartNotice {
  supported: boolean
  title: string
  summary: string
  window_hint: string
  impact: string[]
  works_for?: string[]
  needs_rebuild?: string[]
  checklist: string[]
}

interface HealthAlertPolicy {
  mode: string
  summary: string
  suppressed_count: number
  overflow_count: number
  alert_count: number
  thresholds?: Record<string, string>
}

interface ServicesHealth {
  checked_at: number
  overall_status: 'ok' | 'warning' | 'error' | 'unknown' | 'degraded' | string
  summary: {
    ok: number
    warning: number
    error: number
    unknown: number
  }
  alerts?: HealthAlert[]
  policy?: HealthAlertPolicy
  maintenance_window?: MaintenanceWindow
  services: ServiceHealthItem[]
}

interface RuntimeErrorEvent {
  event_id: string
  signature: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL' | string
  channel: string
  logger: string
  message: string
  occurred_at: number
}

interface RuntimeErrorGroup {
  signature: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL' | string
  channel: string
  logger: string
  message: string
  count: number
  first_seen_at: number
  last_seen_at: number
}

interface RuntimeErrorPayload {
  summary: {
    total: number
    warnings: number
    errors: number
    critical?: number
    unique: number
    last_error?: RuntimeErrorEvent | Record<string, never>
    last_warning?: RuntimeErrorEvent | Record<string, never>
    top_issue?: RuntimeErrorGroup | Record<string, never>
  }
  groups: RuntimeErrorGroup[]
  events: RuntimeErrorEvent[]
  max_events: number
  max_groups: number
}

const loading = ref(true)
const refreshing = ref(false)
const backupLoading = ref(false)
const protocolProbing = ref(false)
const error = ref(false)
const showAdvancedConsole = ref(false)
const lastLoadedAt = ref('')

const system = ref<SystemInfo | null>(null)
const health = ref<HealthInfo | null>(null)
const version = ref<VersionInfo | null>(null)
const humanizer = ref<HumanizerInfo | null>(null)
const talkSchedule = ref<TalkScheduleInfo | null>(null)
const providers = ref<ProvidersInfo | null>(null)
const protocol = ref<ProtocolHealth | null>(null)
const protocolTraces = ref<ProtocolTracePayload | null>(null)
const protocolConnections = ref<ProtocolConnectionPayload | null>(null)
const servicesHealth = ref<ServicesHealth | null>(null)
const runtimeErrors = ref<RuntimeErrorPayload | null>(null)
const providerTesting = ref<Record<string, boolean>>({})
const providerTestResults = ref<Record<string, ProviderTestResult>>({})
const providerSelectionSaving = ref(false)
const providerEditorVisible = ref(false)
const providerDefinitionsSaving = ref(false)
const providerDefaultDraft = ref('')
const providerTaskDraft = ref<Record<string, string>>({})
const providerProfilesDraft = ref<ProviderProfileDraft[]>([])
const providerProfilesOriginal = ref<ProviderProfileDraft[]>([])

const message = useMessage()
const router = useRouter()

const providerTaskOrder: ProviderTaskKey[] = ['main', 'thinker', 'compact', 'slang', 'vision']
const providerNamePattern = /^[A-Za-z0-9_-]+$/

const providerTaskLabels: Record<ProviderTaskKey, string> = {
  main: '主聊天',
  thinker: '思考',
  compact: '压缩',
  slang: '黑话',
  vision: '视觉',
}

const providerApiKeyModeOptions = [
  { label: '保留当前', value: 'keep' },
  { label: '替换密钥', value: 'replace' },
  { label: '清空密钥', value: 'clear' },
]

const heroTitle = computed(() => {
  if (error.value) return '运行状态暂不可用'
  if (health.value?.bot === 'running' && health.value?.napcat === 'connected') {
    return '控制台与消息适配层都处于在线状态'
  }
  if (health.value?.bot === 'running') return 'Bot 运行中，但连接层需要留意'
  return '当前运行状态需要人工确认'
})

const heroDescription = computed(() => {
  if (error.value) return '系统接口全部加载失败，请检查后端服务与依赖环境。'
  if (version.value?.has_update && version.value.latest_tag) {
    return `检测到可用更新 ${version.value.latest_tag}，建议在维护窗口中评估升级。`
  }
  return '这里聚合了进程资源、版本状态、防检测策略与备份入口，便于快速确认运行面是否稳定。'
})

const versionSummary = computed(() => {
  if (!version.value) return 'unknown'
  return version.value.summary || version.value.version || 'unknown'
})

const activeProvider = computed(() =>
  providers.value?.profiles.find(profile => profile.active)
  || providers.value?.profiles.find(profile => profile.name === providers.value?.default_profile)
  || providers.value?.profiles[0]
  || null,
)

const activeProviderUsageSummary = computed(() => activeProvider.value?.last_usage || {})

const providerProfileOptions = computed(() =>
  (providers.value?.profiles || []).map(profile => ({
    label: `${profile.name}${profile.model ? ` · ${profile.model}` : ''}`,
    value: profile.name,
  })),
)

const providerCapabilityOptions = computed(() =>
  providers.value?.capability_options || [
    { value: 'chat', label: '聊天' },
    { value: 'tools', label: '工具调用' },
    { value: 'thinking', label: '深度思考' },
    { value: 'vision', label: '视觉理解' },
    { value: 'json', label: '结构化 JSON' },
    { value: 'compact', label: '压缩任务' },
  ],
)

const providerApiFormatOptions = computed(() =>
  providers.value?.api_format_options || [
    { value: 'anthropic', label: 'anthropic' },
    { value: 'openai', label: 'openai' },
    { value: 'deepseek', label: 'deepseek' },
  ],
)

const currentTaskProfileMap = computed(() => {
  const entries = providers.value?.task_profiles || []
  return Object.fromEntries(entries.map(item => [item.task, item.profile]))
})

const providerSelectionDirty = computed(() => {
  if (!providers.value) return false
  if (providerDefaultDraft.value !== providers.value.default_profile) return true
  return providerTaskOrder.some(task =>
    (providerTaskDraft.value[task] || '') !== (currentTaskProfileMap.value[task] || ''),
  )
})

const providerDefinitionsDirty = computed(() => (
  serializeProviderDrafts(providerProfilesDraft.value) !== serializeProviderDrafts(providerProfilesOriginal.value)
))

const protocolOkCount = computed(() =>
  (protocol.value?.capabilities || []).filter(item => item.status === 'ok' || item.status === 'configured').length,
)

const protocolTraceSummary = computed(() => protocolTraces.value?.summary || protocol.value?.trace_summary || null)

const protocolConnectionSummary = computed(() =>
  protocolConnections.value?.summary || protocol.value?.connection || null,
)

const servicesNeedingAttention = computed(() =>
  (servicesHealth.value?.services || []).filter(item => item.status === 'warning' || item.status === 'error').length,
)

const healthAlerts = computed(() => servicesHealth.value?.alerts || [])
const alertPolicy = computed(() => servicesHealth.value?.policy || null)

const maintenanceWindow = computed(() => servicesHealth.value?.maintenance_window || null)

const restartNotice = computed(() => system.value?.restart_notice || null)

const runtimeErrorSummary = computed(() => runtimeErrors.value?.summary || null)

const runtimeIssueGroups = computed(() => runtimeErrors.value?.groups || [])
const advancedToolLinks = [
  { label: '独立日程页', path: '/schedule', note: '单页查看完整当日日程与心情' },
  { label: '用量统计', path: '/usage', note: '查看今日与本月调用统计' },
  { label: '沙盒', path: '/sandbox', note: '模拟消息与多轮回复' },
  { label: '调度器', path: '/scheduler', note: '查看活跃槽位与待发送状态' },
  { label: '插件', path: '/plugins', note: '查看插件治理与配置细节' },
]

onMounted(() => {
  void loadSystemStatus()
})

async function loadSystemStatus(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const results = await Promise.allSettled([
      api('/api/admin/system'),
      api('/api/admin/health'),
      api('/api/admin/version'),
      api('/api/admin/humanizer'),
      api('/api/admin/talk-schedule'),
      api('/api/admin/providers'),
      api('/api/admin/protocol/health'),
      api('/api/admin/services/health'),
      api('/api/admin/protocol/traces?limit=6'),
      api('/api/admin/protocol/connections?limit=5'),
      api('/api/admin/system/errors?event_limit=6&group_limit=6'),
    ])

    system.value = results[0].status === 'fulfilled' ? results[0].value : null
    health.value = results[1].status === 'fulfilled' ? results[1].value : null
    version.value = results[2].status === 'fulfilled' ? results[2].value : null
    humanizer.value = results[3].status === 'fulfilled' ? results[3].value : null
    talkSchedule.value = results[4].status === 'fulfilled' ? results[4].value : null
    providers.value = results[5].status === 'fulfilled' ? results[5].value : null
    if (providers.value) hydrateProviderSelectionDraft(providers.value)
    protocol.value = results[6].status === 'fulfilled' ? results[6].value : null
    servicesHealth.value = results[7].status === 'fulfilled' ? results[7].value : null
    protocolTraces.value = results[8].status === 'fulfilled' ? results[8].value : null
    protocolConnections.value = results[9].status === 'fulfilled' ? results[9].value : null
    runtimeErrors.value = results[10].status === 'fulfilled' ? results[10].value : null
    error.value = results.every(result => result.status === 'rejected')
    lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function hydrateProviderSelectionDraft(info: ProvidersInfo) {
  providerDefaultDraft.value = info.default_profile || 'main'
  const next: Record<string, string> = {}
  for (const task of providerTaskOrder) {
    const row = info.task_profiles?.find(item => item.task === task)
    next[task] = row?.profile || providerDefaultDraft.value || 'main'
  }
  next.main = providerDefaultDraft.value || 'main'
  providerTaskDraft.value = next
}

function buildProviderDraft(profile: ProviderProfile): ProviderProfileDraft {
  return {
    name: profile.name,
    api_format: profile.api_format || 'anthropic',
    base_url: profile.base_url || '',
    model: profile.model || '',
    max_tokens: typeof profile.max_tokens === 'number' ? profile.max_tokens : null,
    capabilities: [...(profile.capabilities || [])],
    api_key_mask: profile.api_key_mask || '',
    api_key_present: Boolean(profile.api_key_present || profile.api_key_mask),
    api_key_mode: 'keep',
    api_key_input: '',
  }
}

function cloneProviderDrafts(drafts: ProviderProfileDraft[]) {
  return drafts.map(draft => ({
    ...draft,
    capabilities: [...draft.capabilities],
  }))
}

function serializeProviderDrafts(drafts: ProviderProfileDraft[]) {
  return JSON.stringify(
    drafts.map(draft => ({
      ...draft,
      name: draft.name.trim(),
      api_key_input: draft.api_key_mode === 'replace' ? draft.api_key_input : '',
      capabilities: [...draft.capabilities].sort(),
    })),
  )
}

function resetProviderDefinitions() {
  if (!providers.value) return
  const next = (providers.value.profiles || []).map(buildProviderDraft)
  providerProfilesDraft.value = cloneProviderDrafts(next)
  providerProfilesOriginal.value = cloneProviderDrafts(next)
}

function openProviderEditor() {
  if (!providers.value) return
  resetProviderDefinitions()
  providerEditorVisible.value = true
}

function nextProfileName() {
  const used = new Set(providerProfilesDraft.value.map(item => item.name.trim()))
  let index = providerProfilesDraft.value.length + 1
  while (used.has(`profile_${index}`)) index += 1
  return `profile_${index}`
}

function addProviderDraft() {
  providerProfilesDraft.value = [
    ...providerProfilesDraft.value,
    {
      name: nextProfileName(),
      api_format: providerApiFormatOptions.value[0]?.value || 'anthropic',
      base_url: '',
      model: '',
      max_tokens: null,
      capabilities: ['chat'],
      api_key_mask: '',
      api_key_present: false,
      api_key_mode: 'replace',
      api_key_input: '',
    },
  ]
}

function removeProviderDraft(name: string) {
  if (name === 'main') return
  providerProfilesDraft.value = providerProfilesDraft.value.filter(profile => profile.name !== name)
}

function updateProviderDraft(index: number, patch: Partial<ProviderProfileDraft>) {
  providerProfilesDraft.value = providerProfilesDraft.value.map((profile, currentIndex) => (
    currentIndex === index
      ? {
          ...profile,
          ...patch,
          capabilities: [...(patch.capabilities || profile.capabilities || [])],
        }
      : profile
  ))
}

function setProviderCapabilities(index: number, value: Array<string | number>) {
  updateProviderDraft(index, { capabilities: value.map(item => String(item)) })
}

function onProviderCapabilitiesChange(index: number, value: Array<string | number> | null) {
  setProviderCapabilities(index, Array.isArray(value) ? value : [])
}

function setProviderApiKeyMode(index: number, value: string) {
  updateProviderDraft(index, {
    api_key_mode: (value || 'keep') as ProviderApiKeyMode,
    api_key_input: value === 'replace' ? providerProfilesDraft.value[index]?.api_key_input || '' : '',
  })
}

function validateProviderDrafts() {
  if (!providerProfilesDraft.value.length) return '至少保留一个 profile'
  const names = new Set<string>()
  let hasMain = false
  for (const profile of providerProfilesDraft.value) {
    const name = profile.name.trim()
    if (!name) return 'profile 名称不能为空'
    if (!providerNamePattern.test(name)) return `profile 名称只能包含字母、数字、下划线或短横线: ${name}`
    if (names.has(name)) return `profile 名称重复: ${name}`
    if (profile.api_key_mode === 'replace' && !profile.api_key_input.trim()) {
      return `profile ${name} 选择了替换密钥，但尚未填写 api_key`
    }
    names.add(name)
    if (name === 'main') hasMain = true
  }
  if (!hasMain) return '必须保留 main profile'
  return ''
}

async function probeProtocol() {
  protocolProbing.value = true
  try {
    protocol.value = await api('/api/admin/protocol/probe', { method: 'POST' })
    protocolConnections.value = await api('/api/admin/protocol/connections?limit=5')
    message.success('协议能力探测完成')
  } catch {
    message.error('协议能力探测失败')
  } finally {
    protocolProbing.value = false
  }
}

async function createBackup() {
  backupLoading.value = true
  try {
    const data = await api('/api/admin/backup', { method: 'POST' })
    if (data.ok) message.success(data.message)
    else message.error(data.error || '备份失败')
  } catch {
    message.error('备份失败')
  } finally {
    backupLoading.value = false
  }
}

function openAdvancedTool(path: string) {
  void router.push({ path })
}

function setDefaultProviderDraft(value: string) {
  providerDefaultDraft.value = value || 'main'
  providerTaskDraft.value = {
    ...providerTaskDraft.value,
    main: providerDefaultDraft.value,
  }
}

function setTaskProviderDraft(task: ProviderTaskKey, value: string) {
  providerTaskDraft.value = {
    ...providerTaskDraft.value,
    [task]: value || providerDefaultDraft.value || 'main',
  }
}

function providerTaskModel(task: ProviderTaskKey) {
  const profileName = providerTaskDraft.value[task]
  return providers.value?.profiles.find(profile => profile.name === profileName)?.model || '--'
}

async function saveProviderSelection() {
  if (!providers.value || !providerSelectionDirty.value) return
  providerSelectionSaving.value = true
  try {
    const data = await api('/api/admin/providers/selection', {
      method: 'POST',
      body: {
        default_profile: providerDefaultDraft.value || 'main',
        task_profiles: providerTaskDraft.value,
      },
    })
    if (!data?.ok) {
      message.error(data?.error || 'Provider profile 切换失败')
      return
    }
    message.success(data.message || 'Provider profile 已热切换')
    await loadSystemStatus(true)
  } catch (err: any) {
    message.error(err?.message || 'Provider profile 切换请求失败')
  } finally {
    providerSelectionSaving.value = false
  }
}

async function saveProviderDefinitions() {
  if (!providers.value || !providerDefinitionsDirty.value) return
  const validationError = validateProviderDrafts()
  if (validationError) {
    message.error(validationError)
    return
  }

  providerDefinitionsSaving.value = true
  try {
    const payload = providerProfilesDraft.value.map(profile => ({
      name: profile.name.trim(),
      api_format: profile.api_format || 'anthropic',
      base_url: profile.base_url || '',
      model: profile.model || '',
      max_tokens: profile.max_tokens ?? null,
      capabilities: profile.capabilities,
      api_key_mode: profile.api_key_mode,
      api_key: profile.api_key_mode === 'replace' ? profile.api_key_input : '',
    }))
    const data = await api('/api/admin/providers/definitions', {
      method: 'POST',
      body: { profiles: payload },
    })
    if (!data?.ok) {
      message.error(data?.error || 'Provider 定义保存失败')
      return
    }
    providerEditorVisible.value = false
    message.success(data.message || 'Provider 定义已保存')
    await loadSystemStatus(true)
  } catch (err: any) {
    message.error(err?.message || 'Provider 定义保存请求失败')
  } finally {
    providerDefinitionsSaving.value = false
  }
}

async function testProviderProfile(name: string) {
  providerTesting.value = { ...providerTesting.value, [name]: true }
  try {
    const result = await api(`/api/admin/providers/${encodeURIComponent(name)}/test`, { method: 'POST' })
    providerTestResults.value = { ...providerTestResults.value, [name]: result }
    if (result?.ok) message.success(`${name} profile 测试通过`)
    else message.warning(result?.error || `${name} profile 测试未通过`)
  } catch (err: any) {
    providerTestResults.value = {
      ...providerTestResults.value,
      [name]: {
        ok: false,
        profile: name,
        error: err?.message || 'Provider 测试请求失败',
      },
    }
    message.error('Provider 测试请求失败')
  } finally {
    providerTesting.value = { ...providerTesting.value, [name]: false }
  }
}

function formatDuration(seconds: number | null | undefined) {
  if (!seconds || seconds <= 0) return '--'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分钟`
  return `${minutes}分钟`
}

function formatCooldown(seconds: number | null | undefined) {
  if (!seconds || seconds <= 0) return '0s'
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  return formatDuration(seconds)
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value)))
}

function meterColor(value: number | null | undefined) {
  const percent = formatPercent(value)
  if (percent >= 85) return '#B84C5C'
  if (percent >= 70) return '#C58A2B'
  return '#2E8F6B'
}

function protocolTagType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'configured') return 'info'
  if (status === 'failed') return 'error'
  return 'default'
}

function compatibilityTagType(status: string) {
  if (status === 'supported' || status === 'compatible') return 'success'
  if (status === 'conditional') return 'warning'
  if (status === 'manual') return 'info'
  return 'default'
}

function compatibilityLabel(status: string) {
  if (status === 'supported') return '支持'
  if (status === 'compatible') return '兼容'
  if (status === 'conditional') return '条件支持'
  if (status === 'manual') return '手动确认'
  if (status === 'unchecked') return '未探测'
  return status
}

function protocolTraceType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'pending') return 'warning'
  return 'default'
}

function runtimeErrorLevelType(level: string) {
  if (level === 'CRITICAL' || level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  return 'default'
}

function runtimeErrorLevelLabel(level: string) {
  if (level === 'CRITICAL') return 'Critical'
  if (level === 'ERROR') return 'Error'
  if (level === 'WARNING') return 'Warning'
  return level || 'Unknown'
}

function protocolConnectionType(status: string) {
  if (status === 'connected') return 'success'
  if (status === 'disconnected') return 'error'
  return 'warning'
}

function protocolConnectionLabel(status: string) {
  if (status === 'connected') return '已连接'
  if (status === 'disconnected') return '已断开'
  return '未知'
}

function protocolConnectionEventLabel(event: ProtocolConnectionEvent) {
  if (event.kind === 'error') return '探测异常'
  if (event.status === 'connected') return '连接恢复'
  if (event.status === 'disconnected') return '连接断开'
  return '状态变化'
}

function formatTimestamp(value?: number | null) {
  if (!value) return '--'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatMs(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`
  return `${Math.round(value)}ms`
}

function serviceTagType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'warning' || status === 'degraded') return 'warning'
  if (status === 'error') return 'error'
  return 'default'
}

function serviceStatusLabel(status: string) {
  if (status === 'ok') return '健康'
  if (status === 'warning') return '留意'
  if (status === 'degraded') return '降级'
  if (status === 'error') return '异常'
  return '未知'
}

function providerResultType(result?: ProviderTestResult) {
  if (!result) return 'default'
  return result.ok ? 'success' : 'error'
}

function providerResultLabel(result?: ProviderTestResult) {
  if (!result) return '未测试'
  if (result.ok) return `通过 · ${formatMs(result.elapsed_ms)}`
  return `失败 · ${formatMs(result.elapsed_ms)}`
}

function providerRateLimitType(rateLimit?: ProviderRateLimit) {
  if (!rateLimit) return 'default'
  if (rateLimit.status === 'cooldown') return 'warning'
  if (rateLimit.rate_limited || rateLimit.failures) return 'info'
  return 'default'
}

function providerRateLimitLabel(rateLimit?: ProviderRateLimit) {
  if (!rateLimit) return 'ready'
  if (rateLimit.status === 'cooldown') {
    return `冷却 ${formatCooldown(rateLimit.cooldown_remaining_seconds)}`
  }
  if (rateLimit.rate_limited) return `${rateLimit.rate_limited} 次限流`
  return 'ready'
}

function providerModeLabel(mode?: string) {
  if (!mode) return '--'
  if (mode === 'native') return 'DeepSeek 原生'
  if (mode === 'native-beta') return 'DeepSeek Beta'
  if (mode === 'anthropic-compat') return 'Anthropic 兼容'
  if (mode === 'openai-compat') return 'OpenAI 兼容'
  return mode
}

function providerCacheHitLabel(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return `${value.toFixed(1)}%`
}

function formatTokenCount(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value) || value <= 0) return '--'
  return Number(value).toLocaleString('zh-CN')
}

function alertTagType(severity: string) {
  if (severity === 'error') return 'error'
  if (severity === 'warning') return 'warning'
  return 'info'
}

function alertSeverityLabel(severity: string) {
  if (severity === 'error') return '异常'
  if (severity === 'warning') return '留意'
  return '提示'
}

function serviceMetaTags(service: ServiceHealthItem) {
  if (service.id !== 'memory' || !service.meta) return []
  const semantic = (service.meta.semantic || {}) as Record<string, any>
  const tags: string[] = []
  if (typeof service.meta.card_count === 'number') tags.push(`${service.meta.card_count} cards`)
  if (typeof service.meta.message_count === 'number') tags.push(`${service.meta.message_count} messages`)
  if (typeof service.meta.active_sessions === 'number') tags.push(`${service.meta.active_sessions} sessions`)
  if (semantic.enabled) {
    tags.push(`semantic ${semantic.active_backend || semantic.requested_backend || 'ngram'}`)
    tags.push(`${semantic.hits || 0}/${semantic.queries || 0} hits`)
    if (semantic.fallbacks) tags.push(`${semantic.fallbacks} fallback`)
    if (semantic.errors) tags.push(`${semantic.errors} errors`)
  }
  return tags
}

function memorySemanticLastError(service: ServiceHealthItem) {
  if (service.id !== 'memory' || !service.meta?.semantic?.last_error) return ''
  return String(service.meta.semantic.last_error)
}
</script>

<template>
  <AppPage
    title="系统"
    eyebrow="Runtime Health"
    description="集中查看资源压力、连接状态、版本信息和备份能力。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag size="small" round :type="health?.bot === 'running' ? 'success' : 'error'">
          {{ health?.bot === 'running' ? 'Bot 在线' : 'Bot 异常' }}
        </NTag>
        <NButton secondary :loading="refreshing" @click="loadSystemStatus(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <RestartBotButton />
      </NSpace>
    </template>

    <NSpin :show="loading && !system && !health">
      <template v-if="error">
        <EmptyState
          title="系统状态加载失败"
          description="后端没有返回任何有效状态，请检查依赖环境、进程状态或管理端接口。"
          :icon="HardwareChipOutline"
        >
          <NButton secondary @click="loadSystemStatus(true)">
            重新加载
          </NButton>
        </EmptyState>
      </template>

      <template v-else>
        <AppCard bordered elevated class="system-hero">
          <div class="system-hero__main">
            <p class="system-hero__eyebrow">
              Runtime Snapshot
            </p>
            <h2 class="system-hero__title">
              {{ heroTitle }}
            </h2>
            <p class="system-hero__description">
              {{ heroDescription }}
            </p>
            <div class="system-hero__chips">
              <NTag round size="small">
                版本 {{ version?.version || 'unknown' }}
              </NTag>
              <NTag round size="small" :type="health?.napcat === 'connected' ? 'success' : 'warning'">
                {{ health?.napcat === 'connected' ? 'NapCat 已连接' : 'NapCat 未连接' }}
              </NTag>
              <NTag round size="small">
                {{ lastLoadedAt ? `更新于 ${lastLoadedAt}` : '等待数据' }}
              </NTag>
            </div>
          </div>

          <div class="system-hero__aside">
            <div class="system-hero__aside-card">
              <span class="system-hero__aside-label">当前版本</span>
              <strong class="system-hero__aside-value">
                {{ version?.version || 'unknown' }}
              </strong>
              <span class="system-hero__aside-meta">
                {{ version?.has_update && version?.latest_tag ? `可升级到 ${version.latest_tag}` : '当前没有检测到更高版本' }}
              </span>
            </div>
            <div class="system-hero__aside-card">
              <span class="system-hero__aside-label">运行时长</span>
              <strong class="system-hero__aside-value">
                {{ formatDuration(health?.uptime_seconds) }}
              </strong>
              <span class="system-hero__aside-meta">
                活跃会话 {{ system?.active_sessions ?? '--' }} · PID {{ system?.process?.pid ?? '--' }}
              </span>
            </div>
          </div>
        </AppCard>

        <div class="system-metric-grid">
          <MetricCard
            title="Bot 状态"
            :value="health?.bot === 'running' ? '运行中' : '异常'"
            hint="管理端后端主进程"
            :icon="PulseOutline"
            accent="success"
          />
          <MetricCard
            title="NapCat"
            :value="health?.napcat === 'connected' ? '已连接' : '断开'"
            hint="消息适配层连接状态"
            :icon="GitNetworkOutline"
            :accent="health?.napcat === 'connected' ? 'info' : 'warning'"
          />
          <MetricCard
            title="运行时长"
            :value="formatDuration(health?.uptime_seconds)"
            hint="基于 `/health` 返回的 uptime"
            :icon="FlashOutline"
            accent="primary"
          />
          <MetricCard
            title="活跃会话"
            :value="system?.active_sessions ?? '--'"
            hint="Short-term memory 当前会话数"
            :icon="SparklesOutline"
            accent="warning"
          />
        </div>

        <AppCard bordered elevated class="system-ops-card">
          <div class="system-panel__head">
            <div>
              <p class="system-panel__eyebrow">
                Maintenance Window
              </p>
              <h3 class="system-panel__title">
                运维建议
              </h3>
            </div>
            <NSpace align="center" :size="8">
              <NTag
                size="small"
                round
                :type="serviceTagType(maintenanceWindow?.severity || 'info')"
              >
                {{ maintenanceWindow?.recommended ? '建议维护窗口' : '常规观察' }}
              </NTag>
              <NTag v-if="healthAlerts.length" size="small" round type="warning">
                {{ healthAlerts.length }} 条告警
              </NTag>
              <NTag v-if="alertPolicy?.suppressed_count" size="small" round>
                折叠 {{ alertPolicy.suppressed_count }} 条轻量提醒
              </NTag>
              <NTag v-if="maintenanceWindow?.restart_recommended" size="small" round type="info">
                建议重启验证
              </NTag>
            </NSpace>
          </div>

          <div class="system-ops-grid">
            <div class="system-ops-panel">
              <div class="system-ops-panel__intro">
                <strong>{{ maintenanceWindow?.title || '当前运行面整体稳定' }}</strong>
                <p>{{ maintenanceWindow?.summary || '当前没有明显服务告警，常规调整仍建议先备份再操作。' }}</p>
                <small>{{ maintenanceWindow?.window_hint || '优先选择群聊低峰与管理员可回看系统页的时段。' }}</small>
              </div>

              <div v-if="maintenanceWindow?.reasons?.length" class="system-ops-list">
                <div
                  v-for="reason in maintenanceWindow.reasons"
                  :key="reason"
                  class="system-ops-list__item"
                >
                  <span class="system-ops-list__dot" />
                  <span>{{ reason }}</span>
                </div>
              </div>

              <div v-if="maintenanceWindow?.checklist?.length" class="system-ops-checklist">
                <strong>建议顺序</strong>
                <div
                  v-for="item in maintenanceWindow.checklist"
                  :key="item"
                  class="system-ops-checklist__item"
                >
                  <span class="system-ops-checklist__index" />
                  <span>{{ item }}</span>
                </div>
              </div>
            </div>

            <div class="system-ops-panel system-ops-panel--restart">
              <div class="system-ops-panel__intro">
                <strong>{{ restartNotice?.title || '在线重启说明' }}</strong>
                <p>{{ restartNotice?.summary || '这个按钮只会重启当前 Bot 进程，让配置与运行态重新收敛；它不会重建镜像。' }}</p>
              </div>

              <div v-if="restartNotice?.impact?.length" class="system-ops-list">
                <div
                  v-for="item in restartNotice.impact"
                  :key="item"
                  class="system-ops-list__item"
                >
                  <span class="system-ops-list__dot system-ops-list__dot--impact" />
                  <span>{{ item }}</span>
                </div>
              </div>

              <div v-if="restartNotice?.works_for?.length" class="system-ops-group">
                <small class="system-ops-group__title">适合在线重启</small>
                <div class="system-ops-list">
                  <div
                    v-for="item in restartNotice.works_for"
                    :key="item"
                    class="system-ops-list__item"
                  >
                    <span class="system-ops-list__dot system-ops-list__dot--fit" />
                    <span>{{ item }}</span>
                  </div>
                </div>
              </div>

              <div v-if="restartNotice?.needs_rebuild?.length" class="system-ops-group">
                <small class="system-ops-group__title">需要先重建镜像</small>
                <div class="system-ops-list">
                  <div
                    v-for="item in restartNotice.needs_rebuild"
                    :key="item"
                    class="system-ops-list__item"
                  >
                    <span class="system-ops-list__dot system-ops-list__dot--warning" />
                    <span>{{ item }}</span>
                  </div>
                </div>
              </div>

              <small class="system-ops-panel__hint">
                {{ restartNotice?.window_hint || '改配置可直接在线重启；改代码或依赖请先重建镜像。' }}
              </small>
            </div>
          </div>

          <div v-if="healthAlerts.length" class="system-alert-list">
            <div
              v-for="alert in healthAlerts"
              :key="alert.id"
              class="system-alert-row"
              :class="`system-alert-row--${alert.severity}`"
            >
              <div class="system-alert-row__main">
                <div class="system-alert-row__head">
                  <NTag size="small" round :type="alertTagType(alert.severity)">
                    {{ alertSeverityLabel(alert.severity) }}
                  </NTag>
                  <strong>{{ alert.title }}</strong>
                  <span v-if="alert.metric">{{ alert.metric }}</span>
                </div>
                <p>{{ alert.detail }}</p>
                <small>{{ alert.action }}</small>
              </div>
            </div>
          </div>

          <div v-else-if="alertPolicy?.suppressed_count" class="system-ops-muted">
            当前没有达到阈值的顶部告警，但还有 {{ alertPolicy.suppressed_count }} 条轻量提醒保留在下方服务级健康卡中。
          </div>

          <p v-if="alertPolicy?.summary" class="system-ops-policy-note">
            {{ alertPolicy.summary }}
          </p>
        </AppCard>

        <AppCard bordered elevated class="system-service-health">
          <div class="system-panel__head">
            <div>
              <p class="system-panel__eyebrow">
                Service Health
              </p>
              <h3 class="system-panel__title">
                服务级健康
              </h3>
            </div>
            <NSpace align="center" :size="8">
              <NTag size="small" round :type="serviceTagType(servicesHealth?.overall_status || 'unknown')">
                {{ serviceStatusLabel(servicesHealth?.overall_status || 'unknown') }}
              </NTag>
              <NTag v-if="servicesNeedingAttention" size="small" round type="warning">
                {{ servicesNeedingAttention }} 项需关注
              </NTag>
            </NSpace>
          </div>

          <div v-if="servicesHealth?.services?.length" class="system-service-grid">
            <div
              v-for="service in servicesHealth.services"
              :key="service.id"
              class="system-service-card"
              :class="`system-service-card--${service.status}`"
            >
              <div class="system-service-card__head">
                <div>
                  <strong>{{ service.label }}</strong>
                  <span>{{ service.metric || service.id }}</span>
                </div>
                <NTag size="small" round :type="serviceTagType(service.status)">
                  {{ serviceStatusLabel(service.status) }}
                </NTag>
              </div>
              <div v-if="serviceMetaTags(service).length" class="system-service-card__meta">
                <NTag
                  v-for="tag in serviceMetaTags(service)"
                  :key="tag"
                  size="small"
                  round
                >
                  {{ tag }}
                </NTag>
              </div>
              <p>{{ service.detail }}</p>
              <small v-if="memorySemanticLastError(service)" class="system-service-card__note">
                最近语义错误：{{ memorySemanticLastError(service) }}
              </small>
            </div>
          </div>

          <EmptyState
            v-else
            compact
            title="服务健康尚未返回"
            description="系统资源可用，但服务级聚合接口暂时没有返回数据。"
            :icon="HardwareChipOutline"
          />
        </AppCard>

        <AppCard bordered elevated class="system-runtime-errors">
          <div class="system-panel__head">
            <div>
              <p class="system-panel__eyebrow">
                Runtime Signals
              </p>
              <h3 class="system-panel__title">
                关键错误
              </h3>
            </div>
            <NSpace align="center" :size="8">
              <NTag size="small" round :type="(runtimeErrorSummary?.errors || 0) > 0 ? 'error' : 'success'">
                {{ runtimeErrorSummary?.errors || 0 }} error
              </NTag>
              <NTag size="small" round :type="(runtimeErrorSummary?.warnings || 0) > 0 ? 'warning' : 'default'">
                {{ runtimeErrorSummary?.warnings || 0 }} warning
              </NTag>
            </NSpace>
          </div>

          <div class="system-runtime-errors__summary">
            <div>
              <span>滚动记录</span>
              <strong>{{ runtimeErrorSummary?.total || 0 }}</strong>
            </div>
            <div>
              <span>唯一问题</span>
              <strong>{{ runtimeErrorSummary?.unique || 0 }}</strong>
            </div>
            <div>
              <span>容量</span>
              <strong>{{ runtimeErrors?.max_events || 0 }}</strong>
            </div>
          </div>

          <div v-if="runtimeIssueGroups.length" class="system-runtime-errors__list">
            <div
              v-for="issue in runtimeIssueGroups"
              :key="issue.signature"
              class="system-runtime-error-row"
              :class="`system-runtime-error-row--${issue.level.toLowerCase()}`"
            >
              <div class="system-runtime-error-row__main">
                <div class="system-runtime-error-row__head">
                  <NTag size="small" round :type="runtimeErrorLevelType(issue.level)">
                    {{ runtimeErrorLevelLabel(issue.level) }}
                  </NTag>
                  <strong>{{ issue.channel || issue.logger || 'runtime' }}</strong>
                  <span>{{ issue.count }} 次</span>
                </div>
                <p>{{ issue.message }}</p>
                <small>
                  首次 {{ formatTimestamp(issue.first_seen_at) }} · 最近 {{ formatTimestamp(issue.last_seen_at) }}
                </small>
              </div>
            </div>
          </div>

          <EmptyState
            v-else
            compact
            title="最近没有关键错误"
            description="运行期 WARNING / ERROR / CRITICAL 会在这里自动聚合，便于先看摘要再进入日志页定位。"
            :icon="ShieldCheckmarkOutline"
          />
        </AppCard>

        <div class="system-main-grid">
          <AppCard bordered elevated class="system-panel">
            <div class="system-panel__head">
              <div>
                <p class="system-panel__eyebrow">
                  Resource Pressure
                </p>
                <h3 class="system-panel__title">
                  系统资源
                </h3>
              </div>
              <NTag size="small">
                PID {{ system?.process?.pid ?? '--' }}
              </NTag>
            </div>

            <div class="system-resource-list">
              <div class="system-resource">
                <div class="system-resource__head">
                  <span>CPU</span>
                  <strong>{{ formatPercent(system?.cpu_percent) }}%</strong>
                </div>
                <NProgress
                  type="line"
                  :percentage="formatPercent(system?.cpu_percent)"
                  :height="10"
                  :show-indicator="false"
                  :color="meterColor(system?.cpu_percent)"
                />
              </div>

              <div class="system-resource">
                <div class="system-resource__head">
                  <span>内存</span>
                  <strong>
                    {{ system?.memory ? `${system.memory.used_gb} / ${system.memory.total_gb} GB` : '--' }}
                  </strong>
                </div>
                <NProgress
                  type="line"
                  :percentage="formatPercent(system?.memory?.percent)"
                  :height="10"
                  :show-indicator="false"
                  :color="meterColor(system?.memory?.percent)"
                />
              </div>

              <div class="system-resource">
                <div class="system-resource__head">
                  <span>磁盘</span>
                  <strong>
                    {{ system?.disk ? `${system.disk.used_gb} / ${system.disk.total_gb} GB` : '--' }}
                  </strong>
                </div>
                <NProgress
                  type="line"
                  :percentage="formatPercent(system?.disk?.percent)"
                  :height="10"
                  :show-indicator="false"
                  :color="meterColor(system?.disk?.percent)"
                />
              </div>
            </div>

            <div class="system-stats-grid">
              <div class="system-stat-card">
                <span class="system-stat-card__label">进程内存</span>
                <strong class="system-stat-card__value">
                  {{ system?.process?.memory_mb != null ? `${Number(system.process.memory_mb).toFixed(1)} MB` : '--' }}
                </strong>
              </div>
              <div class="system-stat-card">
                <span class="system-stat-card__label">线程数</span>
                <strong class="system-stat-card__value">
                  {{ system?.process?.threads ?? '--' }}
                </strong>
              </div>
            </div>
          </AppCard>

          <AppCard bordered elevated class="system-panel">
            <div class="system-panel__head">
              <div>
                <p class="system-panel__eyebrow">
                  Policies & Release
                </p>
                <h3 class="system-panel__title">
                  运行策略
                </h3>
              </div>
              <NTag v-if="version?.has_update" size="small" type="info">
                可更新
              </NTag>
            </div>

            <div class="system-stack">
              <AppCard bordered embedded class="system-stack__item">
                <div class="system-stack__head">
                  <div class="system-stack__icon">
                    <NIcon :component="HardwareChipOutline" />
                  </div>
                  <div>
                    <h4>版本信息</h4>
                    <p>{{ versionSummary }}</p>
                  </div>
                </div>
                <div class="system-stack__body">
                  <NTag size="small">{{ version?.version || 'unknown' }}</NTag>
                  <NTag v-if="version?.has_update && version?.latest_tag" size="small" type="info">
                    最新 {{ version.latest_tag }}
                  </NTag>
                </div>
                <a
                  v-if="version?.has_update && version?.latest_url"
                  class="system-link"
                  :href="version.latest_url"
                  target="_blank"
                  rel="noreferrer"
                >
                  查看发布说明
                </a>
              </AppCard>

              <AppCard bordered embedded class="system-stack__item">
                <div class="system-stack__head">
                  <div class="system-stack__icon">
                    <NIcon :component="ShieldCheckmarkOutline" />
                  </div>
                  <div>
                    <h4>防检测策略</h4>
                    <p>{{ humanizer?.enabled ? '已启用' : '已关闭' }}</p>
                  </div>
                </div>
                <div class="system-inline-list">
                  <span>延迟 {{ humanizer?.enabled ? `${Number(humanizer.min_delay || 0).toFixed(1)}-${Number(humanizer.max_delay || 0).toFixed(1)}s` : '--' }}</span>
                  <span>字延迟 {{ humanizer?.enabled ? `${Number(humanizer.char_delay || 0).toFixed(2)}s` : '--' }}</span>
                </div>
              </AppCard>

              <AppCard bordered embedded class="system-stack__item">
                <div class="system-stack__head">
                  <div class="system-stack__icon">
                    <NIcon :component="SparklesOutline" />
                  </div>
                  <div>
                    <h4>发言倍率</h4>
                    <p>当前时间倍率策略</p>
                  </div>
                </div>
                <div class="system-inline-list">
                  <span>{{ talkSchedule ? `${Number(talkSchedule.time_multiplier).toFixed(1)}x` : '--' }}</span>
                  <span>会影响主动发言节奏</span>
                </div>
              </AppCard>
            </div>
          </AppCard>
        </div>

        <AppCard bordered elevated class="system-advanced-entry">
          <div class="system-panel__head">
            <div>
              <p class="system-panel__eyebrow">
                Advanced Tools
              </p>
              <h3 class="system-panel__title">
                低频工具与深度排查
              </h3>
            </div>
            <NButton secondary @click="showAdvancedConsole = !showAdvancedConsole">
              {{ showAdvancedConsole ? '收起高级区' : '打开高级区' }}
            </NButton>
          </div>

          <p class="system-advanced-entry__description">
            这些页面和观测能力仍然保留，但默认不占主导航注意力。只有在调试、深查或迁移时再进入即可。
          </p>

          <div class="system-advanced-entry__tools">
            <button
              v-for="tool in advancedToolLinks"
              :key="tool.path"
              type="button"
              class="system-advanced-entry__tool"
              @click="openAdvancedTool(tool.path)"
            >
              <strong>{{ tool.label }}</strong>
              <span>{{ tool.note }}</span>
            </button>
          </div>
        </AppCard>

        <template v-if="showAdvancedConsole">
          <div class="system-observability-grid">
          <AppCard bordered elevated class="system-panel">
            <div class="system-panel__head">
              <div>
                <p class="system-panel__eyebrow">
                  Provider Profiles
                </p>
                <h3 class="system-panel__title">
                  LLM Provider
                </h3>
              </div>
              <NSpace align="center" :size="10">
                <NButton size="small" secondary @click="openProviderEditor">
                  定义管理
                </NButton>
                <NTag size="small" round type="info">
                  {{ providers?.profiles.length || 0 }} 个 profile
                </NTag>
              </NSpace>
            </div>

            <div class="system-provider-card">
              <div class="system-provider-card__head">
                <div>
                  <span class="system-provider-card__label">当前默认</span>
                  <strong>{{ activeProvider?.name || '--' }}</strong>
                  <p>{{ activeProvider?.model || '未配置模型' }}</p>
                </div>
                <NTag size="small" round :type="providerSelectionDirty ? 'warning' : 'success'">
                  {{ providerSelectionDirty ? '待应用' : '运行中' }}
                </NTag>
              </div>
              <div class="system-inline-list">
                <span>{{ activeProvider?.api_format || '--' }}</span>
                <span>{{ activeProvider?.base_url || '--' }}</span>
                <span>max {{ activeProvider?.max_tokens ?? '--' }}</span>
              </div>
              <div class="system-provider-runtime">
                <div class="system-provider-runtime__item">
                  <span>运行模式</span>
                  <strong>{{ providerModeLabel(activeProvider?.provider_mode) }}</strong>
                </div>
                <div class="system-provider-runtime__item">
                  <span>最近命中率</span>
                  <strong>{{ providerCacheHitLabel(activeProvider?.last_cache_hit_pct) }}</strong>
                </div>
                <div class="system-provider-runtime__item">
                  <span>Replay Tokens</span>
                  <strong>{{ formatTokenCount(activeProvider?.last_reasoning_replay_tokens) }}</strong>
                </div>
                <div class="system-provider-runtime__item">
                  <span>Payload Sanitizer</span>
                  <strong>{{ activeProvider?.last_payload_sanitized ? '介入过' : '未介入' }}</strong>
                </div>
              </div>
              <div class="system-inline-list">
                <span>hit {{ formatTokenCount(activeProvider?.last_prompt_cache_hit_tokens) }}</span>
                <span>miss {{ formatTokenCount(activeProvider?.last_prompt_cache_miss_tokens) }}</span>
                <span>provider {{ activeProvider?.provider_kind || '--' }}</span>
                <span v-if="activeProviderUsageSummary?.completion_tokens_details?.reasoning_tokens != null">
                  reasoning {{ formatTokenCount(activeProviderUsageSummary.completion_tokens_details.reasoning_tokens) }}
                </span>
              </div>
              <div class="system-provider-switcher">
                <span>默认 profile</span>
                <NSelect
                  size="small"
                  :value="providerDefaultDraft"
                  :options="providerProfileOptions"
                  :disabled="!providerProfileOptions.length"
                  @update:value="value => setDefaultProviderDraft(String(value))"
                />
                <NButton
                  size="small"
                  type="primary"
                  secondary
                  :disabled="!providerSelectionDirty"
                  :loading="providerSelectionSaving"
                  @click="saveProviderSelection"
                >
                  应用热切换
                </NButton>
              </div>
            </div>

            <div class="system-provider-list">
              <div
                v-for="profile in providers?.profiles || []"
                :key="profile.name"
                class="system-provider-row"
              >
                <div class="system-provider-row__main">
                  <strong>{{ profile.name }}</strong>
                  <span>{{ profile.model || '--' }}</span>
                  <small>
                    {{ providerModeLabel(profile.provider_mode) }} · hit {{ providerCacheHitLabel(profile.last_cache_hit_pct) }} · replay {{ formatTokenCount(profile.last_reasoning_replay_tokens) }}
                  </small>
                  <small v-if="providerTestResults[profile.name]">
                    {{ providerTestResults[profile.name].ok ? providerTestResults[profile.name].text_preview || '连通性正常' : providerTestResults[profile.name].error }}
                  </small>
                </div>
                <div class="system-provider-row__actions">
                  <NTag size="small" round :type="profile.active ? 'success' : 'default'">
                    {{ profile.active ? '默认' : profile.api_format }}
                  </NTag>
                  <NTag
                    v-if="providerTestResults[profile.name]"
                    size="small"
                    round
                    :type="providerResultType(providerTestResults[profile.name])"
                  >
                    {{ providerResultLabel(providerTestResults[profile.name]) }}
                  </NTag>
                  <NTag
                    size="small"
                    round
                    :type="providerRateLimitType(profile.rate_limit)"
                  >
                    {{ providerRateLimitLabel(profile.rate_limit) }}
                  </NTag>
                  <NButton
                    size="tiny"
                    secondary
                    :loading="providerTesting[profile.name]"
                    @click="testProviderProfile(profile.name)"
                  >
                    测试
                  </NButton>
                </div>
              </div>
            </div>

            <div v-if="providers?.task_profiles?.length" class="system-task-profile-list system-task-profile-list--editable">
              <div
                v-for="task in providerTaskOrder"
                :key="task"
                class="system-task-profile-row"
              >
                <span>{{ providerTaskLabels[task] }}</span>
                <NSelect
                  size="tiny"
                  :value="providerTaskDraft[task]"
                  :options="providerProfileOptions"
                  :disabled="!providerProfileOptions.length || task === 'main'"
                  @update:value="value => setTaskProviderDraft(task, String(value))"
                />
                <em>{{ task === 'main' ? '跟随默认 profile' : providerTaskModel(task) }}</em>
              </div>
            </div>
            <p class="system-provider-note">
              热切换只改变任务映射；“定义管理”会修改 profile 本体并同步写回 `config/config.json`。两者都会立即刷新运行中的 LLMClient，不会清空现有会话。
            </p>
          </AppCard>

          <AppCard bordered elevated class="system-panel">
            <div class="system-panel__head">
              <div>
                <p class="system-panel__eyebrow">
                  Protocol Probe
                </p>
                <h3 class="system-panel__title">
                  协议能力
                </h3>
              </div>
              <NButton size="small" secondary :loading="protocolProbing" @click="probeProtocol">
                探测
              </NButton>
            </div>

            <div class="system-protocol-summary">
              <div>
                <span>适配器</span>
                <strong>{{ protocol?.adapter || 'napcat' }}</strong>
              </div>
              <div>
                <span>连接 Bot</span>
                <strong>{{ protocol?.connected_bots ?? 0 }}</strong>
              </div>
              <div>
                <span>可用能力</span>
                <strong>{{ protocolOkCount }}/{{ protocol?.capabilities.length || 0 }}</strong>
              </div>
            </div>

            <div class="system-connection-panel">
              <div class="system-connection-panel__head">
                <div>
                  <span>连接历史</span>
                  <strong>
                    {{ protocolConnectionLabel(protocolConnectionSummary?.current_status || 'unknown') }}
                    · {{ protocolConnectionSummary?.connected_bots ?? 0 }} 个 Bot
                  </strong>
                </div>
                <NTag
                  size="small"
                  round
                  :type="protocolConnectionType(protocolConnectionSummary?.current_status || 'unknown')"
                >
                  {{ protocolConnectionSummary?.event_count || 0 }} 条记录
                </NTag>
              </div>
              <div class="system-connection-meta">
                <span>最近变化 {{ formatTimestamp(protocolConnectionSummary?.changed_at) }}</span>
                <span>最近确认 {{ formatTimestamp(protocolConnectionSummary?.last_seen_at) }}</span>
                <span v-if="protocolConnectionSummary?.last_recovery_seconds != null">
                  上次恢复 {{ formatDuration(protocolConnectionSummary.last_recovery_seconds) }}
                </span>
              </div>
              <p v-if="protocolConnectionSummary?.last_error" class="system-connection-error">
                最近错误：{{ protocolConnectionSummary.last_error }}
              </p>
              <div v-if="protocolConnections?.events?.length" class="system-connection-list">
                <div
                  v-for="event in protocolConnections.events"
                  :key="event.event_id"
                  class="system-connection-row"
                >
                  <div>
                    <strong>{{ protocolConnectionEventLabel(event) }}</strong>
                    <span>
                      {{ formatTimestamp(event.occurred_at) }}
                      · {{ event.source }}
                      <template v-if="event.self_ids?.length">
                        · self_id={{ event.self_ids.join(', ') }}
                      </template>
                    </span>
                    <small v-if="event.error">{{ event.error }}</small>
                  </div>
                  <NTag size="small" round :type="protocolConnectionType(event.status)">
                    {{ protocolConnectionLabel(event.status) }}
                  </NTag>
                </div>
              </div>
              <p v-else class="system-connection-empty">
                暂无连接状态变化；Bot 首次连接、断开或探测异常后会在这里留下记录。
              </p>
            </div>

            <div class="system-trace-panel">
              <div class="system-trace-panel__head">
                <div>
                  <span>请求 Echo 追踪</span>
                  <strong>{{ protocolTraceSummary?.ok || 0 }} 成功 / {{ protocolTraceSummary?.failed || 0 }} 失败</strong>
                </div>
                <NTag size="small" round :type="(protocolTraceSummary?.failed || 0) > 0 ? 'warning' : 'success'">
                  平均 {{ formatMs(protocolTraceSummary?.avg_elapsed_ms) }}
                </NTag>
              </div>
              <div v-if="protocolTraces?.traces?.length" class="system-trace-list">
                <div
                  v-for="trace in protocolTraces.traces"
                  :key="trace.trace_id"
                  class="system-trace-row"
                >
                  <div>
                    <strong>{{ trace.action }}</strong>
                    <span>{{ trace.trace_id }} · {{ formatMs(trace.elapsed_ms) }}</span>
                  </div>
                  <NTag size="small" round :type="protocolTraceType(trace.status)">
                    {{ trace.status }}
                  </NTag>
                </div>
              </div>
              <p v-else class="system-trace-empty">
                Bot 连接后，OneBot API 调用会在这里留下最近追踪记录。
              </p>
            </div>

            <div class="system-capability-list">
              <div
                v-for="capability in protocol?.capabilities || []"
                :key="capability.key"
                class="system-capability-row"
              >
                <div>
                  <strong>{{ capability.label }}</strong>
                  <span>{{ capability.detail }}</span>
                </div>
                <NTag size="small" round :type="protocolTagType(capability.status)">
                  {{ capability.status }}
                </NTag>
              </div>
            </div>

            <div class="system-compatibility-panel">
              <div class="system-compatibility-panel__head">
                <div>
                  <span>兼容清单</span>
                  <strong>NapCat 默认 · LLOneBot 备用目标</strong>
                </div>
                <NTag size="small" round>
                  只读检查
                </NTag>
              </div>
              <div class="system-compatibility-list">
                <div
                  v-for="item in protocol?.compatibility || []"
                  :key="item.key"
                  class="system-compatibility-row"
                >
                  <div class="system-compatibility-row__main">
                    <strong>{{ item.label }}</strong>
                    <span>{{ item.detail }}</span>
                  </div>
                  <div class="system-compatibility-row__tags">
                    <NTag size="small" round :type="compatibilityTagType(item.napcat)">
                      NapCat {{ compatibilityLabel(item.napcat) }}
                    </NTag>
                    <NTag size="small" round :type="compatibilityTagType(item.llonebot)">
                      LLOneBot {{ compatibilityLabel(item.llonebot) }}
                    </NTag>
                  </div>
                </div>
              </div>
            </div>
          </AppCard>
        </div>

          <AppCard bordered elevated class="system-backup">
            <div class="system-backup__copy">
              <p class="system-panel__eyebrow">
                Backup
              </p>
              <h3 class="system-panel__title">
                快速备份运行数据
              </h3>
              <p class="system-backup__description">
                将 `config/` 与 `storage/` 归档到时间戳压缩包，适合在调参、升级或数据迁移前先留一个恢复点。
              </p>
            </div>
            <div class="system-backup__action">
              <NButton type="primary" :loading="backupLoading" @click="createBackup">
                <template #icon>
                  <NIcon :component="ArchiveOutline" />
                </template>
                创建备份
              </NButton>
            </div>
          </AppCard>
        </template>
      </template>
    </NSpin>

    <NDrawer v-model:show="providerEditorVisible" :width="760">
      <NDrawerContent closable>
        <template #header>
          <div class="system-provider-editor__header">
            <div>
              <p class="system-panel__eyebrow">
                Provider Definition Editor
              </p>
              <h3 class="system-panel__title">
                管理 LLM Profile 定义
              </h3>
            </div>
            <NTag size="small" round :type="providerDefinitionsDirty ? 'warning' : 'success'">
              {{ providerDefinitionsDirty ? '有未保存修改' : '已同步' }}
            </NTag>
          </div>
        </template>

        <div class="system-provider-editor">
          <NAlert type="info" :show-icon="false" class="system-provider-editor__tip">
            `main` profile 会同步 legacy `llm.base_url / api_key / model / max_tokens`，方便旧配置与新 profile 体系兼容；删除其他 profile 后，引用它的任务映射会自动回退到当前默认 profile。
          </NAlert>

          <div class="system-provider-editor__toolbar">
            <div class="system-inline-list">
              <span>{{ providerProfilesDraft.length }} 个定义</span>
              <span>密钥默认只显示遮罩值</span>
            </div>
            <NButton size="small" secondary @click="addProviderDraft">
              新增 profile
            </NButton>
          </div>

          <div class="system-provider-editor__list">
            <AppCard
              v-for="(profile, index) in providerProfilesDraft"
              :key="`${profile.name || 'profile'}-${index}`"
              bordered
              embedded
              class="system-provider-editor__card"
            >
              <div class="system-provider-editor__card-head">
                <div>
                  <strong>{{ profile.name === 'main' ? 'main · 兼容基线' : profile.name || `profile ${index + 1}` }}</strong>
                  <p v-if="profile.name === 'main'">
                    会同步 legacy `llm.*` 根配置，并作为其它 profile 的回退基线。
                  </p>
                  <p v-else>
                    保存后会写回 `llm.profiles.{{ profile.name || `profile_${index + 1}` }}`。
                  </p>
                </div>
                <NButton
                  size="small"
                  quaternary
                  type="error"
                  :disabled="profile.name === 'main'"
                  @click="removeProviderDraft(profile.name)"
                >
                  删除
                </NButton>
              </div>

              <NForm label-placement="top" class="system-provider-editor__form">
                <NGrid :cols="24" :x-gap="14" :y-gap="6" responsive="screen">
                  <NFormItemGi :span="8" label="Profile 名称">
                    <NInput
                      :value="profile.name"
                      placeholder="例如 slang / vision"
                      @update:value="value => updateProviderDraft(index, { name: String(value || '') })"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="8" label="API 格式">
                    <NSelect
                      :value="profile.api_format"
                      :options="providerApiFormatOptions"
                      @update:value="value => updateProviderDraft(index, { api_format: String(value || 'anthropic') })"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="8" label="Max Tokens">
                    <NInputNumber
                      :value="profile.max_tokens"
                      :min="1"
                      :step="128"
                      clearable
                      style="width: 100%"
                      @update:value="value => updateProviderDraft(index, { max_tokens: typeof value === 'number' ? value : null })"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="12" label="Base URL">
                    <NInput
                      :value="profile.base_url"
                      placeholder="https://api.example.com/v1"
                      @update:value="value => updateProviderDraft(index, { base_url: String(value || '') })"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="12" label="Model">
                    <NInput
                      :value="profile.model"
                      placeholder="claude-sonnet / gpt-4o-mini"
                      @update:value="value => updateProviderDraft(index, { model: String(value || '') })"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="24" label="能力声明">
                    <NCheckboxGroup
                      :value="profile.capabilities"
                      @update:value="onProviderCapabilitiesChange(index, $event)"
                    >
                      <NSpace wrap :size="[10, 10]">
                        <NCheckbox
                          v-for="item in providerCapabilityOptions"
                          :key="item.value"
                          :value="item.value"
                        >
                          {{ item.label }}
                        </NCheckbox>
                      </NSpace>
                    </NCheckboxGroup>
                  </NFormItemGi>

                  <NFormItemGi :span="10" label="密钥处理">
                    <NSelect
                      :value="profile.api_key_mode"
                      :options="providerApiKeyModeOptions"
                      @update:value="value => setProviderApiKeyMode(index, String(value || 'keep'))"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="14" label="API Key">
                    <NInput
                      :value="profile.api_key_input"
                      type="password"
                      show-password-on="click"
                      :placeholder="profile.api_key_mode === 'replace'
                        ? (profile.api_key_present ? `当前：${profile.api_key_mask || '已保存密钥'}` : '输入新的 api_key')
                        : (profile.api_key_present ? `当前：${profile.api_key_mask || '已保存密钥'}` : '当前未设置密钥')"
                      :disabled="profile.api_key_mode !== 'replace'"
                      @update:value="value => updateProviderDraft(index, { api_key_input: String(value || '') })"
                    />
                  </NFormItemGi>
                </NGrid>
              </NForm>
            </AppCard>
          </div>

          <div class="system-provider-editor__footer">
            <NButton secondary @click="resetProviderDefinitions">
              重置草稿
            </NButton>
            <NSpace :size="10">
              <NButton secondary @click="providerEditorVisible = false">
                关闭
              </NButton>
              <NButton
                type="primary"
                :disabled="!providerDefinitionsDirty"
                :loading="providerDefinitionsSaving"
                @click="saveProviderDefinitions"
              >
                保存定义
              </NButton>
            </NSpace>
          </div>
        </div>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.system-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.95fr);
  gap: 18px;
  overflow: hidden;
  margin-bottom: 24px;
  padding: 24px;
  border-radius: 24px;
  background: var(--om-hero-gradient);
}

.system-hero__eyebrow,
.system-panel__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.system-hero__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: clamp(26px, 3vw, 36px);
  line-height: 1.08;
  letter-spacing: -0.04em;
}

.system-hero__description {
  margin: 14px 0 0;
  color: var(--om-text-2);
  font-size: 15px;
  line-height: 1.8;
}

.system-hero__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.system-hero__aside {
  display: grid;
  gap: 14px;
}

.system-hero__aside-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 116px;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.42);
}

.dark .system-hero__aside-card {
  background: rgba(18, 29, 34, 0.48);
}

.system-hero__aside-label {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.system-hero__aside-value {
  margin-top: 10px;
  color: var(--om-text-1);
  font-size: 18px;
  line-height: 1.4;
}

.system-hero__aside-meta {
  margin-top: 8px;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.system-ops-card {
  display: grid;
  gap: 18px;
  margin-bottom: 24px;
  padding: 20px;
}

.system-ops-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(280px, 0.85fr);
  gap: 16px;
}

.system-ops-panel {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-ops-panel--restart {
  background: linear-gradient(135deg, rgba(var(--primary-color), 0.12), rgba(var(--primary-color), 0.03));
}

.system-ops-panel__intro {
  display: grid;
  gap: 6px;
}

.system-ops-panel__intro strong,
.system-ops-checklist strong {
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-ops-panel__intro p,
.system-ops-panel__hint {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.system-ops-panel__hint,
.system-ops-panel__intro small {
  color: var(--om-text-3);
}

.system-ops-panel__intro small {
  font-size: 12px;
  line-height: 1.6;
}

.system-ops-list,
.system-ops-checklist,
.system-alert-list {
  display: grid;
  gap: 10px;
}

.system-ops-group {
  display: grid;
  gap: 10px;
}

.system-ops-group__title {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.system-ops-list__item,
.system-ops-checklist__item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.system-ops-list__dot,
.system-ops-checklist__index {
  width: 8px;
  height: 8px;
  margin-top: 7px;
  border-radius: 999px;
  background: var(--om-warning);
}

.system-ops-list__dot--impact {
  background: var(--om-primary);
}

.system-ops-list__dot--fit {
  background: var(--om-success);
}

.system-ops-list__dot--warning {
  background: var(--om-warning);
}

.system-ops-checklist__index {
  background: var(--om-success);
}

.system-alert-list {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.system-alert-row {
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-alert-row--error {
  border-color: color-mix(in srgb, var(--om-danger) 32%, var(--om-border));
}

.system-alert-row--warning {
  border-color: color-mix(in srgb, var(--om-warning) 32%, var(--om-border));
}

.system-alert-row__main {
  display: grid;
  gap: 8px;
}

.system-alert-row__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.system-alert-row__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.system-alert-row__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.system-alert-row p,
.system-alert-row small {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.system-alert-row small {
  color: var(--om-text-3);
}

.system-ops-muted,
.system-ops-policy-note {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.7;
}

.system-ops-muted {
  padding: 12px 14px;
  border: 1px dashed var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 28%, transparent);
}

.system-main-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.system-advanced-entry {
  display: grid;
  gap: 16px;
  margin-top: 16px;
  padding: 20px;
}

.system-advanced-entry__description {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.system-advanced-entry__tools {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.system-advanced-entry__tool {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.18s ease,
    transform 0.18s ease,
    background-color 0.18s ease;
}

.system-advanced-entry__tool:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
}

.system-advanced-entry__tool strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.system-advanced-entry__tool span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-observability-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.system-service-health {
  margin-bottom: 24px;
  padding: 20px;
}

.system-service-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.system-service-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.system-service-card--warning,
.system-service-card--degraded {
  border-color: color-mix(in srgb, var(--om-warning) 32%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 10%, var(--om-surface));
}

.system-service-card--error {
  border-color: color-mix(in srgb, var(--om-danger) 32%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 10%, var(--om-surface));
}

.system-service-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-service-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-service-card__head span {
  display: block;
  margin-top: 5px;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-service-card p {
  margin: 12px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-service-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.system-service-card__note {
  display: block;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-runtime-errors {
  margin-bottom: 24px;
  padding: 20px;
}

.system-runtime-errors__summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.system-runtime-errors__summary div {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.system-runtime-errors__summary span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-runtime-errors__summary strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 800;
}

.system-runtime-errors__list {
  display: grid;
  gap: 10px;
}

.system-runtime-error-row {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-runtime-error-row--warning {
  border-color: color-mix(in srgb, var(--om-warning) 30%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 9%, var(--om-surface));
}

.system-runtime-error-row--error,
.system-runtime-error-row--critical {
  border-color: color-mix(in srgb, var(--om-danger) 30%, var(--om-border));
  background: color-mix(in srgb, var(--om-danger) 9%, var(--om-surface));
}

.system-runtime-error-row__main {
  min-width: 0;
}

.system-runtime-error-row__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.system-runtime-error-row__head strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.system-runtime-error-row__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.system-runtime-error-row p {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-runtime-error-row small {
  display: block;
  margin-top: 8px;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-panel {
  min-height: 100%;
  padding: 20px;
}

.system-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.system-panel__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.system-resource-list {
  display: grid;
  gap: 16px;
}

.system-resource {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.system-resource__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-resource__head strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.system-stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.system-stat-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.system-stat-card__label {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-stat-card__value {
  display: block;
  margin-top: 10px;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.system-stack {
  display: grid;
  gap: 14px;
}

.system-stack__item {
  padding: 16px;
  border-radius: 18px;
}

.system-stack__head {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}

.system-stack__head h4 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-stack__head p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-stack__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 14px;
  background: rgba(var(--primary-color), 0.12);
  color: rgb(var(--primary-color));
}

.system-stack__body {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.system-inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-provider-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.system-provider-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-card__label {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.system-provider-card strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 20px;
}

.system-provider-card p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-provider-switcher {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
  padding: 10px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 68%, transparent);
}

.system-provider-switcher > span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 700;
}

.system-provider-runtime {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.system-provider-runtime__item {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 58%, transparent);
}

.system-provider-runtime__item span {
  display: block;
  color: var(--om-text-3);
  font-size: 11px;
}

.system-provider-runtime__item strong {
  display: block;
  margin-top: 6px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-provider-list,
.system-capability-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.system-task-profile-list {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.system-task-profile-row {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 34%, transparent);
}

.system-task-profile-list--editable {
  grid-template-columns: repeat(5, minmax(118px, 1fr));
}

.system-task-profile-row span,
.system-task-profile-row em {
  display: block;
  overflow: hidden;
  color: var(--om-text-3);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-task-profile-row strong {
  display: block;
  overflow: hidden;
  margin: 5px 0 4px;
  color: var(--om-text-1);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-task-profile-row :deep(.n-select) {
  margin: 6px 0;
}

.system-provider-note {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-provider-row,
.system-capability-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 32%, transparent);
}

.system-provider-row__main,
.system-capability-row div {
  min-width: 0;
}

.system-provider-row strong,
.system-capability-row strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-provider-row span,
.system-capability-row span {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-provider-row__main small {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-provider-row__actions {
  display: inline-flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.system-protocol-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.system-protocol-summary div {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.system-protocol-summary span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-protocol-summary strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 18px;
}

.system-trace-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.system-connection-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.system-connection-panel__head,
.system-trace-panel__head,
.system-connection-row,
.system-trace-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.system-connection-panel__head span,
.system-trace-panel__head span,
.system-connection-row span,
.system-trace-row span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-connection-panel__head strong,
.system-trace-panel__head strong,
.system-connection-row strong,
.system-trace-row strong {
  display: block;
  margin-top: 5px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-connection-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 12px;
  color: var(--om-text-2);
  font-size: 12px;
}

.system-connection-error {
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--om-danger) 30%, var(--om-border));
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-danger) 9%, transparent);
  color: var(--om-danger);
  font-size: 12px;
  line-height: 1.6;
}

.system-connection-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.system-connection-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 35%, transparent);
}

.system-connection-row div {
  min-width: 0;
}

.system-connection-row small {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-danger);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-trace-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.system-trace-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 35%, transparent);
}

.system-trace-empty {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-connection-empty {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-compatibility-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 66%, transparent);
}

.system-compatibility-panel__head,
.system-compatibility-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-compatibility-panel__head span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-compatibility-panel__head strong {
  display: block;
  margin-top: 5px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-compatibility-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.system-compatibility-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 35%, transparent);
}

.system-compatibility-row__main {
  min-width: 0;
}

.system-compatibility-row__main strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-compatibility-row__main span {
  display: block;
  margin-top: 5px;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.system-compatibility-row__tags {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.system-link {
  display: inline-flex;
  margin-top: 14px;
  color: rgb(var(--primary-color));
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}

.system-link:hover {
  text-decoration: underline;
}

.system-backup {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  padding: 22px 24px;
}

.system-backup__copy {
  max-width: 720px;
}

.system-backup__description {
  margin: 12px 0 0;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.75;
}

.system-backup__action {
  flex-shrink: 0;
}

.system-provider-editor__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-editor {
  display: grid;
  gap: 16px;
  padding-bottom: 10px;
}

.system-provider-editor__tip {
  border-radius: 16px;
}

.system-provider-editor__toolbar,
.system-provider-editor__footer,
.system-provider-editor__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-editor__list {
  display: grid;
  gap: 12px;
}

.system-provider-editor__card {
  padding: 16px;
}

.system-provider-editor__card-head {
  align-items: flex-start;
  margin-bottom: 12px;
}

.system-provider-editor__card-head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.system-provider-editor__card-head p {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-provider-editor__form :deep(.n-form-item) {
  margin-bottom: 0;
}

.system-provider-editor__footer {
  padding-top: 4px;
}

@media (max-width: 1100px) {
  .system-metric-grid,
  .system-advanced-entry__tools,
  .system-alert-list,
  .system-main-grid,
  .system-observability-grid,
  .system-service-grid,
  .system-runtime-errors__summary,
  .system-task-profile-list,
  .system-provider-runtime {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .system-hero {
    grid-template-columns: 1fr;
  }

  .system-ops-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .system-metric-grid,
  .system-advanced-entry__tools,
  .system-alert-list,
  .system-main-grid,
  .system-observability-grid,
  .system-service-grid,
  .system-runtime-errors__summary,
  .system-provider-runtime,
  .system-task-profile-list,
  .system-protocol-summary,
  .system-stats-grid {
    grid-template-columns: 1fr;
  }

  .system-hero,
  .system-backup {
    padding: 20px;
  }

  .system-backup {
    flex-direction: column;
    align-items: flex-start;
  }

  .system-provider-row,
  .system-provider-switcher,
  .system-provider-editor__header,
  .system-provider-editor__toolbar,
  .system-provider-editor__footer,
  .system-provider-editor__card-head,
  .system-compatibility-panel__head,
  .system-compatibility-row,
  .system-connection-panel__head,
  .system-connection-row {
    flex-direction: column;
    align-items: stretch;
  }

  .system-provider-switcher {
    display: flex;
  }

  .system-provider-row__actions,
  .system-compatibility-row__tags {
    justify-content: flex-start;
  }
}
</style>

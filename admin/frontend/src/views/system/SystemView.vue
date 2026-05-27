<script setup lang="ts">
import { HardwareChipOutline, RefreshOutline } from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import SystemAdvancedEntry from './components/SystemAdvancedEntry.vue'
import SystemHero from './components/SystemHero.vue'
import SystemMaintenance from './components/SystemMaintenance.vue'
import SystemPolicies from './components/SystemPolicies.vue'
import SystemProtocol from './components/SystemProtocol.vue'
import SystemProviderEditorDrawer from './components/SystemProviderEditorDrawer.vue'
import SystemProviders from './components/SystemProviders.vue'
import SystemResources from './components/SystemResources.vue'
import SystemRuntimeErrors from './components/SystemRuntimeErrors.vue'
import SystemServiceHealth from './components/SystemServiceHealth.vue'
import type {
  HealthInfo,
  HumanizerInfo,
  ProtocolConnectionPayload,
  ProtocolHealth,
  ProtocolTracePayload,
  ProviderApiKeyMode,
  ProviderProfile,
  ProviderProfileDraft,
  ProviderTaskKey,
  ProviderTestResult,
  ProvidersInfo,
  RuntimeErrorPayload,
  ServicesHealth,
  SystemInfo,
  TalkScheduleInfo,
  VersionInfo,
} from './helpers/types'

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

const providerTaskOrder: ProviderTaskKey[] = [
  'main',
  'thinker',
  'compact',
  'reply_gate',
  'vision',
  'slang',
  'slang_review',
  'slang_drift',
  'slang_semantic',
  'style',
  'memo',
  'persona_import',
  'chat_private',
  'bilibili_intent',
  'element_detect',
  'graph_review',
  'graph_edge_classifier',
  'reflection_consolidator',
  'episode_summarizer',
  'scheduler_eot',
  'scheduler_replay_judge',
]
const providerNamePattern = /^[A-Za-z0-9_-]+$/

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

const servicesNeedingAttention = computed(() =>
  (servicesHealth.value?.services || []).filter(item => item.status === 'warning' || item.status === 'error').length,
)

const healthAlerts = computed(() => servicesHealth.value?.alerts || [])
const alertPolicy = computed(() => servicesHealth.value?.policy || null)

const maintenanceWindow = computed(() => servicesHealth.value?.maintenance_window || null)

const restartNotice = computed(() => system.value?.restart_notice || null)

const advancedToolLinks: { label: string, path: string, note: string }[] = []

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
  // Moved to ConfigSystemBackup in config page
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
        <SystemHero
          :health="health"
          :system="system"
          :version="version"
          :last-loaded-at="lastLoadedAt"
          :error="error"
        />

        <SystemResources :system="system" />

        <SystemMaintenance
          :maintenance-window="maintenanceWindow"
          :restart-notice="restartNotice"
          :health-alerts="healthAlerts"
          :alert-policy="alertPolicy"
        />

        <SystemServiceHealth
          :services-health="servicesHealth"
          :attention-count="servicesNeedingAttention"
        />

        <SystemRuntimeErrors :runtime-errors="runtimeErrors" />

        <SystemPolicies
          :version="version"
          :humanizer="humanizer"
          :talk-schedule="talkSchedule"
        />

        <SystemAdvancedEntry
          :expanded="showAdvancedConsole"
          :tools="advancedToolLinks"
          @toggle="showAdvancedConsole = !showAdvancedConsole"
          @navigate="openAdvancedTool"
        />

        <template v-if="showAdvancedConsole">
          <div class="system-observability-grid">
            <SystemProviders
              :providers="providers"
              :default-draft="providerDefaultDraft"
              :task-draft="providerTaskDraft"
              :testing="providerTesting"
              :test-results="providerTestResults"
              :selection-saving="providerSelectionSaving"
              :selection-dirty="providerSelectionDirty"
              @update-default-draft="setDefaultProviderDraft"
              @update-task-draft="setTaskProviderDraft"
              @save-selection="saveProviderSelection"
              @test-profile="testProviderProfile"
              @open-editor="openProviderEditor"
            />

            <SystemProtocol
              :protocol="protocol"
              :traces="protocolTraces"
              :connections="protocolConnections"
              :probing="protocolProbing"
              @probe="probeProtocol"
            />
          </div>
        </template>
      </template>
    </NSpin>

    <SystemProviderEditorDrawer
      v-model:show="providerEditorVisible"
      :drafts="providerProfilesDraft"
      :capability-options="providerCapabilityOptions"
      :api-format-options="providerApiFormatOptions"
      :dirty="providerDefinitionsDirty"
      :saving="providerDefinitionsSaving"
      @add="addProviderDraft"
      @remove="removeProviderDraft"
      @patch="updateProviderDraft"
      @set-key-mode="setProviderApiKeyMode"
      @capabilities-change="onProviderCapabilitiesChange"
      @reset="resetProviderDefinitions"
      @save="saveProviderDefinitions"
    />
  </AppPage>
</template>

<style scoped>
.system-observability-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

@media (max-width: 1100px) {
  .system-observability-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .system-observability-grid {
    grid-template-columns: 1fr;
  }
}
</style>

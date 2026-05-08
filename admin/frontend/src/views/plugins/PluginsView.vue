<script setup lang="ts">
import {
  ArrowBackOutline,
  LockClosedOutline,
  RefreshOutline,
  SettingsOutline,
  StorefrontOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

type PluginMode = 'user' | 'system' | 'store' | 'governance'
type PluginDetailTab = 'overview' | 'settings' | 'commands' | 'health' | 'source'
type TagType = 'default' | 'primary' | 'success' | 'warning' | 'info' | 'error'

interface PluginName {
  zh?: string
  en?: string
}

interface PluginHealth {
  name: string
  enabled: boolean
  state: string
  display_state?: string
  display_label?: string
  display_type?: TagType | string
  calls?: number
  errors?: number
  last_error?: string
  last_hook?: string
  last_elapsed_ms?: number
  max_elapsed_ms?: number
  hook_budget_ms?: number
  slow_calls?: number
  permission_denials?: number
  last_permission_denied?: string
  cooldown_reason?: string
  cooldown_remaining_seconds?: number
}

interface PluginPackageInfo {
  name: string
  loaded: boolean
  kind: string
  display_name?: PluginName
  tier?: string
  toggle_policy?: string
  category?: string
  source_status: string
  source_label: string
  package_path: string
  entry_path: string
  manifest_path: string
  config_default_path?: string
  config_schema_path?: string
  config_paths: string[]
  manifest_status: string
  signature_status: string
  source_attestation_status: string
  compatibility_status: string
  governance_status: string
  governance_label: string
  action_hint: string
  warnings: string[]
  store?: Record<string, any>
}

interface PluginIndexPayload {
  summary: {
    indexed_count: number
    loaded_count: number
    warning_count: number
    review_required_count: number
    blocked_count: number
    attention_count: number
  }
  install_policy?: {
    remote_install_enabled: boolean
    detail: string
  }
  store_policy?: {
    remote_install_enabled: boolean
    detail: string
  }
  entries: PluginPackageInfo[]
  plugin_root: string
}

interface Plugin {
  name: string
  display_name?: PluginName
  description?: string
  version: string
  priority: number
  enabled: boolean
  persistent_enabled?: boolean | null
  author?: string
  category?: string
  tier?: 'system' | 'user' | string
  toggle_policy?: 'locked' | 'runtime' | 'restart_required' | string
  locked?: boolean
  configurable?: boolean
  config_status?: 'ready' | 'missing_schema' | 'read_only' | 'legacy_blocked' | string
  permissions?: string[]
  capabilities?: string[]
  config_spec?: Record<string, any>
  store?: Record<string, any>
  health?: PluginHealth
  package?: PluginPackageInfo | null
  hook_budget_ms?: number
  capability_only?: boolean
}

interface Tool {
  plugin: string
  function?: {
    name?: string
    description?: string
  }
}

interface Command {
  plugin: string
  name: string
  description: string
  usage: string
  permission: string
}

interface PluginSettings {
  schema: Record<string, any>
  values: Record<string, any>
  defaults?: Record<string, any>
  effective_values: Record<string, any>
  updated_at?: number
  path?: string
  default_path?: string
  schema_path?: string
  has_saved_values?: boolean
  apply_mode?: string
  requires_restart?: boolean
  restart_required_fields?: string[]
}

interface PluginDetail extends Plugin {
  dependencies?: Record<string, string>
  commands?: Command[]
  tools?: Tool[]
  settings_schema?: Record<string, any>
  settings?: PluginSettings
  error?: string
}

interface PluginMetaPayload {
  plugin_api_version?: number
  plugin_layout_version?: number
  build_commit?: string
  frontend_build_id?: string
  omubot_version?: string
  legacy_detected?: boolean
  legacy_single_file_detected?: boolean
  legacy_plugins?: string[]
  plugin_root?: string
}

interface SettingsField {
  key: string
  label: string
  description: string
  kind: 'switch' | 'text' | 'number' | 'select' | 'list' | 'object-array' | 'json'
  options: Array<{ label: string, value: any }>
  min?: number
  max?: number
  step?: number
  itemFields?: SettingsField[]
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const loading = ref(true)
const refreshing = ref(false)
const detailLoading = ref(false)
const settingsSaving = ref(false)
const plugins = ref<Plugin[]>([])
const pluginIndex = ref<PluginIndexPayload | null>(null)
const pluginStore = ref<PluginIndexPayload | null>(null)
const tools = ref<Tool[]>([])
const commands = ref<Command[]>([])
const selectedDetail = ref<PluginDetail | null>(null)
const detailError = ref('')
const searchText = ref('')
const mode = ref<PluginMode>('user')
const showSystemPlugins = ref(false)
const pluginMeta = ref<PluginMetaPayload | null>(null)
const pluginCenterBlocked = ref(false)
const pluginCenterBlockReason = ref('')
const pluginCenterLegacyPlugins = ref<string[]>([])
const stateChanging = ref<Record<string, boolean>>({})
const settingsDraft = ref<Record<string, any>>({})
const settingsJsonDrafts = ref<Record<string, string>>({})
const settingsOriginalJson = ref('')

const detailName = computed(() => String(route.params.name || ''))
const isDetailRoute = computed(() => Boolean(detailName.value))
const detailTab = computed<PluginDetailTab>(() => {
  const raw = String(route.query.tab || 'overview')
  return ['overview', 'settings', 'commands', 'health', 'source'].includes(raw)
    ? raw as PluginDetailTab
    : 'overview'
})

const modeOptions = [
  { key: 'user', label: '用户插件' },
  { key: 'store', label: '插件商店' },
  { key: 'governance', label: '治理队列' },
] as const

const detailTabOptions = [
  { key: 'overview', label: '概览' },
  { key: 'settings', label: '配置' },
  { key: 'commands', label: '命令工具' },
  { key: 'health', label: '健康' },
  { key: 'source', label: '包来源' },
] as const

const commandCountByPlugin = computed(() => {
  const counts = new Map<string, number>()
  for (const command of commands.value) counts.set(command.plugin, (counts.get(command.plugin) || 0) + 1)
  return counts
})

const toolCountByPlugin = computed(() => {
  const counts = new Map<string, number>()
  for (const tool of tools.value) counts.set(tool.plugin, (counts.get(tool.plugin) || 0) + 1)
  return counts
})

const userPlugins = computed(() => plugins.value.filter(plugin => plugin.tier !== 'system'))
const systemPlugins = computed(() => plugins.value.filter(plugin => plugin.tier === 'system' || plugin.locked))

const configurableCount = computed(() => plugins.value.filter(plugin => plugin.configurable).length)
const attentionCount = computed(() => plugins.value.filter(plugin => pluginNeedsAttention(plugin)).length)
const lockedCount = computed(() => {
  const indexedSystem = pluginIndex.value?.entries?.filter(entry => entry.tier === 'system').length || 0
  return indexedSystem || systemPlugins.value.length
})

const pluginSummaryItems = computed(() => [
  {
    label: '用户插件',
    value: userPlugins.value.length,
    hint: '日常可管理',
  },
  {
    label: '可配置',
    value: configurableCount.value,
    hint: '已接入 Web 表单',
  },
  {
    label: '需关注',
    value: attentionCount.value,
    hint: '异常、保护或治理提示',
  },
  {
    label: '系统锁定',
    value: lockedCount.value,
    hint: '默认隐藏，只读展示',
  },
])

const visiblePlugins = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  const source = mode.value === 'system' ? systemPlugins.value : userPlugins.value
  return [...source]
    .filter((plugin) => {
      if (!query) return true
      const display = plugin.display_name || {}
      const haystack = [
        plugin.name,
        display.zh,
        display.en,
        plugin.description,
        plugin.category,
        ...(plugin.capabilities || []),
      ].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(query)
    })
    .sort((left, right) => {
      if (left.enabled !== right.enabled) return left.enabled ? -1 : 1
      if (left.priority !== right.priority) return left.priority - right.priority
      return displayName(left).localeCompare(displayName(right), 'zh-CN')
    })
})

const storeEntries = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  return [...(pluginStore.value?.entries || pluginIndex.value?.entries || [])]
    .filter((entry) => {
      if (!query) return true
      const display = entry.display_name || {}
      return [entry.name, display.zh, display.en, entry.kind, entry.source_label, entry.governance_label]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query)
    })
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
})

const governanceQueue = computed(() => {
  const order: Record<string, number> = { blocked: 0, review: 1, attention: 2, ready: 3, healthy: 4 }
  return storeEntries.value
    .filter(entry => entry.governance_status !== 'healthy')
    .sort((left, right) => (order[left.governance_status] ?? 99) - (order[right.governance_status] ?? 99))
})

const selectedSettingsSchema = computed(() =>
  selectedDetail.value?.settings?.schema || selectedDetail.value?.settings_schema || {},
)

const selectedSettingFields = computed(() => schemaFields(selectedSettingsSchema.value))

const settingsDirty = computed(() =>
  JSON.stringify(settingsDraft.value) !== settingsOriginalJson.value
  || selectedSettingFields.value.some((field) => {
    if (field.kind !== 'json') return false
    return settingsJsonDrafts.value[field.key] !== stringifyJson(settingsDraft.value[field.key])
  }),
)

watch(
  () => route.params.name,
  (name) => {
    if (name) void loadPluginDetail(String(name))
    else selectedDetail.value = null
  },
  { immediate: true },
)

onMounted(() => {
  void loadPlugins()
})

async function loadPlugins(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true
  try {
    const metaPayload = await api('/api/admin/plugins/meta')
    pluginMeta.value = metaPayload
    const legacyDetected = Boolean(metaPayload?.legacy_detected || metaPayload?.legacy_single_file_detected)
    pluginCenterBlocked.value = legacyDetected
    pluginCenterLegacyPlugins.value = Array.isArray(metaPayload?.legacy_plugins)
      ? metaPayload.legacy_plugins
      : []
    pluginCenterBlockReason.value = legacyDetected
      ? '检测到旧版根目录单文件插件，插件中心已阻断。请先迁移后重启。'
      : ''
    if (legacyDetected) {
      plugins.value = []
      tools.value = []
      commands.value = []
      pluginIndex.value = null
      pluginStore.value = null
      selectedDetail.value = null
      if (detailName.value) await router.replace({ path: '/plugins' })
      return
    }

    const results = await Promise.allSettled([
      api(`/api/admin/plugins${showSystemPlugins.value ? '?include_system=true' : ''}`),
      api('/api/admin/plugins/index'),
      api('/api/admin/plugins/store'),
      api('/api/admin/tools'),
      api('/api/admin/commands'),
    ])
    if (
      results[0].status === 'fulfilled'
      && results[0].value?.blocked
    ) {
      pluginCenterBlocked.value = true
      pluginCenterBlockReason.value = String(results[0].value?.error || '插件中心已阻断，请先完成插件布局迁移。')
      pluginCenterLegacyPlugins.value = Array.isArray(results[0].value?.meta?.legacy_plugins)
        ? results[0].value.meta.legacy_plugins
        : pluginCenterLegacyPlugins.value
      plugins.value = []
      tools.value = []
      commands.value = []
      pluginIndex.value = null
      pluginStore.value = null
      selectedDetail.value = null
      if (detailName.value) await router.replace({ path: '/plugins' })
      return
    }
    plugins.value = results[0].status === 'fulfilled' ? (results[0].value.plugins || []) : []
    pluginIndex.value = results[1].status === 'fulfilled' ? results[1].value : null
    pluginStore.value = results[2].status === 'fulfilled' ? results[2].value : null
    tools.value = results[3].status === 'fulfilled' ? (results[3].value.tools || []) : []
    commands.value = results[4].status === 'fulfilled' ? (results[4].value.commands || []) : []
    if (detailName.value) await loadPluginDetail(detailName.value, true)
  } catch {
    message.error('插件中心加载失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function loadPluginDetail(name: string, silent = false) {
  if (pluginCenterBlocked.value) return
  if (!silent) detailLoading.value = true
  detailError.value = ''
  try {
    const data = await api(`/api/admin/plugins/${encodeURIComponent(name)}`)
    if (data.error) throw new Error(data.error)
    selectedDetail.value = data
    hydrateSettingsDraft(data.settings, data.settings_schema)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '插件详情加载失败'
    selectedDetail.value = null
  } finally {
    detailLoading.value = false
  }
}

async function goDetail(plugin: Plugin) {
  await router.push({ path: `/plugins/${encodeURIComponent(plugin.name)}`, query: { tab: 'overview' } })
}

async function goSettings(plugin: Plugin) {
  await router.push({ path: `/plugins/${encodeURIComponent(plugin.name)}`, query: { tab: 'settings' } })
}

async function goBack() {
  await router.push({ path: '/plugins' })
}

async function goSlangSettings() {
  await router.push({ path: '/slang' })
}

async function setDetailTab(tab: PluginDetailTab) {
  await router.replace({ path: route.path, query: { ...route.query, tab } })
}

async function toggleSystemPlugins() {
  showSystemPlugins.value = !showSystemPlugins.value
  mode.value = showSystemPlugins.value ? 'system' : 'user'
  await loadPlugins(true)
}

async function setPluginEnabled(plugin: Plugin, enabled: boolean) {
  if (plugin.locked || plugin.tier === 'system') {
    message.warning('系统级插件无法关闭')
    return
  }
  stateChanging.value = { ...stateChanging.value, [plugin.name]: true }
  try {
    const data = await api(`/api/admin/plugins/${encodeURIComponent(plugin.name)}/state`, {
      method: 'POST',
      body: { enabled },
    })
    if (!data.ok) throw new Error(data.error || '状态切换失败')
    message.success(enabled ? '插件已启用' : '插件已停用')
    await loadPlugins(true)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '状态切换失败')
  } finally {
    stateChanging.value = { ...stateChanging.value, [plugin.name]: false }
  }
}

async function saveSettings() {
  if (!selectedDetail.value) return
  const values = materializeSettingsDraft()
  if (values === null) return

  settingsSaving.value = true
  try {
    const data = await api(`/api/admin/plugins/${encodeURIComponent(selectedDetail.value.name)}/settings`, {
      method: 'POST',
      body: { values },
    })
    if (!data.ok) throw new Error(data.error || '配置保存失败')
    selectedDetail.value = {
      ...selectedDetail.value,
      settings: data.settings,
    }
    hydrateSettingsDraft(data.settings, data.settings?.schema)
    if (data.requires_restart) message.warning('配置已保存，需要在线重启后生效')
    else message.success('配置已保存')
    await loadPlugins(true)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '配置保存失败')
  } finally {
    settingsSaving.value = false
  }
}

function displayName(plugin: Plugin | PluginPackageInfo | PluginDetail) {
  return plugin.display_name?.zh || plugin.name
}

function englishName(plugin: Plugin | PluginPackageInfo | PluginDetail) {
  return plugin.display_name?.en || plugin.name
}

function pluginNeedsAttention(plugin: Plugin) {
  const state = plugin.health?.state || ''
  const packageStatus = plugin.package?.governance_status || ''
  return ['degraded', 'throttled', 'failed'].includes(state) || ['blocked', 'review', 'attention'].includes(packageStatus)
}

function healthLabel(plugin: Plugin | PluginDetail) {
  if (!plugin.enabled) return '已停用'
  if (plugin.health?.display_label) return plugin.health.display_label
  const state = plugin.health?.state || 'healthy'
  if (state === 'healthy') return '健康'
  if (state === 'permission_limited') return '按权限运行'
  if (state === 'throttled') return '已保护'
  if (state === 'degraded') return '需关注'
  return '状态未知'
}

function asTagType(value: unknown, fallback: TagType = 'default'): TagType {
  return ['default', 'primary', 'success', 'warning', 'info', 'error'].includes(String(value))
    ? String(value) as TagType
    : fallback
}

function healthType(plugin: Plugin | PluginDetail): TagType {
  if (!plugin.enabled) return 'default'
  if (plugin.health?.display_type) return asTagType(plugin.health.display_type)
  const state = plugin.health?.state || 'healthy'
  if (state === 'healthy') return 'success'
  if (state === 'permission_limited') return 'info'
  if (state === 'throttled') return 'warning'
  if (state === 'degraded') return 'warning'
  return 'error'
}

function configStatusLabel(plugin: Plugin | PluginDetail) {
  if (plugin.config_status === 'legacy_blocked') return '旧布局阻断'
  if (plugin.config_status === 'read_only') return '只读'
  if (plugin.config_status === 'missing_schema') return '缺少 Schema'
  if (plugin.config_status === 'ready') return '可配置'
  return plugin.configurable ? '可配置' : '只读'
}

function configStatusType(plugin: Plugin | PluginDetail) {
  if (plugin.config_status === 'legacy_blocked') return 'error'
  if (plugin.config_status === 'missing_schema') return 'warning'
  if (plugin.config_status === 'ready') return 'success'
  return 'info'
}

function governanceType(status: string) {
  if (status === 'blocked') return 'error'
  if (status === 'review' || status === 'attention') return 'warning'
  if (status === 'ready') return 'info'
  return 'success'
}

function schemaType(schema: Record<string, any>) {
  const rawType = schema.type
  if (Array.isArray(rawType)) return rawType.find(item => item !== 'null') || rawType[0]
  return rawType
}

function schemaFields(schema: Record<string, any>): SettingsField[] {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object') return []
  return Object.entries(properties).map(([key, rawSchema]) => {
    const fieldSchema = (rawSchema && typeof rawSchema === 'object' ? rawSchema : {}) as Record<string, any>
    const type = schemaType(fieldSchema)
    const enumValues = Array.isArray(fieldSchema.enum) ? fieldSchema.enum : []
    const enumNames = Array.isArray(fieldSchema.enumNames) ? fieldSchema.enumNames : []
    const itemType = schemaType((fieldSchema.items || {}) as Record<string, any>)
    let kind: SettingsField['kind'] = 'text'
    if (enumValues.length) kind = 'select'
    else if (type === 'boolean') kind = 'switch'
    else if (type === 'integer' || type === 'number') kind = 'number'
    else if (type === 'array' && (!itemType || itemType === 'string')) kind = 'list'
    else if (type === 'array' && itemType === 'object') kind = 'object-array'
    else if (type === 'object' || type === 'array') kind = 'json'
    return {
      key,
      label: String(fieldSchema.title || key),
      description: String(fieldSchema.description || ''),
      kind,
      options: enumValues.map((value, index) => ({ label: String(enumNames[index] || value), value })),
      min: typeof fieldSchema.minimum === 'number' ? fieldSchema.minimum : undefined,
      max: typeof fieldSchema.maximum === 'number' ? fieldSchema.maximum : undefined,
      step: typeof fieldSchema.multipleOf === 'number' ? fieldSchema.multipleOf : type === 'integer' ? 1 : undefined,
      itemFields: kind === 'object-array' ? schemaFields((fieldSchema.items || {}) as Record<string, any>) : undefined,
    }
  })
}

function stringifyJson(value: any) {
  return JSON.stringify(value ?? {}, null, 2)
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value ?? null)) as T
}

function hydrateSettingsDraft(settings?: PluginSettings, schema?: Record<string, any>) {
  const draft = deepClone(settings?.effective_values || settings?.values || {})
  settingsDraft.value = draft
  settingsOriginalJson.value = JSON.stringify(draft)
  const jsonDrafts: Record<string, string> = {}
  for (const field of schemaFields(schema || settings?.schema || {})) {
    if (field.kind === 'json') jsonDrafts[field.key] = stringifyJson(draft[field.key])
  }
  settingsJsonDrafts.value = jsonDrafts
}

function materializeSettingsDraft() {
  const values = deepClone(settingsDraft.value)
  for (const field of selectedSettingFields.value) {
    if (field.kind !== 'json') continue
    try {
      values[field.key] = JSON.parse(settingsJsonDrafts.value[field.key] || '{}')
    } catch {
      message.error(`${field.label} 不是合法 JSON`)
      return null
    }
  }
  return values
}

function setSettingValue(key: string, value: any) {
  settingsDraft.value = { ...settingsDraft.value, [key]: value }
}

function setSettingJsonValue(key: string, value: string) {
  settingsJsonDrafts.value = { ...settingsJsonDrafts.value, [key]: value }
}

function setSettingListValue(key: string, value: string[]) {
  setSettingValue(key, value)
}

function ensureObjectArray(key: string) {
  const value = settingsDraft.value[key]
  return Array.isArray(value) ? value : []
}

function defaultObjectForField(field: SettingsField) {
  const item: Record<string, any> = {}
  for (const child of field.itemFields || []) {
    if (child.kind === 'switch') item[child.key] = false
    else if (child.kind === 'number') item[child.key] = child.min ?? 0
    else if (child.kind === 'list') item[child.key] = []
    else item[child.key] = ''
  }
  return item
}

function objectFieldInputType(field: SettingsField) {
  return ['reply', 'description', 'prompt', 'template'].includes(field.key) ? 'textarea' : 'text'
}

function addObjectArrayItem(field: SettingsField) {
  const rows = [...ensureObjectArray(field.key), defaultObjectForField(field)]
  setSettingValue(field.key, rows)
}

function removeObjectArrayItem(key: string, index: number) {
  const rows = [...ensureObjectArray(key)]
  rows.splice(index, 1)
  setSettingValue(key, rows)
}

function setObjectArrayValue(key: string, index: number, childKey: string, value: any) {
  const rows = [...ensureObjectArray(key)]
  const current = { ...(rows[index] || {}) }
  current[childKey] = value
  rows[index] = current
  setSettingValue(key, rows)
}

function resetSettingsDraft() {
  hydrateSettingsDraft(selectedDetail.value?.settings, selectedSettingsSchema.value)
}

function formatCount(value: number | undefined) {
  return String(value || 0)
}
</script>

<template>
  <AppPage
    title="插件中心"
    description="管理用户插件、查看系统级能力，并为后续本地插件商店保留规范入口。"
  >
    <template #actions>
      <NButton secondary :loading="refreshing" @click="loadPlugins(true)">
        <template #icon><NIcon :component="RefreshOutline" /></template>
        刷新
      </NButton>
    </template>

    <AppCard v-if="pluginCenterBlocked" elevated bordered class="plugin-blocked-card">
      <EmptyState
        title="插件中心已阻断"
        :description="pluginCenterBlockReason || '检测到旧版根目录单文件插件，需先完成目录化迁移。'"
      />
      <div class="meta-grid wide blocked-meta">
        <span>构建 Commit</span><strong>{{ pluginMeta?.build_commit || 'unknown' }}</strong>
        <span>前端 Build ID</span><strong>{{ pluginMeta?.frontend_build_id || 'unknown' }}</strong>
        <span>插件 API 版本</span><strong>{{ pluginMeta?.plugin_api_version ?? '-' }}</strong>
        <span>插件布局版本</span><strong>{{ pluginMeta?.plugin_layout_version ?? '-' }}</strong>
        <span>插件根目录</span><strong>{{ pluginMeta?.plugin_root || 'plugins' }}</strong>
        <span>检测到 legacy 插件</span>
        <strong>{{ pluginCenterLegacyPlugins.length ? pluginCenterLegacyPlugins.join(', ') : '未知' }}</strong>
      </div>
    </AppCard>

    <div v-else-if="isDetailRoute" class="plugin-detail-page">
      <AppCard elevated bordered class="plugin-detail-card">
        <div class="detail-hero">
          <div class="detail-hero-top">
            <NButton secondary size="medium" @click="goBack">
              <template #icon><NIcon :component="ArrowBackOutline" /></template>
              返回插件中心
            </NButton>
            <NSpace v-if="selectedDetail" align="center">
              <NTag :type="healthType(selectedDetail)" round>{{ healthLabel(selectedDetail) }}</NTag>
              <NTag :type="configStatusType(selectedDetail)" round>{{ configStatusLabel(selectedDetail) }}</NTag>
              <NTag v-if="selectedDetail.locked || selectedDetail.tier === 'system'" type="info" round>系统级 / 锁定 / 不可关闭</NTag>
            </NSpace>
          </div>
          <div class="detail-heading">
            <div>
              <h2>{{ selectedDetail ? displayName(selectedDetail) : detailName }}</h2>
              <p>{{ selectedDetail?.description || detailError || '正在读取插件详情' }}</p>
            </div>
            <div v-if="selectedDetail" class="detail-identity">
              <span>{{ englishName(selectedDetail) }}</span>
              <strong>{{ selectedDetail.name }}</strong>
              <span>v{{ selectedDetail.version }} · {{ selectedDetail.category || 'general' }}</span>
            </div>
          </div>
        </div>

        <NSpin :show="detailLoading">
          <EmptyState v-if="detailError" title="插件详情不可用" :description="detailError" compact />
          <div v-else-if="selectedDetail" class="detail-content">
            <div class="detail-tabs" role="tablist">
              <button
                v-for="item in detailTabOptions"
                :key="item.key"
                type="button"
                :class="{ active: detailTab === item.key }"
                @click="setDetailTab(item.key)"
              >
                {{ item.label }}
              </button>
            </div>

            <section v-if="detailTab === 'overview'" class="detail-panel">
              <h3>概览</h3>
              <div class="meta-grid">
                <span>英文名</span><strong>{{ englishName(selectedDetail) }}</strong>
                <span>插件 ID</span><strong>{{ selectedDetail.name }}</strong>
                <span>版本</span><strong>v{{ selectedDetail.version }}</strong>
                <span>分类</span><strong>{{ selectedDetail.category || 'general' }}</strong>
                <span>级别</span><strong>{{ selectedDetail.tier === 'system' ? '系统级（不可关闭）' : '用户级' }}</strong>
                <span>命令</span><strong>{{ selectedDetail.commands?.length || 0 }}</strong>
                <span>工具</span><strong>{{ selectedDetail.tools?.length || 0 }}</strong>
              </div>
              <NDivider />
              <NSpace>
                <NTag v-for="cap in selectedDetail.capabilities || []" :key="cap" round>{{ cap }}</NTag>
              </NSpace>
            </section>

            <section v-else-if="detailTab === 'settings'" class="detail-panel">
              <div class="panel-title-row">
                <h3>配置</h3>
                <NSpace align="center">
                  <NButton v-if="selectedDetail.name === 'slang'" text @click="goSlangSettings">打开黑话设置</NButton>
                  <NTag v-if="selectedDetail.locked || selectedDetail.settings?.apply_mode === 'read_only'" type="info" round>只读</NTag>
                  <NTag v-else-if="selectedDetail.settings?.requires_restart" type="warning" round>保存后需重启</NTag>
                  <NTag v-else-if="selectedSettingFields.length" type="success" round>可在线保存</NTag>
                </NSpace>
              </div>

              <EmptyState
                v-if="!selectedSettingFields.length"
                :title="selectedDetail.config_status === 'missing_schema' ? '缺少配置 Schema' : '此插件配置只读'"
                :description="selectedDetail.config_status === 'missing_schema'
                  ? '该插件未声明 JSON Schema，暂时无法生成结构化配置表单。'
                  : '系统级能力默认不开放 Web 修改；用户插件需要配置时会在这里显示结构化表单。'"
                compact
              />
              <div v-else class="settings-shell">
                <div class="settings-status-strip">
                  <div>
                    <strong>{{ selectedSettingFields.length }} 项可配置</strong>
                    <span>{{ selectedDetail.settings?.requires_restart ? '保存后需要在线重启生效' : '保存后可热更新生效' }}</span>
                  </div>
                  <NTag :type="selectedDetail.settings?.requires_restart ? 'warning' : 'success'" round>
                    {{ selectedDetail.settings?.requires_restart ? '需重启' : '热更新' }}
                  </NTag>
                </div>

                <div class="settings-form">
                  <div
                    v-for="field in selectedSettingFields"
                    :key="field.key"
                    class="setting-row"
                    :class="{ 'setting-row--object-array': field.kind === 'object-array' }"
                  >
                    <div class="setting-copy">
                      <strong>{{ field.label }}</strong>
                      <span>{{ field.description || field.key }}</span>
                    </div>
                    <div class="setting-control-wrap">
                      <NSwitch
                        v-if="field.kind === 'switch'"
                        :value="Boolean(settingsDraft[field.key])"
                        @update:value="value => setSettingValue(field.key, value)"
                      />
                      <NInputNumber
                        v-else-if="field.kind === 'number'"
                        class="setting-control"
                        :value="Number(settingsDraft[field.key] ?? 0)"
                        :min="field.min"
                        :max="field.max"
                        :step="field.step"
                        @update:value="value => setSettingValue(field.key, value)"
                      />
                      <NSelect
                        v-else-if="field.kind === 'select'"
                        class="setting-control"
                        :value="settingsDraft[field.key]"
                        :options="field.options"
                        @update:value="value => setSettingValue(field.key, value)"
                      />
                      <NDynamicTags
                        v-else-if="field.kind === 'list'"
                        class="setting-control"
                        :value="settingsDraft[field.key] || []"
                        @update:value="(value: string[]) => setSettingListValue(field.key, value)"
                      />
                      <div v-else-if="field.kind === 'object-array'" class="setting-control object-array-editor">
                        <div
                          v-for="(item, index) in ensureObjectArray(field.key)"
                          :key="`${field.key}-${index}`"
                          class="object-array-item"
                        >
                          <div class="object-array-title">
                            <div>
                              <strong>{{ field.label }} {{ index + 1 }}</strong>
                              <span>结构化规则，不需要手写 JSON</span>
                            </div>
                            <NButton quaternary size="small" @click="removeObjectArrayItem(field.key, index)">删除</NButton>
                          </div>
                          <div class="object-field-grid">
                            <label
                              v-for="child in field.itemFields || []"
                              :key="child.key"
                              class="object-field"
                              :class="[
                                `object-field--${child.kind}`,
                                { 'object-field--long': ['reply', 'description'].includes(child.key) },
                              ]"
                            >
                              <span>{{ child.label }}</span>
                              <NSwitch
                                v-if="child.kind === 'switch'"
                                :value="Boolean(item[child.key])"
                                @update:value="value => setObjectArrayValue(field.key, index, child.key, value)"
                              />
                              <NInputNumber
                                v-else-if="child.kind === 'number'"
                                :value="Number(item[child.key] ?? 0)"
                                :min="child.min"
                                :max="child.max"
                                :step="child.step"
                                @update:value="value => setObjectArrayValue(field.key, index, child.key, value)"
                              />
                              <NInput
                                v-else
                                :value="String(item[child.key] ?? '')"
                                :type="objectFieldInputType(child)"
                                :autosize="objectFieldInputType(child) === 'textarea' ? { minRows: child.key === 'reply' ? 2 : 1, maxRows: 6 } : undefined"
                                @update:value="value => setObjectArrayValue(field.key, index, child.key, value)"
                              />
                            </label>
                          </div>
                        </div>
                        <NButton dashed block size="medium" @click="addObjectArrayItem(field)">新增规则</NButton>
                      </div>
                      <NInput
                        v-else-if="field.kind === 'json'"
                        class="setting-control"
                        type="textarea"
                        :autosize="{ minRows: 4, maxRows: 10 }"
                        :value="settingsJsonDrafts[field.key]"
                        @update:value="value => setSettingJsonValue(field.key, value)"
                      />
                      <NInput
                        v-else
                        class="setting-control"
                        :value="String(settingsDraft[field.key] ?? '')"
                        @update:value="value => setSettingValue(field.key, value)"
                      />
                    </div>
                  </div>
                </div>

                <div class="settings-actions">
                  <span>覆盖文件：{{ selectedDetail.settings?.path || 'storage/plugins/config/<name>.json' }}</span>
                  <NSpace>
                    <NButton secondary size="medium" :disabled="!settingsDirty" @click="resetSettingsDraft">重置</NButton>
                    <NButton type="primary" size="medium" :loading="settingsSaving" :disabled="!settingsDirty" @click="saveSettings">
                      保存配置
                    </NButton>
                  </NSpace>
                </div>
              </div>
            </section>

            <section v-else-if="detailTab === 'commands'" class="detail-panel">
              <h3>命令工具</h3>
              <div class="runtime-list">
                <article v-for="command in selectedDetail.commands || []" :key="command.name">
                  <strong>/{{ command.name }}</strong>
                  <span>{{ command.description || command.usage || '无说明' }}</span>
                </article>
                <article v-for="tool in selectedDetail.tools || []" :key="tool.function?.name || tool.plugin">
                  <strong>{{ tool.function?.name || 'tool' }}</strong>
                  <span>{{ tool.function?.description || '无说明' }}</span>
                </article>
              </div>
              <EmptyState
                v-if="!(selectedDetail.commands?.length || selectedDetail.tools?.length)"
                title="没有公开命令或工具"
                compact
              />
            </section>

            <section v-else-if="detailTab === 'health'" class="detail-panel">
              <h3>健康</h3>
              <div class="meta-grid wide">
                <span>展示状态</span><strong>{{ healthLabel(selectedDetail) }}</strong>
                <span>原始状态</span><strong>{{ selectedDetail.health?.state || 'healthy' }}</strong>
                <span>最近 Hook</span><strong>{{ selectedDetail.health?.last_hook || '暂无' }}</strong>
                <span>权限跳过</span><strong>{{ selectedDetail.health?.permission_denials || 0 }}</strong>
                <span>最近跳过权限</span><strong>{{ selectedDetail.health?.last_permission_denied || '暂无' }}</strong>
                <span>错误数</span><strong>{{ selectedDetail.health?.errors || 0 }}</strong>
                <span>调用数</span><strong>{{ selectedDetail.health?.calls || 0 }}</strong>
                <span>耗时预算</span><strong>{{ selectedDetail.health?.hook_budget_ms || selectedDetail.hook_budget_ms || 0 }} ms</strong>
                <span>最后错误</span><strong>{{ selectedDetail.health?.last_error || '暂无' }}</strong>
              </div>
            </section>

            <section v-else class="detail-panel">
              <h3>包来源</h3>
              <div class="meta-grid wide">
                <span>入口</span><strong>{{ selectedDetail.package?.entry_path || selectedDetail.package?.source_label || '系统能力声明' }}</strong>
                <span>Manifest</span><strong>{{ selectedDetail.package?.manifest_path || '未声明' }}</strong>
                <span>默认配置</span><strong>{{ selectedDetail.package?.config_default_path || '无' }}</strong>
                <span>Schema</span><strong>{{ selectedDetail.package?.config_schema_path || '无' }}</strong>
                <span>Store</span><strong>{{ selectedDetail.store?.visibility || 'local' }}</strong>
                <span>治理状态</span><strong>{{ selectedDetail.package?.governance_label || '运行正常' }}</strong>
              </div>
            </section>
          </div>
        </NSpin>
      </AppCard>
    </div>

    <template v-else>
      <div class="plugin-summary-strip" aria-label="插件中心摘要">
        <div v-for="item in pluginSummaryItems" :key="item.label" class="summary-pill">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
          <small>{{ item.hint }}</small>
        </div>
      </div>

      <AppCard elevated bordered>
        <PageToolbar>
          <template #left>
            <div class="plugin-tabs" role="tablist">
              <button
                v-for="item in modeOptions"
                :key="item.key"
                type="button"
                :class="{ active: mode === item.key }"
                @click="mode = item.key"
              >
                {{ item.label }}
              </button>
            </div>
            <NInput v-model:value="searchText" clearable placeholder="搜索中文名、英文名或插件 ID" class="plugin-search" />
          </template>
          <template #right>
            <NButton
              size="medium"
              secondary
              :type="showSystemPlugins ? 'primary' : 'default'"
              @click="toggleSystemPlugins"
            >
              {{ showSystemPlugins ? '隐藏系统插件' : '显示系统插件' }}
            </NButton>
            <NText depth="3" class="toolbar-note">本地只读商店，不执行远程安装</NText>
          </template>
        </PageToolbar>

        <NSpin :show="loading">
          <div v-if="mode === 'store'" class="store-view">
            <div class="store-banner">
              <NIcon :component="StorefrontOutline" />
              <div>
                <strong>插件商店预留区</strong>
                <span>{{ pluginStore?.store_policy?.detail || pluginStore?.install_policy?.detail || '当前只索引本地插件包。' }}</span>
              </div>
            </div>
            <div class="package-grid">
              <article v-for="entry in storeEntries" :key="entry.name" class="package-card">
                <div class="card-title-row">
                  <div>
                    <strong>{{ entry.display_name?.zh || entry.name }}</strong>
                    <span>{{ entry.display_name?.en || entry.name }} · {{ entry.name }}</span>
                  </div>
                  <NTag :type="governanceType(entry.governance_status)" round>{{ entry.governance_label }}</NTag>
                </div>
                <p>{{ entry.source_label }}</p>
                <div class="card-meta">
                  <span>{{ entry.kind }}</span>
                  <span>manifest: {{ entry.manifest_status }}</span>
                  <span>source: {{ entry.source_status }}</span>
                </div>
              </article>
            </div>
            <EmptyState v-if="!storeEntries.length" title="没有匹配的插件包" compact />
          </div>

          <div v-else-if="mode === 'governance'" class="governance-list">
            <article v-for="entry in governanceQueue" :key="entry.name" class="governance-item">
              <div>
                <strong>{{ entry.display_name?.zh || entry.name }}</strong>
                <span>{{ entry.action_hint || entry.source_label }}</span>
              </div>
              <NTag :type="governanceType(entry.governance_status)" round>{{ entry.governance_label }}</NTag>
            </article>
            <EmptyState v-if="!governanceQueue.length" title="治理队列为空" description="本地插件包来源、manifest 与兼容性暂未发现阻塞项。" compact />
          </div>

          <div v-else class="plugin-grid">
            <article
              v-for="plugin in visiblePlugins"
              :key="plugin.name"
              class="plugin-card"
              :class="{ 'plugin-card--system': plugin.locked || plugin.tier === 'system' }"
              @click="goDetail(plugin)"
            >
              <div class="plugin-card-main">
                <div class="card-title-row">
                  <div class="plugin-title-block">
                    <strong>{{ displayName(plugin) }}</strong>
                    <span>{{ englishName(plugin) }} · {{ plugin.name }}</span>
                  </div>
                  <NTag :type="healthType(plugin)" round>{{ healthLabel(plugin) }}</NTag>
                </div>
                <p class="plugin-description">{{ plugin.description || '暂无说明' }}</p>
                <div class="plugin-card-tags">
                  <NTag v-if="plugin.locked || plugin.tier === 'system'" type="info" round>
                    <template #icon><NIcon :component="LockClosedOutline" /></template>
                    系统级 / 锁定 / 不可关闭
                  </NTag>
                  <NTag :type="configStatusType(plugin)" round>
                    <template #icon><NIcon :component="SettingsOutline" /></template>
                    {{ configStatusLabel(plugin) }}
                  </NTag>
                  <NTag round>{{ plugin.category || 'general' }}</NTag>
                </div>
              </div>
              <div class="plugin-card-footer">
                <div class="plugin-card-stats">
                  <span>v{{ plugin.version }}</span>
                  <span>工具 {{ formatCount(toolCountByPlugin.get(plugin.name)) }}</span>
                  <span>命令 {{ formatCount(commandCountByPlugin.get(plugin.name)) }}</span>
                </div>
              </div>
              <div class="plugin-card-actions" @click.stop>
                <div class="plugin-action-buttons">
                  <NButton secondary size="medium" @click="goDetail(plugin)">详情</NButton>
                  <NButton
                    v-if="plugin.configurable"
                    secondary
                    size="medium"
                    type="primary"
                    @click="goSettings(plugin)"
                  >
                    配置
                  </NButton>
                </div>
                <div class="plugin-state-control">
                  <NSwitch
                    v-if="plugin.tier !== 'system' && !plugin.locked"
                    :value="plugin.enabled"
                    :loading="stateChanging[plugin.name]"
                    @update:value="value => setPluginEnabled(plugin, value)"
                  />
                  <NTag v-else type="info" round>系统锁定</NTag>
                </div>
              </div>
            </article>
          </div>

          <EmptyState
            v-if="mode !== 'store' && mode !== 'governance' && !visiblePlugins.length"
            title="没有匹配的插件"
            description="试试清空搜索；系统级能力默认隐藏，可通过右侧入口查看。"
            compact
          />
        </NSpin>
      </AppCard>
    </template>
  </AppPage>
</template>

<style scoped>
.plugin-summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.summary-pill {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas:
    "label value"
    "hint value";
  gap: 2px 12px;
  align-items: center;
  padding: 14px 16px;
  border: 1px solid rgba(49, 108, 114, 0.14);
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(49, 108, 114, 0.10), rgba(255, 255, 255, 0.40)),
    var(--om-surface-solid);
}

.summary-pill span {
  grid-area: label;
  color: var(--om-text-2);
  font-weight: 600;
}

.summary-pill strong {
  grid-area: value;
  color: var(--om-text-1);
  font-size: 26px;
  line-height: 1;
}

.summary-pill small {
  grid-area: hint;
  color: var(--om-text-3);
}

.plugin-blocked-card {
  display: grid;
  gap: 16px;
}

.blocked-meta {
  padding: 16px;
  border-radius: 14px;
  background: var(--om-surface-2);
}

.plugin-tabs,
.detail-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 5px;
  border: 1px solid rgba(49, 108, 114, 0.16);
  border-radius: 999px;
  background: rgba(49, 108, 114, 0.08);
}

.plugin-tabs button,
.detail-tabs button {
  min-height: 34px;
  border: 0;
  border-radius: 999px;
  padding: 8px 15px;
  color: var(--om-text-2);
  background: transparent;
  cursor: pointer;
  transition: all 0.18s ease;
}

.plugin-tabs button.active,
.detail-tabs button.active {
  color: rgb(var(--primary-color));
  background: var(--om-surface-solid);
  box-shadow: 0 8px 20px rgba(23, 42, 48, 0.08);
}

.plugin-search {
  width: min(320px, 100%);
}

.toolbar-note {
  white-space: nowrap;
}

.plugin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.package-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}

.plugin-card,
.package-card,
.governance-item,
.detail-panel,
.store-banner {
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: var(--om-surface-solid);
}

.plugin-card,
.package-card {
  position: relative;
  display: grid;
  min-height: 168px;
  padding: 16px 16px 14px 18px;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.plugin-card {
  grid-template-rows: 1fr auto;
  gap: 10px;
  overflow: hidden;
}

.plugin-card::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: linear-gradient(180deg, rgba(49, 108, 114, 0.90), rgba(49, 108, 114, 0.22));
  opacity: 0.72;
}

.plugin-card--system::before {
  background: linear-gradient(180deg, rgba(77, 120, 146, 0.78), rgba(77, 120, 146, 0.18));
}

.plugin-card:hover,
.package-card:hover {
  transform: translateY(-2px);
  border-color: rgba(49, 108, 114, 0.34);
  box-shadow: 0 14px 32px rgba(23, 42, 48, 0.10);
}

.plugin-card-main {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.card-title-row,
.detail-heading,
.panel-title-row,
.detail-hero-top,
.settings-status-strip,
.object-array-title {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.card-title-row {
  align-items: flex-start;
}

.plugin-title-block {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.plugin-title-block strong,
.detail-heading h2,
.detail-panel h3,
.settings-status-strip strong {
  margin: 0;
  color: var(--om-text-1);
}

.plugin-title-block strong {
  font-size: 16px;
}

.plugin-title-block span,
.plugin-description,
.package-card p,
.store-banner span,
.governance-item span,
.detail-heading p,
.detail-identity span,
.setting-copy span,
.settings-actions span,
.settings-status-strip span,
.object-array-title span {
  color: var(--om-text-3);
}

.plugin-description,
.package-card p {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
}

.plugin-card-tags,
.plugin-card-stats,
.card-meta,
.plugin-card-actions,
.plugin-action-buttons,
.plugin-state-control,
.settings-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.plugin-card-footer {
  padding-top: 0;
}

.plugin-card-stats,
.card-meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.plugin-card-actions {
  justify-content: space-between;
  min-height: 38px;
  padding-top: 10px;
  border-top: 1px solid var(--om-border);
}

.plugin-action-buttons {
  flex: 1;
  min-width: 0;
}

.plugin-action-buttons :deep(.n-button) {
  min-width: 82px;
  height: 36px;
  padding-inline: 18px;
}

.plugin-state-control {
  justify-content: flex-end;
  min-width: 66px;
}

.store-banner {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 18px;
  margin-top: 18px;
  background: rgba(49, 108, 114, 0.08);
}

.store-banner .n-icon {
  font-size: 26px;
  color: rgb(var(--primary-color));
}

.governance-list {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}

.governance-item {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
}

.plugin-detail-page {
  display: grid;
  gap: 18px;
}

.plugin-detail-card {
  padding: 20px;
}

.detail-hero {
  display: grid;
  gap: 18px;
  padding: 4px 0 18px;
  border-bottom: 1px solid var(--om-border);
}

.detail-hero-top,
.detail-heading {
  align-items: flex-start;
}

.detail-heading h2 {
  margin-top: 8px;
  font-size: 25px;
}

.detail-heading p {
  max-width: 720px;
  margin: 8px 0 0;
  line-height: 1.6;
}

.detail-identity {
  display: grid;
  min-width: 180px;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
  text-align: right;
}

.detail-content {
  display: grid;
  gap: 16px;
  padding-top: 18px;
}

.detail-panel {
  padding: 20px;
}

.detail-panel.full {
  grid-column: 1 / -1;
}

.meta-grid {
  display: grid;
  grid-template-columns: 128px minmax(0, 1fr);
  gap: 11px 16px;
}

.meta-grid.wide {
  grid-template-columns: 132px minmax(0, 1fr);
}

.meta-grid span {
  color: var(--om-text-3);
}

.meta-grid strong {
  min-width: 0;
  overflow-wrap: anywhere;
}

.settings-shell,
.settings-form,
.object-array-editor,
.runtime-list {
  display: grid;
  gap: 14px;
}

.settings-status-strip {
  align-items: center;
  padding: 14px 16px;
  border: 1px solid rgba(49, 108, 114, 0.14);
  border-radius: 16px;
  background: rgba(49, 108, 114, 0.08);
}

.settings-status-strip > div {
  display: grid;
  gap: 4px;
}

.setting-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.62fr) minmax(320px, 1.38fr);
  gap: 14px;
  align-items: center;
  padding: 13px 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.setting-row--object-array {
  grid-template-columns: 1fr;
  align-items: stretch;
  gap: 12px;
  padding: 14px;
}

.setting-row--object-array .setting-copy {
  grid-template-columns: auto minmax(0, 1fr);
  align-items: baseline;
  gap: 8px 12px;
}

.setting-row--object-array .setting-copy span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.setting-copy {
  display: grid;
  gap: 4px;
}

.setting-copy strong {
  color: var(--om-text-1);
}

.setting-control-wrap {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}

.setting-row--object-array .setting-control-wrap {
  display: block;
}

.setting-control {
  width: 100%;
}

.object-array-item {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-solid);
}

.object-array-title {
  align-items: flex-start;
}

.object-array-title > div {
  display: grid;
  gap: 2px;
}

.object-field-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.object-field {
  display: grid;
  grid-column: span 3;
  gap: 5px;
  color: var(--om-text-2);
}

.object-field--long {
  grid-column: span 3;
}

.object-field--switch {
  grid-column: span 2;
  align-content: end;
  min-height: 58px;
}

.runtime-list article {
  display: grid;
  gap: 4px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.runtime-list span {
  color: var(--om-text-3);
}

.settings-actions {
  position: sticky;
  bottom: 14px;
  z-index: 2;
  justify-content: space-between;
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 92%, transparent);
  box-shadow: 0 12px 26px rgba(23, 42, 48, 0.08);
  backdrop-filter: blur(12px);
}

@media (max-width: 1180px) {
  .plugin-summary-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .plugin-grid {
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  }

  .package-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .plugin-summary-strip,
  .plugin-grid,
  .package-grid {
    grid-template-columns: 1fr;
  }

  .plugin-tabs,
  .detail-tabs {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    border-radius: 18px;
  }

  .plugin-search {
    width: 100%;
  }

  .detail-hero-top,
  .detail-heading,
  .settings-status-strip,
  .setting-row,
  .object-field-grid,
  .meta-grid,
  .settings-actions {
    grid-template-columns: 1fr;
  }

  .detail-hero-top,
  .detail-heading,
  .settings-status-strip,
  .settings-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .detail-identity {
    text-align: left;
  }

  .setting-control-wrap {
    justify-content: flex-start;
  }

  .setting-row--object-array .setting-copy {
    grid-template-columns: 1fr;
  }

  .object-field,
  .object-field--long,
  .object-field--switch {
    grid-column: auto;
  }
}
</style>

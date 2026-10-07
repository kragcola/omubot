<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  NText,
} from 'naive-ui'

import type { EditableConfig, EditableModelProfile, TaskName } from '@/api/types'
import { apiErrorMessage, apiRequest } from '@/api/client'
import type { ModelCatalogResponse, ModelCheckResponse } from '@/api/generated'
import { sessionState } from '@/app/session'
import ContactSettingsPanel from './ContactSettingsPanel.vue'
import {
  addProfile,
  applyAdvancedText,
  cancelDeleteProfile,
  cancelReload,
  confirmDeleteProfile,
  confirmReload,
  hasSettingsDraftChanges,
  isGregorianMonthDay,
  loadSettings,
  markAdvancedText,
  profile,
  profileNames,
  requestDeleteProfile,
  requestReload,
  rollbackSettings,
  saveSettings,
  selectProfile,
  settingsState,
  updateConfigField,
  updateProfileField,
  updateTaskBinding,
} from './store'

const catalogKey = ref('')
const catalogModels = ref<string[]>([])
const catalogBusy = ref(false)
const catalogMessage = ref('')
const catalogNames = ref<Record<string, string>>({})
const saveFlowBusy = ref(false)
const catalogDrafts = new Map<string, {endpoint: string; format: string; key: string; models: string[]; message: string; names: Record<string, string>}>()
let catalogEpoch = 0
let catalogController: AbortController | undefined
function clearCatalog(): void {
  catalogEpoch += 1
  catalogController?.abort()
  catalogKey.value = ''
  catalogModels.value = []
  catalogMessage.value = ''
  catalogNames.value = {}
  modelActionMessage.value = ''
  catalogBusy.value = false
}
onBeforeUnmount(() => { clearCatalog(); catalogDrafts.clear() })
async function fetchCatalog(): Promise<void> {
  const selected = currentProfile.value
  if (!selected || catalogBusy.value || fieldsDisabled.value) return
  const epoch = ++catalogEpoch
  const generation = sessionState.generation
  catalogController = new AbortController()
  catalogBusy.value = true
  catalogMessage.value = ''
  const key = catalogKey.value
  try {
    const result = await apiRequest<ModelCatalogResponse>('/api/admin/models', {
      method: 'POST', adminMutation: true, signal: catalogController.signal,
      body: {api_format: selected.api_format, endpoint: selected.endpoint, api_key: key, profile: settingsState.selectedProfile, expected_revision: settingsState.snapshot?.revision},
    })
    if (epoch !== catalogEpoch || generation !== sessionState.generation) return
    catalogModels.value = result.models
    catalogNames.value = result.display_names ?? {}
    catalogMessage.value = `来源：${result.source_url ?? selected.endpoint} · ${result.pages ?? 1} 页 · ${result.models.length} 项${result.truncated ? '（已截断，可手填其他 ID）' : ''}。列表不代表本 Bot 已支持全部能力。`
  } catch (error: unknown) {
    if (epoch === catalogEpoch && generation === sessionState.generation) catalogMessage.value = `获取失败：${apiErrorMessage(error)}${catalogModels.value.length ? ' 当前保留的是旧列表。' : ' 可手填模型 ID。'}`
  } finally {
    if (epoch === catalogEpoch) catalogBusy.value = false
  }
}

const modelActionBusy = ref(false)
const modelActionMessage = ref('')
async function modelAction(action: 'model-key' | 'model-check'): Promise<void> {
  if (modelActionBusy.value || saveFlowBusy.value || settingsState.saving || settingsState.loading || hasSettingsDraftChanges() || !settingsState.snapshot) return
  if (action === 'model-key' && !catalogKey.value.trim()) return
  const epoch = catalogEpoch
  const generation = sessionState.generation
  modelActionBusy.value = true
  modelActionMessage.value = ''
  try {
    const result = await apiRequest<ModelCheckResponse>('/api/admin/' + action, {
      method: action === 'model-key' ? 'PUT' : 'POST', adminMutation: true,
      body: {profile: settingsState.selectedProfile, expected_revision: settingsState.snapshot.revision,
        api_key: action === 'model-key' ? catalogKey.value : ''},
    })
    if (epoch === catalogEpoch && generation === sessionState.generation) modelActionMessage.value = result.detail
  } catch {
    if (epoch === catalogEpoch && generation === sessionState.generation) modelActionMessage.value = '操作失败，请先保存当前配置，确认登录有效，再重试。'
  } finally { modelActionBusy.value = false }
}

const rollbackTarget = ref<number | null>(null)
const newProfileName = ref('')

const profileOptions = computed(() => profileNames().map((name) => ({ label: name, value: name })))
const currentProfile = computed(() => profile(settingsState.selectedProfile))
type GroupMode = EditableConfig['group_modes'][string]
const groupModeRows = computed(() => Object.entries(settingsState.draft?.group_modes ?? {}))
const groupModeOptions: Array<{label: string; value: GroupMode}> = [
  { label: 'active · 正常处理', value: 'active' },
  { label: 'silent · 仅本地观察', value: 'silent' },
  { label: 'off · 不接纳', value: 'off' },
]
const newGroupId = ref('')
const newGroupMode = ref<GroupMode>('active')
const groupModeError = ref('')
type GroupProfile = EditableConfig['group_profiles'][string]
type GroupReplyStyle = NonNullable<GroupProfile['reply_style']>
const groupProfileRows = computed(() => Object.entries(settingsState.draft?.group_profiles ?? {}))
type CalendarLunarRule =
  | {kind: 'fixed'; month: number; day: number; leap_month: boolean}
  | {kind: 'year_eve'}
type CalendarLunarFixedField = 'month' | 'day' | 'leap_month'
type GroupCalendarEvent = EditableConfig['group_calendar_events'][string][number] & {
  lunar?: CalendarLunarRule | null
}
type CalendarCategory = GroupCalendarEvent['category']
type CalendarSubjectKind = GroupCalendarEvent['subject_kind']
type CalendarWeekdayRule = NonNullable<GroupCalendarEvent['weekday']>
type CalendarWeekdayField = keyof CalendarWeekdayRule
type CalendarEventField = Exclude<keyof GroupCalendarEvent, 'weekday' | 'lunar'>
type CalendarRuleKind = 'fixed' | 'weekday' | 'lunar_fixed' | 'lunar_year_eve'
const groupCalendarRows = computed(() => Object.entries(settingsState.draft?.group_calendar_events ?? {}) as Array<[string, GroupCalendarEvent[]]>)
const groupCalendarCount = computed(() => groupCalendarRows.value.reduce((total, row) => total + row[1].length, 0))
const groupCalendarError = ref('')
const newCalendarGroupId = ref('')
const newCalendarName = ref('')
const newCalendarDate = ref('')
const newCalendarRuleKind = ref<CalendarRuleKind>('fixed')
const newCalendarMonth = ref<number | null>(null)
const newCalendarOrdinal = ref<number | null>(null)
const newCalendarWeekday = ref<number | null>(null)
const newCalendarLunarMonth = ref<number | null>(null)
const newCalendarLunarDay = ref<number | null>(null)
const newCalendarLunarLeapMonth = ref(false)
const newCalendarCategory = ref<CalendarCategory>('birthday')
const newCalendarSubjectKind = ref<CalendarSubjectKind>('member')
const newCalendarSubjectId = ref('')
const calendarCategoryOptions: Array<{label: string; value: CalendarCategory}> = [
  { label: '生日', value: 'birthday' },
  { label: '纪念日', value: 'anniversary' },
  { label: '特殊日', value: 'special_day' },
]
const calendarRuleKindOptions: Array<{label: string; value: CalendarRuleKind}> = [
  { label: '固定公历日期', value: 'fixed' },
  { label: '每年指定月的第几个星期几', value: 'weekday' },
  { label: '固定农历日期', value: 'lunar_fixed' },
  { label: '除夕（春节前一天）', value: 'lunar_year_eve' },
]
const calendarLunarMonthOptions = ['正月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '冬月', '腊月']
  .map((label, index) => ({ label, value: index + 1 }))
const calendarLunarDayOptions = ['初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十',
  '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十',
  '廿一', '廿二', '廿三', '廿四', '廿五', '廿六', '廿七', '廿八', '廿九', '三十']
  .map((label, index) => ({ label, value: index + 1 }))
const calendarMonthOptions = Array.from({ length: 12 }, (_, index) => ({
  label: `${index + 1} 月`,
  value: index + 1,
}))
const calendarOrdinalOptions = [1, 2, 3, 4, 5, -1, -2, -3, -4, -5].map((value) => ({
  label: value > 0 ? `第 ${value} 个` : `倒数第 ${-value} 个`,
  value,
}))
const calendarWeekdayOptions = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label, value) => ({
  label: `${label}（${value}）`,
  value,
}))
const calendarSubjectOptions: Array<{label: string; value: CalendarSubjectKind}> = [
  { label: 'Bot 本人', value: 'bot' },
  { label: '群成员', value: 'member' },
  { label: '群本身', value: 'group' },
]

type WorldbookGateKey =
  | 'worldbook_enabled'
  | 'worldbook_chat_projection_enabled'
  | 'worldbook_schedule_projection_enabled'
  | 'worldbook_storylet_enabled'
  | 'worldbook_dream_proposal_enabled'
  | 'worldbook_social_evidence_enabled'
  | 'episode_query_rerank_enabled'
  | 'journal_enabled'
const worldbookGateRows: Array<{key: WorldbookGateKey; label: string; help: string}> = [
  { key: 'episode_query_rerank_enabled', label: '经历按当前话题排序', help: '共同经历允许读取时，在有限历史池中优先选择相关经历；到期经历始终停止使用。保存并应用后生效。' },
  { key: 'worldbook_enabled', label: 'Worldbook 总门', help: '关闭时不读取 registry、不启动后台任务，也不改变提示词或状态。' },
  { key: 'worldbook_chat_projection_enabled', label: '聊天投影配置门', help: '总门和群白名单同时开启后，当前消息可命中已审核 Canon 配置；同群已提交故事也可进入聊天。保存后需应用运行版本。' },
  { key: 'worldbook_schedule_projection_enabled', label: '日程投影配置门', help: '总门和群白名单同时开启后，实时模式会按本地日界推进；实际模型调用仍需单独的 model.schedule 权限。' },
  { key: 'worldbook_storylet_enabled', label: 'Storylet 配置门', help: '还需日程门、已审核的 Storylet JSON 与已提交当日日程；满足后由故事账本受控提交虚构事件。' },
  { key: 'worldbook_dream_proposal_enabled', label: 'Dream 提案配置门', help: '管理员在 Dream 页面手动生成与审核；模型调用还需 model.dream 权限，提案不会自动提交故事。' },
  { key: 'worldbook_social_evidence_enabled', label: '共同经历配置门', help: '默认关闭；形成共同经历还需精确群白名单、同群已审核 episode 与完整成功的文字回复；聊天读取还需聊天投影门，并只在相关话题命中时使用当前有效来源。' },
  { key: 'journal_enabled', label: '公开日志与虚构日记', help: '默认关闭。本人逐字公开同意可生成固定事实模板；虚构成稿另需 Worldbook 和故事群名单。管理员预览、审核与发布分两步，本地预演不会外发。' },
]
const draftWorldbookGroups = computed(() => settingsState.draft?.worldbook_allowed_groups ?? [])
const runningWorldbookGroups = computed(() => settingsState.snapshot?.effective_config.worldbook_allowed_groups ?? [])
const draftMemoryGroups = computed(() => settingsState.draft?.memory_capture_groups ?? [])
const privateRuntimeSummary = computed(() => {
  const running = settingsState.snapshot?.effective_config
  return running?.private_conversation_enabled
    ? `运行版本：开启 · 私聊名单 ${running.private_conversation_peers?.join('、') || '空'}`
    : '运行版本：关闭'
})
function changePrivatePeers(value: string): void {
  updateConfigField('private_conversation_peers', value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))
}
function changeJournalGroups(value: string): void {
  updateConfigField('journal_allowed_groups', value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))
}
function changeJournalLiveAccounts(value: string): void {
  updateConfigField('journal_allowed_live_uins', value.split(/[,，\n]/).map(id => id.trim()).filter(Boolean))
}
const scopedFeatures = [
  { enabled: 'followup_reply_enabled', groups: 'followup_reply_groups', label: '短追评回复', help: '首轮成功发送后，可在短暂静默中追加最多两次短回复；当前新消息会取消追评。' },
  { enabled: 'graph_extraction_enabled', groups: 'graph_extraction_groups', label: '手动文档关系抽取', help: '管理端从已批准的非个人短片段提出待审核关系，使用记忆任务模型；不自动应用。' },
  { enabled: 'video_metadata_enabled', groups: 'video_metadata_groups', label: '视频轻量元数据', help: '识别当前消息的视频链接及卡片；YouTube 标题读取须有 HTTP 工具能力与精确地址授权。' },
  { enabled: 'url_titles_enabled', groups: 'url_titles_groups', label: '当前链接标题', help: '读取当前消息中最多三个公开链接的标题，和视频元数据共享时间预算；需要精确地址读取权限。' },
  { enabled: 'element_rules_enabled', groups: 'element_rules_groups', label: '句式回应', help: '对四条默认句式和管理员自定义规则进行模板回应或角色短评；静默群不回应。' },
] as const
function changeScopedFeatureGroups(key: typeof scopedFeatures[number]['groups'], value: string): void {
  updateConfigField(key, value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))
}
function scopedFeatureStatus(config: EditableConfig | null, feature: typeof scopedFeatures[number]): string {
  return config?.[feature.enabled] ? `开启 · 允许群 ${(config[feature.groups] ?? []).join('、') || '空'}` : '关闭'
}
function changePlannedReplyGroups(value: string): void {
  updateConfigField('planned_reply_groups', value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))
}
function changeFoodGroups(value: string): void {
  updateConfigField('food_groups', value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))
}
const effectiveMemorySummary = computed(() => {
  const running = settingsState.snapshot?.effective_config
  if (!running?.memory_capture_enabled) return '运行版本：关闭'
  return `运行版本：开启 · 群白名单 ${running.memory_capture_groups?.join('、') || '无群'}`
})
function changeMemoryGroups(value: string): void {
  updateConfigField('memory_capture_groups', value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))
}
function changeMemoryPath(key: 'memory_spool_dir' | 'memory_key_file', value: string): void {
  updateConfigField(key, value)
}
const newWorldbookGroupId = ref('')
const worldbookGroupError = ref('')
const draftWorldbookScheduleReady = computed(() => Boolean(
  settingsState.draft?.worldbook_enabled
  && settingsState.draft.worldbook_schedule_projection_enabled
  && draftWorldbookGroups.value.length,
))
const runningWorldbookScheduleReady = computed(() => Boolean(
  settingsState.snapshot?.effective_config.worldbook_enabled
  && settingsState.snapshot.effective_config.worldbook_schedule_projection_enabled
  && runningWorldbookGroups.value.length,
))
function changeWorldbookGate(key: WorldbookGateKey, value: boolean): void {
  updateConfigField(key, value)
}
function addWorldbookGroup(): void {
  const draft = settingsState.draft
  if (fieldsDisabled.value || !draft) return
  const groupId = newWorldbookGroupId.value
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(groupId)) {
    worldbookGroupError.value = '请输入 1–64 位精确群 ID，只能包含字母、数字、下划线和短横线。'
    return
  }
  if (draftWorldbookGroups.value.includes(groupId)) {
    worldbookGroupError.value = '该群已在 Worldbook 白名单中。'
    return
  }
  if (draftWorldbookGroups.value.length >= 128) {
    worldbookGroupError.value = '最多配置 128 个群；空白名单不会放行任何群。'
    return
  }
  if (updateConfigField('worldbook_allowed_groups', [...draftWorldbookGroups.value, groupId])) {
    newWorldbookGroupId.value = ''
    worldbookGroupError.value = ''
  }
}
function removeWorldbookGroup(groupId: string): void {
  if (!settingsState.draft) return
  updateConfigField('worldbook_allowed_groups', draftWorldbookGroups.value.filter((item) => item !== groupId))
  worldbookGroupError.value = ''
}

type ClimateMode = 'off' | 'observe' | 'active'
const climateModeOptions: Array<{label: string; value: ClimateMode}> = [
  { label: 'off · 关闭', value: 'off' },
  { label: 'observe · 观察', value: 'observe' },
  { label: 'active · 主动表达', value: 'active' },
]
const climateModeNames: Record<ClimateMode, string> = {
  off: '关闭（off）',
  observe: '观察（observe）',
  active: '主动表达（active）',
}
const climateModeDetails: Record<ClimateMode, string> = {
  off: '不采集 Climate 信号，也不生成表达提示。',
  observe: '只采集真实来源信号并展示诊断，不生成表达提示。',
  active: '采集真实来源信号，并只在有界预算内生成表达提示。',
}
const draftClimateMode = computed<ClimateMode>(() => {
  const value = settingsState.draft?.climate_mode
  return value === 'observe' || value === 'active' ? value : 'off'
})
const runningClimateMode = computed<ClimateMode>(() => {
  const value = settingsState.snapshot?.effective_config.climate_mode
  return value === 'observe' || value === 'active' ? value : 'off'
})
function streamReplyStatus(config: EditableConfig | null): string {
  if (!config?.stream_reply_enabled) return '关闭'
  if (config.tool_capabilities.length !== 0) {
    return '已开启，但配置了工具能力，当前沿用普通非流式路径'
  }
  const replyProfile = config.models[config.task_models.reply ?? config.active_model]
  if (!replyProfile) {
    return '已开启，但未找到有效回复模型配置'
  }
  return '开启；无工具、无图片的群文字回复适用'
}
function plannedReplyStatus(config: EditableConfig | null): string {
  return config?.planned_reply_enabled ? `开启 · 允许群 ${config.planned_reply_groups?.join('、') || '空'}` : '关闭'
}
const draftPlannedReplyStatus = computed(() => plannedReplyStatus(settingsState.draft))
const runningPlannedReplyStatus = computed(() => plannedReplyStatus(settingsState.snapshot?.effective_config ?? null))
const draftStreamReplyStatus = computed(() => streamReplyStatus(settingsState.draft))
const runningStreamReplyStatus = computed(() => streamReplyStatus(settingsState.snapshot?.effective_config ?? null))
function changeClimateMode(value: string | null): void {
  if (value !== 'off' && value !== 'observe' && value !== 'active') return
  updateConfigField('climate_mode', value)
}
const inheritGroupReplyStyle = '__inherit__'
const groupReplyStyleOptions: Array<{label: string; value: string}> = [
  { label: '继承实例人格', value: inheritGroupReplyStyle },
  { label: 'gentle · 温和', value: 'gentle' },
  { label: 'playful · 活泼', value: 'playful' },
  { label: 'concise · 简洁', value: 'concise' },
  { label: 'energetic · 有活力', value: 'energetic' },
  { label: 'steady · 沉稳', value: 'steady' },
  { label: 'default · 默认', value: 'default' },
]
const groupReplyStyles: readonly GroupReplyStyle[] = ['default', 'gentle', 'playful', 'concise', 'energetic', 'steady']
const newGroupProfileId = ref('')
const groupProfileError = ref('')
type ApiFormat = EditableModelProfile['api_format']
const endpointPresets: Record<ApiFormat, string> = {
  anthropic: 'https://api.anthropic.com/v1/messages',
  openai_chat: 'https://api.openai.com/v1/chat/completions',
  openai_responses: 'https://api.openai.com/v1/responses',
  deepseek: 'https://api.deepseek.com/chat/completions',
}
const knownEndpointRoots = new Set<string>([
  'https://api.anthropic.com',
  'https://api.openai.com',
  'https://api.deepseek.com',
  ...Object.values(endpointPresets),
])
function normalizeEndpoint(value: string): string {
  return value.trim().replace(/\/+$/, '')
}
function isKnownEndpoint(value: string): boolean {
  return knownEndpointRoots.has(normalizeEndpoint(value))
}
const currentEndpointPreset = computed(() => {
  const format = currentProfile.value?.api_format
  return format ? endpointPresets[format] : ''
})
const customEndpoint = computed(() => {
  const endpoint = currentProfile.value?.endpoint ?? ''
  return Boolean(endpoint.trim()) && !isKnownEndpoint(endpoint)
})
watch([() => settingsState.selectedProfile, () => currentProfile.value?.endpoint,
  () => currentProfile.value?.api_format, () => sessionState.adminAuthenticated],
  ([name, endpoint, format, authenticated], [oldName, oldEndpoint, oldFormat, wasAuthenticated]) => {
    for (const cachedName of catalogDrafts.keys()) if (!profileNames().includes(cachedName)) catalogDrafts.delete(cachedName)
    if (authenticated && wasAuthenticated && name !== oldName && oldName && oldEndpoint && oldFormat && profileNames().includes(String(oldName))) {
      catalogDrafts.set(String(oldName), {endpoint: String(oldEndpoint), format: String(oldFormat),
        key: catalogKey.value, models: [...catalogModels.value], message: catalogMessage.value, names: {...catalogNames.value}})
    }
    if (saveFlowBusy.value && authenticated && name === oldName) return
    clearCatalog()
    if (!authenticated) { catalogDrafts.clear(); return }
    const saved = catalogDrafts.get(String(name))
    if (saved && saved.endpoint === endpoint && saved.format === format) {
      catalogKey.value = saved.key
      catalogModels.value = saved.models
      catalogMessage.value = saved.message
      catalogNames.value = saved.names
    } else catalogDrafts.delete(String(name))
  })

const fieldsDisabled = computed(() => modelActionBusy.value || saveFlowBusy.value || settingsState.saving || settingsState.loading || settingsState.advancedEdited || !settingsState.draft)
const canSave = computed(() => Boolean(settingsState.draft && (hasSettingsDraftChanges() || catalogKey.value.trim()) && !modelActionBusy.value && !saveFlowBusy.value && !settingsState.saving && !settingsState.loading && !settingsState.advancedEdited))
const canRollback = computed(() => Boolean(settingsState.snapshot && rollbackTarget.value !== null && !hasSettingsDraftChanges() && !modelActionBusy.value && !saveFlowBusy.value && !settingsState.saving && !settingsState.loading && !settingsState.advancedEdited))
const rollbackOptions = computed(() => (settingsState.snapshot?.versions ?? [])
  .filter((version) => version !== settingsState.snapshot?.revision)
  .map((version) => ({ label: `版本 ${version}`, value: version })))

const formatOptions = [
  { label: 'Anthropic Messages', value: 'anthropic' },
  { label: 'OpenAI Chat', value: 'openai_chat' },
  { label: 'OpenAI Responses', value: 'openai_responses' },
  { label: 'DeepSeek', value: 'deepseek' },
]
const reasoningOptions = [
  { label: 'None', value: 'none' },
  { label: 'Minimal', value: 'minimal' },
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
  { label: 'XHigh', value: 'xhigh' },
  { label: 'Max', value: 'max' },
]
const tokenParameterOptions = [
  { label: 'max_tokens', value: 'max_tokens' },
  { label: 'max_completion_tokens', value: 'max_completion_tokens' },
]
const taskRows: Array<{ key: TaskName; label: string; help: string }> = [
  { key: 'reply', label: '普通回复', help: '默认：未绑定，使用默认模型；群消息的主要回复任务。' },
  { key: 'thinker', label: 'Thinker', help: '默认：未绑定，使用默认模型；启用后才会为思考任务使用授权模型。' },
  { key: 'vision', label: '视觉', help: '默认：未绑定；预留绑定，视觉功能尚未接入。' },
  { key: 'schedule', label: '日程', help: '默认：未绑定，使用默认模型；仅在 Worldbook 日程门和群白名单开启且独立授权后使用。' },
  { key: 'dream', label: 'Dream', help: '默认：未绑定，使用默认模型；仅在 Dream 门和群白名单开启且独立授权后使用。' },
  { key: 'journal', label: '虚构日记成稿', help: '未单独绑定时使用默认模型；仅虚构成稿调用模型，固定事实模板不调用模型。' },
  { key: 'memory', label: '记忆抽取', help: '默认关闭；仅 memory_capture_enabled 开启后处理白名单内、获权的纯文字来源，并生成待审候选。' },
]

function chooseProfile(name: string): void {
  if (fieldsDisabled.value) return
  selectProfile(name)
}

// Defaults remain in the draft contract; the form renders them as hints.
const profileDefaults = {model: 'offline-model', endpoint: endpointPresets.anthropic, api_key_env: 'OMUBOT_MODEL_KEY'}
const configTextDefaults = {onebot_endpoint: 'http://127.0.0.1:3000', onebot_token_env: 'OMUBOT_ONEBOT_TOKEN', timezone: 'Asia/Shanghai'}
const configNumberDefaults = {
  queue_capacity: 16, total_timeout: 105, reply_generation_timeout: 30,
  reply_admission_timeout: 30, reply_delivery_timeout: 45,
  model_timeout: 20, send_timeout: 5, history_ttl: 900,
}
const replyPhaseFields = [
  { key: 'reply_generation_timeout', label: '回复准备期限', max: 30,
    help: '从实际正文准备开始计，包含工具与人格生成；后续生成只用剩余预算，不会重新计时。排队和判断仍受整体期限约束。' },
  { key: 'reply_admission_timeout', label: '首条消息准入期限', max: 30,
    help: '回复准备好后等待首次实际发送的时限，仍需原 QQ 限频和权限准入。' },
  { key: 'reply_delivery_timeout', label: '消息组发送期限', max: 45,
    help: '从第一条实际发送起计；后续气泡不会延长期限，最多五条且逐条计费。' },
] as const
const qqDefaults = { account_min_interval: 5, account_hour_limit: 60, account_day_limit: 180,
  target_min_interval: 8, target_hour_limit: 30, target_day_limit: 90,
  admission_wait_seconds: 30, account_queue_limit: 8, target_queue_limit: 2 }
type QQLimitKey = keyof typeof qqDefaults
const qqFields: { key: QQLimitKey; label: string; min: number; max?: number }[] = [
  { key: 'account_min_interval', label: '账号最小间隔（秒）', min: 5 },
  { key: 'target_min_interval', label: '目标最小间隔（秒）', min: 8 },
  { key: 'account_hour_limit', label: '账号滚动一小时额度', min: 1, max: 60 },
  { key: 'account_day_limit', label: '账号滚动 24 小时额度', min: 1, max: 180 },
  { key: 'target_hour_limit', label: '目标滚动一小时额度', min: 1, max: 30 },
  { key: 'target_day_limit', label: '目标滚动 24 小时额度', min: 1, max: 90 },
  { key: 'admission_wait_seconds', label: '准入等待上限（秒）', min: 1, max: 30 },
  { key: 'account_queue_limit', label: '账号等待者上限', min: 1, max: 8 },
  { key: 'target_queue_limit', label: '目标等待者上限', min: 1, max: 2 },
]

function changeQQLimit(key: QQLimitKey, value: number | null): void {
  if (!settingsState.draft || value === null) return
  updateConfigField('qq_delivery_limits', { ...qqDefaults, ...settingsState.draft.qq_delivery_limits, [key]: value })
}
const editedDefaults = ref(new Set<string>())
function textValue(value: string, fallback: string, key: string): string {
  return value === fallback && !editedDefaults.value.has(key) ? '' : value
}
function numberValue(value: number | null, fallback: number, key: string): number | null {
  return value === fallback && !editedDefaults.value.has(key) ? null : value
}
function noteEdit(key: string, present: boolean): void {
  if (present) editedDefaults.value.add(key)
  else editedDefaults.value.delete(key)
}
watch(() => settingsState.selectedProfile, () => { editedDefaults.value.clear() })

function changeProfileText(key: 'api_key_env' | 'endpoint' | 'model', value: string): void {
  noteEdit(key, value !== '')
  const fallback = key === 'endpoint' ? (currentEndpointPreset.value || profileDefaults.endpoint) : profileDefaults[key]
  updateProfileField(settingsState.selectedProfile, key, value || fallback)
}

function changeProfileNumber(key: 'max_output_tokens' | 'temperature', value: number | null): void {
  noteEdit(key, value !== null)
  updateProfileField(settingsState.selectedProfile, key, value ?? (key === 'max_output_tokens' ? 1024 : null))
}

function changeProfileBoolean(key: 'thinking' | 'send_history' | 'vision_enabled', value: boolean): void {
  updateProfileField(settingsState.selectedProfile, key, value)
}

function changeProfileFormat(value: string | null): void {
  if (value !== 'anthropic' && value !== 'openai_chat' && value !== 'openai_responses' && value !== 'deepseek') return
  const previousEndpoint = currentProfile.value?.endpoint ?? ''
  const shouldUsePreset = !previousEndpoint.trim() || isKnownEndpoint(previousEndpoint)
  updateProfileField(settingsState.selectedProfile, 'api_format', value)
  if (shouldUsePreset) {
    noteEdit('endpoint', false)
    updateProfileField(settingsState.selectedProfile, 'endpoint', endpointPresets[value])
  }
}

function useEndpointPreset(): void {
  const format = currentProfile.value?.api_format
  if (!format || fieldsDisabled.value) return
  noteEdit('endpoint', false)
  updateProfileField(settingsState.selectedProfile, 'endpoint', endpointPresets[format])
}

function changeProfileReasoning(value: string | null): void {
  if (value === null || value === 'none' || value === 'minimal' || value === 'low' || value === 'medium' || value === 'high' || value === 'xhigh' || value === 'max') {
    updateProfileField(settingsState.selectedProfile, 'reasoning_effort', value)
  }
}

function changeProfileTokenParameter(value: string | null): void {
  if (value === null || value === 'max_tokens' || value === 'max_completion_tokens') {
    updateProfileField(settingsState.selectedProfile, 'token_parameter', value)
  }
}

function changeConfigText(key: 'onebot_endpoint' | 'onebot_token_env' | 'timezone', value: string): void {
  noteEdit(key, value !== '')
  updateConfigField(key, value || configTextDefaults[key])
}

function changeConfigNumber(key: keyof typeof configNumberDefaults, value: number | null): void {
  noteEdit(key, value !== null)
  updateConfigField(key, value ?? configNumberDefaults[key])
}

function changeConfigBoolean(key: 'thinker_enabled' | 'mention_force_reply_enabled' | 'stream_reply_enabled' | 'planned_reply_enabled' | 'followup_reply_enabled' | 'graph_extraction_enabled' | 'video_metadata_enabled' | 'url_titles_enabled' | 'element_rules_enabled' | 'memory_capture_enabled' | 'private_conversation_enabled' | 'context_observation_enabled' | 'graph_observation_enabled' | 'cross_group_sharing_enabled' | 'food_enabled' | 'food_search_enabled', value: boolean): void {
  updateConfigField(key, value)
}

function changeGroupMode(groupId: string, value: string | null): void {
  if (value !== 'active' && value !== 'silent' && value !== 'off') return
  const draft = settingsState.draft
  if (!draft) return
  updateConfigField('group_modes', { ...draft.group_modes, [groupId]: value })
}

function addGroupMode(): void {
  const draft = settingsState.draft
  if (fieldsDisabled.value || !draft) return
  const groupId = newGroupId.value
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(groupId)) {
    groupModeError.value = '请输入 1–64 位精确群 ID，只能包含字母、数字、下划线和短横线。'
    return
  }
  if (Object.hasOwn(draft.group_modes, groupId)) {
    groupModeError.value = '该群已有规则，请在列表中修改模式。'
    return
  }
  if (groupModeRows.value.length >= 128) {
    groupModeError.value = '最多配置 128 个群；未列群仍按 active 处理。'
    return
  }
  if (updateConfigField('group_modes', { ...draft.group_modes, [groupId]: newGroupMode.value })) {
    newGroupId.value = ''
    newGroupMode.value = 'active'
    groupModeError.value = ''
  }
}

function removeGroupMode(groupId: string): void {
  const draft = settingsState.draft
  if (!draft) return
  const groupModes = { ...draft.group_modes }
  delete groupModes[groupId]
  updateConfigField('group_modes', groupModes)
}

function changeGroupReplyStyle(groupId: string, value: string | null): void {
  const draft = settingsState.draft
  if (!draft) return
  const replyStyle = value === inheritGroupReplyStyle || value === null
    ? null
    : groupReplyStyles.includes(value as GroupReplyStyle) ? value as GroupReplyStyle : undefined
  if (replyStyle === undefined) return
  const profile = draft.group_profiles[groupId]
  if (!profile) return
  updateConfigField('group_profiles', {
    ...draft.group_profiles,
    [groupId]: { ...profile, reply_style: replyStyle },
  })
  groupProfileError.value = ''
}

function changeGroupCustomPrompt(groupId: string, value: string): void {
  const draft = settingsState.draft
  if (!draft || value.length > 6000) {
    groupProfileError.value = '群补充提示最多 6000 个字符。'
    return
  }
  const profile = draft.group_profiles[groupId]
  if (!profile) return
  updateConfigField('group_profiles', {
    ...draft.group_profiles,
    [groupId]: { ...profile, custom_prompt: value || null },
  })
  groupProfileError.value = ''
}

function addGroupProfile(): void {
  const draft = settingsState.draft
  if (fieldsDisabled.value || !draft) return
  const groupId = newGroupProfileId.value
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(groupId)) {
    groupProfileError.value = '请输入 1–64 位精确群 ID，只能包含字母、数字、下划线和短横线。'
    return
  }
  if (Object.hasOwn(draft.group_profiles, groupId)) {
    groupProfileError.value = '该群已有回复风格配置。'
    return
  }
  if (groupProfileRows.value.length >= 128) {
    groupProfileError.value = '最多配置 128 个群 Profile。'
    return
  }
  const profile: GroupProfile = { reply_style: null, custom_prompt: null }
  if (updateConfigField('group_profiles', { ...draft.group_profiles, [groupId]: profile })) {
    newGroupProfileId.value = ''
    groupProfileError.value = ''
  }
}

function removeGroupProfile(groupId: string): void {
  const draft = settingsState.draft
  if (!draft) return
  const groupProfiles = { ...draft.group_profiles }
  delete groupProfiles[groupId]
  updateConfigField('group_profiles', groupProfiles)
  groupProfileError.value = ''
}

function changeGroupCalendarEvent(
  groupId: string,
  index: number,
  field: CalendarEventField,
  value: string | null,
): void {
  const draft = settingsState.draft
  const events = draft?.group_calendar_events[groupId]
  const current = events?.[index]
  if (!draft || !events || !current) return
  const updated = { ...current }
  if (field === 'name' || field === 'date') {
    updated[field] = value ?? ''
  } else if (field === 'category') {
    if (!value || !calendarCategoryOptions.some((option) => option.value === value)) return
    updated.category = value as CalendarCategory
  } else if (field === 'subject_kind') {
    if (!value || !calendarSubjectOptions.some((option) => option.value === value)) return
    updated.subject_kind = value as CalendarSubjectKind
    if (updated.subject_kind !== 'member') updated.subject_id = null
  } else {
    updated.subject_id = value?.trim() || null
  }
  const nextEvents = [...events]
  nextEvents[index] = updated
  updateConfigField('group_calendar_events', { ...draft.group_calendar_events, [groupId]: nextEvents })
  groupCalendarError.value = ''
}

function changeGroupCalendarWeekday(
  groupId: string,
  index: number,
  field: CalendarWeekdayField,
  value: number | null,
): void {
  const draft = settingsState.draft
  const events = draft?.group_calendar_events[groupId]
  const current = events?.[index]
  if (!draft || !events || !current?.weekday || value === null) return
  const nextEvents = [...events]
  nextEvents[index] = { ...current, weekday: { ...current.weekday, [field]: value } }
  updateConfigField('group_calendar_events', { ...draft.group_calendar_events, [groupId]: nextEvents })
  groupCalendarError.value = ''
}

function changeGroupCalendarLunar(
  groupId: string,
  index: number,
  field: CalendarLunarFixedField,
  value: number | boolean | null,
): void {
  const draft = settingsState.draft
  const events = draft?.group_calendar_events[groupId]
  const current = events?.[index] as GroupCalendarEvent | undefined
  if (!draft || !events || current?.lunar?.kind !== 'fixed' || value === null) return
  const lunar = field === 'leap_month'
    ? { ...current.lunar, leap_month: value as boolean }
    : { ...current.lunar, [field]: value as number }
  const nextEvents = [...events] as GroupCalendarEvent[]
  nextEvents[index] = { ...current, lunar }
  updateConfigField('group_calendar_events', { ...draft.group_calendar_events, [groupId]: nextEvents })
  groupCalendarError.value = ''
}

function addGroupCalendarEvent(): void {
  const draft = settingsState.draft
  if (fieldsDisabled.value || !draft) return
  const groupId = newCalendarGroupId.value
  const name = newCalendarName.value.trim()
  const date = newCalendarDate.value.trim()
  const month = newCalendarMonth.value
  const ordinal = newCalendarOrdinal.value
  const weekday = newCalendarWeekday.value
  const lunarMonth = newCalendarLunarMonth.value
  const lunarDay = newCalendarLunarDay.value
  const subjectId = newCalendarSubjectId.value.trim()
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(groupId)) {
    groupCalendarError.value = '请输入 1–64 位精确群 ID。'
    return
  }
  if (!Object.hasOwn(draft.group_calendar_events, groupId)
    && groupCalendarRows.value.length >= 128) {
    groupCalendarError.value = '最多配置 128 个群的日历。'
    return
  }
  const current = draft.group_calendar_events[groupId] ?? []
  if (current.length >= 64) {
    groupCalendarError.value = '每个群最多配置 64 项日历事件。'
    return
  }
  if (!name || name.length > 80) {
    groupCalendarError.value = '名称须为 1–80 个字符。'
    return
  }
  if (newCalendarRuleKind.value === 'fixed' && !isGregorianMonthDay(date)) {
    groupCalendarError.value = '日期请按公历 MM-DD 填写；02-29 只在闰年匹配。'
    return
  }
  if (newCalendarRuleKind.value === 'weekday'
    && (typeof month !== 'number' || !Number.isInteger(month) || month < 1 || month > 12
      || typeof ordinal !== 'number' || !Number.isInteger(ordinal) || ordinal < -5
      || ordinal > 5 || ordinal === 0
      || typeof weekday !== 'number' || !Number.isInteger(weekday) || weekday < 0
      || weekday > 6)) {
    groupCalendarError.value = '请选择有效月份、第几个和星期。'
    return
  }
  if (newCalendarRuleKind.value === 'lunar_fixed'
    && (typeof lunarMonth !== 'number' || !Number.isInteger(lunarMonth) || lunarMonth < 1 || lunarMonth > 12
      || typeof lunarDay !== 'number' || !Number.isInteger(lunarDay) || lunarDay < 1 || lunarDay > 30)) {
    groupCalendarError.value = '请选择有效农历月份和日期。'
    return
  }
  if (newCalendarSubjectKind.value === 'member' && !/^[A-Za-z0-9_-]{1,64}$/.test(subjectId)) {
    groupCalendarError.value = '群成员事件需要填写精确成员 ID。'
    return
  }
  const event: GroupCalendarEvent = {
    name,
    category: newCalendarCategory.value,
    subject_kind: newCalendarSubjectKind.value,
    subject_id: newCalendarSubjectKind.value === 'member' ? subjectId : null,
  }
  if (newCalendarRuleKind.value === 'fixed') event.date = date
  else if (newCalendarRuleKind.value === 'weekday') {
    event.weekday = {
      month: month!,
      ordinal: ordinal!,
      weekday: weekday!,
    }
  } else if (newCalendarRuleKind.value === 'lunar_fixed') {
    event.lunar = {
      kind: 'fixed',
      month: lunarMonth!,
      day: lunarDay!,
      leap_month: newCalendarLunarLeapMonth.value,
    }
  } else {
    event.lunar = { kind: 'year_eve' }
  }
  if (updateConfigField('group_calendar_events', {
    ...draft.group_calendar_events,
    [groupId]: [...current, event],
  })) {
    newCalendarName.value = ''
    newCalendarDate.value = ''
    newCalendarMonth.value = null
    newCalendarOrdinal.value = null
    newCalendarWeekday.value = null
    newCalendarLunarMonth.value = null
    newCalendarLunarDay.value = null
    newCalendarLunarLeapMonth.value = false
    newCalendarSubjectId.value = ''
    groupCalendarError.value = ''
  }
}

function removeGroupCalendarEvent(groupId: string, index: number): void {
  const draft = settingsState.draft
  const current = draft?.group_calendar_events[groupId]
  if (!draft || !current) return
  const next = { ...draft.group_calendar_events }
  const events = current.filter((_, eventIndex) => eventIndex !== index)
  if (events.length) next[groupId] = events
  else delete next[groupId]
  updateConfigField('group_calendar_events', next)
  groupCalendarError.value = ''
}

function changeTask(task: TaskName, value: string | null): void {
  updateTaskBinding(task, value)
}

function onRollbackTarget(value: number | null): void {
  rollbackTarget.value = value
}

async function runRollback(): Promise<void> {
  if (!canRollback.value || rollbackTarget.value === null) return
  const target = rollbackTarget.value
  if (await rollbackSettings(target)) rollbackTarget.value = null
}

async function runSave(): Promise<void> {
  if (!canSave.value) return
  const name = settingsState.selectedProfile
  const key = catalogKey.value
  const generation = sessionState.generation
  saveFlowBusy.value = true
  modelActionMessage.value = ''
  let configSaved = false
  try {
    if (hasSettingsDraftChanges()) {
      if (!await saveSettings()) return
      configSaved = true
    }
    if (generation !== sessionState.generation || name !== settingsState.selectedProfile) return
    if (key.trim()) {
      const result = await apiRequest<ModelCheckResponse>('/api/admin/model-key', {
        method: 'PUT', adminMutation: true,
        body: {profile: name, expected_revision: settingsState.snapshot?.revision, api_key: key},
      })
      if (!result.ok) throw new Error('model_key_save_failed')
      if (generation === sessionState.generation && name === settingsState.selectedProfile) {
        catalogKey.value = ''
        catalogDrafts.delete(name)
        modelActionMessage.value = `${configSaved ? '配置已保存；' : ''}${result.detail} 输入框已清空，可直接使用已保存密钥获取列表。`
      }
    }
  } catch {
    if (generation === sessionState.generation && name === settingsState.selectedProfile) modelActionMessage.value = configSaved
      ? '配置已保存，但运行密钥保存失败。输入的 key 已保留，请重试保存。'
      : '运行密钥保存失败，输入的 key 已保留，请重试。'
  } finally { saveFlowBusy.value = false }
}
function deleteProfile(): void {
  const name = settingsState.pendingDeleteProfile
  if (fieldsDisabled.value) return
  confirmDeleteProfile()
  if (name && !profileNames().includes(name)) catalogDrafts.delete(name)
}
const deleteImpact = computed(() => {
  const name = settingsState.pendingDeleteProfile
  const draft = settingsState.draft
  if (!name || !draft) return ''
  const tasks = Object.entries(draft.task_models).filter(([, model]) => model === name).map(([task]) => task)
  return `${draft.active_model === name ? '这是默认模型，删除后将改用剩余的第一个模型。' : ''}${tasks.length ? `任务 ${tasks.join('、')} 的单独绑定将移除，改用默认模型。` : ''}删除只修改草稿，保存后生效。`
})
const officialDeepSeek = computed(() => {
  try { return currentProfile.value?.api_format === 'deepseek' && new URL(currentProfile.value.endpoint).origin === 'https://api.deepseek.com' } catch { return false }
})

function runAddProfile(): void {
  const requested = newProfileName.value.trim()
  if (requested && !profileNames().includes(requested)) catalogDrafts.delete(requested)
  const added = addProfile(requested)
  if (added) { catalogDrafts.delete(added); newProfileName.value = '' }
}

onMounted(() => {
  void loadSettings()
})
</script>

<template>
  <section class="settings-view" aria-label="模型配置">
    <n-alert v-if="settingsState.error" class="notice" type="error" :show-icon="true">
      {{ settingsState.error }}
    </n-alert>
    <n-alert v-if="settingsState.notice" class="notice" type="info" :show-icon="true">
      {{ settingsState.notice }}
    </n-alert>

    <n-card v-if="settingsState.loading && !settingsState.snapshot" class="page-card">
      <div class="empty-state">正在读取配置…</div>
    </n-card>

    <template v-if="settingsState.snapshot && settingsState.draft">
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">日常编辑</p>
            <h2>命名模型</h2>
            <p class="subtle-text">灰色文字是默认提示，可直接输入；留空沿用默认值，已有自定义值正常显示。修改先留在草稿，保存才提交。</p>
          </div>
          <div class="inline-add-profile">
            <router-link class="runtime-entry" to="/runtime">运行管理</router-link>
            <label class="sr-only" for="new-profile-name">新模型配置名</label>
            <n-input v-model:value="newProfileName" class="compact-input" :disabled="fieldsDisabled" placeholder="配置名（可选）" :input-props="{ id: 'new-profile-name', 'aria-label': '新模型配置名' }" @keyup.enter="runAddProfile" />
            <n-button type="primary" :disabled="fieldsDisabled" @click="runAddProfile">新增模型
            </n-button>
            <p class="form-hint">默认自动生成 profile-1、profile-2…；可填 1–64 位字母、数字、_ 或 -，最多 16 个。</p>
          </div>
        </div>
        <div class="split-layout settings-editor-layout">
          <div>
            <div class="profile-list" aria-label="模型配置列表">
              <button
                v-for="name in profileNames()"
                :key="name"
                type="button"
                class="profile-option"
                :class="{ 'is-active': name === settingsState.selectedProfile }"
                :aria-pressed="name === settingsState.selectedProfile"
                :disabled="fieldsDisabled"
                @click="chooseProfile(name)"
              >
                <span class="profile-name">{{ name }}{{ name === settingsState.draft.active_model ? ' · 默认' : '' }}</span>
                <span class="profile-format">{{ settingsState.draft?.models[name]?.api_format }}</span>
              </button>
            </div>
            <p v-if="!profileNames().length" class="empty-state">还没有模型配置。</p>
          </div>

          <div v-if="currentProfile" class="profile-editor">
            <div class="section-heading">
              <h3>{{ settingsState.selectedProfile }} · 正在编辑</h3>
              <n-button size="small" type="error" secondary :disabled="fieldsDisabled || profileNames().length <= 1" @click="requestDeleteProfile">删除模型</n-button>
            </div>
            <div class="form-grid">
              <div class="form-field">
                <label class="field-label" for="profile-format">接口格式</label>
                <n-select id="profile-format" :value="currentProfile.api_format" :options="formatOptions" :disabled="fieldsDisabled" @update:value="changeProfileFormat" />
                <p class="form-hint">默认：Anthropic Messages。按服务端协议选择对应格式。</p>
              </div>
              <div class="form-field form-field-wide">
                <label class="field-label" for="profile-endpoint">Endpoint</label>
                <n-input :value="textValue(currentProfile.endpoint, currentEndpointPreset, 'endpoint')" :placeholder="currentEndpointPreset || profileDefaults.endpoint" :disabled="fieldsDisabled" :input-props="{ id: 'profile-endpoint', 'aria-label': '模型 Endpoint' }" @update:value="(value) => changeProfileText('endpoint', value)" />
                <n-button size="small" quaternary :disabled="fieldsDisabled" @click="useEndpointPreset">使用官方预设</n-button>
                <p v-if="customEndpoint" class="form-hint">当前使用自定义地址；切换接口格式会保留此地址。需要官方地址时点击“使用官方预设”。</p>
                <p v-else class="form-hint">当前格式的官方预设：{{ currentEndpointPreset }}。可填根地址或 /v1 基址，保存时自动补全；其他自定义完整路径保留。</p>
              </div>

            </div>
            <div class="form-field">
              <label class="field-label" for="catalog-key">API key</label>
              <n-input v-model:value="catalogKey" type="password" :input-props="{id: 'catalog-key', autocomplete: 'off'}" placeholder="填写 API key，可查询列表或单独保存为运行密钥" :disabled="fieldsDisabled || catalogBusy" />
              <p class="form-hint">查询会联网发送 key 到上方地址。已保存的相同地址可留空使用运行密钥；新地址须填写。临时 key 分模型保留，离开本页或注销清空。可手填未列出的模型。</p>
              <n-button :loading="catalogBusy" :disabled="fieldsDisabled || catalogBusy" @click="fetchCatalog">自动获取模型列表</n-button>
              <div class="form-field">
                <label class="field-label" for="profile-model">完整模型 ID</label>
                <n-input :value="textValue(currentProfile.model, profileDefaults.model, 'model')" placeholder="填写真实模型 ID（离线占位：offline-model）" :disabled="fieldsDisabled" :input-props="{ id: 'profile-model', 'aria-label': '完整模型 ID' }" @update:value="(value) => changeProfileText('model', value)" />
                <n-select v-if="catalogModels.length" aria-label="已获取的模型列表" :value="currentProfile.model" :options="catalogModels.map(value => ({label: catalogNames[value] ? `${catalogNames[value]} · ${value}` : value, value}))" filterable placeholder="搜索并选择已获取的模型" :disabled="fieldsDisabled" @update:value="value => value && changeProfileText('model', value)" />
                <p class="form-hint">offline-model 仅为离线占位，不能作为真实模型调用。请填写或选择服务商返回的完整 ID。</p>
              </div>
              <p v-if="officialDeepSeek" class="form-hint">2026-09-16 核查：deepseek-flash 为含视觉能力的 V4.1 Flash；旧 vision ID 已退役兼容转发，v4-pro 也转发 flash。供应商支持视觉 ≠ 本 Bot 已接入图片。<a href="https://api-docs.deepseek.com/updates/" target="_blank" rel="noopener noreferrer">官方更新</a></p>
              <p v-if="catalogMessage" class="form-hint" role="status">{{ catalogMessage }}</p>
              <p class="form-hint">保存会提交全部配置草稿，并仅保存当前编辑模型的 key；其他模型的临时 key 需分别保存。留空保留已有密钥。环境变量优先，密钥绑定地址且不进入配置导出。</p>
              <div class="button-row">
                <n-button type="primary" :disabled="!canSave" :loading="saveFlowBusy" @click="runSave">保存配置与密钥</n-button>
                <n-button :disabled="fieldsDisabled || hasSettingsDraftChanges()" :loading="modelActionBusy" @click="modelAction('model-check')">验证真实模型</n-button>
              </div>
              <p class="form-hint">验证使用已保存配置及运行密钥，发送一条固定测试文本；不附带历史、不发送QQ。可能消耗少量模型额度。</p>
              <p v-if="modelActionMessage" class="form-hint" role="status">{{ modelActionMessage }}</p>
            </div>
            <details class="settings-details">
              <summary>高级模型选项 <small>输出、推理、温度和上下文</small></summary>
              <div class="details-body">
                <div class="form-grid">
                  <div class="form-field">
                <label class="field-label" for="profile-key-env">API key 环境变量</label>
                <n-input :value="textValue(currentProfile.api_key_env, profileDefaults.api_key_env, 'api_key_env')" placeholder="例如 OMUBOT_MODEL_KEY" :disabled="fieldsDisabled" :input-props="{ id: 'profile-key-env', 'aria-label': 'API key 环境变量' }" @update:value="(value) => changeProfileText('api_key_env', value)" />
                <p class="form-hint">默认：OMUBOT_MODEL_KEY。这里只填环境变量名，不是 API key；密钥留在运行环境。</p>
              </div>
                  <div class="form-field">
                    <label class="field-label" for="profile-output">最大输出 token</label>
                    <n-input-number id="profile-output" :value="numberValue(currentProfile.max_output_tokens, 1024, 'max_output_tokens')" placeholder="默认 1024" :min="1" :disabled="fieldsDisabled" @update:value="(value) => changeProfileNumber('max_output_tokens', value)" />
                    <p class="form-hint">默认：1024 token；范围 1–32768，控制单次模型输出上限。</p>
                  </div>
                  <div class="form-field">
                    <label class="field-label" for="profile-reasoning">推理强度</label>
                    <n-select id="profile-reasoning" :value="currentProfile.reasoning_effort" :options="reasoningOptions" clearable placeholder="不指定" :disabled="fieldsDisabled" @update:value="changeProfileReasoning" />
                    <p class="form-hint">默认：未指定。Anthropic 不支持此项；DeepSeek 需同时开启思考。</p>
                  </div>
                  <div class="form-field">
                    <label class="field-label" for="profile-token-param">Token 参数名</label>
                    <n-select id="profile-token-param" :value="currentProfile.token_parameter" :options="tokenParameterOptions" clearable placeholder="自动" :disabled="fieldsDisabled" @update:value="changeProfileTokenParameter" />
                    <p class="form-hint">默认：自动。仅 openai_chat 可指定 max_tokens 或 max_completion_tokens。</p>
                  </div>
                  <div class="form-field">
                    <label class="field-label" for="profile-temperature">Temperature</label>
                    <n-input-number id="profile-temperature" :value="currentProfile.temperature" :min="0" :max="2" :step="0.1" clearable :disabled="fieldsDisabled" @update:value="(value) => changeProfileNumber('temperature', value)" />
                    <p class="form-hint">默认：未指定；范围 0–2。Anthropic 还要求不超过 1。</p>
                  </div>
                </div>
                <div class="toggle-grid">
                  <label class="toggle-row">
                    <n-switch :value="currentProfile.send_history" :disabled="fieldsDisabled" @update:value="(value) => changeProfileBoolean('send_history', value)" />
                    <span><strong>发送历史消息</strong><small>默认：开启。让模型读取本轮上下文；思考模式必须关闭。</small></span>
                  </label>
                  <label class="toggle-row">
                    <n-switch :value="currentProfile.thinking" :disabled="fieldsDisabled" @update:value="(value) => changeProfileBoolean('thinking', value)" />
                    <span><strong>启用模型思考</strong><small>默认：关闭。仅 DeepSeek 支持，并要求历史消息关闭、Temperature 未指定。</small></span>
                  </label>
                  <label class="toggle-row">
                    <n-switch :value="currentProfile.vision_enabled" :disabled="fieldsDisabled" @update:value="(value) => changeProfileBoolean('vision_enabled', value)" />
                    <span><strong>允许此模型接收图片</strong><small>默认：关闭。开启仍需来源方的 <code>media.read</code> 与模型授权 <code>allow_images</code>。</small></span>
                  </label>
                </div>
            <p class="form-hint">图片还需获准的受控字节来源和来源权限；当前支持显式白名单内的 HTTPS 图片，不读取容器图片路径。</p>
              </div>
            </details>
          </div>
        </div>

        <n-modal :show="Boolean(settingsState.pendingDeleteProfile)" preset="dialog" title="删除模型配置"
          positive-text="删除草稿模型" negative-text="取消" :positive-button-props="{ disabled: fieldsDisabled }" @positive-click="deleteProfile" @negative-click="cancelDeleteProfile"
          @update:show="value => !value && cancelDeleteProfile()">
          <p>删除“{{ settingsState.pendingDeleteProfile }}”？</p><p>{{ deleteImpact }}</p>
        </n-modal>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">运行绑定</p>
            <h2>任务与连接</h2>
          </div>
          <n-tag v-if="settingsState.draft.thinker_enabled" type="success" size="small">Thinker 已启用</n-tag>
          <n-tag v-else type="default" size="small">Thinker 未启用</n-tag>
        </div>
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label" for="active-model">默认模型</label>
            <n-select id="active-model" :value="settingsState.draft.active_model" :options="profileOptions" :disabled="fieldsDisabled" @update:value="(value) => value && updateConfigField('active_model', value)" />
            <p class="form-hint">默认：default（初始命名模型）。未单独绑定任务时使用此模型，普通回复也可单独覆盖；此处显示当前草稿。</p>
          </div>
          <div class="form-field toggle-field">
            <span class="field-label">Thinker 任务</span>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.thinker_enabled" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('thinker_enabled', value)" />
              <span><strong>允许思考任务运行</strong><small>默认：开启。授权页按运行中的任务模型生成范围。</small></span>
            </label>
          </div>
          <div class="form-field form-field-wide toggle-field">
            <span class="field-label">强 @ 回复</span>
            <label class="toggle-row">
              <n-switch
                :value="settingsState.draft.mention_force_reply_enabled ?? true"
                :disabled="fieldsDisabled"
                aria-label="启用强 @ 回复"
                @update:value="(value) => changeConfigBoolean('mention_force_reply_enabled', value)"
              />
              <span><strong>直接 @ Bot 时使用强回复规则</strong><small>默认开启；关闭后进入普通群聊参与规则。</small></span>
            </label>
            <p class="form-hint">此开关只控制直接 @ Bot 的强规则；引用 Bot 回复仍独立使用强规则。保存后需重启才影响运行版。</p>
          </div>
          <div v-for="task in taskRows" :key="task.key" class="form-field">
            <label class="field-label" :for="`task-${task.key}`">{{ task.label }}模型</label>
            <n-select :id="`task-${task.key}`" :value="settingsState.draft.task_models[task.key] ?? null" :options="profileOptions" clearable placeholder="使用默认模型" :disabled="fieldsDisabled || (task.key === 'thinker' && !settingsState.draft.thinker_enabled)" @update:value="(value) => changeTask(task.key, value)" />
            <p class="form-hint">{{ task.help }}</p>
          </div>
          <div class="form-field form-field-wide">
            <div class="toggle-field">
              <span class="field-label">私聊会话</span>
              <label class="toggle-row">
                <n-switch :value="settingsState.draft.private_conversation_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('private_conversation_enabled', value)" />
                <span><strong>允许精确名单用户发起私聊</strong><small>默认关闭；保存后按现有配置流程重启生效，权限仍需在权限页面单独授予。</small></span>
              </label>
            </div>
            <p class="form-hint">{{ privateRuntimeSummary }}。私聊使用实例角色，群记忆和群角色资料保持隔离。</p>
            <label class="field-label" for="private-conversation-peers">私聊用户 ID 名单</label>
            <n-input id="private-conversation-peers" :value="(settingsState.draft.private_conversation_peers ?? []).join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="fieldsDisabled" placeholder="精确用户 ID，逗号或换行分隔" @update:value="changePrivatePeers" />
            <p class="form-hint">启用时必须有精确用户 ID；不支持通配符。保存配置不会提前开放运行版本的名单。</p>
          </div>
          <div class="form-field form-field-wide">
            <span class="field-label">只读运行观测</span>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.context_observation_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('context_observation_enabled', value)" />
              <span><strong>记录请求上下文与预算计数</strong><small>默认关闭；仅记录实际请求的有界计数，不保留正文或用户标识。管理员可在诊断页面查看。</small></span>
            </label>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.graph_observation_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('graph_observation_enabled', value)" />
              <span><strong>开放图谱只读健康快照</strong><small>默认关闭；管理员按精确群查询当前来源有效性，不自动修复或重新索引。</small></span>
            </label>
            <p class="form-hint">保存后按现有配置流程重启生效。运行版本：上下文观测{{ settingsState.snapshot?.effective_config.context_observation_enabled ? '开启' : '关闭' }}，图谱快照{{ settingsState.snapshot?.effective_config.graph_observation_enabled ? '开启' : '关闭' }}。</p>
          </div>
          <div class="form-field form-field-wide">
            <span class="field-label">非个人内容跨群共享</span>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.cross_group_sharing_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('cross_group_sharing_enabled', value)" />
              <span><strong>消费明确授权的非个人投影</strong><small>默认关闭。每条授权只覆盖选定来源群到目标群的知识文档、黑话或通用表达；个人事实、私聊、原文及关系不共享。</small></span>
            </label>
            <p class="form-hint">权限页面管理具体对象及有效期。保存配置后按现有流程生效；运行版本：{{ settingsState.snapshot?.effective_config.cross_group_sharing_enabled ? '开启' : '关闭' }}。</p>
          </div>
          <ContactSettingsPanel :value="settingsState.draft.proactive_contact ?? null" :base-revision="settingsState.baseRevision" :disabled="fieldsDisabled" @update="value => updateConfigField('proactive_contact', value)" />
          <div class="form-field form-field-wide memory-capture-settings">
            <div class="toggle-field">
              <span class="field-label">N6 结构化记忆采集</span>
              <label class="toggle-row">
                <n-switch :value="settingsState.draft.memory_capture_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('memory_capture_enabled', value)" />
                <span><strong>允许采集白名单群的纯文字</strong><small>默认关闭；重启后生效。首版会跳过带 @、引用或媒体的消息，只产生待审候选，不自动批准或应用。</small></span>
              </label>
            </div>
            <p class="form-hint">{{ effectiveMemorySummary }}。未知 QQ Bot 无可靠自动识别；请维护 known_bot_ids。运行版状态与已保存草稿分开显示。</p>
            <div class="form-field">
              <label class="field-label" for="memory-capture-groups">精确群 ID 白名单</label>
              <n-input id="memory-capture-groups" :value="draftMemoryGroups.join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" placeholder="例如 20001, 20002；可用逗号或换行分隔" :disabled="fieldsDisabled" @update:value="changeMemoryGroups" />
              <p class="form-hint">只匹配完整群 ID；不能留空启用。作者与 Bot 还必须同时具备同群 message.read、memory.archive、memory.learn 权限。</p>
            </div>
            <div class="form-grid">
              <div class="form-field">
                <label class="field-label" for="memory-spool-dir">加密临时目录</label>
                <n-input id="memory-spool-dir" :value="settingsState.draft.memory_spool_dir ?? ''" placeholder="../memory-spool" :disabled="fieldsDisabled" @update:value="(value) => changeMemoryPath('memory_spool_dir', value)" />
                <p class="form-hint">相对路径以 SQLite 文件所在目录为基准；必须位于数据库目录外、旧项目外，并使用独立私有目录。</p>
              </div>
              <div class="form-field">
                <label class="field-label" for="memory-key-file">加密密钥文件路径</label>
                <n-input id="memory-key-file" :value="settingsState.draft.memory_key_file ?? ''" placeholder="../memory-key/archive.key" :disabled="fieldsDisabled" @update:value="(value) => changeMemoryPath('memory_key_file', value)" />
                <p class="form-hint">密钥内容不在网页中输入。启用前需预先安全创建独立 0600 文件；系统不会生成缺失密钥。</p>
              </div>
            </div>
          </div>
          <div class="form-field form-field-wide">
            <label class="field-label" for="onebot-endpoint">OneBot Endpoint</label>
            <n-input id="onebot-endpoint" :value="textValue(settingsState.draft.onebot_endpoint, configTextDefaults.onebot_endpoint, 'onebot_endpoint')" placeholder="http://127.0.0.1:3000" :disabled="fieldsDisabled" @update:value="(value) => changeConfigText('onebot_endpoint', value)" />
            <p class="form-hint">默认：http://127.0.0.1:3000。请填完整 Endpoint；本地 HTTP 可用，外部地址需 HTTPS。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="onebot-token-env">OneBot token 环境变量</label>
            <n-input id="onebot-token-env" :value="textValue(settingsState.draft.onebot_token_env, configTextDefaults.onebot_token_env, 'onebot_token_env')" placeholder="OMUBOT_ONEBOT_TOKEN" :disabled="fieldsDisabled" @update:value="(value) => changeConfigText('onebot_token_env', value)" />
            <p class="form-hint">默认：OMUBOT_ONEBOT_TOKEN。这里只填环境变量名，不是 OneBot token。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="timezone">时区</label>
            <n-input id="timezone" :value="textValue(settingsState.draft.timezone, configTextDefaults.timezone, 'timezone')" placeholder="Asia/Shanghai" :disabled="fieldsDisabled" @update:value="(value) => changeConfigText('timezone', value)" />
            <p class="form-hint">默认：Asia/Shanghai。使用 IANA 时区名，仅决定时间工具使用的时区。</p>
          </div>
          <div class="form-field form-field-wide">
            <label class="field-label" for="climate-mode">Climate 模式</label>
            <n-select id="climate-mode" :value="draftClimateMode" :options="climateModeOptions" :disabled="fieldsDisabled" @update:value="changeClimateMode" />
            <p class="form-hint">{{ climateModeDetails[draftClimateMode] }}</p>
            <p class="form-hint">当前草稿：{{ climateModeNames[draftClimateMode] }}；运行版：{{ climateModeNames[runningClimateMode] }}。保存只更新已保存版本；重启或运行管理中的应用后，运行版才会切换。</p>
          </div>
          <div class="form-field form-field-wide">
            <span class="field-label">群聊流式文字回复</span>
            <label class="toggle-row">
              <n-switch
                :value="settingsState.draft.stream_reply_enabled ?? false"
                :disabled="fieldsDisabled"
                aria-label="启用群聊流式文字回复"
                @update:value="(value) => changeConfigBoolean('stream_reply_enabled', value)"
              />
              <span><strong>启用流式回复</strong><small>默认关闭；保存配置后重启才会影响运行版。</small></span>
            </label>
            <p class="form-hint">OpenAI Chat、Responses、Anthropic 和 DeepSeek 已接入文字流式适配；有图片或 <code>tool_capabilities</code> 非空（例如默认的 <code>clock.read</code>）时，继续走普通路径。离线模型使用普通回复。</p>
            <p class="form-hint">草稿：{{ draftStreamReplyStatus }}；运行版：{{ runningStreamReplyStatus }}。保存仍使用当前配置版本 CAS，已保存配置与运行配置分开显示。</p>
          </div>
        </div>
        <details class="settings-details">
          <summary>高级运行参数 <small>队列和各项超时</small></summary>
          <div class="details-body">
            <div class="form-grid">
          <div class="form-field">
            <span class="field-label">分段预生成回复</span>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.planned_reply_enabled ?? false" :disabled="fieldsDisabled" aria-label="启用分段预生成回复" @update:value="(value) => changeConfigBoolean('planned_reply_enabled', value)" />
              <span><strong>先准备全部段落再发送</strong><small>默认关闭；启用会增加模型调用次数。</small></span>
            </label>
            <p class="form-hint">仅白名单内主动参与的群文字回复适用。候选准备完毕后才开始发送，相关新消息可以打断待发段落；需要业务工具的对话沿用常规路径。</p>
            <label class="field-label" for="planned-reply-groups">允许群</label>
            <n-input id="planned-reply-groups" :value="(settingsState.draft.planned_reply_groups ?? []).join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="fieldsDisabled" placeholder="精确群 ID，逗号或换行分隔" @update:value="changePlannedReplyGroups" />
            <p class="form-hint">草稿：{{ draftPlannedReplyStatus }}；运行版：{{ runningPlannedReplyStatus }}。保存更新已保存版本，应用配置或重启后运行版才会切换。</p>
          </div>
          <div v-for="feature in scopedFeatures" :key="feature.enabled" class="form-field">
            <span class="field-label">{{ feature.label }}</span>
            <label class="toggle-row">
              <n-switch :value="settingsState.draft[feature.enabled]" :disabled="fieldsDisabled" :aria-label="'启用' + feature.label" @update:value="(value) => changeConfigBoolean(feature.enabled, value)" />
              <span><strong>启用{{ feature.label }}</strong><small>默认关闭，仅准确群名单适用。</small></span>
            </label>
            <p class="form-hint">{{ feature.help }}</p>
            <label class="field-label" :for="feature.groups">允许群</label>
            <n-input :id="feature.groups" :value="(settingsState.draft[feature.groups] ?? []).join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="fieldsDisabled" placeholder="精确群 ID，逗号或换行分隔" @update:value="(value) => changeScopedFeatureGroups(feature.groups, value)" />
            <p class="form-hint">草稿：{{ scopedFeatureStatus(settingsState.draft, feature) }}；运行版：{{ scopedFeatureStatus(settingsState.snapshot?.effective_config ?? null, feature) }}。应用配置后才会切换运行版。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="queue-capacity">队列容量</label>
            <n-input-number id="queue-capacity" :value="numberValue(settingsState.draft.queue_capacity, configNumberDefaults.queue_capacity, 'queue_capacity')" :placeholder="'默认 ' + configNumberDefaults.queue_capacity" :min="1" :max="256" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber('queue_capacity', value)" />
            <p class="form-hint">默认：16。等待处理的任务数上限，范围 1–256。</p>
          </div>
          <div class="form-field form-field-wide">
            <span class="field-label">QQ 发送额度</span>
            <p class="form-hint">真实 QQ 发送统一受治理。此处只能收紧初始额度；保存后应用配置或重启才切换运行策略。暂停与解除请到运行管理。</p>
            <div class="form-grid">
              <div v-for="field in qqFields" :key="field.key" class="form-field">
                <label class="field-label" :for="'qq-' + field.key">{{ field.label }}</label>
                <n-input-number :id="'qq-' + field.key" :value="settingsState.draft.qq_delivery_limits?.[field.key] ?? qqDefaults[field.key]"
                  :min="field.min" :max="field.max" :disabled="fieldsDisabled" @update:value="(value) => changeQQLimit(field.key, value)" />
                <p class="form-hint">运行版：{{ settingsState.snapshot?.effective_config.qq_delivery_limits?.[field.key] ?? qqDefaults[field.key] }}。</p>
              </div>
            </div>
          </div>
          <div class="form-field">
            <label class="field-label" for="total-timeout">轮次整体期限（秒）</label>
            <n-input-number id="total-timeout" :value="numberValue(settingsState.draft.total_timeout, configNumberDefaults.total_timeout, 'total_timeout')" :placeholder="'默认 ' + configNumberDefaults.total_timeout" :min="0.1" :max="105" :step="0.1" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber('total_timeout', value)" />
            <p class="form-hint">新配置默认 105 秒；从本轮提交起计，始终约束各阶段。旧保存的较短期限保持原值；资料或权限到期可更早结束。</p>
          </div>
          <div v-for="field in replyPhaseFields" :key="field.key" class="form-field">
            <label class="field-label" :for="field.key">{{ field.label }}（秒）</label>
            <n-input-number :id="field.key" :value="numberValue(settingsState.draft[field.key] ?? null, configNumberDefaults[field.key], field.key)" :placeholder="'默认 ' + configNumberDefaults[field.key]" :min="0.1" :max="field.max" :step="0.1" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber(field.key, value)" />
            <p class="form-hint">{{ field.help }} 只能收紧，最多 {{ field.max }} 秒。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="model-timeout">模型超时（秒）</label>
            <n-input-number id="model-timeout" :value="numberValue(settingsState.draft.model_timeout, configNumberDefaults.model_timeout, 'model_timeout')" :placeholder="'默认 ' + configNumberDefaults.model_timeout" :min="0.1" :max="60" :step="0.1" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber('model_timeout', value)" />
            <p class="form-hint">默认：20 秒；等待模型响应的时限，范围 0.1–60 秒。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="send-timeout">发送超时（秒）</label>
            <n-input-number id="send-timeout" :value="numberValue(settingsState.draft.send_timeout, configNumberDefaults.send_timeout, 'send_timeout')" :placeholder="'默认 ' + configNumberDefaults.send_timeout" :min="0.1" :max="30" :step="0.1" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber('send_timeout', value)" />
            <p class="form-hint">默认：5 秒；发送回复的时限，范围 0.1–30 秒。</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="history-ttl">历史保留（秒）</label>
            <n-input-number id="history-ttl" :value="numberValue(settingsState.draft.history_ttl, configNumberDefaults.history_ttl, 'history_ttl')" :placeholder="'默认 ' + configNumberDefaults.history_ttl" :min="0.1" :max="3600" :step="1" :disabled="fieldsDisabled" @update:value="(value) => changeConfigNumber('history_ttl', value)" />
            <p class="form-hint">默认：900 秒；会话历史的保留时长，范围 0.1–3600 秒。</p>
          </div>
            </div>
          </div>
        </details>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">食物推荐</p>
            <h2>菜单与联网参考</h2>
            <p class="subtle-text">推荐始终从本地菜单选择，保存的个人偏好和地点不会上传搜索服务。保存后需应用配置版本。</p>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-field toggle-field">
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.food_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('food_enabled', value)" />
              <span><strong>启用食物指令</strong><small>默认关闭；仅精确群名单内生效。</small></span>
            </label>
          </div>
          <div class="form-field toggle-field">
            <label class="toggle-row">
              <n-switch :value="settingsState.draft.food_search_enabled ?? false" :disabled="fieldsDisabled" @update:value="(value) => changeConfigBoolean('food_search_enabled', value)" />
              <span><strong>使用联网参考</strong><small>默认关闭；随机选择不联网。搜索不可用时会明确显示本地推荐提示。</small></span>
            </label>
          </div>
          <div class="form-field">
            <label class="field-label" for="food-groups">食物指令群名单</label>
            <n-input id="food-groups" :value="(settingsState.draft.food_groups ?? []).join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="fieldsDisabled" placeholder="精确群 ID，逗号或换行分隔" @update:value="changeFoodGroups" />
          </div>
          <div class="form-field">
            <label class="field-label" for="search-endpoint">SearXNG 地址</label>
            <n-input id="search-endpoint" :value="settingsState.draft.search_endpoint ?? ''" :disabled="fieldsDisabled" placeholder="https://search.example/search" @update:value="(value) => updateConfigField('search_endpoint', value.trim())" />
            <p class="form-hint">联网还需在高级配置启用 network.search，并在权限页面授予该实际搜索目的地许可。</p>
          </div>
        </div>
        <p class="form-hint">运行版本：食物指令{{ settingsState.snapshot?.effective_config.food_enabled ? '开启' : '关闭' }}，联网参考{{ settingsState.snapshot?.effective_config.food_search_enabled ? '开启' : '关闭' }}；允许群 {{ settingsState.snapshot?.effective_config.food_groups?.join('、') || '空' }}。</p>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">N7 Worldbook</p>
            <h2>角色世界书门</h2>
            <p class="subtle-text">角色生活与日记开关属于同一份配置草稿，默认关闭、群名单为空。已提交的日程和故事可参与授权范围内的聊天；日记仅提供人工虚构草稿、审批和本地预览。</p>
          </div>
          <n-tag :type="draftWorldbookScheduleReady ? 'success' : 'default'" size="small">
            {{ draftWorldbookScheduleReady ? '日程配置组合已填写' : '日程配置组合未完成' }}
          </n-tag>
        </div>
        <div class="worldbook-gate-list">
          <div v-for="gate in worldbookGateRows" :key="gate.key" class="worldbook-gate-row">
            <div>
              <span class="field-label">{{ gate.label }}</span>
              <p class="form-hint">{{ gate.help }}</p>
            </div>
            <n-switch
              :value="Boolean(settingsState.draft[gate.key])"
              :disabled="fieldsDisabled"
              :aria-label="gate.label"
              @update:value="(value) => changeWorldbookGate(gate.key, value)"
            />
          </div>
        </div>
        <div class="form-grid worldbook-group-add">
          <div class="form-field">
            <label class="field-label" for="new-worldbook-group-id">日程/Worldbook 群白名单</label>
            <n-input id="new-worldbook-group-id" v-model:value="newWorldbookGroupId" maxlength="64" placeholder="例如 123456789" :disabled="fieldsDisabled || draftWorldbookGroups.length >= 128" />
            <p class="form-hint">只允许精确群 ID；空白名单严格拒绝所有群，不代表全群。</p>
          </div>
          <div class="form-field worldbook-group-action">
            <span class="field-label" aria-hidden="true">&nbsp;</span>
            <n-button type="primary" :disabled="fieldsDisabled || draftWorldbookGroups.length >= 128" @click="addWorldbookGroup">加入白名单</n-button>
          </div>
        </div>
        <div v-if="draftWorldbookGroups.length" class="worldbook-group-list">
          <div v-for="groupId in draftWorldbookGroups" :key="groupId" class="worldbook-group-row">
            <code class="group-mode-id">{{ groupId }}</code>
            <n-button size="small" :disabled="fieldsDisabled" @click="removeWorldbookGroup(groupId)">移除</n-button>
          </div>
        </div>
        <p v-if="worldbookGroupError" class="form-hint group-mode-error" role="alert">{{ worldbookGroupError }}</p>
        <div class="form-field">
          <label class="field-label" for="journal-allowed-groups">日记群名单</label>
          <n-input id="journal-allowed-groups" :value="(settingsState.draft.journal_allowed_groups ?? []).join(', ')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :disabled="fieldsDisabled" placeholder="精确群 ID，逗号或换行分隔" @update:value="changeJournalGroups" />
          <p class="form-hint">事实日志使用日记群名单，虚构成稿还需 Worldbook 群名单。运行版本：{{ settingsState.snapshot?.effective_config.journal_enabled ? '开启' : '关闭' }}；日记群 {{ settingsState.snapshot?.effective_config.journal_allowed_groups?.join('、') || '空' }}。</p>
        </div>
        <div class="form-field">
          <label class="field-label">允许真实发布</label>
          <n-switch :value="settingsState.draft.journal_allow_live_publish ?? false" :disabled="fieldsDisabled" @update:value="value => updateConfigField('journal_allow_live_publish', value)" />
          <p class="form-hint">默认关闭。保存不会启用运行中的发布，也不能代替账号与发布通道验证。运行版本：{{ settingsState.snapshot?.effective_config.journal_allow_live_publish ? '允许' : '关闭' }}。</p>
        </div>
        <div class="form-field">
          <label class="field-label" for="journal-live-accounts">允许发布的账号 ID（最多 8 个）</label>
          <n-input id="journal-live-accounts" :value="(settingsState.draft.journal_allowed_live_uins ?? []).join(', ')" :disabled="fieldsDisabled" placeholder="完整数字账号 ID，逗号或换行分隔" @update:value="changeJournalLiveAccounts" />
          <p class="form-hint">最多 8 个互不重复的完整数字账号 ID，不支持通配或昵称。运行账号名单：{{ settingsState.snapshot?.effective_config.journal_allowed_live_uins?.join('、') || '空' }}。当前真实发布是否可用，以日记页面读取的服务端状态为准。</p>
        </div>
        <n-alert class="group-mode-notice" type="info" :show-icon="true">
          草稿配置：{{ draftWorldbookScheduleReady ? '总门、日程投影门和至少一个群白名单已填写' : '日程配置组合未完成（需总门 + 日程投影门 + 精确群白名单）' }}；运行版配置：{{ runningWorldbookScheduleReady ? '已填写日程组合' : '未填写完整日程组合' }}。保存后需通过运行管理应用配置版本；权限仍单独生效。日记审批不会触发发布，真实发布仍需单独的运行验证和账号门禁。
        </n-alert>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">群聊接纳</p>
            <h2>群模式</h2>
            <p class="subtle-text">未列群默认为 active，保留现有行为。这里只设置群模式，不会授予任何读取、模型调用或发送权限。</p>
          </div>
          <n-tag size="small">{{ groupModeRows.length }} / 128 条规则</n-tag>
        </div>
        <div v-if="groupModeRows.length" class="group-mode-list">
          <div v-for="row in groupModeRows" :key="row[0]" class="group-mode-row">
            <code class="group-mode-id">{{ row[0] }}</code>
            <n-select
              :value="row[1]"
              :options="groupModeOptions"
              :disabled="fieldsDisabled"
              :aria-label="`群 ${row[0]} 的模式`"
              @update:value="(value) => changeGroupMode(row[0], value)"
            />
            <n-button :disabled="fieldsDisabled" @click="removeGroupMode(row[0])">删除规则</n-button>
          </div>
        </div>
        <div class="form-grid group-mode-add">
          <div class="form-field">
            <label class="field-label" for="new-group-mode-id">精确群 ID</label>
            <n-input id="new-group-mode-id" v-model:value="newGroupId" maxlength="64" placeholder="例如 123456789" :disabled="fieldsDisabled || groupModeRows.length >= 128" />
          </div>
          <div class="form-field">
            <label class="field-label" for="new-group-mode">模式</label>
            <n-select id="new-group-mode" v-model:value="newGroupMode" :options="groupModeOptions" :disabled="fieldsDisabled || groupModeRows.length >= 128" />
          </div>
          <div class="form-field group-mode-action">
            <span class="field-label" aria-hidden="true">&nbsp;</span>
            <n-button type="primary" :disabled="fieldsDisabled || groupModeRows.length >= 128" @click="addGroupMode">添加规则</n-button>
          </div>
        </div>
        <p v-if="groupModeError" class="form-hint group-mode-error" role="alert">{{ groupModeError }}</p>
        <n-alert class="group-mode-notice" type="info" :show-icon="true">
          active 按现有授权流程处理；silent 只允许在 message.read 已授权时做本地短期观察，不调用模型、不回复；off 不接纳群消息。配置保存后需重启生效。
        </n-alert>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">群聊人格补充</p>
            <h2>群回复风格</h2>
            <p class="subtle-text">按精确群 ID 配置可选回复风格和补充提示。“继承实例人格”或留空表示不加群级覆盖；“default”是显式选择默认回复风格。补充提示不会替换人格核心，也不会授予任何权限。</p>
          </div>
          <n-tag size="small">{{ groupProfileRows.length }} / 128 个群</n-tag>
        </div>
        <div v-if="groupProfileRows.length" class="group-profile-list">
          <div v-for="row in groupProfileRows" :key="row[0]" class="group-profile-row">
            <code class="group-mode-id">{{ row[0] }}</code>
            <n-select
              :value="row[1].reply_style ?? inheritGroupReplyStyle"
              :options="groupReplyStyleOptions"
              :disabled="fieldsDisabled"
              :aria-label="`群 ${row[0]} 的回复风格`"
              @update:value="(value) => changeGroupReplyStyle(row[0], value)"
            />
            <n-input
              type="textarea"
              :value="row[1].custom_prompt ?? ''"
              maxlength="6000"
              show-count
              :autosize="{ minRows: 1, maxRows: 3 }"
              placeholder="补充提示；留空时沿用实例人格"
              :disabled="fieldsDisabled"
              :aria-label="`群 ${row[0]} 的补充提示`"
              @update:value="(value) => changeGroupCustomPrompt(row[0], value)"
            />
            <n-button :disabled="fieldsDisabled" @click="removeGroupProfile(row[0])">删除 Profile</n-button>
          </div>
        </div>
        <div class="form-grid group-profile-add">
          <div class="form-field">
            <label class="field-label" for="new-group-profile-id">精确群 ID</label>
            <n-input id="new-group-profile-id" v-model:value="newGroupProfileId" maxlength="64" placeholder="例如 123456789" :disabled="fieldsDisabled || groupProfileRows.length >= 128" />
          </div>
          <div class="form-field group-mode-action">
            <span class="field-label" aria-hidden="true">&nbsp;</span>
            <n-button type="primary" :disabled="fieldsDisabled || groupProfileRows.length >= 128" @click="addGroupProfile">添加群 Profile</n-button>
          </div>
        </div>
        <p v-if="groupProfileError" class="form-hint group-mode-error" role="alert">{{ groupProfileError }}</p>
        <n-alert class="group-mode-notice" type="info" :show-icon="true">
          群 Profile 只保存 persona core 之外的风格与提示补充，system 总长度不能超过 6200 字；仍需独立通过 message.read、model.invoke 与 message.reply 授权门。配置保存后需重启生效。
        </n-alert>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">群聊日期上下文</p>
            <h2>生日、纪念日与特殊日</h2>
            <p class="subtle-text">每项只属于一个精确群 ID。可填公历 MM-DD，或选择每年固定月份、第几个（1–5／倒数 1–5）和星期（0=周一至 6=周日）；02-29 只在闰年匹配。</p>
          </div>
          <n-tag size="small">{{ groupCalendarCount }} 项 · {{ groupCalendarRows.length }} 个群</n-tag>
        </div>
        <div v-if="groupCalendarRows.length" class="group-calendar-list">
          <div v-for="row in groupCalendarRows" :key="row[0]" class="group-calendar-group">
            <div class="group-calendar-heading">
              <code class="group-mode-id">{{ row[0] }}</code>
              <span class="form-hint">{{ row[1].length }} 项</span>
            </div>
            <div
              v-for="(event, eventIndex) in row[1]"
              :key="`${event.subject_kind}-${event.subject_id ?? ''}-${event.date}-${event.category}-${eventIndex}`"
              class="group-calendar-event"
              :class="{ 'group-calendar-event-structured-rule': Boolean(event.weekday) || event.lunar?.kind === 'fixed' }"
            >
              <n-input
                :value="event.name"
                maxlength="80"
                :disabled="fieldsDisabled"
                :aria-label="`群 ${row[0]} 的事件名称`"
                @update:value="(value) => changeGroupCalendarEvent(row[0], eventIndex, 'name', value)"
              />
              <n-input
                v-if="event.date !== undefined && event.date !== null"
                :value="event.date"
                maxlength="5"
                placeholder="MM-DD"
                :disabled="fieldsDisabled"
                :aria-label="`群 ${row[0]} 的公历日期`"
                @update:value="(value) => changeGroupCalendarEvent(row[0], eventIndex, 'date', value)"
              />
              <div v-else-if="event.weekday" class="group-calendar-weekday-fields">
                <n-select
                  :value="event.weekday.month"
                  :options="calendarMonthOptions"
                  :disabled="fieldsDisabled"
                  :aria-label="`群 ${row[0]} 的事件月份`"
                  @update:value="(value) => changeGroupCalendarWeekday(row[0], eventIndex, 'month', value)"
                />
                <n-select
                  :value="event.weekday.ordinal"
                  :options="calendarOrdinalOptions"
                  :disabled="fieldsDisabled"
                  :aria-label="`群 ${row[0]} 的第几个星期`"
                  @update:value="(value) => changeGroupCalendarWeekday(row[0], eventIndex, 'ordinal', value)"
                />
                <n-select
                  :value="event.weekday.weekday"
                  :options="calendarWeekdayOptions"
                  :disabled="fieldsDisabled"
                  :aria-label="`群 ${row[0]} 的星期`"
                  @update:value="(value) => changeGroupCalendarWeekday(row[0], eventIndex, 'weekday', value)"
                />
              </div>
              <div v-else-if="event.lunar?.kind === 'fixed'" class="group-calendar-lunar-fields">
                <n-select
                  :value="event.lunar.month"
                  :options="calendarLunarMonthOptions"
                  :disabled="fieldsDisabled"
                  :aria-label="`群 ${row[0]} 的农历月份`"
                  @update:value="(value) => changeGroupCalendarLunar(row[0], eventIndex, 'month', value)"
                />
                <n-select
                  :value="event.lunar.day"
                  :options="calendarLunarDayOptions"
                  :disabled="fieldsDisabled"
                  :aria-label="`群 ${row[0]} 的农历日期`"
                  @update:value="(value) => changeGroupCalendarLunar(row[0], eventIndex, 'day', value)"
                />
                <label class="group-calendar-leap-month">
                  <n-switch
                    :value="event.lunar.leap_month"
                    :disabled="fieldsDisabled"
                    :aria-label="`群 ${row[0]} 的农历月份是否为闰月`"
                    @update:value="(value) => changeGroupCalendarLunar(row[0], eventIndex, 'leap_month', value)"
                  />
                  <span>{{ event.lunar.leap_month ? '闰月' : '普通月' }}</span>
                </label>
              </div>
              <span v-else class="group-calendar-rule-note">除夕 · 春节前一天</span>
              <n-select
                :value="event.category"
                :options="calendarCategoryOptions"
                :disabled="fieldsDisabled"
                :aria-label="`群 ${row[0]} 的事件类别`"
                @update:value="(value) => changeGroupCalendarEvent(row[0], eventIndex, 'category', value)"
              />
              <n-select
                :value="event.subject_kind"
                :options="calendarSubjectOptions"
                :disabled="fieldsDisabled"
                :aria-label="`群 ${row[0]} 的事件主体`"
                @update:value="(value) => changeGroupCalendarEvent(row[0], eventIndex, 'subject_kind', value)"
              />
              <n-input
                v-if="event.subject_kind === 'member'"
                :value="event.subject_id ?? ''"
                maxlength="64"
                placeholder="成员 ID"
                :disabled="fieldsDisabled"
                :aria-label="`群 ${row[0]} 的成员 ID`"
                @update:value="(value) => changeGroupCalendarEvent(row[0], eventIndex, 'subject_id', value)"
              />
              <span v-else class="group-calendar-subject-note">{{ event.subject_kind === 'bot' ? 'Bot 本人' : '当前群' }}</span>
              <n-button
                :disabled="fieldsDisabled"
                :aria-label="`删除群 ${row[0]} 的 ${event.name}事件`"
                @click="removeGroupCalendarEvent(row[0], eventIndex)"
              >删除</n-button>
            </div>
          </div>
        </div>
        <div class="form-grid group-calendar-add">
          <div class="form-field">
            <label class="field-label" for="new-calendar-group-id">精确群 ID</label>
            <n-input id="new-calendar-group-id" v-model:value="newCalendarGroupId" maxlength="64" placeholder="例如 123456789" :disabled="fieldsDisabled" />
          </div>
          <div class="form-field">
            <label class="field-label" for="new-calendar-name">名称</label>
            <n-input id="new-calendar-name" v-model:value="newCalendarName" maxlength="80" placeholder="群内称呼或事件名" :disabled="fieldsDisabled" />
          </div>
          <div class="form-field">
            <label class="field-label" for="new-calendar-rule-kind">日期规则</label>
            <n-select id="new-calendar-rule-kind" v-model:value="newCalendarRuleKind" :options="calendarRuleKindOptions" :disabled="fieldsDisabled" />
          </div>
          <div v-if="newCalendarRuleKind === 'fixed'" class="form-field">
            <label class="field-label" for="new-calendar-date">公历日期</label>
            <n-input id="new-calendar-date" v-model:value="newCalendarDate" maxlength="5" placeholder="MM-DD" :disabled="fieldsDisabled" />
          </div>
          <div v-else-if="newCalendarRuleKind === 'weekday'" class="form-field group-calendar-weekday-fields">
            <n-select v-model:value="newCalendarMonth" :options="calendarMonthOptions" placeholder="月份" :disabled="fieldsDisabled" aria-label="事件月份" />
            <n-select v-model:value="newCalendarOrdinal" :options="calendarOrdinalOptions" placeholder="第几个" :disabled="fieldsDisabled" aria-label="第几个星期" />
            <n-select v-model:value="newCalendarWeekday" :options="calendarWeekdayOptions" placeholder="星期" :disabled="fieldsDisabled" aria-label="星期" />
          </div>
          <div v-else-if="newCalendarRuleKind === 'lunar_fixed'" class="form-field">
            <label class="field-label">农历日期</label>
            <div class="group-calendar-lunar-fields">
              <n-select
                v-model:value="newCalendarLunarMonth"
                :options="calendarLunarMonthOptions"
                placeholder="月份"
                :disabled="fieldsDisabled"
                aria-label="农历月份"
              />
              <n-select
                v-model:value="newCalendarLunarDay"
                :options="calendarLunarDayOptions"
                placeholder="日期"
                :disabled="fieldsDisabled"
                aria-label="农历日期"
              />
              <label class="group-calendar-leap-month">
                <n-switch
                  :value="newCalendarLunarLeapMonth"
                  :disabled="fieldsDisabled"
                  aria-label="农历月份是否为闰月"
                  @update:value="(value) => newCalendarLunarLeapMonth = value"
                />
                <span>{{ newCalendarLunarLeapMonth ? '闰月' : '普通月' }}</span>
              </label>
            </div>
          </div>
          <p v-else class="form-hint group-calendar-rule-note">除夕按农历年最后一天匹配，也就是春节前一天。</p>
          <div class="form-field">
            <label class="field-label" for="new-calendar-category">类别</label>
            <n-select id="new-calendar-category" v-model:value="newCalendarCategory" :options="calendarCategoryOptions" :disabled="fieldsDisabled" />
          </div>
          <div class="form-field">
            <label class="field-label" for="new-calendar-subject-kind">主体</label>
            <n-select id="new-calendar-subject-kind" v-model:value="newCalendarSubjectKind" :options="calendarSubjectOptions" :disabled="fieldsDisabled" />
          </div>
          <div v-if="newCalendarSubjectKind === 'member'" class="form-field">
            <label class="field-label" for="new-calendar-subject-id">成员 ID</label>
            <n-input id="new-calendar-subject-id" v-model:value="newCalendarSubjectId" maxlength="64" placeholder="精确成员 ID" :disabled="fieldsDisabled" />
          </div>
          <div class="form-field group-calendar-add-action">
            <span class="field-label" aria-hidden="true">&nbsp;</span>
            <n-button type="primary" :disabled="fieldsDisabled" @click="addGroupCalendarEvent">添加事件</n-button>
          </div>
        </div>
        <p v-if="groupCalendarError" class="form-hint group-mode-error" role="alert">{{ groupCalendarError }}</p>
        <n-alert class="group-mode-notice" type="info" :show-icon="true">
          使用上方配置的时区 {{ settingsState.draft.timezone }}。周期规则每年只在指定月份匹配，“倒数第 1 个”表示该月最后一个指定星期；当月不存在的第 5 个不会触发。农历固定日可选普通月或闰月；除夕按农历年最后一天匹配，也就是春节前一天。成员事件必须指定成员 ID；Bot 本人必须显式选择“Bot 本人”。正式运行且启用 Climate 或配置了群日期时，会自动获取并缓存国务院年度节假日安排；获取失败不猜测假期。保存受当前配置 revision 保护，修改会等待运行配置应用后生效。
        </n-alert>
      </n-card>

      <n-card class="page-card settings-summary" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">保存与运行</p>
            <h2>当前配置版本</h2>
          </div>
          <n-tag :type="settingsState.snapshot.restart_required ? 'warning' : 'success'" size="small">
            {{ settingsState.snapshot.restart_required ? '等待重启生效' : '运行中已生效' }}
          </n-tag>
        </div>
        <div class="info-grid">
          <div class="info-block">
            <div class="label">已保存版本</div>
            <div class="value">{{ settingsState.snapshot.revision }}</div>
          </div>
          <div class="info-block">
            <div class="label">运行版本</div>
            <div class="value">{{ settingsState.snapshot.effective_revision }}</div>
          </div>
          <div class="info-block">
            <div class="label">待重启字段</div>
            <div class="value">{{ settingsState.snapshot.changed_fields.length ? settingsState.snapshot.changed_fields.join('、') : '无' }}</div>
          </div>
          <div class="info-block">
            <div class="label">可回滚版本</div>
            <div class="value">{{ settingsState.snapshot.versions.length ? settingsState.snapshot.versions.join('、') : '暂无历史' }}</div>
          </div>
        </div>
        <div class="button-row">
          <n-button :loading="settingsState.loading" :disabled="fieldsDisabled" @click="requestReload">重新读取</n-button>
          <div class="rollback-picker">
            <label class="field-label" for="rollback-target">回滚目标版本</label>
            <n-select id="rollback-target" v-model:value="rollbackTarget" class="compact-select" clearable :options="rollbackOptions" placeholder="选择历史版本" aria-label="回滚目标版本" :disabled="fieldsDisabled || !rollbackOptions.length" @update:value="onRollbackTarget" />
            <p class="form-hint">默认不选择；回滚会新建一个版本，不会自动重启。</p>
          </div>
          <n-button type="warning" :disabled="!canRollback" @click="runRollback">回滚到选定版本</n-button>
          <p v-if="hasSettingsDraftChanges()" class="form-hint">回滚前请先保存草稿，或重新读取以放弃草稿。</p>
        </div>
        <div v-if="settingsState.reloadPending" class="confirm-strip">
          <p>当前页面有未保存草稿。重新读取会放弃这些修改。</p>
          <div class="button-row">
            <n-button size="small" @click="cancelReload">继续编辑</n-button>
            <n-button size="small" type="warning" @click="confirmReload">放弃草稿并重新读取</n-button>
          </div>
        </div>
      </n-card>

      <n-card class="page-card" :bordered="false">
        <details class="settings-details advanced-details">
          <summary>高级 JSON <small>完整可编辑配置文档</small></summary>
          <p class="form-hint">可在JSON中编辑 element_custom_rules（id、有限正则pattern、reply、use_llm），以及 model_prices（精确provider/model、version、currency及四类每百万token价率）。价表空缺或用量未知时不估算费用。food_search_group_overrides只覆盖指定群，保存后按运行版本生效。</p>
          <div class="advanced-editor">
          <div class="section-heading">
            <div>
              <p class="eyebrow">完整文档</p>
              <h2>高级 JSON</h2>
              <p class="subtle-text">这里保留全部可编辑字段。修改文本后请明确应用到字段，页面不会自动覆盖未应用草稿。</p>
            </div>
            <n-tag v-if="settingsState.advancedEdited" type="warning" size="small">未应用</n-tag>
          </div>
          <n-input
            :value="settingsState.advancedText"
            type="textarea"
            :disabled="modelActionBusy || saveFlowBusy || settingsState.saving || settingsState.loading"
            :autosize="{ minRows: 12, maxRows: 30 }"
            placeholder="粘贴完整配置 JSON；必须包含全部可编辑字段和至少一个模型配置。"
            aria-label="完整配置 JSON"
            @update:value="markAdvancedText"
          />
          <p class="form-hint advanced-json-hint">未单独展示字段的默认值：reply_segment_chars=1000（每段字符数）、max_reply_segments=5（最多气泡，可收紧到1–5；旧保存值保持不变）、max_sessions=128（会话数）、tool_capabilities=[&quot;clock.read&quot;]（时间工具）、model_concurrency=4（模型并发）、thinker_reserve=1（Thinker 保留并发）、max_active_sessions=8（活跃会话）、max_interruptions=3（允许打断次数）。每个 models 配置的 vision_enabled 默认 false；开启仍需 media.read 与 allow_images，当前直接图片可通过显式 visual_url_hosts 白名单取得 HTTPS 字节，保持当前消息来源绑定、IP/TLS 校验、大小预算及独立图片权限；容器路径不会读取。强 @ 回复默认开启；关闭 mention_force_reply_enabled 后直接 @ Bot 进入普通群聊参与规则，Bot 回复引用的强规则保持独立。Bot 互刷熔断默认开启：bot_pair_loop_alt_threshold=10、bot_pair_known_alt_threshold=6（60 秒内方向翻转次数；阈值范围 1–127）、bot_pair_cooldown_seconds=60；known_bot_ids 只会对已知 Bot 使用更严的 6 次阈值。仅统计本 Bot 与同一用户的双向方向翻转，真人单向连发不触发；关闭熔断可将 bot_pair_guard_enabled 设为 false。RWS 默认 rws_mode=off；shadow 只记录，primary 仅裁决灰区，阈值由 rws_threshold 设置。无可用信号时的 0.5 是中性决策分数，不是概率，也不表示与旧调度行为等价；A 的 complete/hold 不作为 EOT 概率，EOT、Hawkes、记忆、reward 和 bandit 当前缺失或关闭；task_models 默认空对象，未绑定任务使用默认模型。搜索默认未配置；可在高级配置设置 search_endpoint 为 SearXNG 实例或 /search 地址，并在 tool_capabilities 增加 network.search，保存并应用后再独立授予搜索目标权限。visual_url_hosts 默认为空，仅填写明确允许的图片域名。</p>
          <p class="form-hint">N6 记忆采集默认关闭。需要在管理员配置中显式设置 memory_capture_enabled=true、精确 memory_capture_groups、memory_spool_dir 与 memory_key_file；模型通过 task_models.memory 绑定，启用后重启生效。密钥文件必须由管理员预先安全创建（0600），系统不会缺省生成密钥。配置只保存路径；密钥内容不进入配置、公开状态、诊断或日志。</p>
          <div class="button-row">
            <n-button type="primary" :disabled="modelActionBusy || saveFlowBusy || !settingsState.advancedEdited || settingsState.saving || settingsState.loading" @click="applyAdvancedText">应用 JSON 到字段</n-button>
            <n-text depth="3">应用后仍需使用页面底部的保存条提交。
            </n-text>
          </div>
          </div>
        </details>
      </n-card>
      <div class="settings-save-bar" role="region" aria-label="保存配置">
        <div>
          <strong>{{ hasSettingsDraftChanges() ? '有未保存的配置草稿' : '配置未修改' }}</strong>
          <span>基于版本 {{ settingsState.baseRevision }}；冲突时保留草稿。</span>
        </div>
        <n-button type="primary" :disabled="!canSave" :loading="saveFlowBusy" @click="runSave">保存配置与密钥
        </n-button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.runtime-entry {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}

.runtime-entry:hover {
  text-decoration: underline;
}

.group-mode-list {
  display: grid;
  gap: 10px;
  margin: 16px 0;
}

.group-mode-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(190px, 240px) auto;
  align-items: center;
  gap: 12px;
}

.group-mode-id {
  overflow-wrap: anywhere;
}

.group-mode-add {
  align-items: end;
}

.group-mode-action {
  justify-self: start;
}

.group-mode-error {
  color: var(--om-danger, #b42318);
}

.group-mode-notice {
  margin-top: 14px;
}

.worldbook-gate-list {
  display: grid;
  gap: 12px;
  margin: 16px 0;
}

.worldbook-gate-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--om-border, #e4e7ec);
  border-radius: 10px;
}

.worldbook-gate-row .form-hint {
  margin-bottom: 0;
}

.worldbook-group-add {
  align-items: end;
}

.worldbook-group-action {
  justify-self: start;
}

.worldbook-group-list {
  display: grid;
  gap: 8px;
  margin: 16px 0;
}

.worldbook-group-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--om-surface-subtle, #f7f8fa);
}

.group-profile-list {
  display: grid;
  gap: 12px;
  margin: 16px 0;
}

.group-profile-row {
  display: grid;
  grid-template-columns: minmax(100px, 0.7fr) minmax(180px, 1fr) minmax(240px, 2fr) auto;
  align-items: center;
  gap: 12px;
}

.group-profile-add {
  align-items: end;
}

.group-calendar-list {
  display: grid;
  gap: 14px;
  margin: 16px 0;
}

.group-calendar-group {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-surface-subtle, #f7f8fa);
}

.group-calendar-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.group-calendar-event {
  display: grid;
  grid-template-columns: minmax(120px, 1.2fr) 96px minmax(105px, 0.8fr) minmax(120px, 0.9fr) minmax(120px, 0.9fr) auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.group-calendar-event-structured-rule {
  grid-template-columns: minmax(120px, 1.2fr) minmax(210px, 1.6fr) minmax(105px, 0.8fr) minmax(120px, 0.9fr) minmax(120px, 0.9fr) auto;
}

.group-calendar-weekday-fields {
  display: grid;
  grid-template-columns: 0.8fr 1.2fr 1.2fr;
  gap: 4px;
  min-width: 0;
}

.group-calendar-lunar-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.group-calendar-leap-month {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.group-calendar-rule-note {
  color: var(--om-text-2);
  font-size: 12px;
}

.group-calendar-subject-note {
  color: var(--om-text-2);
  font-size: 12px;
}

.group-calendar-add {
  grid-template-columns: minmax(140px, 1fr) minmax(130px, 1fr) 100px minmax(110px, 0.8fr) minmax(120px, 0.9fr) minmax(120px, 0.9fr) auto;
  align-items: end;
}

.group-calendar-add-action {
  white-space: nowrap;
}

@media (max-width: 640px) {
  .group-mode-row {
    grid-template-columns: minmax(0, 1fr) minmax(140px, 1fr);
  }

  .group-mode-row :deep(.n-button) {
    grid-column: 2;
    justify-self: start;
  }

  .group-profile-row {
    grid-template-columns: minmax(0, 1fr) minmax(150px, 1fr);
  }

  .group-profile-row :deep(.n-input),
  .group-profile-row :deep(.n-button) {
    grid-column: 2;
  }

  .group-profile-row :deep(.n-button) {
    justify-self: start;
  }

  .group-calendar-event,
  .group-calendar-add {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .group-calendar-lunar-fields {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .group-calendar-leap-month {
    grid-column: 1 / -1;
  }

  .group-calendar-event :deep(.n-button),
  .group-calendar-add-action {
    grid-column: 2;
    justify-self: start;
  }

  .worldbook-gate-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
}
</style>

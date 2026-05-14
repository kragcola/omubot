<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  ChevronForwardOutline,
  PeopleOutline,
  RefreshOutline,
  SaveOutline,
  ShieldCheckmarkOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NTabPane,
  NTabs,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { api } from '../../api/client'
import {
  onGroupActivity,
  onGroupMessage,
  type SSEGroupActivitySnapshot,
  type SSEGroupMessage,
  useSSE,
} from '../../composables/useSSE'
import AppDrawerHeader from '../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import FieldGroup from '../../components/common/FieldGroup.vue'
import StateBadge from '../../components/common/StateBadge.vue'

type ReplyStyle = 'default' | 'gentle' | 'playful' | 'concise' | 'energetic' | 'steady'
type StickerMode = 'inherit' | 'off' | 'rarely' | 'normal' | 'frequently'
type ToolMode = 'inherit' | 'allow' | 'block'
type PresenceMode = 'active' | 'silent_learn' | 'off'
type GroupAccessMode = 'whitelist' | 'blacklist'
type DrawerTab = 'basic' | 'rhythm' | 'advanced'
type ChipTone = 'default' | 'info' | 'warning' | 'success' | 'error'
type StateStatus = 'success' | 'warning' | 'error' | 'info' | 'default'

interface GroupProfileOverride {
  allowed_tools?: string[] | null
  at_only: boolean | null
  blocked_tools?: string[] | null
  talk_value: number | null
  planner_smooth: number | null
  debounce_seconds: number | null
  batch_size: number | null
  history_load_count: number | null
  reply_style: ReplyStyle | null
  custom_prompt: string | null
  tools_enabled: boolean | null
  sticker_mode: StickerMode | null
  slang_enabled: boolean | null
  presence_mode: PresenceMode | null
  blocked_users?: Array<string | number>
}

interface GroupItem {
  group_id: string
  group_name?: string
  member_count?: number | null
  max_member_count?: number | null
  group_remark?: string
  bot_card?: string
  last_message_at?: number | null
  message_count_window?: number
  user_message_count_window?: number
  at_only: boolean
  talk_value: number
  planner_smooth: number
  debounce_seconds: number
  batch_size: number
  history_load_count: number
  privacy_mask?: boolean
  blocked_users: Array<string | number>
  global_blocked_users?: Array<string | number>
  allowed_tools?: string[]
  blocked_tools?: string[]
  global_allowed_tools?: string[]
  global_blocked_tools?: string[]
  reply_style: ReplyStyle
  custom_prompt: string
  tools_enabled: boolean
  sticker_mode: StickerMode
  slang_enabled: boolean
  access_allowed?: boolean
  presence_mode: PresenceMode
  profile_override: GroupProfileOverride
  profile_customized: boolean
}

interface GroupAccessPolicy {
  mode: GroupAccessMode
  whitelist: string[]
  blacklist: string[]
  log_dropped: boolean
}

interface GroupProfileForm {
  blocked_users: string[]
  allowed_tools: string[]
  blocked_tools: string[]
  at_only: boolean
  talk_value: number
  planner_smooth: number
  debounce_seconds: number
  batch_size: number
  history_load_count: number
  reply_style: ReplyStyle
  custom_prompt: string
  tools_enabled: boolean
  sticker_mode: StickerMode
  slang_enabled: boolean
  presence_mode: PresenceMode
}

interface GroupToolCatalogItem {
  name: string
  plugin: string
  category?: string
  description?: string
}

interface GroupProfileAuditEntry {
  id: string
  group_id: string
  group_name?: string
  action: 'save' | 'reset' | string
  saved_at: number
  summary?: {
    changed_fields?: string[]
    changed_count?: number
    profile_customized?: boolean
  }
  changes: Array<{
    field: string
    label: string
    before: any
    after: any
  }>
}

interface GroupState {
  active_users: string[]
  recent_topics: string[]
  message_frequency: number
  recent_mentions: string[]
  error?: string
}

interface GroupMessage {
  user_id: string
  message: string
  timestamp: string
  speaker?: string
  role?: string
}

const REPLY_STYLE_LABELS: Record<ReplyStyle, string> = {
  default: '默认',
  gentle: '柔和',
  playful: '轻快',
  concise: '简洁',
  energetic: '活跃',
  steady: '稳定',
}

const STICKER_MODE_LABELS: Record<StickerMode, string> = {
  inherit: '继承全局',
  off: '关闭',
  rarely: '克制',
  normal: '常规',
  frequently: '高频',
}

const PRESENCE_MODE_LABELS: Record<PresenceMode, string> = {
  active: '主动发言',
  silent_learn: '静默学习',
  off: '完全关闭',
}

const loading = ref(true)
const refreshing = ref(false)
const detailRefreshing = ref(false)
const profileSaving = ref(false)
const profileResetting = ref(false)
const policyLoading = ref(true)
const policyRefreshing = ref(false)
const policySaving = ref(false)
const groups = ref<GroupItem[]>([])
const groupPolicy = ref<GroupAccessPolicy | null>(null)
const groupPolicyOriginal = ref<GroupAccessPolicy | null>(null)
const groupPolicyPath = ref('config/group-policy.json')
const searchText = ref('')
const selectedGroup = ref<GroupItem | null>(null)
const drawerVisible = ref(false)
const policyDrawerVisible = ref(false)
const drawerTab = ref<DrawerTab>('basic')
const groupState = ref<GroupState | null>(null)
const groupMessages = ref<GroupMessage[]>([])
const toolCatalog = ref<GroupToolCatalogItem[]>([])
const profileAuditEntries = ref<GroupProfileAuditEntry[]>([])
const stateLoading = ref(false)
const profileDraft = ref<GroupProfileForm | null>(null)
const profileOriginal = ref<GroupProfileForm | null>(null)

const renameDialogVisible = ref(false)
const renameDraft = ref('')
const renameSubmitting = ref(false)
const cardDialogVisible = ref(false)
const cardDraft = ref('')
const cardSubmitting = ref(false)
const leaveSubmitting = ref(false)

const message = useMessage()

const replyStyleOptions: SelectOption[] = [
  { label: '默认', value: 'default' },
  { label: '柔和', value: 'gentle' },
  { label: '轻快', value: 'playful' },
  { label: '简洁', value: 'concise' },
  { label: '活跃', value: 'energetic' },
  { label: '稳定', value: 'steady' },
]

const stickerModeOptions: SelectOption[] = [
  { label: '继承全局', value: 'inherit' },
  { label: '关闭', value: 'off' },
  { label: '克制', value: 'rarely' },
  { label: '常规', value: 'normal' },
  { label: '高频', value: 'frequently' },
]

const presenceModeOptions: SelectOption[] = [
  { label: '主动发言', value: 'active' },
  { label: '静默学习', value: 'silent_learn' },
  { label: '完全关闭', value: 'off' },
]

const groupAccessModeOptions: SelectOption[] = [
  { label: '白名单模式', value: 'whitelist' },
  { label: '黑名单模式', value: 'blacklist' },
]

const toolModeOptions: SelectOption[] = [
  { label: '继承', value: 'inherit' },
  { label: '允许', value: 'allow' },
  { label: '屏蔽', value: 'block' },
]

const filteredGroups = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  if (!query) return groups.value
  return groups.value.filter((group) => {
    const haystack = `${group.group_id} ${group.group_name || ''}`.toLowerCase()
    return haystack.includes(query)
  })
})

const customProfileCount = computed(() =>
  groups.value.filter(group => group.profile_customized).length,
)

const silentLearnCount = computed(() =>
  groups.value.filter(group => group.presence_mode === 'silent_learn').length,
)

const activePresenceCount = computed(() =>
  groups.value.filter(group => group.presence_mode === 'active').length,
)

const offPresenceCount = computed(() =>
  groups.value.filter(group => group.presence_mode === 'off').length,
)

const policyDirty = computed(() => (
  JSON.stringify(groupPolicy.value || {}) !== JSON.stringify(groupPolicyOriginal.value || {})
))

const policyEffectiveOpenCount = computed(() => {
  const policy = groupPolicy.value
  if (!policy) return 0
  if (policy.mode === 'whitelist') {
    return groups.value.filter(group => policy.whitelist.includes(group.group_id)).length
  }
  return groups.value.filter(group => !policy.blacklist.includes(group.group_id)).length
})

const policyEffectiveClosedCount = computed(() =>
  Math.max(0, groups.value.length - policyEffectiveOpenCount.value),
)

const policyModeLabel = computed(() => (
  groupPolicy.value?.mode === 'whitelist'
    ? '白名单模式：白名单群可发言/工具，其余默认关闭'
    : '黑名单模式：黑名单群禁发言/工具，其余可发言/工具'
))

const policyModeShortLabel = computed(() => (
  groupPolicy.value?.mode === 'whitelist' ? '白名单' : '黑名单'
))

function openPolicyDrawer() {
  policyDrawerVisible.value = true
}

const profileDirty = computed(() => (
  JSON.stringify(profileDraft.value || {}) !== JSON.stringify(profileOriginal.value || {})
))

const groupedToolCatalog = computed(() => {
  const groupsMap = new Map<string, GroupToolCatalogItem[]>()
  for (const tool of toolCatalog.value) {
    const key = tool.plugin || 'runtime'
    if (!groupsMap.has(key)) groupsMap.set(key, [])
    groupsMap.get(key)?.push(tool)
  }
  return [...groupsMap.entries()]
    .map(([plugin, tools]) => ({
      plugin,
      tools: [...tools].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN')),
    }))
    .sort((left, right) => left.plugin.localeCompare(right.plugin, 'zh-CN'))
})

const columns: DataTableColumns<GroupItem> = [
  {
    title: '群',
    key: 'group',
    minWidth: 240,
    render: (row) => {
      const primary = row.group_remark || row.group_name || `群 ${row.group_id}`
      const secondary = row.group_remark && row.group_name && row.group_remark !== row.group_name
        ? row.group_name
        : ''
      return h('div', {
        class: ['group-cell', `group-cell--${presenceStatus(row)}`],
      }, [
        h('strong', { class: 'group-cell__title' }, primary),
        secondary
          ? h('span', { class: 'group-cell__subtitle' }, secondary)
          : null,
        h('span', { class: 'group-cell__meta' }, [
          h('span', { class: 'group-cell__meta-prefix' }, '#'),
          String(row.group_id),
        ]),
      ])
    },
  },
  {
    title: '参与模式',
    key: 'presence_mode',
    width: 140,
    render: row => h(StateBadge, {
      label: presenceLabel(row),
      status: presenceStatus(row),
    }),
  },
  {
    title: '差异',
    key: 'diff',
    minWidth: 280,
    render: (row) => {
      const chips = groupDiffChips(row)
      if (chips.length === 0) {
        return h('span', { class: 'group-cell__hint' }, '继承全局默认')
      }
      const visible = chips.slice(0, 4)
      const overflow = chips.length - visible.length
      const nodes = visible.map(chip =>
        h(NTag, {
          size: 'small',
          type: chip.type,
          round: true,
        }, () => chip.label),
      )
      if (overflow > 0) {
        nodes.push(h(NTag, {
          size: 'small',
          type: 'default',
          round: true,
        }, () => `+${overflow}`))
      }
      return h('div', { class: 'group-feature-list' }, nodes)
    },
  },
  {
    title: '成员',
    key: 'member_count',
    width: 110,
    align: 'right',
    render: row => h('div', { class: 'group-cell__member' }, [
      h('span', { class: 'group-cell__member-count' }, memberCountText(row)),
    ]),
  },
  {
    title: '最近活跃',
    key: 'last_active',
    width: 150,
    render: (row) => {
      const info = lastActiveText(row)
      const messageCount = row.message_count_window || 0
      return h('div', { class: 'group-cell__activity' }, [
        h(NTag, {
          size: 'small',
          round: true,
          type: info.tone === 'success' ? 'success' : info.tone === 'info' ? 'info' : 'default',
        }, () => info.label),
        messageCount > 0
          ? h('span', { class: 'group-cell__activity-meta' }, `24h · ${messageCount} 条`)
          : null,
      ])
    },
  },
  {
    title: '发言值',
    key: 'talk_value',
    width: 90,
    align: 'right',
    render: row => h(NText, { depth: 3 }, () => Number(row.talk_value || 0).toFixed(2)),
  },
]

onMounted(() => {
  void loadInitialData()
})

// Keep the shared EventSource alive while this view is mounted so we
// receive group_message / group_activity pushes without manual polling.
useSSE()

function applyGroupMessageEvent(event: SSEGroupMessage) {
  const targetId = event.group_id
  let touched = false
  groups.value = groups.value.map((group) => {
    if (group.group_id !== targetId) return group
    touched = true
    const next: GroupItem = {
      ...group,
      last_message_at: event.ts,
      message_count_window: (group.message_count_window || 0) + 1,
      user_message_count_window:
        (group.user_message_count_window || 0) + (event.is_bot ? 0 : 1),
    }
    if (selectedGroup.value?.group_id === targetId) {
      selectedGroup.value = next
    }
    return next
  })
  if (!touched) {
    // Group not yet in the list (e.g. first inbound message after a
    // restart) — pull a fresh snapshot so the row appears.
    void loadGroups(true)
  }
}

function applyGroupActivitySnapshot(snapshot: SSEGroupActivitySnapshot) {
  const summary = snapshot.groups || {}
  groups.value = groups.value.map((group) => {
    const stats = summary[group.group_id]
    if (!stats) return group
    const next: GroupItem = {
      ...group,
      last_message_at: stats.last_at ? Number(stats.last_at) : group.last_message_at ?? null,
      message_count_window: Number(stats.count_window ?? group.message_count_window ?? 0),
      user_message_count_window: Number(
        stats.user_count_window ?? group.user_message_count_window ?? 0,
      ),
    }
    if (selectedGroup.value?.group_id === group.group_id) {
      selectedGroup.value = next
    }
    return next
  })
}

const unsubscribeGroupMessage = onGroupMessage(applyGroupMessageEvent)
const unsubscribeGroupActivity = onGroupActivity(applyGroupActivitySnapshot)

onUnmounted(() => {
  unsubscribeGroupMessage()
  unsubscribeGroupActivity()
})

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function uniqueStrings(values: Array<string | number | null | undefined>) {
  return [...new Set(values
    .map(value => String(value ?? '').trim())
    .filter(Boolean))]
}

function blockedUserDraftFromGroup(group: GroupItem) {
  return uniqueStrings(group.profile_override?.blocked_users || [])
}

function normalizeGroupIds(values: Array<string | number | null | undefined>) {
  return uniqueStrings(values)
}

function presenceStatus(group: GroupItem): StateStatus {
  if (group.presence_mode === 'active') return 'success'
  if (group.presence_mode === 'silent_learn') return 'info'
  if (group.presence_mode === 'off') return 'warning'
  return 'default'
}

function presenceLabel(group: GroupItem): string {
  return PRESENCE_MODE_LABELS[group.presence_mode]
}

function memberCountText(group: GroupItem): string {
  if (group.member_count === null || group.member_count === undefined) return '—'
  if (group.max_member_count && group.max_member_count > 0) {
    return `${group.member_count} / ${group.max_member_count}`
  }
  return String(group.member_count)
}

function lastActiveText(group: GroupItem): { label: string, tone: 'success' | 'info' | 'default' } {
  const ts = group.last_message_at
  if (!ts || ts <= 0) return { label: '未见消息', tone: 'default' }
  const now = Date.now() / 1000
  const diff = Math.max(0, now - ts)
  if (diff < 60) return { label: '刚刚', tone: 'success' }
  if (diff < 3600) return { label: `${Math.round(diff / 60)} 分钟前`, tone: 'success' }
  if (diff < 24 * 3600) return { label: `${Math.round(diff / 3600)} 小时前`, tone: 'info' }
  if (diff < 7 * 24 * 3600) return { label: `${Math.round(diff / 86400)} 天前`, tone: 'info' }
  const date = new Date(ts * 1000)
  return {
    label: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`,
    tone: 'default',
  }
}

function groupDiffChips(group: GroupItem): Array<{ label: string, type: ChipTone }> {
  const chips: Array<{ label: string, type: ChipTone }> = []
  if (group.at_only) {
    chips.push({ label: '@ 才回复', type: 'warning' })
  }
  if (group.reply_style && group.reply_style !== 'default') {
    chips.push({ label: `风格 ${REPLY_STYLE_LABELS[group.reply_style]}`, type: 'info' })
  }
  if (group.sticker_mode && group.sticker_mode !== 'inherit') {
    chips.push({
      label: `贴纸 ${STICKER_MODE_LABELS[group.sticker_mode]}`,
      type: group.sticker_mode === 'off' ? 'warning' : 'info',
    })
  }
  if (!group.tools_enabled) {
    chips.push({ label: '工具关闭', type: 'warning' })
  }
  if (!group.slang_enabled) {
    chips.push({ label: '黑话关闭', type: 'warning' })
  }
  const allowed = group.allowed_tools?.length || 0
  const blocked = group.blocked_tools?.length || 0
  if (allowed > 0 || blocked > 0) {
    const parts: string[] = []
    if (allowed > 0) parts.push(`允许 ${allowed}`)
    if (blocked > 0) parts.push(`屏蔽 ${blocked}`)
    chips.push({ label: `工具 ${parts.join(' · ')}`, type: 'info' })
  }
  if ((group.blocked_users?.length || 0) > 0) {
    chips.push({ label: `屏蔽用户 ${group.blocked_users.length}`, type: 'error' })
  }
  if (group.custom_prompt && group.custom_prompt.trim().length > 0) {
    chips.push({ label: '附加提示词', type: 'info' })
  }
  return chips
}

function profileFromGroup(group: GroupItem): GroupProfileForm {
  return {
    blocked_users: blockedUserDraftFromGroup(group),
    allowed_tools: [...(group.allowed_tools || [])],
    blocked_tools: [...(group.blocked_tools || [])],
    at_only: Boolean(group.at_only),
    talk_value: Number(group.talk_value || 0),
    planner_smooth: Number(group.planner_smooth || 0),
    debounce_seconds: Number(group.debounce_seconds || 0),
    batch_size: Number(group.batch_size || 1),
    history_load_count: Number(group.history_load_count || 1),
    reply_style: group.reply_style,
    custom_prompt: group.custom_prompt || '',
    tools_enabled: Boolean(group.tools_enabled),
    sticker_mode: group.sticker_mode,
    slang_enabled: Boolean(group.slang_enabled),
    presence_mode: group.presence_mode,
  }
}

function policyFromResponse(policy: GroupAccessPolicy): GroupAccessPolicy {
  return {
    mode: policy.mode === 'blacklist' ? 'blacklist' : 'whitelist',
    whitelist: normalizeGroupIds(policy.whitelist || []),
    blacklist: normalizeGroupIds(policy.blacklist || []),
    log_dropped: Boolean(policy.log_dropped),
  }
}

function hydratePolicyDraft(policy: GroupAccessPolicy) {
  const nextPolicy = policyFromResponse(policy)
  groupPolicy.value = nextPolicy
  groupPolicyOriginal.value = deepClone(nextPolicy)
}

function hydrateProfileDraft(group: GroupItem) {
  const nextDraft = profileFromGroup(group)
  profileDraft.value = nextDraft
  profileOriginal.value = deepClone(nextDraft)
}

function mergeGroupItem(prev: GroupItem | undefined, next: GroupItem): GroupItem {
  if (!prev) return next
  // Drop keys whose value is undefined so we don't blow away enrichment fields
  // (member_count / last_message_at / activity counters) when a partial response
  // returns only profile fields.
  const cleanedNext: Partial<GroupItem> = {}
  for (const [key, value] of Object.entries(next)) {
    if (value !== undefined) {
      ;(cleanedNext as Record<string, unknown>)[key] = value
    }
  }
  return {
    ...prev,
    ...cleanedNext,
    group_name: cleanedNext.group_name ?? prev.group_name,
  } as GroupItem
}

function syncSelectedFromList(nextGroup: GroupItem, preserveDraft = false) {
  let merged: GroupItem = nextGroup
  groups.value = groups.value.map((group) => {
    if (group.group_id !== nextGroup.group_id) return group
    merged = mergeGroupItem(group, nextGroup)
    return merged
  })
  selectedGroup.value = merged
  if (!preserveDraft) {
    hydrateProfileDraft(selectedGroup.value)
  }
}

async function loadGroups(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api('/api/admin/groups')
    groups.value = data.groups || []
    if (selectedGroup.value) {
      const latest = groups.value.find(group => group.group_id === selectedGroup.value?.group_id)
      if (latest) {
        selectedGroup.value = latest
        if (!profileDirty.value) {
          hydrateProfileDraft(latest)
        }
      }
    }
  } catch {
    message.error('群列表加载失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function loadGroupPolicy(silent = false) {
  if (silent) policyRefreshing.value = true
  else policyLoading.value = true

  try {
    const data = await api<{ ok?: boolean, path?: string, policy?: GroupAccessPolicy, error?: string }>('/api/admin/groups/policy')
    if (!data?.policy) {
      throw new Error(data?.error || 'invalid-group-policy-payload')
    }
    groupPolicyPath.value = data.path || 'config/group-policy.json'
    hydratePolicyDraft(data.policy)
  } catch {
    message.error('群门禁加载失败')
  } finally {
    policyLoading.value = false
    policyRefreshing.value = false
  }
}

async function loadInitialData() {
  await Promise.all([
    loadGroups(),
    loadGroupPolicy(),
  ])
}

async function refreshAllData() {
  await Promise.all([
    loadGroups(true),
    loadGroupPolicy(true),
  ])
}

async function openDrawer(group: GroupItem) {
  selectedGroup.value = group
  hydrateProfileDraft(group)
  drawerTab.value = 'basic'
  drawerVisible.value = true
  await refreshGroupDetail()
}

function rowProps(row: GroupItem) {
  return {
    style: 'cursor: pointer;',
    onClick: () => {
      void openDrawer(row)
    },
  }
}

async function refreshGroupDetail() {
  if (!selectedGroup.value) return
  stateLoading.value = !detailRefreshing.value
  detailRefreshing.value = true
  const group = selectedGroup.value

  try {
    const [profileRes, stateRes, msgRes] = await Promise.allSettled([
      api(`/api/admin/groups/${group.group_id}/profile`),
      api(`/api/admin/groups/${group.group_id}/state`),
      api(`/api/admin/groups/${group.group_id}/messages?limit=20`),
    ])
    if (profileRes.status === 'fulfilled' && profileRes.value?.group) {
      toolCatalog.value = profileRes.value.tool_catalog || []
      profileAuditEntries.value = profileRes.value.audit?.entries || []
      syncSelectedFromList(profileRes.value.group as GroupItem, profileDirty.value)
    }
    groupState.value = stateRes.status === 'fulfilled' ? stateRes.value : null
    groupMessages.value = msgRes.status === 'fulfilled' ? (msgRes.value.messages || []) : []
  } finally {
    stateLoading.value = false
    detailRefreshing.value = false
  }
}

function toolMode(toolName: string): ToolMode {
  if (!profileDraft.value) return 'inherit'
  if (profileDraft.value.blocked_tools.includes(toolName)) return 'block'
  if (profileDraft.value.allowed_tools.includes(toolName)) return 'allow'
  return 'inherit'
}

function setToolMode(toolName: string, mode: ToolMode) {
  if (!profileDraft.value) return
  const allowed = new Set(profileDraft.value.allowed_tools)
  const blocked = new Set(profileDraft.value.blocked_tools)
  allowed.delete(toolName)
  blocked.delete(toolName)
  if (mode === 'allow') allowed.add(toolName)
  if (mode === 'block') blocked.add(toolName)
  profileDraft.value = {
    ...profileDraft.value,
    allowed_tools: [...allowed].sort((left, right) => left.localeCompare(right, 'zh-CN')),
    blocked_tools: [...blocked].sort((left, right) => left.localeCompare(right, 'zh-CN')),
  }
}

function buildProfilePayload() {
  if (!profileDraft.value) return null
  const rawBlockedUsers = uniqueStrings(profileDraft.value.blocked_users)
  const invalidUsers = rawBlockedUsers.filter(value => !/^\d+$/.test(value))
  if (invalidUsers.length) {
    throw new Error(`屏蔽用户只支持纯数字 QQ 号：${invalidUsers.join('、')}`)
  }
  return {
    ...profileDraft.value,
    blocked_users: rawBlockedUsers.map(value => Number(value)),
    allowed_tools: uniqueStrings(profileDraft.value.allowed_tools),
    blocked_tools: uniqueStrings(profileDraft.value.blocked_tools),
  }
}

async function saveProfile() {
  if (!selectedGroup.value || !profileDraft.value || !profileDirty.value) return
  profileSaving.value = true
  try {
    const payload = buildProfilePayload()
    const data = await api(`/api/admin/groups/${selectedGroup.value.group_id}/profile`, {
      method: 'POST',
      body: payload,
    })
    if (!data.ok || !data.group) {
      message.error(data.error || '保存群策略失败')
      return
    }
    syncSelectedFromList(data.group as GroupItem)
    if (data.audit_entry) {
      profileAuditEntries.value = [data.audit_entry as GroupProfileAuditEntry, ...profileAuditEntries.value].slice(0, 10)
    }
    message.success(data.message || '群策略已保存')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存群策略失败')
  } finally {
    profileSaving.value = false
  }
}

async function resetProfileOverride() {
  if (!selectedGroup.value) return
  profileResetting.value = true
  try {
    const data = await api(`/api/admin/groups/${selectedGroup.value.group_id}/profile`, {
      method: 'DELETE',
    })
    if (!data.ok || !data.group) {
      message.error(data.error || '恢复全局默认失败')
      return
    }
    syncSelectedFromList(data.group as GroupItem)
    if (data.audit_entry) {
      profileAuditEntries.value = [data.audit_entry as GroupProfileAuditEntry, ...profileAuditEntries.value].slice(0, 10)
    }
    message.success(data.message || '已恢复全局默认')
  } catch {
    message.error('恢复全局默认失败')
  } finally {
    profileResetting.value = false
  }
}

function resetDraft() {
  if (!selectedGroup.value || !profileOriginal.value) return
  hydrateProfileDraft(selectedGroup.value)
}

function openRenameDialog() {
  if (!selectedGroup.value) return
  renameDraft.value = selectedGroup.value.group_remark || ''
  renameDialogVisible.value = true
}

async function submitRename() {
  if (!selectedGroup.value) return
  const next = renameDraft.value.trim()
  if (next === (selectedGroup.value.group_remark || '')) {
    renameDialogVisible.value = false
    return
  }
  renameSubmitting.value = true
  try {
    const data = await api(`/api/admin/groups/${selectedGroup.value.group_id}/group-remark`, {
      method: 'POST',
      body: { group_remark: next },
    })
    if (!data.ok) {
      message.error(data.error || '设置 Bot 端备注失败')
      return
    }
    if (data.group) {
      syncSelectedFromList(data.group as GroupItem, profileDirty.value)
    }
    renameDialogVisible.value = false
    if (data.warning) {
      message.warning(data.warning, { duration: 6000 })
    } else {
      message.success(data.message || 'Bot 端备注已更新')
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '设置 Bot 端备注失败')
  } finally {
    renameSubmitting.value = false
  }
}

function openBotCardDialog() {
  if (!selectedGroup.value) return
  cardDraft.value = selectedGroup.value.bot_card || ''
  cardDialogVisible.value = true
}

async function submitBotCard() {
  if (!selectedGroup.value) return
  cardSubmitting.value = true
  try {
    const data = await api(`/api/admin/groups/${selectedGroup.value.group_id}/bot-card`, {
      method: 'POST',
      body: { card: cardDraft.value.trim() },
    })
    if (!data.ok) {
      message.error(data.error || '设置群名片失败')
      return
    }
    if (data.group) {
      syncSelectedFromList(data.group as GroupItem, profileDirty.value)
    }
    cardDialogVisible.value = false
    if (data.warning) {
      message.warning(data.warning, { duration: 6000 })
    } else {
      message.success(data.message || '群名片已更新')
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '设置群名片失败')
  } finally {
    cardSubmitting.value = false
  }
}

async function leaveGroup(dismiss: boolean) {
  if (!selectedGroup.value) return
  leaveSubmitting.value = true
  const targetId = selectedGroup.value.group_id
  try {
    const data = await api(`/api/admin/groups/${targetId}/leave`, {
      method: 'POST',
      body: { confirm: true, dismiss },
    })
    if (!data.ok) {
      message.error(data.error || '退群失败')
      return
    }
    message.success(data.message || (dismiss ? '已解散该群' : '已退出该群'))
    drawerVisible.value = false
    selectedGroup.value = null
    await loadGroups(true)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '退群失败')
  } finally {
    leaveSubmitting.value = false
  }
}

function timelineType(msg: GroupMessage) {
  if (msg.role === 'assistant') return 'assistant'
  return 'user'
}

function speakerLabel(msg: GroupMessage) {
  if (msg.role === 'assistant') return msg.speaker || 'Bot'
  return msg.speaker || msg.user_id || '成员'
}

function formatAuditTime(value?: number) {
  if (!value) return '--'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

function auditActionLabel(action?: string) {
  if (action === 'reset') return '恢复全局默认'
  return '保存群策略'
}

function policyGroupIds(values: Array<string | number | null | undefined>) {
  return normalizeGroupIds(values)
}

function buildGroupPolicyPayload() {
  if (!groupPolicy.value) return null
  const whitelist = normalizeGroupIds(groupPolicy.value.whitelist)
  const blacklist = normalizeGroupIds(groupPolicy.value.blacklist)
  const invalid = [...whitelist, ...blacklist].filter(value => !/^\d+$/.test(value))
  if (invalid.length > 0) {
    throw new Error(`群号只支持纯数字：${invalid.join('、')}`)
  }
  return {
    mode: groupPolicy.value.mode,
    whitelist: whitelist.map(value => Number(value)),
    blacklist: blacklist.map(value => Number(value)),
    log_dropped: Boolean(groupPolicy.value.log_dropped),
  }
}

async function saveGroupPolicy() {
  if (!groupPolicy.value || !policyDirty.value) return
  policySaving.value = true
  try {
    const payload = buildGroupPolicyPayload()
    if (!payload) return
    const data = await api('/api/admin/groups/policy', {
      method: 'POST',
      body: payload,
    })
    if (!data.ok || !data.policy) {
      message.error(data.error || '保存群门禁失败')
      return
    }
    groupPolicyPath.value = data.path || groupPolicyPath.value || 'config/group-policy.json'
    hydratePolicyDraft(data.policy as GroupAccessPolicy)
    if (Array.isArray(data.groups)) {
      groups.value = data.groups as GroupItem[]
      if (selectedGroup.value) {
        const latest = groups.value.find(group => group.group_id === selectedGroup.value?.group_id)
        if (latest) {
          selectedGroup.value = latest
          if (!profileDirty.value) {
            hydrateProfileDraft(latest)
          }
        }
      }
    }
    message.success(data.message || '群门禁已保存')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存群门禁失败')
  } finally {
    policySaving.value = false
  }
}

function resetGroupPolicyDraft() {
  if (!groupPolicyOriginal.value) return
  hydratePolicyDraft(groupPolicyOriginal.value)
}
</script>

<template>
  <AppPage
    title="群管理"
    eyebrow="Group Runtime"
    description="查看群聊运行差异，并按群覆盖回复风格、节奏与功能开关。点击列表行打开详细配置抽屉。"
  >
    <template #action>
      <NSpace align="center" :size="8">
        <NButton secondary size="small" @click="openPolicyDrawer">
          <template #icon>
            <NIcon :component="ShieldCheckmarkOutline" />
          </template>
          门禁
        </NButton>
        <NButton secondary size="small" :loading="refreshing || policyRefreshing" @click="refreshAllData">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
      </NSpace>
    </template>

    <div class="groups-summary">
      <div class="groups-summary__group">
        <span class="groups-summary__chip groups-summary__chip--total">
          <NIcon :component="PeopleOutline" :size="14" />
          <span class="groups-summary__chip-label">群</span>
          <strong>{{ groups.length }}</strong>
        </span>
        <span class="groups-summary__chip groups-summary__chip--soft">
          <span class="groups-summary__chip-label">自定义</span>
          <strong>{{ customProfileCount }}</strong>
        </span>
      </div>

      <div class="groups-summary__group groups-summary__group--presence">
        <span class="groups-summary__chip groups-summary__chip--success">
          <span class="groups-summary__dot" />
          <span class="groups-summary__chip-label">主动</span>
          <strong>{{ activePresenceCount }}</strong>
        </span>
        <span class="groups-summary__chip groups-summary__chip--info">
          <span class="groups-summary__dot" />
          <span class="groups-summary__chip-label">静默学习</span>
          <strong>{{ silentLearnCount }}</strong>
        </span>
        <span class="groups-summary__chip groups-summary__chip--warning">
          <span class="groups-summary__dot" />
          <span class="groups-summary__chip-label">完全关闭</span>
          <strong>{{ offPresenceCount }}</strong>
        </span>
      </div>

      <button type="button" class="groups-summary__policy" @click="openPolicyDrawer">
        <NIcon :component="ShieldCheckmarkOutline" :size="14" />
        <span class="groups-summary__policy-label">门禁</span>
        <strong>{{ policyModeShortLabel }}</strong>
        <span class="groups-summary__policy-counts">
          开 {{ policyEffectiveOpenCount }} · 关 {{ policyEffectiveClosedCount }}
        </span>
        <NIcon :component="ChevronForwardOutline" :size="12" class="groups-summary__policy-chevron" />
      </button>
    </div>

    <div class="groups-toolbar">
      <NInput
        v-model:value="searchText"
        clearable
        placeholder="搜索群号或群名"
        size="small"
        class="groups-toolbar__search"
      />
      <span class="groups-toolbar__count">
        {{ filteredGroups.length }} / {{ groups.length }} 个群
      </span>
    </div>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <NDataTable
        v-if="filteredGroups.length > 0"
        :columns="columns"
        :data="filteredGroups"
        :row-key="(row: GroupItem) => row.group_id"
        :row-props="rowProps"
        :bordered="false"
        size="small"
        class="groups-table"
      />

      <EmptyState
        v-else
        title="没有匹配的群"
        description="尝试清空搜索条件，或先在门禁里加入新群。"
        :icon="PeopleOutline"
      />
    </template>

    <NDrawer v-model:show="drawerVisible" :width="640">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Group Profile"
            :title="selectedGroup?.group_name || selectedGroup?.group_id || '群详情'"
            :description="selectedGroup ? `群号 ${selectedGroup.group_id}` : ''"
          >
            <template #aside>
              <NSpace :size="6" align="center">
                <StateBadge
                  v-if="selectedGroup"
                  :label="selectedGroup.profile_customized ? '已覆盖全局' : '继承全局'"
                  :status="selectedGroup.profile_customized ? 'info' : 'default'"
                  compact
                />
                <StateBadge
                  v-if="profileDirty"
                  label="未保存"
                  status="warning"
                  compact
                />
                <NButton secondary size="small" :loading="detailRefreshing" @click="refreshGroupDetail">
                  刷新
                </NButton>
              </NSpace>
            </template>
          </AppDrawerHeader>
        </template>

        <template v-if="selectedGroup && profileDraft">
          <AppDrawerLayout class="group-detail">
            <template #toolbar>
              <div class="group-detail__meta">
                <div class="group-detail__meta-stats">
                  <span class="group-detail__meta-item">
                    <span class="group-detail__meta-label">成员</span>
                    <span class="group-detail__meta-value">{{ memberCountText(selectedGroup) }}</span>
                  </span>
                  <span class="group-detail__meta-item">
                    <span class="group-detail__meta-label">最近活跃</span>
                    <span class="group-detail__meta-value">{{ lastActiveText(selectedGroup).label }}</span>
                  </span>
                  <span class="group-detail__meta-item">
                    <span class="group-detail__meta-label">24h 消息</span>
                    <span class="group-detail__meta-value">
                      {{ selectedGroup.message_count_window || 0 }}
                      <span class="group-detail__meta-sub">/ 用户 {{ selectedGroup.user_message_count_window || 0 }}</span>
                    </span>
                  </span>
                  <span v-if="selectedGroup.group_remark" class="group-detail__meta-item">
                    <span class="group-detail__meta-label">Bot 端备注</span>
                    <span class="group-detail__meta-value">{{ selectedGroup.group_remark }}</span>
                  </span>
                  <span v-if="selectedGroup.bot_card" class="group-detail__meta-item">
                    <span class="group-detail__meta-label">Bot 群名片</span>
                    <span class="group-detail__meta-value">{{ selectedGroup.bot_card }}</span>
                  </span>
                </div>
                <div class="group-detail__meta-actions">
                  <NButton size="small" secondary @click="openRenameDialog">改 Bot 备注</NButton>
                  <NButton size="small" secondary @click="openBotCardDialog">改 Bot 群名片</NButton>
                  <NPopconfirm
                    placement="bottom-end"
                    :positive-button-props="{ type: 'warning' }"
                    @positive-click="leaveGroup(false)"
                  >
                    <template #trigger>
                      <NButton size="small" type="warning" ghost :loading="leaveSubmitting">退群</NButton>
                    </template>
                    确认 Bot 退出群 {{ selectedGroup.group_id }}？这是不可逆的操作。
                  </NPopconfirm>
                </div>
              </div>
            </template>

            <NTabs
              v-model:value="drawerTab"
              type="segment"
              size="small"
              animated
              class="group-detail__tabs"
            >
              <NTabPane name="basic" tab="基础">
                <div class="group-profile__fields">
                  <FieldGroup label="参与模式" helper="主动可回复并使用工具，静默只学习不发言，关闭则完全跳过。">
                    <NRadioGroup v-model:value="profileDraft.presence_mode">
                      <NRadioButton
                        v-for="opt in presenceModeOptions"
                        :key="String(opt.value)"
                        :value="opt.value"
                      >
                        {{ opt.label }}
                      </NRadioButton>
                    </NRadioGroup>
                  </FieldGroup>

                  <div class="settings-row">
                    <div class="settings-row__copy">
                      <span class="settings-row__label">@ 才回复</span>
                      <span class="settings-row__helper">开启后只有 @bot 才会触发回复。</span>
                    </div>
                    <NSwitch v-model:value="profileDraft.at_only" />
                  </div>

                  <FieldGroup label="回复风格" helper="影响 Prompt 的语气段。默认风格继承全局人设。">
                    <NRadioGroup v-model:value="profileDraft.reply_style">
                      <NRadioButton
                        v-for="opt in replyStyleOptions"
                        :key="String(opt.value)"
                        :value="opt.value"
                      >
                        {{ opt.label }}
                      </NRadioButton>
                    </NRadioGroup>
                  </FieldGroup>

                  <FieldGroup label="贴纸策略" helper="继承全局即跟随主配置；其余按本群单独控制贴纸频率。">
                    <NRadioGroup v-model:value="profileDraft.sticker_mode">
                      <NRadioButton
                        v-for="opt in stickerModeOptions"
                        :key="String(opt.value)"
                        :value="opt.value"
                      >
                        {{ opt.label }}
                      </NRadioButton>
                    </NRadioGroup>
                  </FieldGroup>

                  <div class="settings-row">
                    <div class="settings-row__copy">
                      <span class="settings-row__label">工具调用</span>
                      <span class="settings-row__helper">关闭后本群完全不进入 ToolRegistry。</span>
                    </div>
                    <NSwitch v-model:value="profileDraft.tools_enabled" />
                  </div>

                  <div class="settings-row">
                    <div class="settings-row__copy">
                      <span class="settings-row__label">黑话系统</span>
                      <span class="settings-row__helper">关闭后不学习也不注入本群约定语。</span>
                    </div>
                    <NSwitch v-model:value="profileDraft.slang_enabled" />
                  </div>

                  <FieldGroup
                    label="群附加提示词"
                    helper="拼接在主 Prompt 之后，用于本群专属规则；空则继承全局。"
                  >
                    <NInput
                      v-model:value="profileDraft.custom_prompt"
                      type="textarea"
                      :autosize="{ minRows: 3, maxRows: 8 }"
                      placeholder="例如：少说教，多接梗；讨论问题时先给结论。"
                    />
                  </FieldGroup>

                  <FieldGroup
                    label="群内额外屏蔽用户"
                    helper="只对本群生效；全局屏蔽名单仍照常作用。仅接受纯数字 QQ 号。"
                  >
                    <div class="group-profile__stack">
                      <NDynamicTags
                        :value="profileDraft.blocked_users"
                        @update:value="profileDraft.blocked_users = uniqueStrings($event)"
                      />
                      <div
                        v-if="(selectedGroup.global_blocked_users || []).length > 0"
                        class="group-detail__chips group-detail__chips--compact"
                      >
                        <NTag
                          v-for="user in selectedGroup.global_blocked_users"
                          :key="`global-${user}`"
                          size="small"
                          round
                          type="default"
                        >
                          全局 {{ user }}
                        </NTag>
                      </div>
                    </div>
                  </FieldGroup>
                </div>
              </NTabPane>

              <NTabPane name="rhythm" tab="节奏">
                <NForm label-placement="top" class="group-profile__form">
                  <NAlert type="info" :show-icon="false" class="group-profile__tip">
                    节奏参数控制本群的发言时机和上下文窗口。除非有明确观测到的问题，建议保持默认。
                  </NAlert>

                  <NGrid :cols="24" :x-gap="14" :y-gap="10" responsive="screen">
                    <NFormItemGi :span="8" label="发言值">
                      <NInputNumber
                        v-model:value="profileDraft.talk_value"
                        :min="0"
                        :max="1"
                        :step="0.05"
                        style="width: 100%"
                      />
                    </NFormItemGi>

                    <NFormItemGi :span="8" label="规划间隔 (s)">
                      <NInputNumber
                        v-model:value="profileDraft.planner_smooth"
                        :min="0"
                        :max="120"
                        :step="0.5"
                        style="width: 100%"
                      />
                    </NFormItemGi>

                    <NFormItemGi :span="8" label="回复冷却 (s)">
                      <NInputNumber
                        v-model:value="profileDraft.debounce_seconds"
                        :min="0"
                        :max="300"
                        :step="0.5"
                        style="width: 100%"
                      />
                    </NFormItemGi>

                    <NFormItemGi :span="12" label="批量窗口">
                      <NInputNumber
                        v-model:value="profileDraft.batch_size"
                        :min="1"
                        :max="100"
                        :step="1"
                        style="width: 100%"
                      />
                    </NFormItemGi>

                    <NFormItemGi :span="12" label="历史载入">
                      <NInputNumber
                        v-model:value="profileDraft.history_load_count"
                        :min="1"
                        :max="200"
                        :step="1"
                        style="width: 100%"
                      />
                    </NFormItemGi>
                  </NGrid>
                </NForm>
              </NTabPane>

              <NTabPane name="advanced" tab="高级">
                <AppPanelSection eyebrow="Tool Matrix" title="群级工具矩阵">
                  <template #aside>
                    <StateBadge
                      :label="profileDraft.tools_enabled ? `${toolCatalog.length} 个工具` : '总开关已关'"
                      :status="profileDraft.tools_enabled ? 'success' : 'warning'"
                      compact
                    />
                  </template>

                  <NAlert type="info" :show-icon="false" class="group-profile__tip">
                    “允许”非空时只保留允许名单；“屏蔽”永远优先。日常默认全部继承即可。
                  </NAlert>

                  <div v-if="toolCatalog.length > 0" class="group-tool-matrix">
                    <div class="group-tool-matrix__stats">
                      <div class="group-detail__stat">
                        <span>允许</span>
                        <strong>{{ profileDraft.allowed_tools.length }}</strong>
                      </div>
                      <div class="group-detail__stat">
                        <span>屏蔽</span>
                        <strong>{{ profileDraft.blocked_tools.length }}</strong>
                      </div>
                      <div class="group-detail__stat">
                        <span>继承</span>
                        <strong>{{ Math.max(0, toolCatalog.length - profileDraft.allowed_tools.length - profileDraft.blocked_tools.length) }}</strong>
                      </div>
                    </div>

                    <div
                      v-for="toolGroup in groupedToolCatalog"
                      :key="toolGroup.plugin"
                      class="group-tool-cluster"
                    >
                      <div class="group-tool-cluster__head">
                        <strong>{{ toolGroup.plugin }}</strong>
                        <span>{{ toolGroup.tools.length }} 个工具</span>
                      </div>

                      <div class="group-tool-cluster__body">
                        <div
                          v-for="tool in toolGroup.tools"
                          :key="tool.name"
                          class="group-tool-row"
                        >
                          <div class="group-tool-row__copy">
                            <strong>{{ tool.name }}</strong>
                            <span>{{ tool.description || '这个工具没有额外描述。' }}</span>
                          </div>

                          <NSelect
                            :value="toolMode(tool.name)"
                            :options="toolModeOptions"
                            size="small"
                            class="group-tool-row__select"
                            @update:value="(value: ToolMode) => setToolMode(tool.name, value)"
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <EmptyState
                    v-else
                    compact
                    title="运行时还没有可治理工具"
                    description="ToolRegistry 当前为空，等插件完成注册后会出现。"
                    :icon="ChatbubbleEllipsesOutline"
                  />
                </AppPanelSection>

                <AppPanelSection eyebrow="Live State" title="实时状态">
                  <NSkeleton v-if="stateLoading" :repeat="4" text />

                  <template v-else-if="groupState?.error">
                    <EmptyState
                      compact
                      title="状态板不可用"
                      :description="groupState.error"
                      :icon="ShieldCheckmarkOutline"
                    />
                  </template>

                  <template v-else-if="groupState">
                    <div class="group-detail__stats group-detail__stats--compact">
                      <div class="group-detail__stat">
                        <span>消息频率</span>
                        <strong>{{ groupState.message_frequency?.toFixed(1) ?? '--' }} /分钟</strong>
                      </div>
                      <div class="group-detail__stat">
                        <span>活跃成员</span>
                        <strong>{{ groupState.active_users?.length || 0 }}</strong>
                      </div>
                      <div class="group-detail__stat">
                        <span>近期话题</span>
                        <strong>{{ groupState.recent_topics?.length || 0 }}</strong>
                      </div>
                      <div class="group-detail__stat">
                        <span>近期提及</span>
                        <strong>{{ groupState.recent_mentions?.length || 0 }}</strong>
                      </div>
                    </div>

                    <div class="group-detail__topic-block">
                      <h4>活跃成员</h4>
                      <div class="group-detail__chips">
                        <NTag v-for="user in groupState.active_users" :key="user" size="small" round>
                          {{ user }}
                        </NTag>
                        <span v-if="!groupState.active_users?.length" class="group-detail__empty-line">暂无活跃成员</span>
                      </div>
                    </div>

                    <div class="group-detail__topic-block">
                      <h4>近期话题</h4>
                      <div class="group-detail__chips">
                        <NTag
                          v-for="topic in groupState.recent_topics"
                          :key="topic"
                          size="small"
                          type="info"
                          round
                        >
                          {{ topic }}
                        </NTag>
                        <span v-if="!groupState.recent_topics?.length" class="group-detail__empty-line">暂无话题</span>
                      </div>
                    </div>
                  </template>
                </AppPanelSection>

                <AppPanelSection eyebrow="Recent Messages" title="最近消息">
                  <template #aside>
                    <StateBadge :label="`${groupMessages.length} 条`" status="default" compact />
                  </template>

                  <div v-if="groupMessages.length > 0" class="group-timeline">
                    <div
                      v-for="(msg, idx) in groupMessages"
                      :key="`${msg.timestamp}-${idx}`"
                      class="group-timeline__item"
                      :class="timelineType(msg) === 'assistant' ? 'group-timeline__item--assistant' : 'group-timeline__item--user'"
                    >
                      <div class="group-timeline__meta">
                        <strong>{{ speakerLabel(msg) }}</strong>
                        <span>{{ msg.timestamp }}</span>
                      </div>
                      <p class="group-timeline__message">
                        {{ msg.message || '空消息' }}
                      </p>
                    </div>
                  </div>

                  <EmptyState
                    v-else
                    compact
                    title="暂无消息记录"
                    description="当前消息日志里还没有这组群聊的最近消息。"
                    :icon="ChatbubbleEllipsesOutline"
                  />
                </AppPanelSection>

                <AppPanelSection eyebrow="Policy History" title="最近策略调整">
                  <template #aside>
                    <StateBadge :label="`${profileAuditEntries.length} 条`" status="default" compact />
                  </template>

                  <div v-if="profileAuditEntries.length > 0" class="group-audit-list">
                    <div
                      v-for="entry in profileAuditEntries"
                      :key="entry.id"
                      class="group-audit-item"
                    >
                      <div class="group-audit-item__head">
                        <strong>{{ auditActionLabel(entry.action) }}</strong>
                        <span>{{ formatAuditTime(entry.saved_at) }}</span>
                      </div>

                      <div class="group-detail__chips group-detail__chips--compact">
                        <NTag
                          size="small"
                          round
                          :type="entry.action === 'reset' ? 'warning' : 'info'"
                        >
                          {{ entry.summary?.changed_count || entry.changes.length || 0 }} 项变化
                        </NTag>
                        <NTag
                          v-for="change in entry.changes.slice(0, 4)"
                          :key="`${entry.id}-${change.field}`"
                          size="small"
                          round
                        >
                          {{ change.label }}
                        </NTag>
                      </div>

                      <div v-if="entry.changes.length > 0" class="group-audit-item__changes">
                        <div
                          v-for="change in entry.changes.slice(0, 6)"
                          :key="`${entry.id}-${change.field}-detail`"
                          class="group-audit-item__change"
                        >
                          <span>{{ change.label }}</span>
                          <strong>{{ change.before ?? '空' }} → {{ change.after ?? '空' }}</strong>
                        </div>
                      </div>
                    </div>
                  </div>

                  <EmptyState
                    v-else
                    compact
                    title="还没有策略调整记录"
                    description="等这个群第一次保存或恢复后，这里会保留最近的审计轨迹。"
                    :icon="ShieldCheckmarkOutline"
                  />
                </AppPanelSection>
              </NTabPane>
            </NTabs>

            <template #footer>
              <NPopconfirm
                v-if="selectedGroup.profile_customized"
                @positive-click="resetProfileOverride"
              >
                <template #trigger>
                  <NButton tertiary :loading="profileResetting">
                    恢复全局默认
                  </NButton>
                </template>
                确认恢复为全局默认？当前群的覆盖会被清空。
              </NPopconfirm>
              <span v-if="profileDirty" class="group-detail__footer-hint">
                有未保存改动，关闭抽屉会丢失
              </span>
              <NButton
                type="primary"
                :loading="profileSaving"
                :disabled="!profileDirty"
                @click="saveProfile"
              >
                <template #icon>
                  <NIcon :component="SaveOutline" />
                </template>
                保存
              </NButton>
            </template>
          </AppDrawerLayout>
        </template>
      </NDrawerContent>
    </NDrawer>

    <NModal
      v-model:show="renameDialogVisible"
      preset="card"
      title="设置 Bot 端备注"
      style="width: 420px;"
      :mask-closable="!renameSubmitting"
    >
      <NSpace vertical :size="8">
        <NText depth="3">
          仅 Bot 端可见的群备注（OneBot set_group_remark），不会修改群本身。留空即清除。
        </NText>
        <NInput
          v-model:value="renameDraft"
          maxlength="60"
          show-count
          placeholder="例如：摸鱼群（朋友 A）"
          :disabled="renameSubmitting"
          @keyup.enter="submitRename"
        />
      </NSpace>
      <template #footer>
        <NSpace justify="end">
          <NButton :disabled="renameSubmitting" @click="renameDialogVisible = false">取消</NButton>
          <NButton type="primary" :loading="renameSubmitting" @click="submitRename">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal
      v-model:show="cardDialogVisible"
      preset="card"
      title="设置 Bot 群名片"
      style="width: 420px;"
      :mask-closable="!cardSubmitting"
    >
      <NSpace vertical :size="8">
        <NText depth="3">
          仅修改本群内 Bot 自己的群名片，留空则使用昵称。
        </NText>
        <NInput
          v-model:value="cardDraft"
          maxlength="60"
          show-count
          placeholder="例如：bot · 主号"
          :disabled="cardSubmitting"
          @keyup.enter="submitBotCard"
        />
      </NSpace>
      <template #footer>
        <NSpace justify="end">
          <NButton :disabled="cardSubmitting" @click="cardDialogVisible = false">取消</NButton>
          <NButton type="primary" :loading="cardSubmitting" @click="submitBotCard">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <NDrawer v-model:show="policyDrawerVisible" :width="520">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Access Gate"
            title="群聊门禁"
            description="控制 bot 能否进入某个群的学习/回复链路。门禁不替代单群参与模式。"
          >
            <template #aside>
              <NButton secondary size="small" :loading="policyRefreshing" @click="loadGroupPolicy(true)">
                刷新
              </NButton>
            </template>
          </AppDrawerHeader>
        </template>

        <AppDrawerLayout v-if="groupPolicy">
          <NAlert type="info" :show-icon="false" class="group-policy__tip">
            {{ policyModeLabel }}。修改保存后立即写入
            <code>{{ groupPolicyPath }}</code>。
          </NAlert>

          <NSkeleton v-if="policyLoading" :repeat="3" text />

          <NForm v-else label-placement="top" class="group-policy__form">
            <NGrid :cols="24" :x-gap="14" :y-gap="10" responsive="screen">
              <NFormItemGi :span="12" label="门禁模式">
                <NRadioGroup v-model:value="groupPolicy.mode">
                  <NRadioButton
                    v-for="opt in groupAccessModeOptions"
                    :key="String(opt.value)"
                    :value="opt.value"
                  >
                    {{ opt.label }}
                  </NRadioButton>
                </NRadioGroup>
              </NFormItemGi>

              <NFormItemGi :span="12" label="记录拦截日志">
                <NSwitch v-model:value="groupPolicy.log_dropped" />
              </NFormItemGi>

              <NFormItemGi :span="24" label="白名单群聊">
                <div class="group-policy__stack">
                  <NDynamicTags
                    :value="groupPolicy.whitelist"
                    @update:value="groupPolicy.whitelist = policyGroupIds($event)"
                  />
                  <span class="group-policy__hint">
                    白名单模式下只有这些群可以发言/工具调用。仅接受纯数字群号。
                  </span>
                </div>
              </NFormItemGi>

              <NFormItemGi :span="24" label="黑名单群聊">
                <div class="group-policy__stack">
                  <NDynamicTags
                    :value="groupPolicy.blacklist"
                    @update:value="groupPolicy.blacklist = policyGroupIds($event)"
                  />
                  <span class="group-policy__hint">
                    黑名单模式下这些群禁止发言/工具调用。仅接受纯数字群号。
                  </span>
                </div>
              </NFormItemGi>
            </NGrid>
          </NForm>

          <div class="group-policy__counts">
            <StateBadge :label="`开放 ${policyEffectiveOpenCount}`" status="success" compact />
            <StateBadge :label="`关闭 ${policyEffectiveClosedCount}`" status="warning" compact />
          </div>

          <template #footer>
            <span v-if="policyDirty" class="group-detail__footer-hint">
              有未保存改动
            </span>
            <NButton secondary :disabled="!policyDirty" @click="resetGroupPolicyDraft">
              重置草稿
            </NButton>
            <NButton
              type="primary"
              :loading="policySaving"
              :disabled="!policyDirty"
              @click="saveGroupPolicy"
            >
              <template #icon>
                <NIcon :component="SaveOutline" />
              </template>
              保存门禁
            </NButton>
          </template>
        </AppDrawerLayout>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.groups-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.groups-summary__group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--om-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.groups-summary__group--presence {
  padding: 6px;
  gap: 4px;
}

.groups-summary__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 13px;
  line-height: 1.2;
  white-space: nowrap;
  color: var(--chip-text, var(--om-text-2));
  background: var(--chip-bg, transparent);
  border: 1px solid var(--chip-border, transparent);
}

.groups-summary__chip-label {
  color: inherit;
}

.groups-summary__chip strong {
  color: var(--chip-strong, var(--om-text-1));
  font-weight: 700;
}

.groups-summary__chip--total {
  --chip-text: var(--om-text-1);
  --chip-strong: var(--om-text-1);
  padding-left: 6px;
}

.groups-summary__chip--soft {
  --chip-text: var(--om-text-2);
  --chip-bg: color-mix(in srgb, var(--om-surface-2) 70%, transparent);
}

.groups-summary__chip--success {
  --chip-text: var(--om-success);
  --chip-strong: var(--om-success);
  --chip-bg: color-mix(in srgb, var(--om-success) 12%, transparent);
  --chip-border: color-mix(in srgb, var(--om-success) 28%, transparent);
}

.groups-summary__chip--info {
  --chip-text: var(--om-info);
  --chip-strong: var(--om-info);
  --chip-bg: color-mix(in srgb, var(--om-info) 12%, transparent);
  --chip-border: color-mix(in srgb, var(--om-info) 28%, transparent);
}

.groups-summary__chip--warning {
  --chip-text: var(--om-warning);
  --chip-strong: var(--om-warning);
  --chip-bg: color-mix(in srgb, var(--om-warning) 14%, transparent);
  --chip-border: color-mix(in srgb, var(--om-warning) 32%, transparent);
}

.groups-summary__dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: currentColor;
}

.groups-summary__policy {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 6px 12px;
  border: 1px solid color-mix(in srgb, var(--om-info) 32%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-info) 10%, transparent);
  color: var(--om-info);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition: background-color 0.16s ease, border-color 0.16s ease, transform 0.12s ease;
}

.groups-summary__policy:hover {
  background: color-mix(in srgb, var(--om-info) 18%, transparent);
  border-color: color-mix(in srgb, var(--om-info) 50%, transparent);
}

.groups-summary__policy:active {
  transform: translateY(1px);
}

.groups-summary__policy-label {
  color: inherit;
}

.groups-summary__policy strong {
  color: var(--om-info);
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-info) 16%, transparent);
}

.groups-summary__policy-counts {
  color: color-mix(in srgb, var(--om-info) 70%, var(--om-text-3));
  font-size: 12px;
}

.groups-summary__policy-chevron {
  color: var(--om-info);
  opacity: 0.7;
}

.groups-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.groups-toolbar__search {
  width: min(320px, 100%);
}

.groups-toolbar__count {
  color: var(--om-text-3);
  font-size: 12px;
}

.groups-table:deep(.n-data-table-th) {
  background: var(--om-surface-2);
  padding-top: 10px;
  padding-bottom: 10px;
}

.groups-table:deep(.n-data-table-td) {
  padding-top: 14px;
  padding-bottom: 14px;
  vertical-align: middle;
  border-bottom-color: color-mix(in srgb, var(--om-border) 60%, transparent);
}

.groups-table:deep(.n-data-table-tr:hover .n-data-table-td) {
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
}

/* group-cell rules moved to unscoped block below — NDataTable render functions don't inherit scope id */

.group-feature-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-width: 100%;
}

.group-detail {
  display: grid;
  gap: 14px;
}

.group-detail__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--om-surface-2);
  border: 1px solid var(--om-border);
}

.group-detail__meta-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  flex: 1 1 auto;
  min-width: 0;
}

.group-detail__meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.group-detail__meta-label {
  color: var(--om-text-3);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.group-detail__meta-value {
  color: var(--om-text-1);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.group-detail__meta-sub {
  color: var(--om-text-3);
  font-size: 12px;
  margin-left: 4px;
}

.group-detail__meta-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
}

.group-detail__tabs {
  --tab-pad: 14px;
}

.group-detail__tabs:deep(.n-tab-pane) {
  display: grid;
  gap: 14px;
  padding-top: 14px;
}

.group-detail__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.group-detail__stats--compact {
  margin-bottom: 14px;
}

.group-detail__stat {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.group-detail__stat span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.group-detail__stat strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 15px;
  line-height: 1.5;
}

.group-detail__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.group-detail__chips--compact {
  gap: 6px;
}

.group-detail__topic-block + .group-detail__topic-block {
  margin-top: 12px;
}

.group-detail__topic-block h4 {
  margin: 0 0 10px;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.group-detail__empty-line {
  color: var(--om-text-3);
  font-size: 13px;
}

.group-detail__footer-hint {
  margin-right: auto;
  color: var(--om-warning);
  font-size: 12px;
}

.group-profile__fields {
  display: grid;
  gap: 14px;
}

.settings-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 64%, transparent);
}

.settings-row__copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.settings-row__label {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.settings-row__helper {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.group-profile__form :deep(.n-form-item-label__text) {
  font-weight: 600;
}

.group-profile__form :deep(.n-radio-group),
.group-profile__fields :deep(.n-radio-group) {
  flex-wrap: wrap;
}

.group-profile__tip {
  margin-bottom: 4px;
  border-radius: 14px;
}

.group-profile__stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.group-tool-matrix {
  display: grid;
  gap: 14px;
}

.group-tool-matrix__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.group-tool-cluster {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 76%, transparent);
}

.group-tool-cluster__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.group-tool-cluster__head strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.group-tool-cluster__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-tool-cluster__body {
  display: grid;
  gap: 10px;
}

.group-tool-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 116px;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--om-border) 88%, transparent);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 36%, transparent);
}

.group-tool-row__copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.group-tool-row__copy strong {
  color: var(--om-text-1);
  font-size: 13px;
}

.group-tool-row__copy span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.group-tool-row__select {
  align-self: center;
}

.group-audit-list {
  display: grid;
  gap: 12px;
}

.group-audit-item {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 74%, transparent);
}

.group-audit-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.group-audit-item__head strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.group-audit-item__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-audit-item__changes {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.group-audit-item__change {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 84%, transparent);
}

.group-audit-item__change span {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-audit-item__change strong {
  color: var(--om-text-1);
  font-size: 12px;
  text-align: right;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.group-timeline {
  display: grid;
  gap: 10px;
}

.group-timeline__item {
  padding: 14px;
  border-radius: 16px;
}

.group-timeline__item--user {
  background: color-mix(in srgb, var(--om-surface-solid) 76%, transparent);
}

.group-timeline__item--assistant {
  background: rgba(var(--primary-color), 0.1);
}

.group-timeline__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.group-timeline__meta strong {
  color: var(--om-text-1);
  font-size: 13px;
}

.group-timeline__meta span {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-timeline__message {
  margin: 8px 0 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.group-policy__tip {
  border-radius: 14px;
}

.group-policy__form :deep(.n-form-item-label__text) {
  font-weight: 600;
}

.group-policy__stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.group-policy__hint {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.group-policy__counts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 760px) {
  .group-detail__stats,
  .group-tool-matrix__stats {
    grid-template-columns: 1fr;
  }

  .group-tool-row {
    grid-template-columns: 1fr;
  }

  .group-tool-row__select {
    width: 100%;
  }

  .groups-toolbar {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

<style>
/* unscoped: NDataTable render() builds DOM in a separate render context where Vue scope ids don't propagate */
.group-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding-left: 12px;
  border-left: 3px solid var(--cell-accent, var(--om-border));
}

.group-cell--success {
  --cell-accent: var(--om-success);
}

.group-cell--info {
  --cell-accent: var(--om-info);
}

.group-cell--warning {
  --cell-accent: var(--om-warning);
}

.group-cell--error {
  --cell-accent: var(--om-danger);
}

.group-cell__title {
  display: block;
  width: 100%;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-cell__subtitle {
  display: block;
  width: 100%;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-cell__meta {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border: 1px solid color-mix(in srgb, var(--cell-accent, var(--om-text-3)) 45%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--cell-accent, var(--om-text-3)) 22%, var(--om-surface-2));
  color: var(--om-text-2);
  font-family: var(--om-font-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  font-variant-numeric: tabular-nums;
}

.group-cell__meta-prefix {
  color: var(--om-text-3);
  font-weight: 600;
  opacity: 0.7;
}

.group-cell__hint {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-cell__member {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.group-cell__member-count {
  color: var(--om-text-1);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.group-cell__activity {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.group-cell__activity-meta {
  color: var(--om-text-3);
  font-size: 12px;
}
</style>

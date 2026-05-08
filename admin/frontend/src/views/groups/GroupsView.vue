<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  PeopleOutline,
  RefreshOutline,
  SaveOutline,
  ShieldCheckmarkOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { api } from '../../api/client'
import AppDrawerHeader from '../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

type ReplyStyle = 'default' | 'gentle' | 'playful' | 'concise' | 'energetic' | 'steady'
type StickerMode = 'inherit' | 'off' | 'rarely' | 'normal' | 'frequently'
type ToolMode = 'inherit' | 'allow' | 'block'

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
  blocked_users?: Array<string | number>
}

interface GroupItem {
  group_id: string
  group_name?: string
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
  profile_override: GroupProfileOverride
  profile_customized: boolean
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

const loading = ref(true)
const refreshing = ref(false)
const detailRefreshing = ref(false)
const profileSaving = ref(false)
const profileResetting = ref(false)
const groups = ref<GroupItem[]>([])
const searchText = ref('')
const replyMode = ref<'all' | 'at_only' | 'free'>('all')
const selectedGroup = ref<GroupItem | null>(null)
const drawerVisible = ref(false)
const groupState = ref<GroupState | null>(null)
const groupMessages = ref<GroupMessage[]>([])
const toolCatalog = ref<GroupToolCatalogItem[]>([])
const profileAuditEntries = ref<GroupProfileAuditEntry[]>([])
const stateLoading = ref(false)
const profileDraft = ref<GroupProfileForm | null>(null)
const profileOriginal = ref<GroupProfileForm | null>(null)
const showAdvancedDetails = ref(false)

const message = useMessage()

const replyModeOptions: SelectOption[] = [
  { label: '全部模式', value: 'all' },
  { label: '@ 才回复', value: 'at_only' },
  { label: '自由回复', value: 'free' },
]

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

const filteredGroups = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  return groups.value.filter((group) => {
    if (replyMode.value === 'at_only' && !group.at_only) return false
    if (replyMode.value === 'free' && group.at_only) return false
    if (!query) return true
    const haystack = `${group.group_id} ${group.group_name || ''}`.toLowerCase()
    return haystack.includes(query)
  })
})

const customProfileCount = computed(() =>
  groups.value.filter(group => group.profile_customized).length,
)

const toolsDisabledCount = computed(() =>
  groups.value.filter(group => !group.tools_enabled).length,
)

const slangDisabledCount = computed(() =>
  groups.value.filter(group => !group.slang_enabled).length,
)

const averageTalkValue = computed(() => {
  if (!groups.value.length) return '--'
  const total = groups.value.reduce((sum, group) => sum + Number(group.talk_value || 0), 0)
  return (total / groups.value.length).toFixed(2)
})

const profileDirty = computed(() => (
  JSON.stringify(profileDraft.value || {}) !== JSON.stringify(profileOriginal.value || {})
))

const selectedFeatureChips = computed(() => (
  selectedGroup.value ? groupFeatureChips(selectedGroup.value) : []
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
    minWidth: 220,
    render: row => h('div', { class: 'group-cell' }, [
      h('strong', { class: 'group-cell__title' }, row.group_name || `群 ${row.group_id}`),
      h('span', { class: 'group-cell__meta' }, row.group_id),
    ]),
  },
  {
    title: '回复模式',
    key: 'at_only',
    width: 120,
    render: row => h(NTag, {
      size: 'small',
      type: row.at_only ? 'warning' : 'success',
      round: true,
    }, () => row.at_only ? '@ 才回复' : '自由回复'),
  },
  {
    title: '风格',
    key: 'reply_style',
    width: 110,
    render: row => h(NTag, {
      size: 'small',
      type: row.reply_style === 'default' ? 'default' : 'info',
      round: true,
    }, () => REPLY_STYLE_LABELS[row.reply_style]),
  },
  {
    title: '能力策略',
    key: 'features',
    minWidth: 220,
    render: row => h('div', { class: 'group-feature-list' }, groupFeatureChips(row).map(chip =>
      h(NTag, {
        size: 'small',
        type: chip.type,
        round: true,
      }, () => chip.label),
    )),
  },
  {
    title: '发言值',
    key: 'talk_value',
    width: 90,
    render: row => h(NText, {}, () => Number(row.talk_value || 0).toFixed(2)),
  },
  {
    title: '',
    key: 'actions',
    width: 90,
    render: row => h(NButton, {
      size: 'small',
      secondary: true,
      onClick: () => openDrawer(row),
    }, () => '配置'),
  },
]

onMounted(() => {
  void loadGroups()
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

function groupFeatureChips(group: GroupItem): Array<{ label: string, type: 'default' | 'info' | 'warning' | 'success' | 'error' }> {
  const chips: Array<{ label: string, type: 'default' | 'info' | 'warning' | 'success' | 'error' }> = []
  chips.push({
    label: group.tools_enabled ? '工具开启' : '工具关闭',
    type: group.tools_enabled ? 'success' : 'warning',
  })
  chips.push({
    label: `贴纸 ${STICKER_MODE_LABELS[group.sticker_mode]}`,
    type: group.sticker_mode === 'off' ? 'warning' : (group.sticker_mode === 'inherit' ? 'default' : 'info'),
  })
  chips.push({
    label: group.slang_enabled ? '黑话开启' : '黑话关闭',
    type: group.slang_enabled ? 'success' : 'warning',
  })
  if (group.profile_customized) {
    chips.push({
      label: '已覆盖全局',
      type: 'info',
    })
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
  }
}

function hydrateProfileDraft(group: GroupItem) {
  const nextDraft = profileFromGroup(group)
  profileDraft.value = nextDraft
  profileOriginal.value = deepClone(nextDraft)
}

function syncSelectedFromList(nextGroup: GroupItem, preserveDraft = false) {
  groups.value = groups.value.map(group =>
    group.group_id === nextGroup.group_id
      ? { ...group, ...nextGroup, group_name: nextGroup.group_name || group.group_name }
      : group,
  )
  selectedGroup.value = groups.value.find(group => group.group_id === nextGroup.group_id) || nextGroup
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

async function openDrawer(group: GroupItem) {
  selectedGroup.value = group
  hydrateProfileDraft(group)
  showAdvancedDetails.value = false
  drawerVisible.value = true
  await refreshGroupDetail()
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
  if (!window.confirm('确认恢复为全局默认群策略吗？当前群的覆盖设置会被清空。')) return
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
</script>

<template>
  <AppPage
    title="群管理"
    eyebrow="Group Runtime"
    description="查看群聊运行差异，并为不同群设置独立的回复风格、主动节奏与功能策略。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NButton secondary :loading="refreshing" @click="loadGroups(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新群列表
        </NButton>
      </NSpace>
    </template>

    <div class="groups-metric-grid">
      <MetricCard
        title="群数量"
        :value="groups.length"
        hint="已从配置、调度器、日志和在线群列表聚合"
        :icon="PeopleOutline"
        accent="primary"
      />
      <MetricCard
        title="自定义 Profile"
        :value="customProfileCount"
        hint="已覆盖全局默认策略的群"
        :icon="ShieldCheckmarkOutline"
        accent="info"
      />
      <MetricCard
        title="工具关闭群"
        :value="toolsDisabledCount"
        hint="关闭工具调用，只保留自然回复"
        :icon="ChatbubbleEllipsesOutline"
        accent="warning"
      />
      <MetricCard
        title="黑话关闭群"
        :value="slangDisabledCount"
        :hint="averageTalkValue === '--' ? '暂无发言值数据' : `平均发言值 ${averageTalkValue}`"
        :icon="TimeOutline"
        accent="success"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="searchText"
          clearable
          placeholder="搜索群号或群名"
          style="width: min(280px, 100%)"
        />
        <NSelect
          v-model:value="replyMode"
          :options="replyModeOptions"
          style="width: 144px"
        />
      </template>
      <template #right>
        <NTag size="small" round>
          当前 {{ filteredGroups.length }} / {{ groups.length }} 个群
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <NDataTable
        v-if="filteredGroups.length > 0"
        :columns="columns"
        :data="filteredGroups"
        :row-key="(row: GroupItem) => row.group_id"
        :bordered="false"
        size="small"
        class="groups-table"
      />

      <EmptyState
        v-else
        title="没有匹配的群"
        description="尝试清空搜索条件，或切换回复模式筛选。"
        :icon="PeopleOutline"
      />
    </template>

    <NDrawer v-model:show="drawerVisible" :width="680">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Group Profile"
            :title="selectedGroup?.group_name || selectedGroup?.group_id || '群详情'"
            :description="selectedGroup ? `群号 ${selectedGroup.group_id}` : ''"
          >
            <template #aside>
              <NButton secondary size="small" :loading="detailRefreshing" @click="refreshGroupDetail">
                刷新详情
              </NButton>
            </template>
          </AppDrawerHeader>
        </template>

        <template v-if="selectedGroup && profileDraft">
          <AppDrawerLayout class="group-detail">
            <AppPanelSection eyebrow="Snapshot" title="当前群策略">
              <template #aside>
                <NTag
                  size="small"
                  round
                  :type="selectedGroup.profile_customized ? 'info' : 'default'"
                >
                  {{ selectedGroup.profile_customized ? '已覆盖全局' : '继承全局' }}
                </NTag>
              </template>

              <div class="group-detail__stats">
                <div class="group-detail__stat">
                  <span>群号</span>
                  <strong>{{ selectedGroup.group_id }}</strong>
                </div>
                <div class="group-detail__stat">
                  <span>发言值</span>
                  <strong>{{ Number(selectedGroup.talk_value || 0).toFixed(2) }}</strong>
                </div>
                <div class="group-detail__stat">
                  <span>规划间隔</span>
                  <strong>{{ selectedGroup.planner_smooth }}s</strong>
                </div>
                <div class="group-detail__stat">
                  <span>回复冷却</span>
                  <strong>{{ selectedGroup.debounce_seconds }}s</strong>
                </div>
                <div class="group-detail__stat">
                  <span>批量窗口</span>
                  <strong>{{ selectedGroup.batch_size }}</strong>
                </div>
                <div class="group-detail__stat">
                  <span>历史载入</span>
                  <strong>{{ selectedGroup.history_load_count }}</strong>
                </div>
              </div>

              <div class="group-detail__chips">
                <NTag
                  size="small"
                  round
                  :type="selectedGroup.at_only ? 'warning' : 'success'"
                >
                  {{ selectedGroup.at_only ? '@ 才回复' : '自由回复' }}
                </NTag>
                <NTag
                  v-for="chip in selectedFeatureChips"
                  :key="chip.label"
                  size="small"
                  round
                  :type="chip.type"
                >
                  {{ chip.label }}
                </NTag>
                <NTag size="small" round :type="selectedGroup.reply_style === 'default' ? 'default' : 'info'">
                  风格 {{ REPLY_STYLE_LABELS[selectedGroup.reply_style] }}
                </NTag>
              </div>

              <div v-if="selectedGroup.blocked_users.length > 0" class="group-detail__chips">
                <NTag
                  v-for="user in selectedGroup.blocked_users"
                  :key="String(user)"
                  size="small"
                  type="error"
                  round
                >
                  屏蔽 {{ user }}
                </NTag>
              </div>
            </AppPanelSection>

            <AppPanelSection eyebrow="Profile" title="群策略配置">
              <template #aside>
                <NTag size="small" round :type="profileDirty ? 'warning' : 'success'">
                  {{ profileDirty ? '有未保存修改' : '已同步' }}
                </NTag>
              </template>

              <NAlert type="info" :show-icon="false" class="group-profile__tip">
                保存时会自动把与全局默认完全相同的值回退为继承，避免把每群配置写死。
              </NAlert>

              <NForm label-placement="top" class="group-profile__form">
                <NGrid :cols="24" :x-gap="16" :y-gap="8" responsive="screen">
                  <NFormItemGi :span="12" label="回复模式">
                    <NSwitch v-model:value="profileDraft.at_only">
                      <template #checked>
                        @ 才回复
                      </template>
                      <template #unchecked>
                        自由回复
                      </template>
                    </NSwitch>
                  </NFormItemGi>

                  <NFormItemGi :span="12" label="回复风格">
                    <NSelect
                      v-model:value="profileDraft.reply_style"
                      :options="replyStyleOptions"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="8" label="发言值">
                    <NInputNumber
                      v-model:value="profileDraft.talk_value"
                      :min="0"
                      :max="1"
                      :step="0.05"
                      style="width: 100%"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="8" label="规划间隔">
                    <NInputNumber
                      v-model:value="profileDraft.planner_smooth"
                      :min="0"
                      :max="120"
                      :step="0.5"
                      style="width: 100%"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="8" label="回复冷却">
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

                  <NFormItemGi :span="12" label="贴纸策略">
                    <NSelect
                      v-model:value="profileDraft.sticker_mode"
                      :options="stickerModeOptions"
                    />
                  </NFormItemGi>

                  <NFormItemGi :span="6" label="工具调用">
                    <NSwitch v-model:value="profileDraft.tools_enabled" />
                  </NFormItemGi>

                  <NFormItemGi :span="6" label="黑话系统">
                    <NSwitch v-model:value="profileDraft.slang_enabled" />
                  </NFormItemGi>

                  <NFormItemGi :span="24" label="群内额外屏蔽用户">
                    <div class="group-profile__stack">
                      <NDynamicTags
                        :value="profileDraft.blocked_users"
                        @update:value="profileDraft.blocked_users = uniqueStrings($event)"
                      />
                      <span class="group-profile__hint">
                        这里只编辑当前群的额外屏蔽名单；全局屏蔽用户仍会继续生效。
                      </span>
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
                  </NFormItemGi>

                  <NFormItemGi :span="24" label="群附加提示词">
                    <NInput
                      v-model:value="profileDraft.custom_prompt"
                      type="textarea"
                      :autosize="{ minRows: 4, maxRows: 8 }"
                      placeholder="例如：少说教，多接梗；讨论问题时先给结论。"
                    />
                  </NFormItemGi>
                </NGrid>
              </NForm>
            </AppPanelSection>

            <AppPanelSection eyebrow="Advanced" title="高级治理">
              <template #aside>
                <NButton tertiary size="small" @click="showAdvancedDetails = !showAdvancedDetails">
                  {{ showAdvancedDetails ? '收起' : '展开' }}
                </NButton>
              </template>

              <p class="group-advanced-note">
                工具矩阵、实时状态、最近消息和策略审计都保留在这里，默认折叠，避免把日常群策略页面做得过重。
              </p>
            </AppPanelSection>

            <template v-if="showAdvancedDetails">
              <AppPanelSection eyebrow="Tool Matrix" title="群级工具矩阵">
              <template #aside>
                <NTag size="small" round :type="profileDraft.tools_enabled ? 'success' : 'warning'">
                  {{ profileDraft.tools_enabled ? `${toolCatalog.length} 个工具可治理` : '工具总开关已关闭' }}
                </NTag>
              </template>

              <NAlert type="info" :show-icon="false" class="group-profile__tip">
                当“允许工具”非空时，只会保留允许名单里的工具；“屏蔽工具”永远优先，适合按群禁用高风险或不合时宜的能力。
              </NAlert>

              <div v-if="toolCatalog.length > 0" class="group-tool-matrix">
                <div class="group-tool-matrix__stats">
                  <div class="group-detail__stat">
                    <span>允许名单</span>
                    <strong>{{ profileDraft.allowed_tools.length }}</strong>
                  </div>
                  <div class="group-detail__stat">
                    <span>屏蔽名单</span>
                    <strong>{{ profileDraft.blocked_tools.length }}</strong>
                  </div>
                  <div class="group-detail__stat">
                    <span>继承状态</span>
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

                      <NButtonGroup class="group-tool-row__modes">
                        <NButton
                          size="small"
                          :type="toolMode(tool.name) === 'inherit' ? 'primary' : 'default'"
                          :secondary="toolMode(tool.name) !== 'inherit'"
                          @click="setToolMode(tool.name, 'inherit')"
                        >
                          继承
                        </NButton>
                        <NButton
                          size="small"
                          :type="toolMode(tool.name) === 'allow' ? 'success' : 'default'"
                          :secondary="toolMode(tool.name) !== 'allow'"
                          @click="setToolMode(tool.name, 'allow')"
                        >
                          允许
                        </NButton>
                        <NButton
                          size="small"
                          :type="toolMode(tool.name) === 'block' ? 'error' : 'default'"
                          :secondary="toolMode(tool.name) !== 'block'"
                          @click="setToolMode(tool.name, 'block')"
                        >
                          屏蔽
                        </NButton>
                      </NButtonGroup>
                    </div>
                  </div>
                </div>
              </div>

              <EmptyState
                v-else
                compact
                title="运行时还没有可治理工具"
                description="当前 ToolRegistry 为空，等插件完成注册后这里会自动出现可配置的工具矩阵。"
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
                <NTag size="small" round>
                  {{ groupMessages.length }} 条
                </NTag>
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
                <NTag size="small" round>
                  {{ profileAuditEntries.length }} 条
                </NTag>
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
                description="等这个群第一次保存或恢复群策略后，这里会保留最近的审计轨迹。"
                :icon="ShieldCheckmarkOutline"
              />
              </AppPanelSection>
            </template>

            <template #footer>
              <NButton
                v-if="selectedGroup.profile_customized"
                tertiary
                :loading="profileResetting"
                @click="resetProfileOverride"
              >
                恢复全局默认
              </NButton>
              <NButton secondary :disabled="!profileDirty" @click="resetDraft">
                重置草稿
              </NButton>
              <NButton
                type="primary"
                :loading="profileSaving"
                :disabled="!profileDirty"
                @click="saveProfile"
              >
                <template #icon>
                  <NIcon :component="SaveOutline" />
                </template>
                保存群策略
              </NButton>
            </template>
          </AppDrawerLayout>
        </template>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.groups-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.groups-table:deep(.n-data-table-th) {
  background: var(--om-surface-2);
}

.groups-table:deep(.n-data-table-tr) {
  cursor: default;
}

.group-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.group-cell__title {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.group-cell__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.group-feature-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.group-detail {
  display: grid;
  gap: 14px;
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
  margin-top: 14px;
}

.group-detail__chips--compact {
  margin-top: 8px;
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

.group-profile__tip {
  margin-bottom: 16px;
  border-radius: 14px;
}

.group-profile__form :deep(.n-form-item-label__text) {
  font-weight: 600;
}

.group-profile__stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.group-profile__hint {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.group-advanced-note {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
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
  grid-template-columns: minmax(0, 1fr) auto;
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

.group-tool-row__modes {
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

@media (max-width: 1180px) {
  .groups-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .groups-metric-grid,
  .group-detail__stats,
  .group-tool-matrix__stats,
  .group-tool-row {
    grid-template-columns: 1fr;
  }

  .group-tool-row__modes,
  .group-audit-item__change {
    justify-content: flex-start;
  }
}
</style>

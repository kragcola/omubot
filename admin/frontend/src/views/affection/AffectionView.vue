<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  HeartOutline,
  PersonOutline,
  RefreshOutline,
  SearchOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSkeleton,
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

interface User {
  user_id: string
  score: number
  tier: string
  total_interactions: number
  daily_count: number
  custom_nickname: string
  preferred_suffix: string
  last_interaction: string
}

interface UserDetail extends User {
  daily_date?: string
  group_nicknames?: Record<string, string>
  default_suffix?: string
  first_interaction?: string
  mood_bonus_valence?: number
}

const users = ref<User[]>([])
const loading = ref(true)
const refreshing = ref(false)
const searchText = ref('')
const tierFilter = ref('')

const selectedUser = ref<UserDetail | null>(null)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const saving = ref(false)

const editNickname = ref('')
const editSuffix = ref('')
const editScore = ref(0)

const message = useMessage()

const tierOptions: SelectOption[] = [
  { label: '全部等级', value: '' },
  { label: '亲密', value: 'close' },
  { label: '友好', value: 'friendly' },
  { label: '普通', value: 'neutral' },
]

const filteredUsers = computed(() => {
  const query = searchText.value.trim().toLowerCase()

  return users.value.filter((user) => {
    if (tierFilter.value && user.tier !== tierFilter.value) return false
    if (!query) return true

    return [
      user.user_id,
      user.custom_nickname,
      user.preferred_suffix,
    ].join(' ').toLowerCase().includes(query)
  })
})

const namedUsers = computed(() => users.value.filter(user => Boolean(user.custom_nickname?.trim())).length)
const closeUsers = computed(() => users.value.filter(user => user.tier === 'close').length)
const activeToday = computed(() => users.value.filter(user => user.daily_count > 0).length)
const averageScore = computed(() => {
  if (!users.value.length) return '--'
  const total = users.value.reduce((sum, user) => sum + Number(user.score || 0), 0)
  return (total / users.value.length).toFixed(1)
})

const columns: DataTableColumns<User> = [
  {
    title: '#',
    key: 'rank',
    width: 64,
    render: (_row, index) => h(NTag, {
      size: 'small',
      round: true,
      type: index < 3 ? 'success' : 'default',
    }, () => `${index + 1}`),
  },
  {
    title: '用户',
    key: 'user',
    minWidth: 220,
    render: row => h('div', { class: 'affection-user-cell' }, [
      h('strong', { class: 'affection-user-cell__title' }, row.custom_nickname || row.user_id),
      h('span', { class: 'affection-user-cell__meta' }, row.user_id),
    ]),
  },
  {
    title: '等级',
    key: 'tier',
    width: 96,
    render: row => h(NTag, {
      size: 'small',
      round: true,
      type: tierTagType(row.tier),
    }, () => tierLabel(row.tier)),
  },
  {
    title: '分数',
    key: 'score',
    width: 92,
    render: row => h(NText, {}, () => Number(row.score || 0).toFixed(1)),
  },
  {
    title: '总互动',
    key: 'total_interactions',
    width: 92,
  },
  {
    title: '今日互动',
    key: 'daily_count',
    width: 92,
  },
  {
    title: '最近互动',
    key: 'last_interaction',
    minWidth: 148,
    render: row => h(NText, { depth: '3' }, () => formatDate(row.last_interaction)),
  },
  {
    title: '',
    key: 'actions',
    width: 96,
    render: row => h(NButton, {
      size: 'small',
      secondary: true,
      onClick: () => openDetail(row),
    }, () => '详情'),
  },
]

onMounted(() => {
  void loadUsers()
})

async function loadUsers(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api('/api/admin/affection?limit=100')
    users.value = data.users || []
  } catch (error) {
    console.error('Failed to load affection:', error)
    users.value = []
    message.error('好感度列表加载失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function openDetail(user: User) {
  drawerVisible.value = true
  detailLoading.value = true
  const initialDetail: UserDetail = {
    ...user,
    group_nicknames: {},
    default_suffix: '',
    first_interaction: '',
    mood_bonus_valence: 0,
  }
  selectedUser.value = initialDetail
  syncEditor(initialDetail)

  try {
    const data = await api(`/api/admin/affection/${user.user_id}`)
    const loadedDetail: UserDetail = {
      ...user,
      ...data,
    }
    selectedUser.value = loadedDetail
    syncEditor(loadedDetail)
  } catch (error) {
    console.error('Failed to load affection detail:', error)
    message.error('详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

function syncEditor(user: UserDetail) {
  editNickname.value = user.custom_nickname || ''
  editSuffix.value = user.preferred_suffix || ''
  editScore.value = Number(user.score || 0)
}

async function save() {
  if (!selectedUser.value) return

  saving.value = true
  try {
    const data = await api(`/api/admin/affection/${selectedUser.value.user_id}`, {
      method: 'PATCH',
      body: {
        custom_nickname: editNickname.value,
        preferred_suffix: editSuffix.value,
        score: editScore.value,
      },
    })

    if (!data.ok) {
      message.error(data.error || '保存失败')
      return
    }

    selectedUser.value = {
      ...selectedUser.value,
      custom_nickname: editNickname.value,
      preferred_suffix: editSuffix.value,
      score: editScore.value,
    }

    users.value = users.value.map(user => user.user_id === selectedUser.value?.user_id
      ? {
          ...user,
          custom_nickname: editNickname.value,
          preferred_suffix: editSuffix.value,
          score: editScore.value,
        }
      : user)

    message.success('已保存')
    drawerVisible.value = false
  } catch (error) {
    console.error('Failed to save affection detail:', error)
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

function resetFilters() {
  searchText.value = ''
  tierFilter.value = ''
}

function tierLabel(tier: string) {
  if (tier === 'close') return '亲密'
  if (tier === 'friendly') return '友好'
  if (tier === 'neutral') return '普通'
  return tier || '未知'
}

function tierTagType(tier: string) {
  if (tier === 'close') return 'success'
  if (tier === 'friendly') return 'info'
  return 'default'
}

function formatDate(value?: string) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatSignedNumber(value?: number) {
  if (value == null) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`
}
</script>

<template>
  <AppPage
    title="好感度"
    eyebrow="Affection Profiles"
    description="查看用户关系等级、互动密度和命名偏好，快速定位需要人工修正的关系画像。"
  >
    <template #action>
      <NButton secondary :loading="refreshing" @click="loadUsers(true)">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新列表
      </NButton>
    </template>

    <div class="affection-metric-grid">
      <MetricCard
        title="画像总数"
        :value="users.length"
        hint="当前已建立好感度画像的用户数量"
        :icon="PersonOutline"
        accent="primary"
      />
      <MetricCard
        title="亲密等级"
        :value="closeUsers"
        hint="关系等级为 close 的用户数"
        :icon="HeartOutline"
        accent="success"
      />
      <MetricCard
        title="今日活跃"
        :value="activeToday"
        hint="今日互动计数大于 0 的画像数"
        :icon="ChatbubbleEllipsesOutline"
        accent="info"
      />
      <MetricCard
        title="平均分"
        :value="averageScore"
        :hint="namedUsers > 0 ? `${namedUsers} 个用户已自定义昵称` : '当前还没有自定义昵称'"
        :icon="SparklesOutline"
        accent="warning"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="searchText"
          clearable
          placeholder="搜索 QQ、昵称或称呼"
          class="affection-toolbar__search"
        >
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>
        <NSelect
          v-model:value="tierFilter"
          :options="tierOptions"
          class="affection-toolbar__filter"
        />
      </template>

      <template #right>
        <NButton secondary @click="resetFilters">
          重置
        </NButton>
        <NTag round size="small">
          当前 {{ filteredUsers.length }} / {{ users.length }} 位
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <NDataTable
        v-if="filteredUsers.length > 0"
        :columns="columns"
        :data="filteredUsers"
        :row-key="(row: User) => row.user_id"
        :bordered="false"
        size="small"
        class="affection-table"
        :max-height="580"
      />

      <EmptyState
        v-else
        title="没有匹配的关系画像"
        description="尝试清空搜索条件，或切换等级筛选查看完整列表。"
        :icon="HeartOutline"
      />
    </template>

    <NDrawer v-model:show="drawerVisible" :width="620">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Affection Profile"
            :title="selectedUser?.custom_nickname || selectedUser?.user_id || '关系画像详情'"
            :description="selectedUser ? `用户 ${selectedUser.user_id}` : ''"
          >
            <template v-if="selectedUser" #aside>
              <NTag round size="small" :type="tierTagType(selectedUser.tier)">
                {{ tierLabel(selectedUser.tier) }}
              </NTag>
            </template>
          </AppDrawerHeader>
        </template>

        <NSkeleton v-if="detailLoading" :repeat="10" text />

        <template v-else-if="selectedUser">
          <AppDrawerLayout class="affection-detail">
            <AppPanelSection eyebrow="Snapshot" title="关系快照">
              <div class="affection-detail__stats">
                <div class="affection-detail__stat">
                  <span>当前分数</span>
                  <strong>{{ Number(selectedUser.score || 0).toFixed(1) }}</strong>
                </div>
                <div class="affection-detail__stat">
                  <span>总互动</span>
                  <strong>{{ selectedUser.total_interactions }}</strong>
                </div>
                <div class="affection-detail__stat">
                  <span>今日互动</span>
                  <strong>{{ selectedUser.daily_count }}</strong>
                </div>
                <div class="affection-detail__stat">
                  <span>心情加成</span>
                  <strong>{{ formatSignedNumber(selectedUser.mood_bonus_valence) }}</strong>
                </div>
                <div class="affection-detail__stat">
                  <span>首次互动</span>
                  <strong>{{ formatDate(selectedUser.first_interaction) }}</strong>
                </div>
                <div class="affection-detail__stat">
                  <span>最近互动</span>
                  <strong>{{ formatDate(selectedUser.last_interaction) }}</strong>
                </div>
              </div>
            </AppPanelSection>

            <AppPanelSection eyebrow="Naming" title="称呼与编辑">

              <div class="affection-detail__form-grid">
                <label class="affection-detail__field">
                  <span>昵称</span>
                  <NInput v-model:value="editNickname" placeholder="为空则按默认昵称处理" />
                </label>
                <label class="affection-detail__field">
                  <span>偏好后缀</span>
                  <NInput v-model:value="editSuffix" placeholder="例如 同学、老师、宝宝" />
                </label>
                <label class="affection-detail__field affection-detail__field--full">
                  <span>分数 (0-100)</span>
                  <NInputNumber
                    v-model:value="editScore"
                    :min="0"
                    :max="100"
                    :step="1"
                    class="affection-detail__numeric"
                  />
                </label>
              </div>

              <div class="affection-detail__chips">
                <NTag round size="small">
                  默认后缀 {{ selectedUser.default_suffix || '--' }}
                </NTag>
                <NTag round size="small" type="info">
                  日统计日期 {{ selectedUser.daily_date || '--' }}
                </NTag>
              </div>
            </AppPanelSection>

            <AppPanelSection eyebrow="Group Nicknames" title="群内称呼">

              <div
                v-if="selectedUser.group_nicknames && Object.keys(selectedUser.group_nicknames).length > 0"
                class="affection-group-nicknames"
              >
                <div
                  v-for="(nickname, groupId) in selectedUser.group_nicknames"
                  :key="groupId"
                  class="affection-group-nicknames__item"
                >
                  <span>{{ groupId }}</span>
                  <strong>{{ nickname || '--' }}</strong>
                </div>
              </div>

              <EmptyState
                v-else
                compact
                title="还没有群内称呼覆盖"
                description="当前这个用户在各群里仍沿用默认称呼逻辑。"
                :icon="TimeOutline"
              />
            </AppPanelSection>

            <template #footer>
              <NButton secondary @click="drawerVisible = false">
                取消
              </NButton>
              <NButton type="primary" :loading="saving" @click="save">
                保存修改
              </NButton>
            </template>
          </AppDrawerLayout>
        </template>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.affection-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.affection-toolbar__search {
  width: min(260px, 100%);
}

.affection-toolbar__filter {
  width: 132px;
}

.affection-detail__numeric {
  width: 100%;
}

.affection-table:deep(.n-data-table-th) {
  background: var(--om-surface-2);
}

.affection-user-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.affection-user-cell__title {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.affection-user-cell__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.affection-detail {
  display: grid;
  gap: 14px;
}

.affection-detail__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.affection-detail__stat,
.affection-group-nicknames__item {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.affection-detail__stat span,
.affection-group-nicknames__item span,
.affection-detail__field span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.affection-detail__stat strong,
.affection-group-nicknames__item strong {
  display: block;
  margin-top: 8px;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.6;
}

.affection-detail__form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.affection-detail__field {
  display: grid;
  gap: 8px;
}

.affection-detail__field--full {
  grid-column: 1 / -1;
}

.affection-detail__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.affection-group-nicknames {
  display: grid;
  gap: 12px;
}

@media (max-width: 1100px) {
  .affection-metric-grid,
  .affection-detail__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .affection-metric-grid,
  .affection-detail__stats,
  .affection-detail__form-grid {
    grid-template-columns: 1fr;
  }
}
</style>

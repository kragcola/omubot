<script setup lang="ts">
import {
  AlbumsOutline,
  PauseCircleOutline,
  PlayOutline,
  PulseOutline,
  RefreshOutline,
  SearchOutline,
  TimeOutline,
  VolumeMuteOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NInput,
  NSelect,
  NSkeleton,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface SlotInfo {
  consecutive_skip: number
  msg_count: number
  pending_at: number | null
  last_fire_time: number | null
  last_user_id: string
  has_trigger: boolean
  is_muted: boolean
  has_running_task: boolean
}

interface SlotEntry extends SlotInfo {
  groupId: string
}

const loading = ref(true)
const refreshing = ref(false)
const slots = ref<Record<string, SlotInfo>>({})
const searchText = ref('')
const statusFilter = ref<'all' | 'muted' | 'running' | 'pending'>('all')
const lastLoadedAt = ref('')
const loadError = ref('')
const message = useMessage()

const statusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '已静音', value: 'muted' },
  { label: '生成中', value: 'running' },
  { label: '待发送', value: 'pending' },
]

const slotEntries = computed<SlotEntry[]>(() =>
  Object.entries(slots.value)
    .map(([groupId, slot]) => ({ groupId, ...slot }))
    .sort((a, b) => {
      const rankA = Number(a.has_running_task) * 4 + Number(a.has_trigger) * 2 + Number(!a.is_muted)
      const rankB = Number(b.has_running_task) * 4 + Number(b.has_trigger) * 2 + Number(!b.is_muted)
      if (rankA !== rankB) return rankB - rankA
      if (a.msg_count !== b.msg_count) return b.msg_count - a.msg_count
      return a.groupId.localeCompare(b.groupId)
    }),
)

const filteredSlots = computed(() => {
  const query = searchText.value.trim().toLowerCase()

  return slotEntries.value.filter((slot) => {
    if (statusFilter.value === 'muted' && !slot.is_muted) return false
    if (statusFilter.value === 'running' && !slot.has_running_task) return false
    if (statusFilter.value === 'pending' && !(slot.has_trigger || slot.pending_at)) return false
    if (!query) return true

    return `${slot.groupId} ${slot.last_user_id || ''}`.toLowerCase().includes(query)
  })
})

const totalSlots = computed(() => slotEntries.value.length)
const mutedSlots = computed(() => slotEntries.value.filter(slot => slot.is_muted).length)
const runningSlots = computed(() => slotEntries.value.filter(slot => slot.has_running_task).length)
const pendingSlots = computed(() => slotEntries.value.filter(slot => slot.has_trigger || slot.pending_at).length)

onMounted(() => {
  void loadSlots()
})

async function loadSlots(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  loadError.value = ''

  try {
    const data = await api('/api/admin/scheduler')
    slots.value = data.slots || {}
    loadError.value = data.error || ''
    lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch (error) {
    console.error('Failed to load scheduler:', error)
    loadError.value = '调度器状态加载失败'
    slots.value = {}
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function toggleMute(groupId: string, muted: boolean) {
  const action = muted ? 'unmute' : 'mute'

  try {
    const data = await api(`/api/admin/scheduler/${groupId}/${action}`, { method: 'POST' })
    if (!data.ok) {
      message.error(data.error || '操作失败')
      return
    }

    slots.value[groupId] = {
      ...slots.value[groupId],
      is_muted: Boolean(data.muted),
    }
    message.success(data.muted ? '已静音' : '已取消静音')
  } catch (error) {
    console.error('Failed to toggle mute:', error)
    message.error('操作失败')
  }
}

function resetFilters() {
  searchText.value = ''
  statusFilter.value = 'all'
}

function formatTimestamp(timestamp: number | null) {
  if (!timestamp) return '--'

  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function statusType(slot: SlotEntry) {
  if (slot.is_muted) return 'error'
  if (slot.has_running_task) return 'info'
  if (slot.has_trigger || slot.pending_at) return 'warning'
  return 'success'
}

function statusLabel(slot: SlotEntry) {
  if (slot.is_muted) return '已静音'
  if (slot.has_running_task) return '生成中'
  if (slot.has_trigger || slot.pending_at) return '待发送'
  return '正常'
}
</script>

<template>
  <AppPage
    title="调度器"
    eyebrow="Scheduler Runtime"
    description="查看群聊调度槽的运行压力、待发送状态与静音开关，快速识别需要人工干预的会话。"
  >
    <template #action>
      <div class="scheduler-hero-actions">
        <NTag round size="small" :type="loadError ? 'error' : 'success'">
          {{ loadError ? '调度器状态异常' : `已载入 ${totalSlots} 个槽位` }}
        </NTag>
        <NTag v-if="lastLoadedAt" round size="small">
          更新于 {{ lastLoadedAt }}
        </NTag>
        <NButton secondary :loading="refreshing" @click="loadSlots(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新状态
        </NButton>
      </div>
    </template>

    <div class="scheduler-metric-grid">
      <MetricCard
        title="活跃槽位"
        :value="totalSlots"
        hint="当前调度器中已建立状态的群聊槽位"
        :icon="AlbumsOutline"
        accent="primary"
      />
      <MetricCard
        title="已静音"
        :value="mutedSlots"
        hint="这些槽位已被手动关闭自动响应"
        :icon="VolumeMuteOutline"
        accent="warning"
      />
      <MetricCard
        title="生成中"
        :value="runningSlots"
        hint="当前有任务正在生成回复"
        :icon="PulseOutline"
        accent="info"
      />
      <MetricCard
        title="待发送"
        :value="pendingSlots"
        hint="已触发但仍在等待发送或调度"
        :icon="TimeOutline"
        accent="success"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="searchText"
          clearable
          placeholder="搜索群号或最后用户"
          style="width: min(260px, 100%)"
        >
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>
        <NSelect
          v-model:value="statusFilter"
          :options="statusOptions"
          style="width: 148px"
        />
      </template>

      <template #right>
        <NButton secondary @click="resetFilters">
          重置筛选
        </NButton>
        <NTag round size="small">
          当前 {{ filteredSlots.length }} / {{ totalSlots }} 个槽位
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <EmptyState
        v-if="totalSlots === 0"
        title="当前没有活跃调度槽"
        :description="loadError || '调度器还没有记录到正在活跃的群聊上下文。'"
        :icon="PauseCircleOutline"
      />

      <EmptyState
        v-else-if="filteredSlots.length === 0"
        compact
        title="没有匹配的槽位"
        description="尝试清空搜索条件，或切换回全部状态查看完整列表。"
        :icon="SearchOutline"
      />

      <div v-else class="scheduler-slot-grid">
        <AppCard
          v-for="slot in filteredSlots"
          :key="slot.groupId"
          bordered
          elevated
          interactive
          class="scheduler-slot-card"
        >
          <div class="scheduler-slot-card__head">
            <div class="scheduler-slot-card__title-block">
              <p class="scheduler-slot-card__eyebrow">
                Group Slot
              </p>
              <h3 class="scheduler-slot-card__title">
                {{ slot.groupId }}
              </h3>
            </div>

            <div class="scheduler-slot-card__tags">
              <NTag round size="small" :type="statusType(slot)">
                {{ statusLabel(slot) }}
              </NTag>
              <NTag
                v-if="slot.consecutive_skip > 0"
                round
                size="small"
                type="warning"
              >
                连续跳过 {{ slot.consecutive_skip }}
              </NTag>
            </div>
          </div>

          <div class="scheduler-slot-card__stats">
            <div class="scheduler-slot-card__stat">
              <span>消息数</span>
              <strong>{{ slot.msg_count }}</strong>
            </div>
            <div class="scheduler-slot-card__stat">
              <span>最后用户</span>
              <strong class="scheduler-slot-card__mono">{{ slot.last_user_id || '--' }}</strong>
            </div>
            <div class="scheduler-slot-card__stat">
              <span>最后触发</span>
              <strong>{{ formatTimestamp(slot.last_fire_time) }}</strong>
            </div>
            <div class="scheduler-slot-card__stat">
              <span>待发时间</span>
              <strong>{{ formatTimestamp(slot.pending_at) }}</strong>
            </div>
          </div>

          <AppCard bordered embedded class="scheduler-slot-card__summary">
            <div class="scheduler-slot-card__summary-item">
              <span>触发状态</span>
              <NText>{{ slot.has_trigger ? '已排队' : '暂无待发' }}</NText>
            </div>
            <div class="scheduler-slot-card__summary-item">
              <span>任务状态</span>
              <NText>{{ slot.has_running_task ? '回复生成中' : '空闲中' }}</NText>
            </div>
          </AppCard>

          <div class="scheduler-slot-card__footer">
            <NText depth="3" class="scheduler-slot-card__footer-note">
              {{ slot.is_muted ? '已停止自动响应，可随时恢复。' : '当前允许调度器自动生成和发送回复。' }}
            </NText>
            <NButton
              size="small"
              :type="slot.is_muted ? 'success' : 'warning'"
              @click="toggleMute(slot.groupId, slot.is_muted)"
            >
              <template #icon>
                <NIcon :component="slot.is_muted ? PlayOutline : VolumeMuteOutline" />
              </template>
              {{ slot.is_muted ? '取消静音' : '静音槽位' }}
            </NButton>
          </div>
        </AppCard>
      </div>
    </template>
  </AppPage>
</template>

<style scoped>
.scheduler-hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.scheduler-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.scheduler-slot-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.scheduler-slot-card {
  display: grid;
  gap: 16px;
  padding: 20px;
}

.scheduler-slot-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.scheduler-slot-card__title-block {
  min-width: 0;
}

.scheduler-slot-card__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.scheduler-slot-card__title {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.scheduler-slot-card__tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.scheduler-slot-card__stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.scheduler-slot-card__stat {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.scheduler-slot-card__stat span,
.scheduler-slot-card__summary-item span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.scheduler-slot-card__stat strong {
  display: block;
  margin-top: 8px;
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scheduler-slot-card__mono {
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
}

.scheduler-slot-card__summary {
  display: grid;
  gap: 10px;
  padding: 16px;
  border-radius: 18px;
}

.scheduler-slot-card__summary-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.scheduler-slot-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.scheduler-slot-card__footer-note {
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .scheduler-metric-grid,
  .scheduler-slot-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .scheduler-metric-grid,
  .scheduler-slot-grid,
  .scheduler-slot-card__stats {
    grid-template-columns: 1fr;
  }

  .scheduler-slot-card__head,
  .scheduler-slot-card__footer {
    flex-direction: column;
    align-items: stretch;
  }

  .scheduler-slot-card__tags {
    justify-content: flex-start;
  }
}
</style>

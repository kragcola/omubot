<script setup lang="ts">
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  FlashOutline,
  GitNetworkOutline,
  PulseOutline,
  RefreshOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import { useSSE } from '../../composables/useSSE'

interface DashboardUsage {
  total_calls?: number
  total_input_tokens?: number
  total_output_tokens?: number
}

interface DashboardMood {
  energy: number
  valence: number
  openness: number
  tension: number
  label: string
}

interface DashboardSlot {
  time: string
  activity: string
  mood_hint: string
  location: string
}

interface DashboardSchedule {
  theme: string
  day_narrative: string
  slots: DashboardSlot[]
}

interface DashboardData {
  uptime_seconds: number
  usage?: DashboardUsage
  mood?: DashboardMood | null
  schedule?: DashboardSchedule | null
}

interface DashboardHealth {
  bot: string
  napcat: string
  uptime_seconds: number
}

interface DashboardHealthAlert {
  id: string
  severity: 'error' | 'warning' | 'info' | string
  title: string
  detail: string
  action: string
  metric?: string
}

interface DashboardMaintenanceWindow {
  recommended: boolean
  restart_recommended: boolean
  summary?: string
}

interface DashboardServicesHealth {
  alerts?: DashboardHealthAlert[]
  maintenance_window?: DashboardMaintenanceWindow
}

interface DashboardSlangSummary {
  candidate_count: number
  ai_pending_review_count: number
  approved_count: number
  today_hits: number
}

interface PendingItem {
  id: string
  title: string
  value: string
  note: string
  route: string
  severity: 'warning' | 'error' | 'info' | 'success'
}

const router = useRouter()

const data = ref<DashboardData | null>(null)
const health = ref<DashboardHealth | null>(null)
const servicesHealth = ref<DashboardServicesHealth | null>(null)
const slangSummary = ref<DashboardSlangSummary | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const loadError = ref('')
const lastLoadedAt = ref('')
const nowTick = ref(Date.now())
let clockTimer: ReturnType<typeof setInterval> | null = null
const { logs, connected } = useSSE()

const compactFormatter = new Intl.NumberFormat('zh-CN', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

const visibleLogs = computed(() =>
  logs.value.filter(entry => isDashboardLogVisible(entry.channel, entry.message)),
)

const importantLogs = computed(() => {
  const priorityLogs = visibleLogs.value.filter(entry => entry.level === 'ERROR' || entry.level === 'WARNING')
  const source = priorityLogs.length > 0 ? priorityLogs : visibleLogs.value
  return source.slice(-8).reverse()
})

const scheduleSlots = computed(() => data.value?.schedule?.slots ?? [])
const scheduleTimelineSlots = computed(() => {
  const slots = scheduleSlots.value
  if (slots.length === 0) return []

  const timeline = slots
    .map((slot, index) => {
      const minute = parseSlotMinute(slot.time)
      return {
        ...slot,
        _sourceIndex: index,
        _minute: minute == null ? index * 10_000 : minute,
      }
    })
    .sort((a, b) => a._minute - b._minute)

  const hasCrossMidnight = timeline.some(slot => slot._minute >= 18 * 60)
    && timeline.some(slot => slot._minute < 6 * 60)

  const normalized = timeline.map(slot => ({
    ...slot,
    _timelineMinute: hasCrossMidnight && slot._minute < 6 * 60
      ? slot._minute + 24 * 60
      : slot._minute,
  }))

  const currentMinuteRaw = getNowMinute(nowTick.value)
  const currentMinute = hasCrossMidnight && currentMinuteRaw < 6 * 60
    ? currentMinuteRaw + 24 * 60
    : currentMinuteRaw

  const upcoming = normalized
    .filter(slot => slot._timelineMinute >= currentMinute)
    .sort((a, b) => a._timelineMinute - b._timelineMinute)
  const passed = normalized
    .filter(slot => slot._timelineMinute < currentMinute)
    .sort((a, b) => b._timelineMinute - a._timelineMinute)

  return [...upcoming, ...passed].map((slot, index) => ({
    ...slot,
    isPast: slot._timelineMinute < currentMinute,
    isCurrentLead: index === 0,
  }))
})

const nextSlot = computed(() => scheduleTimelineSlots.value[0] ?? null)
const maintenanceWindow = computed(() => servicesHealth.value?.maintenance_window || null)
const healthAlerts = computed(() => servicesHealth.value?.alerts || [])
const pendingItems = computed<PendingItem[]>(() => {
  const items: PendingItem[] = []

  if ((slangSummary.value?.candidate_count || 0) > 0) {
    items.push({
      id: 'slang-candidate',
      title: '黑话待审核',
      value: `${slangSummary.value?.candidate_count || 0} 条`,
      note: '新词条还没有人工确认，暂时不会直接进入 Prompt。',
      route: '/slang',
      severity: 'warning',
    })
  }

  if ((slangSummary.value?.ai_pending_review_count || 0) > 0) {
    items.push({
      id: 'slang-ai-review',
      title: 'AI 待人工复核',
      value: `${slangSummary.value?.ai_pending_review_count || 0} 条`,
      note: '这些词条已经被 AI 通过，需要你做最后确认或否决。',
      route: '/slang',
      severity: 'info',
    })
  }

  if (health.value?.napcat !== 'connected') {
    items.push({
      id: 'napcat',
      title: 'NapCat 连接异常',
      value: health.value?.napcat === 'connected' ? '已连接' : '待处理',
      note: '消息适配层没有稳定连上时，Bot 无法正常收发群消息。',
      route: '/system',
      severity: 'error',
    })
  }

  if (maintenanceWindow.value?.restart_recommended) {
    items.push({
      id: 'restart',
      title: '建议重启验证',
      value: '需确认',
      note: maintenanceWindow.value.summary || '当前运行态建议在低峰期执行一次重启验证。',
      route: '/system',
      severity: 'warning',
    })
  }

  for (const alert of healthAlerts.value.slice(0, 2)) {
    items.push({
      id: `alert-${alert.id}`,
      title: alert.title,
      value: alert.metric || '需关注',
      note: alert.detail || alert.action || '系统检测到需要人工关注的运行信号。',
      route: '/system',
      severity: alert.severity === 'error' ? 'error' : alert.severity === 'warning' ? 'warning' : 'info',
    })
  }

  return items.slice(0, 4)
})

const primaryShortcut = computed(() => {
  if ((slangSummary.value?.candidate_count || 0) > 0 || (slangSummary.value?.ai_pending_review_count || 0) > 0) {
    return {
      label: '去处理待审核',
      route: '/slang',
    }
  }
  if (pendingItems.value.some(item => item.route === '/system')) {
    return {
      label: '查看系统异常',
      route: '/system',
    }
  }
  return null
})

const heroTitle = computed(() => {
  if (loadError.value) return '控制台暂时无法同步运行信息'
  if (health.value?.bot === 'running' && health.value?.napcat === 'connected') {
    return '今天的运行状态整体稳定'
  }
  if (health.value?.bot === 'running') return 'Bot 在线，但连接或服务需要留意'
  return '当前运行状态需要人工检查'
})

const heroNarrative = computed(() => {
  if (loadError.value) return loadError.value
  if (pendingItems.value.length > 0) {
    return `当前有 ${pendingItems.value.length} 项需要你留意的事项；建议先看下方待处理清单，再决定是否进入系统页排查。`
  }
  const narrative = data.value?.schedule?.day_narrative?.trim()
  if (narrative) return narrative
  return '今天没有明显待办，仪表盘会持续展示运行状态、节奏变化和关键日志。'
})

const moodMeters = computed(() => {
  const mood = data.value?.mood
  if (!mood) return []

  return [
    {
      label: '能量',
      value: normalizePercent(mood.energy),
      display: `${normalizePercent(mood.energy)}%`,
      color: '#2E8F6B',
    },
    {
      label: '张力',
      value: normalizePercent(mood.tension),
      display: `${normalizePercent(mood.tension)}%`,
      color: '#C58A2B',
    },
    {
      label: '开放度',
      value: normalizePercent(mood.openness),
      display: `${normalizePercent(mood.openness)}%`,
      color: '#4D7892',
    },
    {
      label: '情绪倾向',
      value: normalizeCenteredPercent(mood.valence),
      display: `${normalizeCenteredPercent(mood.valence)}%`,
      color: 'rgb(var(--primary-color))',
    },
  ]
})

onMounted(() => {
  startClockTicker()
  void loadDashboard()
})

onBeforeUnmount(() => {
  if (clockTimer) {
    clearInterval(clockTimer)
    clockTimer = null
  }
})

async function loadDashboard(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    loadError.value = ''
    const results = await Promise.allSettled([
      api<DashboardData>('/api/admin/dashboard'),
      api<DashboardHealth>('/api/admin/health'),
      api<DashboardServicesHealth>('/api/admin/services/health'),
      api<DashboardSlangSummary>('/api/admin/slang/summary'),
    ])

    data.value = results[0].status === 'fulfilled' ? results[0].value : null
    health.value = results[1].status === 'fulfilled' ? results[1].value : null
    servicesHealth.value = results[2].status === 'fulfilled' ? results[2].value : null
    slangSummary.value = results[3].status === 'fulfilled' ? results[3].value : null

    if (results.every(result => result.status === 'rejected')) {
      loadError.value = '后端数据暂不可用，请检查服务是否正常启动。'
    }

    lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch (error) {
    console.error(error)
    loadError.value = '后端数据暂不可用，请检查服务是否正常启动。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function normalizePercent(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value * 100)))
}

function normalizeCenteredPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 50
  return Math.max(0, Math.min(100, Math.round((value + 1) * 50)))
}

function startClockTicker() {
  if (clockTimer) clearInterval(clockTimer)
  clockTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 20_000)
}

function getNowMinute(timestamp: number) {
  const now = new Date(timestamp)
  return now.getHours() * 60 + now.getMinutes()
}

function parseSlotMinute(value: string) {
  const match = value.match(/(\d{1,2})[:：](\d{1,2})/)
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null
  return hour * 60 + minute
}

function isDashboardLogVisible(channel: string | undefined, message: string) {
  const combined = `${channel || ''} ${message || ''}`
  return !/(卡片|memory\s*card|card_id|lookup_cards|update_card|\bcard\b)/i.test(combined)
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

function formatCompactNumber(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return compactFormatter.format(value)
}

function logType(level: string) {
  if (level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  return 'default'
}

function pendingTagType(severity: PendingItem['severity']) {
  if (severity === 'error') return 'error'
  if (severity === 'warning') return 'warning'
  if (severity === 'success') return 'success'
  return 'info'
}

function goTo(route: string) {
  void router.push({ path: route })
}
</script>

<template>
  <AppPage
    title="仪表盘"
    eyebrow="Daily Console"
    description="把今天最重要的运行状态、节奏变化和待处理事项放在一个页面里。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag round size="small" :type="connected ? 'success' : 'error'">
          {{ connected ? '实时在线' : '实时断开' }}
        </NTag>
        <NButton
          v-if="primaryShortcut"
          type="primary"
          secondary
          @click="goTo(primaryShortcut.route)"
        >
          {{ primaryShortcut.label }}
        </NButton>
        <NButton secondary :loading="refreshing" @click="loadDashboard(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <RestartBotButton />
      </NSpace>
    </template>

    <NSpin :show="loading && !data && !health">
      <AppCard bordered elevated class="dashboard-hero">
        <div class="dashboard-hero__main">
          <p class="dashboard-hero__eyebrow">
            Today At A Glance
          </p>
          <h2 class="dashboard-hero__title">
            {{ heroTitle }}
          </h2>
          <p class="dashboard-hero__description">
            {{ heroNarrative }}
          </p>
          <div class="dashboard-hero__chips">
            <NTag round :type="health?.bot === 'running' ? 'success' : 'error'" size="small">
              {{ health?.bot === 'running' ? 'Bot 在线' : 'Bot 待检查' }}
            </NTag>
            <NTag round :type="health?.napcat === 'connected' ? 'success' : 'warning'" size="small">
              {{ health?.napcat === 'connected' ? 'NapCat 正常' : 'NapCat 断开' }}
            </NTag>
            <NTag round size="small">
              {{ lastLoadedAt ? `更新于 ${lastLoadedAt}` : '等待首帧数据' }}
            </NTag>
          </div>
        </div>

        <div class="dashboard-hero__aside">
          <div class="dashboard-hero__aside-card">
            <span class="dashboard-hero__aside-label">今日主题</span>
            <strong class="dashboard-hero__aside-value">
              {{ data?.schedule?.theme || '未生成日程主题' }}
            </strong>
            <span class="dashboard-hero__aside-meta">
              {{ data?.mood?.label || '等待心情画像' }}
            </span>
          </div>
          <div class="dashboard-hero__aside-card">
            <span class="dashboard-hero__aside-label">下一段节奏</span>
            <strong class="dashboard-hero__aside-value">
              {{ nextSlot?.activity || '暂无待展示时段' }}
            </strong>
            <span class="dashboard-hero__aside-meta">
              {{ nextSlot ? `${nextSlot.time}${nextSlot.location ? ` · ${nextSlot.location}` : ''}` : '尚未载入日程' }}
            </span>
          </div>
        </div>
      </AppCard>

      <div class="dashboard-metric-grid">
        <MetricCard
          title="Bot 状态"
          :value="health?.bot === 'running' ? '在线' : '待检查'"
          hint="当前管理端后端主进程"
          :icon="PulseOutline"
          :accent="health?.bot === 'running' ? 'success' : 'warning'"
        />
        <MetricCard
          title="NapCat"
          :value="health?.napcat === 'connected' ? '已连接' : '断开'"
          hint="消息适配层连接状态"
          :icon="GitNetworkOutline"
          :accent="health?.napcat === 'connected' ? 'info' : 'warning'"
        />
        <MetricCard
          title="下一段"
          :value="nextSlot?.time || '--:--'"
          :hint="nextSlot?.activity || '今天还没有后续节奏安排'"
          :icon="TimeOutline"
          accent="primary"
        />
        <MetricCard
          title="当前心情"
          :value="data?.mood?.label || '待生成'"
          :hint="data?.mood ? `能量 ${normalizePercent(data.mood.energy)}% · 张力 ${normalizePercent(data.mood.tension)}%` : '等待情绪引擎返回画像'"
          :icon="SparklesOutline"
          accent="success"
        />
        <MetricCard
          title="待处理事项"
          :value="pendingItems.length"
          :hint="pendingItems.length ? '优先处理黑话审核与连接异常' : '当前没有需要你立刻处理的问题'"
          :icon="AlertCircleOutline"
          :accent="pendingItems.length ? 'warning' : 'info'"
        />
        <MetricCard
          title="今日调用"
          :value="formatCompactNumber(data?.usage?.total_calls)"
          :hint="data?.usage?.total_input_tokens != null ? `输入 ${formatCompactNumber(data?.usage?.total_input_tokens)} · 输出 ${formatCompactNumber(data?.usage?.total_output_tokens)}` : '等待更多用量数据'"
          :icon="FlashOutline"
          accent="info"
        />
      </div>

      <div class="dashboard-priority-grid">
        <AppCard bordered elevated class="dashboard-panel">
          <div class="dashboard-panel__head">
            <div>
              <p class="dashboard-panel__eyebrow">
                Next Actions
              </p>
              <h3 class="dashboard-panel__title">
                待处理事项
              </h3>
            </div>
            <NTag :type="pendingItems.length ? 'warning' : 'success'" size="small" round>
              {{ pendingItems.length ? `${pendingItems.length} 项` : '已清空' }}
            </NTag>
          </div>

          <div v-if="pendingItems.length" class="dashboard-todo-list">
            <div
              v-for="item in pendingItems"
              :key="item.id"
              class="dashboard-todo-item"
            >
              <div class="dashboard-todo-item__main">
                <div class="dashboard-todo-item__head">
                  <NTag size="small" round :type="pendingTagType(item.severity)">
                    {{ item.value }}
                  </NTag>
                  <strong>{{ item.title }}</strong>
                </div>
                <p>{{ item.note }}</p>
              </div>
              <NButton size="small" secondary @click="goTo(item.route)">
                处理
              </NButton>
            </div>
          </div>

          <EmptyState
            v-else
            compact
            title="当前没有待处理事项"
            description="黑话审核、连接状态和关键服务都处于可继续观察的状态。"
            :icon="CheckmarkCircleOutline"
          />
        </AppCard>

        <AppCard bordered elevated class="dashboard-panel">
          <div class="dashboard-panel__head">
            <div>
              <p class="dashboard-panel__eyebrow">
                Critical Stream
              </p>
              <h3 class="dashboard-panel__title">
                最近关键日志
              </h3>
            </div>
            <NTag :type="connected ? 'success' : 'error'" size="small" round>
              {{ connected ? 'SSE 已连接' : 'SSE 断开' }}
            </NTag>
          </div>

          <div v-if="importantLogs.length > 0" class="dashboard-log">
            <div
              v-for="log in importantLogs"
              :key="`${log.ts}-${log.level}-${log.message}`"
              class="dashboard-log__item"
            >
              <div class="dashboard-log__meta">
                <NTag :type="logType(log.level)" size="tiny">
                  {{ log.level }}
                </NTag>
                <span class="dashboard-log__time">{{ log.ts }}</span>
              </div>
              <p class="dashboard-log__message">
                {{ log.message }}
              </p>
            </div>
          </div>

          <EmptyState
            v-else
            compact
            title="关键日志还没有内容"
            :description="connected ? '当前没有新的 warning / error，系统会继续在这里显示最近的重要信号。' : '当前 SSE 未连接，确认后端服务与管理端事件流是否可用。'"
            :icon="PulseOutline"
          />
        </AppCard>
      </div>

      <AppCard bordered elevated class="dashboard-panel dashboard-panel--full">
        <div class="dashboard-panel__head">
          <div>
            <p class="dashboard-panel__eyebrow">
              Today Rhythm
            </p>
            <h3 class="dashboard-panel__title">
              今日状态
            </h3>
          </div>
          <NTag size="small" round>
            {{ scheduleSlots.length }} 段
          </NTag>
        </div>

        <div class="dashboard-rhythm">
          <section class="dashboard-rhythm__group">
            <div class="dashboard-rhythm__section-head">
              <h4>情绪剖面</h4>
              <span>{{ data?.mood?.label || '待生成' }}</span>
            </div>

            <div v-if="moodMeters.length > 0" class="dashboard-meters">
              <div
                v-for="meter in moodMeters"
                :key="meter.label"
                class="dashboard-meter"
              >
                <div class="dashboard-meter__label">
                  <span>{{ meter.label }}</span>
                  <strong>{{ meter.display }}</strong>
                </div>
                <NProgress
                  type="line"
                  :percentage="meter.value"
                  :height="10"
                  :show-indicator="false"
                  :color="meter.color"
                />
              </div>
            </div>

            <EmptyState
              v-else
              compact
              title="尚无情绪画像"
              description="当前没有 mood engine 返回的可展示结果。"
              :icon="SparklesOutline"
            />
          </section>

          <section class="dashboard-rhythm__group">
            <div class="dashboard-rhythm__section-head">
              <h4>日程预览</h4>
              <span>{{ data?.schedule?.theme || '未生成日程' }}</span>
            </div>

            <div v-if="scheduleTimelineSlots.length > 0" class="dashboard-slots">
              <div
                v-for="slot in scheduleTimelineSlots"
                :key="`${slot.time}-${slot.activity}`"
                :class="['dashboard-slot', { 'dashboard-slot--past': slot.isPast, 'dashboard-slot--lead': slot.isCurrentLead }]"
              >
                <div class="dashboard-slot__time">
                  {{ slot.time || '--:--' }}
                </div>
                <div class="dashboard-slot__body">
                  <p class="dashboard-slot__activity">
                    {{ slot.activity || '未命名活动' }}
                  </p>
                  <p class="dashboard-slot__meta">
                    {{ slot.location || slot.mood_hint || '无位置或心情提示' }}
                  </p>
                </div>
              </div>
            </div>

            <EmptyState
              v-else
              compact
              title="今天还没有节奏安排"
              description="日程生成后，这里会按当前时间展示全量时间段。"
              :icon="TimeOutline"
            />
          </section>
        </div>
      </AppCard>
    </NSpin>
  </AppPage>
</template>

<style scoped>
.dashboard-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.9fr);
  gap: 18px;
  overflow: hidden;
  margin-bottom: 24px;
  padding: 24px;
  border-radius: 24px;
  background: var(--om-hero-gradient);
}

.dashboard-hero__main {
  position: relative;
  z-index: 1;
}

.dashboard-hero__eyebrow {
  margin: 0 0 10px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.dashboard-hero__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: clamp(26px, 3.2vw, 38px);
  line-height: 1.08;
  letter-spacing: -0.04em;
}

.dashboard-hero__description {
  margin: 14px 0 0;
  max-width: 760px;
  color: var(--om-text-2);
  font-size: 15px;
  line-height: 1.8;
}

.dashboard-hero__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.dashboard-hero__aside {
  display: grid;
  gap: 14px;
}

.dashboard-hero__aside-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 116px;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.42);
}

.dark .dashboard-hero__aside-card {
  background: rgba(18, 29, 34, 0.48);
}

.dashboard-hero__aside-label {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.dashboard-hero__aside-value {
  margin-top: 10px;
  color: var(--om-text-1);
  font-size: 18px;
  line-height: 1.4;
}

.dashboard-hero__aside-meta {
  margin-top: 8px;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.dashboard-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.dashboard-priority-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.dashboard-panel {
  min-height: 100%;
  padding: 20px;
}

.dashboard-panel--full {
  margin-top: 0;
}

.dashboard-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.dashboard-panel__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.dashboard-panel__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.dashboard-todo-list,
.dashboard-log {
  display: grid;
  gap: 10px;
}

.dashboard-todo-item,
.dashboard-log__item {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.dashboard-todo-item {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}

.dashboard-todo-item__main {
  display: grid;
  gap: 8px;
}

.dashboard-todo-item__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.dashboard-todo-item__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.dashboard-todo-item p {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.dashboard-log__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dashboard-log__time {
  color: var(--om-text-3);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
}

.dashboard-log__message {
  margin: 10px 0 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.dashboard-rhythm {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 18px;
}

.dashboard-rhythm__group {
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: var(--om-surface-2);
}

.dashboard-rhythm__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.dashboard-rhythm__section-head h4 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.dashboard-rhythm__section-head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.dashboard-meters {
  display: grid;
  gap: 14px;
}

.dashboard-meter__label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--om-text-2);
  font-size: 12px;
}

.dashboard-meter__label strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.dashboard-slots {
  display: grid;
  gap: 12px;
}

.dashboard-slot {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding: 14px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 78%, transparent);
}

.dashboard-slot--past {
  opacity: 0.62;
}

.dashboard-slot--lead {
  border: 1px solid color-mix(in srgb, rgb(var(--primary-color)) 20%, var(--om-border));
}

.dashboard-slot--past .dashboard-slot__time {
  color: var(--om-text-3);
}

.dashboard-slot__time {
  color: rgb(var(--primary-color));
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.dashboard-slot__activity {
  margin: 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.dashboard-slot__meta {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .dashboard-hero,
  .dashboard-priority-grid,
  .dashboard-rhythm,
  .dashboard-metric-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 780px) {
  .dashboard-hero {
    padding: 20px;
  }

  .dashboard-todo-item,
  .dashboard-slot {
    grid-template-columns: 1fr;
  }
}
</style>

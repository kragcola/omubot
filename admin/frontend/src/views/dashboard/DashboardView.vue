<script setup lang="ts">
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  ChatbubbleEllipsesOutline,
  FlashOutline,
  HappyOutline,
  ImageOutline,
  PeopleOutline,
  RefreshOutline,
  SparklesOutline,
  TerminalOutline,
  TimeOutline,
  TrendingUpOutline,
} from '@vicons/ionicons5'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import LogPanel from '../../components/common/LogPanel.vue'
import type { LogPanelLine } from '../../components/common/LogPanel.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import SparklineChart from '../../components/common/SparklineChart.vue'
import StateBadge from '../../components/common/StateBadge.vue'
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

interface DashboardStyleSummary {
  total: number
  pending: number
  approved: number
  rejected: number
  risk_count?: number
  feedback_count?: number
  profile_count?: number
  enabled_profile_count?: number
}

interface UsageBucket {
  bucket?: string
  calls?: number
  total_input?: number
  total_output?: number
}

interface UsageTopGroup {
  group_id: string
  calls: number
  total_input?: number
  total_output?: number
}

interface UsageDataResponse {
  timeseries: UsageBucket[]
  summary: Record<string, unknown>
  top_users: unknown[]
  top_groups: UsageTopGroup[]
  by_model: unknown[]
}

interface LearningLatestItem {
  id?: string
  title: string
  subtitle: string
  time: string
  status?: string
}

interface LearningSlang {
  approved_today: number
  reviewed_today: number
  pending: number
  today_hits: number
  latest: LearningLatestItem[]
  error?: string
}

interface LearningStyle {
  approved_today: number
  reviewed_today: number
  pending: number
  latest: LearningLatestItem[]
  error?: string
}

interface LearningStickers {
  added_today: number
  total: number
  latest: LearningLatestItem[]
  samples: string[]
  error?: string
}

interface LearningTodayResponse {
  as_of: string
  total_new: number
  total_reviewed: number
  slang: LearningSlang
  style: LearningStyle
  stickers: LearningStickers
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
const styleSummary = ref<DashboardStyleSummary | null>(null)
const usageData = ref<UsageDataResponse | null>(null)
const learningToday = ref<LearningTodayResponse | null>(null)
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

const logLines = computed<LogPanelLine[]>(() => {
  const priority = visibleLogs.value.filter(entry => entry.level === 'ERROR' || entry.level === 'WARNING')
  const source = priority.length > 0 ? priority : visibleLogs.value
  return source.slice(-30).map((entry, idx) => ({
    id: `${entry.ts}-${idx}`,
    timestamp: entry.ts,
    channel: entry.channel || '',
    text: entry.message,
    level: logLevel(entry.level),
  }))
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

  // 先按时间顺序排列（自然顺序展示一整天），同时标记哪个是当前/下一段
  const nextLeadIdx = normalized.findIndex(s => s._timelineMinute >= currentMinute)
  const leadIdx = nextLeadIdx === -1 ? normalized.length - 1 : nextLeadIdx

  return normalized.map((slot, idx) => ({
    ...slot,
    isPast: slot._timelineMinute < currentMinute,
    isCurrentLead: idx === leadIdx,
  }))
})

const nextSlot = computed(() => scheduleTimelineSlots.value.find(s => !s.isPast) ?? null)
const maintenanceWindow = computed(() => servicesHealth.value?.maintenance_window || null)
const healthAlerts = computed(() => servicesHealth.value?.alerts || [])

const usageHourlyBuckets = computed(() => {
  const rows = usageData.value?.timeseries ?? []
  const map = new Map<string, number>()
  for (const row of rows) {
    const bucket = String(row.bucket ?? '')
    const calls = Number(row.calls ?? 0)
    map.set(bucket, (map.get(bucket) ?? 0) + calls)
  }
  const buckets: Array<{ hour: string, calls: number }> = []
  for (let h = 0; h < 24; h++) {
    const key = String(h).padStart(2, '0')
    buckets.push({ hour: key, calls: map.get(key) ?? 0 })
  }
  return buckets
})

const usageTopGroups = computed(() => {
  const rows = usageData.value?.top_groups ?? []
  const max = rows.reduce((m, r) => Math.max(m, Number(r.total_input ?? 0) + Number(r.total_output ?? 0)), 0)
  return rows.slice(0, 5).map((row) => {
    const tokens = Number(row.total_input ?? 0) + Number(row.total_output ?? 0)
    return {
      group_id: row.group_id,
      calls: Number(row.calls ?? 0),
      tokens,
      percent: max > 0 ? Math.round((tokens / max) * 100) : 0,
    }
  })
})

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
      note: '已通过 AI 初审，等待人工确认或否决。',
      route: '/slang',
      severity: 'info',
    })
  }

  if ((styleSummary.value?.pending || 0) > 0) {
    items.push({
      id: 'style-pending',
      title: '表达学习待审',
      value: `${styleSummary.value?.pending || 0} 条`,
      note: '新提取的表达片段等待人工核对是否入库。',
      route: '/style',
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

  return items.slice(0, 5)
})

const primaryShortcut = computed(() => {
  if ((slangSummary.value?.candidate_count || 0) > 0 || (slangSummary.value?.ai_pending_review_count || 0) > 0) {
    return { label: '去处理黑话', route: '/slang' }
  }
  if ((styleSummary.value?.pending || 0) > 0) {
    return { label: '去处理风格', route: '/style' }
  }
  if (pendingItems.value.some(item => item.route === '/system')) {
    return { label: '查看系统异常', route: '/system' }
  }
  return null
})

const heroTitle = computed(() => {
  if (loadError.value) return '控制台暂时无法同步运行信息'
  if (health.value?.bot === 'running' && health.value?.napcat === 'connected') {
    if (pendingItems.value.length > 0) return `一切在线，有 ${pendingItems.value.length} 件事等你处理`
    return '今天运行状态稳定，一切在线'
  }
  if (health.value?.bot === 'running') return 'Bot 在线，但连接或服务需要留意'
  return '当前运行状态需要人工检查'
})

const heroNarrative = computed(() => {
  if (loadError.value) return loadError.value
  if (pendingItems.value.length > 0) {
    return `优先处理待办清单里的 ${pendingItems.value.length} 项事务。其他信号正在持续监测，实时日志可在下方面板查看。`
  }
  const narrative = data.value?.schedule?.day_narrative?.trim()
  if (narrative) return narrative
  return '今天没有明显待办，仪表盘会持续展示运行状态、节奏变化和关键日志。'
})

const moodChips = computed(() => {
  const mood = data.value?.mood
  if (!mood) return []
  return [
    { label: '能量', value: normalizePercent(mood.energy), tone: 'success' as const },
    { label: '张力', value: normalizePercent(mood.tension), tone: 'warning' as const },
    { label: '开放度', value: normalizePercent(mood.openness), tone: 'info' as const },
    { label: '倾向', value: normalizeCenteredPercent(mood.valence), tone: 'primary' as const },
  ]
})

const statusBadges = computed(() => {
  const badges: Array<{ status: 'success' | 'warning' | 'error' | 'info', label: string }> = []
  badges.push({
    status: health.value?.bot === 'running' ? 'success' : 'error',
    label: health.value?.bot === 'running' ? 'Bot 在线' : 'Bot 待检查',
  })
  badges.push({
    status: health.value?.napcat === 'connected' ? 'success' : 'warning',
    label: health.value?.napcat === 'connected' ? 'NapCat 正常' : 'NapCat 断开',
  })
  badges.push({
    status: connected.value ? 'success' : 'warning',
    label: connected.value ? 'SSE 实时' : 'SSE 断开',
  })
  if (lastLoadedAt.value) {
    badges.push({ status: 'info', label: `更新 ${lastLoadedAt.value}` })
  }
  return badges
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
      api<DashboardStyleSummary>('/api/admin/style/summary'),
      api<UsageDataResponse>('/admin/usage/data?period=day'),
      api<LearningTodayResponse>('/api/admin/learning/today'),
    ])

    data.value = results[0].status === 'fulfilled' ? results[0].value : null
    health.value = results[1].status === 'fulfilled' ? results[1].value : null
    servicesHealth.value = results[2].status === 'fulfilled' ? results[2].value : null
    slangSummary.value = results[3].status === 'fulfilled' ? results[3].value : null
    styleSummary.value = results[4].status === 'fulfilled' ? results[4].value : null
    usageData.value = results[5].status === 'fulfilled' ? results[5].value : null
    learningToday.value = results[6].status === 'fulfilled' ? results[6].value : null

    if (results.slice(0, 2).every(result => result.status === 'rejected')) {
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

function formatCompactNumber(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return compactFormatter.format(value)
}

function formatGroupId(id: string) {
  if (!id) return '--'
  if (id.length <= 6) return id
  return `${id.slice(0, 3)}…${id.slice(-3)}`
}

function logLevel(level: string): LogPanelLine['level'] {
  if (level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  if (level === 'SUCCESS') return 'success'
  if (level === 'DEBUG') return 'debug'
  return 'info'
}

function pendingStatus(severity: PendingItem['severity']): 'success' | 'warning' | 'error' | 'info' {
  if (severity === 'error') return 'error'
  if (severity === 'warning') return 'warning'
  if (severity === 'success') return 'success'
  return 'info'
}

function moodToneColor(tone: 'success' | 'warning' | 'info' | 'primary') {
  if (tone === 'success') return 'var(--om-success)'
  if (tone === 'warning') return 'var(--om-warning)'
  if (tone === 'info') return 'var(--om-info)'
  return 'rgb(var(--primary-color))'
}

function goTo(route: string) {
  void router.push({ path: route })
}
</script>

<template>
  <AppPage
    title="仪表盘"
    eyebrow="Daily Console"
    description="运行状态、节奏变化、学习信号和关键日志，一眼看完。"
  >
    <template #action>
      <NSpace align="center" :size="10">
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
      <div class="dash-layout">
        <!-- ================== Main column ================== -->
        <div class="dash-main">
          <!-- Hero -->
          <AppCard bordered elevated class="dash-hero">
            <div class="dash-hero__badges">
              <StateBadge
                v-for="badge in statusBadges"
                :key="badge.label"
                :status="badge.status"
                :label="badge.label"
                compact
              />
            </div>
            <h2 class="dash-hero__title">
              {{ heroTitle }}
            </h2>
            <p class="dash-hero__desc">
              {{ heroNarrative }}
            </p>

            <div class="dash-hero__kpi">
              <div class="dash-hero-kpi">
                <span class="dash-hero-kpi__label">
                  <NIcon :component="FlashOutline" :size="12" />
                  今日调用
                </span>
                <strong class="dash-hero-kpi__value">{{ formatCompactNumber(data?.usage?.total_calls) }}</strong>
                <span class="dash-hero-kpi__hint">
                  输入 {{ formatCompactNumber(data?.usage?.total_input_tokens) }} · 输出 {{ formatCompactNumber(data?.usage?.total_output_tokens) }}
                </span>
              </div>
              <div class="dash-hero-kpi">
                <span class="dash-hero-kpi__label">
                  <NIcon :component="PeopleOutline" :size="12" />
                  活跃群
                </span>
                <strong class="dash-hero-kpi__value">{{ usageTopGroups.length }}</strong>
                <span class="dash-hero-kpi__hint">
                  近 7 天产生调用的群
                </span>
              </div>
              <div class="dash-hero-kpi" :class="{ 'dash-hero-kpi--alert': pendingItems.length > 0 }">
                <span class="dash-hero-kpi__label">
                  <NIcon :component="AlertCircleOutline" :size="12" />
                  待处理
                </span>
                <strong class="dash-hero-kpi__value">{{ pendingItems.length }}</strong>
                <span class="dash-hero-kpi__hint">
                  {{ pendingItems.length ? '清单见下方' : '全部已清空' }}
                </span>
              </div>
            </div>
          </AppCard>

          <!-- 24h usage curve -->
          <AppPanelSection class="mt-16" eyebrow="USAGE" title="24 小时调用曲线">
            <template #aside>
              <StateBadge
                status="default"
                :label="`共 ${usageHourlyBuckets.reduce((a, b) => a + b.calls, 0)} 次`"
                compact
              />
            </template>
            <SparklineChart
              :values="usageHourlyBuckets.map(b => b.calls)"
              :labels="usageHourlyBuckets.map(b => b.hour)"
              :height="120"
              color="rgb(var(--primary-color))"
            />
          </AppPanelSection>

          <!-- Row: top groups + pending -->
          <div class="dash-grid-2 mt-16">
            <AppPanelSection eyebrow="TOP GROUPS" title="近 7 天活跃群">
              <template #aside>
                <NButton size="tiny" text @click="goTo('/groups')">
                  查看全部
                </NButton>
              </template>
              <div v-if="usageTopGroups.length" class="dash-top-groups">
                <div
                  v-for="(g, idx) in usageTopGroups"
                  :key="g.group_id"
                  class="dash-top-groups__row"
                  @click="goTo('/groups')"
                >
                  <span class="dash-top-groups__rank">#{{ idx + 1 }}</span>
                  <span class="dash-top-groups__id">{{ formatGroupId(g.group_id) }}</span>
                  <span class="dash-top-groups__bar">
                    <span class="dash-top-groups__fill" :style="{ width: `${g.percent}%` }" />
                  </span>
                  <span class="dash-top-groups__count">{{ g.calls }} 次</span>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="暂无群调用数据"
                description="近 7 天内尚未有群产生 LLM 调用。"
                :icon="PeopleOutline"
              />
            </AppPanelSection>

            <AppPanelSection eyebrow="TODO" title="待处理与学习信号">
              <template #aside>
                <StateBadge
                  :status="pendingItems.length ? 'warning' : 'success'"
                  :label="pendingItems.length ? `${pendingItems.length} 项` : '已清空'"
                  compact
                />
              </template>
              <div v-if="pendingItems.length" class="dash-todo">
                <div
                  v-for="item in pendingItems"
                  :key="item.id"
                  class="dash-todo__item"
                  @click="goTo(item.route)"
                >
                  <StateBadge :status="pendingStatus(item.severity)" :label="item.value" compact />
                  <div class="dash-todo__body">
                    <strong>{{ item.title }}</strong>
                    <p>{{ item.note }}</p>
                  </div>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="没有待处理事项"
                description="黑话、风格与系统运行都处于可观察状态。"
                :icon="CheckmarkCircleOutline"
              />

              <div v-if="slangSummary || styleSummary" class="dash-learning">
                <div v-if="slangSummary" class="dash-learning__row">
                  <NIcon :component="TrendingUpOutline" :size="14" />
                  <span class="dash-learning__label">黑话</span>
                  <span class="dash-learning__metric">今日触达 <strong>{{ slangSummary.today_hits || 0 }}</strong></span>
                  <span class="dash-learning__metric">已入库 <strong>{{ slangSummary.approved_count || 0 }}</strong></span>
                </div>
                <div v-if="styleSummary" class="dash-learning__row">
                  <NIcon :component="SparklesOutline" :size="14" />
                  <span class="dash-learning__label">风格</span>
                  <span class="dash-learning__metric">已入库 <strong>{{ styleSummary.approved || 0 }}</strong></span>
                  <span class="dash-learning__metric">启用画像 <strong>{{ styleSummary.enabled_profile_count || 0 }}</strong> / {{ styleSummary.profile_count || 0 }}</span>
                </div>
              </div>
            </AppPanelSection>
          </div>

          <!-- Today learning — 3 columns: slang / style / stickers -->
          <AppPanelSection
            class="mt-16"
            eyebrow="TODAY LEARNING"
            title="今日学习收录"
            :description="`今日新入库 ${learningToday?.total_new ?? 0} 条 · 已审核 ${learningToday?.total_reviewed ?? 0} 条`"
          >
            <template #aside>
              <StateBadge
                :status="(learningToday?.total_new ?? 0) > 0 ? 'success' : 'default'"
                :label="`+${learningToday?.total_new ?? 0}`"
                compact
              />
            </template>
            <div class="dash-learn-grid">
              <!-- Slang card -->
              <div class="dash-learn" @click="goTo('/slang')">
                <div class="dash-learn__head">
                  <span class="dash-learn__icon" style="--tone: var(--om-warning)">
                    <NIcon :component="ChatbubbleEllipsesOutline" :size="16" />
                  </span>
                  <div class="dash-learn__head-main">
                    <div class="dash-learn__label">黑话</div>
                    <div class="dash-learn__numbers">
                      <strong>{{ learningToday?.slang.approved_today ?? 0 }}</strong>
                      <span>新入库</span>
                    </div>
                  </div>
                </div>
                <div class="dash-learn__meta">
                  <span>今审 {{ learningToday?.slang.reviewed_today ?? 0 }}</span>
                  <span>·</span>
                  <span>命中 {{ learningToday?.slang.today_hits ?? 0 }}</span>
                  <span>·</span>
                  <span class="dash-learn__pending">
                    待审 {{ learningToday?.slang.pending ?? 0 }}
                  </span>
                </div>
                <ul v-if="learningToday?.slang.latest.length" class="dash-learn__list">
                  <li
                    v-for="(item, idx) in learningToday.slang.latest"
                    :key="`slang-${idx}`"
                  >
                    <span class="dash-learn__time">{{ item.time }}</span>
                    <span class="dash-learn__title-text">{{ item.title }}</span>
                    <span v-if="item.subtitle" class="dash-learn__subtitle">{{ item.subtitle }}</span>
                  </li>
                </ul>
                <div v-else class="dash-learn__empty">
                  今天还没有新入库
                </div>
              </div>

              <!-- Style card -->
              <div class="dash-learn" @click="goTo('/style')">
                <div class="dash-learn__head">
                  <span class="dash-learn__icon" style="--tone: var(--om-info)">
                    <NIcon :component="SparklesOutline" :size="16" />
                  </span>
                  <div class="dash-learn__head-main">
                    <div class="dash-learn__label">表达风格</div>
                    <div class="dash-learn__numbers">
                      <strong>{{ learningToday?.style.approved_today ?? 0 }}</strong>
                      <span>新入库</span>
                    </div>
                  </div>
                </div>
                <div class="dash-learn__meta">
                  <span>今审 {{ learningToday?.style.reviewed_today ?? 0 }}</span>
                  <span>·</span>
                  <span class="dash-learn__pending">
                    待审 {{ learningToday?.style.pending ?? 0 }}
                  </span>
                </div>
                <ul v-if="learningToday?.style.latest.length" class="dash-learn__list">
                  <li
                    v-for="(item, idx) in learningToday.style.latest"
                    :key="`style-${idx}`"
                  >
                    <span class="dash-learn__time">{{ item.time }}</span>
                    <span class="dash-learn__title-text">{{ item.title }}</span>
                    <span v-if="item.subtitle" class="dash-learn__subtitle">{{ item.subtitle }}</span>
                  </li>
                </ul>
                <div v-else class="dash-learn__empty">
                  今天还没有新入库
                </div>
              </div>

              <!-- Stickers card -->
              <div class="dash-learn" @click="goTo('/stickers')">
                <div class="dash-learn__head">
                  <span class="dash-learn__icon" style="--tone: var(--om-success)">
                    <NIcon :component="HappyOutline" :size="16" />
                  </span>
                  <div class="dash-learn__head-main">
                    <div class="dash-learn__label">表情包</div>
                    <div class="dash-learn__numbers">
                      <strong>{{ learningToday?.stickers.added_today ?? 0 }}</strong>
                      <span>新入库</span>
                    </div>
                  </div>
                </div>
                <div class="dash-learn__meta">
                  <span>总库 {{ learningToday?.stickers.total ?? 0 }}</span>
                </div>
                <ul v-if="learningToday?.stickers.latest.length" class="dash-learn__list dash-learn__list--sticker">
                  <li
                    v-for="(item, idx) in learningToday.stickers.latest"
                    :key="`sticker-${idx}`"
                  >
                    <span class="dash-learn__time">{{ item.time }}</span>
                    <img
                      v-if="item.id"
                      class="dash-learn__thumb"
                      :src="`/api/admin/stickers/${item.id}/image`"
                      :alt="item.title"
                      loading="lazy"
                    >
                    <span class="dash-learn__title-text">{{ item.title }}</span>
                  </li>
                </ul>
                <div v-else class="dash-learn__empty">
                  <NIcon :component="ImageOutline" :size="22" />
                  <span>今天还没有新入库</span>
                </div>
              </div>
            </div>
          </AppPanelSection>

          <!-- Log panel -->
          <LogPanel
            class="mt-16"
            :lines="logLines"
            :title="connected ? '关键日志 · 实时' : '关键日志 · SSE 断开'"
            subtitle="自动保留最近 30 条；有 warning / error 时优先显示异常信号"
            :icon="TerminalOutline"
            :height="320"
            empty="暂无关键日志"
          >
            <template #actions>
              <NButton size="tiny" @click="goTo('/logs')">
                去日志页
              </NButton>
            </template>
          </LogPanel>
        </div>

        <!-- ================== Right sticky aside ================== -->
        <aside class="dash-aside">
          <!-- Theme + mood -->
          <AppCard bordered elevated class="dash-aside__card">
            <p class="dash-aside__eyebrow">
              Today
            </p>
            <h3 class="dash-aside__theme">
              {{ data?.schedule?.theme || '今日主题' }}
            </h3>
            <p class="dash-aside__mood-label">
              <NIcon :component="SparklesOutline" :size="13" />
              <span>{{ data?.mood?.label || '等待心情画像' }}</span>
            </p>
            <div v-if="moodChips.length" class="dash-mood">
              <div
                v-for="chip in moodChips"
                :key="chip.label"
                class="dash-mood__chip"
              >
                <span class="dash-mood__label">{{ chip.label }}</span>
                <span
                  class="dash-mood__bar"
                  :style="{ '--chip-color': moodToneColor(chip.tone) } as any"
                >
                  <span class="dash-mood__fill" :style="{ width: `${chip.value}%`, background: moodToneColor(chip.tone) }" />
                </span>
                <span class="dash-mood__value">{{ chip.value }}%</span>
              </div>
            </div>
            <div v-if="nextSlot" class="dash-aside__next">
              <span class="dash-aside__next-label">下一段</span>
              <strong>{{ nextSlot.time }}</strong>
              <span class="dash-aside__next-activity">{{ nextSlot.activity }}</span>
            </div>
          </AppCard>

          <!-- Vertical rhythm timeline -->
          <AppCard bordered elevated class="dash-aside__card dash-aside__timeline-card">
            <div class="dash-aside__head">
              <p class="dash-aside__eyebrow">
                Rhythm
              </p>
              <h3 class="dash-aside__title">
                今日节奏
              </h3>
              <p v-if="data?.schedule?.day_narrative" class="dash-aside__narrative">
                {{ data.schedule.day_narrative }}
              </p>
            </div>

            <div v-if="scheduleTimelineSlots.length" class="dash-timeline">
              <div
                v-for="slot in scheduleTimelineSlots"
                :key="`${slot.time}-${slot.activity}`"
                class="dash-timeline__slot"
                :class="{
                  'dash-timeline__slot--past': slot.isPast,
                  'dash-timeline__slot--lead': slot.isCurrentLead,
                }"
              >
                <div class="dash-timeline__marker">
                  <span class="dash-timeline__dot" />
                </div>
                <div class="dash-timeline__body">
                  <div class="dash-timeline__time">
                    {{ slot.time || '--:--' }}
                  </div>
                  <div class="dash-timeline__activity">
                    {{ slot.activity || '未命名活动' }}
                  </div>
                  <div v-if="slot.location || slot.mood_hint" class="dash-timeline__meta">
                    <template v-if="slot.location">
                      {{ slot.location }}
                    </template>
                    <template v-if="slot.location && slot.mood_hint">
                      ·
                    </template>
                    <template v-if="slot.mood_hint">
                      {{ slot.mood_hint }}
                    </template>
                  </div>
                </div>
              </div>
            </div>

            <EmptyState
              v-else
              compact
              title="今天还没有节奏安排"
              description="日程生成后，这里会按当前时间展示全量时段。"
              :icon="TimeOutline"
            />
          </AppCard>
        </aside>
      </div>
    </NSpin>
  </AppPage>
</template>

<style scoped>
.dash-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.dash-main {
  min-width: 0;
}

.dash-aside {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dash-hero {
  padding: 24px 28px;
  background: var(--om-hero-gradient);
  border-radius: 18px;
}

.dash-hero__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.dash-hero__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: clamp(22px, 2.4vw, 30px);
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.25;
}

.dash-hero__desc {
  margin: 12px 0 0;
  max-width: 720px;
  color: var(--om-text-2);
  font-size: 14px;
  line-height: 1.75;
}

.dash-hero__kpi {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 20px;
}

.dash-hero-kpi {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  padding: 12px 16px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
}

.dark .dash-hero-kpi {
  background: color-mix(in srgb, var(--om-surface-solid) 60%, transparent);
}

.dash-hero-kpi--alert {
  border-color: color-mix(in srgb, var(--om-warning) 30%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 6%, transparent);
}

.dash-hero-kpi__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.dash-hero-kpi__value {
  color: var(--om-text-1);
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.dash-hero-kpi__hint {
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.dash-grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.dash-top-groups {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dash-top-groups__row {
  display: grid;
  grid-template-columns: 28px 68px 1fr 60px;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.16s ease;
}

.dash-top-groups__row:hover {
  background: var(--om-surface-2);
}

.dash-top-groups__rank {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.dash-top-groups__id {
  color: var(--om-text-1);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.dash-top-groups__bar {
  position: relative;
  height: 6px;
  border-radius: 999px;
  background: var(--om-surface-2);
  overflow: hidden;
}

.dash-top-groups__fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, rgb(var(--primary-color)), color-mix(in srgb, rgb(var(--primary-color)) 60%, transparent));
  transition: width 0.24s ease;
}

.dash-top-groups__count {
  color: var(--om-text-2);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.dash-todo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dash-todo__item {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 12px;
  align-items: start;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease;
}

.dash-todo__item:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 80%, var(--om-surface-3));
}

.dash-todo__body strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.dash-todo__body p {
  margin: 4px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}

.dash-learning {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--om-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dash-learning__row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--om-text-2);
  font-size: 12px;
}

.dash-learning__label {
  min-width: 30px;
  color: var(--om-text-3);
  font-weight: 600;
  letter-spacing: 0.06em;
}

.dash-learning__metric {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.dash-learning__metric strong {
  color: var(--om-text-1);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

/* ============ Today learning grid ============ */
.dash-learn-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.dash-learn {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease, transform 0.16s ease;
}

.dash-learn:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 80%, var(--om-surface-3));
  transform: translateY(-1px);
}

.dash-learn__head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dash-learn__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  color: var(--tone, var(--om-text-2));
  background: color-mix(in srgb, var(--tone, var(--om-text-2)) 12%, transparent);
}

.dash-learn__head-main {
  min-width: 0;
  flex: 1;
}

.dash-learn__label {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.dash-learn__numbers {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 2px;
}

.dash-learn__numbers strong {
  color: var(--om-text-1);
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  line-height: 1;
}

.dash-learn__numbers span {
  color: var(--om-text-3);
  font-size: 12px;
}

.dash-learn__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: var(--om-text-2);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.dash-learn__meta > span:not(.dash-learn__pending) {
  color: var(--om-text-3);
}

.dash-learn__pending {
  color: var(--om-warning);
  font-weight: 600;
}

.dash-learn__list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  border-top: 1px dashed var(--om-border);
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.dash-learn__list li {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  column-gap: 8px;
  row-gap: 2px;
  align-items: baseline;
}

.dash-learn__list--sticker li {
  grid-template-columns: 38px 28px minmax(0, 1fr);
}

.dash-learn__time {
  color: var(--om-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.dash-learn__title-text {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.35;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  text-overflow: ellipsis;
}

.dash-learn__subtitle {
  grid-column: 2 / -1;
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  text-overflow: ellipsis;
}

.dash-learn__thumb {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  object-fit: cover;
  border: 1px solid var(--om-border);
  background: var(--om-surface-solid);
}

.dash-learn__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 18px 8px;
  color: var(--om-text-3);
  font-size: 12px;
  border: 1px dashed var(--om-border);
  border-radius: 10px;
  margin-top: 4px;
}

@media (max-width: 1200px) {
  .dash-learn-grid {
    grid-template-columns: 1fr;
  }
}

/* ============ Right aside styles ============ */
.dash-aside__card {
  padding: 18px;
  border-radius: 16px;
}

.dash-aside__eyebrow {
  margin: 0 0 6px;
  color: var(--om-text-3);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.dash-aside__theme {
  margin: 0;
  color: var(--om-text-1);
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
}

.dash-aside__mood-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
}

.dash-aside__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 17px;
  font-weight: 700;
}

.dash-aside__narrative {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.65;
}

.dash-aside__head {
  padding-bottom: 14px;
  border-bottom: 1px dashed var(--om-border);
  margin-bottom: 14px;
}

.dash-aside__next {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--om-border);
  font-size: 13px;
}

.dash-aside__next-label {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.dash-aside__next strong {
  color: rgb(var(--primary-color));
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
}

.dash-aside__next-activity {
  min-width: 0;
  flex: 1;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dash-mood {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}

.dash-mood__chip {
  display: grid;
  grid-template-columns: 52px 1fr 40px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.dash-mood__label {
  color: var(--om-text-2);
  font-size: 12px;
}

.dash-mood__value {
  color: var(--om-text-1);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.dash-mood__bar {
  position: relative;
  height: 6px;
  border-radius: 999px;
  background: var(--om-surface-3);
  overflow: hidden;
}

.dash-mood__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.24s ease;
}

/* ============ Vertical timeline ============ */
.dash-aside__timeline-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.dash-timeline {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
  scrollbar-width: thin;
}

.dash-timeline::-webkit-scrollbar {
  width: 4px;
}

.dash-timeline::-webkit-scrollbar-thumb {
  background: var(--om-border);
  border-radius: 999px;
}

.dash-timeline__slot {
  position: relative;
  display: grid;
  grid-template-columns: 20px 1fr;
  gap: 10px;
  padding: 10px 0;
}

.dash-timeline__slot:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 9px;
  top: 22px;
  bottom: -4px;
  width: 1px;
  background: var(--om-border);
}

.dash-timeline__marker {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 4px;
}

.dash-timeline__dot {
  width: 9px;
  height: 9px;
  border-radius: 999px;
  background: var(--om-surface-3);
  border: 2px solid var(--om-surface-2);
  box-shadow: 0 0 0 1px var(--om-border);
  z-index: 1;
}

.dash-timeline__slot--past .dash-timeline__dot {
  background: var(--om-text-3);
  opacity: 0.5;
}

.dash-timeline__slot--lead .dash-timeline__dot {
  background: rgb(var(--primary-color));
  box-shadow: 0 0 0 3px color-mix(in srgb, rgb(var(--primary-color)) 20%, transparent);
  width: 11px;
  height: 11px;
  border-color: var(--om-surface-solid);
}

.dash-timeline__body {
  min-width: 0;
}

.dash-timeline__time {
  color: rgb(var(--primary-color));
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.4;
}

.dash-timeline__slot--past .dash-timeline__time {
  color: var(--om-text-3);
}

.dash-timeline__activity {
  margin-top: 2px;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.45;
}

.dash-timeline__slot--past .dash-timeline__activity {
  color: var(--om-text-2);
  font-weight: 500;
}

.dash-timeline__meta {
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 11px;
  line-height: 1.5;
}

.dash-timeline__slot--past .dash-timeline__meta {
  color: var(--om-text-3);
}

@media (max-width: 1200px) {
  .dash-layout {
    grid-template-columns: 1fr;
  }
  .dash-aside {
    position: static;
  }
  .dash-grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>

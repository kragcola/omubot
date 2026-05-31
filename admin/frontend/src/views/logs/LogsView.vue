<script setup lang="ts">
import {
  ChevronDownOutline,
  CloseOutline,
  DocumentTextOutline,
  MoonOutline,
  PauseOutline,
  PlayOutline,
  PulseOutline,
  RefreshOutline,
  SearchOutline,
  TerminalOutline,
  TrashOutline,
} from '@vicons/ionicons5'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import LogPanel from '../../components/common/LogPanel.vue'
import type { LogPanelLine } from '../../components/common/LogPanel.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import { useSSE, type SSELogEntry } from '../../composables/useSSE'
import { onRovingKeydown } from '../../utils/a11y'

/** Level filter state.
 *  `default` = hide DEBUG but show everything else (UX choice for noise reduction).
 *  Specific levels match exactly.
 */
type LevelFilter = 'default' | 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG'

const levelSegments: Array<{ key: LevelFilter, label: string, hint?: string }> = [
  { key: 'default', label: '默认', hint: '隐藏 DEBUG' },
  { key: 'ERROR', label: 'ERROR' },
  { key: 'WARNING', label: 'WARNING' },
  { key: 'INFO', label: 'INFO' },
  { key: 'DEBUG', label: 'DEBUG' },
]

interface ChannelOption { key: string, label: string }
// key = the actual loguru `bind(channel=...)` value carried over SSE (events.py:206);
// label = the Chinese display name (mirrors bot.py _CHANNEL_LABELS). Filtering matches
// on the English key, NOT the label — earlier the chips used Chinese keys that never
// matched any SSE channel, so channel filtering (and observe mode) silently showed nothing.
const channelOptions: ChannelOption[] = [
  { key: 'scheduler', label: '调度' },
  { key: 'schedule', label: '日程' },
  { key: 'affection', label: '好感' },
  { key: 'mood', label: '心情' },
  { key: 'message_in', label: '收消息' },
  { key: 'message_out', label: '回复' },
  { key: 'thinking', label: '思考' },
  { key: 'system', label: '系统' },
  { key: 'dream', label: '梦境' },
  { key: 'bilibili', label: 'B站' },
]


type GroupFilter = 'all' | 'active' | string
const ACTIVE_GROUPS = ['993065015', '984198159']

const files = ref<string[]>([])
const selectedFile = ref<string>('')
const content = ref('')
const totalLines = ref(0)
const loading = ref(true)
const viewing = ref(false)
const refreshing = ref(false)
const filterLevel = ref<LevelFilter>('default')
const filterChannels = ref<Set<string>>(new Set())
const filterGroup = ref<GroupFilter>('all')
const searchText = ref('')
const paused = ref(false)
const pausedSnapshot = ref<SSELogEntry[]>([])
const lastUpdatedAt = ref('')
const fileError = ref('')

// Collapsed state per source group (default: dream collapsed, bot expanded, others collapsed)
const groupCollapsed = reactive<Record<string, boolean>>({
  bot: true,
  dream: true,
  other: true,
})

const { logs: sseLogs, connected } = useSSE()

const currentStream = computed(() => (paused.value ? pausedSnapshot.value : sseLogs.value))
const isLiveMode = computed(() => !selectedFile.value)

const filteredEntries = computed(() => {
  let list = currentStream.value

  if (filterLevel.value === 'default') {
    list = list.filter(log => log.level !== 'DEBUG')
  } else {
    list = list.filter(log => log.level === filterLevel.value)
  }

  if (filterChannels.value.size > 0) {
    list = list.filter(log => filterChannels.value.has(log.channel || ''))
  }

  if (filterGroup.value !== 'all') {
    const allowed = filterGroup.value === 'active' ? ACTIVE_GROUPS : [filterGroup.value]
    list = list.filter((log) => {
      const gMatch = log.message.match(/group=(\d+)/)
      if (!gMatch) return true
      return allowed.includes(gMatch[1])
    })
  }

  if (searchText.value.trim()) {
    const query = searchText.value.trim().toLowerCase()
    list = list.filter((log) => {
      const haystack = `${log.message} ${log.channel || ''}`.toLowerCase()
      return haystack.includes(query)
    })
  }
  return list.slice(-150)
})

const logPanelLines = computed<LogPanelLine[]>(() =>
  filteredEntries.value.map((entry, idx) => ({
    id: `${entry.ts}-${idx}`,
    level: logLevel(entry.level),
    timestamp: entry.ts,
    channel: entry.channel || 'runtime',
    text: entry.message,
  })),
)

interface FileEntry {
  name: string
  prefix: 'bot' | 'dream' | 'other'
  size?: number
  dateLabel: string
  isActive: boolean
  /** used for sort key */
  sortKey: string
}

const fileEntries = computed<FileEntry[]>(() => {
  const today = new Date().toISOString().slice(0, 10) // YYYY-MM-DD local-ish; log files use same format

  return files.value.map((name) => {
    let prefix: FileEntry['prefix'] = 'other'
    if (name.startsWith('bot_') || name.startsWith('bot.')) prefix = 'bot'
    else if (name.startsWith('dream_') || name.startsWith('dream.')) prefix = 'dream'

    // Extract YYYY-MM-DD from name (bot_2026-05-14.log or bot_2026-05-13.2026-05-13_15-50-00_000000.log)
    const dateMatch = name.match(/(\d{4})-(\d{2})-(\d{2})/)
    const dateStr = dateMatch ? `${dateMatch[1]}-${dateMatch[2]}-${dateMatch[3]}` : ''
    const isActive = dateStr === today

    // For rotate files, extract the time segment (e.g. "08-35" from the second timestamp)
    const rotateMatch = name.match(/\.(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}_\d+\.log$/)
    let dateLabel: string
    if (rotateMatch) {
      dateLabel = `${formatRelativeDate(dateStr)} ${rotateMatch[2]}:${rotateMatch[3]}`
    } else {
      dateLabel = formatRelativeDate(dateStr) || name
    }

    return {
      name,
      prefix,
      dateLabel,
      isActive,
      sortKey: dateStr || name,
    }
  })
})

const groupedFiles = computed(() => {
  const groups: Record<'bot' | 'dream' | 'other', FileEntry[]> = {
    bot: [],
    dream: [],
    other: [],
  }
  for (const entry of fileEntries.value) {
    groups[entry.prefix].push(entry)
  }
  for (const key of Object.keys(groups) as Array<keyof typeof groups>) {
    groups[key].sort((a, b) => b.sortKey.localeCompare(a.sortKey))
  }
  return groups
})

const groupList = computed(() => {
  const groups = groupedFiles.value
  return [
    { key: 'bot' as const, label: 'Bot 主日志', icon: TerminalOutline, items: groups.bot },
    { key: 'dream' as const, label: 'Dream 日志', icon: MoonOutline, items: groups.dream },
    { key: 'other' as const, label: '其他', icon: DocumentTextOutline, items: groups.other },
  ].filter(g => g.items.length > 0)
})

const totalSources = computed(() => 1 + files.value.length)

const sourceSummary = computed(() => {
  if (selectedFile.value) {
    return `${selectedFile.value}${totalLines.value ? ` · ${totalLines.value} 行` : ''}`
  }
  return paused.value ? '实时流已暂停' : '实时流'
})

const filterActive = computed(() => filterLevel.value !== 'default' || !!searchText.value.trim() || filterChannels.value.size > 0 || filterGroup.value !== 'all')

onMounted(() => {
  void loadLogFiles().then(() => {
    void seedLiveStreamFromTail()
  })
})

/**
 * Seed the live stream from the newest bot log file's tail.
 *
 * The SSE `logs` ref is an in-memory module singleton (useSSE.ts) and the
 * server only pushes *new* entries — never replays backlog. So on a fresh page
 * load the live terminal is empty until the next write lands. We bridge that by
 * reading the latest bot log file's tail once and parsing it into the stream;
 * subsequent SSE events append as usual. No-op if the stream already has data
 * (navigated back to the page) or the user is viewing a specific file.
 */
async function seedLiveStreamFromTail() {
  if (sseLogs.value.length > 0 || selectedFile.value) return
  const newestBot = groupedFiles.value.bot[0] || fileEntries.value[0]
  if (!newestBot) return
  try {
    const data = await api('/api/admin/logs/view', {
      params: { file: newestBot.name, lines: 200 },
    })
    if (sseLogs.value.length > 0) return // SSE already delivered while we awaited
    const seeded = parseFileLines(data.content || '')
      .filter(line => line.time && line.message)
      .map<SSELogEntry>(line => ({
        ts: line.time,
        level: line.level || inferLevel(line.channel) || 'INFO',
        channel: fileLabelToChannel(line.channel),
        message: line.message,
      }))
    if (seeded.length) sseLogs.value = [...seeded, ...sseLogs.value].slice(-200)
  } catch {
    // tail seeding is best-effort; SSE will fill in on next write
  }
}

watch(paused, (value) => {
  if (value) pausedSnapshot.value = [...sseLogs.value]
})

async function loadLogFiles() {
  refreshing.value = true
  try {
    const data = await api('/api/admin/logs')
    files.value = data.files || []
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function openFile(file: string) {
  if (!file) return
  selectedFile.value = file
  fileError.value = ''
  viewing.value = true

  try {
    const data = await api('/api/admin/logs/view', {
      params: { file, lines: 500 },
    })
    content.value = data.content || ''
    totalLines.value = data.total_lines || 0
    fileError.value = data.error || ''
    lastUpdatedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    content.value = ''
    totalLines.value = 0
    fileError.value = '文件读取失败'
  } finally {
    viewing.value = false
  }
}

function switchToLive() {
  selectedFile.value = ''
  content.value = ''
  totalLines.value = 0
  fileError.value = ''
}

function clearLiveStream() {
  sseLogs.value.splice(0)
  pausedSnapshot.value = []
}

function togglePause() {
  paused.value = !paused.value
}

function clearSearch() {
  searchText.value = ''
}

function resetFilters() {
  filterLevel.value = 'default'
  searchText.value = ''
  filterChannels.value = new Set()
  filterGroup.value = 'all'
}

function toggleGroup(key: 'bot' | 'dream' | 'other') {
  groupCollapsed[key] = !groupCollapsed[key]
}

function toggleChannel(ch: string) {
  const s = new Set(filterChannels.value)
  if (s.has(ch)) s.delete(ch)
  else s.add(ch)
  filterChannels.value = s
}

const OBSERVE_CHANNELS = new Set(['scheduler', 'schedule', 'affection', 'mood', 'message_in', 'message_out', 'thinking'])

const isObserveMode = computed(() =>
  filterGroup.value === 'active'
  && filterChannels.value.size === OBSERVE_CHANNELS.size
  && [...OBSERVE_CHANNELS].every(ch => filterChannels.value.has(ch)),
)

function toggleObservePreset() {
  if (isObserveMode.value) {
    resetFilters()
  } else {
    filterLevel.value = 'default'
    filterChannels.value = new Set(OBSERVE_CHANNELS)
    filterGroup.value = 'active'
    searchText.value = ''
  }
}

function logLevel(level: string): LogPanelLine['level'] {
  if (level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  if (level === 'SUCCESS') return 'success'
  if (level === 'DEBUG') return 'debug'
  return 'info'
}

function formatRelativeDate(isoDate: string): string {
  if (!isoDate) return ''
  const [y, m, d] = isoDate.split('-').map(Number)
  if (!y || !m || !d) return isoDate
  const entryDate = new Date(y, m - 1, d)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diffDays = Math.round((today.getTime() - entryDate.getTime()) / 86_400_000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays === 2) return '前天'
  if (diffDays < 0) return `未来 +${Math.abs(diffDays)}d`
  if (diffDays < 7) return `${diffDays} 天前`
  return `${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** Parse a file-mode log line into (time, level, channel, message) pieces
 * for colored rendering. Falls back to plain text when regex fails. */
interface ParsedFileLine {
  time: string
  level: string
  channel: string
  message: string
  raw: string
}

function parseFileLines(text: string): ParsedFileLine[] {
  if (!text) return []
  const lines = text.split('\n')
  const out: ParsedFileLine[] = []
  // Two common shapes in this project:
  //  "05-13 18:35:51 系统     | group inventory refreshed ..."
  //  "05-13 18:35:51 [INFO] kernel | Bot 就绪..."
  const reA = /^(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([\w-]+)\s*\|\s*(.*)$/
  const reB = /^(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+(\S+)\s*\|\s*(.*)$/
  for (const raw of lines) {
    if (!raw.trim()) {
      out.push({ time: '', level: '', channel: '', message: '', raw: '' })
      continue
    }
    let m = reA.exec(raw)
    if (m) {
      out.push({ time: m[1], level: m[2], channel: m[3], message: m[4], raw })
      continue
    }
    m = reB.exec(raw)
    if (m) {
      out.push({ time: m[1], level: inferLevel(m[2]), channel: m[2], message: m[3], raw })
      continue
    }
    // continuation / unparsed
    out.push({ time: '', level: '', channel: '', message: raw, raw })
  }
  return out
}

function inferLevel(channel: string): string {
  const upper = (channel || '').toUpperCase()
  if (upper.includes('ERROR')) return 'ERROR'
  if (upper.includes('WARNING') || upper.includes('WARN')) return 'WARNING'
  if (upper.includes('SUCCESS')) return 'SUCCESS'
  if (upper.includes('DEBUG')) return 'DEBUG'
  return ''
}

/**
 * Map a file-line's channel token back to the English SSE channel key, so
 * seeded (tail-parsed) entries filter identically to live SSE entries.
 * Log files render channels as Chinese labels (bot.py _CHANNEL_LABELS); the
 * live filter keys on the English `bind(channel=...)` value. This reverses it.
 */
const _FILE_LABEL_TO_CHANNEL: Record<string, string> = {
  收消息: 'message_in',
  回复: 'message_out',
  思考: 'thinking',
  心情: 'mood',
  好感: 'affection',
  日程: 'schedule',
  调度: 'scheduler',
  用量: 'usage',
  压缩: 'compact',
  系统: 'system',
  调试: 'debug',
  梦境: 'dream',
  B站: 'bilibili',
}
function fileLabelToChannel(label: string): string {
  const trimmed = (label || '').trim()
  return _FILE_LABEL_TO_CHANNEL[trimmed] || trimmed
}

const parsedFileLines = computed(() => parseFileLines(content.value))

function lineLevelClass(level: string): string {
  if (!level) return ''
  const upper = level.toUpperCase()
  if (upper === 'ERROR') return 'logs-file-line--error'
  if (upper === 'WARNING' || upper === 'WARN') return 'logs-file-line--warning'
  if (upper === 'SUCCESS') return 'logs-file-line--success'
  if (upper === 'DEBUG') return 'logs-file-line--debug'
  if (upper === 'INFO') return 'logs-file-line--info'
  return ''
}
</script>

<template>
  <AppPage
    title="日志"
    eyebrow="Live Logs"
    description="默认先看实时事件流，按需回翻归档文件。默认筛选已隐藏 DEBUG 噪音。"
  >
    <template #action>
      <NSpace align="center" :size="10">
        <StateBadge
          :status="connected ? 'success' : 'error'"
          :label="connected ? 'SSE 在线' : 'SSE 断开'"
          compact
        />
        <NButton size="small" secondary :loading="refreshing" @click="loadLogFiles">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新源列表
        </NButton>
        <RestartBotButton />
      </NSpace>
    </template>

    <div class="om-fill-page">
      <div class="logs-layout om-fill-page__body">
        <!-- ============ Main: unified viewer ============ -->
        <AppCard bordered elevated class="logs-main om-fill-card">
          <!-- Header -->
          <header class="logs-main__head">
            <div>
              <p class="logs-main__eyebrow">
                Terminal View
              </p>
              <h3 class="logs-main__title">
                {{ isLiveMode ? '实时终端流' : '文件视图' }}
              </h3>
            </div>
            <StateBadge
              :status="isLiveMode ? (paused ? 'warning' : 'info') : 'default'"
              :label="sourceSummary"
              compact
            />
          </header>

          <!-- Toolbar: single-row, semantic grouping -->
          <div class="logs-toolbar">
            <!-- Live mode: level segment + search on left; pause + clear on right -->
            <template v-if="isLiveMode">
              <div class="logs-toolbar__group logs-toolbar__filters">
                <div class="logs-segment" role="radiogroup" aria-label="日志级别筛选" @keydown="onRovingKeydown">
                  <button
                    v-for="seg in levelSegments"
                    :key="seg.key"
                    type="button"
                    role="radio"
                    :aria-checked="filterLevel === seg.key"
                    :tabindex="filterLevel === seg.key ? 0 : -1"
                    class="logs-segment__btn"
                    :class="{ 'logs-segment__btn--active': filterLevel === seg.key }"
                    :title="seg.hint"
                    @click="filterLevel = seg.key"
                  >
                    {{ seg.label }}
                  </button>
                </div>
                <div class="logs-channel-chips">
                  <button
                    v-for="ch in channelOptions"
                    :key="ch.key"
                    type="button"
                    class="logs-chip"
                    :class="{ 'logs-chip--active': filterChannels.has(ch.key) }"
                    @click="toggleChannel(ch.key)"
                  >
                    {{ ch.label }}
                  </button>
                </div>
                <div class="logs-group-filter">
                  <select v-model="filterGroup" class="logs-group-select">
                    <option value="all">全部群</option>
                    <option value="active">仅活跃群</option>
                    <option v-for="gid in ACTIVE_GROUPS" :key="gid" :value="gid">{{ gid }}</option>
                  </select>
                </div>
                <div class="logs-search">
                  <NIcon :component="SearchOutline" :size="14" class="logs-search__icon" />
                  <input
                    v-model="searchText"
                    type="text"
                    class="logs-search__input"
                    placeholder="搜索消息或 channel"
                  >
                  <button
                    v-if="searchText"
                    type="button"
                    class="logs-search__clear"
                    title="清除搜索"
                    aria-label="清除搜索"
                    @click="clearSearch"
                  >
                    <NIcon :component="CloseOutline" :size="12" />
                  </button>
                </div>
                <NButton
                  v-if="filterActive"
                  size="tiny"
                  text
                  type="primary"
                  class="logs-toolbar__reset"
                  @click="resetFilters"
                >
                  重置筛选
                </NButton>
              </div>
              <div class="logs-toolbar__actions">
                <NButton size="small" :secondary="!isObserveMode" :type="isObserveMode ? 'primary' : 'default'" @click="toggleObservePreset">
                  {{ isObserveMode ? '退出观察' : '观察模式' }}
                </NButton>
                <NButton size="small" secondary @click="togglePause">
                  <template #icon>
                    <NIcon :component="paused ? PlayOutline : PauseOutline" />
                  </template>
                  {{ paused ? '继续流' : '暂停流' }}
                </NButton>
                <NButton size="small" secondary @click="clearLiveStream">
                  <template #icon>
                    <NIcon :component="TrashOutline" />
                  </template>
                  清屏
                </NButton>
              </div>
            </template>

            <!-- File mode: meta chip + reload + back to live -->
            <template v-else>
              <div class="logs-toolbar__group">
                <StateBadge status="info" label="文件模式" compact />
                <span class="logs-toolbar-text">{{ selectedFile }}</span>
                <span v-if="lastUpdatedAt" class="logs-toolbar-text logs-toolbar-text--muted">
                  更新于 {{ lastUpdatedAt }}
                </span>
              </div>
              <div class="logs-toolbar__actions">
                <NButton size="small" secondary :loading="viewing" @click="openFile(selectedFile)">
                  <template #icon>
                    <NIcon :component="RefreshOutline" />
                  </template>
                  重新读取
                </NButton>
                <NButton size="small" secondary @click="switchToLive">
                  返回实时流
                </NButton>
              </div>
            </template>
          </div>

          <!-- Body -->
          <div class="logs-body om-fill-scroll">
            <!-- Live mode via LogPanel -->
            <template v-if="isLiveMode">
              <LogPanel
                v-if="logPanelLines.length"
                :lines="logPanelLines"
                height="100%"
                :paused="paused"
                :icon="TerminalOutline"
                class="logs-logpanel"
                empty="实时流暂无新事件"
              />
              <EmptyState
                v-else
                :title="filterActive ? '当前筛选下没有事件' : '实时终端还没有内容'"
                :description="connected
                  ? (filterActive ? '调整等级或搜索词，或点上方「重置筛选」。' : '事件流已连接，新的日志会在这里持续滚动。')
                  : 'SSE 暂未连接，请确认后端事件流接口可用。'"
                :icon="TerminalOutline"
              />
            </template>

            <!-- File mode via unified pane (no black terminal) -->
            <NSpin v-else :show="viewing" class="logs-spin">
              <div v-if="content" class="logs-file cus-scroll">
                <div class="logs-file__meta">
                  <NIcon :component="DocumentTextOutline" :size="14" />
                  <span class="logs-file__meta-name">{{ selectedFile }}</span>
                  <span class="logs-file__meta-dot">·</span>
                  <span class="logs-file__meta-count">最近 {{ totalLines }} 行</span>
                </div>
                <div class="logs-file__stream">
                  <div
                    v-for="(line, idx) in parsedFileLines"
                    :key="idx"
                    class="logs-file-line"
                    :class="lineLevelClass(line.level)"
                  >
                    <template v-if="line.time || line.level || line.channel">
                      <span class="logs-file-line__time">{{ line.time }}</span>
                      <span v-if="line.level" class="logs-file-line__level">{{ line.level }}</span>
                      <span class="logs-file-line__channel">{{ line.channel }}</span>
                      <span class="logs-file-line__msg">{{ line.message }}</span>
                    </template>
                    <template v-else>
                      <span class="logs-file-line__continuation">{{ line.raw }}</span>
                    </template>
                  </div>
                </div>
              </div>

              <EmptyState
                v-else-if="fileError"
                title="日志文件读取失败"
                :description="fileError"
                :icon="DocumentTextOutline"
              />

              <EmptyState
                v-else
                title="当前文件没有可显示内容"
                description="这个日志文件可能是空的，或者最近 500 行没有可读取文本。"
                :icon="DocumentTextOutline"
              />
            </NSpin>
          </div>
        </AppCard>

        <!-- ============ Sidebar: grouped sources ============ -->
        <AppCard bordered elevated class="logs-sources om-fill-card">
          <header class="logs-sources__head">
            <div>
              <p class="logs-sources__eyebrow">
                Sources
              </p>
              <h3 class="logs-sources__title">
                日志来源
              </h3>
            </div>
            <StateBadge :label="`${totalSources}`" compact />
          </header>

          <button
            type="button"
            class="logs-source logs-source--live"
            :class="{ 'logs-source--active': isLiveMode }"
            @click="switchToLive"
          >
            <span class="logs-source__icon">
              <NIcon :component="PulseOutline" :size="16" />
            </span>
            <span class="logs-source__copy">
              <strong>实时流</strong>
              <span>{{ connected ? 'SSE 在线' : '等待 SSE 恢复' }}</span>
            </span>
            <span v-if="isLiveMode" class="logs-source__active-dot" />
          </button>

          <div v-if="loading" class="logs-skeleton">
            <NSkeleton :repeat="5" text />
          </div>

          <div v-else-if="files.length > 0" class="logs-source-groups cus-scroll om-fill-scroll">
            <section v-for="group in groupList" :key="group.key" class="logs-group">
              <button
                type="button"
                class="logs-group__head"
                :class="{ 'logs-group__head--collapsed': groupCollapsed[group.key] }"
                @click="toggleGroup(group.key)"
              >
                <NIcon :component="group.icon" :size="14" class="logs-group__icon" />
                <span class="logs-group__label">{{ group.label }}</span>
                <span class="logs-group__count">{{ group.items.length }}</span>
                <NIcon :component="ChevronDownOutline" :size="14" class="logs-group__chevron" />
              </button>
              <ul v-if="!groupCollapsed[group.key]" class="logs-group__items">
                <li v-for="file in group.items" :key="file.name">
                  <button
                    type="button"
                    class="logs-file-row"
                    :class="{ 'logs-file-row--active': selectedFile === file.name }"
                    @click="openFile(file.name)"
                  >
                    <span class="logs-file-row__date">{{ file.dateLabel }}</span>
                    <span v-if="file.isActive" class="logs-file-row__active-tag">活跃</span>
                  </button>
                </li>
              </ul>
            </section>
          </div>

          <EmptyState
            v-else
            compact
            title="没有归档日志文件"
            description="当前日志目录下没有可读取的 .log 或 .txt 文件。"
            :icon="DocumentTextOutline"
          />
        </AppCard>
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.logs-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 16px;
  min-height: 0;
}

.logs-main,
.logs-sources {
  min-height: 0;
  padding: 20px;
}

.logs-sources {
  display: flex;
  flex-direction: column;
  max-height: 100%;
  overflow: hidden;
}

.logs-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ============ Header ============ */
.logs-main__head,
.logs-sources__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.logs-main__eyebrow,
.logs-sources__eyebrow {
  margin: 0 0 6px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.logs-main__title,
.logs-sources__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

/* ============ Toolbar ============ */
.logs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.logs-toolbar__group,
.logs-toolbar__filters,
.logs-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.logs-toolbar__filters {
  flex: 1 1 auto;
  min-width: 0;
}

.logs-toolbar__actions {
  flex: 0 0 auto;
}

.logs-toolbar__reset {
  margin-left: 2px;
}

.logs-toolbar-text {
  color: var(--om-text-2);
  font-size: 13px;
}

.logs-toolbar-text--muted {
  color: var(--om-text-3);
  font-size: 12px;
}

/* ============ Segment ============ */
.logs-segment {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  overflow: hidden;
}

.logs-segment__btn {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 5px 12px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0.02em;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.logs-segment__btn:hover {
  color: var(--om-text-1);
  background: var(--om-surface-2);
}

.logs-segment__btn--active,
.logs-segment__btn--active:hover {
  color: rgb(var(--primary-color));
  background: color-mix(in srgb, rgb(var(--primary-color)) 14%, transparent);
}

/* ============ Search ============ */
.logs-search {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 8px 0 28px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  width: min(260px, 100%);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.logs-search:focus-within {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 45%, var(--om-border-strong));
  box-shadow: 0 0 0 3px color-mix(in srgb, rgb(var(--primary-color)) 14%, transparent);
}

.logs-search__icon {
  position: absolute;
  left: 9px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--om-text-3);
  pointer-events: none;
}

.logs-search__input {
  flex: 1;
  min-width: 0;
  height: 100%;
  background: transparent;
  border: 0;
  outline: none;
  color: var(--om-text-1);
  font-size: 13px;
}

.logs-search__input::placeholder {
  color: var(--om-text-3);
}

.logs-search__clear {
  appearance: none;
  border: 0;
  background: transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  color: var(--om-text-3);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.logs-search__clear:hover {
  color: var(--om-text-1);
  background: var(--om-surface-2);
}

/* ============ Channel Chips ============ */
.logs-channel-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.logs-chip {
  appearance: none;
  border: 1px solid var(--om-border);
  background: var(--om-surface-solid);
  padding: 3px 10px;
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  border-radius: 14px;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.logs-chip:hover {
  color: var(--om-text-1);
  border-color: var(--om-border-strong);
}

.logs-chip--active,
.logs-chip--active:hover {
  color: rgb(var(--primary-color));
  background: color-mix(in srgb, rgb(var(--primary-color)) 12%, transparent);
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 40%, var(--om-border));
}

/* ============ Group Filter ============ */
.logs-group-filter {
  display: inline-flex;
  align-items: center;
}

.logs-group-select {
  appearance: none;
  border: 1px solid var(--om-border);
  background: var(--om-surface-solid);
  padding: 4px 24px 4px 10px;
  color: var(--om-text-1);
  font-size: 12px;
  font-weight: 600;
  border-radius: 10px;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M3 5l3 3 3-3'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 8px center;
  transition: border-color 0.15s ease;
}

.logs-group-select:hover {
  border-color: var(--om-border-strong);
}

.logs-group-select:focus {
  outline: none;
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 45%, var(--om-border-strong));
  box-shadow: 0 0 0 3px color-mix(in srgb, rgb(var(--primary-color)) 14%, transparent);
}

/* ============ Body ============ */
.logs-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logs-logpanel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.logs-logpanel :deep(.log-panel__body) {
  flex: 1;
  min-height: 0;
}

.logs-spin {
  flex: 1;
  min-height: 0;
}

.logs-spin:deep(.n-spin-content) {
  height: 100%;
}

/* ============ File view (unified with live) ============ */
.logs-file {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 14px 16px 16px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
  overflow: auto;
}

.logs-file__meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--om-border);
  color: var(--om-text-2);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
}

.logs-file__meta-name {
  color: var(--om-text-1);
  font-weight: 600;
}

.logs-file__meta-dot {
  color: var(--om-text-3);
}

.logs-file__stream {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--om-text-1);
}

.logs-file-line {
  display: grid;
  grid-template-columns: 88px 68px 108px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
  padding: 1px 2px;
  border-radius: 4px;
}

.logs-file-line:hover {
  background: color-mix(in srgb, var(--om-surface-3) 60%, transparent);
}

.logs-file-line__time {
  color: var(--om-text-3);
  font-variant-numeric: tabular-nums;
}

.logs-file-line__level {
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.logs-file-line__channel {
  color: var(--om-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs-file-line__msg {
  color: var(--om-text-1);
  word-break: break-word;
  overflow-wrap: anywhere;
}

.logs-file-line__continuation {
  grid-column: 1 / -1;
  color: var(--om-text-2);
  padding-left: 196px; /* 88 + 10 + 68 + 10 + 20 */
  word-break: break-word;
  overflow-wrap: anywhere;
}

.logs-file-line--error .logs-file-line__level {
  color: var(--om-danger);
}

.logs-file-line--error .logs-file-line__msg {
  color: color-mix(in srgb, var(--om-danger) 85%, var(--om-text-1));
}

.logs-file-line--warning .logs-file-line__level {
  color: var(--om-warning);
}

.logs-file-line--warning .logs-file-line__msg {
  color: color-mix(in srgb, var(--om-warning) 68%, var(--om-text-1));
}

.logs-file-line--success .logs-file-line__level {
  color: var(--om-success);
}

.logs-file-line--info .logs-file-line__level {
  color: var(--om-info);
}

.logs-file-line--debug .logs-file-line__time,
.logs-file-line--debug .logs-file-line__level,
.logs-file-line--debug .logs-file-line__channel,
.logs-file-line--debug .logs-file-line__msg {
  opacity: 0.65;
}

/* ============ Sidebar ============ */
.logs-source,
.logs-file-row,
.logs-group__head {
  appearance: none;
  width: 100%;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.logs-source {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  transition: border-color 0.15s ease, background-color 0.15s ease, transform 0.15s ease;
}

.logs-source:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  background: var(--om-surface-2);
}

.logs-source--active {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 45%, var(--om-border));
  background: color-mix(in srgb, rgb(var(--primary-color)) 12%, transparent);
}

.logs-source--live {
  margin-top: 2px;
  margin-bottom: 12px;
}

.logs-source__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: color-mix(in srgb, rgb(var(--primary-color)) 14%, transparent);
  color: rgb(var(--primary-color));
}

.logs-source__copy {
  min-width: 0;
  display: block;
}

.logs-source__copy strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.logs-source__copy span {
  display: block;
  margin-top: 2px;
  color: var(--om-text-2);
  font-size: 11px;
  line-height: 1.35;
}

.logs-source__active-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgb(var(--primary-color));
  box-shadow: 0 0 0 3px color-mix(in srgb, rgb(var(--primary-color)) 22%, transparent);
}

.logs-skeleton {
  padding: 6px 4px;
}

.logs-source-groups {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
}

.logs-group {
  border-top: 1px dashed var(--om-border);
  padding-top: 8px;
}

.logs-group:first-child {
  border-top: 0;
  padding-top: 0;
}

.logs-group__head {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 8px;
  padding: 6px 6px;
  border-radius: 8px;
  transition: background-color 0.15s ease;
}

.logs-group__head:hover {
  background: var(--om-surface-2);
}

.logs-group__icon {
  color: var(--om-text-3);
}

.logs-group__label {
  color: var(--om-text-1);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.logs-group__count {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--om-surface-2);
}

.logs-group__chevron {
  color: var(--om-text-3);
  transition: transform 0.18s ease;
}

.logs-group__head--collapsed .logs-group__chevron {
  transform: rotate(-90deg);
}

.logs-group__items {
  list-style: none;
  margin: 4px 0 6px;
  padding: 0 0 0 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.logs-file-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  color: var(--om-text-2);
  font-size: 13px;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.logs-file-row:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
}

.logs-file-row--active,
.logs-file-row--active:hover {
  color: rgb(var(--primary-color));
  background: color-mix(in srgb, rgb(var(--primary-color)) 10%, transparent);
  font-weight: 600;
}

.logs-file-row__date {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.logs-file-row__active-tag {
  color: var(--om-success);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--om-success) 14%, transparent);
}

@media (max-width: 1024px) {
  .logs-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .logs-main,
  .logs-sources {
    padding: 16px;
  }

  .logs-file-line {
    grid-template-columns: 70px 56px 1fr;
    row-gap: 2px;
  }

  .logs-file-line__channel {
    grid-column: 2 / -1;
  }

  .logs-file-line__msg {
    grid-column: 1 / -1;
  }

  .logs-file-line__continuation {
    padding-left: 0;
  }
}
</style>

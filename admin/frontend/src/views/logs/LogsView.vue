<script setup lang="ts">
import {
  DocumentTextOutline,
  PauseOutline,
  PlayOutline,
  PulseOutline,
  RefreshOutline,
  TerminalOutline,
  TrashOutline,
} from '@vicons/ionicons5'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import LogPanel from '../../components/common/LogPanel.vue'
import type { LogPanelLine } from '../../components/common/LogPanel.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import { useSSE, type SSELogEntry } from '../../composables/useSSE'

const files = ref<string[]>([])
const selectedFile = ref<string>('')
const content = ref('')
const totalLines = ref(0)
const loading = ref(true)
const viewing = ref(false)
const refreshing = ref(false)
const filterLevel = ref<string>('')
const searchText = ref('')
const paused = ref(false)
const pausedSnapshot = ref<SSELogEntry[]>([])
const lastUpdatedAt = ref('')
const fileError = ref('')

const { logs: sseLogs, connected } = useSSE()

const levelOptions = [
  { label: '全部等级', value: '' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'INFO', value: 'INFO' },
  { label: 'DEBUG', value: 'DEBUG' },
]

const currentStream = computed(() => (paused.value ? pausedSnapshot.value : sseLogs.value))
const isLiveMode = computed(() => !selectedFile.value)

const filteredEntries = computed(() => {
  let list = currentStream.value
  if (filterLevel.value) list = list.filter(log => log.level === filterLevel.value)
  if (searchText.value.trim()) {
    const query = searchText.value.trim().toLowerCase()
    list = list.filter((log) => {
      const haystack = `${log.message} ${log.channel || ''}`.toLowerCase()
      return haystack.includes(query)
    })
  }
  return list.slice(-120)
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

const sourceSummary = computed(() => {
  if (selectedFile.value) {
    return `${selectedFile.value}${totalLines.value ? ` · ${totalLines.value} 行` : ''}`
  }
  return paused.value ? '实时流已暂停' : '实时流模式'
})

onMounted(() => {
  void loadLogFiles()
})

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

function logLevel(level: string): LogPanelLine['level'] {
  if (level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  if (level === 'SUCCESS') return 'success'
  if (level === 'DEBUG') return 'debug'
  return 'info'
}
</script>

<template>
  <AppPage
    title="日志"
    eyebrow="Live Logs"
    description="默认先看实时事件流；只有需要回翻历史时，再打开落盘日志文件。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <StateBadge
          :status="connected ? 'success' : 'error'"
          :label="connected ? 'SSE 在线' : 'SSE 断开'"
          compact
        />
        <NButton secondary :loading="refreshing" @click="loadLogFiles">
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
        <!-- ============ Main: live stream or file viewer ============ -->
        <AppCard bordered elevated class="logs-main om-fill-card">
          <div class="logs-main__head">
            <div>
              <p class="logs-main__eyebrow">
                Terminal View
              </p>
              <h3 class="logs-main__title">
                {{ isLiveMode ? '实时终端流' : '文件尾部视图' }}
              </h3>
            </div>
            <StateBadge
              :status="isLiveMode ? 'info' : 'default'"
              :label="sourceSummary"
              compact
            />
          </div>

          <PageToolbar>
            <template #left>
              <template v-if="isLiveMode">
                <NSelect
                  v-model:value="filterLevel"
                  :options="levelOptions"
                  size="small"
                  style="width: 132px"
                />
                <NInput
                  v-model:value="searchText"
                  clearable
                  size="small"
                  placeholder="搜索消息或 channel"
                  style="width: min(260px, 100%)"
                />
              </template>
              <template v-else>
                <StateBadge status="info" label="文件模式" compact />
                <span class="logs-toolbar-text">{{ selectedFile }}</span>
                <span v-if="lastUpdatedAt" class="logs-toolbar-text">更新于 {{ lastUpdatedAt }}</span>
              </template>
            </template>

            <template #right>
              <template v-if="isLiveMode">
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
              </template>
              <template v-else>
                <NButton size="small" secondary :loading="viewing" @click="openFile(selectedFile)">
                  <template #icon>
                    <NIcon :component="RefreshOutline" />
                  </template>
                  重新读取
                </NButton>
                <NButton size="small" secondary @click="switchToLive">
                  返回实时流
                </NButton>
              </template>
            </template>
          </PageToolbar>

          <div class="logs-body om-fill-scroll">
            <!-- Live mode: use LogPanel -->
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
                :title="filterLevel || searchText ? '当前筛选下没有事件' : '实时终端还没有内容'"
                :description="connected
                  ? (filterLevel || searchText ? '调整等级或搜索词，或清空筛选条件。' : '事件流已连接，新的日志会在这里持续滚动。')
                  : 'SSE 暂未连接，请确认后端事件流接口可用。'"
                :icon="TerminalOutline"
              />
            </template>

            <!-- File mode: raw pre -->
            <NSpin v-else :show="viewing" class="logs-spin">
              <div v-if="content" class="logs-terminal cus-scroll">
                <div class="logs-terminal__bar">
                  <span class="logs-terminal__bar-dot logs-terminal__bar-dot--red" />
                  <span class="logs-terminal__bar-dot logs-terminal__bar-dot--yellow" />
                  <span class="logs-terminal__bar-dot logs-terminal__bar-dot--green" />
                  <span class="logs-terminal__bar-title">
                    {{ selectedFile }} · 最近 {{ totalLines }} 行
                  </span>
                </div>
                <pre class="logs-terminal__file">{{ content }}</pre>
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

        <!-- ============ Sidebar: source picker ============ -->
        <AppCard bordered elevated class="logs-sources om-fill-card">
          <div class="logs-sources__head">
            <div>
              <p class="logs-sources__eyebrow">
                Sources
              </p>
              <h3 class="logs-sources__title">
                日志来源
              </h3>
            </div>
            <StateBadge :label="`${files.length + 1}`" compact />
          </div>

          <button
            type="button"
            class="logs-source logs-source--live"
            :class="{ 'logs-source--active': isLiveMode }"
            @click="switchToLive"
          >
            <div class="logs-source__icon">
              <NIcon :component="PulseOutline" />
            </div>
            <div class="logs-source__copy">
              <strong>实时流</strong>
              <span>{{ connected ? '通过 SSE 接收最新日志' : '等待 SSE 恢复' }}</span>
            </div>
          </button>

          <div v-if="loading" class="logs-source-list">
            <NSkeleton :repeat="6" text />
          </div>

          <div v-else-if="files.length > 0" class="logs-source-list cus-scroll om-fill-scroll">
            <button
              v-for="file in files"
              :key="file"
              type="button"
              class="logs-source"
              :class="{ 'logs-source--active': selectedFile === file }"
              @click="openFile(file)"
            >
              <div class="logs-source__icon">
                <NIcon :component="DocumentTextOutline" />
              </div>
              <div class="logs-source__copy">
                <strong>{{ file }}</strong>
                <span>{{ selectedFile === file && totalLines ? `${totalLines} 行已载入` : '按需查看最近 500 行' }}</span>
              </div>
            </button>
          </div>

          <EmptyState
            v-else
            compact
            title="没有历史日志文件"
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
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
  min-height: 0;
}

.logs-main,
.logs-sources {
  min-height: 0;
  padding: 20px;
}

.logs-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

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
}

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

.logs-terminal {
  height: 100%;
  padding: 14px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(21, 32, 38, 0.96), rgba(13, 21, 26, 0.98));
  color: #dce7ea;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
  overflow: auto;
}

.logs-terminal__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logs-terminal__bar-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
}

.logs-terminal__bar-dot--red {
  background: #ff6f7d;
}

.logs-terminal__bar-dot--yellow {
  background: #ffcb55;
}

.logs-terminal__bar-dot--green {
  background: #41d98a;
}

.logs-terminal__bar-title {
  margin-left: 4px;
  color: rgba(220, 231, 234, 0.8);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
}

.logs-terminal__file {
  margin: 0;
  color: #dce7ea;
  font-size: 12px;
  line-height: 1.72;
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}

.logs-toolbar-text {
  color: var(--om-text-2);
  font-size: 13px;
}

.logs-source-list {
  display: grid;
  gap: 10px;
  min-height: 0;
  padding-right: 2px;
}

.logs-source {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, transform 0.18s ease;
}

.logs-source:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  background: var(--om-surface-2);
}

.logs-source--active {
  border-color: color-mix(in srgb, rgb(var(--primary-color)) 45%, var(--om-border));
  background: color-mix(in srgb, rgb(var(--primary-color)) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, rgb(var(--primary-color)) 12%, transparent);
}

.logs-source--live {
  margin-bottom: 10px;
}

.logs-source__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: color-mix(in srgb, rgb(var(--primary-color)) 14%, transparent);
  color: rgb(var(--primary-color));
}

.logs-source__copy {
  min-width: 0;
}

.logs-source__copy strong {
  display: block;
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs-source__copy span {
  display: block;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
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
}
</style>

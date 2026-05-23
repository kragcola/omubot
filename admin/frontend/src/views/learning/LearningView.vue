<script setup lang="ts">
import { FlashOutline, RefreshOutline, WarningOutline } from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import { api } from '../../api/client'
import AllOverviewDashboard from './components/AllOverviewDashboard.vue'
import LearningReviewHost from './components/LearningReviewHost.vue'
import LearningTable from './components/LearningTable.vue'
import StageStrip from './components/StageStrip.vue'
import NounComingSoonCard from './slots/NounComingSoonCard.vue'
import NounDrawerHost from './slots/NounDrawerHost.vue'
import NounSidePanelSlot from './slots/NounSidePanelSlot.vue'
import NounToolbarSlot from './slots/NounToolbarSlot.vue'
import EpisodeFoldInProvider from './slots/episode/EpisodeFoldInProvider.vue'
import MemoryFoldInProvider from './slots/memory/MemoryFoldInProvider.vue'
import SlangFoldInProvider from './slots/slang/SlangFoldInProvider.vue'
import StyleFoldInProvider from './slots/style/StyleFoldInProvider.vue'
import type { NounSlotContext } from './slots/types'
import type {
  LearningDateFilter,
  LearningExtractNounKey,
  LearningExtractNounProgress,
  LearningExtractNounStatus,
  LearningExtractRun,
  LearningExtractRunStatus,
  LearningItem,
  LearningItemsResponse,
  LearningNounFilter,
  LearningNounKey,
  LearningSortKey,
  LearningStageKey,
  StageStripItem,
} from './types'

interface PipelineStagePayload {
  total: number
  by_noun: Record<LearningNounKey, number | null>
}

interface LearningPipelineResponse {
  as_of: string
  stages: Record<LearningStageKey, PipelineStagePayload>
  warnings: Array<{ noun: string, error: string }>
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const stageMetas: Array<Omit<StageStripItem, 'total' | 'byNoun'>> = [
  {
    key: 'candidate',
    eyebrow: 'Stage 01',
    label: '候选池',
    description: '抽取产物与折返后的待处理条目。',
  },
  {
    key: 'review',
    eyebrow: 'Stage 02',
    label: '待审',
    description: 'AI 初审或人工介入后尚未拍板的条目。',
  },
  {
    key: 'approved',
    eyebrow: 'Stage 03',
    label: '入库',
    description: '已通过审核并可进入后续使用的条目。',
  },
  {
    key: 'hits',
    eyebrow: 'Stage 04',
    label: '命中',
    description: '今日进入 prompt 的观测命中，包含完整注入与裁剪注入。',
  },
  {
    key: 'archived',
    eyebrow: 'Stage 05',
    label: '归档',
    description: '已拒绝、静音、禁用或过期的条目。',
  },
]

const stageKeys = stageMetas.map(stage => stage.key)
const nounKeys: LearningNounKey[] = ['slang', 'style', 'episode', 'memory', 'fact', 'graph_relation']
const extractNounKeys: LearningExtractNounKey[] = ['slang', 'style', 'consolidator']
const dateKeys: LearningDateFilter[] = ['today', '7d', '30d', 'all']
const sortKeys: LearningSortKey[] = ['newest', 'confidence', 'group']

const nounLabels: Record<LearningNounKey, string> = {
  slang: '黑话',
  style: '风格',
  episode: '经验',
  memory: '记忆',
  fact: '事实',
  graph_relation: '关系',
}

const extractNounLabels: Record<LearningExtractNounKey, string> = {
  slang: '黑话',
  style: '风格',
  consolidator: '记忆候选',
}

const extractMetricLabels: Record<string, string> = {
  saved: '保存',
  extracted_terms: '提取',
  candidates: '候选',
  scanned: '扫描',
}

const nounOptions = [
  { label: '全部', value: 'all' },
  ...nounKeys.map(noun => ({ label: nounLabels[noun], value: noun })),
]

const dateOptions = [
  { label: '今天', value: 'today' },
  { label: '7 天', value: '7d' },
  { label: '30 天', value: '30d' },
  { label: '全部', value: 'all' },
]

const sortOptions = [
  { label: '最新', value: 'newest' },
  { label: '置信度', value: 'confidence' },
  { label: '来源群', value: 'group' },
]

const pipeline = ref<LearningPipelineResponse | null>(null)
const learningItems = ref<LearningItem[]>([])
const loading = ref(false)
const itemsLoading = ref(false)
const moreLoading = ref(false)
const extractAllLoading = ref(false)
const error = ref('')
const itemError = ref('')
const itemWarnings = ref<Array<{ noun: string, error: string }>>([])
const nextCursor = ref('')
const hasMoreItems = ref(false)
const groupDraft = ref('')
const reviewOpen = ref(false)
const reviewItem = ref<LearningItem | null>(null)
const extractRun = ref<LearningExtractRun | null>(null)
let extractPollTimer: ReturnType<typeof setTimeout> | null = null

const routeState = computed(() => normalizeRouteQuery(route.query))
const activeStage = computed(() => routeState.value.stage)
const activeNoun = computed(() => routeState.value.noun)
const activeDate = computed(() => routeState.value.date)
const activeGroup = computed(() => routeState.value.group)
const activeSort = computed(() => routeState.value.sort)

const stageItems = computed<StageStripItem[]>(() => {
  return stageMetas.map((stage) => {
    const payload = pipeline.value?.stages[stage.key]
    return {
      ...stage,
      total: payload?.total ?? 0,
      byNoun: payload?.by_noun ?? emptyNounCounts(),
    }
  })
})

const activeStageItem = computed(() => stageItems.value.find(stage => stage.key === activeStage.value) ?? stageItems.value[0])

const extractRunActive = computed(() =>
  isActiveExtractStatus(extractRun.value?.status),
)

const extractButtonLoading = computed(() =>
  extractAllLoading.value || extractRunActive.value,
)

const extractRunAlertType = computed<'info' | 'success' | 'warning' | 'error'>(() => {
  if (!extractRun.value) return 'info'
  if (extractRun.value.status === 'completed') return 'success'
  if (extractRun.value.status === 'partial_failed') return 'warning'
  if (extractRun.value.status === 'failed' || extractRun.value.status === 'not_found') return 'error'
  return 'info'
})

const extractRunTitle = computed(() => {
  const status = extractRun.value?.status
  if (status === 'completed') return '抽取已完成'
  if (status === 'partial_failed') return '抽取部分失败'
  if (status === 'failed') return '抽取失败'
  if (status === 'not_found') return '抽取记录不存在'
  return '抽取运行中'
})

const extractNounRows = computed(() => {
  const run = extractRun.value
  return extractNounKeys.map((key) => {
    const progress = run?.nouns?.[key]
    return {
      key,
      label: extractNounLabels[key],
      status: progress?.status ?? 'pending',
      result: progress?.result ?? null,
      error: progress?.error ?? '',
      updatedAt: progress?.updated_at ?? run?.updated_at ?? '',
    }
  })
})

const visibleNounRows = computed(() => {
  const counts = activeStageItem.value.byNoun
  return nounKeys
    .filter(noun => activeNoun.value === 'all' || activeNoun.value === noun)
    .map(noun => ({
      key: noun,
      label: nounLabels[noun],
      value: counts[noun],
    }))
})

const combinedWarnings = computed(() => [
  ...(pipeline.value?.warnings ?? []),
  ...itemWarnings.value,
])

const slotContext = computed<NounSlotContext>(() => ({
  noun: activeNoun.value,
  stage: activeStage.value,
  group: activeGroup.value,
  date: activeDate.value,
  refresh,
}))

const isAllNoun = computed(() => activeNoun.value === 'all')
const showNounSlots = computed(() => activeNoun.value !== 'all')
const isSlangNoun = computed(() => activeNoun.value === 'slang')
const isStyleNoun = computed(() => activeNoun.value === 'style')
const isEpisodeNoun = computed(() => activeNoun.value === 'episode')
const isMemoryNoun = computed(() => activeNoun.value === 'memory')
const foldedNoun = computed(() =>
  isSlangNoun.value || isStyleNoun.value || isEpisodeNoun.value || isMemoryNoun.value,
)
const nounTakesMain = computed(() =>
  foldedNoun.value
  && activeStage.value !== 'hits'
  && !isMemoryNoun.value,
)

const formattedAsOf = computed(() => {
  if (!pipeline.value?.as_of) return '尚未同步'
  const date = new Date(pipeline.value.as_of)
  if (Number.isNaN(date.getTime())) return pipeline.value.as_of
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
})

watch(
  () => route.query,
  () => {
    const normalized = buildQuery(routeState.value)
    if (!sameQuery(route.query, normalized)) {
      void router.replace({ name: 'learning', query: normalized })
    }
  },
  { immediate: true },
)

watch(
  activeGroup,
  (group) => {
    groupDraft.value = group
  },
  { immediate: true },
)

watch(
  () => [activeGroup.value, activeDate.value],
  () => {
    void loadPipeline()
  },
  { immediate: true },
)

watch(
  () => [
    activeStage.value,
    activeNoun.value,
    activeDate.value,
    activeGroup.value,
    activeSort.value,
  ],
  () => {
    void loadItems()
  },
  { immediate: true },
)

onUnmounted(() => {
  stopExtractPolling()
})

function emptyNounCounts(): Record<LearningNounKey, number | null> {
  return {
    slang: null,
    style: null,
    episode: null,
    memory: null,
    fact: null,
    graph_relation: null,
  }
}

function firstQueryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '')
  return String(value ?? '')
}

function normalizeRouteQuery(query: typeof route.query) {
  const rawStage = firstQueryValue(query.stage)
  const stage = stageKeys.includes(rawStage as LearningStageKey)
    ? rawStage as LearningStageKey
    : 'candidate'
  const rawNoun = firstQueryValue(query.noun)
  const noun = (rawNoun === 'all' || nounKeys.includes(rawNoun as LearningNounKey))
    ? rawNoun as LearningNounFilter
    : 'all'
  const rawDate = firstQueryValue(query.date)
  const date = stage === 'hits'
    ? 'today'
    : dateKeys.includes(rawDate as LearningDateFilter)
      ? rawDate as LearningDateFilter
      : 'all'
  const rawSort = firstQueryValue(query.sort)
  const sort = sortKeys.includes(rawSort as LearningSortKey)
    ? rawSort as LearningSortKey
    : 'newest'
  return {
    stage,
    noun,
    date,
    sort,
    group: firstQueryValue(query.group).trim(),
  }
}

function buildQuery(state: {
  stage: LearningStageKey
  noun: LearningNounFilter
  date: LearningDateFilter
  sort: LearningSortKey
  group: string
}) {
  const query: Record<string, string> = {}
  if (state.stage !== 'candidate') query.stage = state.stage
  if (state.noun !== 'all') query.noun = state.noun
  if (state.date !== 'all') query.date = state.date
  if (state.sort !== 'newest') query.sort = state.sort
  if (state.group) query.group = state.group
  return query
}

function sameQuery(raw: typeof route.query, normalized: Record<string, string>) {
  const rawKeys = Object.keys(raw).filter(key => firstQueryValue(raw[key]) !== '')
  const normalizedKeys = Object.keys(normalized)
  if (rawKeys.length !== normalizedKeys.length) return false
  return normalizedKeys.every(key => firstQueryValue(raw[key]) === normalized[key])
}

function nextQuery(patch: Partial<ReturnType<typeof normalizeRouteQuery>>) {
  return buildQuery({
    ...routeState.value,
    ...patch,
  })
}

function selectStage(stage: LearningStageKey) {
  if (stage === activeStage.value) return
  const patch: Partial<ReturnType<typeof normalizeRouteQuery>> = { stage }
  if (stage === 'hits') patch.date = 'today'
  else if (stage === 'approved' || stage === 'archived') patch.date = 'all'
  void router.push({ name: 'learning', query: nextQuery(patch) })
}

function jumpToNounStage(noun: LearningNounKey, stage: LearningStageKey) {
  const patch: Partial<ReturnType<typeof normalizeRouteQuery>> = {
    noun,
    stage,
  }
  if (stage === 'hits') patch.date = 'today'
  else if (stage === 'approved' || stage === 'archived') patch.date = 'all'
  void router.push({ name: 'learning', query: nextQuery(patch) })
}

function updateNoun(noun: string | number | boolean) {
  void router.push({
    name: 'learning',
    query: nextQuery({ noun: String(noun) as LearningNounFilter }),
  })
}

function updateDate(date: string | number | null) {
  void router.push({
    name: 'learning',
    query: nextQuery({ date: date as LearningDateFilter }),
  })
}

function updateGroup(group: string | null) {
  void router.replace({
    name: 'learning',
    query: nextQuery({ group: String(group ?? '').trim() }),
  })
}

function updateSort(sort: string | number | null) {
  void router.replace({
    name: 'learning',
    query: nextQuery({ sort: sort as LearningSortKey }),
  })
}

async function loadPipeline() {
  loading.value = true
  error.value = ''
  try {
    pipeline.value = await api<LearningPipelineResponse>('/api/admin/learning/pipeline', {
      query: {
        group: activeGroup.value,
        date: activeDate.value,
      },
    })
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    message.error('学习管道读取失败')
  } finally {
    loading.value = false
  }
}

async function loadItems(options: { append?: boolean } = {}) {
  const append = Boolean(options.append)
  if (append && (!hasMoreItems.value || moreLoading.value)) return
  if (append) {
    moreLoading.value = true
  } else {
    itemsLoading.value = true
    learningItems.value = []
    nextCursor.value = ''
    hasMoreItems.value = false
  }
  itemError.value = ''
  try {
    const payload = await api<LearningItemsResponse>('/api/admin/learning/items', {
      query: {
        stage: activeStage.value,
        noun: activeNoun.value,
        group: activeGroup.value,
        date: activeDate.value,
        sort: activeSort.value,
        limit: 30,
        cursor: append ? nextCursor.value : '',
      },
    })
    learningItems.value = append
      ? [...learningItems.value, ...payload.items]
      : payload.items
    nextCursor.value = payload.next_cursor || ''
    hasMoreItems.value = Boolean(payload.has_more)
    itemWarnings.value = payload.warnings || []
  } catch (err) {
    itemError.value = err instanceof Error ? err.message : String(err)
    message.error('学习条目读取失败')
  } finally {
    if (append) {
      moreLoading.value = false
    } else {
      itemsLoading.value = false
    }
  }
}

async function runExtractAll() {
  extractAllLoading.value = true
  try {
    const payload = await api<LearningExtractRun>('/api/admin/learning/extract-all', {
      method: 'POST',
      body: {
        group_id: activeGroup.value,
        limit: 80,
        max_batches: 1,
        batch_size: 50,
        wait: false,
      },
    })
    if (!payload.ok) {
      if (payload.run_id) {
        extractRun.value = payload
        startExtractPolling(payload.run_id)
      }
      message.warning(payload.error || '抽取任务未启动')
      return
    }
    extractRun.value = payload
    if (isActiveExtractStatus(payload.status)) {
      message.success('抽取任务已启动')
      startExtractPolling(payload.run_id)
    } else {
      finishExtractRun(payload)
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : '抽取任务失败')
  } finally {
    extractAllLoading.value = false
  }
}

function startExtractPolling(runId: string) {
  stopExtractPolling()

  const poll = async () => {
    try {
      const payload = await api<LearningExtractRun>(`/api/admin/learning/extract-all/${runId}`)
      extractRun.value = payload
      if (isActiveExtractStatus(payload.status)) {
        extractPollTimer = setTimeout(poll, 1200)
        return
      }
      finishExtractRun(payload)
    } catch (err) {
      stopExtractPolling()
      message.error(err instanceof Error ? err.message : '抽取进度读取失败')
    }
  }

  extractPollTimer = setTimeout(poll, 800)
}

function stopExtractPolling() {
  if (!extractPollTimer) return
  clearTimeout(extractPollTimer)
  extractPollTimer = null
}

function finishExtractRun(payload: LearningExtractRun) {
  stopExtractPolling()
  extractRun.value = payload
  announceExtractRun(payload)
  refresh()
}

function announceExtractRun(payload: LearningExtractRun) {
  if (payload.status === 'not_found') {
    message.warning('抽取记录不存在')
    return
  }
  const nouns = Object.values(payload.nouns || {})
  const failed = nouns.filter(item =>
    ['failed', 'timeout', 'cancelled'].includes(item.status),
  ).length
  const skipped = nouns.filter(item => item.status === 'skipped').length
  if (payload.status === 'failed' || failed > 0) {
    message.warning(`抽取已完成，${failed || 1} 项失败`)
  } else if (skipped > 0) {
    message.warning(`抽取已完成，${skipped} 项未接入`)
  } else {
    message.success('抽取已完成')
  }
}

function isActiveExtractStatus(status?: LearningExtractRunStatus) {
  return status === 'queued' || status === 'running'
}

function extractStatusLabel(status: LearningExtractNounStatus | LearningExtractRunStatus | string) {
  const labels: Record<string, string> = {
    pending: '等待',
    queued: '排队',
    running: '运行中',
    completed: '完成',
    skipped: '跳过',
    failed: '失败',
    timeout: '超时',
    cancelled: '取消',
    partial_failed: '部分失败',
    not_found: '不存在',
  }
  return labels[status] || status || '未知'
}

function extractStatusType(
  status: LearningExtractNounStatus | LearningExtractRunStatus | string,
): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (status === 'completed') return 'success'
  if (status === 'running' || status === 'queued') return 'info'
  if (status === 'pending' || status === 'skipped' || status === 'partial_failed') return 'warning'
  if (status === 'failed' || status === 'timeout' || status === 'cancelled' || status === 'not_found') {
    return 'error'
  }
  return 'default'
}

function extractResultHint(row: {
  status: LearningExtractNounStatus
  result: LearningExtractNounProgress['result']
  error: string
}) {
  if (row.error) return row.error
  if (!row.result) {
    if (row.status === 'running') return '正在处理'
    if (row.status === 'pending') return '等待开始'
    return extractStatusLabel(row.status)
  }
  if (row.result.error) return String(row.result.error)
  if (row.result.run_id) return `run ${row.result.run_id}`
  for (const [key, label] of Object.entries(extractMetricLabels)) {
    const value = row.result[key]
    if (typeof value === 'number') return `${label} ${value}`
  }
  return row.result.ok === false ? '失败' : '已完成'
}

function formatExtractTime(value: string) {
  if (!value) return '尚未更新'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function loadMoreItems() {
  void loadItems({ append: true })
}

function openReview(item: LearningItem) {
  reviewItem.value = item
  reviewOpen.value = true
}

function openItemDetail(item: LearningItem) {
  if (!item.deep_link) return
  void router.push(item.deep_link).catch(() => {})
}

function onReviewDone() {
  void loadPipeline()
  void loadItems()
}

function refresh() {
  void loadPipeline()
  void loadItems()
}

function formatCount(value: number | null): string {
  return value === null ? '--' : String(value)
}
</script>

<template>
  <AppPage
    title="学习管道总览"
    eyebrow="Learning Pipeline"
    description="候选、审核、入库、命中、归档的同一条学习管道。"
  >
    <template #action>
      <NPopconfirm
        positive-text="继续"
        negative-text="取消"
        @positive-click="runExtractAll"
      >
        <template #trigger>
          <NButton secondary :loading="extractButtonLoading">
            <template #icon>
              <NIcon :component="FlashOutline" />
            </template>
            一键抽取
          </NButton>
        </template>
        将并发触发黑话、风格、记忆候选抽取，可能消耗较多 LLM 配额。
      </NPopconfirm>
      <NButton :loading="loading" @click="refresh">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <div class="learning-page">
      <StageStrip
        :stages="stageItems"
        :active-stage="activeStage"
        :loading="loading && !pipeline"
        @select="selectStage"
      />

      <PageToolbar>
        <template #left>
          <NRadioGroup :value="activeNoun" @update:value="updateNoun">
            <NRadioButton
              v-for="option in nounOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </NRadioButton>
          </NRadioGroup>
        </template>
        <template #right>
          <NInput
            v-model:value="groupDraft"
            class="learning-page__group"
            placeholder="群号"
            clearable
            @update:value="updateGroup"
          />
          <NSelect
            :value="activeDate"
            class="learning-page__date"
            :options="dateOptions"
            :disabled="activeStage === 'hits'"
            @update:value="updateDate"
          />
          <NSelect
            :value="activeSort"
            class="learning-page__sort"
            :options="sortOptions"
            @update:value="updateSort"
          />
          <NounToolbarSlot v-if="showNounSlots" :ctx="slotContext">
            <div id="learning-noun-toolbar-target" />
          </NounToolbarSlot>
        </template>
      </PageToolbar>

      <NAlert v-if="error || itemError" type="error" :bordered="false">
        {{ error || itemError }}
      </NAlert>

      <NAlert
        v-else-if="combinedWarnings.length"
        type="warning"
        :bordered="false"
      >
        <template #icon>
          <NIcon :component="WarningOutline" />
        </template>
        {{ combinedWarnings.map(item => `${item.noun}: ${item.error}`).join('；') }}
      </NAlert>

      <NAlert
        v-if="extractRun"
        :type="extractRunAlertType"
        :bordered="false"
        class="learning-extract-run"
      >
        <template #icon>
          <NIcon :component="FlashOutline" />
        </template>
        <div class="learning-extract-run__body">
          <header class="learning-extract-run__header">
            <div>
              <strong>{{ extractRunTitle }}</strong>
              <span>{{ extractRun.run_id }}</span>
            </div>
            <NTag
              size="small"
              round
              :type="extractStatusType(extractRun.status)"
            >
              {{ extractStatusLabel(extractRun.status) }}
            </NTag>
          </header>
          <div class="learning-extract-run__grid">
            <div
              v-for="row in extractNounRows"
              :key="row.key"
              class="learning-extract-run__row"
            >
              <div class="learning-extract-run__row-head">
                <span>{{ row.label }}</span>
                <NTag
                  size="small"
                  round
                  :type="extractStatusType(row.status)"
                >
                  {{ extractStatusLabel(row.status) }}
                </NTag>
              </div>
              <p>{{ extractResultHint(row) }}</p>
            </div>
          </div>
          <span class="learning-extract-run__time">
            {{ formatExtractTime(extractRun.updated_at) }}
          </span>
        </div>
      </NAlert>

      <AllOverviewDashboard
        v-if="isAllNoun"
        :stages="stageItems"
        :items="learningItems"
        :loading="loading && !pipeline"
        :as-of="formattedAsOf"
        :noun-labels="nounLabels"
        :active-stage="activeStage"
        @select-noun="(noun) => updateNoun(noun)"
        @select-stage="jumpToNounStage"
        @open-item="openItemDetail"
      />

      <section v-if="!isAllNoun" class="learning-snapshot">
        <header class="learning-snapshot__header">
          <div>
            <span class="learning-snapshot__eyebrow">Current Stage</span>
            <h2>{{ activeStageItem.label }}</h2>
            <p>{{ activeStageItem.description }}</p>
          </div>
          <div class="learning-snapshot__meta">
            <span>{{ formattedAsOf }}</span>
            <strong>{{ loading && !pipeline ? '...' : activeStageItem.total }}</strong>
          </div>
        </header>

        <div v-if="visibleNounRows.length" class="learning-noun-grid">
          <div
            v-for="row in visibleNounRows"
            :key="row.key"
            class="learning-noun-row"
            :class="{ 'learning-noun-row--empty': row.value === 0 || row.value === null }"
          >
            <span>{{ row.label }}</span>
            <strong>{{ loading && !pipeline ? '...' : formatCount(row.value) }}</strong>
          </div>
        </div>
        <EmptyState
          v-else
          compact
          title="没有匹配项"
          description="调整筛选后会显示对应阶段的统计。"
        />
      </section>

      <div
        v-if="!isAllNoun"
        class="learning-body"
        :class="{ 'learning-body--with-side': showNounSlots }"
      >
        <section class="learning-items">
          <header class="learning-items__header">
            <div>
              <span class="learning-snapshot__eyebrow">Learning Items</span>
              <h2>{{ activeStageItem.label }}列表</h2>
            </div>
            <span v-if="!nounTakesMain">{{ learningItems.length }} 条</span>
          </header>
          <div id="learning-noun-main-target" />
          <LearningTable
            v-if="!nounTakesMain"
            :items="learningItems"
            :loading="itemsLoading"
            :has-more="hasMoreItems"
            :loading-more="moreLoading"
            @review-item="openReview"
            @open-detail="openItemDetail"
            @load-more="loadMoreItems"
          />
        </section>

        <NounSidePanelSlot v-if="showNounSlots" :ctx="slotContext">
          <div id="learning-noun-side-target" />
          <NounComingSoonCard v-if="!foldedNoun" :ctx="slotContext" />
        </NounSidePanelSlot>
      </div>
    </div>

    <NounDrawerHost v-if="showNounSlots" :ctx="slotContext" />

    <SlangFoldInProvider
      v-if="isSlangNoun"
      :stage="activeStage"
      :group="activeGroup"
      main-pane-target="#learning-noun-main-target"
      toolbar-target="#learning-noun-toolbar-target"
      side-target="#learning-noun-side-target"
    />

    <StyleFoldInProvider
      v-if="isStyleNoun"
      :stage="activeStage"
      :group="activeGroup"
      main-pane-target="#learning-noun-main-target"
      toolbar-target="#learning-noun-toolbar-target"
      side-target="#learning-noun-side-target"
    />

    <EpisodeFoldInProvider
      v-if="isEpisodeNoun"
      :stage="activeStage"
      :group="activeGroup"
      main-pane-target="#learning-noun-main-target"
      toolbar-target="#learning-noun-toolbar-target"
      side-target="#learning-noun-side-target"
    />

    <MemoryFoldInProvider
      v-if="isMemoryNoun"
      :stage="activeStage"
      :group="activeGroup"
      main-pane-target="#learning-noun-main-target"
      toolbar-target="#learning-noun-toolbar-target"
      side-target="#learning-noun-side-target"
    />

    <LearningReviewHost
      v-model:show="reviewOpen"
      :item="reviewItem"
      @done="onReviewDone"
    />
  </AppPage>
</template>

<style scoped>
.learning-page {
  display: grid;
  gap: 16px;
}

.learning-body {
  display: grid;
  gap: 16px;
}

.learning-body--with-side {
  grid-template-columns: minmax(0, 1fr) 320px;
  align-items: start;
}

.learning-page__group {
  width: 180px;
}

.learning-page__date,
.learning-page__sort {
  width: 120px;
}

.learning-snapshot {
  display: grid;
  gap: 16px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.learning-items {
  display: grid;
  gap: 8px;
  padding: 10px 12px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
}

.learning-extract-run:deep(.n-alert-body__content) {
  width: 100%;
}

.learning-extract-run__body {
  display: grid;
  gap: 12px;
}

.learning-extract-run__header,
.learning-extract-run__row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.learning-extract-run__header strong {
  display: block;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.learning-extract-run__header span,
.learning-extract-run__time {
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-extract-run__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}

.learning-extract-run__row {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
}

.learning-extract-run__row-head span {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.learning-extract-run__row p {
  margin: 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
}

.learning-items__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.learning-items__header h2 {
  margin: 2px 0 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.learning-items__header span:last-child {
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-snapshot__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.learning-snapshot__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.learning-snapshot h2 {
  margin: 4px 0 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.learning-snapshot p {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.learning-snapshot__meta {
  display: grid;
  flex-shrink: 0;
  justify-items: end;
  gap: 4px;
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-snapshot__meta strong {
  color: var(--om-text-1);
  font-variant-numeric: tabular-nums;
  font-size: 28px;
  line-height: 1;
}

.learning-noun-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(144px, 1fr));
  gap: 12px;
}

.learning-noun-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 56px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
}

.learning-noun-row span {
  color: var(--om-text-2);
  font-size: 13px;
}

.learning-noun-row strong {
  color: var(--om-text-1);
  font-variant-numeric: tabular-nums;
  font-size: 22px;
}

.learning-noun-row--empty {
  opacity: 0.64;
}

@media (max-width: 1180px) {
  .learning-body--with-side {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .learning-snapshot__header {
    flex-direction: column;
  }

  .learning-snapshot__meta {
    justify-items: start;
  }

  .learning-page__group,
  .learning-page__date,
  .learning-page__sort {
    width: 100%;
  }
}
</style>

<script setup lang="ts">
import {
  AddOutline,
  AlbumsOutline,
  ChevronDownOutline,
  ChevronForwardOutline,
  LayersOutline,
  RefreshOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NInputNumber,
  NPopconfirm,
  NSelect,
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

interface Card {
  card_id: string
  category: string
  category_label: string
  scope: string
  scope_label: string
  scope_id: string
  content: string
  confidence: number
  status: string
  priority: number
  source: string
  series_id?: string | null
  created_at: string
  updated_at: string
}

interface CardSeries {
  series_id: string
  series_key: string
  label: string
  source: string
  card_count?: number
  created_at: string
}

interface SeriesHeaderRow {
  _type: 'series-header'
  series_id: string
  label: string
  card_count: number
  expanded: boolean
}

interface CardRow {
  _type: 'card'
  _indent: boolean
  card: Card
}

type FlatRow = SeriesHeaderRow | CardRow
type MemoryViewMode = 'manage' | 'browse'

withDefaults(defineProps<{
  activeView?: MemoryViewMode
}>(), {
  activeView: 'manage',
})

const emit = defineEmits<{
  (e: 'change-view', view: MemoryViewMode): void
}>()

const loading = ref(true)
const refreshing = ref(false)
const cards = ref<Card[]>([])
const total = ref(0)
const seriesList = ref<CardSeries[]>([])
const expandedSeries = ref<Set<string>>(new Set())

const filterScope = ref<string>('')
const filterScopeId = ref('')
const filterSeries = ref<string>('')

const drawerVisible = ref(false)
const editingCard = ref<Card | null>(null)
const isNew = ref(false)
const saving = ref(false)

const editContent = ref('')
const editCategory = ref('fact')
const editConfidence = ref(0.7)
const editPriority = ref(5)
const editScope = ref<'user' | 'group' | 'global'>('user')
const editScopeId = ref('')
const editSeriesId = ref('')

const message = useMessage()

const scopeOptions: SelectOption[] = [
  { label: '全部范围', value: '' },
  { label: '私聊', value: 'user' },
  { label: '群聊', value: 'group' },
  { label: '全局', value: 'global' },
]

const drawerScopeOptions: SelectOption[] = [
  { label: '私聊', value: 'user' },
  { label: '群聊', value: 'group' },
  { label: '全局', value: 'global' },
]

const categoryOptions: SelectOption[] = [
  { label: '偏好', value: 'preference' },
  { label: '边界', value: 'boundary' },
  { label: '关系', value: 'relationship' },
  { label: '事件', value: 'event' },
  { label: '承诺', value: 'promise' },
  { label: '事实', value: 'fact' },
  { label: '状态', value: 'status' },
]

const seriesOptions = computed<SelectOption[]>(() => [
  { label: '全部系列', value: '' },
  { label: '（无系列）', value: '__none__' },
  ...seriesList.value.map(series => ({
    label: `${series.label || series.series_key}${series.card_count ? ` (${series.card_count})` : ''}`,
    value: series.series_id,
  })),
])

const drawerSeriesOptions = computed<SelectOption[]>(() => [
  { label: '不归入系列', value: '' },
  ...seriesList.value.map(series => ({
    label: series.label || series.series_key,
    value: series.series_id,
  })),
])

const flatRows = computed<FlatRow[]>(() => {
  const grouped = new Map<string, Card[]>()
  const standalone: Card[] = []

  for (const card of cards.value) {
    if (card.series_id) {
      const current = grouped.get(card.series_id) || []
      current.push(card)
      grouped.set(card.series_id, current)
    } else {
      standalone.push(card)
    }
  }

  const rows: FlatRow[] = []
  for (const series of seriesList.value) {
    const seriesCards = grouped.get(series.series_id)
    if (!seriesCards?.length) continue
    const expanded = expandedSeries.value.has(series.series_id)
    rows.push({
      _type: 'series-header',
      series_id: series.series_id,
      label: series.label || series.series_key,
      card_count: seriesCards.length,
      expanded,
    })
    if (expanded) {
      for (const card of seriesCards) {
        rows.push({ _type: 'card', _indent: true, card })
      }
    }
  }

  for (const card of standalone) {
    rows.push({ _type: 'card', _indent: false, card })
  }

  return rows
})

const activeCardCount = computed(() =>
  cards.value.filter(card => card.status === 'active').length,
)

const expiredCardCount = computed(() =>
  cards.value.filter(card => card.status === 'expired').length,
)

const visibleSeriesCount = computed(() =>
  new Set(cards.value.map(card => card.series_id).filter(Boolean)).size,
)

const distinctEntityCount = computed(() =>
  new Set(cards.value.map(card => `${card.scope}:${card.scope_id}`)).size,
)

const drawerSeriesLabel = computed(() => {
  if (!editSeriesId.value) return '不归入系列'
  const matched = seriesList.value.find(series => series.series_id === editSeriesId.value)
  return matched?.label || matched?.series_key || '未知系列'
})

const columns: DataTableColumns<FlatRow> = [
  {
    title: '系列 / 分类',
    key: 'series',
    minWidth: 220,
    render: (row) => {
      if (row._type === 'series-header') {
        return h('div', {
          class: 'memory-series-row',
          onClick: (event: MouseEvent) => {
            event.stopPropagation()
            toggleSeries(row.series_id)
          },
        }, [
          h(NIcon, {
            size: 14,
            component: row.expanded ? ChevronDownOutline : ChevronForwardOutline,
          }),
          h('strong', { class: 'memory-series-row__title' }, row.label),
          h(NTag, { size: 'small', round: true, type: 'info' }, () => `${row.card_count} 张`),
        ])
      }

      return h('div', {
        class: ['memory-category-cell', row._indent ? 'memory-category-cell--indented' : ''],
      }, [
        h(NTag, {
          size: 'small',
          round: true,
          type: categoryTagType(row.card.category),
        }, () => row.card.category_label),
      ])
    },
  },
  {
    title: '范围',
    key: 'scope',
    width: 100,
    render: row => row._type === 'card'
      ? h(NTag, { size: 'small', round: true }, () => row.card.scope_label)
      : h('span'),
  },
  {
    title: '实体',
    key: 'scope_id',
    minWidth: 120,
    ellipsis: { tooltip: true },
    render: row => row._type === 'card'
      ? h('div', { class: 'memory-entity-cell' }, [
          h('strong', row.card.scope_id || 'global'),
          h('span', row.card.source),
        ])
      : h('span'),
  },
  {
    title: '内容',
    key: 'content',
    ellipsis: { tooltip: true },
    render: row => {
      if (row._type === 'series-header') {
        return h(NText, { depth: '3' }, () => '点击展开或折叠该系列卡片')
      }
      return h('div', { class: 'memory-content-cell' }, [
        h('p', { class: 'memory-content-cell__text' }, row.card.content || '空内容'),
        h('span', { class: 'memory-content-cell__meta' }, `优先级 ${row.card.priority}`),
      ])
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: row => row._type === 'card'
      ? h(NTag, {
          size: 'small',
          round: true,
          type: statusTagType(row.card.status),
        }, () => statusLabel(row.card.status))
      : h('span'),
  },
  {
    title: '可信度',
    key: 'confidence',
    width: 90,
    render: row => row._type === 'card'
      ? h(NText, {}, () => `${Math.round(row.card.confidence * 100)}%`)
      : h('span'),
  },
  {
    title: '更新时间',
    key: 'updated_at',
    width: 180,
    render: row => row._type === 'card'
      ? h(NText, { depth: '3' }, () => row.card.updated_at || row.card.created_at || '--')
      : h('span'),
  },
  {
    title: '',
    key: 'actions',
    width: 132,
    render: (row) => {
      if (row._type !== 'card') return h('span')
      return h('div', { class: 'memory-actions-cell' }, [
        h(NButton, {
          size: 'small',
          secondary: true,
          onClick: () => openEdit(row.card),
        }, () => '编辑'),
        h(NPopconfirm, {
          onPositiveClick: () => expireCard(row.card.card_id),
        }, {
          trigger: () => h(NButton, {
            size: 'small',
            type: 'error',
            secondary: true,
          }, () => '过期'),
          default: () => '确认将这张卡片标记为过期？',
        }),
      ])
    },
  },
]

onMounted(() => {
  void loadCards()
})

watch(seriesList, (list) => {
  const next = new Set<string>()
  for (const series of list) {
    if (!series.series_key.startsWith('food_served:') && !series.series_key.startsWith('food_pref:')) {
      next.add(series.series_id)
    }
  }
  expandedSeries.value = next
})

async function loadCards(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const params: Record<string, string | number> = { limit: 500 }
    if (filterScope.value) params.scope = filterScope.value
    if (filterScopeId.value.trim()) params.scope_id = filterScopeId.value.trim()

    const [cardResult, seriesResult] = await Promise.allSettled([
      api('/api/admin/memory/cards', { params }),
      api('/api/admin/memory/series'),
    ])

    let allCards: Card[] = []
    if (cardResult.status === 'fulfilled') {
      allCards = cardResult.value.cards || []
      total.value = cardResult.value.total || allCards.length
    } else {
      message.error('卡片列表加载失败')
    }

    if (seriesResult.status === 'fulfilled') {
      seriesList.value = seriesResult.value.series || []
    } else {
      seriesList.value = []
    }

    if (filterSeries.value === '__none__') {
      allCards = allCards.filter(card => !card.series_id)
    } else if (filterSeries.value) {
      allCards = allCards.filter(card => card.series_id === filterSeries.value)
    }

    cards.value = allCards
    total.value = allCards.length
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function resetFilters() {
  filterScope.value = ''
  filterScopeId.value = ''
  filterSeries.value = ''
  void loadCards()
}

function toggleSeries(seriesId: string) {
  const next = new Set(expandedSeries.value)
  if (next.has(seriesId)) next.delete(seriesId)
  else next.add(seriesId)
  expandedSeries.value = next
}

function openEdit(card?: Card) {
  isNew.value = !card
  editingCard.value = card || null
  editContent.value = card?.content || ''
  editCategory.value = card?.category || 'fact'
  editConfidence.value = card?.confidence ?? 0.7
  editPriority.value = card?.priority ?? 5
  editScope.value = (card?.scope as 'user' | 'group' | 'global') || ((filterScope.value as 'user' | 'group' | 'global') || 'user')
  editScopeId.value = card?.scope_id || filterScopeId.value || ''
  editSeriesId.value = card?.series_id || ''
  drawerVisible.value = true
}

async function saveCard() {
  const content = editContent.value.trim()
  const scopeId = editScopeId.value.trim()
  if (!content) {
    message.warning('请先填写卡片内容')
    return
  }
  if (editScope.value !== 'global' && !scopeId) {
    message.warning('私聊或群聊卡片必须填写实体 ID')
    return
  }

  saving.value = true
  try {
    if (isNew.value) {
      const data = await api('/api/admin/memory/cards', {
        method: 'POST',
        body: {
          content,
          category: editCategory.value,
          confidence: editConfidence.value,
          priority: editPriority.value,
          scope: editScope.value,
          scope_id: scopeId,
          series_id: editSeriesId.value || null,
        },
      })
      if (data.ok) {
        message.success('已创建卡片')
        drawerVisible.value = false
        await loadCards(true)
      } else {
        message.error(data.error || '创建失败')
      }
      return
    }

    const data = await api(`/api/admin/memory/cards/${editingCard.value!.card_id}`, {
      method: 'PATCH',
      body: {
        content,
        category: editCategory.value,
        confidence: editConfidence.value,
        priority: editPriority.value,
        scope: editScope.value,
        scope_id: scopeId,
        series_id: editSeriesId.value || null,
      },
    })
    if (data.ok) {
      message.success('已更新卡片')
      drawerVisible.value = false
      await loadCards(true)
    } else {
      message.error(data.error || '更新失败')
    }
  } catch {
    message.error('操作失败')
  } finally {
    saving.value = false
  }
}

async function expireCard(cardId: string) {
  try {
    const data = await api(`/api/admin/memory/cards/${cardId}/expire`, { method: 'POST' })
    if (data.ok) {
      message.success('已标记过期')
      await loadCards(true)
    } else {
      message.error(data.error || '操作失败')
    }
  } catch {
    message.error('操作失败')
  }
}

function categoryTagType(category: string) {
  if (category === 'boundary') return 'error'
  if (category === 'preference') return 'info'
  if (category === 'event') return 'warning'
  return 'default'
}

function statusTagType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'expired') return 'warning'
  if (status === 'superseded') return 'default'
  return 'default'
}

function statusLabel(status: string) {
  if (status === 'active') return '有效'
  if (status === 'expired') return '过期'
  if (status === 'superseded') return '已替代'
  return status || '未知'
}
</script>

<template>
  <AppPage
    title="记忆管理"
    eyebrow="Memory Ops"
    description="查看卡片记忆、系列分组和实体作用域，并对单张记忆进行精细化维护。"
  >
    <template #action>
      <div class="memory-page-actions">
        <NSpace align="center" :size="12">
          <NButton secondary @click="emit('change-view', 'browse')">
            返回实体浏览
          </NButton>
          <NButton secondary :loading="refreshing" @click="loadCards(true)">
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
            刷新
          </NButton>
          <NButton type="primary" @click="openEdit()">
            <template #icon>
              <NIcon :component="AddOutline" />
            </template>
            新建卡片
          </NButton>
        </NSpace>
      </div>
    </template>

    <div class="memory-metric-grid">
      <MetricCard
        title="当前卡片"
        :value="cards.length"
        hint="当前筛选结果中的卡片数"
        :icon="LayersOutline"
        accent="primary"
      />
      <MetricCard
        title="有效卡片"
        :value="activeCardCount"
        hint="状态为 active 的记忆卡"
        :icon="AlbumsOutline"
        accent="success"
      />
      <MetricCard
        title="系列数"
        :value="visibleSeriesCount"
        hint="当前结果中可见的系列分组"
        :icon="TimeOutline"
        accent="info"
      />
      <MetricCard
        title="实体数"
        :value="distinctEntityCount"
        :hint="expiredCardCount > 0 ? `${expiredCardCount} 张已过期` : '当前结果中没有过期卡片'"
        :icon="LayersOutline"
        accent="warning"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NSelect
          v-model:value="filterScope"
          :options="scopeOptions"
          class="memory-toolbar__scope"
        />
        <NInput
          v-model:value="filterScopeId"
          clearable
          placeholder="实体 ID"
          class="memory-toolbar__scope-id"
        />
        <NSelect
          v-model:value="filterSeries"
          :options="seriesOptions"
          clearable
          placeholder="按系列筛选"
          class="memory-toolbar__series"
        />
      </template>
      <template #right>
        <NButton secondary @click="loadCards(true)">
          查询
        </NButton>
        <NButton secondary @click="resetFilters">
          重置
        </NButton>
        <NTag size="small" round>
          总数 {{ total }}
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <NDataTable
        v-if="flatRows.length > 0"
        :columns="columns"
        :data="flatRows"
        :row-key="(row: FlatRow) => row._type === 'series-header' ? `series_${row.series_id}` : row.card.card_id"
        :bordered="false"
        size="small"
        class="memory-table"
        :max-height="580"
        :row-class-name="(row: FlatRow) => row._type === 'series-header' ? 'memory-series-header-row' : row.card.status === 'expired' ? 'memory-card-row--expired' : ''"
        :row-props="(row: FlatRow) => row._type === 'series-header' ? { style: 'cursor:pointer', onClick: () => toggleSeries(row.series_id) } : {}"
      />

      <EmptyState
        v-else
        title="当前没有匹配的记忆卡"
        description="尝试重置筛选条件，或者新建一张卡片开始维护。"
        :icon="LayersOutline"
      />
    </template>

    <NDrawer v-model:show="drawerVisible" :width="560">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Memory Card"
            :title="isNew ? '新建记忆卡片' : '编辑记忆卡片'"
            description="维护记忆卡的作用域、系列归属和长期可检索内容。"
          >
            <template #aside>
              <NTag size="small" round>
                {{ drawerSeriesLabel }}
              </NTag>
            </template>
          </AppDrawerHeader>
        </template>

        <AppDrawerLayout class="memory-drawer">
          <AppPanelSection eyebrow="Identity" title="作用域与归属">
            <div class="memory-drawer__grid">
              <div class="memory-drawer__field">
                <span>作用域</span>
                <NSelect v-model:value="editScope" :options="drawerScopeOptions" />
              </div>
              <div class="memory-drawer__field">
                <span>实体 ID</span>
                <NInput
                  v-model:value="editScopeId"
                  :placeholder="editScope === 'global' ? '全局卡片可留空' : '例如 QQ 号或群号'"
                />
              </div>
              <div class="memory-drawer__field memory-drawer__field--full">
                <span>所属系列</span>
                <NSelect
                  v-model:value="editSeriesId"
                  :options="drawerSeriesOptions"
                />
              </div>
            </div>
          </AppPanelSection>

          <AppPanelSection eyebrow="Content" title="卡片内容">
            <div class="memory-drawer__grid">
              <div class="memory-drawer__field">
                <span>分类</span>
                <NSelect v-model:value="editCategory" :options="categoryOptions" />
              </div>
              <div class="memory-drawer__field">
                <span>可信度</span>
                <NInputNumber
                  v-model:value="editConfidence"
                  :min="0"
                  :max="1"
                  :step="0.1"
                  class="memory-drawer__numeric"
                />
              </div>
              <div class="memory-drawer__field">
                <span>优先级</span>
                <NInputNumber
                  v-model:value="editPriority"
                  :min="1"
                  :max="10"
                  :step="1"
                  class="memory-drawer__numeric"
                />
              </div>
              <div class="memory-drawer__field memory-drawer__field--full">
                <span>内容</span>
                <NInput
                  v-model:value="editContent"
                  type="textarea"
                  :autosize="{ minRows: 4, maxRows: 10 }"
                  placeholder="记录一条可被长期检索和维护的记忆"
                />
              </div>
            </div>
          </AppPanelSection>

          <template #footer>
            <NButton secondary @click="drawerVisible = false">
              取消
            </NButton>
            <NButton type="primary" :loading="saving" @click="saveCard">
              {{ isNew ? '创建卡片' : '保存修改' }}
            </NButton>
          </template>
        </AppDrawerLayout>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.memory-page-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px 12px;
}

.memory-toolbar__scope {
  width: 132px;
}

.memory-toolbar__scope-id {
  width: min(180px, 100%);
}

.memory-toolbar__series {
  width: min(220px, 100%);
}

.memory-drawer__numeric {
  width: 100%;
}

.memory-view-toggle {
  display: inline-flex;
  gap: 8px;
}

.memory-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.memory-table:deep(.n-data-table-th) {
  background: var(--om-surface-2);
}

.memory-series-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.memory-series-row__title {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.memory-category-cell {
  display: flex;
  align-items: center;
}

.memory-category-cell--indented {
  padding-left: 24px;
}

.memory-entity-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.memory-entity-cell strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.memory-entity-cell span {
  color: var(--om-text-3);
  font-size: 12px;
}

.memory-content-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.memory-content-cell__text {
  margin: 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.7;
}

.memory-content-cell__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.memory-actions-cell {
  display: flex;
  gap: 8px;
}

.memory-table:deep(.memory-series-header-row td) {
  background: var(--om-surface-2);
  font-weight: 500;
}

.memory-table:deep(.memory-card-row--expired td) {
  opacity: 0.78;
}

.memory-drawer {
  display: grid;
  gap: 14px;
}

.memory-drawer__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.memory-drawer__field {
  display: grid;
  gap: 8px;
}

.memory-drawer__field span {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.memory-drawer__field--full {
  grid-column: 1 / -1;
}

@media (max-width: 1100px) {
  .memory-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .memory-page-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .memory-view-toggle {
    width: 100%;
  }

  .memory-metric-grid,
  .memory-drawer__grid {
    grid-template-columns: 1fr;
  }
}
</style>

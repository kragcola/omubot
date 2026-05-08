<script setup lang="ts">
import {
  AlbumsOutline,
  ArrowBackOutline,
  ChevronDownOutline,
  ChevronForwardOutline,
  LayersOutline,
  PeopleOutline,
  PersonOutline,
  RefreshOutline,
  SearchOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NInput,
  NSkeleton,
  NTag,
  NText,
} from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface Card {
  card_id: string
  category: string
  category_label: string
  scope: string
  scope_id: string
  content: string
  confidence: number
  status: string
  priority: number
  source: string
  series_id: string | null
  created_at: string
  updated_at: string
}

interface CardSeries {
  series_id: string
  series_key: string
  label: string
  card_count: number
  cards: Card[]
}

interface AffectionProfile {
  user_id: string
  score: number
  tier: string
  total_interactions: number
  daily_count: number
  custom_nickname: string
  preferred_suffix: string
  last_interaction: string
  group_nicknames?: Record<string, string>
}

type MemoryViewMode = 'manage' | 'browse'

withDefaults(defineProps<{
  activeView?: MemoryViewMode
}>(), {
  activeView: 'browse',
})

const emit = defineEmits<{
  (e: 'change-view', view: MemoryViewMode): void
}>()

const userEntities = ref<string[]>([])
const groupEntities = ref<string[]>([])
const loading = ref(true)
const refreshing = ref(false)

const selectedScope = ref('')
const selectedId = ref('')
const cards = ref<Card[]>([])
const seriesList = ref<CardSeries[]>([])
const cardsLoading = ref(false)
const affectionLoading = ref(false)
const affectionProfile = ref<AffectionProfile | null>(null)

const searchInput = ref('')
const searching = ref(false)
const searchFeedback = ref('')

const expandedSeries = ref<Set<string>>(new Set())

const CATEGORY_COLORS: Record<string, string> = {
  preference: '#f0a020',
  boundary: '#d03050',
  relationship: '#18a058',
  event: '#2080f0',
  promise: '#8a50e0',
  fact: '#607080',
  status: '#e07020',
}

const totalEntities = computed(() => userEntities.value.length + groupEntities.value.length)
const standaloneCards = computed(() => cards.value.filter(card => !card.series_id))
const secondaryMetricIcon = computed(() => selectedScope.value ? AlbumsOutline : PersonOutline)
const tertiaryMetricIcon = computed(() => selectedScope.value ? LayersOutline : PeopleOutline)
const latestUpdatedAt = computed(() => {
  const timestamps = [...cards.value]
    .map(card => card.updated_at)
    .filter(Boolean)
    .sort()
  const latest = timestamps[timestamps.length - 1]

  if (!latest) return '--'
  return formatDate(latest)
})

watch(seriesList, (list) => {
  expandedSeries.value = new Set(
    list
      .filter(series => !series.series_key.startsWith('food_served:') && !series.series_key.startsWith('food_pref:'))
      .map(series => series.series_id),
  )
})

onMounted(() => {
  void loadEntities()
})

async function loadEntities(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api('/api/admin/memos')
    userEntities.value = data.entities || []
    groupEntities.value = data.group_entities || []
  } catch (error) {
    console.error('Failed to load memos:', error)
    userEntities.value = []
    groupEntities.value = []
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function openEntity(scope: string, scopeId: string) {
  selectedScope.value = scope
  selectedId.value = scopeId
  cardsLoading.value = true
  cards.value = []
  seriesList.value = []
  searchFeedback.value = ''
  affectionProfile.value = null
  affectionLoading.value = scope === 'user'

  try {
    const [cardResult, seriesResult, affectionResult] = await Promise.allSettled([
      api('/api/admin/memos', { params: { scope, scope_id: scopeId, limit: 200 } }),
      api(`/api/admin/memos/${scope}/${scopeId}/series`),
      scope === 'user' ? api(`/api/admin/affection/${scopeId}`) : Promise.resolve(null),
    ])

    if (cardResult.status === 'fulfilled') {
      cards.value = cardResult.value.memos || []
    } else {
      console.error('Failed to load memos:', cardResult.reason)
    }

    if (seriesResult.status === 'fulfilled') {
      seriesList.value = seriesResult.value.series || []
    } else {
      console.error('Failed to load series:', seriesResult.reason)
    }

    if (scope === 'user') {
      if (affectionResult.status === 'fulfilled' && affectionResult.value) {
        affectionProfile.value = affectionResult.value as AffectionProfile
      } else if (affectionResult.status === 'rejected') {
        console.error('Failed to load affection summary:', affectionResult.reason)
      }
    }
  } catch (error) {
    console.error('Failed to load entity memos:', error)
  } finally {
    cardsLoading.value = false
    affectionLoading.value = false
  }
}

function goBack() {
  selectedScope.value = ''
  selectedId.value = ''
  cards.value = []
  seriesList.value = []
}

async function searchCards() {
  const scopeId = searchInput.value.trim()
  if (!scopeId) return

  searching.value = true
  searchFeedback.value = ''

  try {
    const data = await api('/api/admin/memory/cards', {
      params: { scope_id: scopeId, limit: 100 },
    })
    const results = data.cards || []

    if (results.length > 0) {
      await openEntity(results[0].scope, scopeId)
    } else {
      searchFeedback.value = '没有找到对应实体'
    }
  } catch (error) {
    console.error('Failed to search cards:', error)
    searchFeedback.value = '搜索失败'
  } finally {
    searching.value = false
  }
}

function toggleSeries(seriesId: string) {
  const next = new Set(expandedSeries.value)
  if (next.has(seriesId)) next.delete(seriesId)
  else next.add(seriesId)
  expandedSeries.value = next
}

function isExpanded(seriesId: string) {
  return expandedSeries.value.has(seriesId)
}

function scopeLabel(scope: string) {
  if (scope === 'group') return '群聊'
  if (scope === 'user') return '用户'
  return scope || '未知'
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value?.slice(0, 10) || '--'

  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function categoryStyle(category: string) {
  const color = CATEGORY_COLORS[category] || '#607080'
  return {
    backgroundColor: `${color}20`,
    color,
    borderColor: `${color}40`,
  }
}

function affectionTierLabel(value?: string) {
  if (value === 'close') return '亲密'
  if (value === 'friendly') return '友好'
  if (value === 'neutral') return '普通'
  return value || '未分级'
}
</script>

<template>
  <AppPage
    title="记忆管理"
    eyebrow="Memory Browser"
    description="按实体浏览派生记忆、系列分组和更新时间线，快速确认 Omubot 当前掌握的上下文。"
  >
    <template #action>
      <div class="memos-hero-actions">
        <NButton secondary @click="emit('change-view', 'manage')">
          打开卡片管理
        </NButton>
        <NButton v-if="selectedScope" secondary @click="goBack">
          <template #icon>
            <NIcon :component="ArrowBackOutline" />
          </template>
          返回实体列表
        </NButton>
        <NButton secondary :loading="selectedScope ? cardsLoading : refreshing" @click="selectedScope ? openEntity(selectedScope, selectedId) : loadEntities(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          {{ selectedScope ? '刷新当前实体' : '刷新实体列表' }}
        </NButton>
      </div>
    </template>

    <div class="memos-metric-grid">
      <MetricCard
        :title="selectedScope ? '当前卡片' : '实体总数'"
        :value="selectedScope ? cards.length : totalEntities"
        :hint="selectedScope ? '当前实体下可见的记忆卡总数' : '当前可浏览的用户与群聊实体总数'"
        :icon="LayersOutline"
        accent="primary"
      />
      <MetricCard
        :title="selectedScope ? '系列数' : '用户实体'"
        :value="selectedScope ? seriesList.length : userEntities.length"
        :hint="selectedScope ? '已归入系列的记忆分组数量' : '已记录过记忆的用户实体数'"
        :icon="secondaryMetricIcon"
        accent="success"
      />
      <MetricCard
        :title="selectedScope ? '独立卡片' : '群聊实体'"
        :value="selectedScope ? standaloneCards.length : groupEntities.length"
        :hint="selectedScope ? '未归入任何系列的卡片数量' : '已记录过记忆的群聊实体数'"
        :icon="tertiaryMetricIcon"
        accent="info"
      />
      <MetricCard
        :title="selectedScope ? '最近更新' : '当前视图'"
        :value="selectedScope ? latestUpdatedAt : (searchFeedback || '等待选择')"
        :hint="selectedScope ? '当前实体中最近一张卡片的更新时间' : '可直接按 User ID 或 Group ID 搜索实体'"
        :icon="TimeOutline"
        accent="warning"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="searchInput"
          clearable
          placeholder="输入 User ID / Group ID"
          style="width: min(260px, 100%)"
          @keyup.enter="searchCards"
        >
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>
        <NText v-if="searchFeedback && !selectedScope" depth="3">
          {{ searchFeedback }}
        </NText>
        <template v-else-if="selectedScope">
          <NTag round size="small" :type="selectedScope === 'user' ? 'info' : 'success'">
            {{ scopeLabel(selectedScope) }}
          </NTag>
          <NTag round size="small">
            {{ selectedId }}
          </NTag>
        </template>
      </template>

      <template #right>
        <NButton secondary :loading="searching" @click="searchCards">
          搜索实体
        </NButton>
        <NTag round size="small">
          {{ selectedScope ? `${cards.length} 张卡片` : `${totalEntities} 个实体` }}
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else-if="!selectedScope">
      <div class="memos-entity-grid">
        <AppCard bordered elevated class="memos-entity-panel">
          <div class="memos-panel__head">
            <div>
              <p class="memos-panel__eyebrow">
                User Entities
              </p>
              <h3 class="memos-panel__title">
                用户实体
              </h3>
            </div>
            <NTag round size="small" type="info">
              {{ userEntities.length }}
            </NTag>
          </div>

          <div v-if="userEntities.length > 0" class="memos-entity-list">
            <button
              v-for="uid in userEntities"
              :key="uid"
              type="button"
              class="memos-entity-item"
              @click="openEntity('user', uid)"
            >
              <div class="memos-entity-item__icon">
                <NIcon :component="PersonOutline" />
              </div>
              <div class="memos-entity-item__copy">
                <strong>{{ uid }}</strong>
                <span>查看该用户的系列与独立卡片</span>
              </div>
              <NIcon :component="ChevronForwardOutline" />
            </button>
          </div>

          <EmptyState
            v-else
            compact
            title="暂时没有用户实体"
            description="当前记忆存储中还没有可浏览的用户侧记忆。"
            :icon="PersonOutline"
          />
        </AppCard>

        <AppCard bordered elevated class="memos-entity-panel">
          <div class="memos-panel__head">
            <div>
              <p class="memos-panel__eyebrow">
                Group Entities
              </p>
              <h3 class="memos-panel__title">
                群聊实体
              </h3>
            </div>
            <NTag round size="small" type="success">
              {{ groupEntities.length }}
            </NTag>
          </div>

          <div v-if="groupEntities.length > 0" class="memos-entity-list">
            <button
              v-for="gid in groupEntities"
              :key="gid"
              type="button"
              class="memos-entity-item"
              @click="openEntity('group', gid)"
            >
              <div class="memos-entity-item__icon">
                <NIcon :component="PeopleOutline" />
              </div>
              <div class="memos-entity-item__copy">
                <strong>{{ gid }}</strong>
                <span>查看该群聊的系列与独立卡片</span>
              </div>
              <NIcon :component="ChevronForwardOutline" />
            </button>
          </div>

          <EmptyState
            v-else
            compact
            title="暂时没有群聊实体"
            description="当前记忆存储中还没有可浏览的群聊侧记忆。"
            :icon="PeopleOutline"
          />
        </AppCard>
      </div>
    </template>

    <template v-else>
      <NSkeleton v-if="cardsLoading" :repeat="8" text />

      <template v-else-if="cards.length === 0">
        <EmptyState
          title="当前实体没有记忆卡"
          description="这个实体已被找到，但当前没有可展示的记忆内容。"
          :icon="LayersOutline"
        />
      </template>

      <div v-else class="memos-detail">
        <AppCard bordered elevated class="memos-summary">
          <div class="memos-panel__head">
            <div>
              <p class="memos-panel__eyebrow">
                Entity Snapshot
              </p>
              <h3 class="memos-panel__title">
                {{ selectedId }}
              </h3>
            </div>
            <NTag round size="small" :type="selectedScope === 'user' ? 'info' : 'success'">
              {{ scopeLabel(selectedScope) }}
            </NTag>
          </div>

          <div class="memos-summary__stats">
            <div class="memos-summary__stat">
              <span>总卡片</span>
              <strong>{{ cards.length }}</strong>
            </div>
            <div class="memos-summary__stat">
              <span>系列分组</span>
              <strong>{{ seriesList.length }}</strong>
            </div>
            <div class="memos-summary__stat">
              <span>独立卡片</span>
              <strong>{{ standaloneCards.length }}</strong>
            </div>
            <div class="memos-summary__stat">
              <span>最近更新</span>
              <strong>{{ latestUpdatedAt }}</strong>
            </div>
          </div>

          <div v-if="selectedScope === 'user'" class="memos-relationship">
            <div class="memos-relationship__head">
              <strong>关系画像</strong>
              <NTag v-if="affectionProfile" round size="small" type="info">
                {{ affectionTierLabel(affectionProfile.tier) }}
              </NTag>
            </div>

            <NSkeleton v-if="affectionLoading" :repeat="2" text />

            <div v-else-if="affectionProfile" class="memos-relationship__grid">
              <div class="memos-summary__stat">
                <span>称呼</span>
                <strong>{{ affectionProfile.custom_nickname || '未设置' }}</strong>
              </div>
              <div class="memos-summary__stat">
                <span>后缀</span>
                <strong>{{ affectionProfile.preferred_suffix || '默认' }}</strong>
              </div>
              <div class="memos-summary__stat">
                <span>关系分数</span>
                <strong>{{ Number(affectionProfile.score || 0).toFixed(1) }}</strong>
              </div>
              <div class="memos-summary__stat">
                <span>总互动</span>
                <strong>{{ affectionProfile.total_interactions || 0 }}</strong>
              </div>
            </div>

            <p v-else class="memos-relationship__empty">
              当前没有额外关系画像，仍可继续通过记忆卡浏览这个用户的上下文。
            </p>
          </div>
        </AppCard>

        <AppCard v-if="seriesList.length > 0" bordered elevated class="memos-section">
          <div class="memos-panel__head">
            <div>
              <p class="memos-panel__eyebrow">
                Series
              </p>
              <h3 class="memos-panel__title">
                系列记忆
              </h3>
            </div>
            <NTag round size="small">
              {{ seriesList.length }} 组
            </NTag>
          </div>

          <div class="memos-series-list">
            <AppCard
              v-for="series in seriesList"
              :key="series.series_id"
              bordered
              embedded
              class="memos-series-card"
            >
              <button
                type="button"
                class="memos-series-card__toggle"
                @click="toggleSeries(series.series_id)"
              >
                <div class="memos-series-card__title-wrap">
                  <NIcon :component="isExpanded(series.series_id) ? ChevronDownOutline : ChevronForwardOutline" />
                  <strong>{{ series.label }}</strong>
                </div>
                <NTag round size="small" type="info">
                  {{ series.card_count }} 张
                </NTag>
              </button>

              <div v-if="isExpanded(series.series_id)" class="memos-card-list">
                <AppCard
                  v-for="card in series.cards"
                  :key="card.card_id"
                  bordered
                  class="memos-card-item"
                >
                  <div class="memos-card-item__head">
                    <div class="memos-card-item__tags">
                      <NTag size="small" :style="categoryStyle(card.category)">
                        {{ card.category_label }}
                      </NTag>
                      <NTag round size="small">
                        P{{ card.priority }}
                      </NTag>
                    </div>
                    <NText depth="3">
                      {{ formatDate(card.updated_at) }}
                    </NText>
                  </div>

                  <p class="memos-card-item__content">
                    {{ card.content }}
                  </p>

                  <div class="memos-card-item__meta">
                    <span>可信度 {{ (card.confidence * 100).toFixed(0) }}%</span>
                    <span v-if="card.source">来源 {{ card.source }}</span>
                  </div>
                </AppCard>
              </div>
            </AppCard>
          </div>
        </AppCard>

        <AppCard bordered elevated class="memos-section">
          <div class="memos-panel__head">
            <div>
              <p class="memos-panel__eyebrow">
                Standalone
              </p>
              <h3 class="memos-panel__title">
                独立卡片
              </h3>
            </div>
            <NTag round size="small">
              {{ standaloneCards.length }} 张
            </NTag>
          </div>

          <div v-if="standaloneCards.length > 0" class="memos-card-list">
            <AppCard
              v-for="card in standaloneCards"
              :key="card.card_id"
              bordered
              embedded
              class="memos-card-item"
            >
              <div class="memos-card-item__head">
                <div class="memos-card-item__tags">
                  <NTag size="small" :style="categoryStyle(card.category)">
                    {{ card.category_label }}
                  </NTag>
                  <NTag round size="small">
                    P{{ card.priority }}
                  </NTag>
                </div>
                <NText depth="3">
                  {{ formatDate(card.updated_at) }}
                </NText>
              </div>

              <p class="memos-card-item__content">
                {{ card.content }}
              </p>

              <div class="memos-card-item__meta">
                <span>可信度 {{ (card.confidence * 100).toFixed(0) }}%</span>
                <span v-if="card.source">来源 {{ card.source }}</span>
              </div>
            </AppCard>
          </div>

          <EmptyState
            v-else
            compact
            title="没有独立卡片"
            description="当前实体的卡片都已经归入系列分组。"
            :icon="LayersOutline"
          />
        </AppCard>
      </div>
    </template>
  </AppPage>
</template>

<style scoped>
.memos-hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.memos-view-toggle {
  display: inline-flex;
  gap: 8px;
}

.memos-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.memos-entity-grid,
.memos-detail {
  display: grid;
  gap: 16px;
}

.memos-entity-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.memos-entity-panel,
.memos-section,
.memos-summary {
  padding: 20px;
}

.memos-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.memos-panel__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.memos-panel__title {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.memos-entity-list,
.memos-series-list,
.memos-card-list {
  display: grid;
  gap: 12px;
}

.memos-entity-item,
.memos-series-card__toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.18s ease,
    transform 0.18s ease,
    background-color 0.18s ease;
}

.memos-entity-item:hover,
.memos-series-card__toggle:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  background: var(--om-surface-2);
}

.memos-entity-item__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: rgba(var(--primary-color), 0.12);
  color: rgb(var(--primary-color));
}

.memos-entity-item__copy {
  min-width: 0;
  flex: 1;
}

.memos-entity-item__copy strong {
  display: block;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.memos-entity-item__copy span {
  display: block;
  margin-top: 6px;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.memos-summary__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.memos-summary__stat {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 74%, transparent);
}

.memos-summary__stat span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.memos-summary__stat strong {
  display: block;
  margin-top: 8px;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.6;
}

.memos-relationship {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.memos-relationship__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.memos-relationship__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.memos-relationship__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.memos-relationship__empty {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
}

.memos-series-card {
  padding: 12px;
  border-radius: 18px;
}

.memos-series-card__toggle {
  padding: 12px 14px;
}

.memos-series-card__title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.memos-series-card__title-wrap strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.memos-card-item {
  padding: 16px;
  border-radius: 16px;
}

.memos-card-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.memos-card-item__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.memos-card-item__content {
  margin: 12px 0 0;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.memos-card-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
  color: var(--om-text-3);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .memos-metric-grid,
  .memos-summary__stats,
  .memos-relationship__grid,
  .memos-entity-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .memos-hero-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .memos-view-toggle {
    width: 100%;
  }

  .memos-metric-grid,
  .memos-summary__stats,
  .memos-relationship__grid,
  .memos-entity-grid {
    grid-template-columns: 1fr;
  }

  .memos-panel__head,
  .memos-card-item__head {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

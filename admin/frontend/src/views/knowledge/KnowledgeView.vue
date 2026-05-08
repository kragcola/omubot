<script setup lang="ts">
import {
  DocumentTextOutline,
  FlashOutline,
  LayersOutline,
  RefreshOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

type KnowledgeTab = 'sources' | 'search' | 'context' | 'metrics' | 'graph' | 'candidates'

interface KnowledgeStats {
  loaded?: boolean
  chunk_count?: number
  source_count?: number
  indexed_sources?: number
  skipped_sources?: number
  docs_dir?: string
  recursive?: boolean
  include?: string[]
  exclude?: string[]
  index_persisted?: boolean
  index_db_path?: string
}

interface KnowledgeSource {
  source: string
  path: string
  status: string
  chunk_count: number
  source_hash?: string
  skipped_reason?: string
}

interface KnowledgeResult {
  id?: string
  chunk_id?: string
  content: string
  source?: string
  title?: string
  score?: number
  metadata?: Record<string, unknown>
}

interface ContextHit {
  id: string
  type: 'memory_card' | 'doc_chunk' | 'graph_fact'
  content: string
  score: number
  source: string
  title?: string
  scope?: string
  scope_id?: string
  status?: string
  retriever?: string
  metadata?: Record<string, unknown>
}

interface ContextPack {
  text: string
  hits: ContextHit[]
  omitted_count: number
}

interface ContextMetricRecent {
  created_at?: number
  query?: string
  user_id?: string
  group_id?: string
  hit_count?: number
  pack_chars?: number
  duplicate_count?: number
  omitted_count?: number
  error?: string
}

interface ContextMetrics {
  total_queries: number
  miss_count: number
  miss_rate: number
  hit_count: number
  duplicate_hits: number
  duplicate_rate: number
  avg_pack_chars: number
  max_pack_chars: number
  omitted_total: number
  hit_type_counts: Record<string, number>
  hit_source_counts: Record<string, number>
  recent: ContextMetricRecent[]
}

interface GraphEntity {
  name: string
  fact_count: number
}

interface GraphRelationship {
  fact_id: string
  subject: string
  predicate: string
  object: string
  confidence: number
  status: string
  source: string
  scope?: string
  scope_id?: string
  supersedes?: string
  metadata?: Record<string, unknown>
  evidence?: Array<Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

interface GraphCandidate {
  candidate_id: string
  subject: string
  predicate: string
  object: string
  confidence: number
  status: string
  source: string
  evidence?: Record<string, unknown>
  created_at?: string
  updated_at?: string
  review_note?: string
}

const message = useMessage()

const activeTab = ref<KnowledgeTab>('sources')
const loading = ref(true)
const refreshing = ref(false)
const reindexing = ref(false)
const compatibilityWarning = ref('')
const graphUnsupported = ref(false)
const contextUnsupported = ref(false)

const available = ref(false)
const stats = ref<KnowledgeStats>({})
const sources = ref<KnowledgeSource[]>([])

const searchQ = ref('')
const searchResults = ref<KnowledgeResult[]>([])
const searching = ref(false)
const hasSearched = ref(false)
const lastSearchQ = ref('')

const contextQ = ref('')
const contextUserId = ref('')
const contextGroupId = ref('')
const contextPack = ref<ContextPack | null>(null)
const contextSearching = ref(false)
const hasContextSearched = ref(false)
const metricsLoading = ref(false)
const contextMetrics = ref<ContextMetrics | null>(null)

const graphEntities = ref<GraphEntity[]>([])
const graphRelationships = ref<GraphRelationship[]>([])
const graphScopeRisks = ref<GraphRelationship[]>([])
const graphLoading = ref(false)
const factBusy = ref('')
const factRollbackNotes = ref<Record<string, string>>({})
const supersedeDrafts = ref<Record<string, {
  subject: string
  predicate: string
  object: string
  note: string
}>>({})

const candidates = ref<GraphCandidate[]>([])
const candidateLoading = ref(false)
const candidateBusy = ref('')
const rejectNotes = ref<Record<string, string>>({})

const entryCount = computed(() => stats.value.chunk_count || 0)
const sourceCount = computed(() => stats.value.source_count || sources.value.length || 0)
const skippedCount = computed(() => stats.value.skipped_sources || sources.value.filter(source => source.status !== 'indexed').length)
const indexedCount = computed(() => stats.value.indexed_sources || sources.value.filter(source => source.status === 'indexed').length)
const pendingCount = computed(() => candidates.value.length)
const relationshipCount = computed(() => graphRelationships.value.length)
const scopeRiskCount = computed(() => graphScopeRisks.value.length)

const sourceSummary = computed(() => {
  if (!available.value) return '知识库插件未启用或运行时实例暂不可用。'
  if (!sourceCount.value) return '还没有索引到文档源，可以检查插件配置或执行重建索引。'
  return `已索引 ${indexedCount.value} 个来源，跳过 ${skippedCount.value} 个来源，共 ${entryCount.value} 个文档片段。`
})

const contextHits = computed(() => contextPack.value?.hits || [])
const recentMetricItems = computed(() => contextMetrics.value?.recent || [])

onMounted(() => {
  void loadAll()
})

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([
      loadStats(),
      loadSources(),
      loadGraph(),
      loadCandidates(),
      loadMetrics(),
    ])
  } finally {
    loading.value = false
  }
}

async function loadMetrics() {
  metricsLoading.value = true
  try {
    const data = await api('/api/admin/context/metrics', { params: { limit: 80 } })
    contextMetrics.value = data.metrics || null
  } catch (error) {
    if (isNotFound(error)) {
      contextUnsupported.value = true
      compatibilityWarning.value = '当前运行后端还没有上下文指标 API；请重建/重启 Bot 后再查看评测指标。'
      contextMetrics.value = null
      return
    }
    message.error('上下文指标加载失败')
    console.error('Failed to load context metrics:', error)
  } finally {
    metricsLoading.value = false
  }
}

async function refreshAll() {
  refreshing.value = true
  try {
    await loadAll()
    message.success('知识系统状态已刷新')
  } catch (error) {
    message.error('刷新知识系统失败')
    console.error('Failed to refresh knowledge console:', error)
  } finally {
    refreshing.value = false
  }
}

async function loadStats() {
  try {
    const data = await api('/api/admin/knowledge/stats')
    available.value = Boolean(data.available)
    stats.value = data.stats || {}
  } catch (error) {
    if (!isNotFound(error)) throw error
    compatibilityWarning.value = '当前运行后端还没有新版知识库 API，已降级为旧版统计；完整图谱和上下文调试需要重建/重启 Bot。'
    const data = await api('/api/admin/knowledge')
    available.value = Boolean(data.available ?? true)
    stats.value = {
      loaded: true,
      chunk_count: data.entry_count || 0,
      source_count: 0,
      indexed_sources: 0,
      skipped_sources: 0,
      docs_dir: 'docs',
    }
  }
}

async function loadSources() {
  try {
    const data = await api('/api/admin/knowledge/sources')
    if (typeof data.available === 'boolean') available.value = data.available
    sources.value = data.sources || []
  } catch (error) {
    if (!isNotFound(error)) throw error
    compatibilityWarning.value = '当前运行后端还没有新版知识库 API，文档源详情暂不可用；请重建/重启 Bot 后再查看。'
    sources.value = []
  }
}

async function reindex() {
  reindexing.value = true
  try {
    const data = await api('/api/admin/knowledge/reindex', { method: 'POST' })
    if (data.ok) {
      stats.value = data.stats || stats.value
      message.success(`索引已重建：${data.entry_count || 0} 个片段`)
      await loadSources()
    } else {
      message.error(`重建索引失败：${data.error || 'unknown'}`)
    }
  } catch (error) {
    if (isNotFound(error)) {
      compatibilityWarning.value = '当前运行后端还没有重建索引接口；请先重建/重启 Bot。'
      message.warning('当前后端不支持在线重建索引，请先重建/重启 Bot')
      return
    }
    message.error('重建索引失败')
    console.error('Failed to reindex knowledge:', error)
  } finally {
    reindexing.value = false
  }
}

async function searchKnowledge() {
  const query = searchQ.value.trim()
  if (!query) {
    searchResults.value = []
    hasSearched.value = false
    lastSearchQ.value = ''
    return
  }

  searching.value = true
  try {
    let data
    try {
      data = await api('/api/admin/knowledge/search', {
        params: { q: query, top_k: 20 },
      })
    } catch (error) {
      if (!isNotFound(error)) throw error
      compatibilityWarning.value = '当前运行后端还没有结构化搜索接口，已降级为旧版搜索结果。'
      data = await api('/api/admin/knowledge', {
        params: { q: query, top_k: 20 },
      })
    }
    searchResults.value = data.results || []
    hasSearched.value = true
    lastSearchQ.value = query
  } catch (error) {
    message.error('知识库搜索失败')
    console.error('Knowledge search failed:', error)
  } finally {
    searching.value = false
  }
}

async function debugContext() {
  const query = contextQ.value.trim()
  if (!query) {
    contextPack.value = null
    hasContextSearched.value = false
    return
  }

  contextSearching.value = true
  try {
    const params: Record<string, string | number> = {
      q: query,
      top_k: 12,
      max_chars: 3200,
    }
    if (contextUserId.value.trim()) params.user_id = contextUserId.value.trim()
    if (contextGroupId.value.trim()) params.group_id = contextGroupId.value.trim()
    const data = await api('/api/admin/context/search', { params })
    contextPack.value = data.pack || { text: '', hits: [], omitted_count: 0 }
    hasContextSearched.value = true
  } catch (error) {
    if (isNotFound(error)) {
      contextUnsupported.value = true
      compatibilityWarning.value = '当前运行后端还没有 Context 调试接口；请重建/重启 Bot 后再使用上下文调试。'
      contextPack.value = null
      hasContextSearched.value = true
      message.warning('当前后端不支持上下文调试，请先重建/重启 Bot')
      return
    }
    message.error('上下文调试失败')
    console.error('Context debug failed:', error)
  } finally {
    contextSearching.value = false
  }
}

async function loadGraph() {
  graphLoading.value = true
  try {
    const [entitiesData, relationshipsData, scopeRiskData] = await Promise.all([
      api('/api/admin/knowledge/graph/entities', { params: { limit: 80 } }),
      api('/api/admin/knowledge/graph/relationships', { params: { limit: 120 } }),
      api('/api/admin/knowledge/graph/scope-risks', { params: { limit: 80 } }).catch((error) => {
        if (isNotFound(error)) return { available: false, relationships: [] }
        throw error
      }),
    ])
    graphEntities.value = entitiesData.entities || []
    graphRelationships.value = relationshipsData.relationships || []
    graphScopeRisks.value = scopeRiskData.relationships || []
    syncSupersedeDrafts()
    graphUnsupported.value = !entitiesData.available && !relationshipsData.available
  } catch (error) {
    if (isNotFound(error)) {
      graphUnsupported.value = true
      compatibilityWarning.value = '当前运行后端还没有图谱 API；请重建/重启 Bot 后再查看图谱。'
      graphEntities.value = []
      graphRelationships.value = []
      graphScopeRisks.value = []
      return
    }
    message.error('图谱信息加载失败')
    console.error('Failed to load knowledge graph:', error)
  } finally {
    graphLoading.value = false
  }
}

async function rollbackRelationship(rel: GraphRelationship) {
  factBusy.value = rel.fact_id
  try {
    const data = await api(`/api/admin/knowledge/graph/relationships/${rel.fact_id}/rollback`, {
      method: 'POST',
      params: { note: factRollbackNotes.value[rel.fact_id] || '' },
    })
    if (!data.ok) {
      message.error(data.error || '事实回滚失败')
      return
    }
    message.success('事实已回滚')
    await loadGraph()
  } catch (error) {
    message.error('事实回滚失败')
    console.error('Failed to rollback graph relationship:', error)
  } finally {
    factBusy.value = ''
  }
}

async function supersedeRelationship(rel: GraphRelationship) {
  const draft = supersedeDrafts.value[rel.fact_id]
  if (!draft || !draft.subject.trim() || !draft.predicate.trim() || !draft.object.trim()) {
    message.warning('请填写完整的新三元组')
    return
  }
  factBusy.value = rel.fact_id
  try {
    const data = await api(`/api/admin/knowledge/graph/relationships/${rel.fact_id}/supersede`, {
      method: 'POST',
      body: {
        subject: draft.subject.trim(),
        predicate: draft.predicate.trim(),
        object: draft.object.trim(),
        confidence: Math.max(0.6, rel.confidence || 0.85),
        source: 'admin',
        note: draft.note.trim(),
      },
    })
    if (!data.ok) {
      message.error(data.error || '事实取代失败')
      return
    }
    message.success('事实已取代')
    await loadGraph()
  } catch (error) {
    message.error('事实取代失败')
    console.error('Failed to supersede graph relationship:', error)
  } finally {
    factBusy.value = ''
  }
}

async function loadCandidates() {
  candidateLoading.value = true
  try {
    const data = await api('/api/admin/knowledge/graph/candidates', {
      params: { status: 'pending', limit: 120 },
    })
    candidates.value = data.candidates || []
  } catch (error) {
    if (isNotFound(error)) {
      graphUnsupported.value = true
      compatibilityWarning.value = '当前运行后端还没有图谱候选 API；请重建/重启 Bot 后再查看候选队列。'
      candidates.value = []
      return
    }
    message.error('候选队列加载失败')
    console.error('Failed to load graph candidates:', error)
  } finally {
    candidateLoading.value = false
  }
}

async function approveCandidate(candidate: GraphCandidate) {
  candidateBusy.value = candidate.candidate_id
  try {
    const data = await api(`/api/admin/knowledge/graph/candidates/${candidate.candidate_id}/approve`, {
      method: 'POST',
    })
    if (!data.ok) {
      message.error(data.error || '候选通过失败')
      return
    }
    message.success('候选已通过并写入图谱')
    await Promise.all([loadCandidates(), loadGraph()])
  } catch (error) {
    message.error('候选通过失败')
    console.error('Failed to approve graph candidate:', error)
  } finally {
    candidateBusy.value = ''
  }
}

async function rejectCandidate(candidate: GraphCandidate) {
  candidateBusy.value = candidate.candidate_id
  try {
    const data = await api(`/api/admin/knowledge/graph/candidates/${candidate.candidate_id}/reject`, {
      method: 'POST',
      params: { note: rejectNotes.value[candidate.candidate_id] || '' },
    })
    if (!data.ok) {
      message.error('候选拒绝失败')
      return
    }
    message.success('候选已拒绝')
    await loadCandidates()
  } catch (error) {
    message.error('候选拒绝失败')
    console.error('Failed to reject graph candidate:', error)
  } finally {
    candidateBusy.value = ''
  }
}

function scoreText(value?: number) {
  if (typeof value !== 'number') return '--'
  return value.toFixed(value >= 10 ? 1 : 3)
}

function percentText(value?: number) {
  if (typeof value !== 'number') return '--'
  return `${Math.round(value * 100)}%`
}

function numberText(value?: number) {
  if (typeof value !== 'number') return '--'
  return Math.round(value).toLocaleString()
}

function sourceStatusType(status: string) {
  return status === 'indexed' ? 'success' : 'warning'
}

function hitTypeLabel(type: string) {
  if (type === 'memory_card') return '记忆卡片'
  if (type === 'doc_chunk') return '文档片段'
  return '图谱事实'
}

function hitTypeTag(type: ContextHit['type']) {
  if (type === 'memory_card') return 'success'
  if (type === 'doc_chunk') return 'info'
  return 'warning'
}

function shortHash(value?: string) {
  if (!value) return '--'
  return value.slice(0, 12)
}

function evidenceText(candidate: GraphCandidate) {
  const evidence = candidate.evidence || {}
  const id = evidence.id || evidence.card_id || evidence.chunk_id || ''
  const quote = evidence.quote ? ` · ${String(evidence.quote).slice(0, 80)}` : ''
  return id ? `${String(id)}${quote}` : '未记录证据'
}

function relationshipEvidenceText(rel: GraphRelationship) {
  const evidence = rel.evidence || []
  if (!evidence.length) return '未记录证据'
  return evidence
    .map((item) => {
      const id = item.id || item.card_id || item.chunk_id || ''
      const quote = item.quote ? ` · ${String(item.quote).slice(0, 90)}` : ''
      return id ? `${String(id)}${quote}` : quote.replace(/^ · /, '')
    })
    .filter(Boolean)
    .join(' / ') || '未记录证据'
}

function relationshipScopeText(rel: GraphRelationship) {
  const scope = rel.scope || 'global'
  const scopeId = rel.scope_id || 'global'
  if (scope === 'user') return `用户 ${scopeId}`
  if (scope === 'group') return `群 ${scopeId}`
  return '全局'
}

function metricRatioEntries(data?: Record<string, number>) {
  return Object.entries(data || {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, 8)
}

function syncSupersedeDrafts() {
  const next: typeof supersedeDrafts.value = {}
  for (const rel of graphRelationships.value) {
    const previous = supersedeDrafts.value[rel.fact_id]
    next[rel.fact_id] = previous || {
      subject: rel.subject,
      predicate: rel.predicate,
      object: rel.object,
      note: '',
    }
  }
  supersedeDrafts.value = next
}

function isNotFound(error: unknown) {
  return (error as { response?: { status?: number } })?.response?.status === 404
}
</script>

<template>
  <AppPage
    title="知识库"
    eyebrow="Knowledge Console"
    description="管理文档源、核对检索命中，并调试本轮对话最终会引用哪些记忆、文档和图谱事实。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag round size="small" :type="available ? 'success' : 'warning'">
          {{ available ? '运行中' : '未启用' }}
        </NTag>
        <NButton secondary :loading="refreshing" @click="refreshAll">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <NButton type="primary" secondary :loading="reindexing" @click="reindex">
          重建索引
        </NButton>
      </NSpace>
    </template>

    <NSkeleton v-if="loading" :repeat="5" text />

    <template v-else>
      <NAlert
        v-if="compatibilityWarning"
        type="warning"
        class="knowledge-compat-alert"
        :show-icon="false"
      >
        {{ compatibilityWarning }}
      </NAlert>

      <AppCard bordered elevated class="knowledge-hero">
        <div class="knowledge-hero__main">
          <div>
            <p class="knowledge-eyebrow">
              Context Knowledge System
            </p>
            <h3>{{ sourceSummary }}</h3>
            <p>
              CardStore 仍是生产记忆权威来源；这里负责文档知识、上下文调试和派生图谱治理。
            </p>
          </div>
          <div class="knowledge-hero__badges">
            <NTag round size="small">
              目录 {{ stats.docs_dir || 'docs' }}
            </NTag>
            <NTag round size="small" :type="stats.recursive === false ? 'warning' : 'info'">
              {{ stats.recursive === false ? '仅一级目录' : '递归扫描' }}
            </NTag>
            <NTag round size="small" :type="stats.index_persisted ? 'success' : 'default'">
              {{ stats.index_persisted ? 'SQLite 索引' : '内存索引' }}
            </NTag>
          </div>
        </div>

        <div class="knowledge-status-grid">
          <div class="knowledge-status">
            <span>文档片段</span>
            <strong>{{ entryCount }}</strong>
          </div>
          <div class="knowledge-status">
            <span>文档源</span>
            <strong>{{ sourceCount }}</strong>
          </div>
          <div class="knowledge-status" :class="{ 'knowledge-status--warn': skippedCount > 0 }">
            <span>跳过源</span>
            <strong>{{ skippedCount }}</strong>
          </div>
          <div class="knowledge-status">
            <span>图谱事实</span>
            <strong>{{ relationshipCount }}</strong>
          </div>
          <div class="knowledge-status" :class="{ 'knowledge-status--warn': pendingCount > 0 }">
            <span>候选待审</span>
            <strong>{{ pendingCount }}</strong>
          </div>
          <div class="knowledge-status" :class="{ 'knowledge-status--warn': scopeRiskCount > 0 }">
            <span>作用域待查</span>
            <strong>{{ scopeRiskCount }}</strong>
          </div>
        </div>
      </AppCard>

      <NTabs v-model:value="activeTab" type="segment" animated class="knowledge-tabs">
        <NTabPane name="sources" tab="文档源">
          <PageToolbar class="mb-16">
            <template #left>
              <span class="knowledge-toolbar__title">索引来源</span>
              <span class="knowledge-toolbar__hint">确认哪些 Markdown 文件已进入知识库，哪些被跳过。</span>
            </template>
            <template #right>
              <NButton secondary :loading="reindexing" @click="reindex">
                重新扫描
              </NButton>
            </template>
          </PageToolbar>

          <div v-if="sources.length" class="source-grid">
            <AppCard
              v-for="source in sources"
              :key="source.source"
              bordered
              embedded
              class="source-card"
            >
              <div class="source-card__head">
                <div>
                  <strong>{{ source.source }}</strong>
                  <span>{{ source.path }}</span>
                </div>
                <NTag round size="small" :type="sourceStatusType(source.status)">
                  {{ source.status === 'indexed' ? '已索引' : '已跳过' }}
                </NTag>
              </div>
              <div class="source-card__meta">
                <span>{{ source.chunk_count }} 个片段</span>
                <span>hash {{ shortHash(source.source_hash) }}</span>
              </div>
              <p v-if="source.skipped_reason" class="source-card__reason">
                跳过原因：{{ source.skipped_reason }}
              </p>
            </AppCard>
          </div>

          <EmptyState
            v-else
            title="还没有文档源"
            description="知识库未启用、目录为空，或当前运行实例还没有完成索引。"
            :icon="LayersOutline"
          />
        </NTabPane>

        <NTabPane name="search" tab="搜索核对">
          <PageToolbar class="mb-16">
            <template #left>
              <NInput
                v-model:value="searchQ"
                clearable
                placeholder="输入关键词或问题，核对文档 chunk 命中"
                class="knowledge-query-input"
                @keyup.enter="searchKnowledge"
              />
            </template>
            <template #right>
              <NButton v-if="hasSearched" secondary @click="searchQ = ''; searchResults = []; hasSearched = false">
                清除
              </NButton>
              <NButton type="primary" :loading="searching" @click="searchKnowledge">
                搜索文档
              </NButton>
            </template>
          </PageToolbar>

          <NSpin :show="searching">
            <div v-if="!hasSearched" class="knowledge-empty-panel">
              <EmptyState
                title="输入一句话开始核对"
                description="这里只检查文档知识库命中，不包含记忆卡片或图谱事实。"
                :icon="DocumentTextOutline"
              />
            </div>
            <div v-else-if="searchResults.length === 0" class="knowledge-empty-panel">
              <EmptyState
                title="没有命中文档片段"
                :description="`“${lastSearchQ}” 没有命中知识库，可以换更短的词或检查文档源。`"
                :icon="FlashOutline"
              />
            </div>
            <div v-else class="result-list">
              <AppCard
                v-for="(result, index) in searchResults"
                :key="result.chunk_id || result.id || `${result.source}-${index}`"
                bordered
                embedded
                class="result-card"
              >
                <div class="result-card__head">
                  <div>
                    <strong>{{ result.title || result.source || `结果 ${index + 1}` }}</strong>
                    <span>{{ result.chunk_id || result.id || result.source }}</span>
                  </div>
                  <NTag round size="small" type="info">
                    score {{ scoreText(result.score) }}
                  </NTag>
                </div>
                <p>{{ result.content }}</p>
              </AppCard>
            </div>
          </NSpin>
        </NTabPane>

        <NTabPane name="context" tab="上下文调试">
          <PageToolbar class="mb-16">
            <template #left>
              <NInput
                v-model:value="contextQ"
                clearable
                placeholder="输入本轮用户消息，查看 memory/doc/graph 最终命中"
                class="context-query-input"
                @keyup.enter="debugContext"
              />
              <NInput
                v-model:value="contextUserId"
                clearable
                placeholder="用户 ID，可选"
                class="context-id-input"
              />
              <NInput
                v-model:value="contextGroupId"
                clearable
                placeholder="群 ID，可选"
                class="context-id-input"
              />
            </template>
            <template #right>
              <NButton type="primary" :loading="contextSearching" @click="debugContext">
                调试上下文
              </NButton>
            </template>
          </PageToolbar>

          <NSpin :show="contextSearching">
            <div v-if="!hasContextSearched" class="knowledge-empty-panel">
              <EmptyState
                title="还没有调试上下文"
                description="输入一句真实聊天内容，可以看到统一上下文会引用哪些记忆卡片、文档片段和图谱事实。"
                :icon="LayersOutline"
              />
            </div>

            <div v-else-if="contextUnsupported" class="knowledge-empty-panel">
              <EmptyState
                title="当前后端还没有上下文调试接口"
                description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
                :icon="FlashOutline"
              />
            </div>

            <div v-else class="context-layout">
              <AppCard bordered elevated class="context-pack-card">
                <div class="section-head">
                  <div>
                    <p class="knowledge-eyebrow">Prompt Pack</p>
                    <h3>最终打包文本</h3>
                  </div>
                  <NTag round size="small">
                    省略 {{ contextPack?.omitted_count || 0 }} 条
                  </NTag>
                </div>
                <pre v-if="contextPack?.text" class="context-pack">{{ contextPack.text }}</pre>
                <EmptyState
                  v-else
                  compact
                  title="没有可注入上下文"
                  description="这次查询没有命中可打包内容。"
                  :icon="FlashOutline"
                />
              </AppCard>

              <div class="context-hit-list">
                <AppCard
                  v-for="hit in contextHits"
                  :key="`${hit.type}-${hit.id}`"
                  bordered
                  embedded
                  class="context-hit"
                >
                  <div class="context-hit__head">
                    <div>
                      <strong>{{ hit.title || hit.source || hit.id }}</strong>
                      <span>{{ hit.id }}</span>
                    </div>
                    <NSpace :size="6">
                      <NTag round size="small" :type="hitTypeTag(hit.type)">
                        {{ hitTypeLabel(hit.type) }}
                      </NTag>
                      <NTag round size="small">
                        {{ scoreText(hit.score) }}
                      </NTag>
                    </NSpace>
                  </div>
                  <p>{{ hit.content }}</p>
                  <div class="context-hit__meta">
                    <span>{{ hit.scope || 'global' }}/{{ hit.scope_id || 'global' }}</span>
                    <span>{{ hit.retriever || 'retriever' }}</span>
                    <span>{{ hit.source }}</span>
                  </div>
                </AppCard>
              </div>
            </div>
          </NSpin>
        </NTabPane>

        <NTabPane name="metrics" tab="评测指标">
          <PageToolbar class="mb-16">
            <template #left>
              <span class="knowledge-toolbar__title">上下文质量指标</span>
              <span class="knowledge-toolbar__hint">来自最近统一上下文检索，帮助观察 miss、重复和 Prompt pack 长度。</span>
            </template>
            <template #right>
              <NButton secondary :loading="metricsLoading" @click="loadMetrics">
                刷新指标
              </NButton>
            </template>
          </PageToolbar>

          <NSpin :show="metricsLoading">
            <div v-if="contextMetrics" class="metrics-layout">
              <div class="metrics-grid">
                <AppCard bordered embedded class="metric-mini-card">
                  <span>最近查询</span>
                  <strong>{{ contextMetrics.total_queries }}</strong>
                </AppCard>
                <AppCard bordered embedded class="metric-mini-card">
                  <span>Miss 率</span>
                  <strong>{{ percentText(contextMetrics.miss_rate) }}</strong>
                </AppCard>
                <AppCard bordered embedded class="metric-mini-card">
                  <span>平均 Pack</span>
                  <strong>{{ numberText(contextMetrics.avg_pack_chars) }}</strong>
                </AppCard>
                <AppCard bordered embedded class="metric-mini-card">
                  <span>最大 Pack</span>
                  <strong>{{ numberText(contextMetrics.max_pack_chars) }}</strong>
                </AppCard>
                <AppCard bordered embedded class="metric-mini-card">
                  <span>重复率</span>
                  <strong>{{ percentText(contextMetrics.duplicate_rate) }}</strong>
                </AppCard>
                <AppCard bordered embedded class="metric-mini-card">
                  <span>省略命中</span>
                  <strong>{{ contextMetrics.omitted_total }}</strong>
                </AppCard>
              </div>

              <div class="metrics-columns">
                <AppCard bordered elevated class="metrics-panel">
                  <div class="section-head">
                    <div>
                      <p class="knowledge-eyebrow">Sources</p>
                      <h3>命中来源</h3>
                    </div>
                  </div>
                  <div v-if="metricRatioEntries(contextMetrics.hit_source_counts).length" class="metric-ratio-list">
                    <div
                      v-for="[source, count] in metricRatioEntries(contextMetrics.hit_source_counts)"
                      :key="source"
                      class="metric-ratio-row"
                    >
                      <span>{{ source || 'unknown' }}</span>
                      <strong>{{ count }}</strong>
                    </div>
                  </div>
                  <EmptyState
                    v-else
                    compact
                    title="暂无来源命中"
                    description="还没有最近上下文检索记录。"
                    :icon="FlashOutline"
                  />
                </AppCard>

                <AppCard bordered elevated class="metrics-panel">
                  <div class="section-head">
                    <div>
                      <p class="knowledge-eyebrow">Types</p>
                      <h3>命中类型</h3>
                    </div>
                  </div>
                  <div v-if="metricRatioEntries(contextMetrics.hit_type_counts).length" class="metric-ratio-list">
                    <div
                      v-for="[type, count] in metricRatioEntries(contextMetrics.hit_type_counts)"
                      :key="type"
                      class="metric-ratio-row"
                    >
                      <span>{{ hitTypeLabel(type) }}</span>
                      <strong>{{ count }}</strong>
                    </div>
                  </div>
                  <EmptyState
                    v-else
                    compact
                    title="暂无类型命中"
                    description="还没有最近上下文检索记录。"
                    :icon="FlashOutline"
                  />
                </AppCard>
              </div>

              <div class="recent-context-list">
                <AppCard
                  v-for="item in recentMetricItems"
                  :key="`${item.created_at}-${item.query}`"
                  bordered
                  embedded
                  class="recent-context-card"
                >
                  <div class="recent-context-card__main">
                    <strong>{{ item.query || '空查询' }}</strong>
                    <span>{{ item.group_id ? `群 ${item.group_id}` : item.user_id ? `用户 ${item.user_id}` : '全局' }}</span>
                  </div>
                  <div class="relationship-card__meta">
                    <NTag round size="small" :type="item.hit_count ? 'success' : 'warning'">
                      {{ item.hit_count || 0 }} 命中
                    </NTag>
                    <span>pack {{ item.pack_chars || 0 }}</span>
                    <span>重复 {{ item.duplicate_count || 0 }}</span>
                    <span>省略 {{ item.omitted_count || 0 }}</span>
                  </div>
                </AppCard>
              </div>
            </div>
            <EmptyState
              v-else
              title="暂无上下文指标"
              description="先在“上下文调试”输入一条消息，或等待 Bot 真实对话产生检索记录。"
              :icon="FlashOutline"
            />
          </NSpin>
        </NTabPane>

        <NTabPane name="graph" tab="图谱关系">
          <PageToolbar class="mb-16">
            <template #left>
              <span class="knowledge-toolbar__title">已生效事实</span>
              <span class="knowledge-toolbar__hint">图谱是派生事实层，可重建、可回滚，不替代记忆卡片。</span>
            </template>
            <template #right>
              <NButton secondary :loading="graphLoading" @click="loadGraph">
                刷新图谱
              </NButton>
            </template>
          </PageToolbar>

          <NSpin :show="graphLoading">
            <div class="graph-layout">
              <AppCard bordered elevated class="graph-entities">
                <div class="section-head">
                  <div>
                    <p class="knowledge-eyebrow">Entities</p>
                    <h3>实体</h3>
                  </div>
                  <NTag round size="small">
                    {{ graphEntities.length }} 个
                  </NTag>
                </div>
                <div v-if="graphEntities.length" class="entity-list">
                  <div v-for="entity in graphEntities" :key="entity.name" class="entity-row">
                    <span>{{ entity.name }}</span>
                    <NTag round size="small">
                      {{ entity.fact_count }} 条
                    </NTag>
                  </div>
                </div>
                <EmptyState
                  v-else
                  compact
                  title="暂无实体"
                  description="通过候选审核或后续自动抽取后会出现实体。"
                  :icon="LayersOutline"
                />
              </AppCard>

              <div class="relationship-list">
                <EmptyState
                  v-if="graphUnsupported"
                  title="当前后端还没有图谱接口"
                  description="新版前端已经加载，但运行容器仍是旧后端。请重建/重启 Bot 后再查看图谱关系。"
                  :icon="FlashOutline"
                />
                <NAlert
                  v-if="!graphUnsupported && graphScopeRisks.length"
                  type="warning"
                  :show-icon="false"
                  class="graph-scope-risk"
                >
                  <div class="graph-scope-risk__head">
                    <strong>发现 {{ graphScopeRisks.length }} 条历史全局事实需要复核</strong>
                    <span>这些事实带有记忆卡片证据，但缺少用户/群作用域，可能来自旧版本迁移。确认不该全局可见时，请回滚事实。</span>
                  </div>
                  <div class="graph-scope-risk__list">
                    <div
                      v-for="rel in graphScopeRisks.slice(0, 5)"
                      :key="`risk-${rel.fact_id}`"
                      class="graph-scope-risk__item"
                    >
                      <span>{{ rel.subject }} {{ rel.predicate }} {{ rel.object }}</span>
                      <NButton
                        size="tiny"
                        secondary
                        type="warning"
                        :loading="factBusy === rel.fact_id"
                        @click="rollbackRelationship(rel)"
                      >
                        回滚
                      </NButton>
                    </div>
                  </div>
                </NAlert>
                <AppCard
                  v-for="rel in graphRelationships"
                  :key="rel.fact_id"
                  bordered
                  embedded
                  class="relationship-card"
                >
                  <div class="relationship-card__triple">
                    <strong>{{ rel.subject }}</strong>
                    <span>{{ rel.predicate }}</span>
                    <strong>{{ rel.object }}</strong>
                  </div>
                  <p class="relationship-card__evidence">
                    {{ relationshipEvidenceText(rel) }}
                  </p>
                  <div class="relationship-card__meta">
                    <NTag round size="small" type="success">
                      {{ percentText(rel.confidence) }}
                    </NTag>
                    <span>{{ rel.source }}</span>
                    <span>{{ relationshipScopeText(rel) }}</span>
                    <span>{{ rel.fact_id }}</span>
                    <span v-if="rel.supersedes">取代 {{ rel.supersedes }}</span>
                  </div>
                  <div
                    v-if="supersedeDrafts[rel.fact_id]"
                    class="relationship-card__governance"
                  >
                    <div class="relationship-card__rollback">
                      <NInput
                        v-model:value="factRollbackNotes[rel.fact_id]"
                        clearable
                        placeholder="回滚备注，可选"
                      />
                      <NButton
                        secondary
                        type="warning"
                        :loading="factBusy === rel.fact_id"
                        @click="rollbackRelationship(rel)"
                      >
                        回滚事实
                      </NButton>
                    </div>
                    <div class="relationship-card__supersede">
                      <NInput
                        v-model:value="supersedeDrafts[rel.fact_id].subject"
                        placeholder="主体"
                      />
                      <NInput
                        v-model:value="supersedeDrafts[rel.fact_id].predicate"
                        placeholder="关系"
                      />
                      <NInput
                        v-model:value="supersedeDrafts[rel.fact_id].object"
                        placeholder="客体"
                      />
                      <NInput
                        v-model:value="supersedeDrafts[rel.fact_id].note"
                        placeholder="取代说明，可选"
                      />
                      <NButton
                        type="primary"
                        secondary
                        :loading="factBusy === rel.fact_id"
                        @click="supersedeRelationship(rel)"
                      >
                        取代事实
                      </NButton>
                    </div>
                  </div>
                </AppCard>
                <EmptyState
                  v-if="!graphUnsupported && graphRelationships.length === 0"
                  title="暂无图谱事实"
                  description="当前图谱底座已就绪，但还没有 active fact。"
                  :icon="DocumentTextOutline"
                />
              </div>
            </div>
          </NSpin>
        </NTabPane>

        <NTabPane name="candidates" tab="候选队列">
          <PageToolbar class="mb-16">
            <template #left>
              <span class="knowledge-toolbar__title">待审核候选</span>
              <span class="knowledge-toolbar__hint">中置信事实进入这里，人工通过后才写入图谱。</span>
            </template>
            <template #right>
              <NButton secondary :loading="candidateLoading" @click="loadCandidates">
                刷新候选
              </NButton>
            </template>
          </PageToolbar>

          <NSpin :show="candidateLoading">
            <div v-if="candidates.length" class="candidate-list">
              <AppCard
                v-for="candidate in candidates"
                :key="candidate.candidate_id"
                bordered
                embedded
                class="candidate-card"
              >
                <div class="candidate-card__body">
                  <div>
                    <div class="relationship-card__triple">
                      <strong>{{ candidate.subject }}</strong>
                      <span>{{ candidate.predicate }}</span>
                      <strong>{{ candidate.object }}</strong>
                    </div>
                    <p>{{ evidenceText(candidate) }}</p>
                    <div class="relationship-card__meta">
                      <NTag round size="small" type="warning">
                        {{ percentText(candidate.confidence) }}
                      </NTag>
                      <span>{{ candidate.source }}</span>
                      <span>{{ candidate.candidate_id }}</span>
                    </div>
                  </div>
                  <div class="candidate-card__actions">
                    <NInput
                      v-model:value="rejectNotes[candidate.candidate_id]"
                      clearable
                      placeholder="拒绝备注，可选"
                    />
                    <NSpace justify="end" :size="8">
                      <NButton
                        secondary
                        type="error"
                        :loading="candidateBusy === candidate.candidate_id"
                        @click="rejectCandidate(candidate)"
                      >
                        拒绝
                      </NButton>
                      <NButton
                        type="primary"
                        :loading="candidateBusy === candidate.candidate_id"
                        @click="approveCandidate(candidate)"
                      >
                        通过
                      </NButton>
                    </NSpace>
                  </div>
                </div>
              </AppCard>
            </div>
            <EmptyState
              v-else-if="graphUnsupported"
              title="当前后端还没有图谱候选接口"
              description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
              :icon="FlashOutline"
            />
            <EmptyState
              v-else
              title="没有待审核候选"
              description="当前没有中置信图谱候选。后续接入自动抽取后，这里会成为治理入口。"
              :icon="FlashOutline"
            />
          </NSpin>
        </NTabPane>
      </NTabs>
    </template>
  </AppPage>
</template>

<style scoped>
.knowledge-hero {
  padding: 20px;
  margin-bottom: 18px;
}

.knowledge-compat-alert {
  margin-bottom: 16px;
  border-radius: 14px;
}

.knowledge-hero__main {
  display: flex;
  justify-content: space-between;
  gap: 20px;
}

.knowledge-hero__main h3 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 700;
}

.knowledge-hero__main p {
  max-width: 760px;
  margin: 8px 0 0;
  color: var(--om-text-2);
  line-height: 1.7;
}

.knowledge-hero__badges {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  justify-content: flex-end;
  gap: 8px;
}

.knowledge-status-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-top: 18px;
}

.knowledge-status {
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.knowledge-status span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.knowledge-status strong {
  display: block;
  margin-top: 4px;
  color: var(--om-text-1);
  font-size: 24px;
  line-height: 1;
}

.knowledge-status--warn {
  border-color: rgba(197, 138, 43, 0.35);
  background: rgba(197, 138, 43, 0.08);
}

.knowledge-tabs {
  margin-top: 4px;
}

.knowledge-eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.knowledge-toolbar__title {
  color: var(--om-text-1);
  font-weight: 700;
}

.knowledge-toolbar__hint {
  color: var(--om-text-2);
  font-size: 13px;
}

.knowledge-query-input {
  width: min(520px, 100%);
}

.context-query-input {
  width: min(460px, 100%);
}

.context-id-input {
  width: 160px;
}

.source-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.source-card,
.result-card,
.context-hit,
.relationship-card,
.candidate-card {
  padding: 16px;
}

.source-card__head,
.result-card__head,
.context-hit__head,
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.source-card__head strong,
.result-card__head strong,
.context-hit__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.source-card__head span,
.result-card__head span,
.context-hit__head span,
.source-card__meta,
.context-hit__meta,
.relationship-card__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.source-card__meta,
.context-hit__meta,
.relationship-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.source-card__reason {
  margin: 10px 0 0;
  color: var(--om-warning);
  font-size: 13px;
}

.knowledge-empty-panel {
  min-height: 280px;
}

.result-list,
.context-hit-list,
.relationship-list,
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.graph-scope-risk {
  border-radius: 14px;
}

.graph-scope-risk__head {
  display: grid;
  gap: 4px;
}

.graph-scope-risk__head strong {
  color: var(--om-text-1);
}

.graph-scope-risk__head span {
  color: var(--om-text-2);
  line-height: 1.6;
}

.graph-scope-risk__list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.graph-scope-risk__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid rgba(197, 138, 43, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.45);
}

.graph-scope-risk__item span {
  min-width: 0;
  overflow: hidden;
  color: var(--om-text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-card p,
.context-hit p,
.candidate-card p {
  margin: 12px 0 0;
  color: var(--om-text-1);
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.context-layout,
.graph-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 0.72fr);
  gap: 16px;
  align-items: start;
}

.metrics-layout {
  display: grid;
  gap: 16px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.metric-mini-card {
  padding: 14px;
}

.metric-mini-card span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.metric-mini-card strong {
  display: block;
  margin-top: 6px;
  color: var(--om-text-1);
  font-size: 22px;
}

.metrics-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.metrics-panel {
  padding: 18px;
}

.metric-ratio-list,
.recent-context-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.metric-ratio-row,
.recent-context-card__main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--om-text-1);
}

.metric-ratio-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.recent-context-card {
  padding: 14px;
}

.recent-context-card__main span {
  color: var(--om-text-3);
  font-size: 12px;
}

.context-pack-card,
.graph-entities {
  padding: 20px;
}

.section-head h3 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
}

.context-pack {
  max-height: 520px;
  margin: 14px 0 0;
  padding: 16px;
  overflow: auto;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.entity-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}

.entity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.relationship-card__triple {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--om-text-1);
}

.relationship-card__triple span {
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(49, 108, 114, 0.1);
  color: var(--om-primary);
  font-size: 12px;
  font-weight: 700;
}

.relationship-card__evidence {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.relationship-card__governance {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--om-border);
}

.relationship-card__rollback {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.relationship-card__supersede {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) minmax(90px, 0.6fr) minmax(120px, 1fr) minmax(150px, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.candidate-card__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  gap: 16px;
  align-items: start;
}

.candidate-card__actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

@media (max-width: 1180px) {
  .knowledge-status-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .metrics-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .source-grid,
  .context-layout,
  .graph-layout,
  .metrics-columns,
  .candidate-card__body,
  .relationship-card__rollback,
  .relationship-card__supersede {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .knowledge-hero__main,
  .source-card__head,
  .result-card__head,
  .context-hit__head,
  .section-head {
    flex-direction: column;
  }

  .knowledge-status-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .context-id-input,
  .context-query-input,
  .knowledge-query-input {
    width: 100%;
  }
}
</style>

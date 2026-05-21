<script setup lang="ts">
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import KnowledgeAdminDrawer from './components/KnowledgeAdminDrawer.vue'
import KnowledgeContextPanel from './components/KnowledgeContextPanel.vue'
import KnowledgeHero from './components/KnowledgeHero.vue'
import KnowledgeMetricsPanel from './components/KnowledgeMetricsPanel.vue'
import KnowledgeSearch from './components/KnowledgeSearch.vue'
import KnowledgeSidebar from './components/KnowledgeSidebar.vue'
import KnowledgeSourcesPanel from './components/KnowledgeSourcesPanel.vue'
import { isNotFound, topEntry } from './helpers/formatters'
import type {
  ContextMetrics,
  ContextPack,
  GraphCandidate,
  GraphEdgeRow,
  GraphEntity,
  GraphNodeRow,
  GraphNodeStats,
  GraphRelationship,
  KnowledgeAdminTab,
  KnowledgeResult,
  KnowledgeSource,
  KnowledgeStats,
  KnowledgeTab,
  SupersedeDraft,
} from './helpers/types'

const message = useMessage()

const activeTab = ref<KnowledgeTab>('sources')
const adminDrawerOpen = ref(false)
const adminActiveTab = ref<KnowledgeAdminTab>('candidates')
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
const supersedeDrafts = ref<Record<string, SupersedeDraft>>({})

const candidates = ref<GraphCandidate[]>([])
const candidateLoading = ref(false)
const candidateBusy = ref('')
const rejectNotes = ref<Record<string, string>>({})

const graphNodes = ref<GraphNodeRow[]>([])
const graphNodeTotal = ref(0)
const graphNodeStats = ref<GraphNodeStats | null>(null)
const graphNodeLoading = ref(false)
const graphNodesUnsupported = ref(false)
const graphNodeFilterType = ref('')
const graphNodeFilterGroup = ref('')
const graphNodeSearch = ref('')
const graphNodeDrawerOpen = ref(false)
const graphNodeDrawerNode = ref<GraphNodeRow | null>(null)
const graphNodeDrawerEdges = ref<GraphEdgeRow[]>([])
const graphNodeDrawerLoading = ref(false)

const entryCount = computed(() => stats.value.chunk_count || 0)
const sourceCount = computed(() => stats.value.source_count || sources.value.length || 0)
const skippedCount = computed(() => stats.value.skipped_sources || sources.value.filter(source => source.status !== 'indexed').length)
const indexedCount = computed(() => stats.value.indexed_sources || sources.value.filter(source => source.status === 'indexed').length)
const pendingCount = computed(() => candidates.value.length)
const relationshipCount = computed(() => graphRelationships.value.length)
const scopeRiskCount = computed(() => graphScopeRisks.value.length)

const graphNodeTotalCount = computed(() => graphNodeStats.value?.total_nodes ?? graphNodeTotal.value)
const graphEdgeTotalCount = computed(() => graphNodeStats.value?.total_edges ?? 0)
const graphNodeTopType = computed(() => topEntry(graphNodeStats.value?.by_node_type))
const graphEdgeTopType = computed(() => topEntry(graphNodeStats.value?.by_edge_type))

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
      loadGraphNodes(),
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

function clearSearch() {
  searchQ.value = ''
  searchResults.value = []
  hasSearched.value = false
  lastSearchQ.value = ''
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

async function loadGraphNodes() {
  graphNodeLoading.value = true
  try {
    const params: Record<string, string | number> = { limit: 100 }
    if (graphNodeFilterType.value) params.node_type = graphNodeFilterType.value
    if (graphNodeFilterGroup.value) params.group_id = graphNodeFilterGroup.value
    if (graphNodeSearch.value.trim()) params.search = graphNodeSearch.value.trim()
    const [nodesData, statsData] = await Promise.all([
      api('/api/admin/knowledge/graph/nodes', { params }),
      api('/api/admin/knowledge/graph/stats').catch(() => null),
    ])
    if (nodesData?.available === false) {
      graphNodesUnsupported.value = true
      graphNodes.value = []
      graphNodeTotal.value = 0
      graphNodeStats.value = null
      return
    }
    graphNodesUnsupported.value = false
    graphNodes.value = nodesData?.nodes || []
    graphNodeTotal.value = nodesData?.total || 0
    if (statsData && statsData.available !== false) {
      graphNodeStats.value = {
        total_nodes: statsData.total_nodes || 0,
        total_edges: statsData.total_edges || 0,
        by_node_type: statsData.by_node_type || {},
        by_edge_type: statsData.by_edge_type || {},
      }
    } else {
      graphNodeStats.value = null
    }
  } catch (error) {
    if (isNotFound(error)) {
      graphNodesUnsupported.value = true
      compatibilityWarning.value = '当前运行后端还没有图谱节点 API；请重建/重启 Bot 后再查看图谱节点。'
      graphNodes.value = []
      graphNodeTotal.value = 0
      graphNodeStats.value = null
      return
    }
    message.error('图谱节点加载失败')
    console.error('Failed to load graph nodes:', error)
  } finally {
    graphNodeLoading.value = false
  }
}

async function openGraphNodeDetail(node: GraphNodeRow) {
  graphNodeDrawerNode.value = node
  graphNodeDrawerOpen.value = true
  graphNodeDrawerLoading.value = true
  graphNodeDrawerEdges.value = []
  try {
    const data = await api(`/api/admin/knowledge/graph/nodes/${node.node_id}`)
    if (data?.available === false) {
      graphNodeDrawerEdges.value = []
      return
    }
    graphNodeDrawerEdges.value = data?.edges || []
    if (data?.node) {
      graphNodeDrawerNode.value = data.node as GraphNodeRow
    }
  } catch (error) {
    console.error('Failed to load graph node detail:', error)
    message.error('节点详情加载失败')
  } finally {
    graphNodeDrawerLoading.value = false
  }
}

function clearGraphNodeFilters() {
  graphNodeFilterType.value = ''
  graphNodeFilterGroup.value = ''
  graphNodeSearch.value = ''
  void loadGraphNodes()
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

function handleOpenAdmin(tab: 'candidates' | 'graph' | 'graph_nodes') {
  adminActiveTab.value = tab
  adminDrawerOpen.value = true
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
        <NButton
          quaternary
          size="small"
          @click="adminDrawerOpen = true"
        >
          管理
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

      <KnowledgeHero
        :stats="stats"
        :source-summary="sourceSummary"
      />

      <div class="knowledge-layout">
        <div class="knowledge-main">
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

          <KnowledgeSourcesPanel :sources="sources" />
        </NTabPane>

        <NTabPane name="search" tab="搜索核对">
          <KnowledgeSearch
            v-model:search-q="searchQ"
            :search-results="searchResults"
            :searching="searching"
            :has-searched="hasSearched"
            :last-search-q="lastSearchQ"
            @search="searchKnowledge"
            @clear="clearSearch"
          />
        </NTabPane>

        <NTabPane name="context" tab="上下文调试">
          <KnowledgeContextPanel
            v-model:context-q="contextQ"
            v-model:context-user-id="contextUserId"
            v-model:context-group-id="contextGroupId"
            :context-pack="contextPack"
            :context-hits="contextHits"
            :context-searching="contextSearching"
            :has-context-searched="hasContextSearched"
            :context-unsupported="contextUnsupported"
            @debug="debugContext"
          />
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
            <KnowledgeMetricsPanel
              :context-metrics="contextMetrics"
              :recent-metric-items="recentMetricItems"
            />
          </NSpin>
        </NTabPane>
          </NTabs>
        </div>
        <KnowledgeSidebar
          :available="available"
          :entry-count="entryCount"
          :source-count="sourceCount"
          :relationship-count="relationshipCount"
          :skipped-count="skippedCount"
          :pending-count="pendingCount"
          :scope-risk-count="scopeRiskCount"
          :refreshing="refreshing"
          :reindexing="reindexing"
          @refresh="refreshAll"
          @reindex="reindex"
          @open-admin="handleOpenAdmin"
        />
      </div>

      <KnowledgeAdminDrawer
        v-model:visible="adminDrawerOpen"
        v-model:active-tab="adminActiveTab"
        v-model:fact-rollback-notes="factRollbackNotes"
        v-model:supersede-drafts="supersedeDrafts"
        v-model:reject-notes="rejectNotes"
        v-model:graph-node-filter-type="graphNodeFilterType"
        v-model:graph-node-filter-group="graphNodeFilterGroup"
        v-model:graph-node-search="graphNodeSearch"
        v-model:graph-node-drawer-open="graphNodeDrawerOpen"
        :graph-entities="graphEntities"
        :graph-relationships="graphRelationships"
        :graph-scope-risks="graphScopeRisks"
        :graph-loading="graphLoading"
        :graph-unsupported="graphUnsupported"
        :fact-busy="factBusy"
        :candidates="candidates"
        :candidate-loading="candidateLoading"
        :candidate-busy="candidateBusy"
        :graph-nodes="graphNodes"
        :graph-node-total-count="graphNodeTotalCount"
        :graph-edge-total-count="graphEdgeTotalCount"
        :graph-node-top-type="graphNodeTopType"
        :graph-edge-top-type="graphEdgeTopType"
        :graph-node-loading="graphNodeLoading"
        :graph-nodes-unsupported="graphNodesUnsupported"
        :graph-node-drawer-node="graphNodeDrawerNode"
        :graph-node-drawer-edges="graphNodeDrawerEdges"
        :graph-node-drawer-loading="graphNodeDrawerLoading"
        @reload-graph="loadGraph"
        @reload-candidates="loadCandidates"
        @reload-graph-nodes="loadGraphNodes"
        @rollback="rollbackRelationship"
        @supersede="supersedeRelationship"
        @approve="approveCandidate"
        @reject="rejectCandidate"
        @clear-graph-node-filters="clearGraphNodeFilters"
        @open-graph-node-detail="openGraphNodeDetail"
      />
    </template>
  </AppPage>
</template>

<style scoped>
.knowledge-compat-alert {
  margin-bottom: 16px;
  border-radius: 14px;
}

.knowledge-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 16px;
  align-items: start;
}

.knowledge-main {
  min-width: 0;
}

.knowledge-tabs {
  margin-top: 4px;
}

.knowledge-toolbar__title {
  color: var(--om-text-1);
  font-weight: 700;
}

.knowledge-toolbar__hint {
  color: var(--om-text-2);
  font-size: 13px;
}

@media (max-width: 1180px) {
  .knowledge-layout {
    grid-template-columns: 1fr;
  }
}
</style>

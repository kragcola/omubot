<script setup lang="ts">
import KnowledgeCandidatesPanel from './KnowledgeCandidatesPanel.vue'
import KnowledgeGraphNodesPanel from './KnowledgeGraphNodesPanel.vue'
import KnowledgeGraphPanel from './KnowledgeGraphPanel.vue'
import type {
  GraphCandidate,
  GraphEdgeRow,
  GraphEntity,
  GraphNodeRow,
  GraphRelationship,
  SupersedeDraft,
} from '../helpers/types'

interface Props {
  graphEntities: GraphEntity[]
  graphRelationships: GraphRelationship[]
  graphScopeRisks: GraphRelationship[]
  graphLoading: boolean
  graphUnsupported: boolean
  factBusy: string

  candidates: GraphCandidate[]
  candidateLoading: boolean
  candidateBusy: string

  graphNodes: GraphNodeRow[]
  graphNodeTotalCount: number
  graphEdgeTotalCount: number
  graphNodeTopType: string
  graphEdgeTopType: string
  graphNodeLoading: boolean
  graphNodesUnsupported: boolean
  graphNodeDrawerNode: GraphNodeRow | null
  graphNodeDrawerEdges: GraphEdgeRow[]
  graphNodeDrawerLoading: boolean
}

defineProps<Props>()

const visible = defineModel<boolean>('visible', { required: true })
const activeTab = defineModel<'candidates' | 'graph' | 'graph_nodes'>('activeTab', { required: true })
const factRollbackNotes = defineModel<Record<string, string>>('factRollbackNotes', { required: true })
const supersedeDrafts = defineModel<Record<string, SupersedeDraft>>('supersedeDrafts', { required: true })
const rejectNotes = defineModel<Record<string, string>>('rejectNotes', { required: true })
const graphNodeFilterType = defineModel<string>('graphNodeFilterType', { required: true })
const graphNodeFilterGroup = defineModel<string>('graphNodeFilterGroup', { required: true })
const graphNodeSearch = defineModel<string>('graphNodeSearch', { required: true })
const graphNodeDrawerOpen = defineModel<boolean>('graphNodeDrawerOpen', { required: true })

const emit = defineEmits<{
  (e: 'reload-graph'): void
  (e: 'reload-candidates'): void
  (e: 'reload-graph-nodes'): void
  (e: 'rollback', rel: GraphRelationship): void
  (e: 'supersede', rel: GraphRelationship): void
  (e: 'approve', candidate: GraphCandidate): void
  (e: 'reject', candidate: GraphCandidate): void
  (e: 'clear-graph-node-filters'): void
  (e: 'open-graph-node-detail', node: GraphNodeRow): void
}>()
</script>

<template>
  <NDrawer v-model:show="visible" :width="720" placement="right">
    <NDrawerContent title="知识库管理" closable>
      <p class="knowledge-admin-drawer__hint">
        管理员维护视图：候选审核、图谱关系治理、节点 / 边浏览。日常上下文检索请回主页面。
      </p>
      <NTabs v-model:value="activeTab" type="line" animated class="knowledge-admin-drawer__tabs">
        <NTabPane name="candidates" tab="候选队列">
          <div class="knowledge-admin-drawer__toolbar">
            <div>
              <strong>待审核候选</strong>
              <span>中置信事实进入这里，人工通过后才写入图谱。</span>
            </div>
            <NButton secondary :loading="candidateLoading" @click="emit('reload-candidates')">
              刷新候选
            </NButton>
          </div>
          <KnowledgeCandidatesPanel
            v-model:reject-notes="rejectNotes"
            :candidates="candidates"
            :candidate-loading="candidateLoading"
            :candidate-busy="candidateBusy"
            :graph-unsupported="graphUnsupported"
            @approve="(c) => emit('approve', c)"
            @reject="(c) => emit('reject', c)"
          />
        </NTabPane>

        <NTabPane name="graph" tab="图谱关系">
          <div class="knowledge-admin-drawer__toolbar">
            <div>
              <strong>已生效事实</strong>
              <span>图谱是派生事实层，可重建、可回滚，不替代记忆卡片。</span>
            </div>
            <NButton secondary :loading="graphLoading" @click="emit('reload-graph')">
              刷新图谱
            </NButton>
          </div>
          <KnowledgeGraphPanel
            v-model:fact-rollback-notes="factRollbackNotes"
            v-model:supersede-drafts="supersedeDrafts"
            :graph-entities="graphEntities"
            :graph-relationships="graphRelationships"
            :graph-scope-risks="graphScopeRisks"
            :graph-loading="graphLoading"
            :graph-unsupported="graphUnsupported"
            :fact-busy="factBusy"
            @rollback="(rel) => emit('rollback', rel)"
            @supersede="(rel) => emit('supersede', rel)"
          />
        </NTabPane>

        <NTabPane name="graph_nodes" tab="图谱节点">
          <div class="knowledge-admin-drawer__toolbar">
            <div>
              <strong>图谱节点 / 边</strong>
              <span>通用图层：术语、风格、片段、事实统一以节点+边形式投影。</span>
            </div>
            <NButton secondary :loading="graphNodeLoading" @click="emit('reload-graph-nodes')">
              刷新节点
            </NButton>
          </div>
          <KnowledgeGraphNodesPanel
            v-model:graph-node-filter-type="graphNodeFilterType"
            v-model:graph-node-filter-group="graphNodeFilterGroup"
            v-model:graph-node-search="graphNodeSearch"
            v-model:graph-node-drawer-open="graphNodeDrawerOpen"
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
            @reload="emit('reload-graph-nodes')"
            @clear-filters="emit('clear-graph-node-filters')"
            @open-detail="(node) => emit('open-graph-node-detail', node)"
          />
        </NTabPane>
      </NTabs>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.knowledge-admin-drawer__hint {
  margin: 0 0 14px;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.knowledge-admin-drawer__tabs {
  margin-top: 4px;
}

.knowledge-admin-drawer__toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 4px 0 14px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.knowledge-admin-drawer__toolbar > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.knowledge-admin-drawer__toolbar strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.knowledge-admin-drawer__toolbar span {
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}
</style>

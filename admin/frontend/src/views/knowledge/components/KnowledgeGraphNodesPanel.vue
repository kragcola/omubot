<script setup lang="ts">
import { LayersOutline, SearchOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import type { GraphEdgeRow, GraphNodeRow } from '../helpers/types'

interface Props {
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

const graphNodeFilterType = defineModel<string>('graphNodeFilterType', { required: true })
const graphNodeFilterGroup = defineModel<string>('graphNodeFilterGroup', { required: true })
const graphNodeSearch = defineModel<string>('graphNodeSearch', { required: true })
const graphNodeDrawerOpen = defineModel<boolean>('graphNodeDrawerOpen', { required: true })

const emit = defineEmits<{
  (e: 'reload'): void
  (e: 'clear-filters'): void
  (e: 'open-detail', node: GraphNodeRow): void
}>()
</script>

<template>
  <div class="graph-node-metrics">
    <div class="graph-node-metric graph-node-metric--primary">
      <span class="graph-node-metric__label">节点总数</span>
      <strong class="graph-node-metric__value">{{ graphNodeTotalCount }}</strong>
      <span class="graph-node-metric__hint">active 节点（不含已撤销）</span>
    </div>
    <div class="graph-node-metric graph-node-metric--info">
      <span class="graph-node-metric__label">边总数</span>
      <strong class="graph-node-metric__value">{{ graphEdgeTotalCount }}</strong>
      <span class="graph-node-metric__hint">active 边（不含已撤销）</span>
    </div>
    <div class="graph-node-metric graph-node-metric--success">
      <span class="graph-node-metric__label">主要节点类型</span>
      <strong class="graph-node-metric__value graph-node-metric__value--text">
        {{ graphNodeTopType }}
      </strong>
      <span class="graph-node-metric__hint">按 node_type 分布</span>
    </div>
    <div class="graph-node-metric graph-node-metric--warning">
      <span class="graph-node-metric__label">主要边类型</span>
      <strong class="graph-node-metric__value graph-node-metric__value--text">
        {{ graphEdgeTopType }}
      </strong>
      <span class="graph-node-metric__hint">按 edge_type 分布</span>
    </div>
  </div>

  <section class="graph-node-filters">
    <div class="graph-node-filters__row">
      <NInput
        v-model:value="graphNodeFilterType"
        placeholder="按 node_type 过滤（如 term / fact）"
        clearable
        size="small"
        class="graph-node-filters__input"
        @keyup.enter="emit('reload')"
        @clear="emit('reload')"
      />
      <NInput
        v-model:value="graphNodeFilterGroup"
        placeholder="按群 ID 过滤"
        clearable
        size="small"
        class="graph-node-filters__input"
        @keyup.enter="emit('reload')"
        @clear="emit('reload')"
      />
      <NInput
        v-model:value="graphNodeSearch"
        placeholder="搜索 label / source_id"
        clearable
        size="small"
        class="graph-node-filters__input"
        @keyup.enter="emit('reload')"
        @clear="emit('reload')"
      >
        <template #prefix>
          <NIcon :component="SearchOutline" />
        </template>
      </NInput>
    </div>
    <div class="graph-node-filters__actions">
      <NButton size="small" quaternary @click="emit('clear-filters')">
        清空
      </NButton>
      <NButton size="small" type="primary" secondary @click="emit('reload')">
        应用筛选
      </NButton>
    </div>
  </section>

  <NSpin :show="graphNodeLoading">
    <div v-if="graphNodes.length" class="node-list">
      <AppCard
        v-for="node in graphNodes"
        :key="node.node_id"
        bordered
        embedded
        class="graph-node-card"
      >
        <div class="graph-node-card__head">
          <div>
            <strong>{{ node.label || node.source_id || node.node_id }}</strong>
            <p class="graph-node-card__sub">
              {{ node.source_table || '—' }} · {{ node.source_id || '—' }}
            </p>
          </div>
          <NSpace :size="6">
            <NTag round size="small" type="info">
              {{ node.node_type }}
            </NTag>
            <NTag round size="small" :type="node.status === 'active' ? 'success' : 'default'">
              {{ node.status }}
            </NTag>
          </NSpace>
        </div>
        <div class="graph-node-card__meta">
          <span>scope {{ node.scope }}</span>
          <span>group {{ node.group_id || '—' }}</span>
          <span>node_id {{ node.node_id }}</span>
          <span>updated {{ node.updated_at }}</span>
        </div>
        <div class="graph-node-card__action">
          <NButton size="small" quaternary @click="emit('open-detail', node)">
            查看属性 / 边
          </NButton>
        </div>
      </AppCard>
    </div>
    <EmptyState
      v-else-if="graphNodesUnsupported"
      title="当前后端还没有图谱节点 API"
      description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
      :icon="LayersOutline"
    />
    <EmptyState
      v-else
      title="尚无图谱节点"
      description="Phase D Consolidator 会把术语、风格、片段、事实写入图谱底座。当前节点表为空。"
      :icon="LayersOutline"
    />
  </NSpin>

  <NDrawer
    v-model:show="graphNodeDrawerOpen"
    :width="640"
    placement="right"
  >
    <NDrawerContent
      :title="graphNodeDrawerNode ? (graphNodeDrawerNode.label || graphNodeDrawerNode.node_id) : '节点详情'"
      :native-scrollbar="false"
      closable
    >
      <NSpin :show="graphNodeDrawerLoading">
        <div v-if="graphNodeDrawerNode" class="graph-node-detail">
          <NDescriptions
            :column="2"
            label-placement="left"
            bordered
            size="small"
            class="graph-node-detail__desc"
          >
            <NDescriptionsItem label="node_id">
              {{ graphNodeDrawerNode.node_id }}
            </NDescriptionsItem>
            <NDescriptionsItem label="node_type">
              {{ graphNodeDrawerNode.node_type }}
            </NDescriptionsItem>
            <NDescriptionsItem label="source_table">
              {{ graphNodeDrawerNode.source_table || '—' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="source_id">
              {{ graphNodeDrawerNode.source_id || '—' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="scope">
              {{ graphNodeDrawerNode.scope }}
            </NDescriptionsItem>
            <NDescriptionsItem label="group_id">
              {{ graphNodeDrawerNode.group_id || '—' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="status">
              {{ graphNodeDrawerNode.status }}
            </NDescriptionsItem>
            <NDescriptionsItem label="updated_at">
              {{ graphNodeDrawerNode.updated_at }}
            </NDescriptionsItem>
          </NDescriptions>

          <h4 class="graph-node-detail__heading">
            properties
          </h4>
          <pre class="graph-node-detail__json">{{ JSON.stringify(graphNodeDrawerNode.properties || {}, null, 2) }}</pre>

          <h4 class="graph-node-detail__heading">
            关联边（{{ graphNodeDrawerEdges.length }}）
          </h4>
          <div v-if="graphNodeDrawerEdges.length" class="graph-node-detail__edges">
            <div
              v-for="edge in graphNodeDrawerEdges"
              :key="edge.edge_id"
              class="graph-node-edge"
            >
              <div class="graph-node-edge__head">
                <NTag size="small" round type="info">
                  {{ edge.edge_type }}
                </NTag>
                <NTag size="small" round :type="edge.status === 'active' ? 'success' : 'default'">
                  {{ edge.status }}
                </NTag>
                <span class="graph-node-edge__conf">{{ Math.round((edge.confidence || 0) * 100) }}%</span>
              </div>
              <div class="graph-node-edge__body">
                <span>{{ edge.from_node_id }}</span>
                <span class="graph-node-edge__arrow">→</span>
                <span>{{ edge.to_node_id }}</span>
              </div>
              <div class="graph-node-edge__meta">
                <span>edge_id {{ edge.edge_id }}</span>
                <span>scope {{ edge.scope }} · group {{ edge.group_id || '—' }}</span>
              </div>
            </div>
          </div>
          <EmptyState
            v-else
            title="尚无关联边"
            description="该节点暂无 active 边。Phase D Consolidator 会随事实写入而补齐。"
            :icon="LayersOutline"
          />
        </div>
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.graph-node-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.graph-node-metric {
  position: relative;
  display: grid;
  gap: 6px;
  padding: 14px 16px 16px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
  overflow: hidden;
}

.graph-node-metric::before {
  position: absolute;
  inset: 0 auto auto 0;
  width: 100%;
  height: 3px;
  background: var(--metric-accent, var(--om-primary));
  content: '';
  opacity: 0.85;
}

.graph-node-metric--primary {
  --metric-accent: var(--om-primary);
}

.graph-node-metric--info {
  --metric-accent: var(--om-info);
}

.graph-node-metric--success {
  --metric-accent: var(--om-success);
}

.graph-node-metric--warning {
  --metric-accent: var(--om-warning);
}

.graph-node-metric__label {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.graph-node-metric__value {
  display: block;
  color: var(--om-text-1);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.graph-node-metric__value--text {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0;
  font-family:
    ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
}

.graph-node-metric__hint {
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.4;
}

@media (max-width: 960px) {
  .graph-node-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.graph-node-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface);
}

.graph-node-filters__row {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1.3fr);
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.graph-node-filters__input {
  width: 100%;
}

.graph-node-filters__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

@media (max-width: 720px) {
  .graph-node-filters {
    flex-direction: column;
    align-items: stretch;
  }

  .graph-node-filters__row {
    grid-template-columns: 1fr;
  }

  .graph-node-filters__actions {
    justify-content: flex-end;
  }
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.graph-node-card {
  padding: 16px;
}

.graph-node-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.graph-node-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.graph-node-card__sub {
  margin: 4px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
}

.graph-node-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.graph-node-card__action {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.graph-node-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.graph-node-detail__heading {
  margin: 6px 0 -4px;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.graph-node-detail__json {
  margin: 0;
  padding: 12px;
  background: var(--om-surface-2);
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.graph-node-detail__edges {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.graph-node-edge {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-1);
}

.graph-node-edge__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.graph-node-edge__conf {
  margin-left: auto;
  color: var(--om-text-3);
  font-size: 12px;
}

.graph-node-edge__body {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--om-text-1);
  font-size: 13px;
  word-break: break-all;
}

.graph-node-edge__arrow {
  color: var(--om-text-3);
}

.graph-node-edge__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 6px;
  color: var(--om-text-3);
  font-size: 12px;
}

@media (max-width: 720px) {
  .graph-node-card__head {
    flex-direction: column;
  }
}
</style>

<script setup lang="ts">
import { ChevronForwardOutline, SparklesOutline } from '@vicons/ionicons5'
import { computed, ref, watch } from 'vue'
import AppPanelSection from '../../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../../components/common/EmptyState.vue'
import { useStyleConsoleInject } from './state'
import type { StyleStatus, OutputPolicy, NormalizationInfo, StyleExpression } from './state'

const console_ = useStyleConsoleInject()
const {
  loading,
  expressions,
  setStatus,
  sendFeedback,
  loadNormalizerDetail,
  normalizerDetail,
  lockNormalizerCluster,
  splitNormalizerItem,
  undoNormalizerAutoMerge,
} = console_

const PAGE_SIZE = 12
const page = ref(1)

const pageCount = computed(() => Math.max(1, Math.ceil(expressions.value.length / PAGE_SIZE)))
const pagedExpressions = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return expressions.value.slice(start, start + PAGE_SIZE)
})

watch(() => expressions.value.length, () => {
  if (page.value > pageCount.value) page.value = pageCount.value
  if (page.value < 1) page.value = 1
})

function statusType(status: StyleStatus) {
  return status === 'approved' ? 'success' : status === 'pending' ? 'warning' : status === 'muted' ? 'default' : 'error'
}

function statusTone(status: StyleStatus): 'success' | 'pending' | 'rejected' | 'neutral' {
  if (status === 'approved') return 'success'
  if (status === 'pending') return 'pending'
  if (status === 'rejected' || status === 'muted') return 'rejected'
  return 'neutral'
}

function policyText(policy: OutputPolicy) {
  return policy === 'allow_use' ? '可参考' : policy === 'transform' ? '需转译' : '只观察'
}

function normalizationLabel(info?: NormalizationInfo | null) {
  if (!info?.cluster_id) return ''
  const method = info.method === 'new_cluster' ? '新簇' : info.method || '归一化'
  const score = Number(info.score || 0)
  const scoreText = score > 0 ? ` · ${Math.round(score * 100)}%` : ''
  return `${method}${scoreText}`
}

function normalizerAutoMergeRevision(item: StyleExpression) {
  const info = item.normalization
  const detail = normalizerDetail(info)
  return detail?.revisions.find(entry => entry.action === 'auto_merge' && entry.item_id === info?.item_id)
    || detail?.revisions.find(entry => entry.action === 'auto_merge')
}

function canUndoNormalizerAutoMerge(item: StyleExpression) {
  if (!item.normalization?.cluster_id) return false
  const detail = normalizerDetail(item.normalization)
  return !detail || Boolean(normalizerAutoMergeRevision(item))
}
</script>

<template>
  <AppPanelSection
    class="style-list-panel"
    eyebrow="Style Expressions"
    title="风格表达样本"
  >
    <template v-if="pageCount > 1" #aside>
      <NPagination
        v-model:page="page"
        :page-count="pageCount"
        :page-slot="5"
        size="small"
      />
    </template>

    <NSkeleton v-if="loading" :repeat="6" text />
    <EmptyState
      v-else-if="!expressions.length"
      compact
      title="暂无表达样本"
      description="可以先点上方抽取，或调整筛选条件。"
      :icon="SparklesOutline"
    />
    <div v-else class="expression-list">
      <article
        v-for="item in pagedExpressions"
        :key="item.expression_id"
        class="expression-item"
        :class="`expression-item--${statusTone(item.status)}`"
      >
        <div class="expression-item__main">
          <div class="expression-item__tags">
            <NTag size="small" :type="statusType(item.status)">
              {{ item.status }}
            </NTag>
            <NTag size="small">
              {{ item.scope === 'global' ? '全局' : `群 ${item.group_id}` }}
            </NTag>
            <NTag size="small" :type="item.output_policy === 'observe_only' ? 'warning' : 'info'">
              {{ policyText(item.output_policy) }}
            </NTag>
          </div>
          <h3>{{ item.situation }}</h3>
          <p>{{ item.style }}</p>
          <div class="expression-item__meta">
            <span>置信 {{ Math.round(item.confidence * 100) }}%</span>
            <span>计数 {{ item.count }}</span>
            <span>更新 {{ item.updated_at }}</span>
            <span v-if="item.normalization?.cluster_id">归一化 {{ normalizationLabel(item.normalization) }}</span>
            <span v-if="item.risk_tags.length">风险 {{ item.risk_tags.join(' / ') }}</span>
          </div>
          <div v-if="item.normalization?.cluster_id" class="expression-item__normalization">
            <NTag size="small" round>
              簇 {{ item.normalization.cluster_id.slice(-6) }}
            </NTag>
            <span>代表：{{ item.normalization.canonical_text || item.situation }}</span>
            <span v-if="item.normalization.auto_merged">自动归并</span>
          </div>
          <div
            v-if="item.normalization?.cluster_id"
            class="expression-item__normalizer-actions"
            @mouseenter="loadNormalizerDetail(item.normalization)"
          >
            <NButton size="tiny" secondary @click="lockNormalizerCluster(item)">
              锁定代表
            </NButton>
            <NButton size="tiny" secondary @click="splitNormalizerItem(item)">
              拆出变体
            </NButton>
            <NButton
              size="tiny"
              secondary
              :disabled="!canUndoNormalizerAutoMerge(item)"
              @click="undoNormalizerAutoMerge(item)"
            >
              撤销归并
            </NButton>
          </div>
        </div>
        <div class="expression-item__rail">
          <button
            type="button"
            class="expression-item__config"
            @click="setStatus(item, 'approved')"
          >
            <span class="expression-item__config-icon">
              <NIcon :component="ChevronForwardOutline" />
            </span>
            <span class="expression-item__config-text">配置</span>
          </button>
          <div class="expression-item__actions">
            <NButton size="small" secondary @click="setStatus(item, 'approved')">
              通过
            </NButton>
            <NButton size="small" secondary @click="setStatus(item, 'rejected')">
              拒绝
            </NButton>
            <NButton size="small" secondary @click="setStatus(item, 'muted')">
              静音
            </NButton>
            <NButton size="small" quaternary @click="sendFeedback(item, 'positive')">
              好
            </NButton>
            <NButton size="small" quaternary @click="sendFeedback(item, 'negative')">
              坏
            </NButton>
          </div>
        </div>
      </article>
    </div>

    <div v-if="pageCount > 1" class="style-pagination-bottom">
      <NPagination
        v-model:page="page"
        :page-count="pageCount"
        :page-slot="7"
      />
    </div>
  </AppPanelSection>
</template>

<style scoped>
.style-list-panel {
  font-feature-settings: 'tnum' 1;
}

.expression-list {
  display: grid;
  gap: 8px;
}

.expression-item {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  padding: 12px 14px 12px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.expression-item::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.expression-item--success::before { background: var(--om-success); opacity: 1; }
.expression-item--pending::before { background: var(--om-warning); opacity: 1; }
.expression-item--rejected::before { background: var(--om-danger); opacity: 0.85; }

.expression-item:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.expression-item__tags,
.expression-item__meta,
.expression-item__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.expression-item h3 {
  margin: 8px 0 4px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.45;
}

.expression-item p {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.expression-item__meta {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 70%, transparent);
  color: var(--om-text-3);
  font-size: 11.5px;
}

.expression-item__normalization {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
  color: var(--om-text-3);
  font-size: 11.5px;
}

.expression-item__normalizer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.expression-item__rail {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  align-self: flex-start;
}

.expression-item__config {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 12px 3px 6px;
  border: 1.5px solid color-mix(in srgb, var(--om-info) 32%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
  color: var(--om-info);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.expression-item__config-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--om-info);
  color: #fff;
  font-size: 14px;
}

.expression-item__config:hover {
  border-color: var(--om-info);
  background: var(--om-info);
  color: #fff;
}

.expression-item__config:hover .expression-item__config-icon {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.expression-item__actions {
  justify-content: flex-end;
  align-content: flex-start;
  max-width: 210px;
}

.style-pagination-bottom {
  display: flex;
  justify-content: center;
  margin-top: 14px;
}

@media (max-width: 1100px) {
  .expression-item {
    grid-template-columns: 1fr;
  }
  .expression-item__rail {
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
  }
  .expression-item__actions {
    justify-content: flex-start;
    max-width: none;
  }
}
</style>

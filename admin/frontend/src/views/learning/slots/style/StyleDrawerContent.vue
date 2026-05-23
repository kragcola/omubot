<script setup lang="ts">
import { computed } from 'vue'
import EmptyState from '../../../../components/common/EmptyState.vue'
import { useStyleConsoleInject } from './state'
import type { NormalizationInfo, OutputPolicy, StyleExpression, StyleStatus } from './state'

const console_ = useStyleConsoleInject()
const {
  detailItem,
  drawerVisible,
  closeDetail,
  actionLoading,
  setStatus,
  sendFeedback,
  loadNormalizerDetail,
  normalizerDetail,
  lockNormalizerCluster,
  splitNormalizerItem,
  undoNormalizerAutoMerge,
} = console_

const showDrawer = computed({
  get: () => drawerVisible.value,
  set: (val: boolean) => {
    if (!val) closeDetail()
  },
})

function statusType(status: StyleStatus) {
  return status === 'approved' ? 'success' : status === 'pending' ? 'warning' : status === 'muted' ? 'default' : 'error'
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
  <NDrawer v-model:show="showDrawer" :width="640" placement="right">
    <NDrawerContent
      :title="detailItem?.situation || '风格表达详情'"
      :native-scrollbar="false"
    >
      <EmptyState
        v-if="!detailItem"
        compact
        title="未选中表达"
        description="点击列表中的“配置”以查看详情。"
      />
      <div v-else class="style-detail">
        <section class="style-detail__section">
          <header class="style-detail__tags">
            <NTag size="small" :type="statusType(detailItem.status)">
              {{ detailItem.status }}
            </NTag>
            <NTag size="small">
              {{ detailItem.scope === 'global' ? '全局' : `群 ${detailItem.group_id}` }}
            </NTag>
            <NTag size="small" :type="detailItem.output_policy === 'observe_only' ? 'warning' : 'info'">
              {{ policyText(detailItem.output_policy) }}
            </NTag>
          </header>
          <h3 class="style-detail__title">
            {{ detailItem.situation }}
          </h3>
          <p class="style-detail__text">
            {{ detailItem.style }}
          </p>
        </section>

        <section class="style-detail__section">
          <h4 class="style-detail__heading">
            指标
          </h4>
          <div class="style-detail__grid">
            <div class="style-detail__item">
              <div class="style-detail__label">
                置信度
              </div>
              <div>{{ Math.round(detailItem.confidence * 100) }}%</div>
            </div>
            <div class="style-detail__item">
              <div class="style-detail__label">
                计数
              </div>
              <div>{{ detailItem.count }}</div>
            </div>
            <div class="style-detail__item">
              <div class="style-detail__label">
                更新
              </div>
              <div>{{ detailItem.updated_at }}</div>
            </div>
            <div class="style-detail__item">
              <div class="style-detail__label">
                Expression ID
              </div>
              <div class="style-detail__mono">
                {{ detailItem.expression_id }}
              </div>
            </div>
          </div>
        </section>

        <section v-if="detailItem.risk_tags.length" class="style-detail__section">
          <h4 class="style-detail__heading">
            风险标签
          </h4>
          <NSpace :size="6" wrap>
            <NTag v-for="tag in detailItem.risk_tags" :key="tag" size="small" type="warning">
              {{ tag }}
            </NTag>
          </NSpace>
        </section>

        <section v-if="detailItem.normalization?.cluster_id" class="style-detail__section">
          <h4 class="style-detail__heading">
            归一化
          </h4>
          <div class="style-detail__normalization">
            <NTag size="small" round>
              簇 {{ detailItem.normalization.cluster_id.slice(-6) }}
            </NTag>
            <span>{{ normalizationLabel(detailItem.normalization) }}</span>
            <span v-if="detailItem.normalization.auto_merged">自动归并</span>
          </div>
          <p v-if="detailItem.normalization.canonical_text" class="style-detail__text style-detail__text--muted">
            代表：{{ detailItem.normalization.canonical_text || detailItem.situation }}
          </p>
          <NSpace
            :size="8"
            class="style-detail__normalizer-actions"
            @mouseenter="loadNormalizerDetail(detailItem.normalization)"
          >
            <NButton size="small" secondary :disabled="actionLoading" @click="lockNormalizerCluster(detailItem)">
              锁定代表
            </NButton>
            <NButton size="small" secondary :disabled="actionLoading" @click="splitNormalizerItem(detailItem)">
              拆出变体
            </NButton>
            <NButton
              size="small"
              secondary
              :disabled="actionLoading || !canUndoNormalizerAutoMerge(detailItem)"
              @click="undoNormalizerAutoMerge(detailItem)"
            >
              撤销归并
            </NButton>
          </NSpace>
        </section>
      </div>

      <template v-if="detailItem" #footer>
        <NSpace justify="space-between" align="center" :size="8">
          <NSpace :size="6">
            <NButton size="small" quaternary :disabled="actionLoading" @click="sendFeedback(detailItem, 'positive')">
              好
            </NButton>
            <NButton size="small" quaternary :disabled="actionLoading" @click="sendFeedback(detailItem, 'negative')">
              坏
            </NButton>
          </NSpace>
          <NSpace :size="6">
            <NButton size="small" :disabled="actionLoading" @click="setStatus(detailItem, 'muted')">
              静音
            </NButton>
            <NButton size="small" type="error" secondary :disabled="actionLoading" @click="setStatus(detailItem, 'rejected')">
              拒绝
            </NButton>
            <NButton size="small" type="success" :disabled="actionLoading" @click="setStatus(detailItem, 'approved')">
              通过
            </NButton>
          </NSpace>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.style-detail {
  padding: 4px 2px 16px;
}

.style-detail__section {
  margin-bottom: 22px;
}

.style-detail__section + .style-detail__section {
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px dashed var(--om-border);
}

.style-detail__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.style-detail__title {
  margin: 0 0 8px;
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 650;
  line-height: 1.4;
}

.style-detail__heading {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.style-detail__text {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.style-detail__text--muted {
  margin-top: 8px;
  color: var(--om-text-2);
  font-size: 12.5px;
}

.style-detail__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 18px;
}

.style-detail__item {
  min-width: 0;
}

.style-detail__label {
  margin-bottom: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
}

.style-detail__mono {
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  color: var(--om-text-2);
  word-break: break-all;
}

.style-detail__normalization {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  color: var(--om-text-2);
  font-size: 12.5px;
}

.style-detail__normalizer-actions {
  margin-top: 10px;
}
</style>

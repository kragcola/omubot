<script setup lang="ts">
import { FlashOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { hitTypeLabel } from '../helpers/badges'
import { metricRatioEntries, numberText, percentText } from '../helpers/formatters'
import type { ContextMetricRecent, ContextMetrics } from '../helpers/types'

interface Props {
  contextMetrics: ContextMetrics | null
  recentMetricItems: ContextMetricRecent[]
}

defineProps<Props>()
</script>

<template>
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
      <AppPanelSection eyebrow="Sources" title="命中来源">
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
      </AppPanelSection>

      <AppPanelSection eyebrow="Types" title="命中类型">
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
      </AppPanelSection>
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
</template>

<style scoped>
.metrics-layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.metric-mini-card {
  padding: 14px 16px;
}

.metric-mini-card span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.metric-mini-card strong {
  display: block;
  margin-top: 4px;
  color: var(--om-text-1);
  font-size: 22px;
  line-height: 1;
}

.metrics-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.metric-ratio-list,
.recent-context-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.metric-ratio-row,
.recent-context-card__main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.metric-ratio-row {
  color: var(--om-text-2);
  font-size: 13px;
}

.recent-context-card {
  padding: 12px 14px;
}

.recent-context-card__main span {
  color: var(--om-text-3);
  font-size: 12px;
}

.relationship-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

@media (max-width: 1180px) {
  .metrics-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .metrics-columns {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

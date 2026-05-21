<script setup lang="ts">
import {
  FlashOutline,
  PricetagsOutline,
} from '@vicons/ionicons5'

import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { confidenceText } from '../helpers/formatters'
import type { SlangStats, SlangSummary } from '../helpers/types'

defineProps<{
  summary: SlangSummary
  stats: SlangStats | null
}>()
</script>

<template>
  <div class="slang-stats-cards">
    <AppCard bordered embedded class="slang-stats-cards__card">
      <div class="slang-stats-cards__head">
        <span>热门黑话</span>
        <NTag round size="small">
          {{ stats?.review.total_terms || 0 }} 总词条
        </NTag>
      </div>
      <div v-if="stats?.popular_terms.length" class="slang-stats-cards__list">
        <div v-for="item in stats.popular_terms.slice(0, 5)" :key="item.term_id" class="slang-stats-cards__row">
          <strong>{{ item.term }}</strong>
          <span>{{ item.usage_count }} 次 · {{ confidenceText(item.confidence) }}</span>
        </div>
      </div>
      <EmptyState v-else compact title="还没有排行" description="批准或命中词条后显示高频黑话。" :icon="PricetagsOutline" />
    </AppCard>

    <AppCard bordered embedded class="slang-stats-cards__card">
      <div class="slang-stats-cards__head">
        <span>群活跃排行</span>
        <NTag round size="small">
          {{ summary.group_count }} 个群
        </NTag>
      </div>
      <div v-if="stats?.group_activity.length" class="slang-stats-cards__list">
        <div v-for="item in stats.group_activity.slice(0, 5)" :key="item.group_id" class="slang-stats-cards__row">
          <strong>群 {{ item.group_id }}</strong>
          <span>{{ item.usage_count }} 次 · {{ item.approved_count }}/{{ item.term_count }} 已批准</span>
        </div>
      </div>
      <EmptyState v-else compact title="暂无群活跃数据" description="黑话命中后自动形成排行。" :icon="FlashOutline" />
    </AppCard>
  </div>
</template>

<style scoped>
.slang-stats-cards {
  display: grid;
  gap: 12px;
}

.slang-stats-cards__card {
  padding: 12px;
}

.slang-stats-cards__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.slang-stats-cards__head span {
  color: var(--om-text-1);
  font-size: 12px;
  font-weight: 700;
}

.slang-stats-cards__list {
  display: grid;
  gap: 8px;
}

.slang-stats-cards__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px solid color-mix(in srgb, var(--om-border) 72%, transparent);
}

.slang-stats-cards__row:first-child {
  border-top: 0;
}

.slang-stats-cards__row strong {
  color: var(--om-text-1);
  font-size: 12px;
}

.slang-stats-cards__row span {
  color: var(--om-text-3);
  font-size: 11px;
}
</style>

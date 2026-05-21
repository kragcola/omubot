<script setup lang="ts">
import { LayersOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { sourceStatusType } from '../helpers/badges'
import { shortHash } from '../helpers/formatters'
import type { KnowledgeSource } from '../helpers/types'

interface Props {
  sources: KnowledgeSource[]
}

defineProps<Props>()
</script>

<template>
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
</template>

<style scoped>
.source-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.source-card {
  padding: 16px;
}

.source-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.source-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.source-card__head span,
.source-card__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.source-card__meta {
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

@media (max-width: 1180px) {
  .source-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .source-card__head {
    flex-direction: column;
  }
}
</style>

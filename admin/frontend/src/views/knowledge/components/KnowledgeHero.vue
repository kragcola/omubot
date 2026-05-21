<script setup lang="ts">
import AppCard from '../../../components/common/AppCard.vue'
import type { KnowledgeStats } from '../helpers/types'

interface Props {
  stats: KnowledgeStats
  sourceSummary: string
}

defineProps<Props>()
</script>

<template>
  <AppCard bordered elevated class="knowledge-hero">
    <div class="knowledge-hero__main">
      <div class="knowledge-hero__copy">
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
  </AppCard>
</template>

<style scoped>
.knowledge-hero {
  padding: 16px 20px;
  margin-bottom: 14px;
}

.knowledge-hero__main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.knowledge-hero__copy {
  min-width: 0;
  flex: 1;
}

.knowledge-hero__main h3 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.45;
}

.knowledge-hero__main p {
  max-width: 760px;
  margin: 6px 0 0;
  color: var(--om-text-2);
  line-height: 1.65;
  font-size: 13px;
}

.knowledge-hero__badges {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  justify-content: flex-end;
  gap: 8px;
}

.knowledge-eyebrow {
  margin: 0 0 6px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

@media (max-width: 720px) {
  .knowledge-hero__main {
    flex-direction: column;
  }

  .knowledge-hero__badges {
    justify-content: flex-start;
  }
}
</style>

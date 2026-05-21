<script setup lang="ts">
import AppCard from '../../../components/common/AppCard.vue'
import type { KnowledgeStats } from '../helpers/types'

interface Props {
  stats: KnowledgeStats
  sourceSummary: string
  entryCount: number
  sourceCount: number
  skippedCount: number
  relationshipCount: number
  pendingCount: number
  scopeRiskCount: number
}

defineProps<Props>()
</script>

<template>
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
</template>

<style scoped>
.knowledge-hero {
  padding: 20px;
  margin-bottom: 18px;
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

.knowledge-eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

@media (max-width: 1180px) {
  .knowledge-status-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .knowledge-hero__main {
    flex-direction: column;
  }

  .knowledge-status-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

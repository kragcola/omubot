<script setup lang="ts">
import { DocumentTextOutline, FlashOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import PageToolbar from '../../../components/common/PageToolbar.vue'
import { scoreText } from '../helpers/formatters'
import type { KnowledgeResult } from '../helpers/types'

interface Props {
  searchResults: KnowledgeResult[]
  searching: boolean
  hasSearched: boolean
  lastSearchQ: string
}

defineProps<Props>()

const searchQ = defineModel<string>('searchQ', { required: true })

const emit = defineEmits<{
  (e: 'search'): void
  (e: 'clear'): void
}>()
</script>

<template>
  <PageToolbar class="mb-16">
    <template #left>
      <NInput
        v-model:value="searchQ"
        clearable
        placeholder="输入关键词或问题，核对文档 chunk 命中"
        class="knowledge-query-input"
        @keyup.enter="emit('search')"
      />
    </template>
    <template #right>
      <NButton v-if="hasSearched" secondary @click="emit('clear')">
        清除
      </NButton>
      <NButton type="primary" :loading="searching" @click="emit('search')">
        搜索文档
      </NButton>
    </template>
  </PageToolbar>

  <NSpin :show="searching">
    <div v-if="!hasSearched" class="knowledge-empty-panel">
      <EmptyState
        title="输入一句话开始核对"
        description="这里只检查文档知识库命中，不包含记忆卡片或图谱事实。"
        :icon="DocumentTextOutline"
      />
    </div>
    <div v-else-if="searchResults.length === 0" class="knowledge-empty-panel">
      <EmptyState
        title="没有命中文档片段"
        :description="`“${lastSearchQ}” 没有命中知识库，可以换更短的词或检查文档源。`"
        :icon="FlashOutline"
      />
    </div>
    <div v-else class="result-list">
      <AppCard
        v-for="(result, index) in searchResults"
        :key="result.chunk_id || result.id || `${result.source}-${index}`"
        bordered
        embedded
        class="result-card"
      >
        <div class="result-card__head">
          <div>
            <strong>{{ result.title || result.source || `结果 ${index + 1}` }}</strong>
            <span>{{ result.chunk_id || result.id || result.source }}</span>
          </div>
          <NTag round size="small" type="info">
            score {{ scoreText(result.score) }}
          </NTag>
        </div>
        <p>{{ result.content }}</p>
      </AppCard>
    </div>
  </NSpin>
</template>

<style scoped>
.knowledge-query-input {
  width: min(520px, 100%);
}

.knowledge-empty-panel {
  min-height: 280px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-card {
  padding: 16px;
}

.result-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.result-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.result-card__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.result-card p {
  margin: 12px 0 0;
  color: var(--om-text-1);
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 720px) {
  .result-card__head {
    flex-direction: column;
  }

  .knowledge-query-input {
    width: 100%;
  }
}
</style>

<script setup lang="ts">
import { FlashOutline, LayersOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import PageToolbar from '../../../components/common/PageToolbar.vue'
import { hitTypeLabel, hitTypeTag } from '../helpers/badges'
import { scoreText } from '../helpers/formatters'
import type { ContextHit, ContextPack } from '../helpers/types'

interface Props {
  contextPack: ContextPack | null
  contextHits: ContextHit[]
  contextSearching: boolean
  hasContextSearched: boolean
  contextUnsupported: boolean
}

defineProps<Props>()

const contextQ = defineModel<string>('contextQ', { required: true })
const contextUserId = defineModel<string>('contextUserId', { required: true })
const contextGroupId = defineModel<string>('contextGroupId', { required: true })

const emit = defineEmits<{
  (e: 'debug'): void
}>()
</script>

<template>
  <PageToolbar class="mb-16">
    <template #left>
      <NInput
        v-model:value="contextQ"
        clearable
        placeholder="输入本轮用户消息，查看 memory/doc/graph 最终命中"
        class="context-query-input"
        @keyup.enter="emit('debug')"
      />
      <NInput
        v-model:value="contextUserId"
        clearable
        placeholder="用户 ID，可选"
        class="context-id-input"
      />
      <NInput
        v-model:value="contextGroupId"
        clearable
        placeholder="群 ID，可选"
        class="context-id-input"
      />
    </template>
    <template #right>
      <NButton type="primary" :loading="contextSearching" @click="emit('debug')">
        调试上下文
      </NButton>
    </template>
  </PageToolbar>

  <NSpin :show="contextSearching">
    <div v-if="!hasContextSearched" class="knowledge-empty-panel">
      <EmptyState
        title="还没有调试上下文"
        description="输入一句真实聊天内容，可以看到统一上下文会引用哪些记忆卡片、文档片段和图谱事实。"
        :icon="LayersOutline"
      />
    </div>

    <div v-else-if="contextUnsupported" class="knowledge-empty-panel">
      <EmptyState
        title="当前后端还没有上下文调试接口"
        description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
        :icon="FlashOutline"
      />
    </div>

    <div v-else class="context-layout">
      <AppCard bordered elevated class="context-pack-card">
        <div class="section-head">
          <div>
            <p class="knowledge-eyebrow">
              Prompt Pack
            </p>
            <h3>最终打包文本</h3>
          </div>
          <NTag round size="small">
            省略 {{ contextPack?.omitted_count || 0 }} 条
          </NTag>
        </div>
        <pre v-if="contextPack?.text" class="context-pack">{{ contextPack.text }}</pre>
        <EmptyState
          v-else
          compact
          title="没有可注入上下文"
          description="这次查询没有命中可打包内容。"
          :icon="FlashOutline"
        />
      </AppCard>

      <div class="context-hit-list">
        <AppCard
          v-for="hit in contextHits"
          :key="`${hit.type}-${hit.id}`"
          bordered
          embedded
          class="context-hit"
        >
          <div class="context-hit__head">
            <div>
              <strong>{{ hit.title || hit.source || hit.id }}</strong>
              <span>{{ hit.id }}</span>
            </div>
            <NSpace :size="6">
              <NTag round size="small" :type="hitTypeTag(hit.type)">
                {{ hitTypeLabel(hit.type) }}
              </NTag>
              <NTag round size="small">
                {{ scoreText(hit.score) }}
              </NTag>
            </NSpace>
          </div>
          <p>{{ hit.content }}</p>
          <div class="context-hit__meta">
            <span>{{ hit.scope || 'global' }}/{{ hit.scope_id || 'global' }}</span>
            <span>{{ hit.retriever || 'retriever' }}</span>
            <span>{{ hit.source }}</span>
          </div>
        </AppCard>
      </div>
    </div>
  </NSpin>
</template>

<style scoped>
.context-query-input {
  width: min(460px, 100%);
}

.context-id-input {
  width: 160px;
}

.knowledge-empty-panel {
  min-height: 280px;
}

.context-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 0.72fr);
  gap: 16px;
  align-items: start;
}

.context-pack-card {
  padding: 20px;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.section-head h3 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
}

.knowledge-eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.context-pack {
  max-height: 520px;
  margin: 14px 0 0;
  padding: 16px;
  overflow: auto;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.context-hit-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.context-hit {
  padding: 16px;
}

.context-hit__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.context-hit__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.context-hit__head span,
.context-hit__meta {
  color: var(--om-text-3);
  font-size: 12px;
}

.context-hit__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.context-hit p {
  margin: 12px 0 0;
  color: var(--om-text-1);
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1180px) {
  .context-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .context-hit__head,
  .section-head {
    flex-direction: column;
  }

  .context-id-input,
  .context-query-input {
    width: 100%;
  }
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import {
  DocumentTextOutline,
  FlashOutline,
  LayersOutline,
  SearchOutline,
  StatsChartOutline,
} from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { hitTypeLabel, hitTypeTag } from '../helpers/badges'
import {
  metricRatioEntries,
  numberText,
  percentText,
  scoreText,
} from '../helpers/formatters'
import type {
  ContextHit,
  ContextMetricRecent,
  ContextMetrics,
  ContextPack,
  KnowledgeResult,
} from '../helpers/types'

type WorkspaceTab = 'details' | 'pack' | 'metrics'

interface Props {
  searchResults: KnowledgeResult[]
  hasSearched: boolean
  lastSearchQ: string
  contextPack: ContextPack | null
  contextHits: ContextHit[]
  hasContextSearched: boolean
  contextUnsupported: boolean
  contextMetrics: ContextMetrics | null
  recentMetricItems: ContextMetricRecent[]
  searching: boolean
  contextSearching: boolean
  metricsLoading: boolean
}

const props = defineProps<Props>()

const queryInput = defineModel<string>('queryInput', { required: true })
const userIdInput = defineModel<string>('userIdInput', { required: true })
const groupIdInput = defineModel<string>('groupIdInput', { required: true })
const activeTab = defineModel<WorkspaceTab>('activeTab', { required: true })

const emit = defineEmits<{
  (e: 'submit'): void
  (e: 'reload-metrics'): void
}>()

const submitting = computed(() => props.searching || props.contextSearching)

const totalHits = computed(() => props.searchResults.length + props.contextHits.length)
const showInitialEmpty = computed(
  () => !props.hasSearched && !props.hasContextSearched,
)
const showNoResultEmpty = computed(
  () =>
    (props.hasSearched || props.hasContextSearched)
    && totalHits.value === 0
    && !props.contextUnsupported,
)
</script>

<template>
  <div class="workspace">
    <div class="workspace-query">
      <div class="workspace-query__inputs">
        <NInput
          v-model:value="queryInput"
          clearable
          size="medium"
          placeholder="输入一句真实聊天内容，看看记忆 / 文档 / 图谱最终命中"
          class="workspace-query__main"
          @keyup.enter="emit('submit')"
        />
        <div class="workspace-query__scope">
          <NInput
            v-model:value="userIdInput"
            clearable
            size="small"
            placeholder="用户 ID（可选）"
            class="workspace-query__scope-input"
          />
          <NInput
            v-model:value="groupIdInput"
            clearable
            size="small"
            placeholder="群 ID（可选）"
            class="workspace-query__scope-input"
          />
        </div>
      </div>
      <NButton
        type="primary"
        size="medium"
        :loading="submitting"
        class="workspace-query__submit"
        @click="emit('submit')"
      >
        调试上下文
      </NButton>
    </div>
    <p class="workspace-hint">
      会同时跑文档 chunk 检索 + Prompt Pack 打包；输入 user / group ID 可还原对应作用域下命中。
    </p>

    <NTabs
      v-model:value="activeTab"
      type="line"
      animated
      class="workspace-tabs"
    >
      <NTabPane name="details" tab="命中详情">
        <NSpin :show="submitting">
          <div v-if="showInitialEmpty" class="workspace-empty">
            <EmptyState
              title="先来一句话开始调试"
              description="文档命中和上下文命中会一起列在这里，按类型区分。"
              :icon="LayersOutline"
            />
          </div>
          <div v-else-if="contextUnsupported" class="workspace-empty">
            <EmptyState
              title="当前后端还没有上下文调试接口"
              description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
              :icon="FlashOutline"
            />
          </div>
          <div v-else-if="showNoResultEmpty" class="workspace-empty">
            <EmptyState
              title="没有命中"
              :description="`“${lastSearchQ || queryInput}” 没有命中文档或上下文，可以换更短的关键词或检查 Bot 是否已索引相关文档。`"
              :icon="FlashOutline"
            />
          </div>

          <div v-else class="workspace-hits">
            <AppPanelSection
              v-if="searchResults.length"
              eyebrow="Documents"
              title="文档片段"
            >
              <template #aside>
                <NTag round size="small">
                  {{ searchResults.length }} 命中
                </NTag>
              </template>
              <div class="hit-list">
                <AppCard
                  v-for="(result, index) in searchResults"
                  :key="result.chunk_id || result.id || `${result.source}-${index}`"
                  bordered
                  embedded
                  class="hit-card"
                >
                  <div class="hit-card__head">
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
            </AppPanelSection>

            <AppPanelSection
              v-if="contextHits.length"
              eyebrow="Context Pack"
              title="统一上下文命中"
            >
              <template #aside>
                <NTag round size="small">
                  {{ contextHits.length }} 命中
                </NTag>
              </template>
              <div class="hit-list">
                <AppCard
                  v-for="hit in contextHits"
                  :key="`${hit.type}-${hit.id}`"
                  bordered
                  embedded
                  class="hit-card"
                >
                  <div class="hit-card__head">
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
                  <div class="hit-card__meta">
                    <span>{{ hit.scope || 'global' }}/{{ hit.scope_id || 'global' }}</span>
                    <span>{{ hit.retriever || 'retriever' }}</span>
                    <span>{{ hit.source }}</span>
                  </div>
                </AppCard>
              </div>
            </AppPanelSection>
          </div>
        </NSpin>
      </NTabPane>

      <NTabPane name="pack" tab="Prompt Pack">
        <NSpin :show="contextSearching">
          <div v-if="!hasContextSearched" class="workspace-empty">
            <EmptyState
              title="还没有调试上下文"
              description="点击调试上下文，可以看到统一上下文最终拼好的纯文本。"
              :icon="DocumentTextOutline"
            />
          </div>
          <div v-else-if="contextUnsupported" class="workspace-empty">
            <EmptyState
              title="当前后端还没有上下文调试接口"
              description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
              :icon="FlashOutline"
            />
          </div>
          <AppPanelSection
            v-else
            eyebrow="Prompt Pack"
            title="最终打包文本"
          >
            <template #aside>
              <NTag round size="small">
                省略 {{ contextPack?.omitted_count || 0 }} 条
              </NTag>
            </template>
            <pre v-if="contextPack?.text" class="workspace-pack">{{ contextPack.text }}</pre>
            <EmptyState
              v-else
              compact
              title="没有可注入上下文"
              description="这次查询没有命中可打包内容。"
              :icon="FlashOutline"
            />
          </AppPanelSection>
        </NSpin>
      </NTabPane>

      <NTabPane name="metrics" tab="评测指标">
        <NSpin :show="metricsLoading">
          <div v-if="!contextMetrics" class="workspace-empty">
            <EmptyState
              title="暂无上下文指标"
              description="先调一句上下文，或等待 Bot 真实对话产生检索记录。"
              :icon="StatsChartOutline"
            />
          </div>
          <div v-else class="metrics-layout">
            <div class="metrics-grid">
              <AppCard bordered embedded class="metric-card">
                <span>最近查询</span>
                <strong>{{ contextMetrics.total_queries }}</strong>
              </AppCard>
              <AppCard bordered embedded class="metric-card">
                <span>Miss 率</span>
                <strong>{{ percentText(contextMetrics.miss_rate) }}</strong>
              </AppCard>
              <AppCard bordered embedded class="metric-card">
                <span>平均 Pack</span>
                <strong>{{ numberText(contextMetrics.avg_pack_chars) }}</strong>
              </AppCard>
              <AppCard bordered embedded class="metric-card">
                <span>最大 Pack</span>
                <strong>{{ numberText(contextMetrics.max_pack_chars) }}</strong>
              </AppCard>
              <AppCard bordered embedded class="metric-card">
                <span>重复率</span>
                <strong>{{ percentText(contextMetrics.duplicate_rate) }}</strong>
              </AppCard>
              <AppCard bordered embedded class="metric-card">
                <span>省略命中</span>
                <strong>{{ contextMetrics.omitted_total }}</strong>
              </AppCard>
            </div>

            <div class="metrics-columns">
              <AppPanelSection eyebrow="Sources" title="命中来源">
                <template #aside>
                  <NButton text size="small" :loading="metricsLoading" @click="emit('reload-metrics')">
                    <template #icon>
                      <NIcon :component="SearchOutline" />
                    </template>
                    刷新
                  </NButton>
                </template>
                <div v-if="metricRatioEntries(contextMetrics.hit_source_counts).length" class="ratio-list">
                  <div
                    v-for="[source, count] in metricRatioEntries(contextMetrics.hit_source_counts)"
                    :key="source"
                    class="ratio-row"
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
                <div v-if="metricRatioEntries(contextMetrics.hit_type_counts).length" class="ratio-list">
                  <div
                    v-for="[type, count] in metricRatioEntries(contextMetrics.hit_type_counts)"
                    :key="type"
                    class="ratio-row"
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

            <AppPanelSection
              v-if="recentMetricItems.length"
              eyebrow="Recent"
              title="最近检索"
            >
              <div class="recent-list">
                <AppCard
                  v-for="item in recentMetricItems"
                  :key="`${item.created_at}-${item.query}`"
                  bordered
                  embedded
                  class="recent-card"
                >
                  <div class="recent-card__main">
                    <strong>{{ item.query || '空查询' }}</strong>
                    <span>{{ item.group_id ? `群 ${item.group_id}` : item.user_id ? `用户 ${item.user_id}` : '全局' }}</span>
                  </div>
                  <div class="recent-card__meta">
                    <NTag round size="small" :type="item.hit_count ? 'success' : 'warning'">
                      {{ item.hit_count || 0 }} 命中
                    </NTag>
                    <span>pack {{ item.pack_chars || 0 }}</span>
                    <span>重复 {{ item.duplicate_count || 0 }}</span>
                    <span>省略 {{ item.omitted_count || 0 }}</span>
                  </div>
                </AppCard>
              </div>
            </AppPanelSection>
          </div>
        </NSpin>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.workspace {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.workspace-query {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.workspace-query__inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
}

.workspace-query__main {
  width: 100%;
}

.workspace-query__scope {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.workspace-query__scope-input {
  width: min(200px, 100%);
}

.workspace-query__submit {
  flex-shrink: 0;
  align-self: stretch;
  min-width: 124px;
}

.workspace-hint {
  margin: 0 4px;
  color: var(--om-text-3);
  font-size: 12.5px;
  line-height: 1.6;
}

.workspace-tabs {
  margin-top: 4px;
}

.workspace-empty {
  min-height: 240px;
}

.workspace-hits {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.hit-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
}

.hit-card {
  padding: 14px 16px;
}

.hit-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.hit-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.hit-card__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.hit-card p {
  margin: 10px 0 0;
  color: var(--om-text-1);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.hit-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.workspace-pack {
  max-height: 520px;
  margin: 0;
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

.metric-card {
  padding: 14px 16px;
}

.metric-card span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.metric-card strong {
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

.ratio-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.ratio-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--om-text-2);
  font-size: 13px;
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.recent-card {
  padding: 12px 14px;
}

.recent-card__main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.recent-card__main span {
  color: var(--om-text-3);
  font-size: 12px;
}

.recent-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
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
  .workspace-query {
    flex-direction: column;
  }

  .workspace-query__submit {
    width: 100%;
  }

  .workspace-query__scope-input {
    width: 100%;
  }

  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hit-card__head {
    flex-direction: column;
  }
}
</style>

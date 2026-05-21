<script setup lang="ts">
import { SearchOutline, TimeOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import AppDrawerHeader from '../../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import {
  REPEAT_POLICY_OPTIONS,
  STATUS_OPTIONS,
  isAiApproved,
  needsHumanReview,
  revisionActionLabel,
  statusLabel,
  statusType,
} from '../helpers/badges'
import { confidenceText, formatSearchQueries, formatTime } from '../helpers/formatters'
import type {
  SlangObservation,
  SlangRevision,
  SlangTerm,
} from '../helpers/types'

defineProps<{
  detailLoading: boolean
  observations: SlangObservation[]
  revisions: SlangRevision[]
  mergeOptions: { label: string, value: string }[]
  mergeLoading: boolean
}>()

const visible = defineModel<boolean>('visible', { required: true })
const detailTerm = defineModel<SlangTerm | null>('detailTerm', { required: true })
const editAliases = defineModel<string>('editAliases', { required: true })
const mergeTargetId = defineModel<string>('mergeTargetId', { required: true })
const mergeSearchText = defineModel<string>('mergeSearchText', { required: true })

const emit = defineEmits<{
  (e: 'save'): void
  (e: 'recompute-confidence'): void
  (e: 'merge'): void
  (e: 'review-ai', term: SlangTerm, action: 'human-approve' | 'deny' | 'return-candidate'): void
  (e: 'search-merge', query: string): void
}>()
</script>

<template>
  <NDrawer v-model:show="visible" :width="620">
    <NDrawerContent closable>
      <template #header>
        <AppDrawerHeader
          eyebrow="Slang Detail"
          :title="detailTerm?.term || '黑话详情'"
          :description="detailTerm ? `群 ${detailTerm.group_id || '全局'} · ${statusLabel(detailTerm.status)}` : ''"
        >
          <template v-if="detailTerm" #aside>
            <NTag :type="statusType(detailTerm.status)" round size="small">
              {{ confidenceText(detailTerm.confidence) }}
            </NTag>
          </template>
        </AppDrawerHeader>
      </template>

      <NSkeleton v-if="detailLoading" :repeat="6" text />

      <AppDrawerLayout v-else-if="detailTerm">
        <AppPanelSection eyebrow="Editor" title="术语与释义">
          <div class="slang-detail-grid">
            <label>
              <span>术语</span>
              <NInput v-model:value="detailTerm.term" />
            </label>
            <label>
              <span>作用域</span>
              <NSelect v-model:value="detailTerm.scope" :options="[{ label: '当前群', value: 'group' }, { label: '全局', value: 'global' }]" />
            </label>
            <label v-if="detailTerm.scope === 'group'">
              <span>群号</span>
              <NInput v-model:value="detailTerm.group_id" />
            </label>
            <label>
              <span>状态</span>
              <NSelect v-model:value="detailTerm.status" :options="STATUS_OPTIONS.filter(option => option.value)" />
            </label>
            <label>
              <span>置信度</span>
              <NInputNumber v-model:value="detailTerm.confidence" :min="0" :max="1" :step="0.05" />
            </label>
            <label>
              <span>复述策略</span>
              <NSelect v-model:value="detailTerm.repeat_policy" :options="REPEAT_POLICY_OPTIONS" />
            </label>
            <label class="slang-detail-grid__full">
              <span>释义</span>
              <NInput v-model:value="detailTerm.meaning" type="textarea" :autosize="{ minRows: 3, maxRows: 6 }" />
            </label>
            <label class="slang-detail-grid__full">
              <span>别名（每行一个）</span>
              <NInput v-model:value="editAliases" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" />
            </label>
            <label class="slang-detail-grid__full">
              <span>备注</span>
              <NInput v-model:value="detailTerm.notes" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" />
            </label>
          </div>
        </AppPanelSection>

        <AppPanelSection v-if="isAiApproved(detailTerm)" eyebrow="AI Review" title="AI 通过复核">
          <div class="slang-ai-review-box">
            <div class="slang-ai-review-box__head">
              <div>
                <strong>{{ needsHumanReview(detailTerm) ? '待管理员真实通过或否决' : '已完成人工处理' }}</strong>
                <span>AI 通过会立即参与 Prompt 注入，但仍建议管理员复核来源和释义。</span>
              </div>
              <NTag :type="needsHumanReview(detailTerm) ? 'warning' : 'success'" round size="small">
                {{ needsHumanReview(detailTerm) ? '待复核' : '已处理' }}
              </NTag>
            </div>
            <div class="slang-ai-review-grid">
              <div>
                <span>AI 理由</span>
                <p>{{ detailTerm.meta?.ai_reason || detailTerm.meta?.reason || '未记录' }}</p>
              </div>
              <div>
                <span>群内证据</span>
                <p>{{ detailTerm.meta?.group_evidence || detailTerm.meta?.evidence || '未记录' }}</p>
              </div>
              <div class="slang-ai-review-grid__full">
                <span>搜索查询</span>
                <p>{{ formatSearchQueries(detailTerm) || '未记录' }}</p>
              </div>
              <div class="slang-ai-review-grid__full">
                <span>搜索证据</span>
                <p>{{ detailTerm.meta?.search_evidence || '没有可用搜索证据' }}</p>
              </div>
            </div>
            <NSpace v-if="needsHumanReview(detailTerm)" :size="8">
              <NButton type="success" secondary @click="emit('review-ai', detailTerm, 'human-approve')">
                真实通过
              </NButton>
              <NButton secondary @click="emit('review-ai', detailTerm, 'return-candidate')">
                退回候选
              </NButton>
              <NButton type="error" secondary @click="emit('review-ai', detailTerm, 'deny')">
                否决并静音
              </NButton>
            </NSpace>
          </div>
        </AppPanelSection>

        <AppPanelSection eyebrow="Quality" title="合并与置信度">
          <div class="slang-quality-grid">
            <AppCard bordered embedded class="slang-quality-card">
              <div class="slang-quality-card__head">
                <div>
                  <strong>置信度来源</strong>
                  <span>按出现次数、独立用户、LLM 估计、人工状态和近期活跃重算。</span>
                </div>
                <NButton size="small" secondary @click="emit('recompute-confidence')">
                  重算
                </NButton>
              </div>
              <div class="slang-signal-list">
                <span>出现次数：{{ detailTerm.meta?.confidence_signals?.usage_count ?? '--' }}</span>
                <span>独立用户：{{ detailTerm.meta?.confidence_signals?.unique_users ?? '--' }}</span>
                <span>LLM：{{ detailTerm.meta?.confidence_signals?.llm ?? '--' }}</span>
                <span>人工状态：{{ detailTerm.meta?.confidence_signals?.status ?? '--' }}</span>
              </div>
            </AppCard>

            <AppCard bordered embedded class="slang-quality-card">
              <div class="slang-quality-card__head">
                <div>
                  <strong>合并重复项</strong>
                  <span>把当前词条合并到主词条，观察记录和别名会迁移过去。</span>
                </div>
              </div>
              <NSelect
                v-model:value="mergeTargetId"
                v-model:search-value="mergeSearchText"
                filterable
                remote
                clearable
                :loading="mergeLoading"
                :options="mergeOptions"
                placeholder="搜索并选择主词条"
                @search="(query: string) => emit('search-merge', query)"
              />
              <NButton secondary type="warning" :disabled="!mergeTargetId" @click="emit('merge')">
                合并到主词条
              </NButton>
            </AppCard>
          </div>
        </AppPanelSection>

        <AppPanelSection eyebrow="History" title="修订记录 / 证据链">
          <EmptyState v-if="revisions.length === 0" compact title="暂无修订记录" description="人工编辑、AI 通过、合并和漂移治理会在这里留下前后快照。" :icon="TimeOutline" />
          <div v-else class="slang-revision-list">
            <div v-for="revision in revisions.slice(0, 8)" :key="revision.revision_id" class="slang-revision-row">
              <div class="slang-revision-row__head">
                <strong>{{ revisionActionLabel(revision.action) }}</strong>
                <span>{{ formatTime(revision.created_at) }} · {{ revision.actor || 'system' }}</span>
              </div>
              <p v-if="revision.reason">
                {{ revision.reason }}
              </p>
              <div class="slang-revision-diff">
                <span v-if="revision.before?.meaning !== revision.after?.meaning">
                  释义：{{ revision.before?.meaning || '空' }} → {{ revision.after?.meaning || '空' }}
                </span>
                <span v-if="revision.before?.status !== revision.after?.status">
                  状态：{{ revision.before?.status || '无' }} → {{ revision.after?.status || '无' }}
                </span>
                <span v-if="revision.before?.confidence !== revision.after?.confidence">
                  置信度：{{ confidenceText(revision.before?.confidence || 0) }} → {{ confidenceText(revision.after?.confidence || 0) }}
                </span>
              </div>
            </div>
          </div>
        </AppPanelSection>

        <AppPanelSection eyebrow="Evidence" title="观察记录">
          <EmptyState v-if="observations.length === 0" compact title="暂无观察记录" description="后续命中或抽取会在这里留下证据。" :icon="SearchOutline" />
          <div v-else class="slang-observation-list">
            <div v-for="item in observations" :key="item.observation_id" class="slang-observation">
              <div class="slang-observation__meta">
                <span>{{ formatTime(item.observed_at) }}</span>
                <span>用户 {{ item.user_id || '--' }}</span>
                <span>{{ item.reason || '观察' }}</span>
              </div>
              <p>{{ item.raw_text || item.context }}</p>
            </div>
          </div>
        </AppPanelSection>

        <template #footer>
          <NButton secondary @click="visible = false">
            关闭
          </NButton>
          <NButton type="primary" @click="emit('save')">
            保存修改
          </NButton>
        </template>
      </AppDrawerLayout>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.slang-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-detail-grid label {
  display: grid;
  gap: 8px;
}

.slang-detail-grid span {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-detail-grid__full {
  grid-column: 1 / -1;
}

.slang-ai-review-box {
  display: grid;
  gap: 14px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, rgb(var(--primary-color)) 28%, var(--om-border));
  border-radius: 16px;
  background: color-mix(in srgb, rgba(var(--primary-color), 0.12) 58%, var(--om-surface-solid));
}

.slang-ai-review-box__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.slang-ai-review-box__head > div {
  display: grid;
  gap: 4px;
}

.slang-ai-review-box strong {
  color: var(--om-text-1);
}

.slang-ai-review-box span,
.slang-ai-review-box p {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-ai-review-box p {
  margin: 4px 0 0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.slang-ai-review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-ai-review-grid__full {
  grid-column: 1 / -1;
}

.slang-quality-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-quality-card {
  display: grid;
  align-content: start;
  gap: 12px;
  padding: 14px;
}

.slang-quality-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.slang-quality-card__head strong {
  color: var(--om-text-1);
  font-weight: 700;
}

.slang-quality-card__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-signal-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.slang-signal-list span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-revision-list,
.slang-observation-list {
  display: grid;
  gap: 10px;
}

.slang-revision-row {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-revision-row__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.slang-revision-row__head strong {
  color: var(--om-text-1);
}

.slang-revision-row__head span,
.slang-revision-row p,
.slang-revision-diff span {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-revision-row p {
  margin: 0;
  line-height: 1.6;
}

.slang-revision-diff {
  display: grid;
  gap: 4px;
}

.slang-observation {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-observation__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-observation p {
  margin: 8px 0 0;
  color: var(--om-text-1);
  line-height: 1.65;
}

@media (max-width: 920px) {
  .slang-quality-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .slang-detail-grid,
  .slang-ai-review-grid {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import { AlertCircleOutline, ChevronForwardOutline, PricetagsOutline } from '@vicons/ionicons5'
import { computed } from 'vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import {
  isAiApproved,
  isHumanReviewed,
  needsHumanReview,
  statusLabel,
  statusType,
} from '../helpers/badges'
import { confidenceText } from '../helpers/formatters'
import type { SlangDriftReview, SlangQueueMode, SlangTerm } from '../helpers/types'
import SlangDriftCard from './SlangDriftCard.vue'

const props = defineProps<{
  terms: SlangTerm[]
  driftReviews: SlangDriftReview[]
  queueMode: SlangQueueMode
  pageCount: number
  bulkLoading: boolean
  driftBacklogLoading: boolean
}>()

const page = defineModel<number>('page', { required: true })
const selectedTermIds = defineModel<string[]>('selectedTermIds', { required: true })

const emit = defineEmits<{
  (e: 'open-detail', term: SlangTerm): void
  (e: 'quick-status', term: SlangTerm, action: 'approve' | 'mute' | 'expire'): void
  (e: 'review-ai', term: SlangTerm, action: 'human-approve' | 'deny' | 'return-candidate'): void
  (e: 'drift-action', drift: SlangDriftReview, action: 'accept' | 'reject' | 'alias' | 'mute'): void
  (e: 'bulk-action', action: 'approve' | 'mute' | 'expire' | 'delete_observations'): void
  (e: 'drift-process-backlog'): void
}>()

function handleRowClick(term: SlangTerm) {
  const idx = selectedTermIds.value.indexOf(term.term_id)
  if (idx >= 0) {
    selectedTermIds.value = selectedTermIds.value.filter(id => id !== term.term_id)
  } else {
    selectedTermIds.value = [...selectedTermIds.value, term.term_id]
  }
}

const selectedCount = computed(() => selectedTermIds.value.length)
const pageSelectionChecked = computed(() => (
  props.terms.length > 0 && props.terms.every(term => selectedTermIds.value.includes(term.term_id))
))
const pageSelectionIndeterminate = computed(() => (
  props.terms.some(term => selectedTermIds.value.includes(term.term_id)) && !pageSelectionChecked.value
))

function setPageSelection(checked: boolean) {
  const pageIds = props.terms.map(term => term.term_id)
  if (checked) {
    selectedTermIds.value = Array.from(new Set([...selectedTermIds.value, ...pageIds]))
    return
  }
  selectedTermIds.value = selectedTermIds.value.filter(id => !pageIds.includes(id))
}

function toggleTermSelection(termId: string, checked: boolean) {
  if (checked) {
    selectedTermIds.value = Array.from(new Set([...selectedTermIds.value, termId]))
    return
  }
  selectedTermIds.value = selectedTermIds.value.filter(id => id !== termId)
}
function statusTone(status: SlangTerm['status']): 'success' | 'pending' | 'rejected' | 'neutral' {
  if (status === 'approved') return 'success'
  if (status === 'candidate') return 'pending'
  if (status === 'expired') return 'rejected'
  return 'neutral'
}
</script>

<template>
  <AppPanelSection
    class="slang-list-panel"
    eyebrow="Review Queue"
    title="黑话候选与词表"
  >
    <template v-if="pageCount > 1" #aside>
      <NPagination v-model:page="page" :page-count="pageCount" :page-slot="5" size="small" />
    </template>

    <div v-if="queueMode !== 'drift' && terms.length" class="slang-bulk-bar">
      <NCheckbox
        :checked="pageSelectionChecked"
        :indeterminate="pageSelectionIndeterminate"
        @update:checked="setPageSelection"
      >
        选择本页
      </NCheckbox>
      <span>{{ selectedCount }} 个已选</span>
      <NSpace :size="8">
        <NButton size="small" secondary type="success" :disabled="!selectedCount" :loading="bulkLoading" @click="emit('bulk-action', 'approve')">
          批量批准
        </NButton>
        <NButton size="small" secondary :disabled="!selectedCount" :loading="bulkLoading" @click="emit('bulk-action', 'mute')">
          批量静音
        </NButton>
        <NButton size="small" secondary type="error" :disabled="!selectedCount" :loading="bulkLoading" @click="emit('bulk-action', 'expire')">
          批量过期
        </NButton>
        <NButton size="small" secondary :disabled="!selectedCount" :loading="bulkLoading" @click="emit('bulk-action', 'delete_observations')">
          删除观察
        </NButton>
      </NSpace>
    </div>

    <template v-if="queueMode === 'drift'">
      <div class="slang-drift-toolbar">
        <span class="slang-drift-toolbar__hint">
          AI 一次性复核：把现存 open 漂移交给语义闸，自动同义/别名归档，real_drift 留给你处理。
        </span>
        <NButton
          size="small"
          secondary
          type="primary"
          :loading="driftBacklogLoading"
          :disabled="driftBacklogLoading || driftReviews.length === 0"
          @click="emit('drift-process-backlog')"
        >
          AI 复核存量
        </NButton>
      </div>

      <EmptyState
        v-if="driftReviews.length === 0"
        compact
        title="没有待处理漂移"
        description="当已批准词条出现冲突新释义时，会先进入这里等待治理。"
        :icon="AlertCircleOutline"
      />

      <div v-else class="slang-drift-list">
        <SlangDriftCard
          v-for="drift in driftReviews"
          :key="drift.drift_id"
          :drift="drift"
          @action="(d, a) => emit('drift-action', d, a)"
        />
      </div>
    </template>

    <EmptyState
      v-else-if="terms.length === 0"
      compact
      title="没有匹配的黑话记录"
      description="可以换一组筛选条件，或点击“手动抽取”从近期消息里生成候选。"
      :icon="PricetagsOutline"
    />

    <div v-else class="slang-term-list">
      <div
        v-for="term in terms"
        :key="term.term_id"
        class="slang-term-row"
        :class="[
          `slang-term-row--${statusTone(term.status)}`,
          { 'slang-term-row--selected': selectedTermIds.includes(term.term_id) },
        ]"
        @click="handleRowClick(term)"
      >
        <div class="slang-term-row__check" @click.stop @mousedown.stop>
          <NCheckbox
            :checked="selectedTermIds.includes(term.term_id)"
            @update:checked="(v: boolean) => toggleTermSelection(term.term_id, v)"
          />
        </div>
        <div class="slang-term-row__body">
          <strong class="slang-term-row__term">{{ term.term }}</strong>
          <NTag v-for="alias in term.aliases.slice(0, 3)" :key="alias" class="slang-term-row__alias" size="tiny" round>
            {{ alias }}
          </NTag>
          <span class="slang-term-row__meaning">{{ term.meaning || '—' }}</span>
        </div>
        <button
          class="slang-term-row__config"
          @click.stop="emit('open-detail', term)"
        >
          <span class="slang-term-row__config-icon">
            <NIcon :component="ChevronForwardOutline" />
          </span>
          <span class="slang-term-row__config-text">配置</span>
        </button>
        <span class="slang-term-row__status">
          <NTag :type="statusType(term.status)" round size="small">
            {{ statusLabel(term.status) }}
          </NTag>
        </span>
        <span class="slang-term-row__conf">{{ confidenceText(term.confidence) }}</span>
        <span class="slang-term-row__meta">{{ term.usage_count }}次 · {{ term.unique_user_count }}人</span>
        <div class="slang-term-row__actions" @click.stop>
          <!--
            Action mapping is mutually exclusive per row state — each state shows
            the 1–2 buttons that are actually meaningful, not all possible verbs.
            Avoids the old 4-slot grid where 通过/否决 (AI flow) and 批准/静音
            (direct flow) overlapped semantically and rendered awkward gaps.
          -->
          <template v-if="needsHumanReview(term)">
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" type="success" secondary @click="emit('review-ai', term, 'human-approve')">
                  通过
                </NButton>
              </template>
              人工复核 AI 已批的词条：确认后转 human_reviewed，不再回到队列。
            </NTooltip>
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" secondary type="error" @click="emit('review-ai', term, 'deny')">
                  否决
                </NButton>
              </template>
              人工复核 AI 已批的词条：拒绝后转 muted，不再注入。
            </NTooltip>
          </template>
          <template v-else-if="term.status === 'candidate'">
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" type="success" secondary @click="emit('quick-status', term, 'approve')">
                  批准
                </NButton>
              </template>
              直接批准候选：跳过 AI 流程，立即转 approved 可注入。
            </NTooltip>
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" secondary @click="emit('quick-status', term, 'mute')">
                  静音
                </NButton>
              </template>
              静音候选：转 muted，不再进入审核或注入。
            </NTooltip>
          </template>
          <template v-else-if="term.status === 'approved'">
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" secondary @click="emit('quick-status', term, 'mute')">
                  静音
                </NButton>
              </template>
              已批准词条 — 静音以停止注入（仍可在详情里恢复）。
            </NTooltip>
          </template>
          <template v-else-if="term.status === 'muted'">
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" type="success" secondary @click="emit('quick-status', term, 'approve')">
                  恢复
                </NButton>
              </template>
              已静音词条 — 恢复后转 approved 可注入。
            </NTooltip>
          </template>
          <template v-else-if="term.status === 'expired'">
            <NTooltip placement="top">
              <template #trigger>
                <NButton size="tiny" type="success" secondary @click="emit('quick-status', term, 'approve')">
                  批准
                </NButton>
              </template>
              已过期词条 — 重新批准后回到 approved 状态。
            </NTooltip>
          </template>
        </div>
      </div>
    </div>

    <div v-if="pageCount > 1" class="slang-pagination-bottom">
      <NPagination v-model:page="page" :page-count="pageCount" :page-slot="7" />
    </div>
  </AppPanelSection>
</template>

<style scoped>
.slang-bulk-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: -4px 0 12px;
  padding: 10px 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--om-surface-solid) 78%, transparent);
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-drift-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: -4px 0 10px;
  padding: 8px 14px;
  border: 1px solid color-mix(in srgb, var(--om-warning) 28%, var(--om-border));
  border-radius: 10px;
  background: color-mix(in srgb, var(--om-warning) 6%, var(--om-surface-solid));
  color: var(--om-text-2);
  font-size: 12px;
}

.slang-drift-toolbar__hint {
  flex: 1 1 auto;
  min-width: 0;
}

.slang-term-list {
  display: grid;
  gap: 8px;
}

.slang-drift-list {
  display: grid;
  gap: 8px;
}

.slang-term-row {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto 60px 50px 80px auto;
  align-items: center;
  gap: 10px;
  padding: 10px 14px 10px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.slang-term-row::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.slang-term-row--success::before { background: var(--om-success); opacity: 1; }
.slang-term-row--pending::before { background: var(--om-warning); opacity: 1; }
.slang-term-row--rejected::before { background: var(--om-danger); opacity: 0.85; }

.slang-term-row:hover {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.slang-term-row--selected {
  border-color: var(--om-info);
  background: color-mix(in srgb, var(--om-info) 6%, var(--om-surface-solid));
}

.slang-term-row--selected:hover {
  border-color: var(--om-info);
}

.slang-term-row__check {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
  width: 36px;
  height: 100%;
  margin: -10px -5px -10px -18px;
  padding: 10px 5px 10px 18px;
  cursor: pointer;
}

.slang-term-row__body {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.slang-term-row__term {
  color: var(--om-info);
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
}

.slang-term-row__alias {
  flex-shrink: 0;
}

.slang-term-row__meaning {
  overflow: hidden;
  color: var(--om-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.slang-term-row__status {
  display: flex;
  justify-content: flex-end;
}

.slang-term-row__conf {
  color: var(--om-text-2);
  font-size: 12px;
  text-align: right;
  white-space: nowrap;
}

.slang-term-row__meta {
  color: var(--om-text-3);
  font-size: 11px;
  text-align: right;
  white-space: nowrap;
}

.slang-term-row__config {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 12px 3px 6px;
  border: 1.5px solid color-mix(in srgb, var(--om-info) 32%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
  color: var(--om-info);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.slang-term-row__config-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--om-info);
  color: #fff;
  font-size: 14px;
}

.slang-term-row__config:hover {
  border-color: var(--om-info);
  background: var(--om-info);
  color: #fff;
}

.slang-term-row__config:hover .slang-term-row__config-icon {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.slang-term-row__actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  width: 130px;
  justify-content: flex-end;
}

.slang-term-row__actions :deep(.n-button) {
  min-width: 56px;
}

.slang-pagination-bottom {
  display: flex;
  justify-content: center;
  margin-top: 14px;
}

@media (max-width: 1000px) {
  .slang-term-row {
    grid-template-columns: auto minmax(0, 1fr) auto 60px 50px auto;
  }

  .slang-term-row__meta {
    display: none;
  }
}

@media (max-width: 640px) {
  .slang-term-row {
    grid-template-columns: auto minmax(0, 1fr) auto auto;
  }

  .slang-term-row__status,
  .slang-term-row__conf,
  .slang-term-row__meta {
    display: none;
  }
}
</style>

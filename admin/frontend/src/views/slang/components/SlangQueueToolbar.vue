<script setup lang="ts">
import { SearchOutline } from '@vicons/ionicons5'

import { CONFIDENCE_OPTIONS } from '../helpers/badges'
import type { SlangQueueMode, SlangSummary } from '../helpers/types'

const props = defineProps<{
  summary: SlangSummary
  groups: string[]
  displayTotal: number
  scanningGlobal: boolean
  embedded?: boolean
}>()

const emit = defineEmits<{
  (e: 'reset'): void
  (e: 'scan-global'): void
}>()

const searchText = defineModel<string>('searchText', { default: '' })
const groupFilter = defineModel<string>('groupFilter', { default: '' })
const queueMode = defineModel<SlangQueueMode>('queueMode', { default: 'candidate' })
const minConfidence = defineModel<string>('minConfidence', { default: '' })
const sortBy = defineModel<'updated_desc' | 'confidence_desc' | 'usage_desc' | 'updated_asc'>('sortBy', { default: 'updated_desc' })

const SORT_OPTIONS = [
  { label: '最近更新（新→旧）', value: 'updated_desc' as const },
  { label: '置信度（高→低）', value: 'confidence_desc' as const },
  { label: '使用次数（多→少）', value: 'usage_desc' as const },
  { label: '最早更新（旧→新）', value: 'updated_asc' as const },
]

const pendingCount = computed(() => (
  props.summary.candidate_unreviewed_count
  + props.summary.under_observation_count
  + props.summary.drift_count
))

const eligibleCount = computed(() => props.summary.eligible_backlog_count)
const collectingCount = computed(() => Math.max(0, pendingCount.value - eligibleCount.value))

const totalQueueCount = computed(() => (
  props.summary.candidate_count
  + props.summary.approved_count
  + props.summary.muted_count
  + props.summary.expired_count
))

const queueOptions = computed(() => [
  {
    label: '待清池',
    value: 'candidate' as const,
    count: pendingCount.value,
    subCounts: [
      { label: '可审核', count: eligibleCount.value },
      { label: '采集中', count: collectingCount.value },
    ],
    tooltip: '可审核 = 满足最低使用次数，AI 可以判断。采集中 = 还在等待更多证据',
  },
  {
    label: '语义漂移',
    value: 'drift' as const,
    count: props.summary.drift_count,
  },
  {
    label: '已批准',
    value: 'approved' as const,
    count: props.summary.approved_count,
  },
  {
    label: '已否决',
    value: 'ai_rejected' as const,
    count: props.summary.ai_rejected_count,
  },
  {
    label: '全部',
    value: 'all' as const,
    count: totalQueueCount.value,
  },
])

const groupOptions = computed(() => [
  { label: '全部群', value: '' },
  ...props.groups.map(group => ({ label: `群 ${group}`, value: group })),
])

function setQueueMode(value: SlangQueueMode) {
  if (queueMode.value === value) return
  queueMode.value = value
}
</script>

<template>
  <div class="slang-control-strip">
    <div v-if="!props.embedded" class="slang-control-strip__segments" role="tablist" aria-label="黑话审核队列">
      <NTooltip v-for="option in queueOptions" :key="option.value" :disabled="!option.tooltip" placement="top">
        <template #trigger>
          <button
            class="slang-segment-button"
            :class="{ 'slang-segment-button--active': queueMode === option.value }"
            type="button"
            role="tab"
            :aria-selected="queueMode === option.value"
            @click="setQueueMode(option.value)"
          >
            <span>{{ option.label }}</span>
            <template v-if="option.subCounts">
              <strong class="slang-sub-eligible">{{ option.subCounts[0].count }}</strong>
              <strong class="slang-sub-pending">{{ option.subCounts[1].count }}</strong>
            </template>
            <strong v-else>{{ option.count }}</strong>
          </button>
        </template>
        {{ option.tooltip }}
      </NTooltip>
    </div>

    <div class="slang-control-strip__filters">
      <NInput v-model:value="searchText" class="slang-filter-control slang-filter-control--search" clearable placeholder="搜索词、释义或别名">
        <template #prefix>
          <NIcon :component="SearchOutline" />
        </template>
      </NInput>
      <NSelect v-model:value="groupFilter" class="slang-filter-control" :options="groupOptions" />
      <NSelect v-model:value="minConfidence" class="slang-filter-control slang-filter-control--compact" :options="CONFIDENCE_OPTIONS" />
      <NSelect v-model:value="sortBy" class="slang-filter-control" :options="SORT_OPTIONS" />
    </div>

    <div class="slang-control-strip__actions">
      <NButton secondary class="slang-soft-action" @click="emit('reset')">
        重置
      </NButton>
      <NButton secondary class="slang-soft-action" :loading="scanningGlobal" @click="emit('scan-global')">
        跨群扫描
      </NButton>
      <NTag round size="small" class="slang-total-tag">
        {{ displayTotal }} 条记录
      </NTag>
    </div>
  </div>
</template>

<style scoped>
.slang-control-strip {
  display: grid;
  grid-template-columns: minmax(0, max-content) minmax(360px, 1fr) minmax(0, auto);
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--om-border) 86%, rgb(var(--primary-color)));
  border-radius: 18px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--om-surface-solid) 94%, transparent), color-mix(in srgb, rgb(var(--primary-color)) 5%, var(--om-surface-solid))),
    var(--om-surface-2);
  box-shadow: 0 10px 28px rgba(23, 42, 48, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.48);
}

.slang-control-strip__segments,
.slang-control-strip__filters,
.slang-control-strip__actions {
  min-width: 0;
}

.slang-control-strip__segments {
  display: inline-grid;
  grid-template-columns: repeat(5, minmax(48px, max-content));
  gap: 2px;
  padding: 3px;
  border: 1px solid color-mix(in srgb, var(--om-border) 82%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-segment-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  gap: 6px;
  padding: 0 10px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.slang-segment-button:hover {
  color: var(--om-text-1);
  background: color-mix(in srgb, rgb(var(--primary-color)) 8%, transparent);
}

.slang-segment-button--active {
  color: var(--om-text-1);
  background: color-mix(in srgb, rgb(var(--primary-color)) 16%, var(--om-surface-solid));
  box-shadow: 0 6px 14px rgba(23, 42, 48, 0.08);
}

.slang-segment-button strong {
  min-width: 24px;
  padding: 2px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-surface-solid) 84%, transparent);
  color: inherit;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
}

.slang-sub-eligible {
  background: color-mix(in srgb, var(--om-success) 13%, transparent) !important;
  color: var(--om-success) !important;
}

.slang-sub-pending {
  background: color-mix(in srgb, var(--om-text-3) 10%, transparent) !important;
  color: var(--om-text-3) !important;
}

.slang-control-strip__filters {
  display: grid;
  grid-template-columns: minmax(160px, 1.6fr) minmax(110px, 0.8fr) minmax(110px, 0.7fr) minmax(150px, 1fr);
  gap: 6px;
}

.slang-filter-control {
  min-width: 0;
}

.slang-filter-control :deep(.n-input),
.slang-filter-control :deep(.n-base-selection) {
  --n-height: 36px;
  border-radius: 999px;
}

.slang-filter-control :deep(.n-input__border),
.slang-filter-control :deep(.n-input__state-border),
.slang-filter-control :deep(.n-base-selection__border),
.slang-filter-control :deep(.n-base-selection__state-border) {
  border-radius: 999px;
}

.slang-control-strip__actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.slang-soft-action {
  border-radius: 999px;
  white-space: nowrap;
}

.slang-total-tag {
  min-height: 30px;
  padding: 0 10px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
  white-space: nowrap;
}

@media (max-width: 1280px) {
  .slang-control-strip {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .slang-control-strip__segments {
    grid-column: 1 / -1;
    width: 100%;
    grid-template-columns: repeat(5, minmax(60px, 1fr));
  }
}

@media (max-width: 920px) {
  .slang-control-strip {
    grid-template-columns: 1fr;
  }

  .slang-control-strip__filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-control-strip__actions {
    justify-content: flex-start;
  }
}

@media (max-width: 640px) {
  .slang-control-strip {
    padding: 8px;
    border-radius: 16px;
  }

  .slang-control-strip__segments,
  .slang-control-strip__filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-control-strip__actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .slang-total-tag {
    justify-content: center;
    grid-column: 1 / -1;
  }
}
</style>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../../../api/client'
import AppCard from '../../../components/common/AppCard.vue'
import type { SlangExtractionRun } from '../helpers/types'

const runs = ref<SlangExtractionRun[]>([])
let pollTimer: ReturnType<typeof setInterval> | null = null

const extractionRuns = computed(() =>
  runs.value.filter(r => !r.meta?.kind || r.meta.kind === 'extraction'),
)

const aiReviewRuns = computed(() =>
  runs.value.filter(r => r.meta?.kind === 'backlog_ai_review'),
)

const latestExtraction = computed(() => extractionRuns.value[0] || null)
const latestAiReview = computed(() => aiReviewRuns.value[0] || null)

const hasRunning = computed(() => runs.value.some(r => r.status === 'running'))

async function fetchRuns() {
  try {
    const data = await api('/api/admin/slang/extract/runs', { params: { limit: 10 } })
    runs.value = data.runs || []
  } catch { /* silent */ }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(fetchRuns, hasRunning.value ? 3000 : 15000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// Re-adjust poll interval when running state changes
watch(hasRunning, () => {
  startPolling()
})

function shortTime(iso: string): string {
  if (!iso) return '--'
  return iso.slice(11, 16)
}

onMounted(async () => {
  await fetchRuns()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="slang-ep">
    <AppCard bordered embedded class="slang-ep__card">
      <div class="slang-ep__head">
        <span>AI 抽取</span>
        <NTag v-if="latestExtraction" :type="latestExtraction.status === 'failed' ? 'error' : latestExtraction.status === 'running' ? 'warning' : 'success'" round size="small">
          {{ latestExtraction.status }}
        </NTag>
        <NTag v-else round size="small">未运行</NTag>
      </div>
      <div v-if="extractionRuns.length" class="slang-ep__list">
        <div v-for="run in extractionRuns.slice(0, 3)" :key="run.run_id" class="slang-ep__row">
          <strong>{{ shortTime(run.started_at) }}</strong>
          <span>{{ run.scanned_messages }} 扫 · {{ run.extracted_terms }} 抽 · {{ run.promoted_candidates }} 入库</span>
        </div>
      </div>
      <div v-else class="slang-ep__empty">暂无抽取记录</div>
    </AppCard>

    <AppCard bordered embedded class="slang-ep__card">
      <div class="slang-ep__head">
        <span>AI 清池</span>
        <NTag v-if="latestAiReview" :type="latestAiReview.status === 'failed' ? 'error' : latestAiReview.status === 'running' ? 'warning' : 'success'" round size="small">
          {{ latestAiReview.status === 'running' ? '执行中...' : 'AI 清池' }}
        </NTag>
        <NTag v-else round size="small">未运行</NTag>
      </div>
      <div v-if="aiReviewRuns.length" class="slang-ep__list">
        <div v-for="run in aiReviewRuns.slice(0, 3)" :key="run.run_id" class="slang-ep__row">
          <strong>{{ shortTime(run.started_at) }}</strong>
          <span>
            #{{ run.meta?.batch_index || '?' }} · {{ run.meta?.approved_in_batch || 0 }} 通过 · {{ run.meta?.muted_in_batch || 0 }} 否决
          </span>
        </div>
      </div>
      <div v-else class="slang-ep__empty">暂无复核记录</div>
    </AppCard>
  </div>
</template>

<style scoped>
.slang-ep {
  display: grid;
  gap: 12px;
}

.slang-ep__card {
  padding: 12px;
}

.slang-ep__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.slang-ep__head span {
  color: var(--om-text-1);
  font-size: 12px;
  font-weight: 700;
}

.slang-ep__list {
  display: grid;
  gap: 8px;
}

.slang-ep__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px solid color-mix(in srgb, var(--om-border) 72%, transparent);
}

.slang-ep__row:first-child {
  border-top: 0;
}

.slang-ep__row strong {
  color: var(--om-text-1);
  font-size: 12px;
  white-space: nowrap;
}

.slang-ep__row span {
  color: var(--om-text-3);
  font-size: 11px;
  text-align: right;
}

.slang-ep__empty {
  font-size: 11px;
  color: var(--om-text-3);
}
</style>

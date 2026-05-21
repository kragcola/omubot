<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../../../api/client'
import type { SlangBacklogState } from '../helpers/types'

const message = useMessage()

const props = defineProps<{
  eligibleCount: number
}>()

const emit = defineEmits<{
  (e: 'progress'): void
}>()

const state = ref<SlangBacklogState>({
  active: false,
  processed: 0,
  approved: 0,
  muted: 0,
  kept: 0,
  total_at_start: 0,
  remaining: 0,
  started_at: '',
  last_progress_at: '',
  last_run_id: '',
  last_done_at: '',
})

const loading = ref(false)
const runLoading = ref(false)
const resetLoading = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const percent = computed(() => {
  if (state.value.active) {
    const total = state.value.total_at_start
    if (!total) return 0
    return Math.min(100, Math.round((state.value.processed / total) * 100))
  }
  if (props.eligibleCount === 0 && state.value.last_done_at) return 100
  return 0
})

const isIdleEmpty = computed(() => !state.value.active && props.eligibleCount === 0)
const isComplete = computed(() => !state.value.active && isIdleEmpty.value && !!state.value.last_done_at)
const shouldShow = computed(() => state.value.active || !!state.value.last_done_at || props.eligibleCount > 0)

async function fetchStatus() {
  try {
    const data = await api('/api/admin/slang/backlog-review/status')
    if (data && data.ok !== false) {
      const wasActive = state.value.active
      const oldProcessed = state.value.processed
      const oldRemaining = state.value.remaining
      Object.assign(state.value, data)
      const changed = state.value.processed !== oldProcessed || state.value.remaining !== oldRemaining
      const justFinished = wasActive && !state.value.active
      if (changed || justFinished) {
        emit('progress')
      }
    }
  } catch { /* silent */ }
}

async function triggerRun() {
  if (runLoading.value) return
  runLoading.value = true
  try {
    const data = await api('/api/admin/slang/backlog-review/run', { method: 'POST', body: {} })
    await fetchStatus()
    emit('progress')
    if (data && !data.ok) {
      message.error(data.error || 'AI 清池执行失败')
    }
  } catch (error: any) {
    const msg = error?.data?.error || error?.message || 'AI 清池执行失败'
    message.error(msg)
  } finally {
    runLoading.value = false
  }
}

async function triggerReset() {
  resetLoading.value = true
  try {
    await api('/api/admin/slang/backlog-review/reset', { method: 'POST', body: {} })
    await fetchStatus()
    emit('progress')
    message.success('已重置')
  } catch (error: any) {
    const msg = error?.data?.error || error?.message || '重置失败'
    message.error(msg)
  } finally {
    resetLoading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(fetchStatus, state.value.active ? 3000 : 8000)
}

watch(() => state.value.active, () => {
  startPolling()
})

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function formatTime(iso: string): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(11, 16)
}

onMounted(async () => {
  await fetchStatus()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div v-if="shouldShow" class="slang-backlog-progress">
    <div class="slang-backlog-progress__header">
      <span class="slang-backlog-progress__title">AI 清池进度</span>
      <span v-if="state.active" class="slang-backlog-progress__stats">
        已复核 {{ state.processed }} / {{ state.total_at_start }}
      </span>
      <span v-else-if="isComplete" class="slang-backlog-progress__stats slang-backlog-progress__stats--done">
        上轮完成 {{ state.processed }} 条
      </span>
      <span v-else-if="props.eligibleCount > 0" class="slang-backlog-progress__stats">
        可审核 {{ props.eligibleCount }} 条
      </span>
    </div>

    <NProgress :percentage="percent" type="line" :show-indicator="false" :height="6" />

    <div class="slang-backlog-progress__row">
      <NTag size="tiny" type="success" round>通过 {{ state.approved }}</NTag>
      <NTag size="tiny" round>否决 {{ state.muted }}</NTag>
      <NTag size="tiny" type="warning" round>暂留 {{ state.kept }}</NTag>
      <span class="slang-backlog-progress__time">
        {{ state.last_progress_at ? `上次 ${formatTime(state.last_progress_at)}` : '' }}
      </span>
      <span class="slang-backlog-progress__actions">
        <NButton size="tiny" secondary :loading="runLoading" :disabled="runLoading" @click="triggerRun">
          立即跑一批
        </NButton>
        <NPopconfirm @positive-click="triggerReset">
          <template #trigger>
            <NButton size="tiny" secondary :loading="resetLoading" :disabled="resetLoading">
              {{ isComplete ? '重新开始' : '重置' }}
            </NButton>
          </template>
          确定要重置 AI 清池进度？
        </NPopconfirm>
      </span>
    </div>
  </div>
</template>

<style scoped>
.slang-backlog-progress {
  padding: 10px 14px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  margin-bottom: 12px;
}

.slang-backlog-progress__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.slang-backlog-progress__title {
  font-size: 12px;
  font-weight: 700;
  color: var(--om-text-1);
}

.slang-backlog-progress__stats {
  font-size: 12px;
  color: var(--om-text-2);
}

.slang-backlog-progress__stats--done {
  color: var(--om-success);
}

.slang-backlog-progress__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.slang-backlog-progress__time {
  font-size: 11px;
  color: var(--om-text-3);
}

.slang-backlog-progress__actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}
</style>

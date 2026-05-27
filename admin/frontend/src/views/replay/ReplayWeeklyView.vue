<script setup lang="ts">
import {
  AnalyticsOutline,
  RefreshOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { NButton, NIcon, NTag, useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'

interface ReplayRun {
  run_id: string
  group_id: string
  sample_count: number
  summary: Record<string, number>
  created_at: number
}

const loading = ref(false)
const runs = ref<ReplayRun[]>([])
const errorText = ref('')
const lastLoadedAt = ref('')
const message = useMessage()

const totalRuns = computed(() => runs.value.length)
const totalSamples = computed(() => runs.value.reduce((sum, run) => sum + Number(run.sample_count || 0), 0))
const counterfactualWins = computed(() =>
  runs.value.reduce((sum, run) => sum + Number(run.summary?.counterfactual_better || 0), 0),
)

onMounted(() => {
  void loadRuns()
})

async function loadRuns() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api('/api/admin/replay/weekly')
    runs.value = data.runs || []
    errorText.value = data.error || ''
    lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
    if (errorText.value) message.warning(errorText.value)
  } catch (error) {
    console.error('Failed to load replay reports:', error)
    errorText.value = '重放报表加载失败'
    runs.value = []
  } finally {
    loading.value = false
  }
}

function formatTime(value: number) {
  if (!value) return '--'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <AppPage
    title="反事实重放"
    eyebrow="Scheduler Replay"
    description="查看调度器静默与发言的反事实评审摘要，用于后续人工标注和阈值校准。"
  >
    <template #action>
      <div class="replay-actions">
        <NTag round size="small" :type="errorText ? 'error' : 'success'">
          {{ errorText ? '报表异常' : `已载入 ${totalRuns} 次运行` }}
        </NTag>
        <NTag v-if="lastLoadedAt" round size="small">
          更新于 {{ lastLoadedAt }}
        </NTag>
        <NButton secondary :loading="loading" @click="loadRuns">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
      </div>
    </template>

    <div class="replay-metric-grid">
      <MetricCard
        title="运行次数"
        :value="totalRuns"
        hint="已写入 scheduler_replay.db 的报表运行"
        :icon="AnalyticsOutline"
        accent="primary"
      />
      <MetricCard
        title="样本数"
        :value="totalSamples"
        hint="进入反事实评审的调度样本"
        :icon="TimeOutline"
        accent="info"
      />
      <MetricCard
        title="反事实更优"
        :value="counterfactualWins"
        hint="judge 认为另一种决策更合适的样本"
        :icon="AnalyticsOutline"
        accent="warning"
      />
    </div>

    <AppPanelSection
      eyebrow="Replay Runs"
      title="周报运行"
      description="离线任务写入后，这里会按时间倒序展示每次运行的摘要。"
    >
      <div v-if="runs.length" class="replay-run-list">
        <div v-for="run in runs" :key="run.run_id" class="replay-run-row">
          <div>
            <strong>{{ run.run_id }}</strong>
            <span>群 {{ run.group_id }} · {{ formatTime(run.created_at) }}</span>
          </div>
          <div class="replay-run-row__metrics">
            <NTag size="small" round>样本 {{ run.sample_count }}</NTag>
            <NTag size="small" round type="success">
              原决策 {{ run.summary?.real_better || 0 }}
            </NTag>
            <NTag size="small" round type="warning">
              反事实 {{ run.summary?.counterfactual_better || 0 }}
            </NTag>
            <NTag size="small" round>
              接近 {{ run.summary?.indistinguishable || 0 }}
            </NTag>
          </div>
        </div>
      </div>
      <EmptyState
        v-else
        :icon="AnalyticsOutline"
        title="还没有重放报表"
        description="离线重放任务写入结果后，这里会出现每周摘要。"
        compact
      />
    </AppPanelSection>
  </AppPage>
</template>

<style scoped>
.replay-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
}

.replay-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.replay-run-list {
  display: grid;
  gap: 8px;
}

.replay-run-row {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 34%, transparent);
}

.replay-run-row strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.replay-run-row span {
  display: block;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
}

.replay-run-row__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

@media (max-width: 900px) {
  .replay-metric-grid {
    grid-template-columns: 1fr;
  }

  .replay-run-row {
    flex-direction: column;
    align-items: stretch;
  }

  .replay-run-row__metrics {
    justify-content: flex-start;
  }
}
</style>

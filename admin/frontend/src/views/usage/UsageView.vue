<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  FlashOutline,
  PeopleOutline,
  RefreshOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'

interface UsageSummary {
  total_calls?: number
  total_input_tokens?: number
  total_output_tokens?: number
  cache_read_tokens?: number
  error_count?: number
  avg_elapsed_s?: number
}

interface UsageActor {
  user_id?: string
  group_id?: string
  id?: string
  calls?: number
  total_input?: number
  total_output?: number
}

const loading = ref(true)
const refreshing = ref(false)
const today = ref<UsageSummary | null>(null)
const month = ref<UsageSummary | null>(null)
const topUsers = ref<UsageActor[]>([])
const topGroups = ref<UsageActor[]>([])
const lastLoadedAt = ref('')

const message = useMessage()

const todayTotalTokens = computed(() =>
  (today.value?.total_input_tokens || 0) + (today.value?.total_output_tokens || 0),
)

const monthTotalTokens = computed(() =>
  (month.value?.total_input_tokens || 0) + (month.value?.total_output_tokens || 0),
)

const cacheHitRate = computed(() => {
  const totalInput = today.value?.total_input_tokens || 0
  const cached = today.value?.cache_read_tokens || 0
  if (!totalInput) return '--'
  return `${Math.round((cached / totalInput) * 100)}%`
})

onMounted(() => {
  void loadUsage()
})

async function loadUsage(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const results = await Promise.allSettled([
      api('/api/usage/today'),
      api('/api/usage/month'),
      api('/api/usage/top-users'),
      api('/api/usage/top-groups'),
    ])

    today.value = results[0].status === 'fulfilled' ? results[0].value : null
    month.value = results[1].status === 'fulfilled' ? results[1].value : null
    topUsers.value = results[2].status === 'fulfilled' ? (results[2].value.users || results[2].value || []) : []
    topGroups.value = results[3].status === 'fulfilled' ? (results[3].value.groups || results[3].value || []) : []

    if (results.every(result => result.status === 'rejected')) {
      message.error('用量统计加载失败')
    }

    lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function formatNumber(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return value.toLocaleString('zh-CN')
}

function formatLatency(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return `${value.toFixed(1)}s`
}

function actorLabel(actor: UsageActor, type: 'user' | 'group') {
  if (type === 'user') return actor.user_id || actor.id || 'unknown'
  return actor.group_id || actor.id || 'unknown'
}
</script>

<template>
  <AppPage
    title="用量统计"
    eyebrow="Usage Snapshot"
    description="快速查看今日与本月调用情况，并对近 7 天的高活跃用户和群聊做抽样巡检。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag round size="small">
          {{ lastLoadedAt ? `更新于 ${lastLoadedAt}` : '等待加载' }}
        </NTag>
        <NButton secondary :loading="refreshing" @click="loadUsage(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新统计
        </NButton>
        <RestartBotButton />
      </NSpace>
    </template>

    <NSkeleton v-if="loading" :repeat="6" text />

    <template v-else>
      <div class="usage-metric-grid">
        <MetricCard
          title="今日调用"
          :value="formatNumber(today?.total_calls)"
          hint="当前自然日内记录到的总调用次数"
          :icon="TimeOutline"
          accent="primary"
        />
        <MetricCard
          title="今日总 Token"
          :value="formatNumber(todayTotalTokens)"
          :hint="`输入 ${formatNumber(today?.total_input_tokens)} · 输出 ${formatNumber(today?.total_output_tokens)}`"
          :icon="FlashOutline"
          accent="success"
        />
        <MetricCard
          title="本月调用"
          :value="formatNumber(month?.total_calls)"
          hint="本月累计调用次数"
          :icon="PeopleOutline"
          accent="warning"
        />
        <MetricCard
          title="本月总 Token"
          :value="formatNumber(monthTotalTokens)"
          :hint="`输入 ${formatNumber(month?.total_input_tokens)} · 输出 ${formatNumber(month?.total_output_tokens)}`"
          :icon="ChatbubbleEllipsesOutline"
          accent="info"
        />
      </div>

      <div class="usage-layout">
        <AppCard bordered elevated class="usage-summary">
          <div class="usage-section__head">
            <div>
              <p class="usage-section__eyebrow">
                Runtime Notes
              </p>
              <h3 class="usage-section__title">
                今日运行补充信息
              </h3>
            </div>
          </div>

          <div class="usage-summary-grid">
            <div class="usage-summary-item">
              <span>Cache 命中率</span>
              <strong>{{ cacheHitRate }}</strong>
            </div>
            <div class="usage-summary-item">
              <span>平均延迟</span>
              <strong>{{ formatLatency(today?.avg_elapsed_s) }}</strong>
            </div>
            <div class="usage-summary-item">
              <span>错误数</span>
              <strong>{{ formatNumber(today?.error_count) }}</strong>
            </div>
            <div class="usage-summary-item">
              <span>本月输入</span>
              <strong>{{ formatNumber(month?.total_input_tokens) }}</strong>
            </div>
            <div class="usage-summary-item">
              <span>本月输出</span>
              <strong>{{ formatNumber(month?.total_output_tokens) }}</strong>
            </div>
            <div class="usage-summary-item">
              <span>排行窗口</span>
              <strong>近 7 天</strong>
            </div>
          </div>
        </AppCard>

        <div class="usage-rank-grid">
          <AppCard bordered elevated class="usage-rank-card">
            <div class="usage-section__head">
              <div>
                <p class="usage-section__eyebrow">
                  Top Users
                </p>
                <h3 class="usage-section__title">
                  活跃用户 Top 10
                </h3>
              </div>
              <NTag size="small" round>
                {{ topUsers.length }} 人
              </NTag>
            </div>

            <div v-if="topUsers.length > 0" class="usage-rank-list">
              <div
                v-for="(user, index) in topUsers.slice(0, 10)"
                :key="`${actorLabel(user, 'user')}-${index}`"
                class="usage-rank-item"
              >
                <NTag round size="small" :type="index < 3 ? 'success' : 'default'">
                  #{{ index + 1 }}
                </NTag>
                <div class="usage-rank-item__copy">
                  <strong>{{ actorLabel(user, 'user') }}</strong>
                  <span>{{ formatNumber(user.calls) }} 次调用</span>
                </div>
                <div class="usage-rank-item__stats">
                  <span>Input {{ formatNumber(user.total_input) }}</span>
                  <span>Output {{ formatNumber(user.total_output) }}</span>
                </div>
              </div>
            </div>
            <EmptyState
              v-else
              compact
              title="还没有用户排行数据"
              description="当前统计窗口内没有记录到用户侧调用。"
              :icon="PeopleOutline"
            />
          </AppCard>

          <AppCard bordered elevated class="usage-rank-card">
            <div class="usage-section__head">
              <div>
                <p class="usage-section__eyebrow">
                  Top Groups
                </p>
                <h3 class="usage-section__title">
                  活跃群 Top 10
                </h3>
              </div>
              <NTag size="small" round>
                {{ topGroups.length }} 群
              </NTag>
            </div>

            <div v-if="topGroups.length > 0" class="usage-rank-list">
              <div
                v-for="(group, index) in topGroups.slice(0, 10)"
                :key="`${actorLabel(group, 'group')}-${index}`"
                class="usage-rank-item"
              >
                <NTag round size="small" :type="index < 3 ? 'success' : 'default'">
                  #{{ index + 1 }}
                </NTag>
                <div class="usage-rank-item__copy">
                  <strong>{{ actorLabel(group, 'group') }}</strong>
                  <span>{{ formatNumber(group.calls) }} 次调用</span>
                </div>
                <div class="usage-rank-item__stats">
                  <span>Input {{ formatNumber(group.total_input) }}</span>
                  <span>Output {{ formatNumber(group.total_output) }}</span>
                </div>
              </div>
            </div>
            <EmptyState
              v-else
              compact
              title="还没有群排行数据"
              description="当前统计窗口内没有记录到群聊侧调用。"
              :icon="ChatbubbleEllipsesOutline"
            />
          </AppCard>
        </div>
      </div>
    </template>
  </AppPage>
</template>

<style scoped>
.usage-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.usage-layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.usage-summary,
.usage-rank-card {
  padding: 20px;
}

.usage-rank-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.usage-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.usage-section__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.usage-section__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 700;
}

.usage-summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.usage-summary-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.usage-summary-item span {
  color: var(--om-text-3);
  font-size: 12px;
}

.usage-summary-item strong {
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.usage-rank-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.usage-rank-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 32%, transparent);
}

.usage-rank-item__copy {
  min-width: 0;
}

.usage-rank-item__copy strong {
  display: block;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
  word-break: break-word;
}

.usage-rank-item__copy span,
.usage-rank-item__stats span {
  display: block;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
}

.usage-rank-item__stats {
  text-align: right;
}

@media (max-width: 1200px) {
  .usage-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .usage-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 860px) {
  .usage-rank-grid,
  .usage-summary-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .usage-metric-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .usage-section__head,
  .usage-rank-item {
    grid-template-columns: minmax(0, 1fr);
    flex-direction: column;
    align-items: flex-start;
  }

  .usage-rank-item__stats {
    text-align: left;
  }
}
</style>

<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  PulseOutline,
  RefreshOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NProgress,
  NSkeleton,
  NTag,
  NText,
} from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'

interface MoodProfile {
  energy: number
  valence: number
  openness: number
  tension: number
  label: string
  prompt: string
  error?: string
}

interface ScheduleSlot {
  time: string
  activity: string
  mood_hint: string
  location?: string
  description?: string
}

interface DailySchedule {
  date: string
  schedule: {
    theme: string
    day_narrative: string
    slots: ScheduleSlot[]
  } | null
  store_available?: boolean
  error?: string
  time_multiplier?: number
  dream?: {
    running: boolean
    interval_hours: number
  }
}

const loading = ref(true)
const refreshing = ref(false)
const mood = ref<MoodProfile | null>(null)
const schedule = ref<DailySchedule | null>(null)

const energyPercent = computed(() => Math.round((mood.value?.energy ?? 0.5) * 100))
const valencePercent = computed(() => Math.round(((mood.value?.valence ?? 0) + 1) * 50))
const opennessPercent = computed(() => Math.round((mood.value?.openness ?? 0.5) * 100))
const tensionPercent = computed(() => Math.round((mood.value?.tension ?? 0.3) * 100))

onMounted(() => {
  void loadData()
})

async function loadData(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const [moodRes, scheduleRes] = await Promise.allSettled([
      api('/api/admin/mood'),
      api('/api/admin/schedule'),
    ])

    mood.value = moodRes.status === 'fulfilled' ? moodRes.value : { error: '心情引擎不可用' } as MoodProfile
    schedule.value = scheduleRes.status === 'fulfilled' ? scheduleRes.value : { date: '', schedule: null, error: '日程接口不可用' }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}
</script>

<template>
  <AppPage
    title="日程心情"
    eyebrow="Mood Schedule"
    description="汇总当日心情画像、预定日程和运行倍率，帮助判断当前对话节奏与状态来源。"
  >
    <template #action>
      <NButton secondary :loading="refreshing" @click="loadData(true)">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新状态
      </NButton>
    </template>

    <div class="schedule-metric-grid">
      <MetricCard
        title="当前心情"
        :value="mood?.label || '--'"
        :hint="mood?.prompt || '当前没有可展示的心情描述。'"
        :icon="SparklesOutline"
        accent="primary"
      />
      <MetricCard
        title="Energy"
        :value="`${energyPercent}%`"
        hint="表达当前活跃度与行动意愿"
        :icon="PulseOutline"
        accent="warning"
      />
      <MetricCard
        title="Valence"
        :value="`${valencePercent}%`"
        hint="反映情绪偏积极或偏低落的程度"
        :icon="ChatbubbleEllipsesOutline"
        accent="success"
      />
      <MetricCard
        title="Openness"
        :value="`${opennessPercent}%`"
        hint="反映当前对交流和新输入的开放度"
        :icon="TimeOutline"
        accent="info"
      />
    </div>

    <NSkeleton v-if="loading" :repeat="10" text />

    <template v-else>
      <div class="schedule-layout">
        <AppPanelSection
          eyebrow="Today Schedule"
          title="今日日程"
          class="schedule-panel"
        >
          <template #aside>
            <NTag round size="small" :type="schedule?.schedule?.slots?.length ? 'info' : 'default'">
              {{ schedule?.date || '未获取日期' }}
            </NTag>
          </template>

          <div class="schedule-panel__body">
            <template v-if="schedule?.schedule?.slots?.length">
              <AppCard bordered embedded class="schedule-theme-card">
                <div class="schedule-theme-card__head">
                  <div>
                    <strong>{{ schedule.schedule.theme || '今日主题' }}</strong>
                    <p>{{ schedule.schedule.day_narrative || '当前没有附加叙述。' }}</p>
                  </div>
                </div>
              </AppCard>

              <div class="schedule-timeline">
                <div
                  v-for="(slot, index) in schedule.schedule.slots"
                  :key="`${slot.time}-${index}`"
                  class="schedule-timeline__item"
                >
                  <div class="schedule-timeline__marker" />
                  <div class="schedule-timeline__content">
                    <div class="schedule-timeline__head">
                      <strong>{{ slot.time }}</strong>
                      <NTag v-if="slot.location" size="small" round>
                        {{ slot.location }}
                      </NTag>
                    </div>
                    <p class="schedule-timeline__activity">
                      {{ slot.activity }}
                    </p>
                    <p v-if="slot.description" class="schedule-timeline__desc">
                      {{ slot.description }}
                    </p>
                    <p class="schedule-timeline__hint">
                      {{ slot.mood_hint || '当前没有 mood hint。' }}
                    </p>
                  </div>
                </div>
              </div>
            </template>

            <EmptyState
              v-else
              title="今天还没有排定日程"
              :description="schedule?.error || (schedule?.store_available === false ? '当前没有可用的 schedule store。' : '当前日期下没有可展示的日程槽位。')"
              :icon="TimeOutline"
            />
          </div>
        </AppPanelSection>

        <div class="schedule-side">
          <AppPanelSection
            eyebrow="Mood Detail"
            title="心情细项"
            class="schedule-panel"
          >
            <div class="schedule-panel__body">
              <div class="schedule-progress-list">
                <div class="schedule-progress-item">
                  <div class="schedule-progress-item__head">
                    <span>Energy</span>
                    <strong>{{ energyPercent }}%</strong>
                  </div>
                  <NProgress type="line" :percentage="energyPercent" :height="12" color="#c58a2b" />
                </div>

                <div class="schedule-progress-item">
                  <div class="schedule-progress-item__head">
                    <span>Valence</span>
                    <strong>{{ valencePercent }}%</strong>
                  </div>
                  <NProgress type="line" :percentage="valencePercent" :height="12" color="#2e8f6b" />
                </div>

                <div class="schedule-progress-item">
                  <div class="schedule-progress-item__head">
                    <span>Openness</span>
                    <strong>{{ opennessPercent }}%</strong>
                  </div>
                  <NProgress type="line" :percentage="opennessPercent" :height="12" color="#4d7892" />
                </div>

                <div class="schedule-progress-item">
                  <div class="schedule-progress-item__head">
                    <span>Tension</span>
                    <strong>{{ tensionPercent }}%</strong>
                  </div>
                  <NProgress type="line" :percentage="tensionPercent" :height="12" color="#b84c5c" />
                </div>
              </div>

              <AppCard bordered embedded class="schedule-note-card">
                <p class="schedule-note-card__text">
                  {{ mood?.prompt || mood?.error || '当前没有可展示的心情描述。' }}
                </p>
              </AppCard>
            </div>
          </AppPanelSection>

          <AppPanelSection
            eyebrow="Runtime"
            title="运行状态"
            class="schedule-panel"
          >
            <div class="schedule-panel__body">
              <div class="schedule-runtime-grid">
                <div class="schedule-runtime-card">
                  <span>发言倍率</span>
                  <strong>{{ Number(schedule?.time_multiplier ?? 1).toFixed(1) }}x</strong>
                </div>
                <div class="schedule-runtime-card">
                  <span>梦代理</span>
                  <strong>{{ schedule?.dream?.running ? '运行中' : '空闲' }}</strong>
                </div>
                <div class="schedule-runtime-card">
                  <span>梦间隔</span>
                  <strong>{{ schedule?.dream?.interval_hours ?? '--' }}h</strong>
                </div>
                <div class="schedule-runtime-card">
                  <span>存储状态</span>
                  <strong>{{ schedule?.store_available === false ? '不可用' : '可读取' }}</strong>
                </div>
              </div>

              <NText v-if="schedule?.error" depth="3">
                {{ schedule.error }}
              </NText>
            </div>
          </AppPanelSection>
        </div>
      </div>
    </template>
  </AppPage>
</template>

<style scoped>
.schedule-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.schedule-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) 360px;
  gap: 16px;
}

.schedule-side {
  display: grid;
  align-content: start;
  gap: 16px;
}

.schedule-panel__body {
  display: grid;
  align-content: start;
  gap: 16px;
}

.schedule-theme-card,
.schedule-note-card {
  padding: 18px;
  border-radius: 18px;
}

.schedule-theme-card__head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 700;
}

.schedule-theme-card__head p,
.schedule-note-card__text {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.75;
}

.schedule-timeline {
  display: grid;
  gap: 12px;
}

.schedule-timeline__item {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 12px;
}

.schedule-timeline__marker {
  position: relative;
  margin-top: 7px;
  width: 12px;
  height: 12px;
  border: 3px solid rgba(var(--primary-color), 0.22);
  border-radius: 999px;
  background: rgb(var(--primary-color));
}

.schedule-timeline__item:not(:last-child) .schedule-timeline__marker::after {
  position: absolute;
  top: 12px;
  left: 50%;
  width: 1px;
  height: calc(100% + 12px);
  background: var(--om-border);
  transform: translateX(-50%);
  content: '';
}

.schedule-timeline__content {
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 74%, transparent);
}

.schedule-timeline__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.schedule-timeline__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.schedule-timeline__activity {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--om-text-1);
}

.schedule-timeline__desc {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--om-text-2);
}

.schedule-timeline__hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--om-text-3);
}

.schedule-progress-list {
  display: grid;
  gap: 14px;
}

.schedule-progress-item {
  display: grid;
  gap: 8px;
}

.schedule-progress-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.schedule-progress-item__head span {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.schedule-progress-item__head strong {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.schedule-runtime-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.schedule-runtime-card {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.schedule-runtime-card span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.schedule-runtime-card strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.6;
}

@media (max-width: 1180px) {
  .schedule-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .schedule-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .schedule-metric-grid,
  .schedule-runtime-grid {
    grid-template-columns: 1fr;
  }

  .schedule-timeline__head {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

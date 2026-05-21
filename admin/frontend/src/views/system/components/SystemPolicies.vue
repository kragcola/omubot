<script setup lang="ts">
import { computed } from 'vue'

import {
  HardwareChipOutline,
  ShieldCheckmarkOutline,
  SparklesOutline,
} from '@vicons/ionicons5'

import AppCard from '../../../components/common/AppCard.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import type { HumanizerInfo, TalkScheduleInfo, VersionInfo } from '../helpers/types'

interface Props {
  version: VersionInfo | null
  humanizer: HumanizerInfo | null
  talkSchedule: TalkScheduleInfo | null
}

const props = defineProps<Props>()

const router = useRouter()

const versionSummary = computed(() => {
  if (!props.version) return 'unknown'
  return props.version.summary || props.version.version || 'unknown'
})

function goToConfig(task: string) {
  void router.push({ path: '/config', query: { task } })
}
</script>

<template>
  <AppPanelSection
    class="system-panel"
    eyebrow="Policies & Release"
    title="运行策略"
    description="这里只显示当前生效的运行参数；要修改请到「配置」页对应任务，或编辑 config/talk_schedule.json。"
  >
    <template v-if="version?.has_update" #aside>
      <NTag size="small" type="info">
        可更新
      </NTag>
    </template>

    <div class="system-stack">
      <AppCard bordered embedded class="system-stack__item">
        <div class="system-stack__head">
          <div class="system-stack__icon">
            <NIcon :component="HardwareChipOutline" />
          </div>
          <div>
            <h4>版本信息</h4>
            <p>{{ versionSummary }}</p>
          </div>
        </div>
        <div class="system-stack__body">
          <NTag size="small">{{ version?.version || 'unknown' }}</NTag>
          <NTag v-if="version?.has_update && version?.latest_tag" size="small" type="info">
            最新 {{ version.latest_tag }}
          </NTag>
        </div>
        <p class="system-stack__hint">
          版本号来自 git tag；升级需在主机执行 `git pull` 后用「重启 Bot」按钮重启进程。
        </p>
        <a
          v-if="version?.has_update && version?.latest_url"
          class="system-link"
          :href="version.latest_url"
          target="_blank"
          rel="noreferrer"
        >
          查看发布说明
        </a>
      </AppCard>

      <AppCard bordered embedded class="system-stack__item">
        <div class="system-stack__head">
          <div class="system-stack__icon">
            <NIcon :component="ShieldCheckmarkOutline" />
          </div>
          <div>
            <h4>防检测策略</h4>
            <p>{{ humanizer?.enabled ? '已启用' : '已关闭' }}</p>
          </div>
        </div>
        <div class="system-inline-list">
          <span>延迟 {{ humanizer?.enabled ? `${Number(humanizer.min_delay || 0).toFixed(1)}-${Number(humanizer.max_delay || 0).toFixed(1)}s` : '--' }}</span>
          <span>字延迟 {{ humanizer?.enabled ? `${Number(humanizer.char_delay || 0).toFixed(2)}s` : '--' }}</span>
        </div>
        <p class="system-stack__hint">
          模拟人类输入节奏，避免机械感。开启后每条回复会按字数随机加发送延迟，降低被风控识别的概率。
        </p>
        <button
          type="button"
          class="system-link system-link--button"
          @click="goToConfig('rhythm')"
        >
          去配置·拟人延迟
        </button>
      </AppCard>

      <AppCard bordered embedded class="system-stack__item">
        <div class="system-stack__head">
          <div class="system-stack__icon">
            <NIcon :component="SparklesOutline" />
          </div>
          <div>
            <h4>发言倍率</h4>
            <p>当前时间倍率策略</p>
          </div>
        </div>
        <div class="system-inline-list">
          <span>{{ talkSchedule ? `${Number(talkSchedule.time_multiplier).toFixed(1)}x` : '--' }}</span>
          <span>会影响主动发言节奏</span>
        </div>
        <p class="system-stack__hint">
          按一天中不同时段调整 Bot 主动发言频率（半夜降到 0.5x、白天 1.5x 等）。倍率表在 config/talk_schedule.json，热重载生效。
        </p>
      </AppCard>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.system-panel {
  min-height: 100%;
}

.system-stack {
  display: grid;
  gap: 14px;
}

.system-stack__item {
  padding: 16px;
  border-radius: 18px;
}

.system-stack__head {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}

.system-stack__head h4 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.system-stack__head p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.system-stack__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 14px;
  background: rgba(var(--primary-color), 0.12);
  color: rgb(var(--primary-color));
}

.system-stack__body {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.system-inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-stack__hint {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.65;
}

.system-link {
  display: inline-flex;
  margin-top: 14px;
  color: rgb(var(--primary-color));
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}

.system-link--button {
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.system-link:hover {
  text-decoration: underline;
}
</style>

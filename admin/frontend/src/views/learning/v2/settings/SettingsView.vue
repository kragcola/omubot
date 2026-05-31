<script setup lang="ts">
import { inject, ref, watch } from 'vue'
import {
  NSwitch, NInputNumber, NButton, NDrawer, NDrawerContent,
  NTabs, NTabPane, NRadioGroup, NRadioButton, NAlert,
} from 'naive-ui'
import type { useLearningConsole, PipelineSettings } from '../useLearningConsole'

const console = inject<ReturnType<typeof useLearningConsole>>('learningConsole')!

const form = ref<PipelineSettings>({
  autopilot: { enabled: false, aggressiveness: 'standard', concurrency: 20 },
  slang: {},
  style: { extract_enabled: true, extract_interval_minutes: 120 },
  consolidator: { auto_enabled: false, interval_minutes: 360 },
  affection: { scoring_enabled: true },
})

watch(() => console.settings.value, (s) => {
  if (s) form.value = JSON.parse(JSON.stringify(s))
}, { immediate: true })

async function save() {
  await console.saveSettings(form.value)
}
</script>

<template>
  <div v-if="!console.settings.value && console.settingsLoading.value" class="settings-loading">
    加载设置中…
  </div>
  <div v-else class="settings-view">
    <!-- Autopilot Section -->
    <div class="settings-section">
      <div class="settings-section__header">
        <h3 class="settings-section__title">AI 自动托管</h3>
        <p class="settings-section__desc">开启后管线全流程由 AI 自主处理，下方分项设置将被覆盖</p>
      </div>

      <label class="settings-switch">
        <span>
          <strong>启用自动托管</strong>
          <small>自动提取、审核、归档，无需人工干预</small>
        </span>
        <NSwitch v-model:value="form.autopilot.enabled" />
      </label>

      <template v-if="form.autopilot.enabled">
        <div class="settings-aggressiveness">
          <span class="settings-aggressiveness__label">激进程度</span>
          <NRadioGroup v-model:value="form.autopilot.aggressiveness" size="small">
            <NRadioButton value="conservative">保守</NRadioButton>
            <NRadioButton value="standard">标准</NRadioButton>
            <NRadioButton value="aggressive">激进</NRadioButton>
          </NRadioGroup>
        </div>
        <div class="settings-hint-grid">
          <div class="settings-hint-item">
            <strong>保守</strong>
            <small>仅自动提取，审核仍需人工确认</small>
          </div>
          <div class="settings-hint-item">
            <strong>标准</strong>
            <small>自动提取+审核，低置信度暂留待审</small>
          </div>
          <div class="settings-hint-item">
            <strong>激进</strong>
            <small>全自动，置信度 &gt;0.6 即通过</small>
          </div>
        </div>

        <div class="settings-concurrency">
          <span class="settings-concurrency__label">LLM 并发数</span>
          <NInputNumber
            v-model:value="form.autopilot.concurrency"
            :min="1"
            :max="500"
            :step="10"
            size="small"
            class="settings-concurrency__input"
          />
          <small class="settings-concurrency__hint">DeepSeek 支持最高 500 并发</small>
        </div>
      </template>
    </div>

    <!-- Per-noun settings -->
    <template v-if="!form.autopilot.enabled">
      <!-- Style Extraction -->
      <div class="settings-section">
        <div class="settings-section__header">
          <h3 class="settings-section__title">表达提取</h3>
          <p class="settings-section__desc">从群聊中自动提取表达风格候选</p>
        </div>

        <label class="settings-switch">
          <span>
            <strong>启用自动提取</strong>
            <small>后台定时从群聊中提取表达风格候选词条</small>
          </span>
          <NSwitch v-model:value="form.style.extract_enabled" />
        </label>

        <div class="settings-grid">
          <label>
            <span>提取间隔（分钟）</span>
            <NInputNumber
              v-model:value="form.style.extract_interval_minutes"
              :min="30"
              :max="1440"
              size="small"
            />
          </label>
        </div>
      </div>

      <!-- Consolidator -->
      <div class="settings-section">
        <div class="settings-section__header">
          <h3 class="settings-section__title">记忆整合</h3>
          <p class="settings-section__desc">定期整合短期记忆为长期记忆卡片</p>
        </div>

        <label class="settings-switch">
          <span>
            <strong>启用自动整合</strong>
            <small>后台定时将短期记忆整合为长期记忆卡片</small>
          </span>
          <NSwitch v-model:value="form.consolidator.auto_enabled" />
        </label>

        <div class="settings-grid">
          <label>
            <span>整合间隔（分钟）</span>
            <NInputNumber
              v-model:value="form.consolidator.interval_minutes"
              :min="60"
              :max="1440"
              size="small"
            />
          </label>
        </div>
      </div>

      <!-- Affection -->
      <div class="settings-section">
        <div class="settings-section__header">
          <h3 class="settings-section__title">亲密度</h3>
          <p class="settings-section__desc">基于互动频率和质量计算用户亲密度分数</p>
        </div>

        <label class="settings-switch">
          <span>
            <strong>启用自动计分</strong>
            <small>每次回复后自动记录互动并更新亲密度分数</small>
          </span>
          <NSwitch v-model:value="form.affection.scoring_enabled" />
        </label>
      </div>
    </template>

    <!-- Autopilot override notice -->
    <NAlert v-if="form.autopilot.enabled" type="info" :bordered="false" class="settings-override-notice">
      自动托管已开启，以上分项设置由 AI 自动控制。如需手动调整，请先关闭自动托管。
    </NAlert>

    <!-- Save -->
    <div class="settings-actions">
      <NButton type="primary" :loading="console.settingsSaving.value" @click="save">
        保存设置
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 640px;
}

.settings-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.settings-section__header {
  margin-bottom: 4px;
}

.settings-section__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 600;
}

.settings-section__desc {
  margin: 4px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.5;
}

.settings-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.settings-switch:hover {
  border-color: var(--om-border-strong);
}

.settings-switch span {
  display: grid;
  gap: 3px;
}

.settings-switch strong {
  color: var(--om-text-1);
  font-size: 13px;
}

.settings-switch small {
  color: var(--om-text-3);
  font-size: 12px;
}

.settings-aggressiveness {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
}

.settings-aggressiveness__label {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 500;
}

.settings-hint-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.settings-hint-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-fill);
}

.settings-concurrency {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
}

.settings-concurrency__label {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 500;
}

.settings-concurrency__input {
  width: 120px;
}

.settings-concurrency__hint {
  color: var(--om-text-3);
  font-size: 12px;
}

.settings-hint-item strong {
  color: var(--om-text-1);
  font-size: 12px;
}

.settings-hint-item small {
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
}

.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-grid label {
  display: grid;
  gap: 6px;
}

.settings-grid span {
  color: var(--om-text-2);
  font-size: 13px;
}

.settings-override-notice {
  font-size: 13px;
}

.settings-actions {
  padding-top: 4px;
}

@media (max-width: 640px) {
  .settings-hint-grid {
    grid-template-columns: 1fr;
  }

  .settings-grid {
    grid-template-columns: 1fr;
  }
}

.settings-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: var(--om-text-3);
  font-size: 13px;
}
</style>

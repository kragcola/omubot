<script setup lang="ts">
import { computed } from 'vue'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import {
  providerCacheHitLabel,
  providerModeLabel,
  providerRateLimitLabel,
  providerRateLimitType,
  providerResultLabel,
  providerResultType,
} from '../helpers/badges'
import { formatTokenCount } from '../helpers/formatters'
import type {
  ProviderTaskKey,
  ProviderTestResult,
  ProvidersInfo,
} from '../helpers/types'

interface Props {
  providers: ProvidersInfo | null
  defaultDraft: string
  taskDraft: Record<string, string>
  testing: Record<string, boolean>
  testResults: Record<string, ProviderTestResult>
  selectionSaving: boolean
  selectionDirty: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update-default-draft', value: string): void
  (e: 'update-task-draft', task: ProviderTaskKey, value: string): void
  (e: 'save-selection'): void
  (e: 'test-profile', name: string): void
  (e: 'open-editor'): void
}>()

const providerTaskOrder: ProviderTaskKey[] = [
  'main',
  'thinker',
  'compact',
  'reply_gate',
  'vision',
  'slang',
  'slang_review',
  'slang_drift',
  'slang_semantic',
  'style',
  'style_review',
  'memo',
  'persona_import',
  'chat_private',
  'bilibili_intent',
  'element_detect',
  'graph_review',
  'graph_edge_classifier',
  'reflection_consolidator',
  'episode_summarizer',
  'episode_review',
  'fact_review',
  'scheduler_eot',
  'scheduler_replay_judge',
  'birthday_wish',
]

const providerTaskLabels: Record<ProviderTaskKey, string> = {
  main: '主聊天',
  thinker: '思考',
  compact: '压缩',
  reply_gate: '回复闸门',
  vision: '视觉',
  slang: '黑话提取',
  slang_review: '黑话审核',
  slang_drift: '黑话漂移',
  slang_semantic: '黑话语义',
  style: '风格学习',
  style_review: '风格审核',
  memo: '记忆卡片',
  persona_import: '人设导入',
  chat_private: '私聊',
  bilibili_intent: 'B 站意图',
  element_detect: '元素识别',
  graph_review: '图谱审核',
  graph_edge_classifier: '图谱边分类',
  reflection_consolidator: '反思聚合',
  episode_summarizer: '剧集摘要',
  episode_review: '剧集审核',
  fact_review: '事实审核',
  scheduler_eot: '调度 EOT',
  scheduler_replay_judge: '调度重放评审',
  birthday_wish: '生日祝福',
}

const activeProvider = computed(() =>
  props.providers?.profiles.find(profile => profile.active)
  || props.providers?.profiles.find(profile => profile.name === props.providers?.default_profile)
  || props.providers?.profiles[0]
  || null,
)

const activeProviderUsageSummary = computed(() => activeProvider.value?.last_usage || {})

const providerProfileOptions = computed(() =>
  (props.providers?.profiles || []).map(profile => ({
    label: `${profile.name}${profile.model ? ` · ${profile.model}` : ''}`,
    value: profile.name,
  })),
)

function providerTaskModel(task: ProviderTaskKey) {
  const profileName = props.taskDraft[task]
  return props.providers?.profiles.find(profile => profile.name === profileName)?.model || '--'
}

function providerTaskCacheHit(task: ProviderTaskKey): string {
  const profileName = props.taskDraft[task]
  const profile = props.providers?.profiles.find(p => p.name === profileName)
  const byTask = profile?.last_cache_hit_pct_by_task || {}
  const pct = byTask[task]
  if (pct == null) {
    return '--'
  }
  return `${pct.toFixed(1)}%`
}

function providerTaskCapabilityWarning(task: ProviderTaskKey): string {
  // Warn when a task with non-trivial capability requirements lands on a profile
  // that does not declare them. Mirrors LLMRequest.requires_capabilities defaults
  // used by the spine in services/llm/llm_request.py.
  const profileName = props.taskDraft[task]
  const profile = props.providers?.profiles.find(p => p.name === profileName)
  if (!profile) return ''
  const caps = new Set((profile.capabilities || []).map(String))
  const required: Record<string, string[]> = {
    chat_private: ['chat', 'tools'],
    main: ['chat', 'tools'],
    compact: ['chat', 'tools'],
    vision: ['chat', 'vision'],
    persona_import: ['json'],
    scheduler_eot: ['chat', 'json'],
    scheduler_replay_judge: ['chat', 'json'],
  }
  const wants = required[task] || ['chat']
  const missing = wants.filter(c => !caps.has(c))
  return missing.length ? `缺 ${missing.join('/')}` : ''
}
</script>

<template>
  <AppPanelSection
    class="system-panel"
    eyebrow="Provider Profiles"
    title="LLM Provider"
  >
    <template #aside>
      <NButton size="small" secondary @click="emit('open-editor')">
        定义管理
      </NButton>
      <NTag size="small" round type="info">
        {{ providers?.profiles.length || 0 }} 个 profile
      </NTag>
    </template>

    <div class="system-provider-card">
      <div class="system-provider-card__head">
        <div>
          <span class="system-provider-card__label">当前默认</span>
          <strong>{{ activeProvider?.name || '--' }}</strong>
          <p>{{ activeProvider?.model || '未配置模型' }}</p>
        </div>
        <NTag size="small" round :type="selectionDirty ? 'warning' : 'success'">
          {{ selectionDirty ? '待应用' : '运行中' }}
        </NTag>
      </div>
      <div class="system-inline-list">
        <span>{{ activeProvider?.api_format || '--' }}</span>
        <span>{{ activeProvider?.base_url || '--' }}</span>
        <span>max {{ activeProvider?.max_tokens ?? '--' }}</span>
      </div>
      <div class="system-provider-runtime">
        <div class="system-provider-runtime__item">
          <span>运行模式</span>
          <strong>{{ providerModeLabel(activeProvider?.provider_mode) }}</strong>
        </div>
        <div class="system-provider-runtime__item">
          <span>最近命中率</span>
          <strong>{{ providerCacheHitLabel(activeProvider?.last_cache_hit_pct) }}</strong>
        </div>
        <div class="system-provider-runtime__item">
          <span>Replay Tokens</span>
          <strong>{{ formatTokenCount(activeProvider?.last_reasoning_replay_tokens) }}</strong>
        </div>
        <div class="system-provider-runtime__item">
          <span>Payload Sanitizer</span>
          <strong>{{ activeProvider?.last_payload_sanitized ? '介入过' : '未介入' }}</strong>
        </div>
      </div>
      <div class="system-inline-list">
        <span>hit {{ formatTokenCount(activeProvider?.last_prompt_cache_hit_tokens) }}</span>
        <span>miss {{ formatTokenCount(activeProvider?.last_prompt_cache_miss_tokens) }}</span>
        <span>provider {{ activeProvider?.provider_kind || '--' }}</span>
        <span v-if="activeProviderUsageSummary?.completion_tokens_details?.reasoning_tokens != null">
          reasoning {{ formatTokenCount(activeProviderUsageSummary.completion_tokens_details.reasoning_tokens) }}
        </span>
      </div>
      <div class="system-provider-switcher">
        <span>默认 profile</span>
        <NSelect
          size="small"
          :value="defaultDraft"
          :options="providerProfileOptions"
          :disabled="!providerProfileOptions.length"
          @update:value="value => emit('update-default-draft', String(value))"
        />
        <NButton
          size="small"
          type="primary"
          secondary
          :disabled="!selectionDirty"
          :loading="selectionSaving"
          @click="emit('save-selection')"
        >
          应用热切换
        </NButton>
      </div>
    </div>

    <div class="system-provider-list">
      <div
        v-for="profile in providers?.profiles || []"
        :key="profile.name"
        class="system-provider-row"
      >
        <div class="system-provider-row__main">
          <strong>{{ profile.name }}</strong>
          <span>{{ profile.model || '--' }}</span>
          <small>
            {{ providerModeLabel(profile.provider_mode) }} · hit {{ providerCacheHitLabel(profile.last_cache_hit_pct) }} · replay {{ formatTokenCount(profile.last_reasoning_replay_tokens) }}
          </small>
          <small v-if="testResults[profile.name]">
            {{ testResults[profile.name].ok ? testResults[profile.name].text_preview || '连通性正常' : testResults[profile.name].error }}
          </small>
        </div>
        <div class="system-provider-row__actions">
          <NTag size="small" round :type="profile.active ? 'success' : 'default'">
            {{ profile.active ? '默认' : profile.api_format }}
          </NTag>
          <NTag
            v-if="testResults[profile.name]"
            size="small"
            round
            :type="providerResultType(testResults[profile.name])"
          >
            {{ providerResultLabel(testResults[profile.name]) }}
          </NTag>
          <NTag
            size="small"
            round
            :type="providerRateLimitType(profile.rate_limit)"
          >
            {{ providerRateLimitLabel(profile.rate_limit) }}
          </NTag>
          <NButton
            size="tiny"
            secondary
            :loading="testing[profile.name]"
            @click="emit('test-profile', profile.name)"
          >
            测试
          </NButton>
        </div>
      </div>
    </div>

    <div v-if="providers?.task_profiles?.length" class="system-task-profile-list system-task-profile-list--editable">
      <div
        v-for="task in providerTaskOrder"
        :key="task"
        class="system-task-profile-row"
      >
        <span>{{ providerTaskLabels[task] }}</span>
        <NSelect
          size="tiny"
          :value="taskDraft[task]"
          :options="providerProfileOptions"
          :disabled="!providerProfileOptions.length || task === 'main'"
          @update:value="value => emit('update-task-draft', task, String(value))"
        />
        <em>{{ task === 'main' ? '跟随默认 profile' : providerTaskModel(task) }}</em>
        <div class="system-task-profile-row__metrics">
          <span class="system-task-profile-row__hit">命中 {{ providerTaskCacheHit(task) }}</span>
          <NTag
            v-if="providerTaskCapabilityWarning(task)"
            size="tiny"
            round
            type="warning"
          >
            {{ providerTaskCapabilityWarning(task) }}
          </NTag>
        </div>
      </div>
    </div>
    <p class="system-provider-note">
      热切换只改变任务映射；“定义管理”会修改 profile 本体并同步写回 `config/config.json`。两者都会立即刷新运行中的 LLMClient，不会清空现有会话。
    </p>
  </AppPanelSection>
</template>

<style scoped>
.system-panel {
  min-height: 100%;
}

.system-inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-provider-card {
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
}

.system-provider-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-card__label {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.system-provider-card strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 20px;
}

.system-provider-card p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-provider-switcher {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
  padding: 10px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 68%, transparent);
}

.system-provider-switcher > span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 700;
}

.system-provider-runtime {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.system-provider-runtime__item {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 58%, transparent);
}

.system-provider-runtime__item span {
  display: block;
  color: var(--om-text-3);
  font-size: 11px;
}

.system-provider-runtime__item strong {
  display: block;
  margin-top: 6px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-provider-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.system-task-profile-list {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.system-task-profile-row {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 34%, transparent);
}

.system-task-profile-list--editable {
  grid-template-columns: repeat(5, minmax(118px, 1fr));
}

.system-task-profile-row span,
.system-task-profile-row em {
  display: block;
  overflow: hidden;
  color: var(--om-text-3);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-task-profile-row strong {
  display: block;
  overflow: hidden;
  margin: 5px 0 4px;
  color: var(--om-text-1);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-task-profile-row :deep(.n-select) {
  margin: 6px 0;
}

.system-task-profile-row__metrics {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.system-task-profile-row__hit {
  color: var(--om-text-2);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.system-provider-note {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-provider-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 32%, transparent);
}

.system-provider-row__main {
  min-width: 0;
}

.system-provider-row strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-provider-row span {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-provider-row__main small {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-text-3);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-provider-row__actions {
  display: inline-flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 1100px) {
  .system-task-profile-list,
  .system-provider-runtime {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .system-provider-runtime,
  .system-task-profile-list {
    grid-template-columns: 1fr;
  }

  .system-provider-row,
  .system-provider-switcher {
    flex-direction: column;
    align-items: stretch;
  }

  .system-provider-switcher {
    display: flex;
  }

  .system-provider-row__actions {
    justify-content: flex-start;
  }
}
</style>

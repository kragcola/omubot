<script setup lang="ts">
import { REPEAT_POLICY_OPTIONS } from '../helpers/badges'
import type { SlangSettings } from '../helpers/types'

defineProps<{
  savingSettings: boolean
}>()

const settings = defineModel<SlangSettings>('settings', { required: true })
const allowlistText = defineModel<string>('allowlistText', { required: true })
const stoplistText = defineModel<string>('stoplistText', { required: true })

const emit = defineEmits<{
  (e: 'save'): void
}>()
</script>

<template>
  <div class="slang-settings-form">
    <label class="slang-switch-row">
      <span>
        <strong>启用学习</strong>
        <small>后台从群聊中抽取候选。</small>
      </span>
      <NSwitch v-model:value="settings.learning_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>启用注入</strong>
        <small>已批准黑话进入动态 Prompt。</small>
      </span>
      <NSwitch v-model:value="settings.injection_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>审核优先</strong>
        <small>候选不自动批准，保持安全兜底。</small>
      </span>
      <NSwitch v-model:value="settings.review_required" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>自动跨群提升</strong>
        <small>默认建议关闭；开启后后台抽取会生成 global 候选，仍需人工批准。</small>
      </span>
      <NSwitch v-model:value="settings.auto_promote_global_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>清池启用搜索</strong>
        <small>复用 web_search 验证候选词；搜索失败时保守处理。</small>
      </span>
      <NSwitch v-model:value="settings.backlog_review_search_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>清池自动通过</strong>
        <small>高置信且有搜索证据时直接 approved，无需人工确认。</small>
      </span>
      <NSwitch v-model:value="settings.backlog_auto_approve_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>语义漂移检测</strong>
        <small>已批准词条遇到冲突新释义时进入治理队列，不直接覆盖。</small>
      </span>
      <NSwitch v-model:value="settings.drift_detection_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>漂移 LLM 语义闸</strong>
        <small>新漂移先经 LLM 判定：同义直接放过，别名变体自动并入 alias，仅 real_drift 才进人工队列。</small>
      </span>
      <NSwitch v-model:value="settings.drift_semantic_gate_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>AI 清池（存量复核）</strong>
        <small>自动逐批复核候选池里的旧词条，通过或否决。</small>
      </span>
      <NSwitch v-model:value="settings.backlog_review_enabled" />
    </label>
    <label class="slang-switch-row">
      <span>
        <strong>启用黑话查询工具</strong>
        <small>允许 LLM 按需查询更多已批准黑话，减少 Prompt 常驻长度。</small>
      </span>
      <NSwitch v-model:value="settings.lookup_tool_enabled" />
    </label>

    <div class="slang-settings-grid">
      <label>
        <span>AI 清池时段</span>
        <NDynamicTags v-model:value="settings.daily_ai_review_times" :max="12" />
        <span class="slang-settings__hint">格式 HH:MM，最多 12 个时段</span>
      </label>
      <label>
        <span>自动通过最低置信度</span>
        <NInputNumber v-model:value="settings.backlog_auto_approve_min_confidence" :min="0" :max="1" :step="0.01" />
      </label>
      <label>
        <span>清池每批数量</span>
        <NInputNumber v-model:value="settings.backlog_review_batch_size" :min="10" :max="200" />
      </label>
      <label>
        <span>清池最低置信度</span>
        <NInputNumber v-model:value="settings.backlog_review_min_confidence" :min="0" :max="1" :step="0.05" />
      </label>
      <label>
        <span>清池最低使用次数</span>
        <NInputNumber v-model:value="settings.backlog_review_min_usage_count" :min="1" :max="20" />
      </label>
      <label>
        <span>清池启用搜索</span>
        <NSwitch v-model:value="settings.backlog_review_search_enabled" />
      </label>
      <label>
        <span>kept 连续否决阈值</span>
        <NInputNumber v-model:value="settings.backlog_kept_streak_limit" :min="1" :max="10" />
      </label>
      <label>
        <span>群内取证条数</span>
        <NInputNumber v-model:value="settings.backlog_local_evidence_count" :min="0" :max="20" />
      </label>
      <label>
        <span>跨档触发重判</span>
        <NSwitch v-model:value="settings.backlog_threshold_gating_enabled" />
      </label>
      <label>
        <span>最大注入条数</span>
        <NInputNumber v-model:value="settings.max_injected_terms" :min="1" :max="30" />
      </label>
      <label>
        <span title="未在本轮对话直接命中的高分 approved 黑话，最多带几条作为群背景。0 表示只注入直接命中。">非直接命中上限</span>
        <NInputNumber v-model:value="settings.max_indirect_inject_terms" :min="0" :max="30" />
      </label>
      <label>
        <span>抽取间隔（分钟）</span>
        <NInputNumber v-model:value="settings.extract_interval_minutes" :min="1" :max="1440" />
      </label>
      <label>
        <span>候选最小出现次数</span>
        <NInputNumber v-model:value="settings.candidate_min_count" :min="1" :max="50" />
      </label>
      <label>
        <span>单批扫描消息数</span>
        <NInputNumber v-model:value="settings.extraction_batch_limit" :min="10" :max="500" />
      </label>
      <label>
        <span>跨群提升最小群数</span>
        <NInputNumber v-model:value="settings.global_promote_min_groups" :min="2" :max="20" />
      </label>
      <label>
        <span>批量页大小</span>
        <NInputNumber v-model:value="settings.bulk_page_size" :min="10" :max="200" />
      </label>
      <label>
        <span>统计窗口（天）</span>
        <NInputNumber v-model:value="settings.stats_days" :min="1" :max="120" />
      </label>
      <label>
        <span>Prompt 最大字符</span>
        <NInputNumber v-model:value="settings.max_prompt_chars" :min="300" :max="6000" />
      </label>
      <label>
        <span>漂移最低置信度</span>
        <NInputNumber v-model:value="settings.drift_min_confidence" :min="0" :max="1" :step="0.01" />
      </label>
      <label>
        <span>漂移自动归档天数</span>
        <NInputNumber v-model:value="settings.drift_age_out_days" :min="0" :max="180" />
        <span class="slang-settings__hint">open 状态超过该天数无新证据将自动归档；0 表示关闭</span>
      </label>
      <label>
        <span>注入最低置信度</span>
        <NInputNumber v-model:value="settings.min_inject_confidence" :min="0" :max="1" :step="0.01" />
      </label>
    </div>

    <label class="slang-settings-field">
      <span>默认复述策略</span>
      <NSelect v-model:value="settings.repeat_policy" :options="REPEAT_POLICY_OPTIONS" />
    </label>

    <label class="slang-settings-field">
      <span>语义后端</span>
      <NSelect
        v-model:value="settings.semantic_backend"
        :options="[{ label: '轻量 ngram（默认）', value: 'ngram' }, { label: 'Embedding（v3.5 预留，未安装时降级）', value: 'embedding', disabled: true }]"
      />
    </label>

    <label class="slang-settings-field">
      <span>群白名单</span>
      <NInput
        v-model:value="allowlistText"
        type="textarea"
        :autosize="{ minRows: 3, maxRows: 6 }"
        placeholder="每行一个群号；留空表示所有群可学习"
      />
    </label>

    <label class="slang-settings-field">
      <span>停用词 / 永不学习</span>
      <NInput
        v-model:value="stoplistText"
        type="textarea"
        :autosize="{ minRows: 3, maxRows: 6 }"
        placeholder="每行一个普通词、人名或作品名；命中后不会进入候选"
      />
    </label>

    <NButton type="primary" :loading="savingSettings" @click="emit('save')">
      保存设置
    </NButton>
  </div>
</template>

<style scoped>
.slang-settings-form {
  display: grid;
  gap: 16px;
}

.slang-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-switch-row span {
  display: grid;
  gap: 4px;
}

.slang-switch-row strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.slang-switch-row small {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-settings-grid label,
.slang-settings-field {
  display: grid;
  gap: 8px;
}

.slang-settings-grid span,
.slang-settings-field span {
  color: var(--om-text-2);
  font-size: 13px;
}

@media (max-width: 640px) {
  .slang-settings-grid {
    grid-template-columns: 1fr;
  }
}
</style>

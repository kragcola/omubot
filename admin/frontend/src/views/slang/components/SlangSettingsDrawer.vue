<script setup lang="ts">
import { REPEAT_POLICY_OPTIONS } from '../helpers/badges'
import type { SlangSettings } from '../helpers/types'

defineProps<{
  savingSettings: boolean
}>()

const visible = defineModel<boolean>('visible', { required: true })
const settings = defineModel<SlangSettings>('settings', { required: true })
const allowlistText = defineModel<string>('allowlistText', { required: true })
const stoplistText = defineModel<string>('stoplistText', { required: true })

const emit = defineEmits<{
  (e: 'save'): void
}>()
</script>

<template>
  <NDrawer v-model:show="visible" :width="480" placement="right">
    <NDrawerContent title="黑话系统设置" closable>
      <NTabs type="line" animated>
        <NTabPane name="extraction" tab="抽取">
          <div class="slang-drawer-section">
            <label class="slang-drawer-switch">
              <span>
                <strong>启用学习</strong>
                <small>后台从群聊中抽取候选。</small>
              </span>
              <NSwitch v-model:value="settings.learning_enabled" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>审核优先</strong>
                <small>候选不自动批准，保持安全兜底。</small>
              </span>
              <NSwitch v-model:value="settings.review_required" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>自动跨群提升</strong>
                <small>开启后抽取会生成 global 候选，仍需人工批准。</small>
              </span>
              <NSwitch v-model:value="settings.auto_promote_global_enabled" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>语义漂移检测</strong>
                <small>已批准词条遇到冲突新释义时进入治理队列。</small>
              </span>
              <NSwitch v-model:value="settings.drift_detection_enabled" />
            </label>

            <div class="slang-drawer-grid">
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
                <span>漂移最低置信度</span>
                <NInputNumber v-model:value="settings.drift_min_confidence" :min="0" :max="1" :step="0.01" />
              </label>
            </div>

            <label class="slang-drawer-field">
              <span>群白名单</span>
              <NInput
                v-model:value="allowlistText"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 5 }"
                placeholder="每行一个群号；留空表示所有群可学习"
              />
            </label>

            <label class="slang-drawer-field">
              <span>停用词 / 永不学习</span>
              <NInput
                v-model:value="stoplistText"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 5 }"
                placeholder="每行一个普通词、人名或作品名；命中后不会进入候选"
              />
            </label>
          </div>
        </NTabPane>

        <NTabPane name="review" tab="清池">
          <div class="slang-drawer-section">
            <label class="slang-drawer-switch">
              <span>
                <strong>AI 清池</strong>
                <small>自动逐批复核候选池里的旧词条，通过或否决。</small>
              </span>
              <NSwitch v-model:value="settings.backlog_review_enabled" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>启用搜索验证</strong>
                <small>复用 web_search 验证候选词；搜索失败时保守处理。</small>
              </span>
              <NSwitch v-model:value="settings.backlog_review_search_enabled" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>自动通过</strong>
                <small>高置信且有搜索证据时直接 approved，无需人工确认。</small>
              </span>
              <NSwitch v-model:value="settings.backlog_auto_approve_enabled" />
            </label>

            <div class="slang-drawer-grid">
              <label>
                <span>AI 清池时段</span>
                <NDynamicTags v-model:value="settings.daily_ai_review_times" :max="12" />
                <small>格式 HH:MM，最多 12 个时段</small>
              </label>
              <label>
                <span>自动通过最低置信度</span>
                <NInputNumber v-model:value="settings.backlog_auto_approve_min_confidence" :min="0" :max="1" :step="0.01" />
              </label>
              <label>
                <span>每批数量</span>
                <NInputNumber v-model:value="settings.backlog_review_batch_size" :min="10" :max="200" />
              </label>
              <label>
                <span>最低置信度</span>
                <NInputNumber v-model:value="settings.backlog_review_min_confidence" :min="0" :max="1" :step="0.05" />
              </label>
              <label>
                <span>最低使用次数</span>
                <NInputNumber v-model:value="settings.backlog_review_min_usage_count" :min="1" :max="20" />
              </label>
              <label>
                <span>连续否决阈值</span>
                <NInputNumber v-model:value="settings.backlog_kept_streak_limit" :min="1" :max="10" />
              </label>
            </div>
          </div>
        </NTabPane>

        <NTabPane name="injection" tab="注入">
          <div class="slang-drawer-section">
            <label class="slang-drawer-switch">
              <span>
                <strong>启用注入</strong>
                <small>已批准黑话进入动态 Prompt。</small>
              </span>
              <NSwitch v-model:value="settings.injection_enabled" />
            </label>
            <label class="slang-drawer-switch">
              <span>
                <strong>黑话查询工具</strong>
                <small>允许 LLM 按需查询更多已批准黑话，减少 Prompt 常驻长度。</small>
              </span>
              <NSwitch v-model:value="settings.lookup_tool_enabled" />
            </label>

            <div class="slang-drawer-grid">
              <label>
                <span>最大注入条数</span>
                <NInputNumber v-model:value="settings.max_injected_terms" :min="1" :max="30" />
              </label>
              <label>
                <span>注入最低置信度</span>
                <NInputNumber v-model:value="settings.min_inject_confidence" :min="0" :max="1" :step="0.01" />
              </label>
              <label>
                <span>Prompt 最大字符</span>
                <NInputNumber v-model:value="settings.max_prompt_chars" :min="300" :max="6000" />
              </label>
              <label>
                <span>批量页大小</span>
                <NInputNumber v-model:value="settings.bulk_page_size" :min="10" :max="200" />
              </label>
              <label>
                <span>统计窗口（天）</span>
                <NInputNumber v-model:value="settings.stats_days" :min="1" :max="120" />
              </label>
            </div>

            <label class="slang-drawer-field">
              <span>默认复述策略</span>
              <NSelect v-model:value="settings.repeat_policy" :options="REPEAT_POLICY_OPTIONS" />
            </label>

            <label class="slang-drawer-field">
              <span>语义后端</span>
              <NSelect
                v-model:value="settings.semantic_backend"
                :options="[
                  { label: '轻量 ngram（默认）', value: 'ngram' },
                  { label: 'Embedding（预留，未安装时降级）', value: 'embedding', disabled: true },
                ]"
              />
            </label>
          </div>
        </NTabPane>
      </NTabs>

      <template #footer>
        <NButton type="primary" :loading="savingSettings" block @click="emit('save')">
          保存设置
        </NButton>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.slang-drawer-section {
  display: grid;
  gap: 14px;
  padding: 4px 0;
}

.slang-drawer-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.slang-drawer-switch span {
  display: grid;
  gap: 3px;
}

.slang-drawer-switch strong {
  color: var(--om-text-1);
  font-size: 13px;
}

.slang-drawer-switch small {
  color: var(--om-text-3);
  font-size: 12px;
}

.slang-drawer-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-drawer-grid label,
.slang-drawer-field {
  display: grid;
  gap: 6px;
}

.slang-drawer-grid span,
.slang-drawer-field span {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-drawer-grid small {
  color: var(--om-text-3);
  font-size: 11px;
}
</style>

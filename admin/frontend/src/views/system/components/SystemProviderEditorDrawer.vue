<script setup lang="ts">
import { computed } from 'vue'

import AppCard from '../../../components/common/AppCard.vue'
import type {
  ProviderApiKeyMode,
  ProviderOption,
  ProviderProfileDraft,
} from '../helpers/types'

interface Props {
  show: boolean
  drafts: ProviderProfileDraft[]
  capabilityOptions: ProviderOption[]
  apiFormatOptions: ProviderOption[]
  dirty: boolean
  saving: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'add'): void
  (e: 'remove', name: string): void
  (e: 'patch', index: number, patch: Partial<ProviderProfileDraft>): void
  (e: 'set-key-mode', index: number, value: string): void
  (e: 'capabilities-change', index: number, value: Array<string | number> | null): void
  (e: 'reset'): void
  (e: 'save'): void
}>()

const apiKeyModeOptions = [
  { label: '保留当前', value: 'keep' },
  { label: '替换密钥', value: 'replace' },
  { label: '清空密钥', value: 'clear' },
]

const visible = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value),
})
</script>

<template>
  <NDrawer v-model:show="visible" :width="760">
    <NDrawerContent closable>
      <template #header>
        <div class="system-provider-editor__header">
          <div>
            <p class="system-panel__eyebrow">
              Provider Definition Editor
            </p>
            <h3 class="system-panel__title">
              管理 LLM Profile 定义
            </h3>
          </div>
          <NTag size="small" round :type="dirty ? 'warning' : 'success'">
            {{ dirty ? '有未保存修改' : '已同步' }}
          </NTag>
        </div>
      </template>

      <div class="system-provider-editor">
        <NAlert type="info" :show-icon="false" class="system-provider-editor__tip">
          `main` profile 会同步 legacy `llm.base_url / api_key / model / max_tokens`，方便旧配置与新 profile 体系兼容；删除其他 profile 后，引用它的任务映射会自动回退到当前默认 profile。
        </NAlert>

        <div class="system-provider-editor__toolbar">
          <div class="system-inline-list">
            <span>{{ drafts.length }} 个定义</span>
            <span>密钥默认只显示遮罩值</span>
          </div>
          <NButton size="small" secondary @click="emit('add')">
            新增 profile
          </NButton>
        </div>

        <div class="system-provider-editor__list">
          <AppCard
            v-for="(profile, index) in drafts"
            :key="`${profile.name || 'profile'}-${index}`"
            bordered
            embedded
            class="system-provider-editor__card"
          >
            <div class="system-provider-editor__card-head">
              <div>
                <strong>{{ profile.name === 'main' ? 'main · 兼容基线' : profile.name || `profile ${index + 1}` }}</strong>
                <p v-if="profile.name === 'main'">
                  会同步 legacy `llm.*` 根配置，并作为其它 profile 的回退基线。
                </p>
                <p v-else>
                  保存后会写回 `llm.profiles.{{ profile.name || `profile_${index + 1}` }}`。
                </p>
              </div>
              <NButton
                size="small"
                quaternary
                type="error"
                :disabled="profile.name === 'main'"
                @click="emit('remove', profile.name)"
              >
                删除
              </NButton>
            </div>

            <NForm label-placement="top" class="system-provider-editor__form">
              <NGrid :cols="24" :x-gap="14" :y-gap="6" responsive="screen">
                <NFormItemGi :span="8" label="Profile 名称">
                  <NInput
                    :value="profile.name"
                    placeholder="例如 slang / vision"
                    @update:value="value => emit('patch', index, { name: String(value || '') })"
                  />
                </NFormItemGi>

                <NFormItemGi :span="8" label="API 格式">
                  <NSelect
                    :value="profile.api_format"
                    :options="apiFormatOptions as any"
                    @update:value="value => emit('patch', index, { api_format: String(value || 'anthropic') })"
                  />
                </NFormItemGi>

                <NFormItemGi :span="8" label="Max Tokens">
                  <NInputNumber
                    :value="profile.max_tokens"
                    :min="1"
                    :step="128"
                    clearable
                    style="width: 100%"
                    @update:value="value => emit('patch', index, { max_tokens: typeof value === 'number' ? value : null })"
                  />
                </NFormItemGi>

                <NFormItemGi :span="12" label="Base URL">
                  <NInput
                    :value="profile.base_url"
                    placeholder="https://api.example.com/v1"
                    @update:value="value => emit('patch', index, { base_url: String(value || '') })"
                  />
                </NFormItemGi>

                <NFormItemGi :span="12" label="Model">
                  <NInput
                    :value="profile.model"
                    placeholder="claude-sonnet / gpt-4o-mini"
                    @update:value="value => emit('patch', index, { model: String(value || '') })"
                  />
                </NFormItemGi>

                <NFormItemGi :span="24" label="能力声明">
                  <NCheckboxGroup
                    :value="profile.capabilities"
                    @update:value="emit('capabilities-change', index, $event)"
                  >
                    <NSpace wrap :size="[10, 10]">
                      <NCheckbox
                        v-for="item in capabilityOptions"
                        :key="item.value"
                        :value="item.value"
                      >
                        {{ item.label }}
                      </NCheckbox>
                    </NSpace>
                  </NCheckboxGroup>
                </NFormItemGi>

                <NFormItemGi :span="10" label="密钥处理">
                  <NSelect
                    :value="profile.api_key_mode"
                    :options="apiKeyModeOptions"
                    @update:value="value => emit('set-key-mode', index, String(value || 'keep'))"
                  />
                </NFormItemGi>

                <NFormItemGi :span="14" label="API Key">
                  <NInput
                    :value="profile.api_key_input"
                    type="password"
                    show-password-on="click"
                    :placeholder="profile.api_key_mode === 'replace'
                      ? (profile.api_key_present ? `当前：${profile.api_key_mask || '已保存密钥'}` : '输入新的 api_key')
                      : (profile.api_key_present ? `当前：${profile.api_key_mask || '已保存密钥'}` : '当前未设置密钥')"
                    :disabled="profile.api_key_mode !== 'replace'"
                    @update:value="value => emit('patch', index, { api_key_input: String(value || '') })"
                  />
                </NFormItemGi>
              </NGrid>
            </NForm>
          </AppCard>
        </div>

        <div class="system-provider-editor__footer">
          <NButton secondary @click="emit('reset')">
            重置草稿
          </NButton>
          <NSpace :size="10">
            <NButton secondary @click="emit('update:show', false)">
              关闭
            </NButton>
            <NButton
              type="primary"
              :disabled="!dirty"
              :loading="saving"
              @click="emit('save')"
            >
              保存定义
            </NButton>
          </NSpace>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.system-panel__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.system-panel__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
}

.system-inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: var(--om-text-2);
  font-size: 13px;
}

.system-provider-editor__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-editor {
  display: grid;
  gap: 16px;
  padding-bottom: 10px;
}

.system-provider-editor__tip {
  border-radius: 16px;
}

.system-provider-editor__toolbar,
.system-provider-editor__footer,
.system-provider-editor__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.system-provider-editor__list {
  display: grid;
  gap: 12px;
}

.system-provider-editor__card {
  padding: 16px;
}

.system-provider-editor__card-head {
  align-items: flex-start;
  margin-bottom: 12px;
}

.system-provider-editor__card-head strong {
  display: block;
  color: var(--om-text-1);
  font-size: 15px;
}

.system-provider-editor__card-head p {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-provider-editor__form :deep(.n-form-item) {
  margin-bottom: 0;
}

.system-provider-editor__footer {
  padding-top: 4px;
}

@media (max-width: 760px) {
  .system-provider-editor__header,
  .system-provider-editor__toolbar,
  .system-provider-editor__footer,
  .system-provider-editor__card-head {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

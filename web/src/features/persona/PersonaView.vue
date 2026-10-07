<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NInput, NSelect, NSpace, NTag } from 'naive-ui'

import { apiErrorMessage, apiRequest } from '@/api/client'
import type { EditableConfig, PersonaPreviewResponse } from '@/api/types'
import {
  hasSettingsDraftChanges,
  loadSettings,
  cancelReload,
  confirmReload,
  requestReload,
  saveSettings,
  settingsState,
  updateConfigField,
} from '@/features/settings/store'

const MAX_PERSONA_NAME = 80
const MAX_PERSONA_INSTRUCTIONS = 6000
const MAX_PERSONA_SOURCE = 24000
const personaModeOptions = [
  { label: '简单人格', value: 'simple' },
  { label: 'source.md 人格', value: 'source' },
]

const draftName = computed(() => settingsState.draft?.persona_name ?? '')
const draftInstructions = computed(() => settingsState.draft?.persona_instructions ?? '')
const draftMode = computed(() => settingsState.draft?.persona_mode ?? 'simple')
const draftSource = computed(() => settingsState.draft?.persona_source_markdown ?? '')
const runningName = computed(() => settingsState.snapshot?.effective_config.persona_name ?? '')
const runningInstructions = computed(() => settingsState.snapshot?.effective_config.persona_instructions ?? '')
const runningMode = computed(() => settingsState.snapshot?.effective_config.persona_mode ?? 'simple')

const preview = ref<PersonaPreviewResponse | null>(null)
const previewBusy = ref(false)
const previewError = ref('')
let previewSequence = 0

function withoutPersona(config: EditableConfig): Record<string, unknown> {
  const {
    persona_name: _name,
    persona_instructions: _instructions,
    persona_mode: _mode,
    persona_source_markdown: _source,
    ...rest
  } = config
  return rest
}

const hasOtherDraftChanges = computed(() => {
  const { draft, snapshot } = settingsState
  if (!draft || !snapshot || settingsState.advancedEdited) return false
  return JSON.stringify(withoutPersona(draft)) !== JSON.stringify(withoutPersona(snapshot.config))
})

const editingBlocked = computed(() => Boolean(
  !settingsState.draft || settingsState.loading || settingsState.saving || settingsState.advancedEdited,
))
const canSave = computed(() => Boolean(
  settingsState.draft
  && hasSettingsDraftChanges()
  && !settingsState.loading
  && !settingsState.saving
  && !settingsState.advancedEdited,
))

function updatePersonaField(
  key: 'persona_name' | 'persona_instructions',
  value: string,
): boolean {
  const maxLength = key === 'persona_name' ? MAX_PERSONA_NAME : MAX_PERSONA_INSTRUCTIONS
  return updateConfigField(key, value.slice(0, maxLength))
}

function clearPersonaPreview(): void {
  previewSequence += 1
  preview.value = null
  previewError.value = ''
  previewBusy.value = false
}

function updatePersonaMode(value: 'simple' | 'source'): boolean {
  const updated = updateConfigField('persona_mode', value)
  if (updated) clearPersonaPreview()
  return updated
}

function updatePersonaSource(value: string): boolean {
  const updated = updateConfigField('persona_source_markdown', value.slice(0, MAX_PERSONA_SOURCE))
  if (updated) clearPersonaPreview()
  return updated
}

watch([draftMode, draftSource], clearPersonaPreview, { flush: 'sync' })

async function previewPersona(): Promise<boolean> {
  if (previewBusy.value || draftMode.value !== 'source') return false
  const sequence = ++previewSequence
  previewBusy.value = true
  previewError.value = ''
  try {
    const result = await apiRequest<PersonaPreviewResponse>('/api/admin/persona/preview', {
      method: 'POST',
      adminMutation: true,
      body: { source_markdown: draftSource.value },
    })
    if (sequence !== previewSequence) return false
    preview.value = result
    return result.valid
  } catch (error: unknown) {
    if (sequence === previewSequence) {
      preview.value = null
      previewError.value = apiErrorMessage(error)
    }
    return false
  } finally {
    if (sequence === previewSequence) previewBusy.value = false
  }
}

async function savePersona(): Promise<boolean> {
  if (!canSave.value) return false
  return saveSettings()
}

onMounted(() => {
  void loadSettings()
})
</script>

<template>
  <section class="persona-view" aria-label="人格设定">
    <n-alert v-if="settingsState.error" class="notice" type="error" :show-icon="true">
      <div class="error-content">
        <span>{{ settingsState.error }}</span>
        <n-button size="small" secondary :loading="settingsState.loading" :disabled="settingsState.saving" @click="requestReload">
          重新读取
        </n-button>
      </div>
      <span class="notice-detail">读取失败或版本冲突时，当前草稿会保留在页面中；重新读取有未保存草稿时会先要求确认。</span>
    </n-alert>
    <n-alert v-if="settingsState.notice" class="notice" type="info" :show-icon="true">
      {{ settingsState.notice }}
    </n-alert>

    <n-card v-if="settingsState.loading && !settingsState.snapshot" class="page-card" :bordered="false">
      <div class="empty-state">正在读取人格配置…</div>
    </n-card>

    <template v-if="settingsState.snapshot && settingsState.draft">
      <n-card class="page-card" :bordered="false">
        <div class="section-heading">
          <div>
            <p class="eyebrow">回复上下文</p>
            <h2>人格设定</h2>
            <p class="subtle-text">设置 Bot 在普通回复、Thinker 判断和工具续写中使用的人格上下文；这里不改变权限策略或模型配置。</p>
          </div>
          <n-space align="center" size="small">
            <router-link class="runtime-entry" to="/runtime">运行管理</router-link>
            <n-tag :type="settingsState.snapshot.restart_required ? 'warning' : 'success'" size="small">
              {{ settingsState.snapshot.restart_required ? '待重启生效' : '运行中已生效' }}
            </n-tag>
            <n-tag type="info" size="small">
              运行版本 {{ settingsState.snapshot.effective_revision }}
            </n-tag>
            <n-tag type="default" size="small">
              {{ runningMode === 'source' ? 'source.md 运行版' : '简单人格运行版' }}
            </n-tag>
          </n-space>
        </div>

        <n-alert v-if="settingsState.advancedEdited" class="notice" type="warning" :show-icon="true">
          模型配置页的高级 JSON 有未应用修改。请先应用 JSON 到字段；人格编辑和保存已暂停，避免覆盖未应用内容。
        </n-alert>
        <n-alert v-else-if="hasOtherDraftChanges" class="notice" type="info" :show-icon="true">
          模型配置页还有未保存的修改。本页保存会提交共享配置草稿，因此这些修改也会一起保存。
        </n-alert>
        <n-alert v-else-if="hasSettingsDraftChanges()" class="notice" type="warning" :show-icon="true">
          人格草稿尚未保存。保存后需要重启实例，运行内容才会更新。
        </n-alert>
        <div v-if="settingsState.reloadPending" class="confirm-strip">
          <p>当前页面有未保存草稿。重新读取会放弃这些修改。</p>
          <div class="button-row">
            <n-button size="small" @click="cancelReload">
              继续编辑
            </n-button>
            <n-button size="small" type="warning" @click="void confirmReload()">放弃草稿并重新读取
            </n-button>
          </div>
        </div>

        <div class="form-grid persona-form">
          <div class="form-field form-field-wide">
            <label class="field-label" for="persona-mode">人格来源</label>
            <n-select
              id="persona-mode"
              :value="draftMode"
              :options="personaModeOptions"
              :disabled="editingBlocked"
              @update:value="(value) => updatePersonaMode(value)"
            />
            <p class="form-hint">保存只形成配置草稿；运行管理中应用已保存配置后，运行版本才会变化。</p>
          </div>
          <template v-if="draftMode === 'simple'">
            <div class="form-field form-field-wide">
              <label class="field-label" for="persona-name">人格名称</label>
              <n-input
                id="persona-name"
                :value="draftName"
                :disabled="editingBlocked"
                maxlength="80"
                show-count
                placeholder="例如：小岚、工作助理；留空表示未设置"
                :input-props="{ autocomplete: 'off', 'aria-label': '人格名称' }"
                @update:value="(value) => updatePersonaField('persona_name', value)"
              />
              <p class="form-hint">最多 {{ MAX_PERSONA_NAME }} 个字符。名称会作为回复上下文的一部分，不是 QQ 账号昵称。</p>
            </div>
            <div class="form-field form-field-wide">
              <label class="field-label" for="persona-instructions">身份与风格</label>
              <n-input
                id="persona-instructions"
                type="textarea"
                :value="draftInstructions"
                :disabled="editingBlocked"
                maxlength="6000"
                show-count
                :autosize="{ minRows: 8, maxRows: 18 }"
                placeholder="例如：你是一个克制、清晰的本地助手。先给结论，再补充必要细节；调用工具后用自然语言说明结果。"
                :input-props="{ 'aria-label': '身份与风格' }"
                @update:value="(value) => updatePersonaField('persona_instructions', value)"
              />
              <p class="form-hint">最多 {{ MAX_PERSONA_INSTRUCTIONS }} 个字符。留空不会附加人格指令，模型仍按基础系统约束运行。</p>
            </div>
          </template>
          <template v-else>
            <div class="form-field form-field-wide">
              <label class="field-label" for="persona-source">source.md</label>
              <n-input
                id="persona-source"
                type="textarea"
                :value="draftSource"
                :disabled="editingBlocked"
                :maxlength="MAX_PERSONA_SOURCE"
                show-count
                :autosize="{ minRows: 14, maxRows: 30 }"
                placeholder="粘贴受限的 source.md；保存和运行应用会分别校验并编译。"
                :input-props="{ 'aria-label': 'source.md 人格正文' }"
                @update:value="updatePersonaSource"
              />
              <p class="form-hint">最多 {{ MAX_PERSONA_SOURCE }} 个字符。导入、预览和保存不会自动激活运行版本。</p>
            </div>
            <div class="form-field form-field-wide source-actions">
              <div class="button-row">
                <n-button secondary :loading="previewBusy" :disabled="editingBlocked || previewBusy" @click="void previewPersona()">
                  编译只读预览
                </n-button>
                <span class="form-hint">预览使用管理员会话，不写配置、不调用模型、不发送 QQ。</span>
              </div>
              <n-alert v-if="previewError" class="notice" type="error" :show-icon="true">
                {{ previewError }}
              </n-alert>
              <n-alert v-if="preview && !preview.valid" class="notice" type="error" :show-icon="true">
                <div v-for="issue in preview.issues" :key="`${issue.code}-${issue.line}`">
                  第 {{ issue.line }} 行：{{ issue.message }}（{{ issue.code }}）
                </div>
              </n-alert>
              <div v-if="preview && preview.valid" class="source-preview" aria-label="source.md 编译结果">
                <div class="preview-heading">
                  <div>
                    <p class="eyebrow">编译结果</p>
                    <h3>有效人格版本</h3>
                  </div>
                  <span class="subtle">{{ preview.version }} · {{ preview.source_ref }}</span>
                </div>
                <pre class="preview-value preview-text">{{ preview.system }}</pre>
              </div>
            </div>
          </template>
        </div>

        <div class="persona-preview" aria-label="运行内容只读预览">
          <div class="preview-heading">
            <div>
              <p class="eyebrow">只读预览</p>
              <h3>当前运行内容</h3>
            </div>
            <span class="subtle">版本 {{ settingsState.snapshot.effective_revision }}</span>
          </div>
          <div class="preview-grid">
            <div class="preview-block">
              <span class="preview-label">运行中名称</span>
              <strong v-if="runningMode === 'simple' && runningName" class="preview-value">{{ runningName }}</strong>
              <strong v-else-if="runningMode === 'source'" class="preview-value">source.md 编译人格</strong>
              <span v-else class="preview-empty">未设置</span>
            </div>
            <div class="preview-block preview-block-wide">
              <span class="preview-label">运行中身份与风格</span>
              <pre v-if="runningMode === 'simple' && runningInstructions" class="preview-value preview-text">{{ runningInstructions }}</pre>
              <span v-else-if="runningMode === 'source'" class="preview-value">运行版来自已应用的 source.md 编译结果</span>
              <span v-else class="preview-empty">未设置身份与风格指令</span>
            </div>
          </div>
        </div>
      </n-card>

      <div class="settings-save-bar" role="region" aria-label="保存人格设定">
        <div>
          <strong>{{ hasSettingsDraftChanges() ? '有未保存的配置草稿' : '人格设定未修改' }}</strong>
          <span>基于版本 {{ settingsState.baseRevision }}；完整配置使用版本校验，保存后需重启才会进入运行预览。</span>
        </div>
        <n-button type="primary" :disabled="!canSave" :loading="settingsState.saving" @click="void savePersona()">
          保存人格与配置
        </n-button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.persona-form {
  margin-top: 24px;
}

.runtime-entry {
  color: var(--om-primary-dark);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}

.runtime-entry:hover {
  text-decoration: underline;
}

.notice-detail {
  display: block;
  margin-top: 4px;
  color: var(--om-muted);
  font-size: 12px;
}

.error-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.persona-preview {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.preview-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.preview-heading h3,
.preview-heading p {
  margin: 0;
}

.preview-heading h3 {
  margin-top: 4px;
  color: var(--om-text);
  font-size: 16px;
  font-weight: 720;
}

.preview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  gap: 16px;
  margin-top: 16px;
}

.preview-block {
  min-width: 0;
  padding: 12px 16px;
  background: var(--om-surface-soft);
  border: 1px solid var(--om-border);
  border-radius: var(--om-radius-sm);
}

.preview-block-wide {
  min-height: 96px;
}

.preview-label {
  display: block;
  color: var(--om-muted);
  font-size: 12px;
}

.preview-value,
.preview-empty {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
}

.preview-value {
  color: var(--om-text);
  font-weight: 680;
}

.preview-text {
  margin-bottom: 0;
  white-space: pre-wrap;
  font: inherit;
  font-weight: 500;
  line-height: 1.55;
}

.preview-empty {
  color: var(--om-muted);
}

@media (max-width: 700px) {
  .error-content {
    align-items: flex-start;
    flex-direction: column;
  }

  .preview-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

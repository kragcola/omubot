<script setup lang="ts">
import {
  CheckmarkCircleOutline,
  DocumentTextOutline,
  FolderOpenOutline,
  RefreshOutline,
  SaveOutline,
  ShieldCheckmarkOutline,
  SparklesOutline,
  WarningOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'

type IssueLevel = 'error' | 'warn' | 'info'
type NaiveTagType = 'default' | 'success' | 'warning' | 'error' | 'info'

interface SourceSpan {
  file?: string
  lines?: [number, number] | number[]
}

interface ImportIssue {
  level?: IssueLevel | string
  code?: string
  message?: string
  file?: string
  key_path?: string
  source_span?: SourceSpan
}

interface ReportField {
  file?: string
  key_path?: string
  source_span?: SourceSpan
  confidence?: number
  extractor?: string
  default_used?: boolean
  issue_level?: IssueLevel | string
}

interface ImportReport {
  schema?: string
  persona_id?: string
  source_file?: string
  source_hash?: string
  fields?: ReportField[]
  issues?: ImportIssue[]
  generated_files?: string[]
  status?: string
}

interface PersonaSourceResponse {
  ok: boolean
  persona_id: string
  path?: string
  exists?: boolean
  content?: string
  bytes?: number
  error?: string
}

interface PersonaImportResponse {
  ok: boolean
  persona_id: string
  draft_dir?: string
  report?: ImportReport
  error?: string
}

interface PersonaDraftResponse {
  ok: boolean
  persona_id: string
  draft_dir?: string
  files?: string[]
  report?: ImportReport
  error?: string
}

interface PersonaFreezeResponse {
  ok: boolean
  mode?: string
  persona_id: string
  path?: string
  report?: ImportReport
  error?: string
}

const DEFAULT_PERSONA_ID = 'fengxiaomeng'

const message = useMessage()

const personaId = ref(DEFAULT_PERSONA_ID)
const source = ref('')
const originalSource = ref('')
const sourceExists = ref(false)
const sourcePath = ref('')
const sourceLoaded = ref(false)
const sourceDirtySinceImport = ref(false)

const draft = ref<PersonaDraftResponse | null>(null)
const report = ref<ImportReport | null>(null)
const lastFreeze = ref<PersonaFreezeResponse | null>(null)
const lastAction = ref('')
const loadError = ref('')

const loadingSource = ref(false)
const savingSource = ref(false)
const importing = ref(false)
const refreshingDraft = ref(false)
const freezing = ref(false)

const personaKey = computed(() => personaId.value.trim())
const namespace = computed(() => {
  const value = personaKey.value || DEFAULT_PERSONA_ID
  return value.endsWith('-v2') ? value : `${value}-v2`
})
const sourceDirty = computed(() => source.value !== originalSource.value)
const issues = computed(() => Array.isArray(report.value?.issues) ? report.value.issues : [])
const fields = computed(() => Array.isArray(report.value?.fields) ? report.value.fields : [])
const generatedFiles = computed(() => {
  const files = draft.value?.files?.length
    ? draft.value.files
    : (Array.isArray(report.value?.generated_files) ? report.value.generated_files : [])
  return [...new Set(files)].sort((a, b) => a.localeCompare(b))
})
const issueCount = computed(() => issues.value.length)
const errorCount = computed(() => issues.value.filter(item => item.level === 'error').length)
const warnCount = computed(() => issues.value.filter(item => item.level === 'warn').length)
const hasErrors = computed(() => errorCount.value > 0 || report.value?.status === 'error')
const canSaveSource = computed(() => Boolean(personaKey.value) && sourceDirty.value && !savingSource.value)
const canImport = computed(() =>
  Boolean(personaKey.value)
  && Boolean(source.value.trim())
  && !sourceDirty.value
  && !importing.value,
)
const canFreeze = computed(() =>
  Boolean(draft.value?.ok)
  && !hasErrors.value
  && !sourceDirty.value
  && !sourceDirtySinceImport.value
  && !freezing.value,
)
const reportStatusType = computed<NaiveTagType>(() => {
  if (!report.value) return 'default'
  if (hasErrors.value) return 'error'
  if (warnCount.value > 0) return 'warning'
  return 'success'
})
const reportStatusLabel = computed(() => {
  if (!report.value) return '未导入'
  if (hasErrors.value) return '有错误'
  if (warnCount.value > 0) return '有警告'
  return '通过'
})
const sourceMetricValue = computed(() => {
  if (sourceDirty.value) return '未保存'
  if (!sourceLoaded.value) return '未加载'
  return sourceExists.value ? '已加载' : '待创建'
})
const sourceMetricHint = computed(() => {
  if (sourceDirty.value) return '保存后才能重新导入'
  if (sourceDirtySinceImport.value) return 'source 已变更，需重新导入'
  return sourcePath.value || namespace.value
})
const freezeMetricValue = computed(() => lastFreeze.value?.ok ? '已暂存' : '未暂存')
const freezeMetricHint = computed(() => {
  if (sourceDirty.value) return 'source 尚未保存'
  if (sourceDirtySinceImport.value) return '保存后需要重新导入'
  if (hasErrors.value) return '存在 error，已阻止'
  return lastFreeze.value?.path || '_pending_freeze/'
})
const sourceEditorPlaceholder = computed(() => `---
persona_id: ${personaKey.value || DEFAULT_PERSONA_ID}
canonical_name:
version_hint: 2.1.0
language: zh-CN
---

# 1. 是谁（必填）

- 一句话角色：
- 自称：我
`)

onMounted(() => {
  void loadAll(true)
})

async function loadAll(silent = false) {
  await loadSource(silent)
  await refreshDraft(true)
}

async function loadSource(silent = false) {
  if (!personaKey.value) {
    message.warning('先填写 persona_id')
    return
  }
  loadingSource.value = true
  loadError.value = ''
  try {
    const data = await api<PersonaSourceResponse>(`/api/admin/persona/source/${encodeURIComponent(personaKey.value)}`)
    if (!data.ok) throw new Error(data.error || 'source 读取失败')
    source.value = data.content || ''
    originalSource.value = data.content || ''
    sourceExists.value = Boolean(data.exists)
    sourcePath.value = data.path || ''
    sourceLoaded.value = true
    lastAction.value = data.exists ? 'source 已加载' : 'source 待创建'
    if (!silent) message.success(data.exists ? 'source 已加载' : 'source 尚未创建')
  } catch (error) {
    loadError.value = explainError(error)
    if (!silent) message.error(loadError.value)
  } finally {
    loadingSource.value = false
  }
}

async function saveSource() {
  if (!personaKey.value) {
    message.warning('先填写 persona_id')
    return
  }
  savingSource.value = true
  loadError.value = ''
  try {
    const data = await api<PersonaSourceResponse>(`/api/admin/persona/source/${encodeURIComponent(personaKey.value)}`, {
      method: 'PUT',
      body: { content: source.value },
    })
    if (!data.ok) throw new Error(data.error || 'source 保存失败')
    source.value = data.content ?? source.value
    originalSource.value = source.value
    sourceExists.value = true
    sourcePath.value = data.path || sourcePath.value
    sourceLoaded.value = true
    sourceDirtySinceImport.value = true
    lastFreeze.value = null
    lastAction.value = `source 已保存 · ${data.bytes ?? 0} bytes`
    message.success('source 已保存')
  } catch (error) {
    loadError.value = explainError(error)
    message.error(loadError.value)
  } finally {
    savingSource.value = false
  }
}

async function importDraft() {
  if (!personaKey.value) {
    message.warning('先填写 persona_id')
    return
  }
  if (sourceDirty.value) {
    message.warning('source 尚未保存')
    return
  }
  importing.value = true
  loadError.value = ''
  try {
    const data = await api<PersonaImportResponse>('/api/admin/persona/import', {
      method: 'POST',
      body: { persona_id: personaKey.value },
    })
    if (data.error) throw new Error(data.error)
    report.value = data.report || null
    sourceDirtySinceImport.value = false
    lastFreeze.value = null
    await refreshDraft(true)
    lastAction.value = data.ok ? 'draft 已导入' : 'draft 已生成，存在 issue'
    if (data.ok) message.success('draft 已导入')
    else message.warning('draft 已生成，存在 issue')
  } catch (error) {
    loadError.value = explainError(error)
    message.error(loadError.value)
  } finally {
    importing.value = false
  }
}

async function refreshDraft(silent = false) {
  if (!personaKey.value) return
  refreshingDraft.value = true
  try {
    const data = await api<PersonaDraftResponse>(`/api/admin/persona/draft/${encodeURIComponent(personaKey.value)}`)
    if (!data.ok) {
      draft.value = null
      report.value = null
      if (!silent) message.warning(data.error || 'draft 不存在')
      return
    }
    draft.value = data
    report.value = data.report || report.value
    lastAction.value = 'draft 已刷新'
    if (!silent) message.success('draft 已刷新')
  } catch (error) {
    if (!silent) message.error(explainError(error))
  } finally {
    refreshingDraft.value = false
  }
}

async function pendingFreeze() {
  if (!canFreeze.value) {
    message.warning(freezeMetricHint.value)
    return
  }
  freezing.value = true
  loadError.value = ''
  try {
    const data = await api<PersonaFreezeResponse>(`/api/admin/persona/freeze/${encodeURIComponent(personaKey.value)}`, {
      method: 'POST',
      body: { confirm: true },
    })
    if (!data.ok) throw new Error(data.error || 'Pending Freeze 失败')
    lastFreeze.value = data
    lastAction.value = 'Pending Freeze 已暂存'
    message.success('Pending Freeze 已暂存')
  } catch (error) {
    loadError.value = explainError(error)
    message.error(loadError.value)
  } finally {
    freezing.value = false
  }
}

function explainError(error: unknown) {
  if (error instanceof Error && error.message) return error.message
  return '请求失败'
}

function issueType(level?: string): NaiveTagType {
  if (level === 'error') return 'error'
  if (level === 'warn') return 'warning'
  if (level === 'info') return 'info'
  return 'default'
}

function confidenceLabel(value?: number) {
  if (typeof value !== 'number') return '—'
  return `${Math.round(value * 100)}%`
}

function spanLabel(span?: SourceSpan) {
  const lines = span?.lines
  if (!Array.isArray(lines) || lines.length < 2) return '无 source span'
  return `${span?.file || 'source.md'}:${lines[0]}-${lines[1]}`
}
</script>

<template>
  <AppPage
    title="人设导入"
    description="source.md 到 v2 draft 的导入、校验与 Pending Freeze。"
    eyebrow="Persona Source Importer"
  >
    <template #action>
      <NButton secondary :loading="loadingSource || refreshingDraft" @click="loadAll(false)">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        重新加载
      </NButton>
    </template>

    <div class="persona-importer">
      <div class="persona-importer__metrics">
        <MetricCard
          title="Source"
          :value="sourceMetricValue"
          :hint="sourceMetricHint"
          :icon="DocumentTextOutline"
          accent="primary"
        />
        <MetricCard
          title="Draft 文件"
          :value="generatedFiles.length"
          :hint="draft?.draft_dir || '尚未生成 draft'"
          :icon="FolderOpenOutline"
          accent="info"
        />
        <MetricCard
          title="Issues"
          :value="issueCount"
          :hint="`${errorCount} error · ${warnCount} warn`"
          :icon="hasErrors ? WarningOutline : CheckmarkCircleOutline"
          :accent="hasErrors ? 'warning' : 'success'"
        />
        <MetricCard
          title="Pending Freeze"
          :value="freezeMetricValue"
          :hint="freezeMetricHint"
          :icon="ShieldCheckmarkOutline"
          :accent="lastFreeze?.ok ? 'success' : 'warning'"
        />
      </div>

      <PageToolbar>
        <template #left>
          <NInput
            v-model:value="personaId"
            class="persona-importer__id-input"
            placeholder="persona_id"
            :disabled="loadingSource || savingSource || importing || freezing"
            @keyup.enter="loadAll(false)"
          />
          <NTag size="small" :bordered="false">
            {{ namespace }}
          </NTag>
        </template>
        <template #right>
          <NButton secondary :loading="loadingSource" @click="loadSource(false)">
            <template #icon>
              <NIcon :component="FolderOpenOutline" />
            </template>
            加载
          </NButton>
          <NButton secondary type="primary" :disabled="!canSaveSource" :loading="savingSource" @click="saveSource">
            <template #icon>
              <NIcon :component="SaveOutline" />
            </template>
            保存
          </NButton>
          <NButton type="primary" :disabled="!canImport" :loading="importing" @click="importDraft">
            <template #icon>
              <NIcon :component="SparklesOutline" />
            </template>
            导入
          </NButton>
          <NButton secondary :loading="refreshingDraft" @click="refreshDraft(false)">
            <template #icon>
              <NIcon :component="RefreshOutline" />
            </template>
            刷新 draft
          </NButton>
          <NPopconfirm :show-icon="false" @positive-click="pendingFreeze">
            <template #trigger>
              <NButton type="warning" secondary :disabled="!canFreeze" :loading="freezing">
                <template #icon>
                  <NIcon :component="ShieldCheckmarkOutline" />
                </template>
                Pending Freeze
              </NButton>
            </template>
            确认复制当前 draft 到 _pending_freeze/？
          </NPopconfirm>
        </template>
      </PageToolbar>

      <NAlert v-if="loadError" type="error" :bordered="false">
        {{ loadError }}
      </NAlert>

      <div class="persona-importer__grid">
        <AppPanelSection
          eyebrow="Source"
          title="source.md"
          :description="sourcePath || `${namespace}/source.md`"
        >
          <template #aside>
            <NTag v-if="sourceDirty" type="warning" size="small" :bordered="false">
              未保存
            </NTag>
            <NTag v-else-if="sourceDirtySinceImport" type="warning" size="small" :bordered="false">
              需重新导入
            </NTag>
            <NTag v-else :type="sourceExists ? 'success' : 'default'" size="small" :bordered="false">
              {{ sourceExists ? '已存在' : '待创建' }}
            </NTag>
          </template>

          <NInput
            v-model:value="source"
            class="source-editor"
            type="textarea"
            :placeholder="sourceEditorPlaceholder"
            :autosize="{ minRows: 24, maxRows: 32 }"
            :loading="loadingSource"
          />
          <div class="persona-importer__status-line">
            <span>{{ lastAction || '待操作' }}</span>
            <span>{{ source.length }} chars</span>
          </div>
        </AppPanelSection>

        <AppPanelSection
          eyebrow="Draft"
          title="导入报告"
          :description="report?.source_hash ? `source hash ${report.source_hash}` : '等待 draft report'"
        >
          <template #aside>
            <NTag :type="reportStatusType" size="small" :bordered="false">
              {{ reportStatusLabel }}
            </NTag>
          </template>

          <NTabs v-if="report" type="line" animated>
            <NTabPane name="issues" tab="Issues">
              <div v-if="issues.length" class="persona-list">
                <div
                  v-for="(issue, index) in issues"
                  :key="`${issue.code || 'issue'}-${index}`"
                  class="persona-row persona-row--issue"
                  :class="`persona-row--${issue.level || 'info'}`"
                >
                  <div class="persona-row__main">
                    <div class="persona-row__title">
                      <NTag :type="issueType(issue.level)" size="small" :bordered="false">
                        {{ issue.level || 'info' }}
                      </NTag>
                      <span>{{ issue.code || 'issue' }}</span>
                    </div>
                    <p>{{ issue.message || '未提供 message' }}</p>
                  </div>
                  <div class="persona-row__meta">
                    <span>{{ issue.file || 'draft' }}</span>
                    <span>{{ issue.key_path || '—' }}</span>
                    <span>{{ spanLabel(issue.source_span) }}</span>
                  </div>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="没有 issue"
                description="当前 report 未返回 error 或 warn。"
                :icon="CheckmarkCircleOutline"
              />
            </NTabPane>

            <NTabPane name="fields" tab="Fields">
              <div v-if="fields.length" class="persona-list">
                <div
                  v-for="(field, index) in fields"
                  :key="`${field.file || 'field'}-${field.key_path || index}-${index}`"
                  class="persona-row"
                >
                  <div class="persona-row__main">
                    <div class="persona-row__title">
                      <span>{{ field.file || 'draft' }}</span>
                      <NTag v-if="field.default_used" type="info" size="small" :bordered="false">
                        default
                      </NTag>
                    </div>
                    <p>{{ field.key_path || '.' }}</p>
                  </div>
                  <div class="persona-row__meta">
                    <span>{{ field.extractor || 'extractor' }}</span>
                    <span>{{ confidenceLabel(field.confidence) }}</span>
                    <span>{{ spanLabel(field.source_span) }}</span>
                  </div>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="没有字段记录"
                description="当前 report 未返回 fields。"
                :icon="DocumentTextOutline"
              />
            </NTabPane>

            <NTabPane name="files" tab="Files">
              <div v-if="generatedFiles.length" class="file-list">
                <div v-for="file in generatedFiles" :key="file" class="file-row">
                  <NIcon :component="DocumentTextOutline" />
                  <span>{{ file }}</span>
                </div>
              </div>
              <EmptyState
                v-else
                compact
                title="没有 draft 文件"
                description="当前 persona 还没有生成 draft。"
                :icon="FolderOpenOutline"
              />
            </NTabPane>
          </NTabs>

          <EmptyState
            v-else
            compact
            title="还没有导入报告"
            description="保存 source 后导入 draft。"
            :icon="DocumentTextOutline"
          />
        </AppPanelSection>
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.persona-importer {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.persona-importer__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.persona-importer__id-input {
  width: 240px;
}

.persona-importer__grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
  gap: 16px;
  align-items: flex-start;
}

.source-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.persona-importer__status-line {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  color: var(--om-text-3);
  font-size: 12px;
}

.persona-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.persona-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.persona-row--error {
  border-color: color-mix(in srgb, var(--om-danger) 45%, var(--om-border));
}

.persona-row--warn {
  border-color: color-mix(in srgb, var(--om-warning) 45%, var(--om-border));
}

.persona-row__main {
  min-width: 0;
}

.persona-row__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
}

.persona-row__main p {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
}

.persona-row__meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  color: var(--om-text-3);
  font-size: 12px;
  text-align: right;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 8px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 13px;
}

.file-row .n-icon {
  color: rgb(var(--primary-color));
}

@media (max-width: 1100px) {
  .persona-importer__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .persona-importer__grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .persona-importer__metrics {
    grid-template-columns: 1fr;
  }

  .persona-importer__id-input {
    width: 100%;
  }

  .persona-row {
    grid-template-columns: 1fr;
  }

  .persona-row__meta {
    align-items: flex-start;
    text-align: left;
  }
}
</style>

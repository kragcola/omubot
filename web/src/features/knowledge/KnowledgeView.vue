<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { KnowledgeActivationRequest, KnowledgeDocumentView, KnowledgeImportRequest, KnowledgeRemoveRequest, KnowledgeReviewRequest, KnowledgeSourcePageView, KnowledgeSourceView, MarkdownSourceInput } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const group = ref('')
const loadedGroup = ref('')
const page = ref<KnowledgeSourcePageView | null>(null)
const selectedSourceId = ref('')
const source = ref<KnowledgeSourceView | null>(null)
const currentDocument = ref<KnowledgeDocumentView | null>(null)
const busy = ref(false)
const needsReload = ref(false)
const error = ref('')
const notice = ref('')
const confirmNonPersonal = ref(false)
const draft = reactive({
  sourceId: '', sourceLabel: '', title: '', content: '', expectedRevision: 0, boundSourceId: '',
})
let sequence = 0
let disposed = false

function clearDraft() {
  draft.sourceId = ''
  draft.sourceLabel = ''
  draft.title = ''
  draft.content = ''
  draft.expectedRevision = 0
  draft.boundSourceId = ''
  confirmNonPersonal.value = false
}
function clearSource() {
  selectedSourceId.value = ''
  source.value = null
  currentDocument.value = null
  clearDraft()
}
function clearSensitive() {
  sequence += 1
  page.value = null
  loadedGroup.value = ''
  clearSource()
  needsReload.value = false
  error.value = ''
  notice.value = ''
  busy.value = false
}
watch(() => group.value.trim(), target => {
  if (target !== loadedGroup.value) clearSensitive()
})
watch(() => sessionState.generation, clearSensitive)
watch(() => sessionState.adminAuthenticated, authenticated => { if (!authenticated) clearSensitive() })
onBeforeUnmount(() => { disposed = true; clearSensitive() })

const selectedListSource = computed(() => page.value?.items.find(item => item.source_id === selectedSourceId.value) ?? null)
const canWrite = computed(() => sessionState.adminAuthenticated && !busy.value && !needsReload.value
  && page.value !== null && group.value.trim() === loadedGroup.value
  && source.value !== null && selectedListSource.value !== null
  && source.value.source_id === selectedSourceId.value
  && source.value.revision === selectedListSource.value.revision
  && currentDocument.value?.source.source_id === source.value.source_id
  && currentDocument.value.source.revision === source.value.revision)
const contentWithinLimit = computed(() => new TextEncoder().encode(draft.content).byteLength <= 256 * 1024)
const canImport = computed(() => {
  if (!sessionState.adminAuthenticated || busy.value || needsReload.value || !page.value
    || group.value.trim() !== loadedGroup.value || !draft.sourceId.trim()
    || !draft.sourceLabel.trim() || !draft.title.trim() || !contentWithinLimit.value) return false
  if (draft.boundSourceId) return canWrite.value && draft.boundSourceId === source.value?.source_id
    && draft.sourceId === draft.boundSourceId && draft.expectedRevision === source.value.revision
  return !page.value.items.some(item => item.source_id === draft.sourceId.trim())
})
const canApprove = computed(() => canWrite.value && confirmNonPersonal.value)
const canActivate = computed(() => canWrite.value && source.value?.review_status === 'approved'
  && source.value.reviewed_content_revision === source.value.content_revision)
const draftDirty = computed(() => currentDocument.value !== null && (
  draft.boundSourceId !== currentDocument.value.source.source_id
  || draft.sourceId !== currentDocument.value.document.source_id
  || draft.sourceLabel !== currentDocument.value.document.source_label
  || draft.title !== currentDocument.value.document.title
  || draft.content !== currentDocument.value.document.content
  || draft.expectedRevision !== currentDocument.value.source.revision))
const canRebuild = computed(() => canWrite.value && !draftDirty.value)

function resetToNewImport() {
  clearSource()
  notice.value = ''
}
function applyLoadedSource(nextSource: KnowledgeSourceView, document: KnowledgeDocumentView) {
  selectedSourceId.value = nextSource.source_id
  source.value = nextSource
  currentDocument.value = document
  const items = page.value?.items.map(item => item.source_id === nextSource.source_id ? nextSource : item) ?? []
  if (page.value) page.value = { ...page.value, items }
  draft.sourceId = nextSource.source_id
  draft.sourceLabel = document.document.source_label
  draft.title = document.document.title
  draft.content = document.document.content
  draft.expectedRevision = nextSource.revision
  draft.boundSourceId = nextSource.source_id
  confirmNonPersonal.value = false
}

async function readSource(target: string, sourceId: string, requestSequence: number, epoch: number): Promise<boolean> {
  const detail = await apiRequest<KnowledgeSourceView>(`/api/admin/knowledge/sources/${encodeURIComponent(sourceId)}?group_id=${encodeURIComponent(target)}`)
  if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return false
  const params = new URLSearchParams({ group_id: target, expected_revision: String(detail.revision) })
  const document = await apiRequest<KnowledgeDocumentView>(`/api/admin/knowledge/sources/${encodeURIComponent(sourceId)}/document?${params}`)
  if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return false
  applyLoadedSource(detail, document)
  return true
}

async function load(more = false) {
  if (busy.value || !sessionState.adminAuthenticated) return
  const target = group.value.trim()
  if (!target || (more && (!page.value || target !== loadedGroup.value))) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  const after = more ? page.value?.next_cursor : null
  busy.value = true
  error.value = ''
  notice.value = ''
  if (!more) clearSource()
  try {
    const params = new URLSearchParams({ group_id: target, limit: '50' })
    if (after) params.set('after', after)
    const nextPage = await apiRequest<KnowledgeSourcePageView>(`/api/admin/knowledge/sources?${params}`)
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    const items = new Map((more ? page.value?.items ?? [] : []).map(item => [item.source_id, item]))
    for (const item of nextPage.items) items.set(item.source_id, item)
    page.value = { ...nextPage, items: [...items.values()] }
    loadedGroup.value = target
    needsReload.value = false
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clearSensitive() }
    else error.value = apiErrorMessage(cause)
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

async function selectSource(item: KnowledgeSourceView) {
  if (busy.value || !sessionState.adminAuthenticated || !page.value || group.value.trim() !== loadedGroup.value) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  clearSource()
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await readSource(loadedGroup.value, item.source_id, requestSequence, epoch)
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clearSensitive() }
    else {
      needsReload.value = true
      error.value = apiErrorMessage(cause) + ' 请重新读取来源列表后再操作。'
    }
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

function review(decision: KnowledgeReviewRequest['decision']) {
  if (!canWrite.value || !source.value || (decision === 'approved' && !confirmNonPersonal.value)) return
  const body: KnowledgeReviewRequest = {
    group_id: loadedGroup.value, source_id: source.value.source_id,
    expected_revision: source.value.revision, decision,
    ...(decision === 'approved' ? { non_personal_document: true } : {}),
  }
  return mutate('/api/admin/knowledge/review', body, source.value.source_id)
}
function activation(active: boolean) {
  if (!canWrite.value || !source.value || (active && !canActivate.value)) return
  const body: KnowledgeActivationRequest = {
    group_id: loadedGroup.value, source_id: source.value.source_id,
    expected_revision: source.value.revision, active,
  }
  return mutate('/api/admin/knowledge/activation', body, source.value.source_id)
}
function removeSource() {
  if (!canWrite.value || !source.value) return
  const body: KnowledgeRemoveRequest = {
    group_id: loadedGroup.value, source_id: source.value.source_id,
    expected_revision: source.value.revision,
  }
  return mutate('/api/admin/knowledge/remove', body, source.value.source_id)
}
function rebuildSource() {
  if (!canWrite.value || !source.value) return
  if (draftDirty.value) {
    error.value = '当前有未保存草稿；请先保存或重新读取正文，再从已保存原文重建。'
    return
  }
  const body: KnowledgeRemoveRequest = {
    group_id: loadedGroup.value, source_id: source.value.source_id,
    expected_revision: source.value.revision,
  }
  return mutate('/api/admin/knowledge/rebuild', body, source.value.source_id,
    '已从已保存原文重建；来源修订已更新，内容修订保持不变，当前审阅与生效状态已重新读取。')
}
function saveDocument() {
  if (!canImport.value) return
  const document: MarkdownSourceInput = {
    classification: 'non_personal_document', format: 'markdown',
    source_id: draft.sourceId.trim(), source_label: draft.sourceLabel.trim(),
    title: draft.title.trim(), content: draft.content,
  }
  const body: KnowledgeImportRequest = {
    group_id: loadedGroup.value, expected_revision: draft.expectedRevision, document,
  }
  return mutate('/api/admin/knowledge/import', body, document.source_id)
}

async function mutate(path: string, body: KnowledgeImportRequest | KnowledgeReviewRequest | KnowledgeActivationRequest | KnowledgeRemoveRequest, preserveSourceId: string,
  successNotice = '更改已保存，当前审阅与生效状态已重新读取。') {
  if ((!canWrite.value && path !== '/api/admin/knowledge/import') || !sessionState.adminAuthenticated
    || !page.value || group.value.trim() !== loadedGroup.value) return
  if (path === '/api/admin/knowledge/import' && !canImport.value) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  const target = loadedGroup.value
  busy.value = true
  error.value = ''
  notice.value = ''
  let applied = false
  try {
    await apiRequest(path, { method: 'POST', body, adminMutation: true })
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    applied = true
    needsReload.value = true
    const params = new URLSearchParams({ group_id: target, limit: '50' })
    const refreshed = await apiRequest<KnowledgeSourcePageView>(`/api/admin/knowledge/sources?${params}`)
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    page.value = refreshed
    loadedGroup.value = target
    if (preserveSourceId && refreshed.items.some(item => item.source_id === preserveSourceId)) {
      await readSource(target, preserveSourceId, requestSequence, epoch)
      if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    } else clearSource()
    needsReload.value = false
    notice.value = successNotice
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clearSensitive() }
    else {
      needsReload.value = applied || (isApiError(cause) && cause.status === 409)
      const guidance = isApiError(cause) && cause.status === 409
        ? '版本冲突；草稿已保留，请重新读取当前版本后再提交。'
        : applied ? '操作已提交，但刷新读取失败；请重新读取核对。' : '写入失败；草稿已保留，请重新读取核对后再操作。'
      error.value = `${apiErrorMessage(cause)} ${guidance}`
    }
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}

const statusLabel: Record<KnowledgeSourceView['status'], string> = { active: '生效中', inactive: '未生效', removed: '已移除' }
const reviewLabel: Record<KnowledgeSourceView['review_status'], string> = { pending: '待审阅', approved: '已批准', rejected: '已拒绝' }
</script>

<template>
  <n-card class="page-card" :bordered="false" title="知识来源管理">
    <p class="form-hint">只管理显式导入的非个人 Markdown 文档，不扫描路径或群记录。文档提及某个人不会授予个人学习权限；这里的批准与激活只管理知识来源。</p>
    <form class="form-row" @submit.prevent="void load()">
      <label for="knowledge-group">群 ID</label>
      <n-input id="knowledge-group" v-model:value="group" :disabled="busy" placeholder="输入已获授权的群 ID" />
      <n-button attr-type="submit" :loading="busy" :disabled="!sessionState.adminAuthenticated || busy || !group.trim()">读取知识来源</n-button>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false" role="alert">{{ error }}</n-alert>
    <n-alert v-if="notice" type="success" :show-icon="false">{{ notice }}</n-alert>
    <template v-if="page">
      <n-empty v-if="!page.items.length" description="该群尚无知识来源" />
      <article v-for="item in page.items" :key="item.source_id" class="style-entry">
        <p><strong>{{ item.title }}</strong> · {{ item.source_label }} · {{ item.source_id }}</p>
        <p>修订 {{ item.revision }} · 内容修订 {{ item.content_revision }} · <n-tag>{{ reviewLabel[item.review_status] }}</n-tag> <n-tag>{{ statusLabel[item.status] }}</n-tag></p>
        <p class="form-hint">{{ item.status === 'active' ? '当前已生效' : '当前未生效' }} · 已审内容修订 {{ item.reviewed_content_revision ?? '无' }}</p>
        <n-button :disabled="busy || group.trim() !== loadedGroup" @click="void selectSource(item)">读取正文并编辑</n-button>
      </article>
      <n-button v-if="page.next_cursor !== null" :disabled="busy || group.trim() !== loadedGroup" @click="void load(true)">加载更多来源</n-button>
      <n-button :disabled="busy || group.trim() !== loadedGroup" @click="resetToNewImport">新建来源</n-button>
    </template>
    <template v-if="page && group.trim() === loadedGroup">
      <h3>{{ draft.boundSourceId ? `编辑来源：${draft.boundSourceId}` : '导入 Markdown 文档' }}</h3>
      <form @submit.prevent="void saveDocument()">
        <label for="knowledge-source-id">来源 ID</label>
        <n-input id="knowledge-source-id" v-model:value="draft.sourceId" :disabled="busy || Boolean(draft.boundSourceId)" />
        <label for="knowledge-source-label">来源标签</label>
        <n-input id="knowledge-source-label" v-model:value="draft.sourceLabel" :disabled="busy" />
        <label for="knowledge-title">标题</label>
        <n-input id="knowledge-title" v-model:value="draft.title" :disabled="busy" />
        <p v-if="draft.boundSourceId" class="form-hint">正在编辑已读取的 revision {{ draft.expectedRevision }}；提交采用该版本做 CAS。新建来源使用 revision 0。</p>
        <label for="knowledge-markdown">Markdown 正文（最多 256 KiB UTF-8）</label>
        <n-input id="knowledge-markdown" v-model:value="draft.content" type="textarea" :autosize="{ minRows: 8, maxRows: 24 }" :disabled="busy" />
        <p class="form-hint">分类固定为非个人资料；未净化 HTML 不会渲染。批准前请阅读上方正文并显式确认。</p>
        <n-button attr-type="submit" :disabled="!canImport" :loading="busy">{{ draft.boundSourceId ? '保存当前文档修订' : '导入非个人文档' }}</n-button>
      </form>
    </template>
    <template v-if="source && currentDocument && selectedSourceId === source.source_id">
      <h3>当前正文：{{ currentDocument.document.title }}</h3>
      <pre class="style-content">{{ currentDocument.document.content }}</pre>
      <p>审阅：<n-tag>{{ reviewLabel[source.review_status] }}</n-tag> · 生效：<n-tag>{{ statusLabel[source.status] }}</n-tag> · revision {{ source.revision }}</p>
      <n-checkbox v-model:checked="confirmNonPersonal" :disabled="busy">我已阅读当前 revision，并确认它是非个人资料；提及人物不授予个人学习权限。</n-checkbox>
      <div class="form-row">
        <n-button :disabled="!canApprove" :loading="busy" @click="void review('approved')">批准当前 revision</n-button>
        <n-button :disabled="!canWrite" :loading="busy" @click="void review('rejected')">拒绝当前 revision</n-button>
        <n-button v-if="source.status !== 'active'" :disabled="!canActivate" :loading="busy" @click="void activation(true)">激活已批准来源</n-button>
        <n-button v-else :disabled="!canWrite" :loading="busy" @click="void activation(false)">停用来源</n-button>
        <n-button :disabled="!canRebuild" :loading="busy" @click="void rebuildSource()">从已保存原文重建</n-button>
        <n-button :disabled="!canWrite" :loading="busy" @click="void removeSource()">移除来源</n-button>
      </div>
      <p v-if="draftDirty" class="form-hint">当前有未保存草稿；请先保存或重新读取正文，再重建已保存原文。</p>
      <p class="form-hint">待审阅或未生效来源不计为已生效。所有写入按已读取 revision 提交，冲突时保留草稿并要求重新读取。</p>
    </template>
  </n-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NTag } from 'naive-ui'
import { apiErrorMessage, isApiError } from '@/api/client'
import {
  importCharacterReference, mergeCharacterReference, readCharacterReference,
  restoreCharacterReference, saveCharacterReference,
} from '@/api/characterReference'
import type {
  CharacterReferenceBackupView, CharacterReferenceMutationView, CharacterReferenceStatusView,
} from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const snapshot = ref<CharacterReferenceStatusView | null>(null)
const loadedRevision = ref<string | null>(null)
const draft = ref('')
const confirmed = ref(false)
const backup = ref<CharacterReferenceBackupView | null>(null)
const busy = ref(false)
const needsReload = ref(false)
const freshForBinding = ref(false)
const error = ref('')
const notice = ref('')
const selectedIdsDraft = ref('')
const seriesDraft = ref('')
const workDraft = ref('')
const mutationReceipt = ref<CharacterReferenceMutationView | null>(null)
let sequence = 0
let controller: AbortController | null = null
let disposed = false

function clearSensitive() {
  sequence += 1
  controller?.abort()
  controller = null
  snapshot.value = null
  loadedRevision.value = null
  draft.value = ''
  confirmed.value = false
  backup.value = null
  busy.value = false
  needsReload.value = false
  freshForBinding.value = false
  error.value = ''
  notice.value = ''
  clearMergeDrafts()
}
watch(() => sessionState.generation, clearSensitive)
watch(() => sessionState.adminAuthenticated, authenticated => { if (!authenticated) clearSensitive() })
onBeforeUnmount(() => { disposed = true; clearSensitive() })

const dirty = computed(() => snapshot.value !== null && draft.value !== snapshot.value.metadata_json)
const ready = computed(() => !disposed && sessionState.adminAuthenticated && !busy.value
  && !needsReload.value && snapshot.value?.configured === true
  && loadedRevision.value === snapshot.value.saved_revision)
const canSave = computed(() => ready.value && confirmed.value && Boolean(draft.value.trim()))
const canImport = canSave
const selectedIds = computed(() => selectedIdsDraft.value.split(/[,，\r\n]+/).map(id => id.trim()).filter(Boolean))
const mergeDirty = computed(() => Boolean(selectedIdsDraft.value || seriesDraft.value || workDraft.value))
const canMerge = computed(() => ready.value && confirmed.value && Boolean(snapshot.value?.saved_revision)
  && selectedIds.value.length >= 2 && selectedIds.value.length <= 1024
  && selectedIds.value.every(id => id.length <= 128)
  && Boolean(seriesDraft.value.trim()) && seriesDraft.value.length <= 128
  && !/[\r\n]/.test(seriesDraft.value)
  && Boolean(workDraft.value.trim()) && workDraft.value.length <= 160)
const canRestore = computed(() => ready.value && !dirty.value && !mergeDirty.value && backup.value !== null
  && snapshot.value?.saved_revision !== null)
const canBind = computed(() => !disposed && sessionState.adminAuthenticated && !busy.value
  && needsReload.value && freshForBinding.value && snapshot.value?.configured === true)

function clearMergeDrafts() {
  selectedIdsDraft.value = ''
  seriesDraft.value = ''
  workDraft.value = ''
  mutationReceipt.value = null
}

function begin() {
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  error.value = ''
  notice.value = ''
  return { sequence: ++sequence, epoch: currentAdminEpoch(), signal: controller.signal }
}
function current(ticket: ReturnType<typeof begin>) {
  return !disposed && ticket.sequence === sequence && !ticket.signal.aborted
    && isCurrentAdminEpoch(ticket.epoch) && sessionState.adminAuthenticated
}
function failed(cause: unknown, ticket: ReturnType<typeof begin>, mutation = false) {
  if (!current(ticket)) return
  if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clearSensitive(); return }
  if (mutation && isApiError(cause) && cause.status === 409) {
    needsReload.value = true
    freshForBinding.value = false
    error.value = '版本或元数据冲突；草稿已保留。请读取当前文档并核对，再明确绑定当前版本。页面不会自动覆盖。'
  } else if (mutation && (!isApiError(cause) || cause.status >= 500)) {
    needsReload.value = true
    freshForBinding.value = false
    error.value = '操作结果未知；草稿与备份回执已保留。请读取当前版本后核对，页面不会自动重发或恢复。'
  } else error.value = apiErrorMessage(cause)
}
async function load() {
  if (disposed || busy.value || !sessionState.adminAuthenticated) return
  const preserve = dirty.value || mergeDirty.value || needsReload.value
  const ticket = begin()
  try {
    const next = await readCharacterReference(ticket.signal)
    if (!current(ticket)) return
    snapshot.value = next
    if (preserve) {
      needsReload.value = true
      freshForBinding.value = true
      confirmed.value = false
      notice.value = '当前已保存文档已读取；草稿仍保留。核对下面的当前文档后，再选择草稿绑定或采用当前文档。'
    } else {
      draft.value = next.metadata_json
      loadedRevision.value = next.saved_revision
      confirmed.value = false
      needsReload.value = false
      freshForBinding.value = false
    }
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
function bindCurrent() {
  if (!canBind.value || !snapshot.value) return
  loadedRevision.value = snapshot.value.saved_revision
  needsReload.value = false
  freshForBinding.value = false
  confirmed.value = false
  notice.value = '草稿已明确绑定刚读取的版本；请重新确认公开来源后保存。'
}
function adoptSaved() {
  if (disposed || busy.value || !sessionState.adminAuthenticated || !snapshot.value
    || (needsReload.value && !freshForBinding.value)) return
  draft.value = snapshot.value.metadata_json
  loadedRevision.value = snapshot.value.saved_revision
  confirmed.value = false
  needsReload.value = false
  freshForBinding.value = false
  clearMergeDrafts()
  notice.value = '已采用当前已保存文档，原草稿已由本次操作替换。'
}
function committed(next: CharacterReferenceStatusView) {
  snapshot.value = next
  draft.value = next.metadata_json
  loadedRevision.value = next.saved_revision
  backup.value = next.backup ?? null
  confirmed.value = false
  needsReload.value = false
  freshForBinding.value = false
  mutationReceipt.value = null
  notice.value = '已保存本地元数据。运行版本未自动重载；请核对版本及备份回执。'
}
function committedMutation(next: CharacterReferenceMutationView) {
  committed(next.status)
  mutationReceipt.value = next
  notice.value = next.changed ? '本次元数据操作已提交；运行版本仍以页面所示版本为准。'
    : '本次元数据内容已存在，未再次写入或生成备份。'
  if (next.committed_revision !== next.status.saved_revision) {
    notice.value += ' 提交后当前状态已被其他写者更新；本次计数来自提交回执，下方文档是随后读回的当前版本。'
  }
}
async function importMetadata() {
  if (!canImport.value) return
  const body = { metadata_json: draft.value, expected_revision: loadedRevision.value,
    public_reference_metadata: true }
  const ticket = begin()
  try {
    const next = await importCharacterReference(body, ticket.signal)
    if (current(ticket)) committedMutation(next)
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function mergeMetadata() {
  if (!canMerge.value || !loadedRevision.value) return
  const body = { character_ids: selectedIds.value, series: seriesDraft.value.trim(),
    work: workDraft.value.trim(), expected_revision: loadedRevision.value, public_reference_metadata: true }
  const ticket = begin()
  try {
    const next = await mergeCharacterReference(body, ticket.signal)
    if (current(ticket)) committedMutation(next)
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function save() {
  if (!canSave.value) return
  const body = { metadata_json: draft.value, expected_revision: loadedRevision.value,
    public_reference_metadata: true }
  const ticket = begin()
  try {
    const next = await saveCharacterReference(body, ticket.signal)
    if (current(ticket)) committed(next)
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function restore() {
  if (!canRestore.value || !backup.value || !snapshot.value?.saved_revision) return
  const body = { backup_name: backup.value.name, backup_revision: backup.value.revision,
    expected_revision: snapshot.value.saved_revision }
  const ticket = begin()
  try {
    const next = await restoreCharacterReference(body, ticket.signal)
    if (current(ticket)) committed(next)
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="角色资源">
    <p class="form-hint">维护当前 bot 的公开、非个人虚构角色元数据。路径由 character_reference_path 配置锁定，本页不接收任意路径或图片。self / friend / known 是角色身份语义，不是真人关系。</p>
    <div class="form-row">
      <n-button :disabled="busy || !sessionState.adminAuthenticated" :loading="busy" @click="void load()">读取当前文档</n-button>
    </div>
    <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
    <n-alert v-if="notice" type="success" :show-icon="false">{{ notice }}</n-alert>
    <n-empty v-if="!sessionState.adminAuthenticated" description="管理员登录后才能读取和编辑角色资源。" />
    <template v-else-if="snapshot">
      <p><n-tag>{{ snapshot.ccip_assembled ? 'CCIP 已装配' : 'CCIP 未装配' }}</n-tag> · <n-tag>{{ snapshot.animetrace_assembled ? 'AnimeTrace 已装配' : 'AnimeTrace 未装配' }}</n-tag></p>
      <p class="form-hint">装配状态只表示配置与本地对象装配，不进行服务健康探测。本页不改变识别开关，也不写外部模型或识别服务 registry。</p>
      <n-alert v-if="!snapshot.configured" type="info" :show-icon="false">尚未配置本 bot 的元数据文件。请到“模型配置”的高级配置设置 ccip_endpoint 与 character_reference_path，再按运行管理核对生效状态。</n-alert>
      <div class="field-stack">
        <label for="character-saved">已保存 SHA</label>
        <n-input id="character-saved" :value="snapshot.saved_revision ?? '尚无已保存包'" readonly />
        <label for="character-running">运行 SHA</label>
        <n-input id="character-running" :value="snapshot.runtime_revision ?? '当前未加载运行包'" readonly />
      </div>
      <n-alert v-if="snapshot.restart_required" type="warning" :show-icon="false">已保存与运行版本不同，待 restart / reload。本页不会执行核心重启或自动重载。</n-alert>
      <template v-if="snapshot.configured">
        <n-alert v-if="needsReload" type="warning" :show-icon="false">提交已暂停；读取当前文档后，核对草稿与当前版本，再明确决定。</n-alert>
        <details v-if="needsReload && freshForBinding">
          <summary>刚读取的当前已保存文档</summary>
          <pre class="style-content">{{ snapshot.metadata_json }}</pre>
        </details>
        <form class="field-stack" @submit.prevent="void save()">
          <label for="character-metadata">完整公开角色元数据 JSON</label>
          <n-input id="character-metadata" v-model:value="draft" type="textarea" :autosize="{ minRows: 12, maxRows: 24 }" :disabled="busy" />
          <p class="form-hint">服务端 owner 统一校验文档；可维护身份、别名、来源、作品、系列与 context。幂等导入只新增不存在的角色；同 ID 不同内容会报冲突。保存或导入均不生成 embedding 或更新图库。</p>
          <n-checkbox v-model:checked="confirmed" :disabled="!ready">我已核对来源，确认仅包含公开或可核验授权的非个人虚构视觉角色元数据。</n-checkbox>
          <div class="form-row">
            <n-button attr-type="submit" type="primary" :disabled="!canSave">保存元数据</n-button>
            <n-button :disabled="!canImport" @click="void importMetadata()">幂等导入元数据</n-button>
            <n-button v-if="canBind" @click="bindCurrent">核对草稿并绑定当前版本</n-button>
            <n-button v-if="dirty || canBind" :disabled="busy || (needsReload && !freshForBinding)" @click="adoptSaved">采用当前已保存文档</n-button>
          </div>
        </form>
        <form class="field-stack" @submit.prevent="void mergeMetadata()">
          <h3>显式合并系列元数据</h3>
          <label for="character-merge-ids">要合并的角色 ID</label>
          <n-input id="character-merge-ids" v-model:value="selectedIdsDraft" type="textarea" :disabled="busy"
            placeholder="至少两个明确角色 ID，用逗号或换行分隔" />
          <label for="character-merge-series">系列 ID</label>
          <n-input id="character-merge-series" v-model:value="seriesDraft" :disabled="busy" :maxlength="128" />
          <label for="character-merge-work">作品名称</label>
          <n-input id="character-merge-work" v-model:value="workDraft" :disabled="busy" :maxlength="160" />
          <p class="form-hint">仅调整所选角色的系列与作品；别名、来源、context 和本 bot 的角色关系保持。合并使用上方公开来源确认与已读取版本，仅保存元数据。</p>
          <n-button attr-type="submit" :disabled="!canMerge">合并所选角色元数据</n-button>
        </form>
        <div v-if="mutationReceipt" class="field-stack">
          <h3>本次{{ mutationReceipt.operation === 'import' ? '导入' : '合并' }}回执 <n-tag>仅元数据</n-tag></h3>
          <p>{{ mutationReceipt.changed ? '有内容变更' : '内容已存在，未写入' }}；新增 {{ mutationReceipt.added_count }} 个，已有 {{ mutationReceipt.existing_count }} 个。</p>
          <label for="character-committed">本次提交 SHA</label>
          <n-input id="character-committed" :value="mutationReceipt.committed_revision" readonly />
          <template v-if="mutationReceipt.input_sha256">
            <label for="character-input-sha">本次输入元数据 SHA</label>
            <n-input id="character-input-sha" :value="mutationReceipt.input_sha256" readonly />
          </template>
          <p class="form-hint">本次所选 ID：{{ mutationReceipt.selected_ids.join('、') }}<template v-if="mutationReceipt.series">；系列：{{ mutationReceipt.series }}</template>。提交 SHA 与当前已保存 SHA 分别展示；图库和外部 registry 不在本次操作中。</p>
        </div>
      </template>
      <div v-if="backup" class="field-stack">
        <h3>本页最新备份回执</h3>
        <p class="form-hint">此回执仅保留在本页，不扫描备份目录。可选中文本复制文件名与 SHA，供之后精确恢复。</p>
        <label for="character-backup">备份文件名与 revision</label>
        <n-input id="character-backup" :value="`${backup.name}\n${backup.revision}`" type="textarea" readonly />
        <n-button :disabled="!canRestore" @click="void restore()">恢复这份最新备份</n-button>
        <p class="form-hint">恢复绑定当前已保存 SHA，并再次备份当前文档；未保存草稿或待核对状态会阻止恢复。</p>
      </div>
    </template>
  </n-card>
</template>

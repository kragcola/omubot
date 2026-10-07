<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NTag } from 'naive-ui'
import { apiErrorMessage, isApiError } from '@/api/client'
import { changeSticker, describeSticker, importSticker, readStickers, updateSticker } from '@/api/stickers'
import type { StickerCatalogView, StickerEntryView, StickerMetadataView, StickerTargetRequest } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const group = ref('')
const snapshot = ref<StickerCatalogView | null>(null)
const selected = ref<StickerEntryView | null>(null)
const stickerId = ref('')
const description = ref('')
const usage = ref('')
const ocr = ref('')
const intents = ref('')
const affects = ref('')
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const needsReload = ref(false)
const error = ref('')
const notice = ref('')
let sequence = 0
let disposed = false
let controller: AbortController | null = null

function clearDraft() {
  selected.value = null
  stickerId.value = ''; description.value = ''; usage.value = ''; ocr.value = ''
  intents.value = ''; affects.value = ''; file.value = null
  if (fileInput.value) fileInput.value.value = ''
}
function clearSensitive() {
  sequence += 1; controller?.abort(); controller = null
  snapshot.value = null; busy.value = false; needsReload.value = false
  error.value = ''; notice.value = ''; clearDraft()
}
watch(group, clearSensitive)
watch(() => sessionState.generation, clearSensitive)
watch(() => sessionState.adminAuthenticated, value => { if (!value) clearSensitive() })
onBeforeUnmount(() => { disposed = true; clearSensitive() })

function select(entry: StickerEntryView) {
  clearDraft(); selected.value = entry; stickerId.value = entry.sticker_id
  description.value = entry.description ?? ''; usage.value = entry.usage_hint ?? ''; ocr.value = entry.ocr_text ?? ''
  intents.value = (entry.intent_tags ?? []).join('，'); affects.value = (entry.affect_tags ?? []).join('，')
  notice.value = ''; error.value = ''
}
const ready = computed(() => sessionState.adminAuthenticated && !busy.value && !needsReload.value
  && snapshot.value !== null && snapshot.value.group_id === group.value)
const editable = computed(() => ready.value && selected.value?.status !== 'revoked')
const preview = computed(() => selected.value && selected.value.status !== 'revoked' && snapshot.value
  ? `/api/admin/stickers/${encodeURIComponent(selected.value.sticker_id)}/image?group_id=${encodeURIComponent(group.value)}&expected_revision=${snapshot.value.revision}` : '')
const tags = (text: string) => text.split(/[,，\r\n]+/).map(value => value.trim()).filter(Boolean)
const metadata = (): StickerMetadataView => ({ description: description.value.trim(), usage_hint: usage.value.trim(),
  ocr_text: ocr.value.trim(), intent_tags: tags(intents.value), affect_tags: tags(affects.value) })

const metadataDirty = computed(() => {
  if (!selected.value) return false
  const entry = selected.value
  return JSON.stringify(metadata()) !== JSON.stringify({ description: entry.description ?? '',
    usage_hint: entry.usage_hint ?? '', ocr_text: entry.ocr_text ?? '',
    intent_tags: entry.intent_tags ?? [], affect_tags: entry.affect_tags ?? [] })
})

function begin() {
  controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''; notice.value = ''
  return { sequence: ++sequence, epoch: currentAdminEpoch(), group: group.value, signal: controller.signal }
}
type Context = ReturnType<typeof begin>
const current = (context: Context) => !disposed && context.sequence === sequence
  && context.group === group.value && isCurrentAdminEpoch(context.epoch)
async function run(operation: (context: Context) => Promise<void>) {
  const context = begin()
  try { await operation(context) }
  catch (cause) {
    if (!current(context)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); return }
    if (isApiError(cause) && cause.status === 403) { clearSensitive(); error.value = '当前群的表情管理权限已不可用。'; return }
    error.value = apiErrorMessage(cause); needsReload.value = true
  }
  finally { if (current(context)) busy.value = false }
}
async function load() {
  if (!group.value || group.value.trim() !== group.value || busy.value) return
  await run(async context => {
    const value = await readStickers(context.group, context.signal)
    if (current(context)) { snapshot.value = value; needsReload.value = false; clearDraft() }
  })
}
function target(context: Context): StickerTargetRequest {
  return { group_id: context.group, operation_id: crypto.randomUUID(), sticker_id: stickerId.value,
    expected_revision: snapshot.value!.revision }
}
async function reloadAfterMutation(context: Context, identifier: string) {
  needsReload.value = true
  const value = await readStickers(context.group, context.signal)
  if (!current(context)) return
  snapshot.value = value; needsReload.value = false
  const entry = value.entries.find(row => row.sticker_id === identifier)
  if (entry) select(entry)
  notice.value = '已保存当前版本。待审资源需明确批准后才会参与回复。'
}
function pickFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (file.value && (file.value.size > 8 * 1024 * 1024
    || !['image/jpeg', 'image/png', 'image/webp'].includes(file.value.type))) {
    file.value = null; error.value = '请选择不超过 8 MiB 的 JPEG、PNG 或 WebP 图片。'
  }
}
function readFile(value: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1]!)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(value)
  })
}
async function importAsset() {
  if (!ready.value || !file.value || selected.value || !stickerId.value) return
  const image = file.value
  await run(async context => {
    const payload = target(context)
    const image_base64 = await readFile(image)
    if (!current(context)) return
    await importSticker({ ...payload, image_base64,
      content_type: image.type as 'image/jpeg' | 'image/png' | 'image/webp', metadata: metadata() }, context.signal)
    if (current(context)) await reloadAfterMutation(context, payload.sticker_id)
  })
}
async function saveMetadata() {
  if (!editable.value || !selected.value) return
  await run(async context => {
    const payload = target(context)
    await updateSticker({ ...payload, metadata: metadata() }, context.signal)
    if (current(context)) await reloadAfterMutation(context, payload.sticker_id)
  })
}
async function mutate(operation: 'approve' | 'revoke') {
  if (!editable.value || !selected.value || (operation === 'approve' && metadataDirty.value)) return
  await run(async context => {
    const payload = target(context)
    await changeSticker(operation, payload, context.signal)
    if (current(context)) await reloadAfterMutation(context, payload.sticker_id)
  })
}
async function describe() {
  if (!editable.value || !selected.value || !snapshot.value?.description_available) return
  await run(async context => {
    const payload = target(context)
    const draft = await describeSticker(payload, context.signal)
    if (!current(context)) return
    description.value = draft.metadata.description ?? ''; usage.value = draft.metadata.usage_hint ?? ''
    ocr.value = draft.metadata.ocr_text ?? ''; intents.value = (draft.metadata.intent_tags ?? []).join('，')
    affects.value = (draft.metadata.affect_tags ?? []).join('，')
    notice.value = '视觉描述已回填为草稿。请核对图片文字与用途，再提交元数据；当前资源尚未因此获批。'
  })
}
</script>

<template>
  <div class="page-stack">
    <n-card class="page-card" :bordered="false">
      <p class="eyebrow">资源</p><h1>表情资源</h1>
      <p class="subtle-text">导入、审核和撤销当前群的图片。管理权限与聊天发送权限分别设置。</p>
      <div class="form-field"><label for="sticker-group">群 ID</label>
        <n-input id="sticker-group" v-model:value="group" :disabled="busy" placeholder="精确群 ID" />
      </div>
      <div class="button-row"><n-button :disabled="!group || busy || !sessionState.adminAuthenticated" @click="load">读取目录</n-button>
        <n-button :disabled="!ready" @click="clearDraft">新导入</n-button></div>
      <p class="subtle-text">管理页使用 web-admin 主体的 sticker.manage 群权限。视觉描述还需要当前运行 vision 模型的图片上传权限。</p>
    </n-card>
    <n-alert v-if="error" type="error" class="page-card">{{ error }} 请重新读取目录核对当前版本。</n-alert>
    <n-alert v-if="notice" type="success" class="page-card">{{ notice }}</n-alert>
    <n-card v-if="snapshot" class="page-card" :bordered="false">
      <h2>目录 · 版本 {{ snapshot.revision }}</h2>
      <n-empty v-if="!snapshot.entries.length" description="当前群还没有表情资源" />
      <ul v-else class="resource-list"><li v-for="entry in snapshot.entries" :key="entry.sticker_id">
        <n-button :disabled="busy || needsReload" @click="select(entry)">{{ entry.sticker_id }}</n-button>
        <n-tag>{{ entry.status === 'approved' ? '已批准' : entry.status === 'pending' ? '待审核' : '已撤销' }}</n-tag>
        <span>{{ entry.description }}</span><span v-if="entry.ocr_text">图上文字：{{ entry.ocr_text }}</span>
      </li></ul>
      <h2>{{ selected ? '编辑资源' : '导入资源' }}</h2>
      <p v-if="selected && metadataDirty" class="subtle-text">当前修改尚未保存。先提交元数据并核对已保存版本，再批准。</p>
      <img v-if="preview" :src="preview" class="sticker-preview" alt="当前选中的表情资源" />
      <div class="form-field"><label for="sticker-id">资源 ID</label>
        <n-input id="sticker-id" v-model:value="stickerId" :disabled="busy || Boolean(selected)" placeholder="稳定且不可重复使用的 ID" />
      </div>
      <div v-if="!selected" class="form-field"><label for="sticker-file">图片文件</label>
        <input id="sticker-file" ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp" :disabled="!ready" @change="pickFile" />
      </div>
      <div class="form-field"><label for="sticker-description">内容描述</label>
        <n-input id="sticker-description" v-model:value="description" :disabled="!editable" :maxlength="512" /></div>
      <div class="form-field"><label for="sticker-usage">适用场景</label>
        <n-input id="sticker-usage" v-model:value="usage" :disabled="!editable" :maxlength="512" /></div>
      <div class="form-field"><label for="sticker-ocr">图上文字（OCR）</label>
        <n-input id="sticker-ocr" v-model:value="ocr" :disabled="!editable" :maxlength="512" /></div>
      <div class="form-field"><label for="sticker-intents">意图标签（逗号分隔）</label>
        <n-input id="sticker-intents" v-model:value="intents" :disabled="!editable" placeholder="closing，greeting，companion" /></div>
      <div class="form-field"><label for="sticker-affects">情绪标签（逗号分隔）</label>
        <n-input id="sticker-affects" v-model:value="affects" :disabled="!editable" /></div>
      <div class="button-row">
        <n-button v-if="!selected" :disabled="!ready || !file || !stickerId" @click="importAsset">导入为待审</n-button>
        <n-button v-if="selected" :disabled="!editable" @click="saveMetadata">提交元数据</n-button>
        <n-button v-if="selected" :disabled="!editable || !snapshot.description_available" @click="describe">请求一次视觉描述</n-button>
        <n-button v-if="selected?.status === 'pending'" :disabled="!editable || metadataDirty" @click="mutate('approve')">批准用于本群</n-button>
        <n-button v-if="selected && selected.status !== 'revoked'" :disabled="!editable" @click="mutate('revoke')">撤销此资源</n-button>
      </div>
      <p class="subtle-text">撤销记录保留，ID 不再复用。视觉描述不会自动保存、批准或发送图片。</p>
    </n-card>
  </div>
</template>

<style scoped>
.resource-list { display: grid; gap: 12px; padding: 0; list-style: none; }
.resource-list li { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.sticker-preview { display: block; max-width: 240px; max-height: 240px; margin: 16px 0; object-fit: contain; }
.button-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
</style>

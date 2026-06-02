<script setup lang="ts">
import { h } from 'vue'
import { NSelect, NImage, NTag, useMessage } from 'naive-ui'
import { ScanOutline, RefreshOutline, AddOutline, ServerOutline, FileTrayFullOutline } from '@vicons/ionicons5'
import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface Character {
  character_id: string
  name: string
  relation: string
  aliases: string[]
  pack?: string
  series?: string
  work?: string
  pack_character_count?: number
  mergeable?: boolean
  has_sample?: boolean
}
interface CacheStats { total?: number, matched?: number }
interface SidecarHealth { status?: string, character_count?: number, pack_count?: number, registry_version?: string, api_version?: string }

const message = useMessage()
const loading = ref(true)
const enabled = ref(false)
const characters = ref<Character[]>([])
const cache = ref<CacheStats>({})
const sidecar = ref<SidecarHealth>({})
const savingId = ref('')

// enrollment dialog state
const showEnroll = ref(false)
const enrolling = ref(false)
const enrollTab = ref<'single' | 'series'>('single')
const seriesMode = ref<'build' | 'merge'>('build')
const formId = ref('')
const formName = ref('')
const formRelation = ref('self')
const formFiles = ref<File[]>([])
const seriesPackName = ref('')
const seriesSlug = ref('')
const seriesWork = ref('')
const seriesRelation = ref('known')
const seriesCharactersJson = ref('[\n  { "character_id": "tenma_tsukasa", "name": "天马司" }\n]')
const seriesFiles = ref<File[]>([])
const mergeCharacterIds = ref<string[]>([])

const relationOptions = [
  { label: 'self（bot 自己）', value: 'self' },
  { label: 'friend（熟人）', value: 'friend' },
  { label: 'known（已知）', value: 'known' },
]

const cacheHitRate = computed(() => {
  const t = cache.value.total || 0
  const m = cache.value.matched || 0
  return t > 0 ? `${Math.round((m / t) * 100)}%` : '—'
})
const sidecarOk = computed(() => sidecar.value.status === 'ok')
const canSubmitSingle = computed(() => Boolean(formId.value.trim() && formName.value.trim() && formFiles.value.length > 0))
const mergeableCharacters = computed(() => characters.value.filter(c => c.mergeable))
const mergeOptions = computed(() => characters.value.map(c => ({
  label: `${c.name || c.character_id} (${c.character_id}) · ${c.pack || '无 pack'}${c.mergeable ? '' : ' · 不可合并'}`,
  value: c.character_id,
  disabled: !c.mergeable,
})))
const selectedMergeCharacters = computed(() => mergeCharacterIds.value
  .map(id => characters.value.find(c => c.character_id === id))
  .filter((c): c is Character => Boolean(c)))
const inferredMergeWork = computed(() => {
  if (selectedMergeCharacters.value.length !== mergeCharacterIds.value.length || mergeCharacterIds.value.length < 2) return ''
  if (!selectedMergeCharacters.value.every(c => c.mergeable && (c.work || '').trim())) return ''
  const works = Array.from(new Set(selectedMergeCharacters.value.map(c => String(c.work || '').trim())))
  return works.length === 1 ? works[0] : ''
})
const parsedSeriesCharacters = computed(() => {
  try {
    const parsed = JSON.parse(seriesCharactersJson.value)
    return Array.isArray(parsed) ? parsed.filter(item => item && typeof item === 'object') : []
  } catch {
    return []
  }
})
const seriesJsonValid = computed(() => {
  try {
    const parsed = JSON.parse(seriesCharactersJson.value)
    return Array.isArray(parsed) && parsed.length > 0
  } catch {
    return false
  }
})
const seriesFileCounts = computed(() => parsedSeriesCharacters.value.map((item: any) => {
  const cid = String(item.character_id || '').trim()
  const prefix = String(item.file_prefix || cid).trim()
  return {
    character_id: cid,
    name: String(item.name || cid).trim() || cid,
    prefix,
    count: seriesFiles.value.filter(file => matchesPrefix(file.name, prefix)).length,
  }
}))
const canSubmitSeriesBuild = computed(() => Boolean(
  seriesPackName.value.trim()
  && seriesJsonValid.value
  && seriesFiles.value.length > 0
  && seriesFileCounts.value.length > 0
  && seriesFileCounts.value.every(item => item.character_id && item.prefix && item.count > 0)
))
const canSubmitSeriesMerge = computed(() => Boolean(
  seriesPackName.value.trim()
  && mergeCharacterIds.value.length >= 2
  && selectedMergeCharacters.value.length === mergeCharacterIds.value.length
  && selectedMergeCharacters.value.every(c => c.mergeable)
  && (seriesWork.value.trim() || inferredMergeWork.value)
))

function sampleUrl(c: Character): string {
  return `/api/admin/characters/${encodeURIComponent(c.character_id)}/sample`
}

async function load() {
  loading.value = true
  try {
    const data = await api('/api/admin/characters')
    enabled.value = !!data.enabled
    characters.value = data.characters || []
    cache.value = data.cache || {}
    sidecar.value = data.sidecar || {}
  } catch (e: any) {
    message.error(`加载失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

async function reload() {
  try {
    const data = await api('/api/admin/characters/reload', { method: 'POST' })
    if (data.error) { message.error(data.error); return }
    message.success(`已重扫：新增 ${data.sync?.inserted ?? 0}、保留 ${data.sync?.skipped ?? 0}`)
    await load()
  } catch (e: any) { message.error(`重扫失败：${e?.message || e}`) }
}

async function saveRelation(c: Character) {
  savingId.value = c.character_id
  try {
    const data = await api(`/api/admin/characters/${encodeURIComponent(c.character_id)}`, {
      method: 'PATCH',
      body: { relation: c.relation, name: c.name },
    })
    if (data.error) { message.error(data.error); return }
    message.success(`已更新 ${c.name}`)
  } catch (e: any) { message.error(`更新失败：${e?.message || e}`) }
  finally { savingId.value = '' }
}

function openEnroll() {
  enrollTab.value = 'single'
  seriesMode.value = 'build'
  formId.value = ''
  formName.value = ''
  formRelation.value = 'self'
  formFiles.value = []
  seriesPackName.value = ''
  seriesSlug.value = ''
  seriesWork.value = ''
  seriesRelation.value = 'known'
  seriesCharactersJson.value = '[\n  { "character_id": "tenma_tsukasa", "name": "天马司" }\n]'
  seriesFiles.value = []
  mergeCharacterIds.value = []
  showEnroll.value = true
}

function appendPickedFiles(target: File[], e: Event) {
  const input = e.target as HTMLInputElement
  const picked = input.files ? Array.from(input.files) : []
  // Accumulate across multiple picks (the native input replaces its FileList
  // each time), deduping by name+size so re-selecting the same batch is a no-op.
  const seen = new Set(target.map(f => `${f.name}:${f.size}`))
  for (const f of picked) {
    const key = `${f.name}:${f.size}`
    if (!seen.has(key)) { target.push(f); seen.add(key) }
  }
  // Reset the input so picking the same file again still fires @change.
  input.value = ''
}

function onFilesPicked(e: Event) {
  appendPickedFiles(formFiles.value, e)
}

function onSeriesFilesPicked(e: Event) {
  appendPickedFiles(seriesFiles.value, e)
}

function removeFile(idx: number) {
  formFiles.value.splice(idx, 1)
}

function removeSeriesFile(idx: number) {
  seriesFiles.value.splice(idx, 1)
}

function clearFiles() {
  formFiles.value = []
}

function clearSeriesFiles() {
  seriesFiles.value = []
}

function matchesPrefix(filename: string, prefix: string): boolean {
  return filename === prefix || ['_', '-', '.'].some(sep => filename.startsWith(`${prefix}${sep}`))
}

async function submitEnroll() {
  if (!canSubmitSingle.value) return
  enrolling.value = true
  try {
    const form = new FormData()
    form.append('character_id', formId.value.trim())
    form.append('name', formName.value.trim())
    form.append('relation', formRelation.value)
    for (const f of formFiles.value) form.append('images', f)
    // Raw fetch, not the shared `api` wrapper: the wrapper forces
    // Content-Type: application/json, which would mislabel this multipart body
    // and make FastAPI reject it with 422.
    const resp = await fetch('/api/admin/characters/build', {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) { message.error(data.detail || `录入失败 (HTTP ${resp.status})`); return }
    if (data.error) { message.error(data.error); return }
    message.success(`已录入 ${data.character_id}：嵌入 ${data.embedded}/${data.total} 张、样例 ${data.samples} 张`)
    showEnroll.value = false
    await load()
  } catch (e: any) { message.error(`录入失败：${e?.message || e}`) }
  finally { enrolling.value = false }
}

async function submitSeriesEnroll() {
  if (!canSubmitSeriesBuild.value) return
  enrolling.value = true
  try {
    const form = new FormData()
    form.append('pack_name', seriesPackName.value.trim())
    form.append('series', seriesSlug.value.trim())
    form.append('work', seriesWork.value.trim())
    form.append('relation_default', seriesRelation.value)
    form.append('characters_json', seriesCharactersJson.value.trim())
    for (const f of seriesFiles.value) form.append('images', f)
    const resp = await fetch('/api/admin/characters/build-series', {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) { message.error(data.detail || `录入失败 (HTTP ${resp.status})`); return }
    if (data.error) { message.error(data.error); return }
    message.success(`已录入 ${data.pack}：${data.character_count} 角色、嵌入 ${data.embedded}/${data.total} 张`)
    showEnroll.value = false
    await load()
  } catch (e: any) { message.error(`录入失败：${e?.message || e}`) }
  finally { enrolling.value = false }
}

async function submitSeriesMerge() {
  if (!canSubmitSeriesMerge.value) return
  enrolling.value = true
  try {
    const data = await api('/api/admin/characters/merge-series', {
      method: 'POST',
      body: {
        pack_name: seriesPackName.value.trim(),
        series: seriesSlug.value.trim(),
        work: seriesWork.value.trim(),
        relation_default: seriesRelation.value,
        character_ids: mergeCharacterIds.value,
      },
    })
    if (data.error) { message.error(data.error); return }
    message.success(`已合并 ${data.pack}：${data.character_count} 角色，归档 ${data.archived} 个旧包`)
    showEnroll.value = false
    await load()
  } catch (e: any) { message.error(`合并失败：${e?.message || e}`) }
  finally { enrolling.value = false }
}

onMounted(load)
</script>

<template>
  <AppPage
    title="角色识别"
    eyebrow="Character Recognition"
    description="管理 CCIP 角色库：relation 关系（self/friend/known）在本地按 bot 维护，嵌入向量留在 sidecar。识别命中后描述会带上角色名。"
  >
    <template #action>
      <NSpace :size="10">
        <NButton type="primary" @click="openEnroll">
          <template #icon><NIcon :component="AddOutline" /></template>
          录入角色
        </NButton>
        <NButton secondary @click="reload">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          重扫角色包
        </NButton>
      </NSpace>
    </template>

    <NSpin :show="loading">
      <EmptyState
        v-if="!loading && !enabled"
        title="角色识别未启用"
        description="config 里 vision.character_recognition.enabled = false。开启后重启 bot，本页才会显示注册角色。"
        :icon="ScanOutline"
      />
      <template v-else>
        <div class="char-metrics">
          <MetricCard title="注册角色" :value="characters.length" :icon="ScanOutline" />
          <MetricCard title="识别缓存命中" :value="cacheHitRate" :hint="`${cache.matched || 0} / ${cache.total || 0}`" :icon="FileTrayFullOutline" accent="info" />
          <MetricCard
            title="Sidecar"
            :value="sidecarOk ? '在线' : (sidecar.status || '未知')"
            :hint="sidecarOk ? `${sidecar.character_count ?? 0} 角色 · ${sidecar.api_version || ''}` : ''"
            :icon="ServerOutline"
            :accent="sidecarOk ? 'success' : 'warning'"
          />
        </div>

        <AppCard bordered elevated class="char-table-card">
          <PageToolbar>
            <template #left><span class="char-toolbar-title">注册角色</span></template>
            <template #right><span class="char-muted">relation 改动即时持久化，重扫角色包不会覆盖</span></template>
          </PageToolbar>
          <NDataTable
            v-if="characters.length"
            :data="characters"
            :bordered="false"
            :single-line="false"
            size="small"
            :columns="[
              {
                title: '样例', key: 'sample', width: 72,
                render: (r) => r.has_sample
                  ? h(NImage, { src: sampleUrl(r), width: 40, height: 40, objectFit: 'cover', style: 'border-radius:8px;' })
                  : h('span', { class: 'char-muted' }, '—'),
              },
              { title: 'character_id', key: 'character_id', width: 170 },
              { title: '名称', key: 'name' },
              {
                title: '出处', key: 'work', minWidth: 180,
                render: (r) => r.work
                  ? h(NTag, { size: 'small', round: true }, { default: () => r.work })
                  : h('span', { class: 'char-muted' }, '—'),
              },
              { title: 'pack', key: 'pack', width: 150, render: (r) => r.pack || '—' },
              { title: '别名', key: 'aliases', render: (r) => (r.aliases || []).join('、') || '—' },
              {
                title: 'relation', key: 'relation', width: 190,
                render: (r) => h(NSelect, {
                  value: r.relation, size: 'small', options: relationOptions,
                  loading: savingId === r.character_id,
                  'onUpdate:value': (v: string) => { r.relation = v; saveRelation(r) },
                }),
              },
            ]"
          />
          <EmptyState
            v-else
            title="还没有注册角色"
            description="点右上角「录入角色」，填角色名 + 关系，拖几张该角色的参考图（5-10 张为佳），系统会自动提取特征并收录。"
            :icon="ScanOutline"
          />
        </AppCard>
      </template>
    </NSpin>

    <NModal
      v-model:show="showEnroll"
      preset="card"
      title="录入角色"
      style="max-width: 720px;"
      :mask-closable="!enrolling"
    >
      <NTabs v-model:value="enrollTab" animated>
        <NTabPane name="single" tab="单角色">
          <NSpace vertical :size="16">
            <div>
              <div class="char-field-label">character_id</div>
              <NInput v-model:value="formId" placeholder="fengxiaomeng" :disabled="enrolling" />
            </div>
            <div>
              <div class="char-field-label">显示名称</div>
              <NInput v-model:value="formName" placeholder="凤笑梦" :disabled="enrolling" />
            </div>
            <div>
              <div class="char-field-label">关系 relation</div>
              <NSelect v-model:value="formRelation" :options="relationOptions" :disabled="enrolling" />
            </div>
            <div>
              <div class="char-field-label">参考图</div>
              <input type="file" accept="image/*" multiple :disabled="enrolling" @change="onFilesPicked" />
              <div v-if="formFiles.length" class="char-files">
                <div class="char-files__head">
                  <span class="char-muted">已选 {{ formFiles.length }} 张</span>
                  <NButton text size="tiny" type="error" :disabled="enrolling" @click="clearFiles">清空</NButton>
                </div>
                <ul class="char-files__list">
                  <li v-for="(f, idx) in formFiles" :key="`${f.name}:${f.size}`" class="char-files__item">
                    <span class="char-files__name">{{ f.name }}</span>
                    <span class="char-muted">{{ (f.size / 1024).toFixed(0) }}KB</span>
                    <button type="button" class="char-files__del" :disabled="enrolling" @click="removeFile(idx)">×</button>
                  </li>
                </ul>
              </div>
            </div>
          </NSpace>
        </NTabPane>
        <NTabPane name="series" tab="系列 pack">
          <NSpace vertical :size="16">
            <NRadioGroup v-model:value="seriesMode" :disabled="enrolling" class="char-series-mode">
              <NRadioButton value="build">从新图片生成</NRadioButton>
              <NRadioButton value="merge">合并已有角色</NRadioButton>
            </NRadioGroup>
            <div class="char-series-grid">
              <div>
                <div class="char-field-label">pack_name</div>
                <NInput v-model:value="seriesPackName" placeholder="project_sekai" :disabled="enrolling" />
              </div>
              <div>
                <div class="char-field-label">series</div>
                <NInput v-model:value="seriesSlug" placeholder="默认同 pack_name" :disabled="enrolling" />
              </div>
            </div>
            <div>
              <div class="char-field-label">work</div>
              <NInput v-model:value="seriesWork" placeholder="プロジェクトセカイ カラフルステージ！" :disabled="enrolling" />
            </div>
            <div>
              <div class="char-field-label">默认关系 relation_default</div>
              <NSelect v-model:value="seriesRelation" :options="relationOptions" :disabled="enrolling" />
            </div>
            <template v-if="seriesMode === 'build'">
            <div>
              <div class="char-field-label">characters JSON</div>
              <NInput
                v-model:value="seriesCharactersJson"
                type="textarea"
                :autosize="{ minRows: 5, maxRows: 9 }"
                :status="seriesJsonValid ? undefined : 'warning'"
                :disabled="enrolling"
              />
            </div>
            <div>
              <div class="char-field-label">参考图</div>
              <input type="file" accept="image/*" multiple :disabled="enrolling" @change="onSeriesFilesPicked" />
              <div v-if="seriesFileCounts.length" class="char-prefix-grid">
                <div v-for="item in seriesFileCounts" :key="item.character_id" class="char-prefix-row">
                  <span>{{ item.name }}</span>
                  <span class="char-muted">{{ item.prefix }}</span>
                  <NTag size="small" round :type="item.count > 0 ? 'success' : 'warning'">{{ item.count }} 张</NTag>
                </div>
              </div>
              <div v-if="seriesFiles.length" class="char-files">
                <div class="char-files__head">
                  <span class="char-muted">已选 {{ seriesFiles.length }} 张</span>
                  <NButton text size="tiny" type="error" :disabled="enrolling" @click="clearSeriesFiles">清空</NButton>
                </div>
                <ul class="char-files__list">
                  <li v-for="(f, idx) in seriesFiles" :key="`${f.name}:${f.size}`" class="char-files__item">
                    <span class="char-files__name">{{ f.name }}</span>
                    <span class="char-muted">{{ (f.size / 1024).toFixed(0) }}KB</span>
                    <button type="button" class="char-files__del" :disabled="enrolling" @click="removeSeriesFile(idx)">×</button>
                  </li>
                </ul>
              </div>
            </div>
            </template>
            <template v-else>
              <div>
                <div class="char-field-label">选择已有角色</div>
                <NSelect
                  v-model:value="mergeCharacterIds"
                  multiple
                  filterable
                  :options="mergeOptions"
                  :disabled="enrolling || mergeableCharacters.length === 0"
                  placeholder="选择可合并的单角色 pack"
                />
              </div>
              <EmptyState
                v-if="mergeableCharacters.length === 0"
                title="暂无可合并单角色包"
                description="当前角色都已在系列 pack 中，或缺少完整 embeddings.npz。"
                :icon="FileTrayFullOutline"
                compact
              />
              <div v-if="selectedMergeCharacters.length" class="char-merge-grid">
                <div v-for="item in selectedMergeCharacters" :key="item.character_id" class="char-merge-row">
                  <span>{{ item.name }}</span>
                  <span class="char-muted">{{ item.character_id }}</span>
                  <NTag size="small" round>{{ item.pack || '—' }}</NTag>
                  <NTag v-if="item.work" size="small" round type="info">{{ item.work }}</NTag>
                  <span v-else class="char-muted">work —</span>
                </div>
                <div class="char-merge-row char-merge-row--summary">
                  <span>共同出处</span>
                  <span class="char-muted">{{ seriesWork.trim() || inferredMergeWork || '需填写 work' }}</span>
                </div>
              </div>
            </template>
          </NSpace>
        </NTabPane>
      </NTabs>
      <template #footer>
        <NSpace justify="end">
          <NButton :disabled="enrolling" @click="showEnroll = false">取消</NButton>
          <NButton
            v-if="enrollTab === 'single'"
            type="primary"
            :loading="enrolling"
            :disabled="!canSubmitSingle"
            @click="submitEnroll"
          >
            提取特征并录入
          </NButton>
          <NButton
            v-else-if="seriesMode === 'build'"
            type="primary"
            :loading="enrolling"
            :disabled="!canSubmitSeriesBuild"
            @click="submitSeriesEnroll"
          >
            生成系列 pack
          </NButton>
          <NButton
            v-else
            type="primary"
            :loading="enrolling"
            :disabled="!canSubmitSeriesMerge"
            @click="submitSeriesMerge"
          >
            合并为系列 pack
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </AppPage>
</template>

<script lang="ts">
export default { name: 'CharactersView' }
</script>

<style scoped>
.char-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.char-table-card { padding: 16px; }
.char-toolbar-title { font-size: 15px; font-weight: 700; color: var(--om-text-1); }
.char-muted { color: var(--om-text-3); font-size: 12px; }
.char-field-label { font-size: 12px; font-weight: 600; color: var(--om-text-2); margin-bottom: 8px; }
.char-files { margin-top: 8px; border: 1px solid var(--om-border); border-radius: 8px; padding: 8px 12px; }
.char-files__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.char-files__list { list-style: none; margin: 0; padding: 0; max-height: 160px; overflow-y: auto; }
.char-files__item { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
.char-files__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--om-text-1); }
.char-files__del { appearance: none; border: 0; background: transparent; color: var(--om-text-3); cursor: pointer; font-size: 16px; line-height: 1; padding: 0 4px; }
.char-files__del:hover { color: var(--om-danger); }
.char-files__del:disabled { opacity: 0.4; cursor: not-allowed; }
.char-series-mode { width: 100%; }
.char-series-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.char-prefix-grid {
  display: grid;
  gap: 8px;
  margin-top: 8px;
  padding: 8px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
}
.char-prefix-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--om-text-1);
}
.char-merge-grid {
  display: grid;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
}
.char-merge-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto auto;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--om-text-1);
}
.char-merge-row--summary {
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  padding-top: 8px;
  border-top: 1px solid var(--om-border);
  font-weight: 600;
}
@media (max-width: 720px) {
  .char-series-grid,
  .char-prefix-row,
  .char-merge-row,
  .char-merge-row--summary {
    grid-template-columns: 1fr;
  }
}
</style>

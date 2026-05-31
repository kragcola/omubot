<script setup lang="ts">
import { h } from 'vue'
import { NSelect, useMessage } from 'naive-ui'
import { ScanOutline, RefreshOutline, CloudUploadOutline, ServerOutline, FileTrayFullOutline } from '@vicons/ionicons5'
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
}
interface CacheStats { total?: number, matched?: number }
interface SidecarHealth { status?: string, character_count?: number, pack_count?: number, registry_version?: string, api_version?: string }

const message = useMessage()
const loading = ref(true)
const enabled = ref(false)
const characters = ref<Character[]>([])
const cache = ref<CacheStats>({})
const sidecar = ref<SidecarHealth>({})
const uploading = ref(false)
const savingId = ref('')

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

async function onUpload(options: { file: { file?: File | null } }) {
  const file = options.file.file
  if (!file) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const data = await api('/api/admin/characters/upload', { method: 'POST', body: form })
    if (data.error) { message.error(data.error); return }
    message.success(`上传成功：新增 ${data.sync?.inserted ?? 0}`)
    await load()
  } catch (e: any) { message.error(`上传失败：${e?.message || e}`) }
  finally { uploading.value = false }
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
        <NUpload :show-file-list="false" accept=".zip" :custom-request="onUpload as any">
          <NButton secondary :loading="uploading">
            <template #icon><NIcon :component="CloudUploadOutline" /></template>
            上传 charpack(.zip)
          </NButton>
        </NUpload>
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
              { title: 'character_id', key: 'character_id', width: 180 },
              { title: '名称', key: 'name' },
              { title: '别名', key: 'aliases', render: (r) => (r.aliases || []).join('、') || '—' },
              {
                title: 'relation', key: 'relation', width: 200,
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
            description="用 tools/build_character_pack.py 生成 .charpack 丢进 config/character_packs/，或上传 .zip，然后点「重扫角色包」。"
            :icon="ScanOutline"
          />
        </AppCard>
      </template>
    </NSpin>
  </AppPage>
</template>

<script lang="ts">
export default { name: 'CharactersView' }
</script>

<style scoped>
.char-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}
.char-table-card { padding: 16px 18px; }
.char-toolbar-title { font-size: 15px; font-weight: 700; color: var(--om-text-1); }
.char-muted { color: var(--om-text-3); font-size: 12px; }
</style>
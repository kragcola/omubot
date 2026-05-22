<script setup lang="ts">
import {
  AlbumsOutline,
  ImageOutline,
  RefreshOutline,
  SearchOutline,
  SparklesOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import {
  NButton,
  NIcon,
  NInput,
  NPagination,
  NPopconfirm,
  NSkeleton,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppDrawerHeader from '../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface Sticker {
  id: string
  description: string
  usage_hint: string
  send_count: number
  source: string
}

const stickers = ref<Sticker[]>([])
const loading = ref(true)
const refreshing = ref(false)
const searchText = ref('')
const currentPage = ref(1)
const stickersPerPage = 36

const selected = ref<Sticker | null>(null)
const drawerVisible = ref(false)
const editDesc = ref('')
const editHint = ref('')
const saving = ref(false)

const message = useMessage()

const filteredStickers = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  if (!query) return stickers.value

  return stickers.value.filter(sticker =>
    [sticker.id, sticker.description, sticker.usage_hint, sticker.source]
      .join(' ')
      .toLowerCase()
      .includes(query),
  )
})

const pageCount = computed(() => Math.max(1, Math.ceil(filteredStickers.value.length / stickersPerPage)))
const shouldPaginate = computed(() => filteredStickers.value.length > stickersPerPage)
const pagedStickers = computed(() => {
  const start = (currentPage.value - 1) * stickersPerPage
  return filteredStickers.value.slice(start, start + stickersPerPage)
})

const describedCount = computed(() => stickers.value.filter(sticker => Boolean(sticker.description?.trim())).length)
const hintedCount = computed(() => stickers.value.filter(sticker => Boolean(sticker.usage_hint?.trim())).length)
const totalUsage = computed(() => stickers.value.reduce((sum, sticker) => sum + Number(sticker.send_count || 0), 0))

onMounted(() => {
  void loadStickers()
})

watch(filteredStickers, () => {
  currentPage.value = 1
})

watch(pageCount, (count) => {
  if (currentPage.value > count) {
    currentPage.value = count
  }
})

function imgUrl(id: string) {
  return `/api/admin/stickers/${id}/image`
}

async function loadStickers(silent = false) {
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api('/api/admin/stickers')
    stickers.value = data.stickers || []
  } catch (error) {
    console.error('Failed to load stickers:', error)
    stickers.value = []
    message.error('表情包列表加载失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function openDetail(sticker: Sticker) {
  selected.value = sticker
  editDesc.value = sticker.description || ''
  editHint.value = sticker.usage_hint || ''
  drawerVisible.value = true
}

async function save() {
  if (!selected.value) return

  saving.value = true
  try {
    const data = await api(`/api/admin/stickers/${selected.value.id}`, {
      method: 'PATCH',
      body: {
        description: editDesc.value,
        usage_hint: editHint.value,
      },
    })

    if (!data.ok) {
      message.error(data.error || '更新失败')
      return
    }

    selected.value = {
      ...selected.value,
      description: editDesc.value,
      usage_hint: editHint.value,
    }
    stickers.value = stickers.value.map(sticker => sticker.id === selected.value?.id
      ? {
          ...sticker,
          description: editDesc.value,
          usage_hint: editHint.value,
        }
      : sticker)

    message.success('已更新')
    drawerVisible.value = false
  } catch (error) {
    console.error('Failed to update sticker:', error)
    message.error('更新失败')
  } finally {
    saving.value = false
  }
}

async function remove(id: string) {
  try {
    const data = await api(`/api/admin/stickers/${id}`, { method: 'DELETE' })
    if (!data.ok) {
      message.error(data.error || '删除失败')
      return
    }

    stickers.value = stickers.value.filter(sticker => sticker.id !== id)
    if (selected.value?.id === id) drawerVisible.value = false
    message.success('已删除')
  } catch (error) {
    console.error('Failed to delete sticker:', error)
    message.error('删除失败')
  }
}

function resetFilters() {
  searchText.value = ''
}
</script>

<template>
  <AppPage
    title="表情包"
    eyebrow="Sticker Library"
    description="浏览表情素材、使用频次和提示文案，统一整理贴图资产与调用说明。"
  >
    <template #action>
      <NButton secondary :loading="refreshing" @click="loadStickers(true)">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新图库
      </NButton>
    </template>

    <div class="stickers-metric-grid">
      <MetricCard
        title="素材总数"
        :value="stickers.length"
        hint="当前贴图库中可管理的表情素材数"
        :icon="AlbumsOutline"
        accent="primary"
      />
      <MetricCard
        title="已写描述"
        :value="describedCount"
        hint="拥有展示描述的素材数量"
        :icon="ImageOutline"
        accent="success"
      />
      <MetricCard
        title="已写提示"
        :value="hintedCount"
        hint="拥有 usage hint 的素材数量"
        :icon="SparklesOutline"
        accent="info"
      />
      <MetricCard
        title="累计发送"
        :value="totalUsage"
        hint="当前素材库累计发送次数"
        :icon="TrashOutline"
        accent="warning"
      />
    </div>

    <PageToolbar class="mb-16">
      <template #left>
        <NInput
          v-model:value="searchText"
          clearable
          placeholder="搜索 ID、描述、提示或来源"
          class="stickers-toolbar__search"
        >
          <template #prefix>
            <NIcon :component="SearchOutline" />
          </template>
        </NInput>
      </template>

      <template #right>
        <NButton secondary @click="resetFilters">
          重置
        </NButton>
        <NTag round size="small">
          当前 {{ filteredStickers.length }} / {{ stickers.length }} 个素材
        </NTag>
      </template>
    </PageToolbar>

    <NSkeleton v-if="loading" :repeat="8" text />

    <template v-else>
      <EmptyState
        v-if="stickers.length === 0"
        title="当前还没有表情素材"
        description="贴图库里暂时没有可展示的图片资源。"
        :icon="ImageOutline"
      />

      <EmptyState
        v-else-if="filteredStickers.length === 0"
        compact
        title="没有匹配的素材"
        description="尝试清空搜索条件，或换一个关键词重新查找。"
        :icon="SearchOutline"
      />

      <div v-else class="stickers-grid">
        <div v-if="shouldPaginate" class="stickers-pagination">
          <NPagination
            v-model:page="currentPage"
            :page-count="pageCount"
            :page-slot="8"
            show-quick-jumper
          />
        </div>

        <AppCard
          v-for="sticker in pagedStickers"
          :key="sticker.id"
          bordered
          elevated
          interactive
          class="sticker-card"
          @click="openDetail(sticker)"
        >
          <div class="sticker-card__preview">
            <img
              :src="imgUrl(sticker.id)"
              :alt="sticker.description || sticker.id"
              loading="lazy"
              class="sticker-card__image"
            >
          </div>

          <div class="sticker-card__body">
            <div class="sticker-card__head">
              <strong class="sticker-card__title">
                {{ sticker.description || sticker.id }}
              </strong>
              <NTag size="small" round>
                {{ sticker.send_count }} 次
              </NTag>
            </div>

            <p class="sticker-card__id">
              {{ sticker.id }}
            </p>

            <p class="sticker-card__hint">
              {{ sticker.usage_hint || '还没有编写使用提示。' }}
            </p>
          </div>
        </AppCard>

        <div v-if="shouldPaginate" class="stickers-pagination stickers-pagination--bottom">
          <NPagination
            v-model:page="currentPage"
            :page-count="pageCount"
            :page-slot="8"
            show-quick-jumper
          />
        </div>
      </div>
    </template>

    <NDrawer v-model:show="drawerVisible" :width="540">
      <NDrawerContent closable>
        <template #header>
          <AppDrawerHeader
            eyebrow="Sticker Detail"
            :title="selected?.description || selected?.id || '表情包详情'"
            :description="selected ? `素材 ID ${selected.id}` : ''"
          >
            <template v-if="selected" #aside>
              <NTag round size="small" type="info">
                发送 {{ selected.send_count }} 次
              </NTag>
            </template>
          </AppDrawerHeader>
        </template>

        <template v-if="selected">
          <AppDrawerLayout class="stickers-detail">
            <AppPanelSection eyebrow="Preview" title="素材预览">
              <div class="stickers-detail__preview">
                <img
                  :src="imgUrl(selected.id)"
                  :alt="selected.description || selected.id"
                  class="stickers-detail__image"
                >
              </div>
            </AppPanelSection>

            <AppPanelSection eyebrow="Snapshot" title="素材信息">
              <div class="stickers-detail__stats">
                <div class="stickers-detail__stat">
                  <span>素材 ID</span>
                  <strong>{{ selected.id }}</strong>
                </div>
                <div class="stickers-detail__stat">
                  <span>来源</span>
                  <strong>{{ selected.source || '--' }}</strong>
                </div>
              </div>
            </AppPanelSection>

            <AppPanelSection eyebrow="Metadata" title="描述与提示">
              <div class="stickers-detail__form-grid">
                <label class="stickers-detail__field stickers-detail__field--full">
                  <span>描述</span>
                  <NInput v-model:value="editDesc" placeholder="说明这张表情图表达的意思" />
                </label>
                <label class="stickers-detail__field stickers-detail__field--full">
                  <span>使用提示</span>
                  <NInput
                    v-model:value="editHint"
                    type="textarea"
                    :autosize="{ minRows: 3, maxRows: 6 }"
                    placeholder="记录适合触发这张表情的语境"
                  />
                </label>
              </div>
            </AppPanelSection>

            <template #footer>
              <NPopconfirm @positive-click="remove(selected.id)">
                <template #trigger>
                  <NButton type="error" secondary>
                    删除素材
                  </NButton>
                </template>
                确认删除此表情包？
              </NPopconfirm>
              <div class="stickers-detail__footer-actions">
                <NButton secondary @click="drawerVisible = false">
                  取消
                </NButton>
                <NButton type="primary" :loading="saving" @click="save">
                  保存修改
                </NButton>
              </div>
            </template>
          </AppDrawerLayout>
        </template>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.stickers-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stickers-toolbar__search {
  width: min(300px, 100%);
}

.stickers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}

.stickers-pagination {
  grid-column: 1 / -1;
  display: flex;
  justify-content: center;
  padding: 6px 0 2px;
}

.stickers-pagination--bottom {
  padding: 4px 0 8px;
}

.sticker-card {
  overflow: hidden;
  padding: 0;
  cursor: pointer;
}

.sticker-card__preview {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1;
  padding: 18px;
  background: var(--om-surface-2);
}

.sticker-card__image,
.stickers-detail__image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.sticker-card__body {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.sticker-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.sticker-card__title {
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.sticker-card__id {
  margin: 0;
  color: var(--om-text-3);
  font-size: 12px;
  word-break: break-all;
}

.sticker-card__hint {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.stickers-detail {
  display: grid;
  gap: 14px;
}

.stickers-detail__preview {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 260px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 70%, transparent);
  overflow: hidden;
}

.stickers-detail__stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.stickers-detail__stat {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.stickers-detail__stat span,
.stickers-detail__field span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.stickers-detail__stat strong {
  display: block;
  margin-top: 8px;
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 14px;
  line-height: 1.6;
}

.stickers-detail__form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.stickers-detail__field {
  display: grid;
  gap: 8px;
}

.stickers-detail__field--full {
  grid-column: 1 / -1;
}

.stickers-detail__footer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

@media (max-width: 1100px) {
  .stickers-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .stickers-metric-grid,
  .stickers-detail__stats,
  .stickers-detail__form-grid {
    grid-template-columns: 1fr;
  }

  .stickers-detail__footer-actions {
    width: 100%;
    flex-direction: column;
  }
}
</style>

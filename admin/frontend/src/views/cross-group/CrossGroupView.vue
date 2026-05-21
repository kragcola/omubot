<script setup lang="ts">
import {
  GlobeOutline,
  RefreshOutline,
  EyeOffOutline,
  TimeOutline,
  TelescopeOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppCard from '../../components/common/AppCard.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

type StoreKey =
  | 'all'
  | 'slang'
  | 'style'
  | 'episode'
  | 'normalizer'
  | 'graph_fact'
  | 'graph_candidate'

interface CrossGroupItem {
  store: Exclude<StoreKey, 'all'>
  item_id: string
  label: string
  detail: string
  scope: string
  group_id: string
  status: string
  confidence: number | null
  enabled_by: string
  enabled_at: string
  enabled_for_groups: string[]
  enabled_reason: string
}

interface TimelineEntry {
  store: Exclude<StoreKey, 'all'>
  item_id: string
  action: string
  actor: string
  reason: string
  created_at: string
  label: string
}

interface SimulateTerm {
  term_id: string
  term: string
  group_id: string
}

const STORE_LABEL: Record<Exclude<StoreKey, 'all'>, string> = {
  slang: '黑话',
  style: '语气',
  episode: '经验反思',
  normalizer: '归一化簇',
  graph_fact: '图谱事实',
  graph_candidate: '图谱候选',
}

const STORE_TAGS: { key: StoreKey, label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'slang', label: STORE_LABEL.slang },
  { key: 'style', label: STORE_LABEL.style },
  { key: 'episode', label: STORE_LABEL.episode },
  { key: 'normalizer', label: STORE_LABEL.normalizer },
  { key: 'graph_fact', label: STORE_LABEL.graph_fact },
  { key: 'graph_candidate', label: STORE_LABEL.graph_candidate },
]

const message = useMessage()

const loading = ref(false)
const items = ref<CrossGroupItem[]>([])
const activeStore = ref<StoreKey>('all')
const searchKeyword = ref('')

const disableTarget = ref<CrossGroupItem | null>(null)
const disableReason = ref('')
const disableSubmitting = ref(false)
const showDisableDialog = computed({
  get: () => disableTarget.value !== null,
  set: (val: boolean) => {
    if (!val) {
      disableTarget.value = null
      disableReason.value = ''
    }
  },
})

const timeline = ref<TimelineEntry[]>([])
const timelineLoading = ref(false)

const simulateGroupId = ref('')
const simulateLoading = ref(false)
const simulateResults = ref<SimulateTerm[]>([])
const simulateRan = ref(false)

const filteredItems = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  return items.value.filter((it) => {
    if (activeStore.value !== 'all' && it.store !== activeStore.value) return false
    if (!kw) return true
    return (
      it.label.toLowerCase().includes(kw)
      || it.detail.toLowerCase().includes(kw)
      || it.group_id.toLowerCase().includes(kw)
      || it.enabled_by.toLowerCase().includes(kw)
      || it.enabled_reason.toLowerCase().includes(kw)
    )
  })
})

const totalsByStore = computed(() => {
  const counts: Record<Exclude<StoreKey, 'all'>, number> = {
    slang: 0, style: 0, episode: 0, normalizer: 0, graph_fact: 0, graph_candidate: 0,
  }
  for (const it of items.value) {
    counts[it.store] = (counts[it.store] || 0) + 1
  }
  return counts
})

const tabBadge = computed(() => (key: StoreKey) => {
  if (key === 'all') return items.value.length
  return totalsByStore.value[key] || 0
})

async function fetchItems() {
  loading.value = true
  try {
    const res = await api<{ ok: boolean, items: CrossGroupItem[] }>('/api/admin/cross-group/items')
    items.value = (res.items || []).map((it) => ({
      ...it,
      enabled_for_groups: Array.isArray(it.enabled_for_groups) ? it.enabled_for_groups : [],
      enabled_reason: it.enabled_reason || '',
      confidence: it.confidence ?? null,
    }))
  }
  catch (e: any) {
    message.error(e?.data?.error || '加载失败')
  }
  finally {
    loading.value = false
  }
}

async function fetchTimeline() {
  timelineLoading.value = true
  try {
    const res = await api<{ ok: boolean, entries: TimelineEntry[] }>('/api/admin/cross-group/timeline')
    timeline.value = res.entries || []
  }
  catch {
    timeline.value = []
  }
  finally {
    timelineLoading.value = false
  }
}

function refreshAll() {
  fetchItems()
  fetchTimeline()
}

function disableConfirm(item: CrossGroupItem) {
  disableTarget.value = item
  disableReason.value = ''
}

async function submitDisable() {
  if (!disableTarget.value) return
  const target = disableTarget.value
  disableSubmitting.value = true
  try {
    await api('/api/admin/cross-group/enable', {
      method: 'POST',
      body: {
        store: target.store,
        item_id: target.item_id,
        visible: false,
        reason: disableReason.value.trim() || 'admin disable from console',
      },
    })
    message.success('已禁用跨群可见')
    showDisableDialog.value = false
    await fetchItems()
    await fetchTimeline()
  }
  catch (e: any) {
    message.error(e?.data?.error || '操作失败')
  }
  finally {
    disableSubmitting.value = false
  }
}

async function simulate() {
  const gid = simulateGroupId.value.trim()
  if (!gid) {
    message.warning('请输入群 ID')
    return
  }
  simulateLoading.value = true
  simulateRan.value = true
  try {
    const res = await api<{ ok: boolean, cross_group_terms: SimulateTerm[] }>(
      '/api/admin/cross-group/simulate',
      { method: 'POST', body: { group_id: gid } },
    )
    simulateResults.value = res.cross_group_terms || []
  }
  catch (e: any) {
    message.error(e?.data?.error || '模拟失败')
    simulateResults.value = []
  }
  finally {
    simulateLoading.value = false
  }
}

const STATUS_TAG: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  approved: 'success',
  enabled_for_prompt: 'success',
  candidate: 'info',
  dry_run: 'default',
  pending: 'info',
  disabled: 'error',
  expired: 'error',
  rejected: 'error',
}

const itemColumns = computed<DataTableColumns<CrossGroupItem>>(() => [
  {
    title: '来源',
    key: 'store',
    width: 96,
    render: (row) => h(resolveComponent('NTag') as any, { size: 'small', round: true, type: 'default' }, () => STORE_LABEL[row.store]),
  },
  { title: '内容', key: 'label', minWidth: 220, ellipsis: { tooltip: true } },
  { title: '细节', key: 'detail', minWidth: 240, ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 130,
    render: (row) => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: STATUS_TAG[row.status] || 'default' },
      () => row.status || '-',
    ),
  },
  { title: '来源群', key: 'group_id', width: 130, ellipsis: { tooltip: true } },
  {
    title: '可见范围',
    key: 'enabled_for_groups',
    width: 160,
    render: (row) => {
      if (!row.enabled_for_groups || row.enabled_for_groups.length === 0) {
        return h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '所有群')
      }
      return h(
        resolveComponent('NSpace') as any,
        { size: 4, wrap: true },
        () => row.enabled_for_groups.map((g) => h(
          resolveComponent('NTag') as any,
          { size: 'tiny', round: true, type: 'info' },
          () => g,
        )),
      )
    },
  },
  {
    title: '理由',
    key: 'enabled_reason',
    minWidth: 200,
    ellipsis: { tooltip: true },
    render: (row) => row.enabled_reason
      ? row.enabled_reason
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
  { title: '启用者', key: 'enabled_by', width: 110 },
  { title: '启用时间', key: 'enabled_at', width: 170 },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    fixed: 'right',
    render: row => h(
      resolveComponent('NButton') as any,
      {
        size: 'tiny',
        quaternary: true,
        type: 'error',
        onClick: () => disableConfirm(row),
      },
      {
        default: () => '禁用',
        icon: () => h(resolveComponent('NIcon') as any, { component: EyeOffOutline }),
      },
    ),
  },
])

const timelineColumns = computed<DataTableColumns<TimelineEntry>>(() => [
  { title: '时间', key: 'created_at', width: 170 },
  {
    title: '操作',
    key: 'action',
    width: 130,
    render: (row) => {
      const enable = row.action === 'cross_group_enable'
      return h(
        resolveComponent('NTag') as any,
        { size: 'small', type: enable ? 'success' : 'warning' },
        () => enable ? '启用' : '禁用',
      )
    },
  },
  {
    title: '来源',
    key: 'store',
    width: 110,
    render: row => STORE_LABEL[row.store] || row.store,
  },
  { title: '内容', key: 'label', minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: '理由',
    key: 'reason',
    minWidth: 200,
    ellipsis: { tooltip: true },
    render: row => row.reason
      ? row.reason
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
  { title: '操作者', key: 'actor', width: 110 },
])

onMounted(() => refreshAll())
</script>

<template>
  <AppPage
    title="跨群可见"
    eyebrow="Cross-Group Visibility"
    description="审计被显式开启跨群可见的黑话、语气、经验、归一化簇与图谱条目，可一键禁用并查看启用理由。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag size="small" round :type="loading ? 'default' : 'success'">
          {{ loading ? '同步中…' : `共 ${items.length} 项` }}
        </NTag>
        <NButton secondary :loading="loading" @click="refreshAll">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
      </NSpace>
    </template>

    <div class="cg-metrics">
      <MetricCard
        title="跨群条目总数"
        :value="items.length"
        hint="所有 store 中 cross_group_visible = 1 的总和"
        :icon="GlobeOutline"
        accent="primary"
      />
      <MetricCard
        title="黑话 / 语气"
        :value="totalsByStore.slang + totalsByStore.style"
        :hint="`黑话 ${totalsByStore.slang} · 语气 ${totalsByStore.style}`"
        accent="info"
      />
      <MetricCard
        title="经验 / 归一化"
        :value="totalsByStore.episode + totalsByStore.normalizer"
        :hint="`经验 ${totalsByStore.episode} · 归一化 ${totalsByStore.normalizer}`"
        accent="warning"
      />
      <MetricCard
        title="图谱条目"
        :value="totalsByStore.graph_fact + totalsByStore.graph_candidate"
        :hint="`事实 ${totalsByStore.graph_fact} · 候选 ${totalsByStore.graph_candidate}`"
        accent="success"
      />
    </div>

    <AppCard bordered class="cg-section">
      <PageToolbar class="cg-toolbar">
        <template #left>
          <NTabs
            v-model:value="activeStore"
            type="segment"
            size="small"
            class="cg-tabs"
          >
            <NTabPane
              v-for="tab in STORE_TAGS"
              :key="tab.key"
              :name="tab.key"
              :tab="`${tab.label}${tabBadge(tab.key) > 0 ? ` · ${tabBadge(tab.key)}` : ''}`"
            />
          </NTabs>
        </template>
        <template #right>
          <NInput
            v-model:value="searchKeyword"
            placeholder="搜索内容 / 群 / 启用者 / 理由"
            clearable
            size="small"
            style="width: 260px"
          />
        </template>
      </PageToolbar>

      <NDataTable
        v-if="filteredItems.length > 0"
        :columns="itemColumns"
        :data="filteredItems"
        :loading="loading"
        :bordered="false"
        size="small"
        :scroll-x="1400"
        :pagination="{ pageSize: 20 }"
        :row-key="(row: CrossGroupItem) => `${row.store}:${row.item_id}`"
      />
      <EmptyState
        v-else
        title="当前没有跨群可见条目"
        description="任何被 admin 开启跨群可见的项目会出现在这里。所有变更都会记录到修订历史。"
        :icon="GlobeOutline"
      />
    </AppCard>

    <div class="cg-grid">
      <AppCard bordered class="cg-section">
        <header class="cg-section__head">
          <div class="cg-section__title">
            <NIcon :component="TelescopeOutline" :size="18" />
            <span>模拟视角</span>
          </div>
          <p class="cg-section__hint">
            填入群 ID，预览该群当前可注入的跨群黑话（不含 global / 本群）。
          </p>
        </header>

        <NSpace align="center" :size="12" class="cg-simulate">
          <NInput
            v-model:value="simulateGroupId"
            placeholder="例如 123456789"
            size="small"
            clearable
            style="width: 220px"
            @keyup.enter="simulate"
          />
          <NButton type="primary" size="small" :loading="simulateLoading" @click="simulate">
            模拟查看
          </NButton>
        </NSpace>

        <NDataTable
          v-if="simulateResults.length > 0"
          :columns="[
            { title: '黑话', key: 'term', minWidth: 140 },
            { title: '来源群', key: 'group_id', width: 140 },
            { title: 'ID', key: 'term_id', width: 200 },
          ]"
          :data="simulateResults"
          :bordered="false"
          size="small"
        />
        <EmptyState
          v-else-if="simulateRan && !simulateLoading"
          title="该群无跨群可见黑话"
          description="未命中任何来自其他群的 cross_group_visible 黑话。"
          :icon="TelescopeOutline"
        />
        <p v-else class="cg-section__placeholder">
          模拟结果会展示当前群可注入的跨群黑话。
        </p>
      </AppCard>

      <AppCard bordered class="cg-section">
        <header class="cg-section__head">
          <div class="cg-section__title">
            <NIcon :component="TimeOutline" :size="18" />
            <span>操作时间线</span>
          </div>
          <p class="cg-section__hint">
            最近 50 条启用 / 禁用记录，含 reason。图谱条目暂未接入修订表。
          </p>
        </header>

        <NDataTable
          v-if="timeline.length > 0"
          :columns="timelineColumns"
          :data="timeline"
          :loading="timelineLoading"
          :bordered="false"
          size="small"
          :pagination="{ pageSize: 12 }"
        />
        <EmptyState
          v-else
          title="暂无时间线记录"
          description="跨群启用 / 禁用都会在这里留下审计痕迹。"
          :icon="TimeOutline"
        />
      </AppCard>
    </div>

    <NModal
      v-model:show="showDisableDialog"
      preset="card"
      :title="disableTarget ? `禁用跨群可见 · ${STORE_LABEL[disableTarget.store]}` : '禁用跨群可见'"
      style="width: 520px"
      :mask-closable="!disableSubmitting"
      :close-on-esc="!disableSubmitting"
    >
      <div v-if="disableTarget" class="cg-disable-panel">
        <p class="cg-disable-panel__line">
          <span class="cg-disable-panel__label">内容</span>
          <span class="cg-disable-panel__value">{{ disableTarget.label }}</span>
        </p>
        <p v-if="disableTarget.detail" class="cg-disable-panel__line">
          <span class="cg-disable-panel__label">细节</span>
          <span class="cg-disable-panel__value">{{ disableTarget.detail }}</span>
        </p>
        <p class="cg-disable-panel__line">
          <span class="cg-disable-panel__label">来源群</span>
          <span class="cg-disable-panel__value">{{ disableTarget.group_id || '—' }}</span>
        </p>
        <p v-if="disableTarget.enabled_reason" class="cg-disable-panel__line">
          <span class="cg-disable-panel__label">启用理由</span>
          <span class="cg-disable-panel__value">{{ disableTarget.enabled_reason }}</span>
        </p>

        <NDivider class="cg-disable-panel__divider" />

        <NFormItem label="禁用理由" required>
          <NInput
            v-model:value="disableReason"
            type="textarea"
            placeholder="例如：误启用 / 内容已过时 / 群文化差异不合适"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="200"
            show-count
          />
        </NFormItem>

        <p class="cg-disable-panel__note">
          禁用会立即写入修订表（reason 同步落库），可随时再次开启。
        </p>
      </div>
      <template #footer>
        <NSpace justify="end" :size="8">
          <NButton :disabled="disableSubmitting" @click="showDisableDialog = false">
            取消
          </NButton>
          <NButton
            type="error"
            :loading="disableSubmitting"
            @click="submitDisable"
          >
            确认禁用
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </AppPage>
</template>

<style scoped>
.cg-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.cg-section {
  padding: 20px 22px;
  margin-bottom: 20px;
}

.cg-section + .cg-section {
  margin-top: 0;
}

.cg-toolbar {
  margin-bottom: 16px;
}

.cg-tabs {
  width: 100%;
}

.cg-section__head {
  margin-bottom: 14px;
}

.cg-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--om-text-1);
}

.cg-section__hint {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.cg-section__placeholder {
  margin: 16px 0 0;
  color: var(--om-text-3);
  font-size: 13px;
}

.cg-simulate {
  margin-bottom: 14px;
}

.cg-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: 16px;
}

@media (max-width: 1080px) {
  .cg-grid {
    grid-template-columns: 1fr;
  }
}

.cg-disable-panel__line {
  display: flex;
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.55;
}

.cg-disable-panel__label {
  flex-shrink: 0;
  width: 80px;
  color: var(--om-text-2);
}

.cg-disable-panel__value {
  color: var(--om-text-1);
  word-break: break-word;
}

.cg-disable-panel__divider {
  margin: 14px 0 14px;
}

.cg-disable-panel__note {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
}
</style>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'

import { RefreshOutline, ServerOutline } from '@vicons/ionicons5'
import { NTag } from 'naive-ui'
import type { DataTableColumns, TagProps } from 'naive-ui'

import { api } from '../../../api/client'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../components/common/EmptyState.vue'

interface DatabaseStatusItem {
  db_id: string
  path: string
  owner: string
  clients: string[]
  exists: boolean
  status: 'ok' | 'missing' | 'error'
  detail: string
  size_bytes: number
  user_version: number | null
  target_user_version: number
  version_status: 'current' | 'legacy' | 'future' | 'missing' | 'unknown'
  journal_mode: string
  quick_check: string
  connection_profile: string
  backup_profile: string
  retention_profile: string
  critical: boolean
  optional: boolean
  sensitive: boolean
  rebuildable: boolean
}

interface DatabaseStatusPayload {
  items: DatabaseStatusItem[]
  summary: {
    total: number
    total_size_bytes: number
    ok_count: number
    missing_count: number
    error_count: number
  }
}

const emptyPayload: DatabaseStatusPayload = {
  items: [],
  summary: {
    total: 0,
    total_size_bytes: 0,
    ok_count: 0,
    missing_count: 0,
    error_count: 0,
  },
}

const payload = ref<DatabaseStatusPayload>(emptyPayload)
const loading = ref(true)
const loadError = ref('')
const pagination = { pageSize: 10 }

const attentionCount = computed(
  () => payload.value.summary.missing_count + payload.value.summary.error_count,
)

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const scaled = value / 1024 ** index
  return `${scaled >= 10 || index === 0 ? scaled.toFixed(0) : scaled.toFixed(1)} ${units[index]}`
}

function statusType(row: DatabaseStatusItem): TagProps['type'] {
  if (row.status === 'ok') return 'success'
  if (row.status === 'error') return 'error'
  return row.optional ? 'default' : 'warning'
}

function statusLabel(row: DatabaseStatusItem): string {
  if (row.status === 'ok') return '正常'
  if (row.status === 'error') return '异常'
  return row.optional ? '可选未创建' : '未创建'
}

function versionType(row: DatabaseStatusItem): TagProps['type'] {
  if (row.version_status === 'current') return 'success'
  if (row.version_status === 'future') return 'error'
  if (row.version_status === 'legacy') return 'warning'
  return 'default'
}

function versionLabel(row: DatabaseStatusItem): string {
  const current = row.user_version ?? '--'
  return `v${current} / v${row.target_user_version}`
}

const columns: DataTableColumns<DatabaseStatusItem> = [
  {
    title: '数据库',
    key: 'db_id',
    width: 230,
    render: row => h('div', { class: 'database-identity' }, [
      h('strong', row.db_id),
      h('span', row.path),
    ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: row => h(
      NTag,
      { size: 'small', round: true, type: statusType(row) },
      { default: () => statusLabel(row) },
    ),
  },
  {
    title: '版本',
    key: 'user_version',
    width: 125,
    render: row => h(
      NTag,
      { size: 'small', round: true, type: versionType(row) },
      { default: () => versionLabel(row) },
    ),
  },
  {
    title: 'Schema owner',
    key: 'owner',
    width: 270,
    ellipsis: { tooltip: true },
  },
  {
    title: '治理 profile',
    key: 'profiles',
    width: 270,
    render: row => h('span', { class: 'database-profile' }, [
      `连接 ${row.connection_profile}`,
      h('br'),
      `备份 ${row.backup_profile} · 保留 ${row.retention_profile}`,
    ]),
  },
  {
    title: '容量',
    key: 'size_bytes',
    width: 100,
    align: 'right',
    render: row => formatBytes(row.size_bytes),
  },
]

async function loadDatabases() {
  loading.value = true
  loadError.value = ''
  try {
    payload.value = await api<DatabaseStatusPayload>('/api/admin/databases')
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '数据库状态请求失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadDatabases()
})
</script>

<template>
  <AppPanelSection
    class="system-databases"
    eyebrow="Database Governance"
    title="数据库状态"
    description="Catalog 中全部 SQLite 存储的健康、容量、schema owner 与治理 profile。"
  >
    <template #aside>
      <NTag size="small" round type="success">
        {{ payload.summary.ok_count }} 正常
      </NTag>
      <NTag v-if="attentionCount" size="small" round type="warning">
        {{ attentionCount }} 待关注
      </NTag>
      <NTag size="small" round>
        {{ payload.summary.total }} 库 · {{ formatBytes(payload.summary.total_size_bytes) }}
      </NTag>
      <NButton secondary :loading="loading" @click="loadDatabases">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <NSpin :show="loading">
      <EmptyState
        v-if="loadError"
        compact
        title="数据库状态加载失败"
        :description="loadError"
        :icon="ServerOutline"
      >
        <NButton secondary @click="loadDatabases">
          重试
        </NButton>
      </EmptyState>

      <NDataTable
        v-else-if="payload.items.length"
        :columns="columns"
        :data="payload.items"
        :pagination="pagination"
        :row-key="row => row.db_id"
        :scroll-x="1115"
        :single-line="false"
        size="small"
      />

      <EmptyState
        v-else-if="!loading"
        compact
        title="数据库目录为空"
        description="后端没有返回数据库目录。"
        :icon="ServerOutline"
      />
    </NSpin>
  </AppPanelSection>
</template>

<style scoped>
.system-databases {
  margin-bottom: 24px;
}

.database-identity {
  min-width: 0;
}

.database-identity strong {
  display: block;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.database-identity span,
.database-profile {
  display: block;
  margin-top: 4px;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
</style>

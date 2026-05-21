<script setup lang="ts">
import {
  BulbOutline,
  RefreshOutline,
  TimeOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  ReloadOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppCard from '../../components/common/AppCard.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface EpisodeItem {
  episode_id: string
  group_id: string
  scope: string
  situation: string
  observed_context: string
  action_taken: string
  outcome_signal: string
  reflection: string
  linked_memory_ids: string[]
  confidence: number
  episode_state: string
  source: string
  decay_at: string
  last_used_at: string
  created_at: string
  updated_at: string
  disabled_by_admin: boolean
  cross_group_visible: boolean
  cross_group_enabled_by: string
  cross_group_enabled_at: string
  cross_group_enabled_for_groups: string[]
  cross_group_enabled_reason: string
}

interface EpisodeStats {
  dry_run: number
  candidate: number
  approved: number
  enabled_for_prompt: number
  disabled: number
}

interface Revision {
  revision_id: string
  episode_id: string
  action: string
  actor: string
  prev_state: string
  new_state: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: string
}

type EpisodeAction = 'approve' | 'disable' | 'restore'

const STATE_LABEL: Record<string, string> = {
  dry_run: 'dry_run',
  candidate: '候选',
  approved: '已批准',
  enabled_for_prompt: '已注入',
  disabled: '已停用',
}

const STATE_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  dry_run: 'default',
  candidate: 'info',
  approved: 'success',
  enabled_for_prompt: 'warning',
  disabled: 'error',
}

const ACTION_LABEL: Record<EpisodeAction, string> = {
  approve: '批准',
  disable: '停用',
  restore: '恢复',
}

const message = useMessage()

const loading = ref(false)
const episodes = ref<EpisodeItem[]>([])
const stats = ref<EpisodeStats>({
  dry_run: 0,
  candidate: 0,
  approved: 0,
  enabled_for_prompt: 0,
  disabled: 0,
})
const filterState = ref<string | null>(null)
const filterGroup = ref('')

const stateOptions: SelectOption[] = [
  { label: '全部状态', value: 'all' },
  { label: 'dry_run', value: 'dry_run' },
  { label: '候选 candidate', value: 'candidate' },
  { label: '已批准 approved', value: 'approved' },
  { label: '已注入 enabled_for_prompt', value: 'enabled_for_prompt' },
  { label: '已停用 disabled', value: 'disabled' },
]

const filterStateValue = computed({
  get: () => filterState.value ?? 'all',
  set: (val: string | null) => {
    filterState.value = val === 'all' ? null : val
  },
})

const actionTarget = ref<EpisodeItem | null>(null)
const actionType = ref<EpisodeAction>('approve')
const actionReason = ref('')
const actionSubmitting = ref(false)
const showActionDialog = computed({
  get: () => actionTarget.value !== null,
  set: (val: boolean) => {
    if (!val) {
      actionTarget.value = null
      actionReason.value = ''
    }
  },
})

const detailTarget = ref<EpisodeItem | null>(null)
const showDetail = computed({
  get: () => detailTarget.value !== null,
  set: (val: boolean) => {
    if (!val) detailTarget.value = null
  },
})
const revisions = ref<Revision[]>([])
const revisionsLoading = ref(false)

async function fetchStats() {
  try {
    const res = await api<{ ok: boolean, stats: EpisodeStats }>('/api/admin/episodes/stats')
    stats.value = res.stats || stats.value
  }
  catch {}
}

async function fetchEpisodes() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (filterState.value) params.set('state', filterState.value)
    if (filterGroup.value.trim()) params.set('group_id', filterGroup.value.trim())
    params.set('limit', '100')
    const qs = params.toString()
    const res = await api<{ ok: boolean, episodes: EpisodeItem[] }>(
      `/api/admin/episodes${qs ? '?' + qs : ''}`,
    )
    episodes.value = (res.episodes || []).map(ep => ({
      ...ep,
      linked_memory_ids: Array.isArray(ep.linked_memory_ids) ? ep.linked_memory_ids : [],
      cross_group_enabled_for_groups: Array.isArray(ep.cross_group_enabled_for_groups)
        ? ep.cross_group_enabled_for_groups
        : [],
    }))
  }
  catch (e: any) {
    message.error(e?.data?.error || '加载失败')
  }
  finally {
    loading.value = false
  }
}

function refresh() {
  fetchStats()
  fetchEpisodes()
}

function openActionDialog(ep: EpisodeItem, action: EpisodeAction) {
  actionTarget.value = ep
  actionType.value = action
  actionReason.value = ''
}

async function submitAction() {
  if (!actionTarget.value) return
  const target = actionTarget.value
  const action = actionType.value
  actionSubmitting.value = true
  try {
    await api(`/api/admin/episodes/${target.episode_id}/${action}`, {
      method: 'POST',
      body: { reason: actionReason.value.trim() },
    })
    message.success(`${ACTION_LABEL[action]}成功`)
    showActionDialog.value = false
    await fetchStats()
    await fetchEpisodes()
    if (detailTarget.value && detailTarget.value.episode_id === target.episode_id) {
      await openDetail(target.episode_id)
    }
  }
  catch (e: any) {
    message.error(e?.data?.error || '操作失败')
  }
  finally {
    actionSubmitting.value = false
  }
}

async function openDetail(episodeId: string) {
  const ep = episodes.value.find(e => e.episode_id === episodeId)
  if (!ep) return
  detailTarget.value = ep
  revisions.value = []
  revisionsLoading.value = true
  try {
    const res = await api<{ ok: boolean, revisions: Revision[] }>(
      `/api/admin/episodes/${episodeId}/revisions?limit=100`,
    )
    revisions.value = res.revisions || []
  }
  catch {
    revisions.value = []
  }
  finally {
    revisionsLoading.value = false
  }
}

function decayHint(decayAt: string): string {
  if (!decayAt) return '—'
  const t = new Date(decayAt).getTime()
  if (Number.isNaN(t)) return decayAt
  const diff = t - Date.now()
  if (diff <= 0) return '已过期'
  const hours = Math.round(diff / 3600000)
  if (hours < 24) return `约 ${hours} 小时`
  const days = Math.round(hours / 24)
  return `约 ${days} 天`
}

const columns = computed<DataTableColumns<EpisodeItem>>(() => [
  {
    title: '场景',
    key: 'situation',
    minWidth: 240,
    ellipsis: { tooltip: true },
    render: row => h(
      'a',
      {
        style: 'color: var(--om-text-1); cursor: pointer; font-weight: 500',
        onClick: () => openDetail(row.episode_id),
      },
      row.situation || '(未填写)',
    ),
  },
  {
    title: '状态',
    key: 'episode_state',
    width: 130,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: STATE_TAG_TYPE[row.episode_state] || 'default' },
      () => STATE_LABEL[row.episode_state] || row.episode_state,
    ),
  },
  { title: '群 ID', key: 'group_id', width: 130, ellipsis: { tooltip: true } },
  {
    title: '置信度',
    key: 'confidence',
    width: 90,
    render: row => `${Math.round(row.confidence * 100)}%`,
  },
  { title: '来源', key: 'source', width: 110, ellipsis: { tooltip: true } },
  {
    title: '衰减剩余',
    key: 'decay_at',
    width: 130,
    render: row => h(
      'span',
      { style: row.decay_at ? '' : 'color: var(--om-text-3); font-size: 12px' },
      decayHint(row.decay_at),
    ),
  },
  {
    title: '最近使用',
    key: 'last_used_at',
    width: 170,
    render: row => row.last_used_at
      ? row.last_used_at
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '从未使用'),
  },
  { title: '更新时间', key: 'updated_at', width: 170 },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    fixed: 'right',
    render: (row) => {
      const NButton = resolveComponent('NButton') as any
      const NIcon = resolveComponent('NIcon') as any
      const NSpace = resolveComponent('NSpace') as any
      const btns: any[] = []
      if (row.episode_state === 'candidate') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'success',
          quaternary: true,
          onClick: () => openActionDialog(row, 'approve'),
        }, {
          default: () => '批准',
          icon: () => h(NIcon, { component: CheckmarkCircleOutline }),
        }))
      }
      if (row.episode_state !== 'disabled') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'error',
          quaternary: true,
          onClick: () => openActionDialog(row, 'disable'),
        }, {
          default: () => '停用',
          icon: () => h(NIcon, { component: CloseCircleOutline }),
        }))
      }
      if (row.episode_state === 'disabled') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'info',
          quaternary: true,
          onClick: () => openActionDialog(row, 'restore'),
        }, {
          default: () => '恢复',
          icon: () => h(NIcon, { component: ReloadOutline }),
        }))
      }
      btns.push(h(NButton, {
        size: 'tiny',
        quaternary: true,
        onClick: () => openDetail(row.episode_id),
      }, () => '详情'))
      return h(NSpace, { size: 4 }, () => btns)
    },
  },
])

const revisionColumns = computed<DataTableColumns<Revision>>(() => [
  { title: '时间', key: 'created_at', width: 170 },
  {
    title: '动作',
    key: 'action',
    width: 200,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: 'default' },
      () => row.action,
    ),
  },
  { title: '操作者', key: 'actor', width: 110 },
  {
    title: '状态变化',
    key: 'transition',
    width: 200,
    render: row => row.prev_state || row.new_state
      ? `${row.prev_state || '—'} → ${row.new_state || '—'}`
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
  {
    title: '理由',
    key: 'reason',
    minWidth: 220,
    ellipsis: { tooltip: true },
    render: row => row.reason
      ? row.reason
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
])

onMounted(() => refresh())
</script>

<template>
  <AppPage
    title="经验反思"
    eyebrow="Episodic Memory"
    description="记录 bot 复盘后产出的经验条目，5 状态生命周期：dry_run → candidate → approved → enabled_for_prompt → disabled。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag size="small" round type="info">
          {{ episodes.length }} 条
        </NTag>
        <NButton secondary :loading="loading" @click="refresh">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
      </NSpace>
    </template>

    <div class="ep-metrics">
      <MetricCard
        title="dry_run"
        :value="stats.dry_run"
        hint="刚写入，未达置信阈值"
      />
      <MetricCard
        title="candidate"
        :value="stats.candidate"
        hint="自动晋升后等待审核"
        accent="info"
      />
      <MetricCard
        title="approved"
        :value="stats.approved"
        hint="已审核，未注入 prompt"
        accent="success"
      />
      <MetricCard
        title="enabled_for_prompt"
        :value="stats.enabled_for_prompt"
        hint="正在注入（Phase B 后才解锁）"
        accent="warning"
      />
      <MetricCard
        title="disabled"
        :value="stats.disabled"
        hint="已停用 / 衰减到期"
      />
    </div>

    <NAlert
      v-if="stats.enabled_for_prompt === 0"
      type="info"
      class="ep-alert"
      :show-icon="false"
    >
      <strong>Phase B 提示</strong> · enabled_for_prompt 状态推进按钮在 Phase B BlockTraceBus 落地前不可用。当前可执行：批准 / 停用 / 恢复。
    </NAlert>

    <AppCard bordered class="ep-section">
      <PageToolbar class="ep-toolbar">
        <template #left>
          <NSelect
            v-model:value="filterStateValue"
            :options="stateOptions"
            size="small"
            style="width: 220px"
            @update:value="fetchEpisodes"
          />
          <NInput
            v-model:value="filterGroup"
            placeholder="按群 ID 过滤"
            clearable
            size="small"
            style="width: 200px"
            @keyup.enter="fetchEpisodes"
            @clear="fetchEpisodes"
          />
        </template>
        <template #right>
          <NButton size="small" type="primary" secondary @click="fetchEpisodes">
            应用筛选
          </NButton>
        </template>
      </PageToolbar>

      <NDataTable
        v-if="episodes.length > 0"
        :columns="columns"
        :data="episodes"
        :loading="loading"
        :bordered="false"
        size="small"
        :scroll-x="1500"
        :pagination="{ pageSize: 20 }"
        :row-key="(row: EpisodeItem) => row.episode_id"
      />
      <EmptyState
        v-else
        title="暂无经验反思"
        description="Bot 完成对话后由 Consolidator 写入。dry_run 是默认初始状态，置信度达 0.6 后自动晋升 candidate。"
        :icon="BulbOutline"
      />
    </AppCard>

    <NModal
      v-model:show="showActionDialog"
      preset="card"
      :title="actionTarget ? `${ACTION_LABEL[actionType]} · ${actionTarget.situation || actionTarget.episode_id}` : '操作'"
      style="width: 540px"
      :mask-closable="!actionSubmitting"
      :close-on-esc="!actionSubmitting"
    >
      <div v-if="actionTarget" class="ep-action-panel">
        <p class="ep-action-panel__line">
          <span class="ep-action-panel__label">当前状态</span>
          <NTag size="small" :type="STATE_TAG_TYPE[actionTarget.episode_state] || 'default'">
            {{ STATE_LABEL[actionTarget.episode_state] || actionTarget.episode_state }}
          </NTag>
        </p>
        <p class="ep-action-panel__line">
          <span class="ep-action-panel__label">来源 / 群</span>
          <span class="ep-action-panel__value">{{ actionTarget.source }} · {{ actionTarget.group_id || '—' }}</span>
        </p>
        <p class="ep-action-panel__line">
          <span class="ep-action-panel__label">置信度</span>
          <span class="ep-action-panel__value">{{ Math.round(actionTarget.confidence * 100) }}%</span>
        </p>

        <NDivider class="ep-action-panel__divider" />

        <NFormItem :label="`${ACTION_LABEL[actionType]}理由`">
          <NInput
            v-model:value="actionReason"
            type="textarea"
            :placeholder="actionType === 'approve' ? '说明为什么这条经验值得保留'
              : actionType === 'disable' ? '说明为什么停用（误学习 / 内容已过时 / 与人格冲突）'
                : '说明恢复理由'"
            :autosize="{ minRows: 2, maxRows: 5 }"
            maxlength="200"
            show-count
          />
        </NFormItem>

        <p class="ep-action-panel__note">
          理由会写入 episode_revisions，可在详情抽屉里查看完整历史。
        </p>
      </div>

      <template #footer>
        <NSpace justify="end" :size="8">
          <NButton :disabled="actionSubmitting" @click="showActionDialog = false">
            取消
          </NButton>
          <NButton
            :type="actionType === 'disable' ? 'error' : actionType === 'approve' ? 'success' : 'info'"
            :loading="actionSubmitting"
            @click="submitAction"
          >
            确认{{ ACTION_LABEL[actionType] }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <NDrawer
      v-model:show="showDetail"
      :width="720"
      placement="right"
    >
      <NDrawerContent
        :title="detailTarget?.situation || detailTarget?.episode_id || '经验详情'"
        :native-scrollbar="false"
      >
        <div v-if="detailTarget" class="ep-detail">
          <section class="ep-detail__section">
            <h3 class="ep-detail__title">
              基本信息
            </h3>
            <div class="ep-detail__grid">
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  状态
                </div>
                <NTag size="small" :type="STATE_TAG_TYPE[detailTarget.episode_state] || 'default'">
                  {{ STATE_LABEL[detailTarget.episode_state] || detailTarget.episode_state }}
                </NTag>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  来源
                </div>
                <div>{{ detailTarget.source }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  群 / 范围
                </div>
                <div>{{ detailTarget.scope }} · {{ detailTarget.group_id || '—' }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  置信度
                </div>
                <div>{{ Math.round(detailTarget.confidence * 100) }}%</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  衰减剩余
                </div>
                <div>{{ decayHint(detailTarget.decay_at) }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  最近使用
                </div>
                <div>{{ detailTarget.last_used_at || '从未使用' }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  创建时间
                </div>
                <div>{{ detailTarget.created_at }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  更新时间
                </div>
                <div>{{ detailTarget.updated_at }}</div>
              </div>
            </div>
          </section>

          <section class="ep-detail__section">
            <h3 class="ep-detail__title">
              经验内容
            </h3>
            <div class="ep-detail__field">
              <div class="ep-detail__label">
                场景 situation
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.situation || '—' }}
              </p>
            </div>
            <div v-if="detailTarget.observed_context" class="ep-detail__field">
              <div class="ep-detail__label">
                观察上下文 observed_context
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.observed_context }}
              </p>
            </div>
            <div v-if="detailTarget.action_taken" class="ep-detail__field">
              <div class="ep-detail__label">
                采取行动 action_taken
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.action_taken }}
              </p>
            </div>
            <div v-if="detailTarget.outcome_signal" class="ep-detail__field">
              <div class="ep-detail__label">
                结果信号 outcome_signal
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.outcome_signal }}
              </p>
            </div>
            <div v-if="detailTarget.reflection" class="ep-detail__field">
              <div class="ep-detail__label">
                反思 reflection
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.reflection }}
              </p>
            </div>
            <div v-if="detailTarget.linked_memory_ids?.length" class="ep-detail__field">
              <div class="ep-detail__label">
                关联记忆
              </div>
              <NSpace :size="6" wrap>
                <NTag
                  v-for="mid in detailTarget.linked_memory_ids"
                  :key="mid"
                  size="small"
                  round
                  type="info"
                >
                  {{ mid }}
                </NTag>
              </NSpace>
            </div>
          </section>

          <section v-if="detailTarget.cross_group_visible" class="ep-detail__section">
            <h3 class="ep-detail__title">
              跨群可见
            </h3>
            <div class="ep-detail__grid">
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  启用者
                </div>
                <div>{{ detailTarget.cross_group_enabled_by || '—' }}</div>
              </div>
              <div class="ep-detail__item">
                <div class="ep-detail__label">
                  启用时间
                </div>
                <div>{{ detailTarget.cross_group_enabled_at || '—' }}</div>
              </div>
            </div>
            <div v-if="detailTarget.cross_group_enabled_for_groups.length" class="ep-detail__field">
              <div class="ep-detail__label">
                可见群
              </div>
              <NSpace :size="6" wrap>
                <NTag
                  v-for="g in detailTarget.cross_group_enabled_for_groups"
                  :key="g"
                  size="small"
                  type="info"
                >
                  {{ g }}
                </NTag>
              </NSpace>
            </div>
            <div v-if="detailTarget.cross_group_enabled_reason" class="ep-detail__field">
              <div class="ep-detail__label">
                启用理由
              </div>
              <p class="ep-detail__text">
                {{ detailTarget.cross_group_enabled_reason }}
              </p>
            </div>
          </section>

          <section class="ep-detail__section">
            <h3 class="ep-detail__title ep-detail__title--with-icon">
              <NIcon :component="TimeOutline" :size="16" /> 修订历史
            </h3>
            <NDataTable
              v-if="revisions.length > 0"
              :columns="revisionColumns"
              :data="revisions"
              :loading="revisionsLoading"
              :bordered="false"
              size="small"
              :pagination="{ pageSize: 8 }"
            />
            <EmptyState
              v-else-if="!revisionsLoading"
              title="暂无修订记录"
              description="状态变更与跨群启用都会留下记录。"
              :icon="TimeOutline"
              compact
            />
            <div v-else class="ep-detail__loading">
              <NSpin size="small" />
            </div>
          </section>
        </div>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.ep-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.ep-alert {
  margin-bottom: 18px;
  border-radius: 12px;
}

.ep-section {
  padding: 20px 22px;
  margin-bottom: 16px;
}

.ep-toolbar {
  margin-bottom: 16px;
}

.ep-action-panel__line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 13px;
}

.ep-action-panel__label {
  flex-shrink: 0;
  width: 90px;
  color: var(--om-text-2);
}

.ep-action-panel__value {
  color: var(--om-text-1);
}

.ep-action-panel__divider {
  margin: 14px 0;
}

.ep-action-panel__note {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
}

.ep-detail__section {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--om-border);
  margin-bottom: 18px;
}

.ep-detail__section:last-child {
  border-bottom: none;
}

.ep-detail__title {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.ep-detail__title--with-icon {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ep-detail__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
}

.ep-detail__item {
  min-width: 0;
}

.ep-detail__label {
  margin-bottom: 4px;
  color: var(--om-text-3);
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.ep-detail__field {
  margin-top: 12px;
}

.ep-detail__text {
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.ep-detail__loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>

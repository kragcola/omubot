<script setup lang="ts">
import {
  RefreshOutline,
  FilterOutline,
  CheckmarkCircleOutline,
  CloseCircleOutline,
  TimeOutline,
  BulbOutline,
  CreateOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'

interface Candidate {
  candidate_id: string
  run_id: string
  domain: string
  scope: string
  group_id: string
  source_message_pks: number[]
  payload: Record<string, any>
  confidence: number
  state: string
  decision_reason: string
  decided_by: string
  decided_at: number
  normalizer_cluster_id: string
  created_at: number
}

interface Revision {
  revision_id: string
  candidate_id: string
  action: string
  actor: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: number
  meta: Record<string, any>
}

const DOMAIN_LABEL: Record<string, string> = {
  fact: '事实 fact',
  slang: '黑话 slang',
  style: '风格 style',
  episode: '经验 episode',
  graph_relation: '图谱 graph_relation',
}

const STATE_LABEL: Record<string, string> = {
  dry_run: 'dry_run',
  queued: '已入队 queued',
  approved: '已批准 approved',
  rejected: '已拒绝 rejected',
}

const STATE_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  dry_run: 'default',
  queued: 'info',
  approved: 'success',
  rejected: 'error',
}

const DOMAIN_TAG_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  fact: 'info',
  slang: 'success',
  style: 'warning',
  episode: 'default',
  graph_relation: 'error',
}

const EPISODE_FIELDS: { key: string, label: string }[] = [
  { key: 'situation', label: '场景 situation' },
  { key: 'observed_context', label: '观察上下文 observed_context' },
  { key: 'action_taken', label: '采取行动 action_taken' },
  { key: 'outcome_signal', label: '结果信号 outcome_signal' },
  { key: 'reflection', label: '反思 reflection' },
]

const message = useMessage()

const loading = ref(false)
const candidates = ref<Candidate[]>([])
const filterDomain = ref<string>('all')
const filterState = ref<string>('all')
const filterGroup = ref('')

const stateOptions = [
  { label: '全部状态', value: 'all' },
  { label: 'dry_run', value: 'dry_run' },
  { label: '已入队 queued', value: 'queued' },
  { label: '已批准 approved', value: 'approved' },
  { label: '已拒绝 rejected', value: 'rejected' },
]

const detailTarget = ref<Candidate | null>(null)
const showDetail = computed({
  get: () => detailTarget.value !== null,
  set: (val: boolean) => {
    if (!val) {
      detailTarget.value = null
      editPayload.value = {}
      editing.value = false
    }
  },
})
const revisions = ref<Revision[]>([])
const revisionsLoading = ref(false)

const editing = ref(false)
const editPayload = ref<Record<string, string>>({})
const editReason = ref('')
const editSubmitting = ref(false)

const decideTarget = ref<Candidate | null>(null)
const decideAction = ref<'approved' | 'rejected'>('approved')
const decideReason = ref('')
const decideSubmitting = ref(false)
const showDecideDialog = computed({
  get: () => decideTarget.value !== null,
  set: (val: boolean) => {
    if (!val) {
      decideTarget.value = null
      decideReason.value = ''
    }
  },
})

const filteredCandidates = computed(() => {
  return candidates.value.filter((c) => {
    if (filterDomain.value !== 'all' && c.domain !== filterDomain.value) return false
    if (filterGroup.value.trim() && c.group_id !== filterGroup.value.trim()) return false
    return true
  })
})

const stats = computed(() => {
  const out = { total: 0, dry_run: 0, queued: 0, approved: 0, rejected: 0, episode: 0 }
  for (const c of candidates.value) {
    out.total += 1
    if (c.state in out) (out as any)[c.state] += 1
    if (c.domain === 'episode') out.episode += 1
  }
  return out
})

async function fetchCandidates() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (filterState.value !== 'all') params.set('state', filterState.value)
    params.set('limit', '200')
    const qs = params.toString()
    const res = await api<{ ok: boolean, data: Candidate[], count: number }>(
      `/api/admin/memory_consolidator/candidates${qs ? '?' + qs : ''}`,
    )
    candidates.value = (res.data || []).map(c => ({
      ...c,
      source_message_pks: Array.isArray(c.source_message_pks) ? c.source_message_pks : [],
      payload: c.payload && typeof c.payload === 'object' ? c.payload : {},
    }))
  }
  catch (e: any) {
    message.error(e?.data?.error || '加载候选失败')
  }
  finally {
    loading.value = false
  }
}

function refresh() {
  fetchCandidates()
}

async function openDetail(candidateId: string) {
  const c = candidates.value.find(x => x.candidate_id === candidateId)
  if (!c) return
  detailTarget.value = c
  editPayload.value = {}
  editing.value = false
  revisions.value = []
  revisionsLoading.value = true
  try {
    const res = await api<{ ok: boolean, data: Revision[], count: number }>(
      `/api/admin/memory_consolidator/candidates/${candidateId}/revisions?limit=100`,
    )
    revisions.value = res.data || []
  }
  catch {
    revisions.value = []
  }
  finally {
    revisionsLoading.value = false
  }
}

function startEdit() {
  if (!detailTarget.value) return
  if (detailTarget.value.domain !== 'episode') return
  if (!['dry_run', 'queued'].includes(detailTarget.value.state)) return
  const draft: Record<string, string> = {}
  for (const field of EPISODE_FIELDS) {
    draft[field.key] = String(detailTarget.value.payload[field.key] ?? '')
  }
  editPayload.value = draft
  editReason.value = ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  editPayload.value = {}
  editReason.value = ''
}

async function submitEdit() {
  if (!detailTarget.value) return
  const target = detailTarget.value
  editSubmitting.value = true
  try {
    const res = await api<{ ok: boolean, data: Candidate }>(
      `/api/admin/memory_consolidator/candidates/${target.candidate_id}/payload`,
      {
        method: 'PATCH',
        body: {
          payload: { ...editPayload.value },
          actor: 'admin',
          reason: editReason.value.trim(),
        },
      },
    )
    message.success('payload 已保存')
    // refresh local row
    const idx = candidates.value.findIndex(c => c.candidate_id === target.candidate_id)
    if (idx >= 0 && res.data) {
      candidates.value[idx] = res.data
      detailTarget.value = res.data
    }
    editing.value = false
    // refresh revisions
    try {
      const revRes = await api<{ ok: boolean, data: Revision[] }>(
        `/api/admin/memory_consolidator/candidates/${target.candidate_id}/revisions?limit=100`,
      )
      revisions.value = revRes.data || []
    }
    catch {}
  }
  catch (e: any) {
    message.error(e?.data?.error || '保存失败')
  }
  finally {
    editSubmitting.value = false
  }
}

function openDecide(c: Candidate, action: 'approved' | 'rejected') {
  decideTarget.value = c
  decideAction.value = action
  decideReason.value = ''
}

async function submitDecide() {
  if (!decideTarget.value) return
  const target = decideTarget.value
  const action = decideAction.value
  decideSubmitting.value = true
  try {
    const res = await api<{ ok: boolean, data: any }>(
      `/api/admin/memory_consolidator/candidates/${target.candidate_id}/decide`,
      {
        method: 'POST',
        body: {
          state: action,
          decided_by: 'admin',
          reason: decideReason.value.trim(),
        },
      },
    )
    if (action === 'approved' && target.domain === 'episode') {
      const promote = res.data?.promote
      if (promote?.promoted) {
        message.success(`已批准并 promote 为 episode: ${promote.episode_id}`)
      }
      else if (promote?.skipped_reason) {
        message.warning(`已批准；promote 跳过：${promote.skipped_reason}`)
      }
      else {
        message.success('已批准')
      }
    }
    else {
      message.success(action === 'approved' ? '已批准' : '已拒绝')
    }
    showDecideDialog.value = false
    await fetchCandidates()
    if (detailTarget.value && detailTarget.value.candidate_id === target.candidate_id) {
      const refreshed = candidates.value.find(c => c.candidate_id === target.candidate_id)
      if (refreshed) detailTarget.value = refreshed
    }
  }
  catch (e: any) {
    message.error(e?.data?.error || '操作失败')
  }
  finally {
    decideSubmitting.value = false
  }
}

function timeText(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  if (Number.isNaN(d.getTime())) return String(ts)
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

const columns = computed<DataTableColumns<Candidate>>(() => [
  {
    title: '摘要',
    key: 'summary',
    minWidth: 260,
    ellipsis: { tooltip: true },
    render: (row) => {
      const anchor = row.payload?.situation
        || row.payload?.term
        || row.payload?.expression
        || row.payload?.subject
        || row.payload?.subject_node
        || '(空 payload)'
      return h(
        'a',
        {
          style: 'color: var(--om-text-1); cursor: pointer; font-weight: 500',
          onClick: () => openDetail(row.candidate_id),
        },
        String(anchor),
      )
    },
  },
  {
    title: '域',
    key: 'domain',
    width: 140,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: DOMAIN_TAG_TYPE[row.domain] || 'default' },
      () => DOMAIN_LABEL[row.domain] || row.domain,
    ),
  },
  {
    title: '状态',
    key: 'state',
    width: 130,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: STATE_TAG_TYPE[row.state] || 'default' },
      () => STATE_LABEL[row.state] || row.state,
    ),
  },
  { title: '群 ID', key: 'group_id', width: 120, ellipsis: { tooltip: true } },
  {
    title: '置信度',
    key: 'confidence',
    width: 90,
    render: row => `${Math.round(row.confidence * 100)}%`,
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 170,
    render: row => timeText(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    fixed: 'right',
    render: (row) => {
      const NButton = resolveComponent('NButton') as any
      const NSpace = resolveComponent('NSpace') as any
      const btns: any[] = []
      if (row.state === 'dry_run') {
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'success',
          quaternary: true,
          onClick: () => openDecide(row, 'approved'),
        }, () => '批准'))
        btns.push(h(NButton, {
          size: 'tiny',
          type: 'error',
          quaternary: true,
          onClick: () => openDecide(row, 'rejected'),
        }, () => '拒绝'))
      }
      btns.push(h(NButton, {
        size: 'tiny',
        quaternary: true,
        onClick: () => openDetail(row.candidate_id),
      }, () => '详情'))
      return h(NSpace, { size: 4 }, () => btns)
    },
  },
])

const revisionColumns = computed<DataTableColumns<Revision>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 170,
    render: row => timeText(row.created_at),
  },
  {
    title: '动作',
    key: 'action',
    width: 160,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: 'default' },
      () => row.action,
    ),
  },
  { title: '操作者', key: 'actor', width: 110 },
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

const canEdit = computed(() => {
  if (!detailTarget.value) return false
  if (detailTarget.value.domain !== 'episode') return false
  return ['dry_run', 'queued'].includes(detailTarget.value.state)
})

onMounted(() => refresh())
watch(filterState, fetchCandidates)
</script>

<template>
  <AppPage
    title="记忆候选"
    eyebrow="Memory Consolidator"
    description="Phase C dry-run 产出的 5 域候选审阅入口。episode 域候选 approve 后自动 promote 进 EpisodeStore，admin 可在批准前补改 reflection。"
  >
    <template #action>
      <NSpace align="center" :size="12">
        <NTag size="small" round type="info">
          {{ filteredCandidates.length }} / {{ candidates.length }} 条
        </NTag>
        <NButton secondary :loading="loading" @click="refresh">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
      </NSpace>
    </template>

    <div class="mc-metrics">
      <MetricCard title="总候选" :value="stats.total" hint="当前可见的所有候选" />
      <MetricCard title="dry_run" :value="stats.dry_run" hint="刚生成，等待审核" accent="info" />
      <MetricCard title="已批准" :value="stats.approved" hint="approve 后 episode 自动 promote" accent="success" />
      <MetricCard title="已拒绝" :value="stats.rejected" hint="不进生产存储" accent="warning" />
      <MetricCard title="episode 域" :value="stats.episode" hint="可走 promote 桥进 EpisodeStore" />
    </div>

    <AppPanelSection
      eyebrow="Candidates"
      title="候选列表"
      description="按状态 / 域 / 群 ID 过滤所有 Phase C 产出的候选，点击行可查看 payload 与修订历史。"
      class="mc-list-panel"
    >
      <PageToolbar class="mc-toolbar">
        <template #left>
          <NSelect
            v-model:value="filterState"
            :options="stateOptions"
            size="small"
            class="mc-toolbar__state"
          />
          <NInput
            v-model:value="filterGroup"
            placeholder="按群 ID 过滤"
            clearable
            size="small"
            class="mc-toolbar__group"
          />
          <div class="mc-domain-chips">
            <NTag
              v-for="opt in [
                { value: 'all', label: '全部域' },
                { value: 'fact', label: 'fact' },
                { value: 'slang', label: 'slang' },
                { value: 'style', label: 'style' },
                { value: 'episode', label: 'episode' },
                { value: 'graph_relation', label: 'graph' },
              ]"
              :key="opt.value"
              size="small"
              round
              :checkable="true"
              :checked="filterDomain === opt.value"
              :type="filterDomain === opt.value ? 'primary' : 'default'"
              @update:checked="(v: boolean) => { if (v) filterDomain = opt.value }"
            >
              {{ opt.label }}
            </NTag>
          </div>
        </template>
        <template #right>
          <NButton size="small" type="primary" secondary @click="refresh">
            <template #icon>
              <NIcon :component="FilterOutline" />
            </template>
            应用筛选
          </NButton>
        </template>
      </PageToolbar>

      <NDataTable
        v-if="filteredCandidates.length > 0"
        :columns="columns"
        :data="filteredCandidates"
        :loading="loading"
        :bordered="false"
        size="small"
        :scroll-x="1320"
        :pagination="{ pageSize: 20 }"
        :row-key="(row: Candidate) => row.candidate_id"
      />
      <EmptyState
        v-else
        title="暂无候选"
        description="Phase C 还未跑出本筛选条件下的候选；可在筛选项放宽后重试。"
        :icon="BulbOutline"
      />
    </AppPanelSection>

    <NModal
      v-model:show="showDecideDialog"
      preset="card"
      :title="decideTarget ? `${decideAction === 'approved' ? '批准' : '拒绝'} · ${decideTarget.candidate_id}` : '操作'"
      style="width: 540px"
      :mask-closable="!decideSubmitting"
      :close-on-esc="!decideSubmitting"
    >
      <div v-if="decideTarget" class="mc-decide">
        <p class="mc-decide__line">
          <span class="mc-decide__label">域 / 状态</span>
          <NTag size="small" :type="DOMAIN_TAG_TYPE[decideTarget.domain] || 'default'">
            {{ DOMAIN_LABEL[decideTarget.domain] || decideTarget.domain }}
          </NTag>
          <NTag size="small" :type="STATE_TAG_TYPE[decideTarget.state] || 'default'">
            {{ STATE_LABEL[decideTarget.state] || decideTarget.state }}
          </NTag>
        </p>
        <p class="mc-decide__line">
          <span class="mc-decide__label">置信度 / 群</span>
          <span class="mc-decide__value">{{ Math.round(decideTarget.confidence * 100) }}% · {{ decideTarget.group_id || '—' }}</span>
        </p>
        <NDivider class="mc-decide__divider" />
        <NFormItem :label="`${decideAction === 'approved' ? '批准' : '拒绝'}理由`">
          <NInput
            v-model:value="decideReason"
            type="textarea"
            :placeholder="decideAction === 'approved'
              ? '说明为什么这条候选值得保留'
              : '说明为什么拒绝（误学习 / 与人格冲突 / 重复）'"
            :autosize="{ minRows: 2, maxRows: 5 }"
            maxlength="200"
            show-count
          />
        </NFormItem>
        <p
          v-if="decideAction === 'approved' && decideTarget.domain === 'episode'"
          class="mc-decide__note"
        >
          批准后将自动 promote 为 EpisodeStore 中的 dry_run 经验；可在「经验反思」页面继续推进。
        </p>
      </div>
      <template #footer>
        <NSpace justify="end" :size="8">
          <NButton :disabled="decideSubmitting" @click="showDecideDialog = false">
            取消
          </NButton>
          <NButton
            :type="decideAction === 'approved' ? 'success' : 'error'"
            :loading="decideSubmitting"
            @click="submitDecide"
          >
            确认{{ decideAction === 'approved' ? '批准' : '拒绝' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <NDrawer v-model:show="showDetail" :width="780" placement="right">
      <NDrawerContent
        :title="detailTarget?.candidate_id || '候选详情'"
        :native-scrollbar="false"
      >
        <div v-if="detailTarget" class="mc-detail">
          <section class="mc-detail__section">
            <h3 class="mc-detail__title">
              基本信息
            </h3>
            <div class="mc-detail__grid">
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  域
                </div>
                <NTag size="small" :type="DOMAIN_TAG_TYPE[detailTarget.domain] || 'default'">
                  {{ DOMAIN_LABEL[detailTarget.domain] || detailTarget.domain }}
                </NTag>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  状态
                </div>
                <NTag size="small" :type="STATE_TAG_TYPE[detailTarget.state] || 'default'">
                  {{ STATE_LABEL[detailTarget.state] || detailTarget.state }}
                </NTag>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  群 / scope
                </div>
                <div>{{ detailTarget.scope }} · {{ detailTarget.group_id || '—' }}</div>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  置信度
                </div>
                <div>{{ Math.round(detailTarget.confidence * 100) }}%</div>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  Run ID
                </div>
                <div class="mc-detail__mono">
                  {{ detailTarget.run_id }}
                </div>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  Cluster ID
                </div>
                <div class="mc-detail__mono">
                  {{ detailTarget.normalizer_cluster_id || '—' }}
                </div>
              </div>
              <div class="mc-detail__item">
                <div class="mc-detail__label">
                  创建时间
                </div>
                <div>{{ timeText(detailTarget.created_at) }}</div>
              </div>
              <div v-if="detailTarget.decided_at" class="mc-detail__item">
                <div class="mc-detail__label">
                  审决时间
                </div>
                <div>{{ timeText(detailTarget.decided_at) }} · {{ detailTarget.decided_by }}</div>
              </div>
            </div>
          </section>

          <section class="mc-detail__section">
            <div class="mc-detail__title-row">
              <h3 class="mc-detail__title">
                Payload
              </h3>
              <div v-if="canEdit && !editing">
                <NButton size="tiny" secondary @click="startEdit">
                  <template #icon>
                    <NIcon :component="CreateOutline" />
                  </template>
                  编辑 reflection
                </NButton>
              </div>
            </div>

            <div v-if="detailTarget.domain === 'episode' && editing" class="mc-edit">
              <NFormItem
                v-for="field in EPISODE_FIELDS"
                :key="field.key"
                :label="field.label"
              >
                <NInput
                  v-model:value="editPayload[field.key]"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  :placeholder="field.key === 'reflection' ? '补改 LLM 漏写的反思 — 这条会用作 prompt 注入材料' : '可留空'"
                />
              </NFormItem>
              <NFormItem label="编辑理由">
                <NInput
                  v-model:value="editReason"
                  type="textarea"
                  placeholder="说明为什么补改（如：reflection 字段 LLM 漏写）"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                  maxlength="200"
                  show-count
                />
              </NFormItem>
              <NSpace justify="end" :size="8">
                <NButton :disabled="editSubmitting" @click="cancelEdit">
                  取消
                </NButton>
                <NButton
                  type="primary"
                  :loading="editSubmitting"
                  @click="submitEdit"
                >
                  保存 payload
                </NButton>
              </NSpace>
              <p class="mc-edit__note">
                Payload 会按 episode 域 schema 投影；未知字段会被静默丢弃；保存后会写入 candidate_revisions。
              </p>
            </div>

            <template v-else-if="detailTarget.domain === 'episode'">
              <div
                v-for="field in EPISODE_FIELDS"
                :key="field.key"
                class="mc-detail__field"
              >
                <div class="mc-detail__label">
                  {{ field.label }}
                </div>
                <p class="mc-detail__text">
                  {{ detailTarget.payload[field.key] || '—' }}
                </p>
              </div>
            </template>

            <pre v-else class="mc-detail__json">{{ JSON.stringify(detailTarget.payload, null, 2) }}</pre>
          </section>

          <section v-if="detailTarget.source_message_pks.length" class="mc-detail__section">
            <h3 class="mc-detail__title">
              源消息 PK
            </h3>
            <NSpace :size="6" wrap>
              <NTag
                v-for="pk in detailTarget.source_message_pks"
                :key="pk"
                size="small"
                round
              >
                {{ pk }}
              </NTag>
            </NSpace>
          </section>

          <section class="mc-detail__section">
            <h3 class="mc-detail__title mc-detail__title--with-icon">
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
              description="payload 编辑会写入此处，便于审计。"
              :icon="TimeOutline"
              compact
            />
            <div v-else class="mc-detail__loading">
              <NSpin size="small" />
            </div>
          </section>

          <NSpace v-if="detailTarget.state === 'dry_run'" justify="end" :size="8">
            <NButton type="error" secondary @click="openDecide(detailTarget, 'rejected')">
              <template #icon>
                <NIcon :component="CloseCircleOutline" />
              </template>
              拒绝
            </NButton>
            <NButton type="success" @click="openDecide(detailTarget, 'approved')">
              <template #icon>
                <NIcon :component="CheckmarkCircleOutline" />
              </template>
              批准
            </NButton>
          </NSpace>
        </div>
      </NDrawerContent>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
.mc-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.mc-list-panel {
  margin-bottom: 16px;
}

.mc-toolbar {
  margin-bottom: 16px;
}

.mc-toolbar__state {
  width: 180px;
}

.mc-toolbar__group {
  width: 180px;
}

.mc-domain-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding-left: 4px;
  border-left: 1px solid var(--om-border);
  margin-left: 6px;
}

.mc-decide__line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 13px;
}

.mc-decide__label {
  flex-shrink: 0;
  width: 90px;
  color: var(--om-text-2);
}

.mc-decide__value {
  color: var(--om-text-1);
}

.mc-decide__divider {
  margin: 14px 0;
}

.mc-decide__note {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}

.mc-detail {
  padding: 4px 2px 16px;
}

.mc-detail__section {
  margin-bottom: 22px;
}

.mc-detail__section + .mc-detail__section {
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px dashed var(--om-border);
}

.mc-detail__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mc-detail__title {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.mc-detail__title--with-icon {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mc-detail__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px 18px;
}

.mc-detail__item {
  min-width: 0;
}

.mc-detail__label {
  margin-bottom: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.mc-detail__field {
  margin-bottom: 14px;
}

.mc-detail__text {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.mc-detail__mono {
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  color: var(--om-text-2);
  word-break: break-all;
}

.mc-detail__json {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.mc-detail__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 0;
}

.mc-edit__note {
  margin: 12px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}
</style>

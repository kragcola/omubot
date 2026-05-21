<script setup lang="ts">
import {
  ChatbubbleEllipsesOutline,
  CheckmarkCircleOutline,
  FlashOutline,
  RefreshOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import { recordSortOptions } from '../shared/sort'

type StyleStatus = 'pending' | 'approved' | 'rejected' | 'muted'
type StyleScope = 'group' | 'global'
type OutputPolicy = 'allow_use' | 'transform' | 'observe_only'

interface StyleSummary {
  total: number
  pending: number
  approved: number
  rejected: number
  muted: number
  feedback_count: number
  profile_count: number
  enabled_profile_count: number
}

interface StyleExpression {
  expression_id: string
  situation: string
  style: string
  scope: StyleScope
  group_id: string
  status: StyleStatus
  confidence: number
  count: number
  risk_tags: string[]
  output_policy: OutputPolicy
  updated_at: string
  normalization?: NormalizationInfo | null
  meta?: Record<string, any>
}

interface NormalizationInfo {
  cluster_id: string
  item_id?: string
  canonical_text?: string
  normalized_key?: string
  method?: string
  score?: number
  auto_merged?: boolean
  features?: Record<string, any>
}

interface NormalizerItem {
  item_id: string
  raw_text: string
  count: number
  last_seen_at: string
}

interface NormalizerRevision {
  revision_id: string
  action: string
  item_id?: string
  created_at: string
}

interface NormalizerClusterDetail {
  cluster?: Record<string, any>
  items: NormalizerItem[]
  revisions: NormalizerRevision[]
}

interface StyleProfile {
  profile_id: string
  scope: StyleScope
  group_id: string
  version: number
  status: 'draft' | 'enabled' | 'disabled'
  content: string
  risk_notes: string[]
  created_at: string
}

interface StyleFeedback {
  feedback_id: string
  target_type: string
  group_id: string
  rating: 'positive' | 'negative' | 'neutral'
  source: string
  raw_text: string
  context: string
  created_at: string
}

interface StyleExtractGroupResult {
  group_id: string
  scanned: number
  text_scanned: number
  raw_scanned: number
  batches: number
  backlog_raw: number
  backlog_text: number
  has_more: boolean
  extracted: number
  filtered: number
  saved: number
  approved: number
  pending: number
  expression_ids: string[]
  error?: string
}

interface StyleExtractResult {
  ok: boolean
  error?: string
  groups: string[]
  scope: StyleScope
  scanned: number
  text_scanned: number
  raw_scanned: number
  backlog_raw: number
  backlog_text: number
  has_more: boolean
  batch_limit: number
  max_batches: number
  target_text_rows: number
  extracted: number
  filtered: number
  saved: number
  approved: number
  pending: number
  expression_ids: string[]
  per_group: StyleExtractGroupResult[]
}

const message = useMessage()
const loading = ref(false)
const actionLoading = ref(false)

const groupId = ref('')
const statusFilter = ref<StyleStatus | ''>('pending')
const scopeFilter = ref<StyleScope | ''>('')
const sortMode = ref<'default' | 'time'>('default')

const summary = ref<StyleSummary>({
  total: 0,
  pending: 0,
  approved: 0,
  rejected: 0,
  muted: 0,
  feedback_count: 0,
  profile_count: 0,
  enabled_profile_count: 0,
})
const expressions = ref<StyleExpression[]>([])
const profiles = ref<StyleProfile[]>([])
const feedback = ref<StyleFeedback[]>([])
const lastExtractResult = ref<StyleExtractResult | null>(null)
const normalizerDetails = ref<Record<string, NormalizerClusterDetail>>({})

const statusOptions = [
  { label: '待审', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
  { label: '已静音', value: 'muted' },
  { label: '全部', value: '' },
]

const scopeOptions = [
  { label: '全部作用域', value: '' },
  { label: '本群表达', value: 'group' },
  { label: '全局表达', value: 'global' },
]

async function loadAll() {
  loading.value = true
  try {
    const [summaryResp, expressionResp, profileResp, feedbackResp] = await Promise.all([
      api<StyleSummary>('/api/admin/style/summary'),
      api<{ expressions: StyleExpression[] }>('/api/admin/style/expressions', {
        params: {
          status: statusFilter.value,
          scope: scopeFilter.value,
          group_id: groupId.value.trim(),
          sort: sortMode.value,
          limit: 80,
        },
      }),
      api<{ profiles: StyleProfile[] }>('/api/admin/style/profiles', {
        params: {
          group_id: groupId.value.trim(),
          sort: sortMode.value,
          limit: 20,
        },
      }),
      api<{ feedback: StyleFeedback[] }>('/api/admin/style/feedback', {
        params: {
          group_id: groupId.value.trim(),
          sort: sortMode.value,
          limit: 30,
        },
      }),
    ])
    summary.value = summaryResp
    expressions.value = expressionResp.expressions || []
    profiles.value = profileResp.profiles || []
    feedback.value = feedbackResp.feedback || []
  } catch (error) {
    console.error(error)
    message.error('表达学习数据加载失败')
  } finally {
    loading.value = false
  }
}

async function setStatus(item: StyleExpression, status: StyleStatus) {
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/style/expressions/${item.expression_id}/status`,
      {
        method: 'POST',
        body: { status },
      },
    )
    if (!resp.ok) throw new Error(resp.error || '操作失败')
    message.success('表达状态已更新')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '表达状态更新失败')
  } finally {
    actionLoading.value = false
  }
}

async function sendFeedback(item: StyleExpression, rating: 'positive' | 'negative') {
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/style/expressions/${item.expression_id}/feedback`,
      {
        method: 'POST',
        body: { rating },
      },
    )
    if (!resp.ok) throw new Error(resp.error || '操作失败')
    message.success(rating === 'positive' ? '已强化这条表达' : '已降权这条表达')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '反馈记录失败')
  } finally {
    actionLoading.value = false
  }
}

async function loadNormalizerDetail(info?: NormalizationInfo | null) {
  if (!info?.cluster_id || normalizerDetails.value[info.cluster_id]) return
  try {
    const data = await api<NormalizerClusterDetail>(
      `/api/admin/learning-normalizer/clusters/${info.cluster_id}/items`,
    )
    normalizerDetails.value = {
      ...normalizerDetails.value,
      [info.cluster_id]: data,
    }
  } catch (error) {
    console.error(error)
  }
}

function normalizerDetail(info?: NormalizationInfo | null) {
  return info?.cluster_id ? normalizerDetails.value[info.cluster_id] : undefined
}

async function lockNormalizerCluster(item: StyleExpression) {
  const info = item.normalization
  if (!info?.cluster_id) return
  const canonical = window.prompt('锁定代表写法', info.canonical_text || item.situation)
  if (!canonical) return
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/learning-normalizer/clusters/${info.cluster_id}/lock`,
      { method: 'POST', body: { canonical_text: canonical, reason: 'style console lock' } },
    )
    if (!resp.ok) throw new Error(resp.error || '锁定失败')
    delete normalizerDetails.value[info.cluster_id]
    message.success('代表写法已锁定')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '锁定代表写法失败')
  } finally {
    actionLoading.value = false
  }
}

async function splitNormalizerItem(item: StyleExpression) {
  const info = item.normalization
  if (!info?.item_id || !window.confirm('确认把当前写法拆出归一化簇？')) return
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/learning-normalizer/items/${info.item_id}/split`,
      { method: 'POST', body: { reason: 'style console split' } },
    )
    if (!resp.ok) throw new Error(resp.error || '拆分失败')
    if (info.cluster_id) delete normalizerDetails.value[info.cluster_id]
    message.success('已拆出变体')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '拆出变体失败')
  } finally {
    actionLoading.value = false
  }
}

async function undoNormalizerAutoMerge(item: StyleExpression) {
  const info = item.normalization
  await loadNormalizerDetail(info)
  const detail = normalizerDetail(info)
  const revision = detail?.revisions.find(entry => entry.action === 'auto_merge' && entry.item_id === info?.item_id)
    || detail?.revisions.find(entry => entry.action === 'auto_merge')
  if (!info?.cluster_id || !revision || !window.confirm('确认撤销最近一次自动归并？')) return
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/learning-normalizer/revisions/${revision.revision_id}/undo`,
      { method: 'POST' },
    )
    if (!resp.ok) throw new Error(resp.error || '撤销失败')
    delete normalizerDetails.value[info.cluster_id]
    message.success('已撤销自动归并')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '撤销自动归并失败')
  } finally {
    actionLoading.value = false
  }
}

async function runExtract() {
  actionLoading.value = true
  try {
    const resp = await api<StyleExtractResult>('/api/admin/style/extract/run', {
      method: 'POST',
      body: {
        group_id: groupId.value.trim(),
        scope: scopeFilter.value || 'group',
        auto_approve: false,
      },
    })
    lastExtractResult.value = resp
    if (!resp.ok) throw new Error(resp.error || '抽取失败')
    const backlogText = resp.backlog_text || 0
    const moreHint = backlogText > 0 ? `，剩余待扫文本 ${backlogText} 条` : ''
    message.success(
      `读取 ${resp.raw_scanned || resp.scanned} 行，有效文本 ${resp.text_scanned || resp.scanned} 条，候选 ${resp.extracted} 条，保存 ${resp.saved} 条${moreHint}`,
    )
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '表达抽取失败')
  } finally {
    actionLoading.value = false
  }
}

async function generateProfile() {
  const targetGroup = groupId.value.trim()
  if (!targetGroup && scopeFilter.value !== 'global') {
    message.warning('生成群档案前需要填写群号')
    return
  }
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>('/api/admin/style/profiles/generate', {
      method: 'POST',
      body: {
        group_id: targetGroup,
        scope: scopeFilter.value || 'group',
        enable: true,
      },
    })
    if (!resp.ok) throw new Error(resp.error || '生成失败')
    message.success('动态风格档案已生成并启用')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '动态风格档案生成失败')
  } finally {
    actionLoading.value = false
  }
}

async function disableProfile(profile: StyleProfile) {
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/style/profiles/${profile.profile_id}/disable`,
      { method: 'POST', body: {} },
    )
    if (!resp.ok) throw new Error(resp.error || '禁用失败')
    message.success('动态风格档案已禁用')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '动态风格档案禁用失败')
  } finally {
    actionLoading.value = false
  }
}

async function enableProfile(profile: StyleProfile) {
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>(
      `/api/admin/style/profiles/${profile.profile_id}/enable`,
      { method: 'POST', body: {} },
    )
    if (!resp.ok) throw new Error(resp.error || '启用失败')
    message.success(`动态风格档案 v${profile.version} 已启用`)
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '动态风格档案启用失败')
  } finally {
    actionLoading.value = false
  }
}

async function rollbackProfile(profile: StyleProfile) {
  actionLoading.value = true
  try {
    const resp = await api<{ ok: boolean, error?: string }>('/api/admin/style/profiles/rollback', {
      method: 'POST',
      body: {
        scope: profile.scope,
        group_id: profile.group_id,
      },
    })
    if (!resp.ok) throw new Error(resp.error || '回滚失败')
    message.success('动态风格档案已回滚到上一版')
    await loadAll()
  } catch (error: any) {
    message.error(error?.message || '动态风格档案回滚失败')
  } finally {
    actionLoading.value = false
  }
}

function statusType(status: StyleStatus) {
  return status === 'approved' ? 'success' : status === 'pending' ? 'warning' : status === 'muted' ? 'default' : 'error'
}

function policyText(policy: OutputPolicy) {
  return policy === 'allow_use' ? '可参考' : policy === 'transform' ? '需转译' : '只观察'
}

function normalizationLabel(info?: NormalizationInfo | null) {
  if (!info?.cluster_id) return ''
  const method = info.method === 'new_cluster' ? '新簇' : info.method || '归一化'
  const score = Number(info.score || 0)
  const scoreText = score > 0 ? ` · ${Math.round(score * 100)}%` : ''
  return `${method}${scoreText}`
}

function normalizerAutoMergeRevision(item: StyleExpression) {
  const info = item.normalization
  const detail = normalizerDetail(info)
  return detail?.revisions.find(entry => entry.action === 'auto_merge' && entry.item_id === info?.item_id)
    || detail?.revisions.find(entry => entry.action === 'auto_merge')
}

function canUndoNormalizerAutoMerge(item: StyleExpression) {
  if (!item.normalization?.cluster_id) return false
  const detail = normalizerDetail(item.normalization)
  return !detail || Boolean(normalizerAutoMergeRevision(item))
}

onMounted(loadAll)

watch([groupId, statusFilter, scopeFilter, sortMode], () => {
  void loadAll()
})
</script>

<template>
  <AppPage
    title="表达学习"
    description="管理真实表述方式、动态风格档案和反馈信号。表达学习只调整说法，不改核心人设。"
  >
    <template #action>
      <NButton :loading="loading" secondary @click="loadAll">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <div class="style-console">
      <div class="style-console__metrics">
        <MetricCard title="表达样本" :value="summary.total" :icon="SparklesOutline" hint="所有作用域表达" />
        <MetricCard title="待审" :value="summary.pending" :icon="FlashOutline" accent="warning" hint="需要人工判断" />
        <MetricCard title="已通过" :value="summary.approved" :icon="CheckmarkCircleOutline" accent="success" hint="可参与注入" />
        <MetricCard title="动态档案" :value="summary.enabled_profile_count" :icon="ChatbubbleEllipsesOutline" accent="info" hint="当前启用版本" />
      </div>

      <PageToolbar>
        <template #left>
          <NInput v-model:value="groupId" class="style-console__group-input" placeholder="群号，可留空查看全部" clearable />
          <NSelect v-model:value="statusFilter" class="style-console__select" :options="statusOptions" />
          <NSelect v-model:value="scopeFilter" class="style-console__select" :options="scopeOptions" />
          <NSelect v-model:value="sortMode" class="style-console__select" :options="recordSortOptions" />
        </template>
        <template #right>
          <NButton secondary :loading="actionLoading" @click="runExtract">
            手动抽取
          </NButton>
          <NButton type="primary" :loading="actionLoading" @click="generateProfile">
            生成档案
          </NButton>
        </template>
      </PageToolbar>

      <NSpin :show="loading">
        <div class="style-console__grid">
          <section class="style-panel style-panel--main">
            <div class="style-panel__head">
              <div>
                <h2>表达样本</h2>
                <p>按当前筛选展示，可通过、拒绝、静音或反馈好坏。</p>
              </div>
              <NTag round>
                {{ expressions.length }} 条
              </NTag>
            </div>
            <EmptyState
              v-if="!expressions.length"
              compact
              title="暂无表达样本"
              description="可以先手动抽取，或调整筛选条件。"
              :icon="SparklesOutline"
            />
            <div v-else class="expression-list">
              <article v-for="item in expressions" :key="item.expression_id" class="expression-item">
                <div class="expression-item__main">
                  <div class="expression-item__tags">
                    <NTag size="small" :type="statusType(item.status)">
                      {{ item.status }}
                    </NTag>
                    <NTag size="small">
                      {{ item.scope === 'global' ? '全局' : `群 ${item.group_id}` }}
                    </NTag>
                    <NTag size="small" :type="item.output_policy === 'observe_only' ? 'warning' : 'info'">
                      {{ policyText(item.output_policy) }}
                    </NTag>
                  </div>
                  <h3>{{ item.situation }}</h3>
                  <p>{{ item.style }}</p>
                  <div class="expression-item__meta">
                    <span>置信 {{ Math.round(item.confidence * 100) }}%</span>
                    <span>计数 {{ item.count }}</span>
                    <span>更新 {{ item.updated_at }}</span>
                    <span v-if="item.normalization?.cluster_id">归一化 {{ normalizationLabel(item.normalization) }}</span>
                    <span v-if="item.risk_tags.length">风险 {{ item.risk_tags.join(' / ') }}</span>
                  </div>
                  <div v-if="item.normalization?.cluster_id" class="expression-item__normalization">
                    <NTag size="small" round>
                      簇 {{ item.normalization.cluster_id.slice(-6) }}
                    </NTag>
                    <span>代表：{{ item.normalization.canonical_text || item.situation }}</span>
                    <span v-if="item.normalization.auto_merged">自动归并</span>
                  </div>
                  <div
                    v-if="item.normalization?.cluster_id"
                    class="expression-item__normalizer-actions"
                    @mouseenter="loadNormalizerDetail(item.normalization)"
                  >
                    <NButton size="tiny" secondary @click="lockNormalizerCluster(item)">
                      锁定代表
                    </NButton>
                    <NButton size="tiny" secondary @click="splitNormalizerItem(item)">
                      拆出变体
                    </NButton>
                    <NButton
                      size="tiny"
                      secondary
                      :disabled="!canUndoNormalizerAutoMerge(item)"
                      @click="undoNormalizerAutoMerge(item)"
                    >
                      撤销归并
                    </NButton>
                  </div>
                </div>
                <div class="expression-item__actions">
                  <NButton size="small" secondary @click="setStatus(item, 'approved')">
                    通过
                  </NButton>
                  <NButton size="small" secondary @click="setStatus(item, 'rejected')">
                    拒绝
                  </NButton>
                  <NButton size="small" secondary @click="setStatus(item, 'muted')">
                    静音
                  </NButton>
                  <NButton size="small" quaternary @click="sendFeedback(item, 'positive')">
                    好
                  </NButton>
                  <NButton size="small" quaternary @click="sendFeedback(item, 'negative')">
                    坏
                  </NButton>
                </div>
              </article>
            </div>
          </section>

          <aside class="style-console__side">
            <section class="style-panel">
              <div class="style-panel__head">
                <div>
                  <h2>最近抽取</h2>
                  <p>显示有效文本、原始行、候选和剩余待扫量。</p>
                </div>
              </div>
              <EmptyState
                v-if="!lastExtractResult"
                compact
                title="暂无抽取结果"
                description="点击手动抽取后，这里会显示每个群是否被扫描。"
                :icon="FlashOutline"
              />
              <div v-else class="extract-result">
                <div class="extract-result__totals">
                  <NTag size="small">
                    {{ lastExtractResult.scope === 'global' ? '全局' : '本群' }}
                  </NTag>
                  <span>群 {{ lastExtractResult.groups.length }}</span>
                  <span>有效文本 {{ lastExtractResult.text_scanned ?? lastExtractResult.scanned }}</span>
                  <span>原始行 {{ lastExtractResult.raw_scanned ?? lastExtractResult.scanned }}</span>
                  <span v-if="lastExtractResult.backlog_text">待扫文本 {{ lastExtractResult.backlog_text }}</span>
                  <span>候选 {{ lastExtractResult.extracted }}</span>
                  <span>过滤 {{ lastExtractResult.filtered }}</span>
                  <span>保存 {{ lastExtractResult.saved }}</span>
                </div>
                <div class="extract-group-list">
                  <article
                    v-for="item in lastExtractResult.per_group"
                    :key="item.group_id"
                    class="extract-group-item"
                  >
                    <div class="extract-group-item__head">
                      <NTag size="small">
                        群 {{ item.group_id }}
                      </NTag>
                      <NTag v-if="item.error" size="small" type="error">
                        失败
                      </NTag>
                      <NTag v-else-if="item.saved > 0" size="small" type="success">
                        已保存
                      </NTag>
                      <NTag v-else-if="item.has_more" size="small" type="warning">
                        仍有待扫
                      </NTag>
                      <NTag v-else size="small" type="default">
                        无候选
                      </NTag>
                    </div>
                    <div class="extract-group-item__metrics">
                      <span>有效文本 {{ item.text_scanned ?? item.scanned }}</span>
                      <span>原始行 {{ item.raw_scanned ?? item.scanned }}</span>
                      <span>批次 {{ item.batches || 1 }}</span>
                      <span>候选 {{ item.extracted }}</span>
                      <span v-if="item.filtered">过滤 {{ item.filtered }}</span>
                      <span>保存 {{ item.saved }}</span>
                      <span v-if="item.pending">待审 {{ item.pending }}</span>
                      <span v-if="item.approved">通过 {{ item.approved }}</span>
                    </div>
                    <p v-if="item.has_more">
                      本群仍有 {{ item.backlog_text }} 条有效文本待扫（原始行 {{ item.backlog_raw }}）。
                    </p>
                    <p v-if="item.error">{{ item.error }}</p>
                  </article>
                </div>
              </div>
            </section>

            <section class="style-panel">
              <div class="style-panel__head">
                <div>
                  <h2>动态风格档案</h2>
                  <p>生成后自动启用，可启用旧版、回滚或禁用。</p>
                </div>
              </div>
              <EmptyState
                v-if="!profiles.length"
                compact
                title="暂无档案"
                description="通过已审核表达生成。"
                :icon="ChatbubbleEllipsesOutline"
              />
              <div v-else class="profile-list">
                <article v-for="profile in profiles" :key="profile.profile_id" class="profile-item">
                  <div class="profile-item__head">
                    <NTag :type="profile.status === 'enabled' ? 'success' : 'default'" size="small">
                      v{{ profile.version }} · {{ profile.status }}
                    </NTag>
                    <div class="profile-item__actions">
                      <NButton
                        v-if="profile.status !== 'enabled'"
                        size="tiny"
                        quaternary
                        @click="enableProfile(profile)"
                      >
                        启用
                      </NButton>
                      <NButton
                        v-if="profile.status === 'enabled'"
                        size="tiny"
                        quaternary
                        @click="rollbackProfile(profile)"
                      >
                        回滚
                      </NButton>
                      <NButton
                        v-if="profile.status === 'enabled'"
                        size="tiny"
                        quaternary
                        @click="disableProfile(profile)"
                      >
                        禁用
                      </NButton>
                    </div>
                  </div>
                  <p>{{ profile.content }}</p>
                  <div class="expression-item__meta">
                    <span>创建 {{ profile.created_at }}</span>
                  </div>
                </article>
              </div>
            </section>

            <section class="style-panel">
              <div class="style-panel__head">
                <div>
                  <h2>反馈记录</h2>
                  <p>人工反馈与 bot 回复弱信号。</p>
                </div>
              </div>
              <EmptyState
                v-if="!feedback.length"
                compact
                title="暂无反馈"
                description="反馈会用于后续反思和档案治理。"
              />
              <div v-else class="feedback-list">
                <article v-for="item in feedback" :key="item.feedback_id" class="feedback-item">
                  <div class="feedback-item__head">
                    <NTag size="small">
                      {{ item.rating }}
                    </NTag>
                    <span>{{ item.source }}</span>
                    <span>{{ item.created_at }}</span>
                  </div>
                  <p>{{ item.raw_text || item.context || item.target_type }}</p>
                </article>
              </div>
            </section>
          </aside>
        </div>
      </NSpin>
    </div>
  </AppPage>
</template>

<style scoped>
.style-console {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.style-console__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.style-console__group-input {
  width: 220px;
}

.style-console__select {
  width: 150px;
}

.style-console__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 0.42fr);
  gap: 18px;
  align-items: start;
}

.style-console__side {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.style-panel {
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
  padding: 18px;
}

.style-panel--main {
  min-width: 0;
}

.style-panel__head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.style-panel__head h2 {
  margin: 0;
  color: var(--om-text-1);
  font-size: 17px;
  font-weight: 650;
}

.style-panel__head p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
}

.expression-list,
.profile-list,
.feedback-list,
.extract-group-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.expression-item,
.profile-item,
.feedback-item,
.extract-group-item {
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface);
  padding: 14px;
}

.expression-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
}

.expression-item__tags,
.expression-item__meta,
.expression-item__actions,
.profile-item__head,
.profile-item__actions,
.feedback-item__head,
.extract-result__totals,
.extract-group-item__head,
.extract-group-item__metrics {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.expression-item h3 {
  margin: 10px 0 6px;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 650;
}

.expression-item p,
.profile-item p,
.feedback-item p,
.extract-group-item p {
  margin: 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.expression-item__meta {
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.expression-item__normalization {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.expression-item__normalizer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.expression-item__actions {
  justify-content: flex-end;
  align-content: flex-start;
  max-width: 210px;
}

.profile-item__head,
.feedback-item__head {
  justify-content: space-between;
  margin-bottom: 8px;
}

.feedback-item__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.extract-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.extract-result__totals,
.extract-group-item__metrics {
  color: var(--om-text-3);
  font-size: 12px;
}

.extract-group-item__head {
  justify-content: space-between;
  margin-bottom: 8px;
}

@media (max-width: 1100px) {
  .style-console__metrics,
  .style-console__grid {
    grid-template-columns: 1fr;
  }

  .expression-item {
    grid-template-columns: 1fr;
  }

  .expression-item__actions {
    justify-content: flex-start;
    max-width: none;
  }
}
</style>

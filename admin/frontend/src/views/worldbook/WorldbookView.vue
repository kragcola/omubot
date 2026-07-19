<script setup lang="ts">
import {
  BookOutline,
  RefreshOutline,
  LayersOutline,
  HourglassOutline,
  GitBranchOutline,
  AnalyticsOutline,
  AlbumsOutline,
} from '@vicons/ionicons5'
import { NButton, NIcon, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

import {
  fetchWorldbookSnapshot,
  formatTimestamp,
  gateMetricRows,
  lifecycleStatusTone,
  reasonLabel,
  unavailableCopy,
  type WorldbookArcSummary,
  type WorldbookBlockTrace,
  type WorldbookCommit,
  type WorldbookDecision,
  type WorldbookLifeItem,
  type WorldbookLoadState,
  type WorldbookProposal,
  type WorldbookSnapshot,
} from '../../api/worldbook'

const msg = useMessage()
const loadState = ref<WorldbookLoadState>('idle')
const loading = ref(false)
const snapshot = ref<WorldbookSnapshot | null>(null)
const lastFetchedAt = ref<string | null>(null)
const lastError = ref<string | null>(null)

const isUnavailable = computed(
  () => loadState.value === 'unavailable' || (snapshot.value != null && !snapshot.value.available),
)
const unavailable = computed(() => unavailableCopy(snapshot.value?.reason ?? lastError.value))
const gateRows = computed(() => gateMetricRows(snapshot.value?.gates ?? null))
const ledger = computed(() => snapshot.value?.ledger ?? null)
const life = computed(() => snapshot.value?.life ?? null)
const lifecycle = computed(() => snapshot.value?.lifecycle ?? null)
const registry = computed(() => snapshot.value?.registry ?? null)
const traces = computed(() => snapshot.value?.block_traces ?? [])
const shadow = computed(() => snapshot.value?.shadow ?? null)
const readableTagColor = {
  color: 'var(--om-surface-3)',
  borderColor: 'var(--om-border-strong)',
  textColor: 'var(--om-text-1)',
}

const statusLine = computed(() => {
  if (loadState.value === 'loading' && !snapshot.value) return '加载中…'
  if (loadState.value === 'error') return `错误：${lastError.value ?? '请求失败'}`
  if (isUnavailable.value) return `不可用：${reasonLabel(snapshot.value?.reason)}`
  if (snapshot.value?.available) return `可用 · ${reasonLabel(snapshot.value.reason)}`
  return '等待刷新'
})

function arcLabel(arc: WorldbookArcSummary | null | undefined): string {
  if (!arc) return '—'
  return arc.title?.trim() || arc.arc_id || '—'
}

const lifeColumns: DataTableColumns<WorldbookLifeItem> = [
  { title: 'Key', key: 'key', width: 140, ellipsis: { tooltip: true } },
  { title: 'Value', key: 'value', ellipsis: { tooltip: true } },
  {
    title: 'TTL',
    key: 'expired',
    width: 100,
    render(row) {
      return h(
        NTag,
        {
          size: 'small',
          type: row.expired ? 'warning' : 'success',
          color: readableTagColor,
        },
        () => (row.expired ? '已过期' : '有效'),
      )
    },
  },
  {
    title: 'Decay at',
    key: 'decay_at',
    width: 160,
    render(row) {
      return formatTimestamp(row.decay_at)
    },
  },
  {
    title: 'Source',
    key: 'provenance.source',
    width: 120,
    render(row) {
      return row.provenance?.source || '—'
    },
  },
]

const proposalColumns: DataTableColumns<WorldbookProposal> = [
  { title: 'Proposal', key: 'proposal_id', width: 140, ellipsis: { tooltip: true } },
  { title: 'Kind', key: 'kind', width: 100 },
  {
    title: 'Status',
    key: 'status',
    width: 100,
    render(row) {
      return h(
        NTag,
        { size: 'small', type: lifecycleStatusTone(row.status), color: readableTagColor },
        () => row.status || '—',
      )
    },
  },
  { title: 'Arc', key: 'arc_id', width: 120, ellipsis: { tooltip: true } },
  { title: 'Source', key: 'source', width: 100 },
  {
    title: 'Created',
    key: 'created_at',
    width: 160,
    render(row) {
      return formatTimestamp(row.created_at)
    },
  },
  {
    title: 'Payload keys',
    key: 'payload_key_count',
    width: 100,
  },
]

const decisionColumns: DataTableColumns<WorldbookDecision> = [
  { title: 'Decision', key: 'decision_id', width: 140, ellipsis: { tooltip: true } },
  { title: 'Proposal', key: 'proposal_id', width: 120, ellipsis: { tooltip: true } },
  {
    title: 'Status',
    key: 'status',
    width: 100,
    render(row) {
      return h(
        NTag,
        { size: 'small', type: lifecycleStatusTone(row.status), color: readableTagColor },
        () => row.status || '—',
      )
    },
  },
  { title: 'Reason code', key: 'reason_code', width: 120, ellipsis: { tooltip: true } },
  {
    title: 'Reason',
    key: 'reason',
    ellipsis: { tooltip: true },
  },
  {
    title: 'Decided',
    key: 'decided_at',
    width: 160,
    render(row) {
      return formatTimestamp(row.decided_at)
    },
  },
]

const commitColumns: DataTableColumns<WorldbookCommit> = [
  { title: 'Commit', key: 'commit_id', width: 140, ellipsis: { tooltip: true } },
  { title: 'Event', key: 'event_id', width: 120, ellipsis: { tooltip: true } },
  {
    title: 'Status',
    key: 'status',
    width: 100,
    render(row) {
      return h(
        NTag,
        { size: 'small', type: lifecycleStatusTone(row.status), color: readableTagColor },
        () => row.status || '—',
      )
    },
  },
  { title: 'Arc', key: 'arc_id', width: 120, ellipsis: { tooltip: true } },
  {
    title: 'Committed',
    key: 'committed_at',
    width: 160,
    render(row) {
      return formatTimestamp(row.committed_at)
    },
  },
]

const traceColumns: DataTableColumns<WorldbookBlockTrace> = [
  { title: 'Label', key: 'label', width: 140, ellipsis: { tooltip: true } },
  { title: 'Decision', key: 'decision', width: 100 },
  { title: 'Chars', key: 'char_count', width: 80 },
  { title: 'Priority', key: 'priority', width: 80 },
  { title: 'Hit', key: 'hit_reason', ellipsis: { tooltip: true } },
  { title: 'Budget', key: 'budget_reason', ellipsis: { tooltip: true } },
  {
    title: 'Time',
    key: 'created_at',
    width: 160,
    render(row) {
      return formatTimestamp(row.created_at)
    },
  },
]

async function refresh() {
  loading.value = true
  loadState.value = snapshot.value ? loadState.value : 'loading'
  lastError.value = null
  try {
    const data = await fetchWorldbookSnapshot()
    snapshot.value = data
    lastFetchedAt.value = new Date().toISOString()
    loadState.value = data.available ? 'ready' : 'unavailable'
  }
  catch (error: unknown) {
    const message = error instanceof Error ? error.message : '快照请求失败'
    lastError.value = message
    loadState.value = 'error'
    msg.error(message)
  }
  finally {
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <AppPage
    class="wb-console"
    title="世界书 / Living Story"
    eyebrow="Worldbook"
    description="只读观察 Living Story 运行态：门控、故事栈、Life TTL、Dream 生命周期与 shadow 结论。"
  >
    <template #action>
      <NButton class="wb-refresh" secondary :loading="loading" @click="refresh">
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新
      </NButton>
    </template>

    <div class="wb-metrics">
      <MetricCard
        v-for="row in gateRows"
        :key="row.title"
        :title="row.title"
        :value="row.value"
        :hint="row.hint"
        :accent="row.accent"
        :icon="BookOutline"
      />
    </div>

    <PageToolbar class="mt-16">
      <template #left>
        <span class="wb-toolbar__status" aria-live="polite">{{ statusLine }}</span>
      </template>
      <template #right>
        <span class="wb-toolbar__stamp">
          刷新于 {{ formatTimestamp(lastFetchedAt) }}
        </span>
      </template>
    </PageToolbar>

    <!-- Error -->
    <AppPanelSection
      v-if="loadState === 'error'"
      class="mt-16"
      eyebrow="状态"
      title="快照加载失败"
      description="直达路由保持可用；本页不会重定向。"
    >
      <EmptyState
        :icon="BookOutline"
        title="无法读取世界书快照"
        :description="lastError || '网络或鉴权错误。可点击右上角刷新重试。'"
        compact
      />
    </AppPanelSection>

    <!-- Loading first paint -->
    <AppPanelSection
      v-else-if="loadState === 'loading' && !snapshot"
      class="mt-16"
      eyebrow="状态"
      title="加载中"
    >
      <EmptyState
        :icon="HourglassOutline"
        title="正在拉取快照"
        description="GET /api/admin/worldbook/snapshot"
        compact
      />
    </AppPanelSection>

    <!-- Plugin unavailable / disabled -->
    <AppPanelSection
      v-else-if="isUnavailable"
      class="mt-16"
      eyebrow="插件"
      title="世界书插件不可用"
      :description="reasonLabel(snapshot?.reason)"
    >
      <EmptyState
        :icon="BookOutline"
        :title="unavailable.title"
        :description="unavailable.description"
      />
    </AppPanelSection>

    <!-- Available console body -->
    <template v-else-if="snapshot?.available">
      <!-- Story stack -->
      <AppPanelSection
        class="mt-16"
        eyebrow="Ledger"
        title="活跃故事栈"
        description="main / side / ambient 分层（只读元数据，不含正文）。"
      >
        <template #aside>
          <NTag size="small" :bordered="false" :color="readableTagColor">
            main {{ ledger?.main_count ?? 0 }}
            · side {{ ledger?.side_count ?? 0 }}
            · ambient {{ ledger?.ambient_count ?? 0 }}
          </NTag>
        </template>

        <div v-if="ledger?.error" class="wb-inline-note">
          {{ reasonLabel(ledger.error) }}
        </div>

        <div v-else-if="!ledger?.main && !(ledger?.sides?.length) && !(ledger?.ambient?.length)" class="wb-empty-wrap">
          <EmptyState
            :icon="LayersOutline"
            title="故事栈为空"
            description="当前没有 main / side / ambient arc。"
            compact
          />
        </div>

        <div v-else class="wb-stack">
          <AppCard bordered class="wb-stack__card">
            <p class="wb-stack__role">
              Main
            </p>
            <p class="wb-stack__title">
              {{ arcLabel(ledger?.main) }}
            </p>
            <p v-if="ledger?.main" class="wb-stack__meta">
              {{ ledger.main.stage || '—' }}
              · {{ ledger.main.status || '—' }}
              · goals {{ ledger.main.goal_count }}
              · threads {{ ledger.main.open_thread_count }}
            </p>
            <p v-else class="wb-stack__meta">
              无主线
            </p>
          </AppCard>

          <div class="wb-stack__cols">
            <div class="wb-stack__col">
              <p class="wb-stack__role">
                Side
              </p>
              <div v-if="!(ledger?.sides?.length)" class="wb-stack__meta">
                无侧线
              </div>
              <AppCard
                v-for="arc in ledger?.sides ?? []"
                :key="arc.arc_id || arc.title"
                bordered
                embedded
                class="wb-stack__mini"
              >
                <p class="wb-stack__title wb-stack__title--sm">
                  {{ arcLabel(arc) }}
                </p>
                <p class="wb-stack__meta">
                  {{ arc.stage || '—' }} · {{ arc.status || '—' }}
                </p>
              </AppCard>
            </div>
            <div class="wb-stack__col">
              <p class="wb-stack__role">
                Ambient
              </p>
              <div v-if="!(ledger?.ambient?.length)" class="wb-stack__meta">
                无氛围线
              </div>
              <AppCard
                v-for="arc in ledger?.ambient ?? []"
                :key="arc.arc_id || arc.title"
                bordered
                embedded
                class="wb-stack__mini"
              >
                <p class="wb-stack__title wb-stack__title--sm">
                  {{ arcLabel(arc) }}
                </p>
                <p class="wb-stack__meta">
                  {{ arc.stage || '—' }} · {{ arc.status || '—' }}
                </p>
              </AppCard>
            </div>
          </div>
        </div>
      </AppPanelSection>

      <!-- Life TTL -->
      <AppPanelSection
        class="mt-16"
        eyebrow="Life"
        title="Life TTL"
        :description="life
          ? `revision ${life.revision} · active ${life.active_count} · expired ${life.expired_count} · applied events ${life.applied_event_id_count}`
          : 'Life 状态不可用'"
      >
        <div v-if="life?.error" class="wb-inline-note">
          {{ reasonLabel(life.error) }}
        </div>
        <div v-else-if="!(life?.items?.length)" class="wb-empty-wrap">
          <EmptyState
            :icon="HourglassOutline"
            title="暂无 Life 条目"
            description="没有可展示的 self-state 键值。"
            compact
          />
        </div>
        <NDataTable
          v-else
          size="small"
          :bordered="false"
          :columns="lifeColumns"
          :data="life?.items ?? []"
          :row-key="(row: WorldbookLifeItem) => row.key"
        />
      </AppPanelSection>

      <!-- Dream lifecycle -->
      <AppPanelSection
        class="mt-16"
        eyebrow="Dream"
        title="提案 / 决策 / 提交"
        :description="lifecycle
          ? `proposals ${lifecycle.proposal_count} · decisions ${lifecycle.decision_count} · commits ${lifecycle.commit_count}`
          : '生命周期不可用'"
      >
        <template #aside>
          <NIcon :component="GitBranchOutline" :size="18" class="wb-section-icon" />
        </template>

        <div class="wb-lifecycle">
          <div>
            <p class="wb-subhead">
              Proposals
            </p>
            <div v-if="!(lifecycle?.proposals?.length)" class="wb-empty-wrap">
              <EmptyState
                title="无提案"
                description="尚未产生 Dream proposal。"
                compact
              />
            </div>
            <NDataTable
              v-else
              size="small"
              :bordered="false"
              :columns="proposalColumns"
              :data="lifecycle?.proposals ?? []"
              :row-key="(row: WorldbookProposal) => row.proposal_id"
            />
          </div>

          <div>
            <p class="wb-subhead">
              Decisions
            </p>
            <div v-if="!(lifecycle?.decisions?.length)" class="wb-empty-wrap">
              <EmptyState
                title="无决策"
                description="尚无 accept / reject 记录。"
                compact
              />
            </div>
            <NDataTable
              v-else
              size="small"
              :bordered="false"
              :columns="decisionColumns"
              :data="lifecycle?.decisions ?? []"
              :row-key="(row: WorldbookDecision) => row.decision_id"
            />
          </div>

          <div>
            <p class="wb-subhead">
              Commits
            </p>
            <div v-if="!(lifecycle?.commits?.length)" class="wb-empty-wrap">
              <EmptyState
                title="无提交"
                description="尚无 commit 记录。"
                compact
              />
            </div>
            <NDataTable
              v-else
              size="small"
              :bordered="false"
              :columns="commitColumns"
              :data="lifecycle?.commits ?? []"
              :row-key="(row: WorldbookCommit) => row.commit_id"
            />
          </div>
        </div>
      </AppPanelSection>

      <!-- Traces + shadow -->
      <div class="wb-split mt-16">
        <AppPanelSection
          eyebrow="Traces"
          title="最近 Worldbook Traces"
          :description="`共 ${traces.length} 条（source/provider=worldbook）`"
        >
          <template #aside>
            <NIcon :component="AnalyticsOutline" :size="18" class="wb-section-icon" />
          </template>
          <div v-if="!traces.length" class="wb-empty-wrap">
            <EmptyState
              :icon="AnalyticsOutline"
              title="暂无 worldbook trace"
              description="尚无 worldbook 相关 block_trace 记录。"
              compact
            />
          </div>
          <NDataTable
            v-else
            size="small"
            :bordered="false"
            :columns="traceColumns"
            :data="traces"
            :row-key="(row: WorldbookBlockTrace) => row.trace_id || row.request_id + row.created_at"
          />
        </AppPanelSection>

        <AppPanelSection
          eyebrow="Shadow"
          title="Shadow Verdict"
          :description="shadow ? reasonLabel(shadow.reason) : '无 shadow 数据'"
        >
          <div v-if="!shadow" class="wb-empty-wrap">
            <EmptyState
              title="无 shadow 状态"
              description="快照未返回 shadow 字段。"
              compact
            />
          </div>
          <div v-else-if="shadow.status !== 'ok' || !shadow.content" class="wb-empty-wrap">
            <EmptyState
              title="Shadow 不可用"
              :description="`${shadow.status} · ${reasonLabel(shadow.reason)}`"
              compact
            />
          </div>
          <div v-else class="wb-shadow">
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Verdict</span>
              <NTag
                size="small"
                :type="lifecycleStatusTone(shadow.content.overall_verdict)"
                :color="readableTagColor"
              >
                {{ shadow.content.overall_verdict || '—' }}
              </NTag>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Steps</span>
              <span>{{ shadow.content.step_count }}</span>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Invariant</span>
              <span>{{ shadow.content.invariant_verdict ?? '—' }}</span>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Pack</span>
              <span class="wb-mono">{{ shadow.content.pack_id || '—' }}</span>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Hash</span>
              <span class="wb-mono">{{ shadow.content.report_essential_hash || '—' }}</span>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Storylets selected</span>
              <span>{{ shadow.content.selected_storylet_count }}</span>
            </div>
            <div class="wb-shadow__row">
              <span class="wb-shadow__label">Main arc</span>
              <span class="wb-mono">{{ shadow.content.continuity?.main_arc_id ?? '—' }}</span>
            </div>
          </div>
        </AppPanelSection>
      </div>

      <!-- Registry -->
      <AppPanelSection
        class="mt-16"
        eyebrow="Registry"
        title="注册表元数据"
        :description="registry
          ? `runtime_loaded=${registry.runtime_loaded} · canon ${registry.canon.count} · storylets ${registry.storylets.count}`
          : '注册表不可用'"
      >
        <template #aside>
          <NIcon :component="AlbumsOutline" :size="18" class="wb-section-icon" />
        </template>

        <div v-if="!registry" class="wb-empty-wrap">
          <EmptyState title="无注册表" description="快照未返回 registry。" compact />
        </div>
        <div v-else class="wb-registry">
          <AppCard bordered embedded class="wb-registry__card">
            <p class="wb-subhead">
              Canon · {{ registry.canon.loaded ? 'loaded' : 'unloaded' }} · {{ registry.canon.count }}
            </p>
            <div v-if="!registry.canon.entries.length" class="wb-stack__meta">
              无 canon 条目（或未加载）
            </div>
            <ul v-else class="wb-registry__list">
              <li
                v-for="entry in registry.canon.entries"
                :key="entry.entry_id"
                class="wb-registry__item"
              >
                <span class="wb-registry__name">{{ entry.title || entry.entry_id }}</span>
                <span class="wb-stack__meta">
                  p{{ entry.priority }}
                  · kw {{ entry.keyword_count }}
                  · alias {{ entry.alias_count }}
                  <template v-if="entry.always_active">
                    · always
                  </template>
                </span>
              </li>
            </ul>
          </AppCard>
          <AppCard bordered embedded class="wb-registry__card">
            <p class="wb-subhead">
              Storylets · {{ registry.storylets.loaded ? 'loaded' : 'unloaded' }} · {{ registry.storylets.count }}
            </p>
            <div v-if="!registry.storylets.entries.length" class="wb-stack__meta">
              无 storylet 条目（或未加载）
            </div>
            <ul v-else class="wb-registry__list">
              <li
                v-for="entry in registry.storylets.entries"
                :key="entry.storylet_id"
                class="wb-registry__item"
              >
                <span class="wb-registry__name">{{ entry.title || entry.storylet_id }}</span>
                <span class="wb-stack__meta">
                  p{{ entry.priority }}
                  · sal {{ entry.saliency }}
                  · {{ entry.severity || '—' }}
                  <template v-if="entry.once">
                    · once
                  </template>
                </span>
              </li>
            </ul>
          </AppCard>
        </div>

        <div v-if="snapshot.gates" class="wb-paths mt-16">
          <p class="wb-subhead">
            路径 / 预算
          </p>
          <div class="wb-paths__grid">
            <span class="wb-stack__meta">state_dir</span>
            <span class="wb-mono">{{ snapshot.gates.state_dir || '—' }}</span>
            <span class="wb-stack__meta">canon_dir</span>
            <span class="wb-mono">{{ snapshot.gates.canon_dir || '—' }}</span>
            <span class="wb-stack__meta">storylet_dir</span>
            <span class="wb-mono">{{ snapshot.gates.storylet_dir || '—' }}</span>
            <span class="wb-stack__meta">max_setbacks / tick</span>
            <span>
              {{ snapshot.gates.max_setbacks_per_arc }} / {{ snapshot.gates.max_events_per_tick }}
            </span>
          </div>
        </div>
      </AppPanelSection>
    </template>
  </AppPage>
</template>

<style scoped>
.wb-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.wb-refresh {
  min-width: 44px;
  min-height: 44px;
}

.wb-console :deep(.om-panel-section__eyebrow) {
  color: var(--om-text-2);
}

.wb-toolbar__status,
.wb-toolbar__stamp {
  font-size: 13px;
  color: var(--om-text-2);
}

.wb-toolbar__stamp {
  color: var(--om-text-2);
  font-variant-numeric: tabular-nums;
}

.wb-section-icon {
  color: var(--om-text-2);
}

.wb-inline-note {
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 13px;
}

.wb-empty-wrap {
  padding: 8px 0;
}

.wb-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.wb-stack__card {
  padding: 16px;
}

.wb-stack__cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.wb-stack__col {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wb-stack__mini {
  padding: 12px;
}

.wb-stack__role {
  margin: 0 0 4px;
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--om-text-2);
}

.wb-stack__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--om-text-1);
}

.wb-stack__title--sm {
  font-size: 14px;
}

.wb-stack__meta {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--om-text-2);
}

.wb-lifecycle {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.wb-subhead {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--om-text-2);
}

.wb-split {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 16px;
}

.wb-shadow {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wb-shadow__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
  font-size: 13px;
  color: var(--om-text-1);
}

.wb-shadow__label {
  color: var(--om-text-2);
  flex-shrink: 0;
}

.wb-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
  color: var(--om-text-2);
}

.wb-registry {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.wb-registry__card {
  padding: 16px;
}

.wb-registry__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
}

.wb-registry__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
}

.wb-registry__name {
  font-size: 13px;
  color: var(--om-text-1);
}

.wb-paths__grid {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px 12px;
  align-items: start;
}

@media (max-width: 900px) {
  .wb-metrics {
    grid-template-columns: 1fr 1fr;
  }

  .wb-stack__cols,
  .wb-split,
  .wb-registry {
    grid-template-columns: 1fr;
  }

  .wb-paths__grid {
    grid-template-columns: 1fr;
  }
}
</style>

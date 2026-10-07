<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NSelect, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import { extractGraph, graphObjects, graphReview, graphSources, graphTransition, graphWalk, proposeAlias, proposeRelation, type GraphKind, type GraphObject } from '@/api/graph'
import type { GraphExtractView, GraphVocabulary, GraphHealthCounts, GraphObservationView, GraphObjectPageView, GraphProjectionView, KnowledgeChunkView } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'
import { graphSelfPropose, graphSelfRelation, graphSelfReview, graphSelfSource, graphSelfTransition } from '@/api/graph'
import type { GraphSelfFactSourceView, GraphSelfFactView } from '@/api/generated'

const group = ref('')
const loadedGroup = ref('')
const kind = ref<GraphKind>('relation')
const page = ref<GraphObjectPageView | null>(null)
const query = ref('')
const sources = ref<KnowledgeChunkView[]>([])
const selectedChunk = ref('')
const confirmed = ref(false)
const extractDraft = reactive({ runId: '', vocabulary: '' })
const personalDraft = reactive({ factId: '', relationId: '', targetId: '' })
const personalSource = ref<GraphSelfFactSourceView | null>(null)
const personalRow = ref<GraphSelfFactView | null>(null)
const personalNonSensitive = ref(false)
const personalNonPerson = ref(false)
const personalNeedsReload = ref(false)
const extraction = ref<GraphExtractView | null>(null)
const busy = ref(false)
const needsReload = ref(false)
const error = ref('')
const notice = ref('')
const seeds = ref('')
const projection = ref<GraphProjectionView | null>(null)
const health = ref<GraphObservationView | null>(null)
const healthError = ref('')
const healthStale = ref(false)
const draft = reactive({ id: '', subject: '', predicate: '', target: '', entity: '', alias: '' })
const reviewLabel = { pending: '待审核', approved: '已批准', rejected: '已拒绝' }
const statusLabel = { inactive: '未生效', active: '生效中', ambiguous: '有歧义', revoked: '已撤销' }
const kindOptions = [{ label: '概念关系', value: 'relation' }, { label: '概念别名', value: 'alias' }]
const healthMetrics: { field: keyof GraphHealthCounts; label: string }[] = [
  { field: 'scanned', label: '已检查对象' },
  { field: 'pending', label: '待审核' },
  { field: 'approved', label: '已批准' },
  { field: 'rejected', label: '已拒绝' },
  { field: 'inactive', label: '未生效' },
  { field: 'active', label: '生效中' },
  { field: 'ambiguous', label: '有歧义' },
  { field: 'revoked', label: '已撤销' },
  { field: 'current_primary_valid', label: '当前来源有效' },
  { field: 'current_primary_invalid', label: '当前来源失效' },
  { field: 'active_primary_valid', label: '生效对象来源有效' },
  { field: 'active_primary_invalid', label: '生效对象来源失效' },
]
function healthCount(value: number | undefined): string {
  return value === undefined ? '未知' : new Intl.NumberFormat('zh-CN').format(value)
}

let sequence = 0
let controller: AbortController | null = null
let disposed = false

function clearSensitive() {
  sequence += 1
  controller?.abort()
  controller = null
  loadedGroup.value = ''
  page.value = null
  sources.value = []
  selectedChunk.value = ''
  confirmed.value = false
  extraction.value = null
  personalSource.value = null
  personalRow.value = null
  personalNonSensitive.value = false
  personalNonPerson.value = false
  Object.assign(personalDraft, { factId: '', relationId: '', targetId: '' })
  Object.assign(extractDraft, { runId: '', vocabulary: '' })
  projection.value = null
  health.value = null
  healthError.value = ''
  healthStale.value = false
  query.value = ''
  seeds.value = ''
  Object.assign(draft, { id: '', subject: '', predicate: '', target: '', entity: '', alias: '' })
  needsReload.value = false
  personalNeedsReload.value = false
  busy.value = false
  error.value = ''
  notice.value = ''
}
watch(() => group.value.trim(), target => { if (target !== loadedGroup.value) clearSensitive() })
watch(kind, clearSensitive)
watch(() => personalDraft.factId, () => {
  personalSource.value = null
  personalRow.value = null
  personalNonSensitive.value = false
  personalNonPerson.value = false
})
watch(() => personalDraft.relationId, () => { personalRow.value = null })
watch(() => sessionState.generation, clearSensitive)
watch(() => sessionState.adminAuthenticated, authenticated => { if (!authenticated) clearSensitive() })
onBeforeUnmount(() => { disposed = true; clearSensitive() })

const selectedSource = computed(() => sources.value.find(item => item.source.chunk_id === selectedChunk.value) ?? null)
const ready = computed(() => sessionState.adminAuthenticated && !busy.value && !needsReload.value
  && page.value !== null && group.value.trim() === loadedGroup.value)
const canPropose = computed(() => ready.value && confirmed.value && selectedSource.value !== null
  && selectedSource.value.source.scope.group_id === loadedGroup.value && Boolean(draft.id.trim())
  && (kind.value === 'relation' ? Boolean(draft.subject.trim() && draft.predicate.trim() && draft.target.trim())
    : Boolean(draft.entity.trim() && draft.alias.trim())))
const canExtract = computed(() => ready.value && kind.value === 'relation' && confirmed.value
  && selectedSource.value !== null && selectedSource.value.source.scope.group_id === loadedGroup.value
  && Array.from(selectedSource.value.body).length >= 8 && Array.from(selectedSource.value.body).length <= 240
  && /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(extractDraft.runId.trim())
  && Boolean(extractDraft.vocabulary.trim()))
const personalReady = computed(() => sessionState.adminAuthenticated && !busy.value && Boolean(group.value.trim()))
const personalSourceCurrent = computed(() => personalReady.value && personalSource.value !== null
  && personalSource.value.source.scope.group_id === group.value.trim()
  && personalSource.value.source.fact_id === personalDraft.factId.trim())
const canProposePersonal = computed(() => personalSourceCurrent.value && !personalNeedsReload.value
  && personalNonSensitive.value && personalNonPerson.value
  && /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(personalDraft.relationId.trim())
  && /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(personalDraft.targetId.trim()))
const canActPersonal = computed(() => personalReady.value && !personalNeedsReload.value && personalRow.value !== null
  && personalRow.value.scope.group_id === group.value.trim()
  && personalRow.value.relation_id === personalDraft.relationId.trim())
const canApprovePersonal = computed(() => canActPersonal.value && personalSourceCurrent.value
  && personalNonSensitive.value && personalNonPerson.value
  && JSON.stringify(personalRow.value?.source.fact) === JSON.stringify(personalSource.value?.source))
function objectId(item: GraphObject) { return 'relation_id' in item ? item.relation_id : item.alias_id }
function objectLabel(item: GraphObject) {
  return 'relation_id' in item ? `${item.subject_id} — ${item.predicate} → ${item.target_id}` : `${item.alias} → ${item.entity_id}`
}
function canAct(item: GraphObject) {
  return ready.value && item.scope.group_id === loadedGroup.value
    && page.value?.items.some(row => objectId(row) === objectId(item) && row.revision === item.revision)
}
function canApprove(item: GraphObject) {
  return canAct(item) && confirmed.value && selectedSource.value !== null
    && JSON.stringify(selectedSource.value.source) === JSON.stringify(item.source)
}
function chooseSource(chunk: KnowledgeChunkView) {
  if (!ready.value || !sources.value.includes(chunk)) return
  selectedChunk.value = chunk.source.chunk_id
  extraction.value = null
  confirmed.value = false
}
function begin() {
  controller?.abort()
  controller = new AbortController()
  const ticket = { sequence: ++sequence, epoch: currentAdminEpoch(), group: group.value.trim(), kind: kind.value, signal: controller.signal }
  busy.value = true
  error.value = ''
  notice.value = ''
  return ticket
}
function current(ticket: ReturnType<typeof begin>) {
  return !disposed && ticket.sequence === sequence && !ticket.signal.aborted
    && isCurrentAdminEpoch(ticket.epoch) && sessionState.adminAuthenticated
    && group.value.trim() === ticket.group && kind.value === ticket.kind
}
function failed(cause: unknown, ticket: ReturnType<typeof begin>, mutation = false, personal = false) {
  if (!current(ticket)) return
  if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clearSensitive(); return }
  if (mutation && isApiError(cause) && cause.status === 409) {
    if (personal) personalNeedsReload.value = true
    else needsReload.value = true
    error.value = personal
      ? '版本冲突；草稿已保留。重新读取本人关系及来源后再提交，页面不会覆盖当前版本。'
      : '版本冲突；草稿已保留。重新读取列表及来源后再提交，页面不会覆盖当前版本。'
  } else error.value = apiErrorMessage(cause)
}
async function load(more = false) {
  if (busy.value || !sessionState.adminAuthenticated || !group.value.trim()
    || (more && (!ready.value || !page.value?.next_cursor))) return
  const cursor = more ? page.value?.next_cursor ?? null : null
  const ticket = begin()
  try {
    if (!more) healthError.value = ''
    const [objectsResult, healthResult] = await Promise.allSettled([
      graphObjects(ticket.group, ticket.kind, cursor, ticket.signal),
      more ? Promise.resolve(null) : apiRequest<GraphObservationView>(
        `/api/admin/graph/health?${new URLSearchParams({ group_id: ticket.group })}`, { signal: ticket.signal },
      ),
    ])
    if (!current(ticket)) return
    if ([objectsResult, healthResult].some(result => result.status === 'rejected' && isApiError(result.reason) && result.reason.status === 401)) {
      expireAdminSession()
      clearSensitive()
      return
    }
    if (objectsResult.status === 'fulfilled') {
      const next = objectsResult.value
      const items = new Map((more ? page.value?.items ?? [] : []).map(row => [objectId(row), row]))
      for (const row of next.items) items.set(objectId(row), row)
      page.value = { ...next, items: [...items.values()] }
      loadedGroup.value = ticket.group
      needsReload.value = false
      if (!more) { extraction.value = null; sources.value = []; selectedChunk.value = ''; confirmed.value = false; projection.value = null }
    } else failed(objectsResult.reason as unknown, ticket)
    if (!more) {
      if (healthResult.status === 'fulfilled' && loadedGroup.value === ticket.group) {
        health.value = healthResult.value
        healthStale.value = false
      } else if (healthResult.status === 'rejected') {
        healthError.value = apiErrorMessage(healthResult.reason as unknown)
        healthStale.value = Boolean(health.value)
      }
    }
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
async function searchSources() {
  if (!ready.value || !query.value.trim()) return
  const ticket = begin()
  sources.value = []
  selectedChunk.value = ''
  confirmed.value = false
  extraction.value = null
  try {
    const result = await graphSources(ticket.group, query.value.trim(), ticket.signal)
    if (current(ticket)) sources.value = result.items
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
async function propose() {
  if (!canPropose.value || !selectedSource.value) return
  const source = selectedSource.value.source
  const ticket = begin()
  try {
    const row = ticket.kind === 'relation'
      ? await proposeRelation({ group_id: ticket.group, expected_revision: 0, value: {
        relation_id: draft.id.trim(), subject_id: draft.subject.trim(), predicate: draft.predicate.trim(),
        target_id: draft.target.trim(), classification: 'non_personal_concept_relation', source,
      } }, ticket.signal)
      : await proposeAlias({ group_id: ticket.group, expected_revision: 0, value: {
        alias_id: draft.id.trim(), entity_id: draft.entity.trim(), alias: draft.alias.trim(),
        classification: 'non_personal_concept_alias', source,
      } }, ticket.signal)
    if (!current(ticket)) return
    page.value = { items: [row, ...(page.value?.items.filter(old => objectId(old) !== objectId(row)) ?? [])],
      next_cursor: page.value?.next_cursor ?? null }
    healthStale.value = Boolean(health.value?.enabled)
    notice.value = '提案已保存；仍需人工审核，再单独应用。'
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function extract() {
  if (!canExtract.value || !selectedSource.value) return
  const source = selectedSource.value.source
  const runId = extractDraft.runId.trim()
  const ticket = begin()
  extraction.value = null
  try {
    const vocabulary = JSON.parse(extractDraft.vocabulary) as GraphVocabulary
    const result = await extractGraph({ group_id: ticket.group, source, run_id: runId, vocabulary }, ticket.signal)
    if (!current(ticket)) return
    extraction.value = result
    if (result.state === 'proposed') {
      const ids = new Set(result.relations.map(row => row.relation_id))
      page.value = { items: [...result.relations, ...(page.value?.items.filter(old => !ids.has(objectId(old))) ?? [])],
        next_cursor: page.value?.next_cursor ?? null }
      healthStale.value = Boolean(health.value?.enabled)
      notice.value = '抽取提案已保存，仍需人工核对证据、审核并单独应用。'
    } else if (result.state === 'disabled') notice.value = '运行版尚未开启文档关系抽取；请在配置中设置允许群并应用运行配置。'
    else if (result.state === 'already_attempted') notice.value = '这次抽取已尝试过。请重新读取对象列表核对结果，页面不会再次调用模型。'
    else notice.value = '本次没有达到条件的提案；未创建图谱对象。'
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function transition(item: GraphObject, action: 'approved' | 'rejected' | 'apply' | 'revoke') {
  if (!canAct(item) || (action === 'approved' && !canApprove(item))) return
  const ticket = begin()
  const body = { group_id: ticket.group, kind: ticket.kind, object_id: objectId(item), expected_revision: item.revision }
  try {
    const row = action === 'approved' || action === 'rejected'
      ? await graphReview({ ...body, decision: action }, ticket.signal)
      : await graphTransition(action, body, ticket.signal)
    if (!current(ticket)) return
    if (page.value) page.value = { ...page.value, items: page.value.items.map(old => objectId(old) === objectId(row) ? row : old) }
    projection.value = null
    healthStale.value = Boolean(health.value?.enabled)
    notice.value = '状态已保存，请核对当前 revision。'
  } catch (cause) { failed(cause, ticket, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function walk() {
  if (!ready.value) return
  const ids = seeds.value.split(/[\s,，]+/).filter(Boolean)
  if (!ids.length) return
  const ticket = begin()
  projection.value = null
  try {
    const result = await graphWalk({ group_id: ticket.group, seeds: ids, max_depth: 2, max_nodes: 32,
      max_edges: 64, limit: 8, deadline_ms: 25 }, ticket.signal)
    if (current(ticket)) projection.value = result
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
async function loadPersonalSource() {
  if (!personalReady.value || !personalDraft.factId.trim()) return
  const factId = personalDraft.factId.trim()
  const ticket = begin()
  personalSource.value = null
  personalNonSensitive.value = false
  personalNonPerson.value = false
  try {
    const result = await graphSelfSource(ticket.group, factId, ticket.signal)
    if (current(ticket) && factId === personalDraft.factId.trim()) personalSource.value = result
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
async function loadPersonalRelation() {
  if (!personalReady.value || !personalDraft.relationId.trim()) return
  const relationId = personalDraft.relationId.trim()
  const ticket = begin()
  personalRow.value = null
  try {
    const result = await graphSelfRelation(ticket.group, relationId, ticket.signal)
    if (current(ticket) && relationId === personalDraft.relationId.trim()) {
      personalRow.value = result
      personalNeedsReload.value = false
    }
  } catch (cause) { failed(cause, ticket) }
  finally { if (current(ticket)) busy.value = false }
}
async function proposePersonal() {
  if (!canProposePersonal.value || !personalSource.value) return
  const source = personalSource.value
  const relationId = personalDraft.relationId.trim()
  const ticket = begin()
  try {
    const result = await graphSelfPropose({ group_id: ticket.group, expected_revision: 0, value: {
      relation_id: relationId, source: source.source, declaration: 'self_non_sensitive_fact',
      fact_classification: 'self_preference', target_id: personalDraft.targetId.trim(),
      target_value: source.value, target_classification: 'non_personal_concept',
    } }, ticket.signal)
    if (current(ticket) && personalSource.value === source && relationId === personalDraft.relationId.trim()) {
      personalRow.value = result
      personalNonSensitive.value = false
      personalNonPerson.value = false
      notice.value = '本人偏好关系已保存为待审核提案；请重新确认来源后批准，再单独应用。'
    }
  } catch (cause) { failed(cause, ticket, true, true) }
  finally { if (current(ticket)) busy.value = false }
}
async function transitionPersonal(action: 'approved' | 'rejected' | 'apply' | 'revoke') {
  if (!canActPersonal.value || !personalRow.value || (action === 'approved' && !canApprovePersonal.value)) return
  const row = personalRow.value
  const ticket = begin()
  const body = { group_id: ticket.group, relation_id: row.relation_id, expected_revision: row.revision }
  try {
    const result = action === 'approved' || action === 'rejected'
      ? await graphSelfReview({ ...body, decision: action, self_non_sensitive_fact: personalNonSensitive.value,
        non_personal_target: personalNonPerson.value }, ticket.signal)
      : await graphSelfTransition(action, body, ticket.signal)
    if (current(ticket) && personalRow.value === row) {
      personalRow.value = result
      notice.value = '本人偏好关系状态已保存。'
    }
  } catch (cause) { failed(cause, ticket, true, true) }
  finally { if (current(ticket)) busy.value = false }
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="概念关系与别名">
    <p class="form-hint">只维护人工确认的非个人概念；先批准并激活知识文档，再搜索并选取精确来源。可以在已开启的允许群中手动抽取文档关系；提案须审核后另行应用。</p>
    <form class="form-row" @submit.prevent="void load()">
      <label for="graph-group">准确群 ID</label>
      <n-input id="graph-group" v-model:value="group" :disabled="busy" />
      <label for="graph-kind">对象类型</label>
      <n-select id="graph-kind" v-model:value="kind" :options="kindOptions" :disabled="busy" />
      <n-button attr-type="submit" :loading="busy" :disabled="!group.trim() || !sessionState.adminAuthenticated">读取当前列表</n-button>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
    <n-alert v-if="notice" type="success" :show-icon="false">{{ notice }}</n-alert>
    <n-alert v-if="needsReload" type="warning" :show-icon="false">当前版本需要重新读取；草稿仍保留。</n-alert>
    <n-alert v-if="personalNeedsReload" type="warning" :show-icon="false">本人关系版本需要重新读取；草稿仍保留。</n-alert>
    <section v-if="sessionState.adminAuthenticated" class="health-section" aria-labelledby="graph-health-heading">
      <h3 id="graph-health-heading">来源健康快照 <n-tag v-if="healthStale" type="warning" size="small">旧数据</n-tag></h3>
      <p class="form-hint">随当前列表首次读取或手动重新读取；分页不重复扫描。快照只检查当前授权群，不修改图谱。</p>
      <n-alert v-if="healthError" type="error" :show-icon="false" role="alert">
        快照暂时无法读取：{{ healthError }}<span v-if="health">上一次读取已标记为旧数据。</span>
      </n-alert>
      <template v-if="health && group.trim() === loadedGroup">
        <n-empty v-if="!health.enabled" description="图谱观测未开启；请在模型配置中开启并应用运行配置。" />
        <template v-else-if="health.report">
          <n-alert v-if="health.report.state === 'partial'" type="warning" :show-icon="false">
            部分扫描：别名与关系合计最多检查 2,048 个对象。以下计数仅覆盖已检查对象；某类为 0 也可能尚未检查，不代表当前群没有此类对象。
          </n-alert>
          <p v-else class="form-hint">本次当前授权范围扫描完成；以下为已检查对象的计数，不代表回复质量。</p>
          <div class="health-table-wrap">
            <table class="health-table" aria-label="图谱已检查对象计数">
              <thead><tr><th scope="col">状态</th><th scope="col">别名</th><th scope="col">关系</th></tr></thead>
              <tbody><tr v-for="metric in healthMetrics" :key="metric.field">
                <th scope="row">{{ metric.label }}</th>
                <td>{{ healthCount(health.report.aliases[metric.field]) }}</td>
                <td>{{ healthCount(health.report.relations[metric.field]) }}</td>
              </tr></tbody>
            </table>
          </div>
        </template>
        <n-empty v-else description="图谱观测已开启，当前没有可用快照。" />
      </template>
      <n-empty v-else-if="!healthError" description="输入准确群 ID 并读取当前列表后查看快照。" />
    </section>
    <template v-if="page && group.trim() === loadedGroup">
      <h3>选取当前知识来源</h3>
      <form class="form-row" @submit.prevent="void searchSources()">
        <label for="graph-source-query">文档关键词</label>
        <n-input id="graph-source-query" v-model:value="query" :disabled="busy" />
        <n-button attr-type="submit" :disabled="!ready || !query.trim()">搜索已批准来源</n-button>
      </form>
      <n-empty v-if="!sources.length" description="没有已选来源；请先在文档知识库批准并激活非个人文档，再用关键词搜索。" />
      <div v-for="chunk in sources" :key="chunk.source.chunk_id" class="source-choice">
        <n-button :disabled="!ready" @click="chooseSource(chunk)">{{ selectedChunk === chunk.source.chunk_id ? '已选：' : '选择：' }}{{ chunk.title }}</n-button>
        <p class="form-hint">{{ chunk.source.source_id }} · revision {{ chunk.source.source_revision }} · 第 {{ chunk.source.start_line }}–{{ chunk.source.end_line }} 行</p>
        <pre class="style-content">{{ chunk.body }}</pre>
      </div>
      <h3>新提案</h3>
      <form class="field-stack" @submit.prevent="void propose()">
        <label for="graph-object-id">稳定对象 ID（新建 revision 0）</label>
        <n-input id="graph-object-id" v-model:value="draft.id" :disabled="busy" />
        <template v-if="kind === 'relation'">
          <label for="graph-subject">主体概念 ID</label><n-input id="graph-subject" v-model:value="draft.subject" :disabled="busy" />
          <label for="graph-predicate">关系 ID</label><n-input id="graph-predicate" v-model:value="draft.predicate" :disabled="busy" />
          <label for="graph-target">目标概念 ID</label><n-input id="graph-target" v-model:value="draft.target" :disabled="busy" />
        </template>
        <template v-else>
          <label for="graph-entity">实体概念 ID</label><n-input id="graph-entity" v-model:value="draft.entity" :disabled="busy" />
          <label for="graph-alias">别名表面文本</label><n-input id="graph-alias" v-model:value="draft.alias" :disabled="busy" />
        </template>
        <n-checkbox v-model:checked="confirmed" :disabled="!ready || !selectedSource">我已阅读所选片段，确认概念及关系/别名为非个人资料。</n-checkbox>
        <n-button attr-type="submit" :disabled="!canPropose">保存待审核提案</n-button>
      </form>
      <template v-if="kind === 'relation'">
        <h3>从所选片段抽取关系</h3>
        <p class="form-hint">仅接受完整的 8–240 字片段，每次最多两个待审核关系。词表明确指定原文称呼对应的概念和关系 ID；本次使用记忆任务的运行模型，可能产生模型费用。</p>
        <form class="field-stack" @submit.prevent="void extract()">
          <label for="graph-extract-run">本次抽取编号</label>
          <n-input id="graph-extract-run" v-model:value="extractDraft.runId" :disabled="busy" placeholder="例如 manual-20261001-1；同次操作保持编号" />
          <label for="graph-extract-vocabulary">概念和关系词表（JSON）</label>
          <n-input id="graph-extract-vocabulary" v-model:value="extractDraft.vocabulary" type="textarea" :autosize="{ minRows: 4, maxRows: 10 }" :disabled="busy" placeholder='{"concepts":{"激光":"laser","石英":"quartz"},"predicates":{"需要":"requires"}}' />
          <p class="form-hint">先选择来源，并在上方确认已阅读且属于非个人资料。相同编号不可换词表重试；调用中断后先重新读取当前对象。</p>
          <n-button attr-type="submit" :disabled="!canExtract" :loading="busy">抽取待审核关系</n-button>
        </form>
        <div v-if="extraction?.state === 'proposed'" class="source-choice">
          <p class="form-hint">以下证据与置信度仅为本次模型建议；请对照所选原文审核。</p>
          <p v-for="suggestion in extraction.suggestions" :key="suggestion.relation_id">{{ suggestion.relation_id }} · 置信度 {{ suggestion.confidence }} · 原文：{{ suggestion.evidence }}</p>
        </div>
      </template>
      <h3>当前对象</h3>
      <n-empty v-if="!page.items.length" description="当前群没有此类对象。" />
      <div v-for="item in page.items" :key="objectId(item)" class="source-choice">
        <strong>{{ objectId(item) }} · {{ objectLabel(item) }}</strong>
        <p><n-tag>{{ reviewLabel[item.review_status] }}</n-tag> · <n-tag>{{ statusLabel[item.status] }}</n-tag> · revision {{ item.revision }}</p>
        <p class="form-hint">来源 {{ item.source.source_id }} · revision {{ item.source.source_revision }} · chunk {{ item.source.chunk_id }}。来源失效后仍可撤销对象。</p>
        <div class="form-row">
          <n-button :disabled="!canApprove(item) || item.review_status !== 'pending'" @click="void transition(item, 'approved')">批准</n-button>
          <n-button :disabled="!canAct(item) || item.review_status !== 'pending'" @click="void transition(item, 'rejected')">拒绝</n-button>
          <n-button :disabled="!canAct(item) || item.review_status !== 'approved' || item.status === 'active' || item.status === 'revoked'" @click="void transition(item, 'apply')">应用</n-button>
          <n-button :disabled="!canAct(item) || item.status === 'revoked'" @click="void transition(item, 'revoke')">撤销</n-button>
        </div>
      </div>
      <n-button v-if="page.next_cursor" :disabled="!ready" @click="void load(true)">读取更多</n-button>
      <h3>有界图查询</h3>
      <form class="form-row" @submit.prevent="void walk()">
        <label for="graph-seeds">起点概念 ID（逗号或空格分隔）</label>
        <n-input id="graph-seeds" v-model:value="seeds" :disabled="busy" />
        <n-button attr-type="submit" :disabled="!ready || !seeds.trim()">查询两步路径</n-button>
      </form>
      <template v-if="projection">
        <p>节点 {{ projection.nodes_visited }} · 边 {{ projection.edges_examined }} · 停止原因 {{ projection.stop_reason }}</p>
        <p v-for="(path, index) in projection.paths" :key="index">{{ path.join(' → ') }}</p>
        <p v-for="item in projection.relations" :key="item.relation_id">{{ objectLabel(item) }} · 来源 {{ item.source.source_id }} / {{ item.source.chunk_id }}</p>
        <p v-for="item in projection.aliases" :key="item.alias_id">{{ objectLabel(item) }} · 来源 {{ item.source.source_id }} / {{ item.source.chunk_id }}</p>
        <n-empty v-if="!projection.paths.length" description="当前获权范围内没有路径结果。" />
      </template>
    </template>
    <section class="health-section" aria-labelledby="graph-personal-heading">
      <h3 id="graph-personal-heading">本人已确认偏好</h3>
      <p class="form-hint">只接受本人教导、已人工应用的偏好。主体和关系由原事实确定，目标必须是完整原值对应的非人物概念。第三人称呼、人际关系和敏感资料不在此入口；应用后的关系可参与已开启的检索，回复仍受原来源和模型上传权限约束。</p>
      <div class="form-row">
        <label for="graph-personal-fact">已应用事实 ID</label>
        <n-input id="graph-personal-fact" v-model:value="personalDraft.factId" :disabled="busy" />
        <n-button :disabled="!personalReady || !personalDraft.factId.trim()" @click="void loadPersonalSource()">读取当前事实来源</n-button>
      </div>
      <div v-if="personalSourceCurrent && personalSource" class="source-choice">
        <p>本人 {{ personalSource.source.subject_id }} · {{ personalSource.predicate }}：{{ personalSource.value }}</p>
        <p class="form-hint">事实 revision {{ personalSource.source.fact_revision }}；只使用当前仍有效的完整来源。</p>
      </div>
      <div class="field-stack">
        <label for="graph-personal-relation">关系对象 ID</label>
        <n-input id="graph-personal-relation" v-model:value="personalDraft.relationId" :disabled="busy" />
        <label for="graph-personal-target">完整偏好值对应的非人物概念 ID</label>
        <n-input id="graph-personal-target" v-model:value="personalDraft.targetId" :disabled="busy" />
        <n-checkbox v-model:checked="personalNonSensitive" :disabled="!personalSourceCurrent">我确认原事实是本人表达的非敏感偏好。</n-checkbox>
        <n-checkbox v-model:checked="personalNonPerson" :disabled="!personalSourceCurrent">我确认目标概念对应完整原值，且不代表其他人。</n-checkbox>
        <div class="form-row">
          <n-button :disabled="!canProposePersonal" @click="void proposePersonal()">保存本人偏好待审提案</n-button>
          <n-button :disabled="!personalReady || !personalDraft.relationId.trim()" @click="void loadPersonalRelation()">读取当前关系版本</n-button>
        </div>
      </div>
      <div v-if="personalRow && personalRow.scope.group_id === group.trim()" class="source-choice">
        <strong>{{ personalRow.relation_id }} · {{ personalRow.predicate }} → {{ personalRow.target_id }}</strong>
        <p>{{ reviewLabel[personalRow.review_status] }} · {{ statusLabel[personalRow.status] }} · revision {{ personalRow.revision }}</p>
        <div class="form-row">
          <n-button :disabled="!canApprovePersonal || personalRow.review_status !== 'pending'" @click="void transitionPersonal('approved')">批准本人偏好</n-button>
          <n-button :disabled="!canActPersonal || personalRow.review_status !== 'pending'" @click="void transitionPersonal('rejected')">拒绝</n-button>
          <n-button :disabled="!canActPersonal || personalRow.review_status !== 'approved' || personalRow.status === 'active' || personalRow.status === 'revoked'" @click="void transitionPersonal('apply')">应用</n-button>
          <n-button :disabled="!canActPersonal || personalRow.status === 'revoked'" @click="void transitionPersonal('revoke')">撤销</n-button>
        </div>
      </div>
    </section>
  </n-card>
</template>

<style scoped>
.health-section { margin-top: 16px; }
.health-table-wrap { overflow-x: auto; margin-top: 16px; }
.health-table { width: 100%; border-collapse: collapse; text-align: left; }
.health-table th, .health-table td { padding: 8px; border-bottom: 1px solid var(--om-border); }

.field-stack { display: grid; gap: 8px; }
.style-content { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; padding: 12px;
  background: var(--om-surface-soft); border-radius: 8px; }
.source-choice { padding: 16px 0; border-bottom: 1px solid var(--om-border); }
</style>

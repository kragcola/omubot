<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NTag, NText } from 'naive-ui'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { EpisodeDecayRequest, EpisodeManagementView, EpisodePromptStateRequest, FamiliarityAdjustmentRequest, FamiliarityView, SelfAliasResolution } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const groupInput = ref('')
const surfaceInput = ref('')
const aliasAtInput = ref('')
const alias = shallowRef<SelfAliasResolution | null>(null)
const aliasBusy = ref(false)
const aliasError = ref('')
const subjectInput = ref('')
const familiarity = shallowRef<FamiliarityView | null>(null)
const familiarityBusy = ref(false)
const familiarityError = ref('')
const familiarityScore = ref('')
const familiarityNeedsReload = ref(false)
const candidateInput = ref('')
const episode = shallowRef<EpisodeManagementView | null>(null)
const loadedGroup = ref('')
const loadedCandidate = ref('')
const episodeBusy = ref(false)
const episodeError = ref('')
const episodeNotice = ref('')
const needsReload = ref(false)
const decayDraft = ref('')
const reasonDraft = ref('')
let disposed = false
let aliasSequence = 0
let familiaritySequence = 0
let episodeSequence = 0
let draftVersion = 0
let aliasController: AbortController | undefined
let familiarityController: AbortController | undefined
let episodeController: AbortController | undefined

const aliasTimeValid = computed(() => !aliasAtInput.value.trim()
  || Number.isFinite(Number(aliasAtInput.value.trim())))
const canResolve = computed(() => sessionState.adminAuthenticated && !aliasBusy.value
  && Boolean(groupInput.value.trim() && surfaceInput.value.trim()) && aliasTimeValid.value)
const canLoadFamiliarity = computed(() => sessionState.adminAuthenticated && !familiarityBusy.value
  && Boolean(groupInput.value.trim() && subjectInput.value.trim()))
const canAdjustFamiliarity = computed(() => canLoadFamiliarity.value && familiarity.value !== null
  && !familiarityNeedsReload.value && Boolean(familiarityScore.value.trim())
  && Number.isFinite(Number(familiarityScore.value))
  && Number(familiarityScore.value) >= 0 && Number(familiarityScore.value) <= 100)
const canLoadEpisode = computed(() => sessionState.adminAuthenticated && !episodeBusy.value
  && Boolean(groupInput.value.trim() && candidateInput.value.trim()))
const episodeDirty = computed(() => episode.value !== null
  && (decayDraft.value.trim() !== episode.value.decay_at || Boolean(reasonDraft.value.trim())))
const loadedForCurrentQuery = computed(() => episode.value !== null
  && loadedGroup.value === groupInput.value.trim() && loadedCandidate.value === candidateInput.value.trim())
const canWriteEpisode = computed(() => sessionState.adminAuthenticated && loadedForCurrentQuery.value
  && !episodeBusy.value && !needsReload.value && Boolean(reasonDraft.value.trim()))
const decayValid = computed(() => !decayDraft.value.trim()
  || (/(?:Z|[+-]\d{2}:\d{2})$/i.test(decayDraft.value.trim())
    && Number.isFinite(Date.parse(decayDraft.value.trim()))))

function clearAlias() {
  aliasSequence += 1
  aliasController?.abort()
  aliasController = undefined
  alias.value = null
  aliasBusy.value = false
  aliasError.value = ''
}
function clearFamiliarity() {
  familiaritySequence += 1
  familiarityController?.abort()
  familiarityController = undefined
  familiarity.value = null
  familiarityBusy.value = false
  familiarityError.value = ''
  familiarityScore.value = ''
  familiarityNeedsReload.value = false
}
function clearEpisode() {
  episodeSequence += 1
  episodeController?.abort()
  episodeController = undefined
  episode.value = null
  loadedGroup.value = ''
  loadedCandidate.value = ''
  decayDraft.value = ''
  reasonDraft.value = ''
  episodeBusy.value = false
  episodeError.value = ''
  episodeNotice.value = ''
  needsReload.value = false
}
function clearAll(clearInputs: boolean) {
  clearAlias()
  clearFamiliarity()
  clearEpisode()
  if (clearInputs) {
    groupInput.value = ''
    surfaceInput.value = ''
    aliasAtInput.value = ''
    subjectInput.value = ''
    candidateInput.value = ''
  }
}
watch(() => groupInput.value.trim(), () => clearAll(false), { flush: 'sync' })
watch([surfaceInput, aliasAtInput], clearAlias, { flush: 'sync' })
watch(() => subjectInput.value.trim(), clearFamiliarity, { flush: 'sync' })
watch(() => candidateInput.value.trim(), clearEpisode, { flush: 'sync' })
watch([decayDraft, reasonDraft], () => { draftVersion += 1 }, { flush: 'sync' })
watch(() => sessionState.generation, () => clearAll(true), { flush: 'sync' })
watch(() => sessionState.adminAuthenticated, authenticated => {
  if (!authenticated) clearAll(true)
}, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; clearAll(true) })

function currentAlias(sequence: number, epoch: number, group: string, surface: string, at: string) {
  return !disposed && sequence === aliasSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && group === groupInput.value.trim()
    && surface === surfaceInput.value.trim() && at === aliasAtInput.value.trim()
}
function currentFamiliarity(sequence: number, epoch: number, group: string, subject: string) {
  return !disposed && sequence === familiaritySequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && group === groupInput.value.trim()
    && subject === subjectInput.value.trim()
}
function currentEpisode(sequence: number, epoch: number, group: string, candidate: string) {
  return !disposed && sequence === episodeSequence && isCurrentAdminEpoch(epoch)
    && sessionState.adminAuthenticated && group === groupInput.value.trim()
    && candidate === candidateInput.value.trim()
}
function unauthorized(cause: unknown): boolean {
  if (!isApiError(cause) || cause.status !== 401) return false
  expireAdminSession()
  return true
}

async function resolveAlias() {
  if (!canResolve.value) return
  clearAlias()
  const sequence = aliasSequence
  const epoch = currentAdminEpoch()
  const group = groupInput.value.trim()
  const surface = surfaceInput.value.trim()
  const at = aliasAtInput.value.trim()
  const controller = new AbortController()
  aliasController = controller
  aliasBusy.value = true
  const params = new URLSearchParams({ group_id: group, surface })
  if (at) params.set('at', String(Number(at)))
  try {
    const next = await apiRequest<SelfAliasResolution>(`/api/admin/memory/self-alias?${params}`, { signal: controller.signal })
    if (!currentAlias(sequence, epoch, group, surface, at)) return
    alias.value = next
  } catch (cause) {
    if (!currentAlias(sequence, epoch, group, surface, at)) return
    if (!unauthorized(cause)) aliasError.value = apiErrorMessage(cause)
  } finally {
    if (currentAlias(sequence, epoch, group, surface, at)) {
      aliasBusy.value = false
      aliasController = undefined
    }
  }
}

async function loadFamiliarity() {
  if (!canLoadFamiliarity.value) return
  clearFamiliarity()
  const sequence = familiaritySequence
  const epoch = currentAdminEpoch()
  const group = groupInput.value.trim()
  const subject = subjectInput.value.trim()
  const controller = new AbortController()
  familiarityController = controller
  familiarityBusy.value = true
  const params = new URLSearchParams({ group_id: group, subject_id: subject })
  try {
    const next = await apiRequest<FamiliarityView>(`/api/admin/memory/familiarity?${params}`, { signal: controller.signal })
    if (!currentFamiliarity(sequence, epoch, group, subject)) return
    familiarity.value = next
    familiarityScore.value = String(next.score)
  } catch (cause) {
    if (!currentFamiliarity(sequence, epoch, group, subject)) return
    if (!unauthorized(cause)) familiarityError.value = apiErrorMessage(cause)
  } finally {
    if (currentFamiliarity(sequence, epoch, group, subject)) {
      familiarityBusy.value = false
      familiarityController = undefined
    }
  }
}

async function adjustFamiliarity() {
  if (!canAdjustFamiliarity.value || familiarity.value === null) return
  const sequence = familiaritySequence
  const epoch = currentAdminEpoch()
  const group = groupInput.value.trim()
  const subject = subjectInput.value.trim()
  const controller = new AbortController()
  familiarityController = controller
  familiarityBusy.value = true
  familiarityError.value = ''
  const payload: FamiliarityAdjustmentRequest = {
    group_id: group, subject_id: subject, score: Number(familiarityScore.value),
    expected_revision: familiarity.value.revision, operation_id: crypto.randomUUID(),
  }
  try {
    const next = await apiRequest<FamiliarityView>('/api/admin/memory/familiarity/adjust', {
      method: 'POST', body: payload, adminMutation: true, signal: controller.signal,
    })
    if (!currentFamiliarity(sequence, epoch, group, subject)) return
    familiarity.value = next
    familiarityScore.value = String(next.score)
  } catch (cause) {
    if (!currentFamiliarity(sequence, epoch, group, subject)) return
    if (isApiError(cause) && cause.status === 409) familiarityNeedsReload.value = true
    if (!unauthorized(cause)) familiarityError.value = apiErrorMessage(cause)
  } finally {
    if (currentFamiliarity(sequence, epoch, group, subject)) {
      familiarityBusy.value = false
      familiarityController = undefined
    }
  }
}

async function loadEpisode() {
  if (!canLoadEpisode.value) return
  const sequence = ++episodeSequence
  const epoch = currentAdminEpoch()
  const group = groupInput.value.trim()
  const candidate = candidateInput.value.trim()
  const version = draftVersion
  const keepDraft = episodeDirty.value
  episodeController?.abort()
  const controller = new AbortController()
  episodeController = controller
  episodeBusy.value = true
  episodeError.value = ''
  episodeNotice.value = ''
  try {
    const params = new URLSearchParams({ group_id: group, candidate_id: candidate })
    const next = await apiRequest<EpisodeManagementView>(`/api/admin/memory/episode?${params}`, { signal: controller.signal })
    if (!currentEpisode(sequence, epoch, group, candidate)) return
    episode.value = next
    loadedGroup.value = group
    loadedCandidate.value = candidate
    needsReload.value = false
    if (!keepDraft && version === draftVersion) decayDraft.value = next.decay_at
    else episodeNotice.value = '已读取最新状态，未提交的期限和理由草稿已保留。'
  } catch (cause) {
    if (!currentEpisode(sequence, epoch, group, candidate)) return
    if (!unauthorized(cause)) episodeError.value = apiErrorMessage(cause)
  } finally {
    if (currentEpisode(sequence, epoch, group, candidate)) {
      episodeBusy.value = false
      episodeController = undefined
    }
  }
}

function canTransition(action: EpisodePromptStateRequest['action']): boolean {
  if (!canWriteEpisode.value) return false
  const state = episode.value?.state
  return action === 'disable' ? state === 'enabled_for_prompt'
    : action === 'approve_reopen' ? state === 'disabled' : state === 'approved'
}

async function mutateEpisode(action: EpisodePromptStateRequest['action'] | 'decay') {
  if (!canWriteEpisode.value || !episode.value) return
  if (action === 'decay' ? !decayValid.value : !canTransition(action)) return
  const current = episode.value
  const sequence = ++episodeSequence
  const epoch = currentAdminEpoch()
  const group = loadedGroup.value
  const candidate = loadedCandidate.value
  const version = draftVersion
  const common = {
    group_id: group, candidate_id: candidate,
    expected_candidate_revision: current.candidate_revision,
    expected_object_revision: current.object_revision, reason: reasonDraft.value.trim(),
  }
  const body: EpisodeDecayRequest | EpisodePromptStateRequest = action === 'decay'
    ? { ...common, decay_at: decayDraft.value.trim() } : { ...common, action }
  const controller = new AbortController()
  episodeController = controller
  episodeBusy.value = true
  episodeError.value = ''
  episodeNotice.value = ''
  try {
    const next = await apiRequest<EpisodeManagementView>(`/api/admin/memory/episode/${action === 'decay' ? 'decay' : 'state'}`, {
      method: 'POST', body, adminMutation: true, signal: controller.signal,
    })
    if (!currentEpisode(sequence, epoch, group, candidate)) return
    episode.value = next
    if (version === draftVersion) {
      if (action === 'decay') decayDraft.value = next.decay_at
      reasonDraft.value = ''
    }
    episodeNotice.value = action === 'decay' ? '期限已保存，启用状态保持不变。'
      : action === 'approve_reopen' ? '已恢复批准；还需单独启用，才能重新用于对话。'
      : action === 'enable' ? '已启用；是否可用于对话仍以当前期限和来源许可为准。' : '经历已停用。'
  } catch (cause) {
    if (!currentEpisode(sequence, epoch, group, candidate)) return
    if (unauthorized(cause)) return
    if (isApiError(cause) && cause.status === 409) {
      needsReload.value = true
      episodeError.value = '状态版本已变化，请重新读取后再操作；草稿已保留，不会自动重试。'
    } else episodeError.value = apiErrorMessage(cause)
  } finally {
    if (currentEpisode(sequence, epoch, group, candidate)) {
      episodeBusy.value = false
      episodeController = undefined
    }
  }
}

function timeLabel(value: number | null): string {
  return value === null ? '尚未记录' : new Date(value * 1000).toLocaleString()
}
function episodeStateLabel(state: EpisodeManagementView['state']): string {
  return { dry_run: '试运行', candidate: '待审核', approved: '已批准，尚未启用',
    enabled_for_prompt: '已启用到对话', disabled: '已停用' }[state]
}
</script>

<template>
  <n-card class="page-card identity-episode-management" title="本人别名、熟悉度与共同经历" :bordered="false">
    <label class="form-field" for="identity-episode-group">准确群 ID
      <n-input v-model:value="groupInput" :input-props="{ id: 'identity-episode-group', 'aria-label': '本人别名、熟悉度与共同经历的准确群 ID' }" maxlength="64" placeholder="填写完整群 ID" />
    </label>
    <section class="management-section" aria-labelledby="self-alias-heading">
      <h3 id="self-alias-heading">本人别名查询</h3>
      <p class="subtle-text">只查询本群通过本人命令确认的别名；不会据此合并第三人身份。历史时间查询仍须满足当前来源许可。</p>
      <form @submit.prevent="resolveAlias">
        <div class="form-grid">
          <label class="form-field" for="self-alias-surface">别名
            <n-input v-model:value="surfaceInput" :input-props="{ id: 'self-alias-surface', 'aria-label': '本人确认的别名' }" maxlength="256" />
          </label>
          <label class="form-field" for="self-alias-at">查询时间（可选，Unix 秒）
            <n-input v-model:value="aliasAtInput" :input-props="{ id: 'self-alias-at', 'aria-label': '别名查询时间，Unix 秒，可选' }" placeholder="留空查询当前" />
          </label>
        </div>
        <p v-if="!aliasTimeValid" class="form-hint" role="alert">请填写有限数字形式的 Unix 秒，或留空。</p>
        <div class="button-row"><n-button attr-type="submit" :loading="aliasBusy" :disabled="!canResolve">查询本人别名</n-button></div>
      </form>
      <n-alert v-if="aliasError" class="notice" type="error" role="alert">{{ aliasError }}</n-alert>
      <template v-if="alias">
        <n-alert class="notice" :type="alias.status === 'resolved' ? 'success' : 'info'" role="status" aria-live="polite">
          {{ alias.status === 'resolved' ? `唯一对应主体：${alias.candidates[0]?.subject_id}` : alias.status === 'ambiguous' ? '存在多个有效候选，无法确定唯一身份。' : '没有当前可用的本人别名候选。' }}
        </n-alert>
        <div v-for="candidate in alias.candidates" :key="`${candidate.fact_id}:${candidate.fact_revision}`" class="candidate-summary">
          <n-text>{{ candidate.surface }} · 主体 {{ candidate.subject_id }}</n-text>
          <p class="form-hint">有效窗口：{{ timeLabel(candidate.window_from) }} — {{ candidate.window_to === null ? '持续有效' : timeLabel(candidate.window_to) }} · 事实版本 {{ candidate.fact_revision }}</p>
        </div>
      </template>
      <n-empty v-else-if="!aliasBusy && !aliasError" description="输入准确群 ID 和别名后查询" />
    </section>

    <section class="management-section" aria-labelledby="familiarity-heading">
      <h3 id="familiarity-heading">同群熟悉度</h3>
      <p class="subtle-text">按准确主体 ID 查询互动统计，管理员可单独调整分值。分档是系统参考，不代表现实关系或个人喜好。</p>
      <form @submit.prevent="loadFamiliarity">
        <label class="form-field" for="familiarity-subject">准确主体 ID
          <n-input v-model:value="subjectInput" :input-props="{ id: 'familiarity-subject', 'aria-label': '熟悉度查询的准确主体 ID' }" maxlength="128" placeholder="填写完整主体 ID" />
        </label>
        <div class="button-row"><n-button attr-type="submit" :loading="familiarityBusy" :disabled="!canLoadFamiliarity">查询熟悉度</n-button></div>
      </form>
      <n-alert v-if="familiarityError" class="notice" type="error" role="alert">{{ familiarityError }}</n-alert>
      <template v-if="familiarity">
        <p><n-text>群 {{ familiarity.scope.group_id }} · 主体 {{ familiarity.subject_id }} · 版本 {{ familiarity.revision }}</n-text></p>
        <div class="button-row">
          <n-tag>分值 {{ familiarity.score }}</n-tag>
          <n-tag>系统分档：{{ familiarity.tier }}</n-tag>
          <n-tag>有效贡献 {{ familiarity.valid_contributions }}</n-tag>
          <n-tag :type="familiarity.enabled_for_chat ? 'success' : 'warning'">聊天参考开关：{{ familiarity.enabled_for_chat ? '已开启' : '已关闭' }}</n-tag>
        </div>
        <p v-if="familiarity.valid_contributions === 0" class="form-hint" role="status">没有当前有效贡献。</p>
        <p v-if="familiarity.admin_adjustment !== null" class="form-hint">最近管理员设定：{{ familiarity.admin_adjustment }}。后续有效互动与来源撤销仍会影响当前分值。</p>
        <form @submit.prevent="adjustFamiliarity">
          <label class="form-field" for="familiarity-score">管理员设定分值（0—100）
            <n-input v-model:value="familiarityScore" :input-props="{ id: 'familiarity-score', inputmode: 'decimal' }" maxlength="8" />
          </label>
          <div class="button-row"><n-button attr-type="submit" :loading="familiarityBusy" :disabled="!canAdjustFamiliarity">保存调整</n-button></div>
        </form>
        <p v-if="familiarityNeedsReload" class="form-hint" role="status">版本已变化，请先重新查询。</p>
      </template>
      <n-empty v-else-if="!familiarityBusy && !familiarityError" description="输入准确群 ID 和主体 ID 后查询" />
    </section>

    <section class="management-section" aria-labelledby="episode-management-heading">
      <h3 id="episode-management-heading">共同经历期限与启用状态</h3>
      <p class="subtle-text">使用已应用的事件经历候选 ID 读取。停用后需先恢复批准，再单独启用；清除期限不会改变启用状态。</p>
      <form @submit.prevent="loadEpisode">
        <label class="form-field" for="episode-management-candidate">事件经历候选 ID
          <n-input v-model:value="candidateInput" :input-props="{ id: 'episode-management-candidate', 'aria-label': '事件经历候选 ID' }" maxlength="128" placeholder="从下方事件经历候选复制 ID" />
        </label>
        <div class="button-row"><n-button attr-type="submit" :loading="episodeBusy" :disabled="!canLoadEpisode">{{ needsReload ? '重新读取最新状态' : '读取经历状态' }}</n-button></div>
      </form>
      <n-alert v-if="episodeError" class="notice" type="error" role="alert">{{ episodeError }}</n-alert>
      <n-alert v-if="episodeNotice" class="notice" type="success" role="status" aria-live="polite">{{ episodeNotice }}</n-alert>
      <template v-if="episode && loadedForCurrentQuery">
        <div class="button-row">
          <n-tag>{{ episodeStateLabel(episode.state) }}</n-tag>
          <n-tag :type="episode.prompt_eligible ? 'success' : 'warning'">{{ episode.prompt_eligible ? '当前可用于对话' : '当前不可用于对话' }}</n-tag>
        </div>
        <p class="form-hint">候选版本 {{ episode.candidate_revision }} · 经历版本 {{ episode.object_revision }} · 最近使用：{{ timeLabel(episode.last_used_at) }}</p>
        <label class="form-field" for="episode-decay">到期时间（带时区的 ISO 时间，留空清除）
          <n-input v-model:value="decayDraft" :input-props="{ id: 'episode-decay', 'aria-label': '经历到期时间，带时区 ISO 时间，留空清除' }" placeholder="2026-10-01T20:00:00+08:00" />
        </label>
        <p v-if="!decayValid" class="form-hint" role="alert">时间需包含 Z 或时区偏移，例如 +08:00。</p>
        <label class="form-field" for="episode-action-reason">操作理由（必填）
          <n-input v-model:value="reasonDraft" type="textarea" :input-props="{ id: 'episode-action-reason', 'aria-label': '经历管理操作理由，必填' }" :autosize="{ minRows: 2, maxRows: 4 }" maxlength="256" />
        </label>
        <p v-if="episodeDirty" class="form-hint" role="status">有未提交草稿；重新读取会保留草稿。</p>
        <div class="button-row">
          <n-button :disabled="!canWriteEpisode || !decayValid" @click="mutateEpisode('decay')">保存期限</n-button>
          <n-button type="error" :disabled="!canTransition('disable')" @click="mutateEpisode('disable')">停用经历</n-button>
          <n-button :disabled="!canTransition('approve_reopen')" @click="mutateEpisode('approve_reopen')">恢复批准</n-button>
          <n-button type="primary" :disabled="!canTransition('enable')" @click="mutateEpisode('enable')">启用到对话</n-button>
        </div>
      </template>
      <n-empty v-else-if="!episodeBusy && !episodeError" description="填写事件经历候选 ID 后读取" />
    </section>
  </n-card>
</template>

<style scoped>
.management-section { margin-top: 24px; }
.management-section h3 { margin: 0 0 8px; }
.management-section .form-field + .form-field { margin-top: 16px; }
.candidate-summary { margin-top: 12px; }
.candidate-summary p { margin: 4px 0 0; }
</style>

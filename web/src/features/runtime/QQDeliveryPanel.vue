<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NCheckbox, NEmpty, NInput, NInputNumber, NSelect, NSpace, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { QQDeliveryView } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const view = ref<QQDeliveryView | null>(null)
const kind = ref<'account' | 'group' | 'private'>('account')
const targetId = ref('')
const reason = ref('')
const reviewed = ref<string[]>([])
const batchConfirmed = ref(false)
const duration = ref<number | null>(1200)
const loading = ref(false)
const saving = ref(false)
const stale = ref(true)
const error = ref('')
const notice = ref('')
let loadedScope = ''
let lifecycle = 0
let controller: AbortController | undefined
let mounted = true

const scopeKey = computed(() => kind.value === 'account' ? 'account' : `${kind.value}:${targetId.value}`)
const validScope = computed(() => kind.value === 'account' || /^[A-Za-z0-9_-]{1,64}$/.test(targetId.value))
const selected = computed(() => kind.value === 'account' ? view.value?.snapshot?.account : view.value?.snapshot?.target)
const batch = computed(() => view.value?.test_batch)
const activeBatch = computed(() => batch.value && !batch.value.ended && batch.value.expires_in_seconds > 0)
const unknown = computed(() => view.value?.snapshot?.unreviewed_unknown_action_ids ?? [])
const available = computed(() => Boolean(sessionState.adminAuthenticated && view.value?.available
  && validScope.value && loadedScope === scopeKey.value && !stale.value && !loading.value && !saving.value))
const canResume = computed(() => available.value && Boolean(selected.value?.held || view.value?.local_held)
  && Boolean(view.value?.connection_ready) && !view.value?.in_flight && Boolean(reason.value.trim())
  && unknown.value.every(id => reviewed.value.includes(id)) && (!activeBatch.value || batchConfirmed.value)
  && (!batch.value || activeBatch.value)
  && (!activeBatch.value || kind.value === 'account' || !view.value?.snapshot?.account.held))
const canBegin = computed(() => available.value && kind.value !== 'account' && !activeBatch.value
  && Boolean(view.value?.snapshot?.account.held) && !view.value?.in_flight && !view.value?.waiting
  && duration.value !== null && duration.value > 0 && duration.value <= 1200)
const options = [
  { label: '整个账号', value: 'account' }, { label: '精确群', value: 'group' }, { label: '精确私聊', value: 'private' },
]

function current(generation: number, epoch: number, run: number): boolean {
  return mounted && sessionState.adminAuthenticated && sessionState.generation === generation
    && isCurrentAdminEpoch(epoch) && lifecycle === run
}

function scopeBody(): Record<string, string> | null {
  const account = view.value?.snapshot?.account.account_id
  if (!account || kind.value === 'account') return null
  return kind.value === 'group' ? { bot_id: account, kind: 'group', group_id: targetId.value }
    : { bot_id: account, kind: 'private', private_user_id: targetId.value }
}

function accept(result: QQDeliveryView): void {
  view.value = result
  loadedScope = scopeKey.value
  stale.value = false
  reviewed.value = []
  batchConfirmed.value = false
}

async function load(): Promise<void> {
  if (!sessionState.adminAuthenticated || saving.value || loading.value || !validScope.value) return
  const generation = sessionState.generation, epoch = currentAdminEpoch(), run = lifecycle
  const requestScope = scopeKey.value
  const abort = new AbortController()
  controller = abort
  loading.value = true
  error.value = ''
  try {
    const query = kind.value === 'account' ? '' : `?kind=${kind.value}&target_id=${encodeURIComponent(targetId.value)}`
    const result = await apiRequest<QQDeliveryView>(`/api/admin/qq-delivery${query}`, { signal: abort.signal })
    if (current(generation, epoch, run) && requestScope === scopeKey.value) accept(result)
  } catch (cause: unknown) {
    if (!current(generation, epoch, run) || abort.signal.aborted) return
    stale.value = true
    if (isApiError(cause) && cause.status === 401) expireAdminSession()
    else error.value = apiErrorMessage(cause)
  } finally {
    if (current(generation, epoch, run)) loading.value = false
    if (controller === abort) controller = undefined
  }
}

async function mutate(action: 'pause' | 'resume' | 'batch/begin' | 'batch/end'): Promise<void> {
  if (!available.value || !view.value?.snapshot || !selected.value) return
  if (action === 'resume' && !canResume.value || action === 'batch/begin' && !canBegin.value) return
  if (action === 'pause' && !reason.value.trim()) return
  const generation = sessionState.generation, epoch = currentAdminEpoch(), run = lifecycle
  const abort = new AbortController()
  controller = abort
  saving.value = true
  error.value = ''
  notice.value = ''
  const account_id = view.value.snapshot.account.account_id
  const body = action === 'batch/begin'
    ? { account_id, scope: scopeBody(), batch_id: crypto.randomUUID(), duration_seconds: duration.value }
    : action === 'batch/end'
      ? { account_id, scope: scopeBody(), batch_id: batch.value?.batch_id,
        reason: reason.value.trim() || 'admin_batch_ended' }
      : { account_id, scope: scopeBody(), expected_revision: selected.value.revision, reason: reason.value.trim(),
        ...(action === 'resume' ? { reviewed_unknown_action_ids: reviewed.value,
          test_batch_id: activeBatch.value && batchConfirmed.value ? batch.value?.batch_id : null } : {}) }
  try {
    const result = await apiRequest<QQDeliveryView>(`/api/admin/qq-delivery/${action}`, {
      method: 'POST', adminMutation: true, body, signal: abort.signal,
    })
    if (!current(generation, epoch, run)) return
    accept(result)
    if (action === 'batch/begin') kind.value = 'account'
    notice.value = action === 'resume' ? '已解除指定范围停发。仅新的有效请求可继续。'
      : action === 'pause' ? '指定范围已暂停。' : action === 'batch/begin'
        ? '已登记有限批次，仍保持停发。已切换整个账号；刷新后核对当前批次，再解除账号停发。' : '有限批次已结束，账号保持停发。'
  } catch (cause: unknown) {
    if (!current(generation, epoch, run)) return
    stale.value = true
    if (isApiError(cause) && cause.status === 401) expireAdminSession()
    else error.value = isApiError(cause) ? apiErrorMessage(cause)
      : '操作结果未确认，请刷新持久状态后再决定下一步。'
  } finally {
    if (current(generation, epoch, run)) saving.value = false
    if (controller === abort) controller = undefined
  }
}

function review(id: string, checked: boolean): void {
  reviewed.value = checked ? [...reviewed.value, id] : reviewed.value.filter(item => item !== id)
}

watch(() => sessionState.generation, () => {
  lifecycle += 1
  controller?.abort()
  view.value = null
  loading.value = saving.value = false
  stale.value = true
  reviewed.value = []
  batchConfirmed.value = false
  reason.value = error.value = notice.value = ''
  if (sessionState.adminAuthenticated) void load()
})
watch(scopeKey, () => { stale.value = true; reviewed.value = []; batchConfirmed.value = false })
onMounted(() => { void load() })
onBeforeUnmount(() => { mounted = false; lifecycle += 1; controller?.abort() })
</script>

<template>
  <n-card class="page-card qq-panel" :bordered="false" title="QQ 发送治理">
    <div class="qq-body">
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <n-alert v-if="notice" type="success" :bordered="false">{{ notice }}</n-alert>
    <n-space align="center">
      <n-select v-model:value="kind" :options="options" :disabled="saving || loading" aria-label="治理范围" class="qq-scope" />
      <n-input v-if="kind !== 'account'" v-model:value="targetId" :disabled="saving || loading" placeholder="准确目标 ID" aria-label="准确目标 ID" />
      <n-button :loading="loading" :disabled="saving || !validScope" @click="load">刷新当前范围</n-button>
    </n-space>
    <n-empty v-if="view && !view.available" description="当前实例使用离线发送器，未装配真实 QQ 治理。" />
    <template v-if="view?.snapshot">
      <n-space align="center">
        <n-tag :type="view.local_held || selected?.held || !view.connection_ready ? 'warning' : 'success'">
          {{ view.local_held || selected?.held || !view.connection_ready ? '停发' : '可接受新请求' }}
        </n-tag>
        <span>账号 {{ view.snapshot.account.account_id }} · 版本 {{ selected?.revision }}</span>
        <span>{{ view.connection_ready ? '传输身份已核验' : '传输未就绪' }}</span>
      </n-space>
      <p class="form-hint">账号持久原因：{{ view.snapshot.account.reason || '无' }}；
        <template v-if="kind !== 'account'">目标持久原因：{{ selected?.reason || '无' }}；</template>
        本进程原因：{{ view.local_hold_reason || '无' }}。</p>
      <p class="form-hint">排队 {{ view.waiting }}；在途 {{ view.in_flight ? '有' : '无' }}。
        账号一小时 {{ view.snapshot.quota.account_hour_used }}/{{ view.limits.account_hour_limit }}，24 小时
        {{ view.snapshot.quota.account_day_used }}/{{ view.limits.account_day_limit }}；间隔至少 {{ view.limits.account_min_interval }} 秒。
      </p>
      <p v-if="kind !== 'account'" class="form-hint">本目标一小时 {{ view.snapshot.quota.target_hour_used }}/{{ view.limits.target_hour_limit }}，
        24 小时 {{ view.snapshot.quota.target_day_used }}/{{ view.limits.target_day_limit }}；间隔至少 {{ view.limits.target_min_interval }} 秒。</p>
      <p v-for="target in view.waiting_targets" :key="target.scope_key.join(':')" class="form-hint">
        {{ target.scope_key[1] === 'group' ? '群' : '私聊' }} {{ target.scope_key[2] }} 等待 {{ target.waiting }}。</p>
      <p v-if="stale" class="form-hint">先刷新当前范围，核对最新版本后再操作。</p>
      <n-input v-model:value="reason" :disabled="saving" placeholder="暂停来源或解除后的处理结论" aria-label="处理原因" :maxlength="128" />
      <div v-for="id in unknown" :key="id" class="qq-review">
        <n-checkbox :checked="reviewed.includes(id)" :disabled="saving || stale" @update:checked="(checked) => review(id, checked)">
          已核对 {{ id }}：结果仍未知，禁止重放
        </n-checkbox>
      </div>
      <n-checkbox v-if="activeBatch && batch" v-model:checked="batchConfirmed" :disabled="saving || stale">
        确认继续当前批次 {{ batch.batch_id }}
      </n-checkbox>
      <p v-if="activeBatch && view.snapshot.account.held && kind !== 'account'" class="form-hint">
        当前账号仍停发；切换“整个账号”并刷新后，核对当前批次再解除账号停发。</p>
      <n-space>
        <n-button :disabled="!available || !reason.trim()" :loading="saving" @click="mutate('pause')">暂停指定范围</n-button>
        <n-button :disabled="!canResume" :loading="saving" @click="mutate('resume')">核验并解除停发</n-button>
      </n-space>
      <p class="form-hint">解除会重新核验实际账号与在线状态；额度保留，旧等待票、旧后缀与未知动作不恢复。</p>
      <div class="qq-batch">
        <strong>有限测试批次</strong>
        <p v-if="batch" class="form-hint">{{ batch.batch_id }}：{{ batch.ended ? '已结束' : '有效' }}，
          已承诺 {{ batch.committed_cost }}/{{ batch.write_limit }} 次写动作，刷新时剩余 {{ Math.ceil(batch.expires_in_seconds) }} 秒。{{ batch.reason }}</p>
        <p v-if="batch && !activeBatch" class="form-hint">当前批次已结束或到期；登记新批次后才能核验恢复，旧批次不可重用。</p>
        <p class="form-hint">选择准确目标后，账号停发且无排队或在途写者时可登记批次。Bot 每批最多 12 次写动作、20 分钟。</p>
        <n-space align="center">
          <n-input-number v-model:value="duration" :min="1" :max="1200" :disabled="saving" aria-label="测试批次时限（秒）" />
          <n-button :disabled="!canBegin" :loading="saving" @click="mutate('batch/begin')">登记有限批次</n-button>
          <n-button :disabled="!available || !activeBatch" :loading="saving" @click="mutate('batch/end')">结束当前批次</n-button>
        </n-space>
      </div>
    </template>
    </div>
  </n-card>
</template>

<style scoped>
.qq-body { display: flex; flex-direction: column; gap: 12px; }
.qq-scope { min-width: 160px; }
.qq-review { overflow-wrap: anywhere; }
.qq-batch { border-top: 1px solid var(--om-border); padding-top: 16px; }
.qq-panel p { margin: 0; }
</style>

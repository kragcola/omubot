<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NAutoComplete, NButton, NCard, NCheckbox, NEmpty, NInput, NSelect, NSpace, NTag } from 'naive-ui'

import { apiErrorMessage, isApiError } from '@/api/client'
import type { NativePreparationView, NativeReleaseInputs, NativeReleaseStatusView } from '@/api/generated'
import { prepareNativeRelease, readNativePlan, readNativeRelease } from '@/api/nativeRelease'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

type Phase = 'idle' | 'prepare' | 'plan' | 'unknown'
const snapshot = ref<NativeReleaseStatusView | null>(null)
const receipt = ref<NativePreparationView | null>(null)
const targetName = ref('')
const targetSha = ref('')
const priorName = ref('')
const priorSha = ref('')
const preparationId = ref('')
const backupId = ref('')
const receiptSha = ref('')
const selectedBackup = ref<string | null>(null)
const backupScopeUnderstood = ref(false)
const schemaCompatible = ref(false)
const loading = ref(false)
const phase = ref<Phase>('idle')
const needsReload = ref(true)
const error = ref('')
const notice = ref('')
const unknownPreparationId = ref('')
const recoveredSelection = ref(false)
let unknownReadbackFresh = false
let lifecycle = 0
let readSequence = 0
let mounted = true
let readController: AbortController | undefined
let writeController: AbortController | undefined

const shaPattern = /^[0-9a-f]{64}$/
const namePattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.zip$/
const busy = computed(() => phase.value === 'prepare' || phase.value === 'plan')
const sources = computed(() => snapshot.value?.source_archives ?? [])
const backupOptions = computed(() => (snapshot.value?.backups ?? []).map(item => ({
  label: item.preparation_id, value: item.preparation_id,
})))
const validSources = computed(() => namePattern.test(targetName.value.trim())
  && shaPattern.test(targetSha.value.trim().toLowerCase())
  && ((!priorName.value.trim() && !priorSha.value.trim())
    || (namePattern.test(priorName.value.trim()) && shaPattern.test(priorSha.value.trim().toLowerCase()))))
const available = computed(() => Boolean(sessionState.adminAuthenticated && snapshot.value?.managed_instance
  && !busy.value && !loading.value && !needsReload.value))
const canPrepare = computed(() => available.value && validSources.value && backupScopeUnderstood.value
  && phase.value !== 'unknown' && !receipt.value && Boolean(snapshot.value?.config_sha256))
const canPlan = computed(() => available.value && validSources.value
  && /^[0-9a-f]{32}$/.test(backupId.value.trim()) && shaPattern.test(receiptSha.value.trim().toLowerCase())
  && (phase.value !== 'unknown' || recoveredSelection.value))
const canNewPreparation = computed(() => available.value && phase.value !== 'unknown' && Boolean(receipt.value))

function blockerLabel(code: string): string {
  const labels: Record<string, string> = {
    current_release_unknown: '未声明回退源包，无法形成完整回退步骤',
    schema_compatibility_unverified: '目标 schema 与备份的兼容性尚未独立核验',
  }
  return labels[code] ?? code
}

function current(epoch: number, generation: number, adminEpoch: number): boolean {
  return mounted && epoch === lifecycle && generation === sessionState.generation
    && isCurrentAdminEpoch(adminEpoch) && sessionState.adminAuthenticated
}

function clearSession(): void {
  lifecycle += 1
  readSequence += 1
  readController?.abort()
  writeController?.abort()
  readController = undefined
  writeController = undefined
  snapshot.value = null
  receipt.value = null
  targetName.value = ''
  targetSha.value = ''
  priorName.value = ''
  priorSha.value = ''
  preparationId.value = ''
  backupId.value = ''
  receiptSha.value = ''
  selectedBackup.value = null
  backupScopeUnderstood.value = false
  schemaCompatible.value = false
  loading.value = false
  phase.value = 'idle'
  needsReload.value = true
  unknownPreparationId.value = ''
  recoveredSelection.value = false
  unknownReadbackFresh = false
  error.value = ''
  notice.value = ''
}

async function load(): Promise<void> {
  if (!sessionState.adminAuthenticated || loading.value || busy.value) return
  const epoch = lifecycle, generation = sessionState.generation, adminEpoch = currentAdminEpoch()
  const sequence = ++readSequence
  const controller = new AbortController()
  readController = controller
  loading.value = true
  error.value = ''
  try {
    const result = await readNativeRelease(controller.signal)
    if (!current(epoch, generation, adminEpoch) || sequence !== readSequence || controller.signal.aborted) return
    snapshot.value = result
    needsReload.value = false
    backupScopeUnderstood.value = false
    schemaCompatible.value = false
    if (phase.value === 'unknown') unknownReadbackFresh = true
  } catch (cause: unknown) {
    if (!current(epoch, generation, adminEpoch) || sequence !== readSequence || controller.signal.aborted) return
    needsReload.value = true
    if (isApiError(cause) && cause.status === 401) expireAdminSession()
    else error.value = apiErrorMessage(cause)
  } finally {
    if (current(epoch, generation, adminEpoch) && sequence === readSequence) {
      loading.value = false
      if (readController === controller) readController = undefined
    }
  }
}

function inputs(identity: string): NativeReleaseInputs {
  return {
    preparation_id: identity,
    target_name: targetName.value.trim(), target_sha256: targetSha.value.trim().toLowerCase(),
    prior_name: priorName.value.trim() || null, prior_sha256: priorSha.value.trim().toLowerCase() || null,
  }
}

function chooseBackup(identity: string | null): void {
  selectedBackup.value = identity
  if (!available.value || (phase.value === 'unknown' && !unknownReadbackFresh)) return
  const record = snapshot.value?.backups.find(item => item.preparation_id === identity)
  if (!record) return
  backupId.value = record.preparation_id
  receiptSha.value = record.receipt_sha256
  schemaCompatible.value = false
  recoveredSelection.value = true
  receipt.value = null
  notice.value = '已选定回执。请核对与该备份对应的目标包和声明回退包，再人工读取计划。'
}

function bindManualReceipt(): void {
  if (!available.value || (phase.value === 'unknown' && !unknownReadbackFresh)) return
  if (!/^[0-9a-f]{32}$/.test(backupId.value.trim()) || !shaPattern.test(receiptSha.value.trim().toLowerCase())) return
  recoveredSelection.value = true
  schemaCompatible.value = false
  receipt.value = null
  notice.value = '已绑定手填回执身份；服务端将核验完整私有回执与源包 SHA。'
}

function newPreparation(): void {
  if (!canNewPreparation.value) return
  preparationId.value = ''
  backupId.value = ''
  receiptSha.value = ''
  selectedBackup.value = null
  receipt.value = null
  backupScopeUnderstood.value = false
  schemaCompatible.value = false
  recoveredSelection.value = false
  needsReload.value = true
  notice.value = '请刷新状态读取当前修订；下一次备份会使用新编号，并需重新确认范围。'
}

async function submit(kind: 'prepare' | 'plan'): Promise<void> {
  if (kind === 'prepare' ? !canPrepare.value : !canPlan.value) return
  const status = snapshot.value!
  if (kind === 'prepare' && !preparationId.value) preparationId.value = crypto.randomUUID().replaceAll('-', '')
  const identity = kind === 'prepare' ? preparationId.value : backupId.value.trim()
  const source = inputs(identity)
  const epoch = lifecycle, generation = sessionState.generation, adminEpoch = currentAdminEpoch()
  const controller = new AbortController()
  writeController = controller
  phase.value = kind
  error.value = ''
  notice.value = ''
  try {
    const result = kind === 'prepare'
      ? await prepareNativeRelease({ ...source, expected_config_sha256: status.config_sha256!,
        expected_settings_revision: status.settings_revision, expected_policy_revision: status.policy_revision,
        backup_scope_understood: backupScopeUnderstood.value }, controller.signal)
      : await readNativePlan({ ...source, receipt_sha256: receiptSha.value.trim().toLowerCase(),
        schema_compatible: schemaCompatible.value }, controller.signal)
    if (!current(epoch, generation, adminEpoch) || controller.signal.aborted) return
    receipt.value = result
    backupId.value = result.preparation_id
    receiptSha.value = result.receipt_sha256
    if (!unknownPreparationId.value || unknownPreparationId.value === result.preparation_id) {
      unknownPreparationId.value = ''
      phase.value = 'idle'
    } else phase.value = 'unknown'
    recoveredSelection.value = true
    backupScopeUnderstood.value = false
    notice.value = kind === 'prepare'
      ? '一致快照与私有回执已完成。默认尚未确认 schema 兼容；请独立核验后读取计划。'
      : '已从持久回执读回计划；下方步骤由操作者另行执行。'
  } catch (cause: unknown) {
    if (!current(epoch, generation, adminEpoch) || controller.signal.aborted) return
    backupScopeUnderstood.value = false
    schemaCompatible.value = false
    recoveredSelection.value = false
    needsReload.value = true
    if (isApiError(cause) && cause.status === 401) {
      expireAdminSession()
      return
    }
    if (!isApiError(cause) || cause.status >= 500) {
      phase.value = 'unknown'
      if (!unknownPreparationId.value) unknownPreparationId.value = identity
      unknownReadbackFresh = false
      receipt.value = null
      error.value = '操作结果未知，已锁定备份提交。请刷新状态，再选定或手填该编号的回执以人工读回；页面不会自动重发。'
    } else {
      phase.value = unknownPreparationId.value ? 'unknown' : 'idle'
      error.value = cause.status === 409
        ? '版本已变化，输入与准备编号已保留。请刷新状态读取新修订，再重新确认备份范围。'
        : apiErrorMessage(cause)
    }
  } finally {
    if (current(epoch, generation, adminEpoch) && writeController === controller) writeController = undefined
  }
}

watch(() => [sessionState.generation, sessionState.adminAuthenticated], () => {
  clearSession()
  if (sessionState.adminAuthenticated) void load()
})
watch([targetName, targetSha, priorName, priorSha], () => {
  backupScopeUnderstood.value = false
  schemaCompatible.value = false
})
onMounted(() => { void load() })
onBeforeUnmount(() => { mounted = false; clearSession() })
</script>

<template>
  <n-card class="page-card native-release" :bordered="false">
    <div class="section-heading">
      <div>
        <p class="eyebrow">原生部署准备</p>
        <h2>一致备份与人工更新计划</h2>
        <p class="subtle-text">从独立实例的本地源包建立可核对快照与回执，再查看更新和回滚步骤。</p>
      </div>
      <n-button secondary :loading="loading" :disabled="!sessionState.adminAuthenticated || busy || loading" @click="load">
        刷新备份状态
      </n-button>
    </div>
    <n-alert class="notice" type="info" :show-icon="true">
      “创建备份”只快照 state.sqlite3 与 config.toml；“读取计划”只核验已有备份。页面没有停止、重启或更新服务的操作。
    </n-alert>
    <n-alert v-if="error" class="notice" type="error" :show-icon="true">{{ error }}</n-alert>
    <n-alert v-if="notice" class="notice" type="success" :show-icon="true">{{ notice }}</n-alert>
    <n-alert v-if="phase === 'unknown'" class="notice" type="warning" :show-icon="true">
      待核对准备编号：<code>{{ unknownPreparationId }}</code>。刷新后从回执列表选择，或手填该编号与已知回执 SHA。
    </n-alert>
    <n-alert v-if="snapshot && needsReload" class="notice" type="warning" :show-icon="true">
      当前保留上一次读取的状态。请刷新备份状态，核对新的配置 SHA 与修订后再确认操作。
    </n-alert>
    <n-empty v-if="!snapshot" :description="loading ? '正在读取原生备份状态…' : '尚未读取原生备份状态'" />
    <template v-else>
      <dl class="native-details">
        <div><dt>独立实例 / Bot</dt><dd>{{ snapshot.instance_id }} / {{ snapshot.bot_id }}</dd></div>
        <div><dt>安装包版本元数据</dt><dd>{{ snapshot.version_label }}（不能证明当前运行源码）</dd></div>
        <div><dt>当前运行源码 SHA</dt><dd><code>{{ snapshot.running_release_sha256 ?? '未知' }}</code></dd></div>
        <div><dt>已保存配置 / 权限修订</dt><dd>{{ snapshot.settings_revision }} / {{ snapshot.policy_revision }}</dd></div>
        <div class="native-wide"><dt>当前启动配置 SHA</dt><dd><code>{{ snapshot.config_sha256 ?? '未提供' }}</code></dd></div>
      </dl>
      <n-alert v-if="!snapshot.managed_instance" class="notice" type="warning" :show-icon="true">
        当前不是支持的固定独立实例。此流程需要实例内 state.sqlite3 与 config.toml；请按原生部署说明准备实例。
      </n-alert>
      <template v-else>
        <p class="subtle-text">请自行把 source-template ZIP 放入此实例的 releases 目录，并填写经独立核验的 SHA256。这里仅选择文件名。</p>
        <n-alert v-if="snapshot.archives_truncated || snapshot.backups_truncated" class="notice" type="warning" :show-icon="true">
          {{ snapshot.archives_truncated ? '源包' : '' }}{{ snapshot.archives_truncated && snapshot.backups_truncated ? '与' : '' }}{{ snapshot.backups_truncated ? '回执' : '' }}列表最多显示 64 项，当前已截断；可手填已知文件名或回执编号与 SHA。
        </n-alert>
        <div class="native-fields">
          <label><span>目标源包文件名</span><n-auto-complete v-model:value="targetName" :options="sources" :disabled="busy || loading" placeholder="source-template.zip" /></label>
          <label><span>目标源包 SHA256</span><n-input v-model:value="targetSha" :disabled="busy || loading" placeholder="64 位十六进制 SHA" /></label>
          <label><span>声明的回退源包（可空）</span><n-auto-complete v-model:value="priorName" :options="sources" :disabled="busy || loading" placeholder="仅声明，不代表已安装版本" /></label>
          <label><span>声明回退包 SHA256（与文件名成对）</span><n-input v-model:value="priorSha" :disabled="busy || loading" placeholder="无回退源包时留空" /></label>
        </div>
        <n-checkbox v-model:checked="backupScopeUnderstood" :disabled="!available || phase === 'unknown' || Boolean(receipt)">
          我已理解：备份仅包含数据库与启动配置，不包含凭据、附件、其他服务或 QQ 登录态；目标包和回退包 SHA 已独立核验。
        </n-checkbox>
        <div class="native-actions">
          <n-button type="primary" :loading="phase === 'prepare'" :disabled="!canPrepare" @click="submit('prepare')">创建一致备份</n-button>
          <n-button secondary :disabled="!canNewPreparation" @click="newPreparation">准备另一份新备份</n-button>
          <span v-if="preparationId" class="subtle-text">本次准备编号：<code>{{ preparationId }}</code></span>
        </div>
        <div class="native-readback">
          <h3>读取已有回执</h3>
          <p class="subtle-text">选定回执时仍需填写与它绑定的目标源包及声明回退包；读取计划不会创建第二份备份。</p>
          <label><span>实例内已有备份</span><n-select :value="selectedBackup" :options="backupOptions" :disabled="!available" filterable clearable @update:value="chooseBackup" /></label>
          <div class="native-fields">
            <label><span>准备编号</span><n-input v-model:value="backupId" :disabled="busy || loading" placeholder="32 位小写十六进制" /></label>
            <label><span>完整私有回执 SHA256</span><n-input v-model:value="receiptSha" :disabled="busy || loading" placeholder="64 位十六进制 SHA" /></label>
          </div>
          <n-space align="center">
            <n-button secondary :disabled="!available" @click="bindManualReceipt">确认手填回执身份</n-button>
            <n-checkbox v-model:checked="schemaCompatible" :disabled="!available">我已独立核验目标 schema 与备份兼容</n-checkbox>
          </n-space>
          <div class="native-actions">
            <n-button secondary :loading="phase === 'plan'" :disabled="!canPlan" @click="submit('plan')">读取回执并生成计划</n-button>
          </div>
        </div>
      </template>
      <section v-if="receipt" class="native-readback" aria-label="持久备份回执与人工计划">
        <h3>已核验备份回执</h3>
        <n-tag :type="receipt.plan.preparation_ready ? 'success' : 'warning'" size="small">
          {{ receipt.plan.preparation_ready ? '准备条件满足 · 仍须人工执行' : '准备条件尚未满足' }}
        </n-tag>
        <dl class="native-details">
          <div><dt>准备编号</dt><dd><code>{{ receipt.preparation_id }}</code></dd></div>
          <div><dt>数据库 schema</dt><dd>{{ receipt.schema_version }}</dd></div>
          <div><dt>备份已保存配置 / 权限修订</dt><dd>{{ receipt.settings_revision }} / {{ receipt.policy_revision }}</dd></div>
          <div><dt>完整私有回执 SHA</dt><dd><code>{{ receipt.receipt_sha256 }}</code></dd></div>
          <div><dt>数据库 SHA</dt><dd><code>{{ receipt.database_sha256 }}</code></dd></div>
          <div><dt>备份启动配置 SHA</dt><dd><code>{{ receipt.config_sha256 }}</code></dd></div>
          <div><dt>目标源包 SHA</dt><dd><code>{{ receipt.target_sha256 }}</code></dd></div>
          <div><dt>声明的回退源包 SHA</dt><dd><code>{{ receipt.declared_prior_sha256 ?? '未声明' }}</code></dd></div>
        </dl>
        <n-alert v-if="receipt.plan.blockers.length" class="notice" type="warning" :show-icon="true">
          仍需满足：{{ receipt.plan.blockers.map(blockerLabel).join('；') }}
        </n-alert>
        <h3>人工更新步骤</h3><ol><li v-for="step in receipt.plan.update_steps" :key="step">{{ step }}</li></ol>
        <h3>人工回滚步骤</h3><ol><li v-for="step in receipt.plan.rollback_steps" :key="step">{{ step }}</li></ol>
        <h3>覆盖边界</h3><ul><li v-for="limit in receipt.plan.limitations" :key="limit">{{ limit }}</li></ul>
      </section>
    </template>
  </n-card>
</template>

<style scoped>
.native-release { margin-top: 16px; }
.native-details, .native-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 24px; margin: 16px 0; }
.native-details > div, .native-fields > label { min-width: 0; }
.native-wide { grid-column: 1 / -1; }
.native-details dt, label > span { display: block; color: var(--om-muted); font-size: 12px; }
.native-details dd { margin: 4px 0 0; overflow-wrap: anywhere; }
label > span { margin-bottom: 8px; }
.native-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
.native-readback { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--om-border); }
h3 { margin: 16px 0 8px; font-size: 16px; }
code { overflow-wrap: anywhere; color: var(--om-primary-dark); }
ol, ul { padding-left: 24px; }
li { margin: 8px 0; overflow-wrap: anywhere; }
@media (max-width: 900px) { .section-heading { align-items: flex-start; flex-wrap: wrap; } }
@media (max-width: 620px) { .native-details, .native-fields { grid-template-columns: minmax(0, 1fr); } }
</style>

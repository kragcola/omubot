import { reactive, watch } from 'vue'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import { cloneJson } from '@/api/guards'
import {
  currentAdminEpoch,
  expireAdminSession,
  isCurrentAdminEpoch,
  sessionState,
} from '@/app/session'
import type { Grant, ModelStatus, PolicySnapshot, TaskBinding } from '@/api/types'
import type { VisibilityGrant, VisibilityOptionView, VisibilityOptionsView, VisibilityMutationResponse } from '@/api/generated'

type SharedMaterial = 'knowledge' | 'slang' | 'style'

const GRANT_ACTIONS = ['message.read', 'model.invoke', 'message.reply', 'tool.invoke:time.now']
const MEDIA_READ_ACTION = 'media.read'
const STICKER_ACTIONS = ['message.sticker', 'media.send']
const MEMORY_ACTIONS = [
  'memory.archive',
  'memory.learn',
  'memory.retrieve',
  'memory.review',
  'memory.apply',
]
const MAX_GRANTS = 256

export interface PolicyState {
  visibilitySourceGroupId: string
  visibilityTargetGroupId: string
  visibilityMaterial: SharedMaterial
  visibilityGrantId: string
  visibilityOptions: VisibilityOptionView[]
  visibilitySelection: string[]
  visibilityConfirmed: boolean
  visibilityOptionsLoading: boolean
  visibilityOptionsPartial: boolean

  snapshot: PolicySnapshot | null
  draft: Grant[] | null
  baseRevision: number | null
  groupId: string
  userId: string
  fetchUrl: string
  httpUrl: string
  httpMethod: 'GET' | 'POST'
  privatePeerId: string
  allowPrivateHistory: boolean
  expiryHours: number
  allowHistory: boolean
  allowCurrentImage: boolean
  allowImageUpload: boolean
  allowStickerSend: boolean
  allowMemoryArchive: boolean
  allowMemoryLearn: boolean
  allowMemoryRetrieve: boolean
  allowMemoryReview: boolean
  allowMemoryApply: boolean
  loading: boolean
  saving: boolean
  error: string
  notice: string
  pendingClear: boolean
  pendingDelete: number | null
  reloadPending: boolean
}

export const policyState = reactive<PolicyState>({
  visibilitySourceGroupId: '',
  visibilityTargetGroupId: '',
  visibilityMaterial: 'knowledge',
  visibilityGrantId: '',
  visibilityOptions: [],
  visibilitySelection: [],
  visibilityConfirmed: false,
  visibilityOptionsLoading: false,
  visibilityOptionsPartial: false,

  snapshot: null,
  draft: null,
  baseRevision: null,
  groupId: '',
  userId: '',
  fetchUrl: '',
  httpUrl: '',
  httpMethod: 'GET',
  privatePeerId: '',
  allowPrivateHistory: false,
  expiryHours: 24,
  allowHistory: true,
  allowCurrentImage: false,
  allowImageUpload: false,
  allowStickerSend: false,
  allowMemoryArchive: false,
  allowMemoryLearn: false,
  allowMemoryRetrieve: false,
  allowMemoryReview: false,
  allowMemoryApply: false,
  loading: false,
  saving: false,
  error: '',
  notice: '',
  pendingClear: false,
  pendingDelete: null,
  reloadPending: false,
})

let loadSequence = 0
let saveSequence = 0
let visibilityLoadSequence = 0

function serialized(grants: Grant[]): string {
  return JSON.stringify(grants)
}

export function hasPolicyDraftChanges(): boolean {
  if (!policyState.snapshot || !policyState.draft) return false
  return serialized(policyState.snapshot.grants) !== serialized(policyState.draft)
}

function acceptSnapshot(snapshot: PolicySnapshot): void {
  policyState.snapshot = snapshot
  policyState.draft = cloneJson(snapshot.grants)
  policyState.baseRevision = snapshot.revision
  policyState.error = ''
  policyState.notice = ''
  policyState.pendingClear = false
  policyState.pendingDelete = null
  policyState.reloadPending = false
  policyState.privatePeerId = ''
  policyState.allowPrivateHistory = false
  policyState.allowMemoryArchive = false
  policyState.allowMemoryLearn = false
  policyState.allowMemoryRetrieve = false
  policyState.allowMemoryReview = false
  policyState.allowMemoryApply = false
}

export function resetPolicyState(): void {
  loadSequence += 1
  saveSequence += 1
  visibilityLoadSequence += 1
  policyState.visibilitySourceGroupId = ''
  policyState.visibilityTargetGroupId = ''
  policyState.visibilityMaterial = 'knowledge'
  policyState.visibilityGrantId = ''
  policyState.visibilityOptions = []
  policyState.visibilitySelection = []
  policyState.visibilityConfirmed = false
  policyState.visibilityOptionsLoading = false
  policyState.visibilityOptionsPartial = false
  policyState.snapshot = null
  policyState.draft = null
  policyState.baseRevision = null
  policyState.loading = false
  policyState.saving = false
  policyState.error = ''
  policyState.notice = ''
  policyState.pendingClear = false
  policyState.pendingDelete = null
  policyState.reloadPending = false
  policyState.groupId = ''
  policyState.userId = ''
  policyState.fetchUrl = ''
  policyState.httpUrl = ''
  policyState.httpMethod = 'GET'
  policyState.privatePeerId = ''
  policyState.allowPrivateHistory = false
  policyState.expiryHours = 24
  policyState.allowHistory = true
  policyState.allowCurrentImage = false
  policyState.allowImageUpload = false
  policyState.allowStickerSend = false
  policyState.allowMemoryArchive = false
  policyState.allowMemoryLearn = false
  policyState.allowMemoryRetrieve = false
  policyState.allowMemoryReview = false
  policyState.allowMemoryApply = false
}

export async function loadPolicy(force = false): Promise<void> {
  if (policyState.saving || policyState.loading) return
  if (!force && policyState.snapshot && policyState.draft) return
  const epoch = currentAdminEpoch()
  const sequence = ++loadSequence
  policyState.loading = true
  policyState.error = ''
  try {
    const snapshot = await apiRequest<PolicySnapshot>('/api/admin/policy')
    if (!isCurrentAdminEpoch(epoch) || sequence !== loadSequence) return
    acceptSnapshot(snapshot)
  } catch (error: unknown) {
    if (!isCurrentAdminEpoch(epoch) || sequence !== loadSequence) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    policyState.error = apiErrorMessage(error)
  } finally {
    if (sequence === loadSequence) policyState.loading = false
  }
}

export function requestReload(): void {
  if (policyState.saving || policyState.loading) {
    policyState.error = policyState.saving ? '权限正在保存，请等待结果返回后再刷新。' : '权限正在读取，请等待结果返回后再刷新。'
    return
  }
  policyState.reloadPending = hasPolicyDraftChanges()
  if (!policyState.reloadPending) void loadPolicy(true)
}

export function cancelReload(): void {
  policyState.reloadPending = false
}

export async function confirmReload(): Promise<void> {
  if (policyState.saving || policyState.loading) return
  policyState.reloadPending = false
  await loadPolicy(true)
}

function canEdit(): boolean {
  if (policyState.saving || policyState.loading) {
    policyState.error = policyState.saving ? '权限正在保存，请等待结果返回后再编辑。' : '权限正在读取，请等待结果返回后再编辑。'
    return false
  }
  return Boolean(policyState.draft)
}

function markDraftChanged(): void {
  policyState.error = ''
  policyState.notice = '有未保存的权限草稿。'
}

function grantIdentity(grant: Grant): string {
  const scope = grant.scope
  return [
    grant.subject,
    scope.bot_id,
    scope.kind === 'private' ? 'private' : 'group',
    scope.kind === 'private' ? scope.private_user_id : scope.group_id,
    grant.effect ?? 'allow',
    grant.provider ?? '',
    grant.model ?? '',
  ].join('\u0000')
}

function bindingForTask(modelConfig: ModelStatus, task: 'reply' | 'thinker' | 'vision'): TaskBinding | null {
  return modelConfig.task_bindings[task] ?? null
}

function privateModelBindings(snapshot: PolicySnapshot): TaskBinding[] | null {
  const reply = bindingForTask(snapshot.model_config, 'reply')
  if (!reply) return null
  const bindings = [reply]
  if (snapshot.thinker_enabled) {
    const thinker = bindingForTask(snapshot.model_config, 'thinker')
    if (!thinker) return null
    bindings.push(thinker)
  }
  const seen = new Set<string>()
  return bindings.filter((binding) => {
    const key = `${binding.policy_provider}\u0000${binding.model}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function validPrivatePeer(peer: string): boolean {
  return Boolean(peer && peer.length <= 64 && !peer.includes('*') && !peer.startsWith('system:'))
}

export function addUserGrants(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const hours = policyState.expiryHours
  if (!groupId || !userId) {
    policyState.error = '群 ID 和用户 ID 都必须填写。'
    return false
  }
  if (groupId.length > 64 || userId.length > 64 || groupId.includes('*') || userId.includes('*')) {
    policyState.error = '群 ID 和用户 ID 必须是 64 字符以内的精确 ID，不能包含 *。'
    return false
  }
  if (userId.startsWith('system:')) {
    policyState.error = 'system: 是后台任务保留主体，请使用下方的独立授权入口。'
    return false
  }
  if (!Number.isFinite(hours) || hours <= 0 || hours > 8760) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  if (policyState.allowImageUpload && !policyState.allowCurrentImage) {
    policyState.error = '模型图片上传必须同时允许读取本轮当前图片。'
    return false
  }
  const tasks: Array<'reply' | 'thinker'> = policyState.snapshot.thinker_enabled ? ['reply', 'thinker'] : ['reply']
  const destinations: TaskBinding[] = []
  const destinationKeys = new Set<string>()
  for (const task of tasks) {
    const binding = bindingForTask(policyState.snapshot.model_config, task)
    if (!binding) continue
    const key = `${binding.policy_provider}\u0000${binding.model}`
    if (!destinationKeys.has(key)) {
      destinationKeys.add(key)
      destinations.push(binding)
    }
  }
  if (!destinations.length) {
    policyState.error = '当前运行快照没有可用于授权的回复模型。'
    return false
  }
  const expiresAt = Math.ceil(Date.now() / 1000 + hours * 3600)
  const actions = [...GRANT_ACTIONS]
  if (policyState.allowCurrentImage) actions.push(MEDIA_READ_ACTION)
  if (policyState.allowStickerSend) actions.push(...STICKER_ACTIONS)
  const memoryFlags = [
    policyState.allowMemoryArchive,
    policyState.allowMemoryLearn,
    policyState.allowMemoryRetrieve,
    policyState.allowMemoryReview,
    policyState.allowMemoryApply,
  ]
  actions.push(...MEMORY_ACTIONS.filter((_action, index) => memoryFlags[index]))
  const generated = destinations.map((binding): Grant => ({
    subject: userId,
    scope: { bot_id: policyState.snapshot?.bot_id ?? '', group_id: groupId },
    actions: [...actions],
    effect: 'allow',
    provider: binding.policy_provider,
    model: binding.model,
    allow_history: policyState.allowHistory,
    allow_images: policyState.allowImageUpload,
    expires_at: expiresAt,
  }))
  const next = [...policyState.draft]
  for (const grant of generated) {
    const existing = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
    if (existing >= 0) {
      const previous = next[existing]
      if (!previous) continue
      next[existing] = {
        ...grant,
        actions: Array.from(new Set([...previous.actions, ...grant.actions])),
        allow_history: previous.allow_history ?? grant.allow_history,
        allow_images: previous.allow_images ?? grant.allow_images,
      }
    }
    else next.push(grant)
  }
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  policyState.groupId = ''
  policyState.userId = ''
  markDraftChanged()
  return true
}

export function canAddPrivateConversationGrants(): boolean {
  const snapshot = policyState.snapshot
  const peer = policyState.privatePeerId.trim()
  return !policyState.saving && !policyState.loading && Boolean(policyState.draft && snapshot)
    && snapshot?.private_conversation_enabled === true
    && Array.isArray(snapshot.private_conversation_peers)
    && snapshot.private_conversation_peers.includes(peer)
    && validPrivatePeer(peer)
    && Number.isFinite(policyState.expiryHours) && policyState.expiryHours > 0 && policyState.expiryHours <= 8760
    && privateModelBindings(snapshot) !== null
}

export function addPrivateConversationGrants(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const snapshot = policyState.snapshot
  const peer = policyState.privatePeerId.trim()
  const hours = policyState.expiryHours
  if (snapshot.private_conversation_enabled !== true) {
    policyState.error = '当前运行版本未启用私聊会话。'
    return false
  }
  if (!Array.isArray(snapshot.private_conversation_peers)
    || !snapshot.private_conversation_peers.includes(peer) || !validPrivatePeer(peer)) {
    policyState.error = '请输入运行版名单中的精确私聊用户 ID。'
    return false
  }
  if (!Number.isFinite(hours) || hours <= 0 || hours > 8760) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  const bindings = privateModelBindings(snapshot)
  if (!bindings) {
    policyState.error = '当前运行快照没有完整的私聊回复模型配置。'
    return false
  }
  const expiresAt = Math.ceil(Date.now() / 1000 + hours * 3600)
  const privateScope = { kind: 'private' as const, bot_id: snapshot.bot_id, private_user_id: peer }
  const generated: Grant[] = [
    {
      subject: peer,
      scope: privateScope,
      actions: ['message.read', 'message.reply'],
      effect: 'allow',
      provider: '',
      model: '',
      allow_history: false,
      allow_images: false,
      expires_at: expiresAt,
    },
    ...bindings.map((binding): Grant => ({
      subject: peer,
      scope: privateScope,
      actions: ['model.invoke'],
      effect: 'allow',
      provider: binding.policy_provider,
      model: binding.model,
      allow_history: policyState.allowPrivateHistory,
      allow_images: false,
      expires_at: expiresAt,
    })),
  ]
  const next = [...policyState.draft]
  for (const grant of generated) {
    const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
    if (index >= 0) {
      const previous = next[index]!
      next[index] = {
        ...previous,
        actions: Array.from(new Set([...previous.actions, ...grant.actions])),
        allow_history: grant.allow_history,
        allow_images: false,
        expires_at: expiresAt,
      }
    }
    else next.push(grant)
  }
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  policyState.privatePeerId = ''
  policyState.allowPrivateHistory = false
  markDraftChanged()
  return true
}

export function addSearchGrant(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const destination = policyState.snapshot.tool_destinations?.find((item) => item.tool_id === 'web.search')
  if (!destination) {
    policyState.error = '当前运行版本未注册搜索工具，请先保存并应用搜索配置。'
    return false
  }
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const hours = policyState.expiryHours
  if (!groupId || !userId || groupId.length > 64 || userId.length > 64
    || groupId.includes('*') || userId.includes('*') || userId.startsWith('system:')) {
    policyState.error = '请填写精确的群 ID 和普通用户 ID。'
    return false
  }
  if (!Number.isFinite(hours) || hours <= 0 || hours > 8760) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  const grant: Grant = {
    subject: userId,
    scope: { bot_id: policyState.snapshot.bot_id, group_id: groupId },
    actions: ['tool.invoke:web.search'], effect: 'allow',
    provider: destination.destination, model: 'web.search',
    allow_history: policyState.allowHistory, allow_images: policyState.allowImageUpload,
    expires_at: Math.ceil(Date.now() / 1000 + hours * 3600),
  }
  const next = [...policyState.draft]
  const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
  if (index >= 0) {
    const previous = next[index]!
    next[index] = { ...grant, actions: [...new Set([...previous.actions, ...grant.actions])] }
  }
  else next.push(grant)
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

function canonicalFetchUrl(value: string): string | null {
  if (!value || value.length > 2048 || /[\u0000-\u001f\u007f]/u.test(value) || value.includes('\\')) return null
  const input = value.trim()
  if (!input || input.includes('#')) return null
  try {
    const url = new URL(input)
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.port) return null
    return url.href.length <= 2048 ? url.href : null
  } catch {
    return null
  }
}

function exactFetchSubject(groupId: string, userId: string): boolean {
  return Boolean(groupId && userId && groupId.length <= 64 && userId.length <= 64
    && !groupId.includes('*') && !userId.includes('*') && !userId.startsWith('system:'))
}

function validGrantHours(hours: number): boolean {
  return Number.isFinite(hours) && hours > 0 && hours <= 8760
}

export function canAddFetchGrant(): boolean {
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  return !policyState.saving && !policyState.loading
    && Boolean(policyState.snapshot?.request_destination_tools?.includes('web.fetch'))
    && Boolean(policyState.draft)
    && exactFetchSubject(groupId, userId)
    && validGrantHours(policyState.expiryHours)
    && canonicalFetchUrl(policyState.fetchUrl) !== null
}

export function addFetchGrant(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  if (!policyState.snapshot.request_destination_tools?.includes('web.fetch')) {
    policyState.error = '当前运行版本未启用网页读取。'
    return false
  }
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const hours = policyState.expiryHours
  const targetUrl = canonicalFetchUrl(policyState.fetchUrl)
  if (!exactFetchSubject(groupId, userId)) {
    policyState.error = '请填写精确的群 ID 和普通用户 ID。'
    return false
  }
  if (!validGrantHours(hours)) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  if (!targetUrl) {
    policyState.error = '请输入有效的 HTTPS URL（最多 2048 字符），且不能包含凭据、片段或非标准端口。'
    return false
  }
  const grant: Grant = {
    subject: userId,
    scope: { bot_id: policyState.snapshot.bot_id, group_id: groupId },
    actions: ['tool.invoke:web.fetch'],
    effect: 'allow',
    provider: targetUrl,
    model: 'web.fetch',
    allow_history: policyState.allowHistory,
    allow_images: false,
    expires_at: Math.ceil(Date.now() / 1000 + hours * 3600),
  }
  const next = [...policyState.draft]
  const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
  if (index >= 0) {
    const previous = next[index]!
    next[index] = { ...grant, actions: [...new Set([...previous.actions, ...grant.actions])] }
  }
  else next.push(grant)
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

function httpToolId(method: 'GET' | 'POST'): 'http.get' | 'http.post' {
  return method === 'GET' ? 'http.get' : 'http.post'
}

export function canAddHttpGrant(): boolean {
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const snapshot = policyState.snapshot
  const draft = policyState.draft
  const toolId = httpToolId(policyState.httpMethod)
  if (policyState.saving || policyState.loading || !snapshot || !draft
    || !snapshot.request_destination_tools?.includes(toolId)
    || !exactFetchSubject(groupId, userId)
    || !validGrantHours(policyState.expiryHours)) return false
  const targetUrl = canonicalFetchUrl(policyState.httpUrl)
  if (!targetUrl) return false
  const identity = [userId, snapshot.bot_id, groupId, 'allow', targetUrl, toolId].join('\u0000')
  return draft.length < MAX_GRANTS || draft.some((grant) => grantIdentity(grant) === identity)
}

export function addHttpGrant(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const toolId = httpToolId(policyState.httpMethod)
  if (!policyState.snapshot.request_destination_tools?.includes(toolId)) {
    policyState.error = `当前运行版本未启用 ${policyState.httpMethod} 请求授权。`
    return false
  }
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const hours = policyState.expiryHours
  const targetUrl = canonicalFetchUrl(policyState.httpUrl)
  if (!exactFetchSubject(groupId, userId)) {
    policyState.error = '请填写精确的群 ID 和普通用户 ID。'
    return false
  }
  if (!validGrantHours(hours)) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  if (!targetUrl) {
    policyState.error = '请输入有效的 HTTPS URL（最多 2048 字符），且不能包含凭据、片段或非标准端口。'
    return false
  }
  const grant: Grant = {
    subject: userId,
    scope: { bot_id: policyState.snapshot.bot_id, group_id: groupId },
    actions: [`tool.invoke:${toolId}`],
    effect: 'allow',
    provider: targetUrl,
    model: toolId,
    allow_history: policyState.allowHistory,
    allow_images: false,
    expires_at: Math.ceil(Date.now() / 1000 + hours * 3600),
  }
  const next = [...policyState.draft]
  const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
  if (index >= 0) {
    const previous = next[index]!
    next[index] = { ...grant, actions: [...new Set([...previous.actions, ...grant.actions])] }
  }
  else next.push(grant)
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

export function addStickerAdministrationGrant(includeDescription = false): boolean {
  const snapshot = policyState.snapshot
  if (!canEdit() || !snapshot || !policyState.draft) return false
  const groupId = policyState.groupId.trim()
  const hours = policyState.expiryHours
  if (!exactFetchSubject(groupId, 'web-admin') || !validGrantHours(hours)) {
    policyState.error = '请填写精确群 ID 和有效期。'
    return false
  }
  const expiry = Math.ceil(Date.now() / 1000 + hours * 3600)
  const grants: Grant[] = [{ subject: 'web-admin', scope: { bot_id: snapshot.bot_id, group_id: groupId },
    actions: ['sticker.manage'], effect: 'allow', provider: '', model: '', allow_images: false,
    allow_history: false, expires_at: expiry }]
  if (includeDescription) {
    const binding = bindingForTask(snapshot.model_config, 'vision')
    if (snapshot.sticker_description_available !== true || !binding) {
      policyState.error = '当前运行版本未装配可读图的 vision 模型。'
      return false
    }
    grants.push({ subject: 'web-admin', scope: { bot_id: snapshot.bot_id, group_id: groupId },
      actions: ['message.read', 'media.read', 'model.invoke'], effect: 'allow',
      provider: binding.policy_provider, model: binding.model, allow_images: true,
      allow_history: false, expires_at: expiry })
  }
  const next = [...policyState.draft]
  for (const grant of grants) {
    const index = next.findIndex(item => grantIdentity(item) === grantIdentity(grant))
    if (index < 0) next.push(grant)
    else {
      const previous = next[index]!
      next[index] = { ...previous, actions: [...new Set([...previous.actions, ...grant.actions])],
        allow_images: previous.allow_images === true || grant.allow_images,
        expires_at: grant.expires_at }
    }
  }
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

export function canAddDiagnosticGrant(): boolean {
  const snapshot = policyState.snapshot
  const draft = policyState.draft
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  if (policyState.saving || policyState.loading || !snapshot || !draft
    || snapshot.diagnostic_commands_enabled !== true
    || !Array.isArray(snapshot.diagnostic_commands_groups)
    || !snapshot.diagnostic_commands_groups.includes(groupId)
    || !exactFetchSubject(groupId, userId)
    || !validGrantHours(policyState.expiryHours)) return false
  const identity = [userId, snapshot.bot_id, groupId, 'allow', '', ''].join('\u0000')
  return draft.length < MAX_GRANTS || draft.some((grant) => grantIdentity(grant) === identity)
}

export function addDiagnosticGrant(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const snapshot = policyState.snapshot
  const groupId = policyState.groupId.trim()
  const userId = policyState.userId.trim()
  const hours = policyState.expiryHours
  if (snapshot.diagnostic_commands_enabled !== true) {
    policyState.error = '当前运行版本未启用聊天诊断许可。'
    return false
  }
  if (!Array.isArray(snapshot.diagnostic_commands_groups)
    || !snapshot.diagnostic_commands_groups.includes(groupId)) {
    policyState.error = '当前群不在运行版的聊天诊断名单中。'
    return false
  }
  if (!exactFetchSubject(groupId, userId)) {
    policyState.error = '请填写精确的群 ID 和普通用户 ID。'
    return false
  }
  if (!validGrantHours(hours)) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  const grant: Grant = {
    subject: userId,
    scope: { bot_id: snapshot.bot_id, group_id: groupId },
    actions: ['message.read', 'message.reply', 'status.read'],
    effect: 'allow',
    provider: '',
    model: '',
    allow_history: false,
    allow_images: false,
    expires_at: Math.ceil(Date.now() / 1000 + hours * 3600),
  }
  const next = [...policyState.draft]
  const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
  if (index >= 0) {
    const previous = next[index]!
    next[index] = { ...previous, expires_at: grant.expires_at }
  }
  else next.push(grant)
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

export function addBackgroundModelGrant(task: 'schedule' | 'dream'): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  const groupId = policyState.groupId.trim()
  const hours = policyState.expiryHours
  if (!groupId || groupId.length > 64 || groupId.includes('*')) {
    policyState.error = '后台模型授权需要 64 字符以内的精确群 ID。'
    return false
  }
  if (!Number.isFinite(hours) || hours <= 0 || hours > 8760) {
    policyState.error = '有效期需在 0 到 8760 小时之间。'
    return false
  }
  const binding = policyState.snapshot.model_config.task_bindings[task]
  if (!binding) {
    policyState.error = '当前运行快照没有对应的后台模型配置。'
    return false
  }
  const grant: Grant = {
    subject: `system:${task}`,
    scope: { bot_id: policyState.snapshot.bot_id, group_id: groupId },
    actions: [`model.${task}`],
    effect: 'allow',
    provider: binding.policy_provider,
    model: binding.model,
    allow_history: false,
    allow_images: false,
    expires_at: Math.ceil(Date.now() / 1000 + hours * 3600),
  }
  const next = [...policyState.draft]
  const existing = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
  if (existing >= 0) {
    const previous = next[existing]
    if (previous && (previous.actions.length !== 1 || previous.actions[0] !== `model.${task}`)) {
      policyState.error = '已有同主体的混合授权，请先人工检查该条权限。'
      return false
    }
    next[existing] = grant
  }
  else next.push(grant)
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

export function requestDeleteGrant(index: number): void {
  if (!canEdit() || !policyState.draft || index < 0 || index >= policyState.draft.length) return
  policyState.pendingDelete = index
}

export function cancelDeleteGrant(): void {
  policyState.pendingDelete = null
}

export function confirmDeleteGrant(): void {
  if (!canEdit() || !policyState.draft || policyState.pendingDelete === null) return
  const index = policyState.pendingDelete
  if (index < 0 || index >= policyState.draft.length) return
  policyState.draft.splice(index, 1)
  policyState.pendingDelete = null
  markDraftChanged()
}

export function requestClearGrants(): void {
  if (!canEdit() || !policyState.draft) return
  if (!policyState.draft.length) {
    policyState.error = '当前没有可清空的授权。'
    return
  }
  policyState.pendingClear = true
}

export function cancelClearGrants(): void {
  policyState.pendingClear = false
}

export function confirmClearGrants(): void {
  if (!canEdit() || !policyState.draft) return
  policyState.draft = []
  policyState.pendingClear = false
  markDraftChanged()
}

function sharingGroupsValid(): boolean {
  const source = policyState.visibilitySourceGroupId.trim()
  const target = policyState.visibilityTargetGroupId.trim()
  return Boolean(source && target && source !== target && source.length <= 64 && target.length <= 64
    && !source.includes('*') && !target.includes('*'))
}

export function addVisibilityGovernanceDraft(): boolean {
  if (!canEdit() || !policyState.snapshot || !policyState.draft) return false
  if (!sharingGroupsValid() || !validGrantHours(policyState.expiryHours)) {
    policyState.error = '请填写不同的精确来源群和目标群，以及有效授权时长。'
    return false
  }
  const next = cloneJson(policyState.draft)
  const groups: Array<[string, string[]]> = [
    [policyState.visibilitySourceGroupId.trim(), ['policy.configure', 'knowledge.review', 'memory.retrieve', 'memory.review']],
    [policyState.visibilityTargetGroupId.trim(), ['policy.configure']],
  ]
  for (const [group, actions] of groups) {
    const grant: Grant = {subject: 'web-admin', scope: {bot_id: policyState.snapshot.bot_id, kind: 'group', group_id: group},
      actions, effect: 'allow', provider: '', model: '', allow_history: false, allow_images: false,
      expires_at: Math.ceil(Date.now() / 1000 + policyState.expiryHours * 3600)}
    const index = next.findIndex((item) => grantIdentity(item) === grantIdentity(grant))
    if (index < 0) next.push(grant)
    else next[index] = {...grant, actions: [...new Set([...next[index]!.actions, ...actions])]}
  }
  if (next.length > MAX_GRANTS) {
    policyState.error = `权限草稿最多保留 ${MAX_GRANTS} 条，请先删除旧授权。`
    return false
  }
  policyState.draft = next
  markDraftChanged()
  return true
}

export function visibilityOptionKey(option: VisibilityOptionView): string {
  return JSON.stringify(option.ref)
}

export async function loadVisibilityOptions(): Promise<void> {
  if (!canEdit() || !policyState.snapshot || policyState.visibilityOptionsLoading) return
  const group = policyState.visibilitySourceGroupId.trim()
  if (!group || group.length > 64 || group.includes('*')) {
    policyState.error = '请填写精确的来源群 ID。'
    return
  }
  const material = policyState.visibilityMaterial
  const epoch = currentAdminEpoch()
  const sequence = ++visibilityLoadSequence
  policyState.visibilityOptionsLoading = true
  policyState.visibilityOptions = []
  policyState.visibilitySelection = []
  policyState.visibilityConfirmed = false
  policyState.error = ''
  try {
    const result = await apiRequest<VisibilityOptionsView>(`/api/admin/policy/visibility/options?group_id=${encodeURIComponent(group)}&material_type=${material}`)
    if (!isCurrentAdminEpoch(epoch) || sequence !== visibilityLoadSequence) return
    policyState.visibilityOptions = result.items
    policyState.visibilityOptionsPartial = result.partial
  } catch (error: unknown) {
    if (!isCurrentAdminEpoch(epoch) || sequence !== visibilityLoadSequence) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    policyState.error = apiErrorMessage(error)
  } finally {
    if (sequence === visibilityLoadSequence) policyState.visibilityOptionsLoading = false
  }
}

export function canGrantVisibility(): boolean {
  const id = policyState.visibilityGrantId.trim()
  return Boolean(policyState.snapshot && policyState.draft && !policyState.loading && !policyState.saving
    && !policyState.visibilityOptionsLoading && !hasPolicyDraftChanges() && sharingGroupsValid()
    && id && id.length <= 64 && !id.includes('*') && validGrantHours(policyState.expiryHours)
    && policyState.visibilityConfirmed && policyState.visibilitySelection.length > 0
    && policyState.visibilitySelection.length <= 64)
}

async function mutateVisibility(path: string, body: object): Promise<boolean> {
  if (!canEdit() || !policyState.snapshot || hasPolicyDraftChanges()) {
    policyState.error = '请先保存或放弃常规权限草稿，再修改共享许可。'
    return false
  }
  const epoch = currentAdminEpoch()
  const sequence = ++saveSequence
  policyState.saving = true
  policyState.error = ''
  try {
    const result = await apiRequest<VisibilityMutationResponse>(path, {method: 'POST', adminMutation: true, body})
    if (!isCurrentAdminEpoch(epoch) || sequence !== saveSequence || !policyState.snapshot) return false
    const grants = (policyState.snapshot.visibility_grants ?? []).filter((g) => g.grant_id !== result.visibility_grant.grant_id)
    grants.push(result.visibility_grant)
    policyState.snapshot = {...policyState.snapshot, revision: result.revision, visibility_grants: grants}
    policyState.baseRevision = result.revision
    policyState.visibilityConfirmed = false
    policyState.notice = '共享许可已保存；是否消费仍以当前运行版本的共享开关为准。'
    return true
  } catch (error: unknown) {
    if (!isCurrentAdminEpoch(epoch) || sequence !== saveSequence) return false
    if (isApiError(error) && error.status === 401) expireAdminSession()
    policyState.error = `${apiErrorMessage(error)} 请重新读取权限核验结果，不要盲目重复提交。`
    return false
  } finally {
    if (sequence === saveSequence) policyState.saving = false
  }
}

export async function grantVisibility(): Promise<boolean> {
  if (!canGrantVisibility() || !policyState.snapshot) return false
  const snapshot = policyState.snapshot
  const id = policyState.visibilityGrantId.trim()
  const selected = policyState.visibilityOptions.filter((option) => policyState.visibilitySelection.includes(visibilityOptionKey(option)))
  if (selected.length !== policyState.visibilitySelection.length) {
    policyState.error = '所选对象已变化，请重新读取并确认。'
    return false
  }
  const existing = snapshot.visibility_grants?.find((g) => g.grant_id === id)
  return await mutateVisibility('/api/admin/policy/visibility/grant', {
    expected_policy_revision: snapshot.revision, grant_id: id,
    expected_grant_revision: existing?.grant_revision ?? null,
    source_scope: {bot_id: snapshot.bot_id, kind: 'group', group_id: policyState.visibilitySourceGroupId.trim()},
    target_scope: {bot_id: snapshot.bot_id, kind: 'group', group_id: policyState.visibilityTargetGroupId.trim()},
    material_type: policyState.visibilityMaterial, object_refs: selected.map((option) => option.ref),
    non_personal_projection_confirmed: true,
    expires_at: Math.ceil(Date.now() / 1000 + policyState.expiryHours * 3600),
  })
}

export async function revokeVisibility(grant: VisibilityGrant): Promise<boolean> {
  if (!policyState.snapshot || grant.status !== 'active') return false
  return await mutateVisibility('/api/admin/policy/visibility/revoke', {
    expected_policy_revision: policyState.snapshot.revision, grant_id: grant.grant_id,
    expected_grant_revision: grant.grant_revision,
  })
}

watch(() => [policyState.visibilitySourceGroupId, policyState.visibilityMaterial], () => {
  visibilityLoadSequence += 1
  policyState.visibilityOptions = []
  policyState.visibilitySelection = []
  policyState.visibilityConfirmed = false
  policyState.visibilityOptionsLoading = false
  policyState.visibilityOptionsPartial = false
})

export async function savePolicy(): Promise<boolean> {
  if (!policyState.draft || policyState.baseRevision === null || policyState.saving || policyState.loading) return false
  const epoch = currentAdminEpoch()
  const sequence = ++saveSequence
  policyState.saving = true
  policyState.error = ''
  try {
    const response = await apiRequest<{ revision: number }>('/api/admin/policy', {
      method: 'PUT',
      adminMutation: true,
      body: { expected_revision: policyState.baseRevision, grants: cloneJson(policyState.draft) },
    })
    if (!isCurrentAdminEpoch(epoch) || sequence !== saveSequence || !policyState.snapshot || !policyState.draft) return false
    const savedGrants = cloneJson(policyState.draft)
    policyState.snapshot = { ...policyState.snapshot, revision: response.revision, grants: savedGrants }
    policyState.draft = savedGrants
    policyState.baseRevision = response.revision
    policyState.notice = '权限已保存，撤权会立即影响后续任务。'
    return true
  } catch (error: unknown) {
    if (!isCurrentAdminEpoch(epoch) || sequence !== saveSequence) return false
    if (isApiError(error) && error.status === 401) expireAdminSession()
    policyState.error = apiErrorMessage(error)
    return false
  } finally {
    if (sequence === saveSequence) policyState.saving = false
  }
}

watch(() => sessionState.generation, () => {
  if (!sessionState.adminAuthenticated) resetPolicyState()
})

watch(() => policyState.allowCurrentImage, (allowed) => {
  if (!allowed) policyState.allowImageUpload = false
})

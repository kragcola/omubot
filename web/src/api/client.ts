import type { ApiErrorPayload } from './types'

const ADMIN_REQUEST_HEADER = 'X-Omubot-Request'

export interface ApiRequestOptions extends Omit<RequestInit, 'body' | 'headers'> {
  body?: unknown
  headers?: HeadersInit
  adminMutation?: boolean
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly payload: unknown

  constructor(status: number, code: string, payload: unknown) {
    super(code || `http_${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = code || `http_${status}`
    this.payload = payload
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function apiErrorMessage(error: unknown): string {
  if (!isApiError(error)) return '本机服务暂不可用，请稍后重试。'
  const labels: Record<string, string> = {
    unauthorized: '会话已失效，请重新登录。',
    catalog_key_required: '当前配置没有可用的已保存密钥，请先填写 API key。',
    catalog_destination_mismatch: '地址或接口格式已改变，请填写新地址的 API key；旧密钥不会发送到新地址。',
    invalid_pagination: '服务返回了无效的分页游标，请稍后重试或手填模型 ID。',
    catalog_timeout: '获取模型列表超时，请稍后重试。',
    upstream_http: '模型目录接口返回错误，请核对地址、API key 与服务权限。',
    upstream_unavailable: '模型目录服务暂不可达，请检查地址或稍后重试。',
    invalid_response: '服务返回的模型目录格式不兼容，可手动填写模型 ID。',
    redirect_denied: '模型目录地址发生跳转，请直接填写最终服务地址。',
    response_too_large: '模型目录超过读取大小上限，请手填模型 ID。',
    napcat_unauthorized: 'NapCat 管理凭据已失效，请重新连接。',
    napcat_request_rejected: 'NapCat 拒绝请求，请检查管理令牌、动态验证码或当前登录状态。',
    napcat_upstream_unavailable: '无法连接 NapCat，请先启动独立实例并核对地址。',
    napcat_upstream_http: 'NapCat 服务返回错误，请检查地址和服务状态。',
    napcat_invalid_token: 'NapCat 管理令牌格式无效，请检查输入。',
    napcat_invalid_totp: '动态验证码格式无效。',
    napcat_client_closed: '管理连接已关闭，请重新连接。',
    napcat_not_connected: '请先连接独立 NapCat WebUI。',
    napcat_two_factor_required: 'NapCat 已启用二次验证，请填写动态验证码后重新连接。',
    napcat_invalid_endpoint: '请填写本机独立 NapCat WebUI 根地址与端口，例如 http://127.0.0.1:6100。',
    napcat_redirect_denied: 'NapCat 地址发生重定向，请填写实际 WebUI 地址。',
    napcat_invalid_response: 'NapCat 返回格式不兼容，请核对版本及 WebUI 地址。',
    napcat_response_too_large: 'NapCat 返回内容超过大小限制。',
    napcat_runtime_disabled: '尚未配置托管实例，请先完成独立 NapCat 安装。',
    napcat_docker_unavailable: '无法调用 Docker，请启动 Docker Desktop 后重试。',
    napcat_docker_timeout: 'Docker 操作超时，请刷新实例状态后再决定是否重试。',
    napcat_container_command_failed: 'Docker 操作失败，请检查 Docker 是否运行及独立容器是否存在。',
    napcat_container_identity_mismatch: '实例身份或隔离配置不匹配，已拒绝操作，请检查部署。',
    napcat_container_invalid_response: '无法确认独立容器状态，请检查 Docker。',
    napcat_container_not_ready: '实例已尝试启动，但管理服务尚未就绪；可重试连接或停止实例。',
    napcat_log_output_limit: '日志输出超过本次采样上限，请稍后重试。',
    cross_origin_denied: '请求来源被拒绝，请从本机页面操作。',
    request_header_required: '请求缺少页面安全标记，请从本页面重新操作。',
    revision_conflict: '版本冲突：已有其他变更。请重新加载后再保存，页面没有自动覆盖。',
    candidate_not_found: '找不到这条记忆候选，请重新加载列表。',
    candidate_not_reviewable: '这条候选已处理，不能重复审核；请刷新列表。',
    conflict_pending: '这条候选与已有事实冲突，当前只能拒绝或撤回，不能直接批准。',
    invalid_memory_cursor: '候选列表的分页位置已失效，请重新从第一页加载。',
    invalid_memory_page_limit: '每页数量无效，请刷新页面。',
    invalid_memory_status: '候选状态筛选无效，请重新选择。',
    memory_candidate_limit: '候选过多，请使用分页列表。',
    story_disabled: '故事功能当前已关闭。',
    story_unassembled: '故事服务尚未装配完成。',
    story_scope_denied: '该群尚未通过 Worldbook 范围检查。请到“模型配置”→“N7 Worldbook”开启总门、加入准确群 ID 到白名单，保存后重启。',
    story_admin_authorization_required: '故事写入需要管理员授权。',
    story_main_ambiguous: '该群已存在主线 Arc，当前接口不允许再创建另一条。',
    dream_scope_denied: 'Dream 提案不属于当前允许的群或 Arc。',
    dream_authorization_required: 'Dream 提案门或运行授权尚未配置。请到“模型配置”→“N7 Worldbook”开启 Dream 提案门并确认群白名单；生成模型调用还需在“权限策略”授权 model.dream，保存后重启。',
    dream_authorization_denied: '当前管理员没有该群的 Dream 操作授权。',
    dream_model_unavailable_offline: '离线模式不会调用真实模型。切换实时模式并配置可用模型后，服务端才会接受 Dream 生成请求。',
    dream_runtime_unassembled: 'Dream 运行时尚未装配完成。',
    dream_arc_missing: '目标 Arc 已不存在，请重新加载故事。',
    dream_not_validated: '提案尚未通过服务端校验，未提交虚构事件。',
    dream_rejected: '该提案已拒绝，不能提交。',
    dream_decision_finalized: '提案状态已改变，请重新加载后核对。',
    dream_decision_conflict: '提案在其他操作中已改变，请重新加载后核对。',
    dream_proposal_missing: '找不到这条 Dream 提案，请重新加载列表。',
    invalid_dream_cursor: 'Dream 提案分页位置已失效，请从第一页重新查询。',
    invalid_dream_page_limit: 'Dream 提案分页长度无效，请重新查询。',
    conflict: '版本冲突：已有其他变更。请重新加载后再保存。',
    invalid_input: '输入不符合服务端合同，请检查后重试。',
    invalid_settings: '配置文档无效，请检查全部字段。',
    settings_version_not_found: '找不到所选配置版本，请重新加载版本列表。',
    denied: '当前授权不允许此操作。',
    not_found: '当前运行模式不支持此操作。',
    busy: '当前任务队列已满。',
    duplicate: '这条请求已经存在。',
    input_limit: '输入过长。',
    login_rate_limit: '尝试过于频繁，请稍后重试。',
  }
  return labels[error.code] ?? `请求失败（HTTP ${error.status}）：${error.code}`
}

function payloadCode(payload: unknown): string {
  if (typeof payload === 'object' && payload !== null) {
    const candidate = payload as ApiErrorPayload
    if (typeof candidate.error === 'string') return candidate.error
    if (typeof candidate.detail === 'string') return candidate.detail
  }
  return ''
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { body, headers: inputHeaders, adminMutation, ...requestInit } = options
  const headers = new Headers(inputHeaders)
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (adminMutation) headers.set(ADMIN_REQUEST_HEADER, '1')
  const response = await fetch(path, {
    ...requestInit,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
    headers,
  })
  const raw = await response.text()
  let payload: unknown = null
  if (raw) {
    try {
      payload = JSON.parse(raw) as unknown
    } catch {
      payload = raw
    }
  }
  if (!response.ok) throw new ApiError(response.status, payloadCode(payload), payload)
  return payload as T
}

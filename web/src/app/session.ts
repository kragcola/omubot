import { reactive } from 'vue'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { ConfigSnapshot, StatusSnapshot } from '@/api/types'

export interface SessionState {
  statusAuthenticated: boolean
  adminAuthenticated: boolean
  status: StatusSnapshot | null
  statusLoading: boolean
  statusError: string
  authBusy: 'status' | 'admin' | 'status-logout' | 'admin-logout' | null
  authNotice: string
  generation: number
}

export const sessionState = reactive<SessionState>({
  statusAuthenticated: false,
  adminAuthenticated: false,
  status: null,
  statusLoading: false,
  statusError: '',
  authBusy: null,
  authNotice: '',
  generation: 0,
})

let statusRequestEpoch = 0
let adminRequestEpoch = 0
let explicitStatusSession = false
let statusRequestSequence = 0

export function currentSessionEpoch(): number {
  return adminRequestEpoch
}

export function isCurrentSessionEpoch(epoch: number): boolean {
  return epoch === adminRequestEpoch
}

export function currentAdminEpoch(): number {
  return adminRequestEpoch
}

export function isCurrentAdminEpoch(epoch: number): boolean {
  return epoch === adminRequestEpoch
}

function isCurrentStatusEpoch(epoch: number): boolean {
  return epoch === statusRequestEpoch
}

function invalidateStatusRequests(): number {
  statusRequestEpoch += 1
  statusRequestSequence += 1
  sessionState.statusLoading = false
  return statusRequestEpoch
}

export function invalidateAdminRequests(): number {
  adminRequestEpoch += 1
  return adminRequestEpoch
}

export async function refreshStatus(): Promise<StatusSnapshot | null> {
  const epoch = statusRequestEpoch
  const sequence = ++statusRequestSequence
  sessionState.statusLoading = true
  try {
    const data = await apiRequest<StatusSnapshot>('/api/status')
    if (!isCurrentStatusEpoch(epoch) || sequence !== statusRequestSequence) return null
    sessionState.status = data
    sessionState.statusAuthenticated = true
    sessionState.statusError = ''
    return data
  } catch (error: unknown) {
    if (!isCurrentStatusEpoch(epoch) || sequence !== statusRequestSequence) return null
    if (isApiError(error) && error.status === 401) {
      sessionState.statusAuthenticated = false
      sessionState.status = null
      sessionState.statusError = ''
    } else {
      sessionState.statusError = apiErrorMessage(error)
    }
    return null
  } finally {
    if (isCurrentStatusEpoch(epoch) && sequence === statusRequestSequence) sessionState.statusLoading = false
  }
}

export async function probeAdmin(): Promise<boolean> {
  const epoch = currentAdminEpoch()
  try {
    await apiRequest<ConfigSnapshot>('/api/admin/config')
    if (!isCurrentAdminEpoch(epoch)) return false
    sessionState.adminAuthenticated = true
    if (!sessionState.statusAuthenticated) await refreshStatus()
    return true
  } catch (error: unknown) {
    if (!isCurrentAdminEpoch(epoch)) return false
    if (isApiError(error) && error.status === 401) {
      if (sessionState.adminAuthenticated) expireAdminSession()
      else sessionState.adminAuthenticated = false
    }
    return false
  }
}

export async function loginStatus(token: string): Promise<void> {
  if (sessionState.authBusy) return
  const epoch = statusRequestEpoch
  sessionState.authBusy = 'status'
  try {
    await apiRequest<{ authenticated: boolean }>('/session', {
      method: 'POST',
      body: { token },
    })
    if (!isCurrentStatusEpoch(epoch)) return
    invalidateStatusRequests()
    explicitStatusSession = true
    sessionState.authNotice = ''
    // The epoch changes here, so the finally block cannot be the only cleanup.
    sessionState.authBusy = null
    await refreshStatus()
  } finally {
    if (isCurrentStatusEpoch(epoch) && sessionState.authBusy === 'status') sessionState.authBusy = null
  }
}

export async function loginAdmin(token: string): Promise<void> {
  if (sessionState.authBusy) return
  const epoch = currentAdminEpoch()
  sessionState.authBusy = 'admin'
  try {
    await apiRequest<{ authenticated: boolean }>('/admin/session', {
      method: 'POST',
      body: { token },
      adminMutation: true,
    })
    if (!isCurrentAdminEpoch(epoch)) return
    invalidateAdminRequests()
    sessionState.adminAuthenticated = true
    sessionState.authNotice = ''
    sessionState.generation += 1
    // The epoch changes here, so the finally block cannot be the only cleanup.
    sessionState.authBusy = null
    await refreshStatus()
  } finally {
    if (isCurrentAdminEpoch(epoch) && sessionState.authBusy === 'admin') sessionState.authBusy = null
  }
}

export async function logoutAdmin(): Promise<void> {
  const epoch = invalidateAdminRequests()
  invalidateStatusRequests()
  sessionState.authBusy = 'admin-logout'
  sessionState.adminAuthenticated = false
  sessionState.generation += 1
  if (!explicitStatusSession) {
    sessionState.statusAuthenticated = false
    sessionState.status = null
  }
  try {
    await apiRequest<{ authenticated: boolean }>('/admin/logout', {
      method: 'POST',
      adminMutation: true,
    })
  } catch {
    sessionState.authNotice = '管理员本地状态已清除，但服务器会话尚未确认注销。请稍后重试。'
  }
  if (isCurrentAdminEpoch(epoch) && explicitStatusSession) await refreshStatus()
  if (isCurrentAdminEpoch(epoch) && sessionState.authBusy === 'admin-logout') sessionState.authBusy = null
}

export async function logoutStatus(): Promise<void> {
  const epoch = invalidateStatusRequests()
  sessionState.authBusy = 'status-logout'
  explicitStatusSession = false
  sessionState.statusAuthenticated = false
  sessionState.status = null
  try {
    await apiRequest<{ authenticated: boolean }>('/logout', { method: 'POST' })
  } catch {
    sessionState.authNotice = '状态本地会话已清除，但服务器会话尚未确认注销。请稍后重试。'
  }
  if (isCurrentStatusEpoch(epoch) && sessionState.adminAuthenticated) await refreshStatus()
  if (isCurrentStatusEpoch(epoch) && sessionState.authBusy === 'status-logout') sessionState.authBusy = null
}

export function expireAdminSession(): void {
  invalidateAdminRequests()
  invalidateStatusRequests()
  sessionState.adminAuthenticated = false
  sessionState.generation += 1
  sessionState.authNotice = '管理员会话已失效，请重新登录。'
}

export async function bootstrapSession(): Promise<void> {
  await Promise.allSettled([refreshStatus(), probeAdmin()])
}

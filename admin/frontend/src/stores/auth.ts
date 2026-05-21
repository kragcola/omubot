import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useAuthStore = defineStore('auth', () => {
  const authenticated = ref(false)
  const loading = ref(true)

  async function checkAuth() {
    try {
      const resp = await api('/api/admin/me')
      authenticated.value = resp.authenticated === true
    } catch {
      authenticated.value = false
    } finally {
      loading.value = false
    }
  }

  async function login(token: string) {
    try {
      const resp = await api('/api/admin/login', {
        method: 'POST',
        body: { token },
      })
      if (resp.ok) {
        authenticated.value = true
      }
      return resp
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 401) {
        return { ok: false, error: 'invalid_token', status }
      }
      console.error('Login failed:', e)
      return { ok: false, error: 'network_error', status }
    }
  }

  async function logout() {
    try {
      await api('/api/admin/logout', { method: 'POST' })
    } catch { /* ignore network error on logout */ }
    authenticated.value = false
  }

  return { authenticated, loading, checkAuth, login, logout }
})

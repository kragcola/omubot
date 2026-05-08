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
    } catch (e) {
      console.error('Login failed:', e)
      return { ok: false, error: '网络错误' }
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

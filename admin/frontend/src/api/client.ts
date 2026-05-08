import { ofetch } from 'ofetch'
import { useAuthStore } from '../stores/auth'

export const api = ofetch.create({
  baseURL: '',
  credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json' },
  onResponseError({ response }) {
    if (response.status === 401) {
      try {
        const auth = useAuthStore()
        auth.authenticated = false
      } catch { /* store not yet initialized */ }
    }
  },
})

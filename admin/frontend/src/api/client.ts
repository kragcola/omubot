import { ofetch } from 'ofetch'
import { useAuthStore } from '../stores/auth'

function isAgentRuntimeOperatorRequest(options: { headers?: HeadersInit }) {
  return new Headers(options.headers).has('x-agent-runtime-operator-id')
}

export const api = ofetch.create({
  baseURL: '',
  credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json' },
  onResponseError({ response, options }) {
    if (response.status === 401 && !isAgentRuntimeOperatorRequest(options)) {
      try {
        const auth = useAuthStore()
        auth.authenticated = false
      } catch { /* store not yet initialized */ }
    }
  },
})

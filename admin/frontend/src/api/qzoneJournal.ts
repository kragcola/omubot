import { api } from './client'
import type {
  QzoneDraft,
  QzoneDraftAuditResponse,
  QzoneDraftListResponse,
  QzoneDraftRevisionsResponse,
  QzoneDraftStatus,
  QzoneDryRunDescriptor,
  QzoneHealthResponse,
} from '../views/qzone-journal/types'

const BASE = '/api/admin/qzone-journal'

export interface ListDraftsParams {
  status?: QzoneDraftStatus | null
  limit?: number
  offset?: number
}

/** Extract a human-readable error from ofetch / FastAPI failures. */
export function extractApiError(error: unknown, fallback = '请求失败'): string {
  if (error == null) return fallback
  if (typeof error === 'string' && error.trim()) return error.trim()
  if (error instanceof Error && error.message && !isGenericFetchMessage(error.message)) {
    return error.message
  }
  const data = (error as { data?: unknown; response?: { _data?: unknown } })?.data
    ?? (error as { response?: { _data?: unknown } })?.response?._data
  if (typeof data === 'string' && data.trim()) return data.trim()
  if (data && typeof data === 'object') {
    const detail = (data as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (typeof item === 'string') return item
          if (item && typeof item === 'object' && 'msg' in item) {
            return String((item as { msg: unknown }).msg)
          }
          return ''
        })
        .filter(Boolean)
      if (parts.length) return parts.join('；')
    }
    const message = (data as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
    const err = (data as { error?: unknown }).error
    if (typeof err === 'string' && err.trim()) return err.trim()
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function isGenericFetchMessage(message: string): boolean {
  return /^\[?(GET|POST|PUT|PATCH|DELETE)\]?\s/i.test(message)
    || message.startsWith('FetchError')
}

export async function fetchQzoneHealth(): Promise<QzoneHealthResponse> {
  return api<QzoneHealthResponse>(`${BASE}/health`)
}

export async function fetchQzoneDrafts(
  params: ListDraftsParams = {},
): Promise<QzoneDraftListResponse> {
  const query: Record<string, string | number> = {
    limit: params.limit ?? 20,
    offset: params.offset ?? 0,
  }
  if (params.status) query.status = params.status
  return api<QzoneDraftListResponse>(`${BASE}/drafts`, { query })
}

export async function fetchQzoneDraft(draftId: string): Promise<QzoneDraft> {
  return api<QzoneDraft>(`${BASE}/drafts/${encodeURIComponent(draftId)}`)
}

export async function fetchQzoneDraftAudit(
  draftId: string,
): Promise<QzoneDraftAuditResponse> {
  return api<QzoneDraftAuditResponse>(
    `${BASE}/drafts/${encodeURIComponent(draftId)}/audit`,
  )
}

export async function fetchQzoneDraftRevisions(
  draftId: string,
): Promise<QzoneDraftRevisionsResponse> {
  return api<QzoneDraftRevisionsResponse>(
    `${BASE}/drafts/${encodeURIComponent(draftId)}/revisions`,
  )
}

export async function recomposeQzoneDraft(
  draftId: string,
  operatorGuidance?: string | null,
): Promise<QzoneDraft> {
  const guidance = operatorGuidance?.trim()
  const body = guidance ? { operator_guidance: guidance } : {}
  return api<QzoneDraft>(
    `${BASE}/${encodeURIComponent(draftId)}/recompose`,
    { method: 'POST', body },
  )
}

export async function approveQzoneDraft(
  draftId: string,
  note?: string | null,
): Promise<QzoneDraft> {
  const body = {
    approval_scope: 'dry_run' as const,
    ...(note && note.trim() ? { note: note.trim() } : {}),
  }
  return api<QzoneDraft>(`${BASE}/${encodeURIComponent(draftId)}/approve`, {
    method: 'POST',
    body,
  })
}

export async function rejectQzoneDraft(
  draftId: string,
  note: string,
): Promise<QzoneDraft> {
  return api<QzoneDraft>(`${BASE}/${encodeURIComponent(draftId)}/reject`, {
    method: 'POST',
    body: { note: note.trim() },
  })
}

export async function dryRunQzoneDraft(
  draftId: string,
): Promise<QzoneDryRunDescriptor> {
  return api<QzoneDryRunDescriptor>(`${BASE}/${encodeURIComponent(draftId)}/dry-run`, {
    method: 'POST',
  })
}

export async function confirmPublishedQzoneDraft(
  draftId: string,
  note: string,
  externalPostId?: string | null,
): Promise<QzoneDraft> {
  const body: { note: string; external_post_id?: string } = { note: note.trim() }
  const external = externalPostId?.trim()
  if (external) body.external_post_id = external
  return api<QzoneDraft>(
    `${BASE}/${encodeURIComponent(draftId)}/confirm-published`,
    { method: 'POST', body },
  )
}

export async function confirmNotPublishedQzoneDraft(
  draftId: string,
  note: string,
): Promise<QzoneDraft> {
  return api<QzoneDraft>(
    `${BASE}/${encodeURIComponent(draftId)}/confirm-not-published`,
    { method: 'POST', body: { note: note.trim() } },
  )
}

import { apiRequest } from './client'
import type {
  CharacterReferenceMergeRequest, CharacterReferenceMutationView, CharacterReferenceRestoreRequest,
  CharacterReferenceSaveRequest, CharacterReferenceStatusView,
} from './generated'

const base = '/api/admin/characters/reference'

export function readCharacterReference(signal: AbortSignal) {
  return apiRequest<CharacterReferenceStatusView>(base, { signal })
}

export function saveCharacterReference(body: CharacterReferenceSaveRequest, signal: AbortSignal) {
  return apiRequest<CharacterReferenceStatusView>(`${base}/save`, { method: 'POST', body, signal, adminMutation: true })
}

export function restoreCharacterReference(body: CharacterReferenceRestoreRequest, signal: AbortSignal) {
  return apiRequest<CharacterReferenceStatusView>(`${base}/restore`, { method: 'POST', body, signal, adminMutation: true })
}

export function importCharacterReference(body: CharacterReferenceSaveRequest, signal: AbortSignal) {
  return apiRequest<CharacterReferenceMutationView>(`${base}/import`, { method: 'POST', body, signal, adminMutation: true })
}

export function mergeCharacterReference(body: CharacterReferenceMergeRequest, signal: AbortSignal) {
  return apiRequest<CharacterReferenceMutationView>(`${base}/merge`, { method: 'POST', body, signal, adminMutation: true })
}

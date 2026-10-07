import { apiRequest } from './client'
import type {
  StickerCatalogView, StickerDescriptionView, StickerImportRequest, StickerMetadataRequest,
  StickerMutationView, StickerTargetRequest,
} from './generated'

const base = '/api/admin/stickers'
export function readStickers(groupId: string, signal: AbortSignal) {
  return apiRequest<StickerCatalogView>(`${base}?group_id=${encodeURIComponent(groupId)}`, { signal })
}
export function importSticker(body: StickerImportRequest, signal: AbortSignal) {
  return apiRequest<StickerMutationView>(`${base}/import`, { method: 'POST', body, signal, adminMutation: true })
}
export function updateSticker(body: StickerMetadataRequest, signal: AbortSignal) {
  return apiRequest<StickerMutationView>(`${base}/metadata`, { method: 'POST', body, signal, adminMutation: true })
}
export function changeSticker(operation: 'approve' | 'revoke', body: StickerTargetRequest, signal: AbortSignal) {
  return apiRequest<StickerMutationView>(`${base}/${operation}`, { method: 'POST', body, signal, adminMutation: true })
}
export function describeSticker(body: StickerTargetRequest, signal: AbortSignal) {
  return apiRequest<StickerDescriptionView>(`${base}/describe`, { method: 'POST', body, signal, adminMutation: true })
}

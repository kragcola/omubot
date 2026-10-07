import { apiRequest } from './client'
import type {
  NativePlanRequest,
  NativePreparationRequest,
  NativePreparationView,
  NativeReleaseStatusView,
} from './generated'

export function readNativeRelease(signal?: AbortSignal): Promise<NativeReleaseStatusView> {
  return apiRequest<NativeReleaseStatusView>('/api/admin/releases', { signal })
}

export function prepareNativeRelease(
  body: NativePreparationRequest, signal?: AbortSignal,
): Promise<NativePreparationView> {
  return apiRequest<NativePreparationView>('/api/admin/releases/prepare', {
    method: 'POST', body, signal, adminMutation: true,
  })
}

export function readNativePlan(body: NativePlanRequest, signal?: AbortSignal): Promise<NativePreparationView> {
  return apiRequest<NativePreparationView>('/api/admin/releases/plan', {
    method: 'POST', body, signal, adminMutation: true,
  })
}

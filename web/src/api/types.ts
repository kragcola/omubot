export type {
  ActionStatus,
  AuthResponse,
  DeliveryStatus,
  EditableConfig,
  EditableModelProfile,
  ErrorResponse,
  Grant,
  HealthResponse,
  InstanceIdentity,
  ModelBudgetStatus,
  ModelStatus,
  OfflineChatRequest,
  OfflineChatResponse,
  PersonaPreviewIssue,
  PersonaPreviewRequest,
  PersonaPreviewResponse,
  PolicySnapshot,
  PolicyWriteRequest,
  RequestStatusResponse,
  RevisionResponse,
  Scope,
  SettingsRollbackRequest,
  SettingsSnapshot,
  SettingsWriteRequest,
  StatusSnapshot,
  TaskBinding,
  TokenRequest,
} from './generated'

export type ModelProfile = import('./generated').EditableModelProfile
export type ConfigSnapshot = import('./generated').SettingsSnapshot
export type SettingsUpdate = import('./generated').SettingsWriteRequest
export type SettingsRollback = import('./generated').SettingsRollbackRequest
export type PolicyUpdate = import('./generated').PolicyWriteRequest
export type ApiFormat = import('./generated').EditableModelProfile['api_format']
export type ReasoningEffort = NonNullable<import('./generated').EditableModelProfile['reasoning_effort']>
export type TokenParameter = NonNullable<import('./generated').EditableModelProfile['token_parameter']>
export type TaskName = 'reply' | 'thinker' | 'vision' | 'schedule' | 'dream' | 'memory' | 'journal'

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export interface ApiErrorPayload {
  error?: string
  detail?: string
}

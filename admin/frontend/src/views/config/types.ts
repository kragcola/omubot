export type ConfigFieldKind = 'switch' | 'text' | 'number' | 'select' | 'list' | 'kv' | 'object' | 'json'
export type ConfigRiskLevel = 'normal' | 'careful' | 'danger'
export type ConfigRestartHint = 'none' | 'recommended' | 'required'

export interface ConfigFieldSchema {
  key: string
  path: string
  label: string
  description: string
  required: boolean
  kind: ConfigFieldKind
  display_label?: string
  help?: string
  example?: string
  recommended?: string
  risk_level?: ConfigRiskLevel
  restart_hint?: ConfigRestartHint
  secret?: boolean
  options?: Array<string | number | boolean>
  number_type?: 'int' | 'float'
  item_kind?: 'switch' | 'text' | 'number' | 'select'
  value_kind?: 'switch' | 'text' | 'number' | 'select'
  children?: ConfigFieldSchema[]
}

export interface ConfigEditorPayload {
  path: string
  format_mode: 'json' | 'legacy'
  migration_pending: boolean
  editor: {
    schema: ConfigFieldSchema[]
    values: Record<string, any>
    secret_masks: Record<string, string>
  }
  advanced: {
    raw_json: string
  }
}

export interface ConfigDiffChange {
  path: string
  top_level: string
  change_type: 'added' | 'removed' | 'changed'
  secret: boolean
  before_display: string
  after_display: string
}

export interface ConfigDiffSummary {
  total: number
  added: number
  removed: number
  changed: number
  top_level_count: number
  top_levels: string[]
}

export interface ConfigPreviewResult {
  ok: boolean
  mode: string
  summary: ConfigDiffSummary
  changes: ConfigDiffChange[]
  advanced: {
    raw_json: string
  }
  error?: string
  field_errors?: Array<{ path: string, message: string }>
}

export interface ConfigAuditEntry {
  id: string
  saved_at: number
  config_path: string
  mode: string
  summary: ConfigDiffSummary
  changes: ConfigDiffChange[]
}

export interface ConfigAuditPayload {
  version: number
  path: string
  entries: ConfigAuditEntry[]
}

export interface ConfigBackupEntry {
  id: string
  created_at: number
  config_path: string
  trigger: 'save' | 'restore' | 'pre_restore' | string
  mode: string
  summary: ConfigDiffSummary
  note: string
  source_backup_id: string
  size_bytes: number
}

export interface ConfigBackupPayload {
  version: number
  path: string
  snapshot_dir: string
  entries: ConfigBackupEntry[]
}

export interface ConfigSaveResult extends ConfigEditorPayload {
  ok: boolean
  diff?: {
    summary: ConfigDiffSummary
    changes: ConfigDiffChange[]
  }
  audit_entry?: ConfigAuditEntry
  backup_entry?: ConfigBackupEntry
  message?: string
}

export type ConfigRestoreResult = ConfigSaveResult

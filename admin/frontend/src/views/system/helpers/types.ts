export interface HealthInfo {
  bot: string
  napcat: string
  uptime_seconds: number
}

export interface SystemInfo {
  cpu_percent?: number | null
  active_sessions?: number | null
  restart_notice?: RestartNotice
  memory?: {
    total_gb: number
    used_gb: number
    percent: number
  }
  disk?: {
    total_gb: number
    used_gb: number
    percent: number
  }
  process?: {
    pid: number
    memory_mb: number
    threads: number
  }
}

export interface VersionInfo {
  version: string
  summary?: string
  latest_tag?: string
  latest_name?: string
  latest_url?: string
  has_update?: boolean
}

export interface HumanizerInfo {
  enabled: boolean
  min_delay?: number
  max_delay?: number
  char_delay?: number
}

export interface TalkScheduleInfo {
  time_multiplier: number
}

export interface ProviderProfile {
  name: string
  active: boolean
  api_format: string
  base_url: string
  model: string
  max_tokens?: number
  capabilities?: string[]
  api_key_mask?: string
  api_key_present?: boolean
  provider_kind?: string
  provider_mode?: string
  last_cache_hit_pct?: number | null
  last_cache_hit_pct_by_task?: Record<string, number>
  last_prompt_cache_hit_tokens?: number
  last_prompt_cache_miss_tokens?: number
  last_reasoning_replay_tokens?: number
  last_payload_sanitized?: boolean
  last_usage?: Record<string, any>
  rate_limit?: ProviderRateLimit
}

export interface ProviderOption {
  value: string
  label: string
}

export interface ProviderRateLimit {
  profile: string
  status: 'ready' | 'cooldown' | string
  cooldown_remaining_seconds: number
  total_calls: number
  successes: number
  failures: number
  rate_limited: number
  blocked_calls: number
  consecutive_rate_limits: number
  last_task: string
  last_error: string
  last_success_at: number
  last_limited_at: number
  provider_kind?: string
  provider_mode?: string
  last_model?: string
  last_api_format?: string
  last_cache_hit_pct?: number | null
  last_cache_hit_pct_by_task?: Record<string, number>
  last_prompt_cache_hit_tokens?: number
  last_prompt_cache_miss_tokens?: number
  last_reasoning_replay_tokens?: number
  last_payload_sanitized?: boolean
  last_usage?: Record<string, any>
}

export type ProviderTaskKey =
  | 'main'
  | 'thinker'
  | 'compact'
  | 'reply_gate'
  | 'vision'
  | 'slang'
  | 'slang_review'
  | 'slang_drift'
  | 'slang_semantic'
  | 'style'
  | 'memo'
  | 'persona_import'
  | 'chat_private'
  | 'bilibili_intent'
  | 'element_detect'
  | 'graph_review'
  | 'graph_edge_classifier'
  | 'reflection_consolidator'
  | 'episode_summarizer'

export interface ProviderTaskProfile {
  task: ProviderTaskKey | string
  profile: string
  model: string
  api_format: string
}

export interface ProvidersInfo {
  default_profile: string
  task_profiles?: ProviderTaskProfile[]
  profiles: ProviderProfile[]
  capability_options?: ProviderOption[]
  api_format_options?: ProviderOption[]
  rate_limits?: {
    profiles?: Record<string, ProviderRateLimit>
    tasks?: Record<string, ProviderRateLimit>
  }
}

export type ProviderApiKeyMode = 'keep' | 'replace' | 'clear'

export interface ProviderProfileDraft {
  name: string
  api_format: string
  base_url: string
  model: string
  max_tokens: number | null
  capabilities: string[]
  api_key_mask: string
  api_key_present: boolean
  api_key_mode: ProviderApiKeyMode
  api_key_input: string
}

export interface ProviderTestResult {
  ok: boolean
  profile: string
  api_format?: string
  model?: string
  elapsed_ms?: number
  text_preview?: string
  error?: string
  provider_kind?: string
  provider_mode?: string
  payload_sanitized?: boolean
  reasoning_replay_tokens?: number
  usage_summary?: Record<string, any>
}

export interface ProtocolCapability {
  key: string
  label: string
  status: 'ok' | 'configured' | 'unchecked' | 'failed' | string
  detail: string
}

export interface ProtocolCompatibilityItem {
  key: string
  label: string
  napcat: 'supported' | 'compatible' | 'conditional' | 'manual' | 'unchecked' | string
  llonebot: 'supported' | 'compatible' | 'conditional' | 'manual' | 'unchecked' | string
  detail: string
}

export interface ProtocolConnectionSummary {
  current_status: 'connected' | 'disconnected' | 'unknown' | string
  connected_bots: number
  self_ids: string[]
  changed_at: number
  last_seen_at: number
  disconnected_since: number
  last_recovery_seconds?: number | null
  last_error?: string
  event_count: number
}

export interface ProtocolConnectionEvent {
  event_id: string
  kind: 'state' | 'error' | string
  status: string
  previous_status: string
  connected_bots: number
  self_ids: string[]
  source: string
  error?: string
  recovery_seconds?: number | null
  occurred_at: number
}

export interface ProtocolConnectionPayload {
  summary: ProtocolConnectionSummary
  events: ProtocolConnectionEvent[]
  max_items: number
}

export interface ProtocolHealth {
  adapter: string
  api_url: string
  connected_bots: number
  checked_at: number
  trace_summary?: ProtocolTraceSummary
  capabilities: ProtocolCapability[]
  compatibility?: ProtocolCompatibilityItem[]
  connection?: ProtocolConnectionSummary
}

export interface ProtocolTraceSummary {
  total: number
  ok: number
  failed: number
  pending: number
  avg_elapsed_ms: number
  wrapped_bots?: number
  last_error?: string
}

export interface ProtocolTrace {
  trace_id: string
  action: string
  status: string
  elapsed_ms: number
  started_at: number
  error?: string
}

export interface ProtocolTracePayload {
  summary: ProtocolTraceSummary
  traces: ProtocolTrace[]
  max_items: number
}

export interface ServiceHealthItem {
  id: string
  label: string
  status: 'ok' | 'warning' | 'error' | 'unknown' | string
  detail: string
  metric?: string
  meta?: Record<string, any>
}

export interface HealthAlert {
  id: string
  source: string
  severity: 'error' | 'warning' | 'info' | string
  title: string
  detail: string
  metric?: string
  action: string
  maintenance_window: boolean
}

export interface MaintenanceWindow {
  recommended: boolean
  severity: 'error' | 'warning' | 'info' | string
  title: string
  summary: string
  window_hint: string
  reasons: string[]
  checklist: string[]
  restart_recommended: boolean
}

export interface RestartNotice {
  supported: boolean
  title: string
  summary: string
  window_hint: string
  impact: string[]
  works_for?: string[]
  needs_rebuild?: string[]
  checklist: string[]
}

export interface HealthAlertPolicy {
  mode: string
  summary: string
  suppressed_count: number
  overflow_count: number
  alert_count: number
  thresholds?: Record<string, string>
}

export interface ServicesHealth {
  checked_at: number
  overall_status: 'ok' | 'warning' | 'error' | 'unknown' | 'degraded' | string
  summary: {
    ok: number
    warning: number
    error: number
    unknown: number
  }
  alerts?: HealthAlert[]
  policy?: HealthAlertPolicy
  maintenance_window?: MaintenanceWindow
  services: ServiceHealthItem[]
}

export interface RuntimeErrorEvent {
  event_id: string
  signature: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL' | string
  channel: string
  logger: string
  message: string
  occurred_at: number
}

export interface RuntimeErrorGroup {
  signature: string
  level: 'WARNING' | 'ERROR' | 'CRITICAL' | string
  channel: string
  logger: string
  message: string
  count: number
  first_seen_at: number
  last_seen_at: number
}

export interface RuntimeErrorPayload {
  summary: {
    total: number
    warnings: number
    errors: number
    critical?: number
    unique: number
    last_error?: RuntimeErrorEvent | Record<string, never>
    last_warning?: RuntimeErrorEvent | Record<string, never>
    top_issue?: RuntimeErrorGroup | Record<string, never>
  }
  groups: RuntimeErrorGroup[]
  events: RuntimeErrorEvent[]
  max_events: number
  max_groups: number
}

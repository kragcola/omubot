/**
 * Type definitions for the SlangView console.
 *
 * Extracted in PR B-1 of the SlangView refactor (web-refactor-plan §6.3).
 * Mirrors the shapes returned by `/api/admin/slang/*`.
 */

export type SlangStatus = 'candidate' | 'approved' | 'muted' | 'expired'
export type RepeatPolicy = 'understand_only' | 'allow_rephrase' | 'allow_use'
export type SlangQueueMode = 'candidate' | 'ai_rejected' | 'pending_human_review' | 'approved' | 'archived' | 'drift' | 'all'

export interface SlangSummary {
  candidate_count: number
  candidate_unreviewed_count: number
  approved_count: number
  muted_count: number
  expired_count: number
  pending_count: number
  drift_count: number
  ai_review_count: number
  ai_pending_review_count: number
  under_observation_count: number
  ai_rejected_count: number
  human_reviewed_count: number
  eligible_backlog_count: number
  today_hits: number
  group_count: number
  last_extracted_at: string
  last_daily_ai_review_at: string
  latest_run_status: string
}

export interface SlangTerm {
  term_id: string
  term: string
  meaning: string
  aliases: string[]
  scope: 'group' | 'global'
  group_id: string
  confidence: number
  status: SlangStatus
  usage_count: number
  unique_user_count: number
  first_seen_at: string
  last_seen_at: string
  last_inferred_at?: string
  source: string
  repeat_policy: RepeatPolicy
  notes: string
  meta?: Record<string, any>
}

export interface SlangObservation {
  observation_id: string
  group_id: string
  user_id: string
  message_id?: number
  raw_text: string
  context: string
  observed_at: string
  reason: string
}

export interface SlangPendingCandidate {
  pending_id: string
  term: string
  meaning: string
  aliases: string[]
  group_id: string
  confidence: number
  count: number
  unique_user_count: number
  evidence: string
  reason: string
  repeat_policy: RepeatPolicy
  first_seen_at: string
  last_seen_at: string
}

export interface SlangExtractionRun {
  run_id: string
  started_at: string
  finished_at?: string
  status: 'running' | 'success' | 'failed'
  group_count: number
  scanned_messages: number
  extracted_terms: number
  promoted_candidates: number
  error: string
  duration_ms: number
  meta?: Record<string, any>
}

export interface SlangStatsTerm {
  term_id: string
  term: string
  meaning: string
  scope: 'group' | 'global'
  group_id: string
  status: SlangStatus
  confidence: number
  usage_count: number
  unique_user_count: number
  last_seen_at: string
}

export interface SlangStats {
  popular_terms: SlangStatsTerm[]
  group_activity: Array<{
    group_id: string
    term_count: number
    approved_count: number
    usage_count: number
  }>
  recent_trend: Array<{
    date: string
    created: number
    observations: number
  }>
  review: {
    total_terms: number
    candidate_count: number
    reviewed_count: number
    approval_rate: number
  }
  injection: {
    approved_terms: number
    avg_confidence: number
    global_candidates: number
    global_approved: number
    observing_count: number
  }
}

export interface SlangSettings {
  learning_enabled: boolean
  injection_enabled: boolean
  review_required: boolean
  max_injected_terms: number
  max_indirect_inject_terms: number
  extract_interval_minutes: number
  candidate_min_count: number
  group_allowlist: string[]
  repeat_policy: RepeatPolicy
  extraction_batch_limit: number
  auto_promote_global_enabled: boolean
  global_promote_min_groups: number
  bulk_page_size: number
  stats_days: number
  stoplist: string[]
  max_prompt_chars: number
  daily_ai_review_enabled: boolean
  daily_ai_review_times: string[]
  daily_ai_review_search_enabled: boolean
  daily_ai_auto_approve_enabled: boolean
  daily_ai_auto_approve_min_confidence: number
  daily_ai_max_terms_per_group: number
  daily_ai_recent_message_limit: number
  backlog_review_enabled: boolean
  backlog_review_batch_size: number
  backlog_review_min_confidence: number
  backlog_review_min_usage_count: number
  backlog_review_search_enabled: boolean
  backlog_auto_approve_enabled: boolean
  backlog_auto_approve_min_confidence: number
  backlog_kept_streak_limit: number
  backlog_local_evidence_count: number
  backlog_threshold_gating_enabled: boolean
  drift_detection_enabled: boolean
  drift_min_confidence: number
  drift_semantic_gate_enabled: boolean
  drift_age_out_days: number
  lookup_tool_enabled: boolean
  min_inject_confidence: number
  semantic_backend: 'ngram' | 'embedding'
}

export interface SlangRevision {
  revision_id: string
  term_id: string
  action: string
  actor: string
  before: Record<string, any>
  after: Record<string, any>
  reason: string
  created_at: string
  meta?: Record<string, any>
}

export interface SlangDriftReview {
  drift_id: string
  term_id: string
  term: string
  group_id: string
  old_meaning: string
  new_meaning: string
  aliases: string[]
  evidence: string
  confidence: number
  reason: string
  status: 'open' | 'accepted' | 'rejected' | 'aliased' | 'muted' | 'aged_out' | 'auto_dismissed' | 'auto_aliased'
  created_at: string
  updated_at: string
  meta?: Record<string, any>
}

export interface SlangCreateDraft {
  term: string
  meaning: string
  aliases: string
  scope: 'group' | 'global'
  group_id: string
  confidence: number
  status: SlangStatus
  repeat_policy: RepeatPolicy
  notes: string
  evidence: string
}

export interface SlangBacklogState {
  active: boolean
  processed: number
  approved: number
  muted: number
  kept: number
  total_at_start: number
  remaining: number
  started_at: string
  last_progress_at: string
  last_run_id: string
  last_done_at: string
}
